-- Small bounded frontend edit lane. Authoritative codecs, merge and file work
-- remain in save_store; queued edits never capture a native object or save API.
local document = require("paintmanager.state_document")
local transfer_quantity = require("paintmanager.transfer_quantity")
local languages = require("paintmanager.languages")
local M = {}
local Controller = {}; Controller.__index = Controller
local MAX_PENDING = 64
local setting_keys = { shipRadiusCm = true, sort = true, lastPage = true, showNames = true,
    tileDensity = true, showDeveloperTools = true }
for key in pairs(document.optional_setting_defaults) do setting_keys[key] = true end
local sorts = { name_asc = true, name_desc = true, count_asc = true, count_desc = true }
local pages = { learned = true, ready = true, missing = true, settings = true, presets = true }
local function valid_setting(key, value)
    if key == "uiLanguage" then return languages.valid(value) end
    if key == "paintTransferQuantity" then return transfer_quantity.valid(value) end
    if key == "shipRadiusCm" then
        return type(value) == "number" and value == value and value % 1 == 0 and value >= 3500 and value <= 5000
    end
    if key == "sort" then return type(value) == "string" and sorts[value] == true end
    if key == "lastPage" then return type(value) == "string" and pages[value] == true end
    if key == "tileDensity" then return value == "comfortable" or value == "compact" end
    return (key == "showNames" or key == "showDeveloperTools" or type(document.optional_setting_defaults[key]) == "boolean")
        and type(value) == "boolean"
end
local function defaults()
    local settings = { shipRadiusCm = 3500, sort = "name_asc", lastPage = "learned",
        showNames = false, tileDensity = "comfortable", showDeveloperTools = false }
    for key, value in pairs(document.optional_setting_defaults) do settings[key] = value end
    return settings
end
local function copy(value)
    local result = {}
    for key, item in pairs(value) do result[key] = item end
    return result
end
local function scope(token, snapshot)
    return { token = token, accountId = snapshot.accountId, scopeId = snapshot.scopeId,
        path = snapshot.path, loadGeneration = snapshot.loadGeneration }
end
local function same_scope(a, b)
    return a and b and a.token == b.token and a.accountId == b.accountId and a.scopeId == b.scopeId
        and a.path == b.path and a.loadGeneration == b.loadGeneration
end

function M.new(callbacks)
    callbacks = callbacks or {}
    local initial = defaults()
    return setmetatable({ default_settings = initial, callbacks = callbacks, settings = copy(initial),
        favorites = {}, pending = {}, by_key = {}, loaded = false }, Controller)
end
function Controller:_ready()
    if type(self.callbacks.ready) ~= "function" then return nil end
    local ok, token, snapshot = pcall(self.callbacks.ready)
    if not ok or type(token) ~= "string" or token == "" or type(snapshot) ~= "table" then return nil end
    return token, snapshot, scope(token, snapshot)
end
function Controller:_view()
    local settings, favorites = copy(self.settings), copy(self.favorites)
    local function apply(entry)
        if entry.kind == "setting" then settings[entry.name] = entry.value
        else favorites[entry.identity] = entry.value end
    end
    if self.active then for _, entry in ipairs(self.active.entries) do apply(entry) end end
    for _, entry in ipairs(self.pending) do apply(entry) end
    return settings, favorites
end
function Controller:_publish()
    if type(self.callbacks.publish) == "function" then self.callbacks.publish(self:_view()) end
end
function Controller:_error(code, detail)
    if type(self.callbacks.error) == "function" then self.callbacks.error(code, detail) end
end
function Controller:reset()
    if self.active and self.active.job and type(self.active.job.cancel) == "function" then
        pcall(self.active.job.cancel, self.active.job, "Preference scope retired")
    end
    self.active, self.bound, self.loaded = nil, nil, false
    self.settings, self.favorites, self.pending, self.by_key = copy(self.default_settings), {}, {}, {}
    self:_publish()
end
function Controller:load(value)
    local sections = type(value) == "table" and value.sections
    local settings = sections and sections.settings and sections.settings.data
    local favorites = sections and sections.favorites and sections.favorites.data
    if type(settings) ~= "table" or type(favorites) ~= "table" or type(favorites.records) ~= "table"
        or #favorites.records > document.MAX_FAVORITES then return false, "preferences_not_ready" end
    local next_settings, next_favorites = copy(self.default_settings), {}
    for key in pairs(setting_keys) do
        if settings[key] ~= nil then
            if not valid_setting(key, settings[key]) then return false, "preferences_invalid_setting" end
            next_settings[key] = settings[key]
        end
    end
    for _, record in ipairs(favorites.records) do
        if type(record) ~= "table" or type(record.enabled) ~= "boolean" then return false, "preferences_invalid_favorite" end
        local ok, identity = pcall(document.favorite_key, record.assetType, record.assetName)
        if not ok then return false, "preferences_invalid_favorite" end
        next_favorites[identity] = record.enabled
    end
    local _, _, current = self:_ready()
    if current and self.bound and not same_scope(current, self.bound) then self:reset() end
    self.settings, self.favorites, self.loaded = next_settings, next_favorites, true
    self.bound = current or self.bound
    self:_publish()
    return true
end
function Controller:_admit()
    if not self.loaded then return false, "preferences_not_ready" end
    local _, _, current = self:_ready()
    if not current then return false, "preferences_not_ready" end
    if self.bound and not same_scope(self.bound, current) then
        self:reset(); return false, "preferences_not_ready"
    end
    self.bound = current
    return true
