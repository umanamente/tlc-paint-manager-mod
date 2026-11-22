-- Read-only ship stock orchestration. Native capture and owned-data Lua tasks
-- share the bounded game-thread scheduler.
local processing = require("paintmanager.processing")
local collection = require("paintmanager.collection")
local ship_scope = require("paintmanager.ship_scope")
local bridge = require("paintmanager.save_bridge")
local background = require("paintmanager.background")
local ids = require("paintmanager.persistent_id")
local json = require("paintmanager.json")
local M = {}
local Controller = {}; Controller.__index = Controller

local function cancel(job, reason)
    if job and type(job.cancel) == "function" then job:cancel(reason) end
end

function M.new(config, build)
    return setmetatable({ config = config, build = tostring(build), generation = 0,
        revision = 0, now = 0, state = "inactive", writeAllowed = true }, Controller)
end

-- Revoke capture and pending derived output together across a load or ship
-- change. The revision counts published intervals, including partial results.
function Controller:_discard_scope(reason)
    cancel(self.job, reason); cancel(self.diskJob, reason)
    self.job, self.diskJob, self.diskInfo, self.progress = nil, nil, nil, nil
    self.scope, self.completed, self.paintSeeds = nil, nil, nil
    self.pendingSnapshot, self.snapshotWork = nil, nil
    self.diskRetryAt, self.writeAfter, self.diskStatus = 0, nil, nil
    self.request, self.diskRead, self.writeAllowed = nil, false, true
    self.generation = self.generation + 1
end

function Controller:invalidate(reason)
    self:_discard_scope(reason)
    self.bound, self.state, self.error = nil, "error", reason
end

function Controller:admit(event)
    if type(event) ~= "table" or event.invalidated or type(event.baseScopeId) ~= "string"
        or type(event.accountId) ~= "string" or type(event.libraryPath) ~= "string"
        or type(event.loadGeneration) ~= "number" then return false, "A proven restored stock base is required" end
    if self.bound and self.bound.baseScopeId == event.baseScopeId and self.bound.accountId == event.accountId
        and self.bound.libraryPath == event.libraryPath and self.bound.loadGeneration == event.loadGeneration then return true end
    self:invalidate("A restored stock scope was admitted")
    self.bound = {}
    for _, key in ipairs({ "baseScopeId", "contextToken", "scopeId", "accountId", "libraryPath", "loadGeneration" }) do
        self.bound[key] = event[key]
    end
    self.path = event.libraryPath:gsub("PaintManager%.library%.json$", "PaintManager.container-cache.json")
    if self.path == event.libraryPath then self:invalidate("Unrecognized stock-cache sibling path"); return false end
    self.state, self.error = "idle", nil
    return true
end

function Controller:binding() return self.bound end
function Controller:capture() return self.completed, self.revision end

function Controller:_cancel_read(reason)
    if self.diskInfo and self.diskInfo.kind == "container_cache_read" then
        cancel(self.diskJob, reason)
        self.diskJob, self.diskInfo = nil, nil
    end
end

function Controller:cancel_refresh(reason)
    if not self.request then return end
    cancel(self.job, reason)
    self:_cancel_read(reason)
    self.job, self.progress, self.request = nil, nil, nil
    self.state, self.error = "error", reason or "Stock refresh cancelled"
end

function Controller:refresh()
    self:cancel_refresh("Superseded by a new window opening")
    if self.bound then
        self.request, self.lastProof, self.scanDue = {}, -math.huge, false
        self.state, self.error = "scanning", nil
    else
        self.state, self.error = "error", "A restored stock scope is required"
    end
    return self.revision + 1
end

function Controller:_request(boxes, scope)
    scope = scope or self.scope
    return { path = self.path, accountId = self.bound.accountId, gameBuild = self.build,
        sourcePolicyVersion = scope.sourcePolicyVersion or 2, shipId = scope.shipId, boxes = boxes }
end

function Controller:_prove()
    if self.now - self.lastProof < processing.get("context_poll_ms", self.config.context_poll_ms or 500) then return true end
    local request = self.request
    local value = bridge.stock_context(self.bound)
    if self.request ~= request then return false end
    if value.status ~= "ready" then
        self:invalidate(value.error or "Stock restoration base was revoked")
        return false
    end
    self.lastProof = self.now
    return true
end

