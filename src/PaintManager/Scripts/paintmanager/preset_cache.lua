-- Session-only projection of a reconciled library. Storage owns all writes.
local preset_index = require("paintmanager.preset_index")
local M = {}
M.__index = M

function M.new()
    return setmetatable({}, M)
end

function M:clear(reason)
    self.token, self.snapshot, self.library, self.index, self.job = nil, nil, nil, nil, nil
    self.browsed = nil
    self.error = reason
end

function M:same_scope(token, current)
    local old = self.snapshot
    return old and token == self.token and old.scopeId == current.scopeId
        and old.accountId == current.accountId and old.path == current.libraryPath
        and old.loadGeneration == current.loadGeneration
end

function M:publish(token, progress, library, failure, preferences_base)
    local snapshot = progress.snapshot
    if not (token and library and type(library.presets) == "table" and progress.staged and not progress.error
        and not progress.localUncertain and not progress.mirrorPending and snapshot and snapshot.scopeId
        and snapshot.accountId and snapshot.path and snapshot.loadGeneration and type(snapshot.raw) == "string"
        and snapshot.localSnapshot and snapshot.localSnapshot.raw == snapshot.raw
        and snapshot.localSnapshot.missing == false and snapshot.localSnapshot.backupRaw == nil) then
        self:clear(progress.error or failure)
        return
    end
    if self.token == token and self.snapshot and self.snapshot.scopeId == snapshot.scopeId
        and self.snapshot.accountId == snapshot.accountId and self.snapshot.path == snapshot.path
        and self.snapshot.loadGeneration == snapshot.loadGeneration
        and (self.snapshot.raw == snapshot.raw or self.snapshot == preferences_base) then
        -- Exact same document, or a preferences-only transaction guarded by
        -- both replicas of this exact document. Its preset records are identical.
        self.snapshot = snapshot
        return
    end
    self:clear()
    self.token, self.snapshot, self.library = token, snapshot, library
    self.job = preset_index.begin(library)
end

function M:preview()
    if not self.index then return end
    self.browsed = self.browsed or setmetatable({}, { __mode = "k" })
    self.browsed[self.snapshot] = true
    return self.library, self.snapshot, nil, self.index
end

function M:save_base(token, snapshot)
    if self.token == token and self.browsed and self.browsed[snapshot] then return self.snapshot end
    return snapshot
end

function M:step(budget)
    if not self.job or budget:expired() then return end
    local progress = self.job:step(budget)
    if progress.state == "running" then return end
    if progress.state == "done" then
        self.index, self.job = self.job:get_result(), nil
    else self:clear(progress.error or "Could not prepare saved presets") end
end

return M