end
function Controller:_queue(entry)
    local previous = self.by_key[entry.key]
    if previous then previous.value = entry.value
    else self.pending[#self.pending + 1], self.by_key[entry.key] = entry, entry end
end
function Controller:change_settings(changes)
    if type(changes) ~= "table" or getmetatable(changes) ~= nil then return false, "preferences_invalid_setting" end
    for key, value in pairs(changes) do
        if not setting_keys[key] or not valid_setting(key, value) then
            return false, "preferences_invalid_setting"
        end
    end
    local admitted, reason = self:_admit()
    if not admitted then return false, reason end
    local settings = self:_view()
    local edits, extra = {}, 0
    for key, value in pairs(changes) do
        if settings[key] ~= value then
            local entry = { kind = "setting", key = "setting:" .. key, name = key, value = value }
            edits[#edits + 1] = entry
            if not self.by_key[entry.key] then extra = extra + 1 end
        end
    end
    if #self.pending + extra > MAX_PENDING then return false, "preferences_queue_full" end
    if #edits == 0 then return true, "preferences_unchanged" end
    for _, entry in ipairs(edits) do self:_queue(entry) end
    self:_publish()
    return true, "preferences_queued"
end
function Controller:favorite(asset_type, asset_name, enabled)
    local ok, identity = pcall(document.favorite_key, asset_type, asset_name)
    if not ok or type(enabled) ~= "boolean" then return false, "preferences_invalid_favorite" end
    local admitted, reason = self:_admit()
    if not admitted then return false, reason end
    local _, favorites = self:_view()
    if (favorites[identity] == true) == enabled then return true, "preferences_unchanged" end
    local key = "favorite:" .. identity
    if not self.by_key[key] and #self.pending >= MAX_PENDING then return false, "preferences_queue_full" end
    self:_queue({ kind = "favorite", key = key, identity = identity, assetType = asset_type,
        assetName = asset_name, value = enabled })
    self:_publish()
    return true, "preferences_queued"
end
function Controller:is_busy() return self.active ~= nil or #self.pending > 0 end

local function single_budget(parent)
    local result = { used = 0 }
    function result:expired() return self.used >= 1 or parent and parent:expired() or false end
    function result:consume(units)
        units = units or 1
        self.used = self.used + units
        if parent then parent:consume(units) end
    end
    return setmetatable(result, { __index = function(_, key)
        local value = parent and parent[key]
        if type(value) == "function" then return function(_, ...) return value(parent, ...) end end
        return value
    end })
end
function Controller:_settle(progress, value)
    local active = self.active
    local _, _, current = self:_ready()
    if current and active and not same_scope(current, active.scope) then self:reset(); return end
    self.active = nil
    if progress.state == "done" and value then
        -- Rebase before applying later edits. A new request for the same key
        -- stays in pending and cannot be rolled back by the earlier receipt.
        local loaded = self:load(value)
        if not loaded then
            progress = { state = "error", error = "Preference job returned an invalid document",
                localCommitted = progress.localCommitted, localUncertain = progress.localUncertain }
            value = nil
            self:_publish()
        end
    else self:_publish() end
    if type(self.callbacks.settled) == "function" then self.callbacks.settled(progress, value, active.snapshot) end
    if progress.state ~= "done" then
        self:_error((progress.localCommitted or progress.localUncertain or progress.publicationPending)
            and "preferences_save_uncertain" or "preferences_save_failed", progress.error or progress.reason)
    end
    return active
end
function Controller:step(budget, allow_start)
    if budget and budget:expired() then return end
    local token, snapshot, current = self:_ready()
    if current and self.bound and not same_scope(current, self.bound) then
        self:reset()
        if budget then budget:consume(1) end
        return
    end
    if self.active then
        local active, one = self.active, single_budget(budget)
        local ok, progress = pcall(active.job.step, active.job, one)
        if budget and one.used == 0 then budget:consume(1) end
        if self.active ~= active then return end
        if not ok then progress = { state = "error", error = tostring(progress) } end
        if type(progress) ~= "table" then progress = { state = "error", error = "Preference job returned no progress" } end
        if progress.state ~= "running" then
            local value
            if progress.state == "done" then
                local result_ok, result = pcall(active.job.get_result, active.job)
                if result_ok then value = result end
                if not value then
                    progress = copy(progress)
                    progress.state, progress.error = "error", "Preference job completed without a document"
                end
            end
            self:_settle(progress, value)
        end
        return
    end
    if not allow_start or #self.pending == 0 or not current then return end
    local entries, first = {}, self.pending[1]
    if first.kind == "setting" then
        for _, entry in ipairs(self.pending) do if entry.kind == "setting" then entries[#entries + 1] = entry end end
    else entries[1] = first end
    for index = #self.pending, 1, -1 do
        local entry = self.pending[index]
        if entry == first or first.kind == "setting" and entry.kind == "setting" then
            self.by_key[entry.key] = nil; table.remove(self.pending, index)
        end
    end
    self.active = { entries = entries, scope = current, snapshot = snapshot }
    local ok, job = pcall(function()
        local store = self.callbacks.store(token, snapshot)
        if first.kind == "favorite" then return store.begin_favorite(first.assetType, first.assetName, first.value) end
        local changes = {}
        for _, entry in ipairs(entries) do changes[entry.name] = entry.value end
        return store.begin_ui_settings(changes)
    end)
    if budget then budget:consume(1) end
    if ok and type(job) == "table" and type(job.step) == "function" and type(job.get_result) == "function" then
        self.active.job = job
    else self:_settle({ state = "error", error = tostring(job) }, nil) end
end

return M