function Controller:_snapshot_step(budget)
    local work = self.snapshotWork
    if not work or budget:expired() then return end
    if work.generation ~= self.generation or not self.scope or work.scope.token ~= self.scope.token then
        self.snapshotWork = nil; return
    end
    local ok, err = pcall(function()
        if not work.row then
            local source = work.source[work.index]
            if not source then
                self.pendingSnapshot = { boxes = work.boxes, scope = work.scope, revision = work.revision,
                    generation = work.generation, contextToken = work.contextToken }
                self.snapshotWork = nil
                return
            end
            assert(type(source) == "table" and type(source.paints) == "table", "Invalid observed cache row")
            work.index = work.index + 1
            -- Complete empty observations remove old positives. They do not
            -- become disk rows, but an entirely empty positive array is saved.
            if #source.paints == 0 then return end
            assert(#work.boxes < 128 and #source.paints <= 512 and ids.is_valid(source.persistentId), "Invalid observed box identity/limit")
            work.row, work.paintIndex = { persistentId = source.persistentId, boxKind = source.boxKind,
                lastCheckedAt = source.lastCheckedAt, paints = json.array() }, 1
            work.paints = source.paints
        else
            local paint = work.paints[work.paintIndex]
            if paint then
                assert(type(paint) == "table", "Invalid observed paint identity")
                work.row.paints[#work.row.paints + 1] = { assetType = paint.assetType,
                    assetName = paint.assetName, itemPath = paint.itemPath }
                work.paintIndex = work.paintIndex + 1
            else
                work.boxes[#work.boxes + 1] = work.row
                work.row, work.paints = nil, nil
            end
        end
    end)
    budget:consume()
    if not ok then self.snapshotWork = nil; self.diskStatus = "invalid_snapshot: " .. tostring(err) end
end

function Controller:_disk_step(budget, writes_only)
    if self.diskJob then
        local info = self.diskInfo
        local progress = self.diskJob:step(budget)
        if progress.state == "running" then return end
        local result = progress.state == "done" and self.diskJob:get_result() or nil
        self.diskJob, self.diskInfo = nil, nil
        if not info or info.generation ~= self.generation or not self.scope
            or info.token ~= self.scope.token or info.shipId ~= self.scope.shipId then return end
        if result then
            if result.writeAllowed == false then self.writeAllowed = false end
            self.diskStatus = result.status
            -- Restored box mappings/counts are never adopted. These identities
            -- can seed native catalog verification; loaded rediscovery owns IDs.
            if info.kind == "container_cache_read" and result.status ~= "unavailable" then
                self.diskRead = true
                if result.paintSeeds and processing.get("box_hints_enabled", true) then self.paintSeeds = result.paintSeeds end
            end
        else self.diskStatus = progress.error or "cache_task_failed" end
        local failed = not result or result.status == "unavailable"
        if failed then
            self.diskRetryAt = self.now + processing.get("stock_disk_retry_ms", self.config.stock_disk_retry_ms or 2000)
            if info.snapshot and self.writeAllowed and not self.pendingSnapshot
                and (not self.snapshotWork or self.snapshotWork.revision <= info.snapshot.revision) then
                self.pendingSnapshot = info.snapshot
                self.writeAfter = self.diskRetryAt
            end
        end
        if not self.writeAllowed then self.pendingSnapshot, self.snapshotWork = nil, nil end
        return
    end
    if not self.scope or not ids.is_valid(self.scope.shipId) or not self.writeAllowed
        or self.now < (self.diskRetryAt or 0) then return end
    local kind, snapshot
    if not writes_only and not self.diskRead and processing.get("box_cache_read", true) then kind = "container_cache_read"
    elseif writes_only and self.pendingSnapshot and processing.get("box_cache_write", true) and self.now >= (self.writeAfter or 0) then
        snapshot = self.pendingSnapshot
        if snapshot.generation ~= self.generation or snapshot.scope.token ~= self.scope.token
            or snapshot.scope.shipId ~= self.scope.shipId then
            self.pendingSnapshot = nil; return
        end
        kind = "container_cache_write"
    end
    if not kind then return end
    local supported, reason = background.supports(kind)
    if not supported then
        self.diskStatus, self.diskRetryAt = reason or "worker_unavailable", self.now + processing.get("stock_disk_retry_ms", self.config.stock_disk_retry_ms or 2000)
        return
    end
    local request = self:_request(snapshot and snapshot.boxes, snapshot and snapshot.scope)
    local temp_token
    if kind == "container_cache_write" then
        local ok, value, failure = pcall(bridge.new_id, snapshot.contextToken)
        if snapshot ~= self.pendingSnapshot or snapshot.generation ~= self.generation then return end
        if not ok or type(value) ~= "string" or #value == 0 or #value > 256 or not value:match("^[%w%-]+$") then
            self.diskStatus = "cache_token_unavailable: " .. tostring(ok and failure or value)
            self.diskRetryAt = self.now + processing.get("stock_disk_retry_ms", self.config.stock_disk_retry_ms or 2000)
            return
        end
        temp_token = value
    end
    local job, failure = background.begin_task(kind, request, {
        path = self.path, generation = self.generation, temp_token = temp_token })
    if job then
        self.diskJob = job
        self.diskInfo = { kind = kind, generation = self.generation, token = self.scope.token,
            shipId = request.shipId, snapshot = snapshot }
        if snapshot then self.pendingSnapshot, self.writeAfter = nil, nil end
    else
        self.diskStatus, self.diskRetryAt = failure, self.now + processing.get("stock_disk_retry_ms", self.config.stock_disk_retry_ms or 2000)
    end
end

function Controller:take_paint_seeds()
    local seeds = self.paintSeeds; self.paintSeeds = nil
    return seeds
end

function Controller:observe(model)
    if not self.bound or not model or not model.stockScope then return false end
    if self.scope and model.stockScope.token ~= self.scope.token then return false end
    self:cancel_refresh("A completed stock interval was observed")
    self.scope, self.completed = model.stockScope, model
    self.revision = self.revision + 1
    self.state, self.progress, self.error = model.stockComplete and "ready" or "partial", nil, nil
    if self.writeAllowed and processing.get("box_cache_write", true) and model.stockComplete and type(model.boxCache) == "table" then
        self.snapshotWork = { source = model.boxCache, index = 1, boxes = json.array(),
            scope = { token = model.stockScope.token, shipId = model.stockScope.shipId,
                sourcePolicyVersion = model.stockScope.sourcePolicyVersion or 2 },
            generation = self.generation, revision = self.revision, contextToken = model.contextToken }
        self.writeAfter = self.writeAfter or self.now + processing.get("stock_write_delay_ms", self.config.stock_write_delay_ms or 2000)
    end
    return true
end

function Controller:get_progress()
    local p = self.progress or {}
    local presentation = p.presentation or { kind = "stock", phase = self.state,
        mode = "indeterminate", coverage = self.state == "ready" and "complete"
            or self.state == "partial" and "partial" or "pending" }
    return { state = self.state, error = self.error, phase = p.phase or self.state,
        presentation = presentation, diskStatus = self.diskStatus, revision = self.revision, defer = p.defer == true }
end

-- Only the explicit window-open caller can arm an interval. Rechecking the
-- live ship during an admitted interval never admits its replacement.
function Controller:step(budget, foreground, catalog, now)
    if not self.request or not foreground or budget:expired() then return end
    self.now = math.max(self.now, tonumber(now) or self.now)
    if not self:_prove() then return end
    local request = self.request
    local scope, reason = ship_scope.current(self.config)
    if self.request ~= request then return end
    if not scope then
        self:_discard_scope(reason)
        self.state, self.error = "error", reason or "Ship scope unavailable"
        return
    end
    if self.scope and scope.token ~= self.scope.token then
        local interrupted = self.job ~= nil
        self:_discard_scope("Selected ship scope changed")
        if interrupted then
            self.state, self.error = "error", "Selected ship scope changed during refresh"
            return
        end
        self.request = request
    end
    self.scope = scope
    if not self.job then
        local token, context_error = collection.context()
        if self.request ~= request then return end
        if not token then
            self.completed = nil
            self:cancel_refresh(context_error or "Character context unavailable")
            return
        end
        if self.completed and self.completed.contextToken ~= token then self.completed = nil end
        if catalog and catalog.contextToken ~= token then catalog = nil end
        -- Enumerate eligible boxes on every opening, including newly built
        -- boxes. No source registry or progressive prefix is published.
        local job = collection.begin(self.config, { catalog = catalog, openCatalog = true,
            presentation = "paints", shipScope = scope })
        if self.request ~= request then cancel(job, "Stock opening was revoked"); return end
        self.job = job
    end
    -- An advisory read shares the opening's budget. Give collection the next
    -- slice if the read consumed this entire slice (even at a one-unit cap).
    if not self.scanDue and (self.diskJob or not self.diskRead) then
        self:_disk_step(budget)
    end
    self.scanDue = budget:expired()
    if self.scanDue or self.request ~= request then return end
    local job = self.job
    local p = job:step(budget)
    if self.job ~= job or not self.request then return end
    self.progress = p
    if p.state == "running" then return end
    self.job = nil
    if p.state == "done" and self:observe(p.model) then return end
    local status = job.model and job.model.status
    if status == "context_changed" or status == "no_player" then self.completed = nil end
    self:cancel_refresh(p.error or "Stock interval did not complete in the requested scope")
end

-- Finish the optional box file from an already completed collection. This
-- path never resolves a ship, starts an interval or requests a catalog read.
function Controller:settle_output(budget, now)
    if self.request or not self.bound or budget:expired() then return end
    if not self.diskJob and not self.pendingSnapshot and not self.snapshotWork then return end
    self.now = math.max(self.now, tonumber(now) or self.now)
    if self.snapshotWork and processing.get("box_cache_write", true) then self:_snapshot_step(budget) end
    if not budget:expired() then self:_disk_step(budget, true) end
end

function Controller:progress_signature()
    local job, disk = self.job or {}, self.diskJob or {}
    return table.concat({ self.state, tostring(self.revision),
        tostring(job.phase or ""), tostring(job.processed or job.completed or job.steps or 0),
        tostring(disk.phase or ""), tostring(disk.processed or disk.completed or disk.steps or 0),
        tostring(self.snapshotWork and self.snapshotWork.index or 0) }, ":")
end

function Controller:is_busy() return self.request ~= nil or self.diskJob ~= nil end

function Controller:quiesce(budget)
    self:cancel_refresh("Developer processing policy is changing")
    if self.diskJob then
        self:_disk_step(budget, true)
        if self.diskJob then return false end
    end
    return true
end

function Controller:reset_derived()
    if self:is_busy() then return false, "Stock cache tasks must settle before a cold reset" end
    self:_discard_scope("Stock cache reset")
    self.state, self.error = self.bound and "idle" or "inactive", nil
    return true
end

return M
