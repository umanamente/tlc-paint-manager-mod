-- Pure Lua storage stages. Native scope and HUD calls remain in the separate
-- coordinator; this job owns only copied data, file handles and its path lease.
local document = require("paintmanager.state_document")
local repository = require("paintmanager.local_library")
local legacy = require("paintmanager.preset_store")
local schema = require("paintmanager.library_schema")
local json = require("paintmanager.json")
local M = {}
local Job = {}; Job.__index = Job

local function same_snapshot(left, right)
    return left and right and left.missing == right.missing and left.raw == right.raw
        and left.backupRaw == right.backupRaw
end
local function trusted_path(request, control)
    local path = request.path
    assert(type(path) == "string" and #path > 0 and #path <= 4096 and not path:find("[%z\r\n]"),
        "Invalid frozen account library path")
    if not control.allow_test_paths then
        local account = request.accountId
        assert(type(account) == "string" and #account == 17 and account:match("^765%d+$"),
            "Invalid frozen native account identity")
        local normalized = path:gsub("\\", "/")
        assert(normalized:match("^%a:/") or normalized:sub(1, 2) == "//", "Account library path must be absolute")
        assert(not normalized:find("/../", 1, true) and not normalized:find("/./", 1, true),
            "Account library path contains traversal")
        local suffix = "/" .. account .. "/PaintManager.library.json"
        assert(normalized:sub(-#suffix) == suffix,
            "Library path is not bound to the frozen native account")
    end
    return path
end

function M.begin(request, control)
    assert(type(request) == "table" and getmetatable(request) == nil, "Storage request must be plain data")
    control = control or {}
    assert(type(control.is_current) == "function" and type(control.reserve_commit) == "function",
        "Storage requires ownership and commit controls")
    assert(type(control.fs) == "table", "Storage requires a filesystem adapter")
    assert(request.compactResults == nil or type(request.compactResults) == "boolean",
        "Invalid storage result policy")
    if request.reusePreview then
        assert(request.operation == "verify" and request.reusePreview == true and not request.change,
            "A primary proof cannot perform a storage edit")
        local expected = request.expectedLocalSnapshot
        assert(type(expected) == "table" and expected.missing == false and expected.backupRaw == nil
            and type(expected.raw) == "string" and #expected.raw > 0 and #expected.raw <= document.MAX_BYTES
            and request.nativeRaw == expected.raw, "Library preview sources do not match")
    end
    if request.operation == "verify" then
        assert(type(request.expectedLocalSnapshot) == "table" and request.expectedLocalSnapshot.missing == false
            and type(request.expectedLocalSnapshot.raw) == "string", "A verified primary snapshot is required for a proof lease")
        request.nativeRaw, request.change = request.expectedLocalSnapshot.raw, nil
    end
    assert(type(request.now) == "number" and request.now % 1 == 0 and request.now >= 0
        and request.now <= 9007199254740991, "Storage edit time must be frozen before submission")
    local path = trusted_path(request, control)
    return setmetatable({ request = request, control = control, path = path,
        state = request.reusePreview and "prepared" or "running",
        phase = request.reusePreview and "prepared" or "native_decode_start", processed = 0,
        local_snapshot = request.reusePreview and request.expectedLocalSnapshot or nil,
        change = request.change, missing = request.nativeRaw == nil,
        cleanup_errors = json.array() }, Job)
end

function Job:_current()
    if self.commit_reserved then return true end
    local current, reason = self.control.is_current()
    assert(current == true, reason or "Storage generation was invalidated")
    return true
end
function Job:_child()
    self:_current()
    local progress = self.child:step_one()
    assert(progress.state == "running" or progress.state == "done", progress.error or "Worker storage child stopped")
    return progress.state == "done", progress
end
function Job:_result()
    local result = {
        localCommitted = self.local_committed == true, localUncertain = self.local_uncertain == true,
        cleanupErrors = self.cleanup_errors, mirrorOutcome = self.mirror_outcome, error = self.error }
    if not self.request.compactResults or self.state == "prepared" then
        result.data, result.encoded = self.data, self.encoded
        result.matchedPresetId = self.matchedPresetId
        result.nativeSourceMissing, result.nativeSourceMatchesResult = self.missing, self.request.nativeRaw == self.encoded
        result.nativeSourceLive, result.nativeSourceDeleted = self.native_live, self.native_deleted
    end
    if not self.request.compactResults or self.state ~= "done" then result.localSnapshot = self.local_snapshot end
    if self.request.reusePreview and self.state == "local_committed" and self.child then
        result.lockToken = self.child.lock_token
    end
    return result
end
function Job:get_result()
    if self.state == "prepared" or self.state == "local_committed" or self.state == "done" then return self:_result() end
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, processed = self.processed, error = self.error,
        commitReserved = self.commit_reserved == true, localCommitted = self.local_committed == true,
        localUncertain = self.local_uncertain == true, cleanupErrors = self.cleanup_errors }
