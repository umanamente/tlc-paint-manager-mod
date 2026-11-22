-- Current-context paint catalog. Only native discovery/validation runs here;
-- no standalone paint cache file is read, written or restored.
local processing = require("paintmanager.processing")
local collection = require("paintmanager.collection")
local lifecycle = require("paintmanager.save_lifecycle")
local save_bridge = require("paintmanager.save_bridge")
local json = require("paintmanager.json")
local M = {}
local Controller = {}; Controller.__index = Controller

local function cancel(job, reason)
    if job and type(job.cancel) == "function" then job:cancel(reason) end
end
local function ready(binding)
    if not binding or type(lifecycle.is_ready) ~= "function" then return false end
    local current, generation = lifecycle.is_ready(binding.contextToken, binding.scopeId)
    return current == true and generation == binding.loadGeneration
end
local function copy_binding(event)
    if type(event) ~= "table" or event.invalidated or type(event.contextToken) ~= "string"
        or type(event.scopeId) ~= "string" or type(event.accountId) ~= "string"
        or type(event.libraryPath) ~= "string" or type(event.loadGeneration) ~= "number" then
        return nil, "A proven restored account/player scope is required"
    end
    return { contextToken = event.contextToken, scopeId = event.scopeId, accountId = event.accountId,
        libraryPath = event.libraryPath, loadGeneration = event.loadGeneration }
end
function M.new(config, build)
    assert(type(config) == "table", "Catalog configuration is required")
    return setmetatable({ config = config, build = tostring(build), generation = 0,
        state = "inactive", phase = "inactive", revision = 0 }, Controller)
end
function Controller:invalidate(reason)
    self.generation = self.generation + 1
    cancel(self.job, reason or "Paint catalog scope invalidated")
    self.job, self.bound = nil, nil
    self.catalog_model, self.last_catalog, self.learned = nil, nil, nil
    self.advisorySeeds, self.seedWork = nil, nil
    self.state, self.phase, self.error = "inactive", "inactive", reason
    self.revision = self.revision + 1
end
function Controller:binding() return self.bound end
function Controller:_prove_current()
    local binding = self.bound
    local ok, failure = pcall(function()
        assert(ready(binding), "Native save lifecycle revoked the paint-catalog scope")
        local token, reason = collection.context()
        assert(token == binding.contextToken, reason or "Paint-catalog player/world changed")
        local current = save_bridge.storage_context(binding.contextToken)
        assert(current.status == "ready", current.error or "Paint-catalog native account scope is unavailable")
        assert(current.contextToken == binding.contextToken and current.scopeId == binding.scopeId
            and current.accountId == binding.accountId and current.libraryPath == binding.libraryPath
            and current.loadGeneration == binding.loadGeneration, "Paint-catalog native account/save scope changed")
    end)
    if not ok then self:invalidate(tostring(failure)); self.state = "error"; return false end
    return true
end
function Controller:admit(event)
    local binding, reason = copy_binding(event)
    if not binding or not ready(binding) then return false, reason or "Native save lifecycle has not admitted this scope" end
    local previous = self.bound
    if previous and previous.contextToken == binding.contextToken and previous.scopeId == binding.scopeId
        and previous.accountId == binding.accountId and previous.libraryPath == binding.libraryPath
        and previous.loadGeneration == binding.loadGeneration then return true end
    self:invalidate("Restored paint-catalog scope admitted")
    self.bound, self.error = binding, nil
    self.state, self.phase = "waiting_demand", "idle"
    return true
end
function Controller:begin_open()
    self:quiesce()
    if not self.bound then return end
    self.last_catalog = self.catalog_model or self.last_catalog
    local hints = processing.get("paint_hints_enabled", true)
    self.catalog_model = nil
    self.seedWork = { rows = hints and self.last_catalog and self.last_catalog.rows or {}, index = 1,
        seeds = hints and self.advisorySeeds or {}, entries = json.array(), seen = {} }
    self.job = collection.begin_catalog(self.config, { learnedFirst = true, awaitingKnown = true,
        contextToken = self.bound.contextToken, discover = true })
    self.job_generation, self.state, self.phase = self.generation, "warming", "catalog"
end