end
function Job:_unit()
    if not self.commit_reserved then self:_current() end
    if self.phase == "native_decode_start" then
        if self.missing then
            self.native, self.phase = document.new(), "local_read"
        else
            assert(type(self.request.nativeRaw) == "string", "Native source must be owned raw bytes")
            self.child, self.phase = document.begin_decode(self.request.nativeRaw), "native_decode"
        end
    elseif self.phase == "native_decode" then
        if self:_child() then
            self.native, self.encoded = self.child:get_result(), self.child:get_encoded()
            self.native_live, self.native_deleted = #self.native.sections.presets.data.presets,
                #self.native.sections.presets.data.tombstones
            self.child, self.phase = nil, "local_read"
        end
    elseif self.phase == "local_read" then
        self.child, self.phase = repository.begin_read(self.path, { fs = self.control.fs }), "local_decode"
    elseif self.phase == "local_decode" then
        local done, progress = self:_child()
        if done then
            self.local_snapshot = progress.snapshot
            if self.request.expectedLocalSnapshot then
                assert(same_snapshot(self.local_snapshot, self.request.expectedLocalSnapshot),
                    "Local library changed since the preview; refresh before continuing")
            end
            local raw = self.local_snapshot.raw or self.local_snapshot.backupRaw
            if raw == nil or not self.missing and raw == self.request.nativeRaw then
                self.data, self.child, self.phase = self.native, nil, "state_change"
            else self.child, self.phase = document.begin_decode(raw), "local_document" end
        end
    elseif self.phase == "local_document" then
        if self:_child() then
            if self.missing then
                self.data, self.encoded = self.child:get_result(), self.child:get_encoded()
                self.child, self.phase = nil, "state_change"
            else self.child, self.phase = document.begin_merge(self.child:get_result(), self.native), "state_merge" end
        end
    elseif self.phase == "state_merge" or self.phase == "import_merge" then
        if self:_child() then
            self.data, self.encoded = self.child:get_result(), self.child:get_encoded()
            self.child, self.phase = nil, "state_change"
        end
    elseif self.phase == "state_change" then
        if self.change and self.change.import_path then
            self.child, self.phase = legacy.begin_load(self.change.import_path, { fs = self.control.fs }), "legacy_read"
            self.change = nil
        elseif self.change and self.change.library then
            self.child, self.phase = schema.begin_validate(self.change.library), "library_validate"
            self.change = nil
        elseif self.change then
            self.child, self.phase = document.begin_update(self.data, self.change, { now = self.request.now }), "state_update"
            self.change = nil
        elseif self.encoded then self.state, self.phase = "prepared", "prepared"
        else self.child, self.phase = document.begin_encode(self.data), "state_encode" end
    elseif self.phase == "library_validate" then
        if self:_child() then
            self.pending_library, self.child, self.update_index, self.phase = self.child:get_result(), nil, 1, "library_update"
            assert(#self.pending_library.tombstones == 0, "Use explicit deletion or replica import for tombstones")
        end
    elseif self.phase == "library_update" then
        local preset = self.pending_library.presets[self.update_index]
        if preset then
            self.child, self.phase = document.begin_update(self.data, { preset = preset }, { now = self.request.now }), "library_updated"
            self.update_index = self.update_index + 1
        else self.pending_library, self.phase = nil, "state_change" end
    elseif self.phase == "library_updated" or self.phase == "state_update" then
        if self:_child() then
            self.matchedPresetId = self.child.matchedPresetId
            self.data, self.encoded = self.child:get_result(), self.child:get_encoded()
            self.child = nil
            self.phase = self.phase == "library_updated" and "library_update" or "state_change"
        end
    elseif self.phase == "legacy_read" then
        if self:_child() then
            self.child, self.phase = document.begin_import(self.child:get_result()), "legacy_migrate"
        end
    elseif self.phase == "legacy_migrate" then
        if self:_child() then self.child, self.phase = document.begin_merge(self.data, self.child:get_result()), "import_merge" end
    elseif self.phase == "state_encode" then
        if self:_child() then
            self.encoded, self.child, self.state, self.phase = self.child:get_result(), nil, "prepared", "prepared"
        end
    elseif self.phase == "publish_start" then
        local options = { fs = self.control.fs, encoded = self.encoded, hold_for_mirror = true,
            temp_token = self.request.tempToken,
            lease_only = self.local_snapshot.raw == self.encoded,
            is_current = function() return self:_current() end,
            reserve_commit = function()
                local accepted, reason = self.control.reserve_commit()
                if accepted then self.commit_reserved = true end
                return accepted, reason
            end }
        if self.request.reusePreview then
            self.child = repository.begin_primary_lease(self.path, self.local_snapshot, options)
        else
            self.child = repository.begin_validated_save(self.path, self.data, self.local_snapshot, self.encoded, options)
        end
        self.phase = "local_publish"
    elseif self.phase == "local_publish" then
        local progress = self.child:step_one()
        if progress.localCommitted then self.local_committed = true end
        if progress.state == "awaiting_mirror" then
            self.local_snapshot, self.state, self.phase = progress.snapshot, "local_committed", "local_committed"
            self.cleanup_errors = progress.cleanupErrors or self.cleanup_errors
        elseif progress.state == "error" and progress.localCommitted then
            self.local_snapshot, self.local_uncertain, self.error = progress.snapshot, true, progress.error
            self.cleanup_errors = progress.cleanupErrors or self.cleanup_errors
            self.child, self.state, self.phase = nil, "local_committed", "local_committed"
        else assert(progress.state == "running", progress.error or "Local publication stopped unexpectedly") end
    end
end
function Job:step_one()
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(self._unit, self)
    self.processed = self.processed + 1
    if not ok then
        if self.child then
            local stopped = self.child:cancel(tostring(err))
            if stopped and stopped.localCommitted then self.local_committed = true end
            self.cleanup_errors = stopped and stopped.cleanupErrors or self.cleanup_errors
        end
        self.child, self.state, self.phase, self.error = nil, "error", "error", tostring(err)
    end
    return self:get_progress()
end
function Job:authorize_publish(permit)
    assert(self.state == "prepared", "Storage job has no prepared publication")
    assert(permit == true or type(permit) == "table", "Storage publication requires a fresh native permit")
    self:_current()
    self.state, self.phase = "running", "publish_start"
    return self:get_progress()
end
function Job:finish_stage(outcome)
    assert(self.state == "local_committed", "Storage job has no local publication to acknowledge")
    self.mirror_outcome = outcome or "pending"
    if self.child then
        local progress = self.child:finish_stage(self.mirror_outcome)
        self.cleanup_errors = progress.cleanupErrors or self.cleanup_errors
        if progress.state ~= "done" then self.error = progress.error or "Local writer lease cleanup failed" end
    end
    self.child, self.state, self.phase = nil, "done", "done"
    return self:get_progress()
end
function Job:cancel(reason)
    if self.state == "local_committed" then return self:finish_stage("pending") end
    if self.state == "done" or self.state == "error" or self.state == "cancelled" then return self:get_progress() end
    if self.commit_reserved then
        self.cancel_requested = reason or "Storage request cancelled after commit reservation"
        return self:get_progress()
    end
    if self.child then
        local progress = self.child:cancel(reason or "Storage request cancelled")
        self.cleanup_errors = progress.cleanupErrors or self.cleanup_errors
    end
    self.child, self.state, self.phase, self.error = nil, "cancelled", "cancelled", reason
    return self:get_progress()
end
return M