function Controller:catalog() return ready(self.bound) and self.catalog_model or nil end
function Controller:learned_capture() return ready(self.bound) and self.learned or nil end
function Controller:describe()
    local native = self.job and type(self.job.get_progress) == "function" and self.job:get_progress() or nil
    local presentation = native and native.presentation or { kind = "recipes", phase = self.phase,
        mode = self.phase == "idle" and "idle" or "indeterminate", coverage = "pending" }
    return { state = self.state, phase = self.phase, error = self.error, backend = "lua_cooperative",
        persistence = "session_only", ready = self.catalog_model ~= nil and ready(self.bound),
        revision = self.revision, defer = self.deferred == true, binding = self.bound,
        exhaustive = false, presentation = presentation }
end
function Controller:observe(model)
    if not ready(self.bound) or type(model) ~= "table" or model.contextToken ~= self.bound.contextToken
        or model.status ~= "captured" then return false end
    self.catalog_model, self.last_catalog = model, model
    self.state, self.phase = "ready", "idle"
    self.revision = self.revision + 1
    return true
end
function Controller:seed_advisory(entries)
    if not ready(self.bound) or type(entries) ~= "table" or not processing.get("paint_hints_enabled", true) then return false end
    -- A retained box file may offer identity hints, never counts or permission.
    -- Validate these only on the next actual catalog demand.
    self.advisorySeeds = entries
    return true
end
local function seed_identity(work, row)
    local entry = collection.cache_entry(row)
    if not entry then return end
    local key = entry.assetType .. "\0" .. entry.assetName
    if not work.seen[key] then
        work.seen[key] = true
        work.entries[#work.entries + 1] = entry
    end
end
function Controller:_unit(budget)
    if self.phase == "seed" then
        local work = self.seedWork
        local row = work.rows[work.index]
        if row then seed_identity(work, row); work.index = work.index + 1
        elseif not work.seeding then
            work.rows, work.index, work.seeding = work.seeds, 1, true
        else
            self.seedWork = nil
            self.job:provide_known(work.entries, true)
            self.phase = "catalog"
        end
        budget:consume(1)
        return false
    elseif self.phase == "catalog" then
        if not self:_prove_current() then budget:consume(1); return true end
        local progress = self.job:step(budget)
        if self.job_generation ~= self.generation or not ready(self.bound) then return true end
        if progress.state == "running" then
            if progress.phase == "catalog_wait" then
                self.learned = self.job:get_learned_capture()
                self.phase, self.deferred = "seed", true
                return true
            end
            self.deferred = progress.defer == true; return self.deferred
        end
        if progress.state ~= "done" then
            local failure = progress.error or "Native paint catalog validation stopped"
            self:invalidate(failure)
            self.state = "error"
            return true
        end
        if not self:_prove_current() then return true end
        self.catalog_model = self.job:get_catalog()
        if not self.catalog_model then
            self:invalidate("Native paint catalog completed without a reusable model"); self.state = "error"; return true
        end
        self.last_catalog, self.job, self.state, self.phase = self.catalog_model, nil, "ready", "idle"
        self.revision = self.revision + 1
    end
    return true
end

function Controller:step(budget)
    self.deferred = false
    if not self.bound or self.phase == "idle" or not self:_prove_current() then return self:describe() end
    while not budget:expired() do
        if self:_unit(budget) then break end
    end
    return self:describe()
end
function Controller:progress_signature()
    local job = self.job or {}
    return table.concat({ self.phase or "", tostring(self.revision), tostring(job.phase or ""),
        tostring(job.processed or job.completed or job.steps or 0),
        tostring(self.seedWork and self.seedWork.index or 0) }, ":")
end
function Controller:is_busy() return self.job ~= nil or self.seedWork ~= nil end
function Controller:quiesce()
    cancel(self.job, "Catalog request ended")
    self.job, self.seedWork, self.learned = nil, nil, nil
    self.catalog_model = self.catalog_model or self.last_catalog
    self.phase, self.state = "idle", self.catalog_model and "ready" or "waiting_demand"
    return true
end
function Controller:reset_derived()
    if self:is_busy() then return false, "Catalog tasks must settle before a cold reset" end
    self.catalog_model, self.last_catalog, self.advisorySeeds, self.learned = nil, nil, nil, nil
    self.phase, self.state = "idle", "waiting_demand"
    self.revision = self.revision + 1
    return true
end
return M
