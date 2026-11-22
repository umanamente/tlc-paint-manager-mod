-- Account-local aggregate with an ordinary-save HUD mirror. Local commit and
-- native staging are ordered, recoverable operations, never one transaction.
local processing = require("paintmanager.processing")
local document = require("paintmanager.state_document")
local bridge = require("paintmanager.save_bridge")
local repository = require("paintmanager.local_library")
local legacy = require("paintmanager.preset_store")
local plain_work = require("paintmanager.plain_work")
local worker_store = require("paintmanager.worker_save_store")
local M = {}
local Job = {}; Job.__index = Job
local pure_phases = { native_decode = true, local_document = true, state_merge = true,
    import_merge = true, library_validate = true, library_updated = true, state_update = true,
    legacy_migrate = true, state_encode = true }
local function snapshot_copy(value)
    if not value then return value end
    local copy = {}
    for key, item in pairs(value) do
        if key == "localSnapshot" then
            copy.localSnapshot = { raw = item.raw, missing = item.missing, backupRaw = item.backupRaw }
        else copy[key] = item end
    end
    return copy
end

function M.new(config, token, build, expected_snapshot, options)
    options = options or {}
    expected_snapshot = snapshot_copy(expected_snapshot)
    local store, anchor = {}, nil
    local native_worker = worker_store.available(options)
    store.backend = "lua_cooperative"
    local function begin(change, library_only)
        if native_worker then
            return worker_store.begin(config, token, build, change, library_only,
                expected_snapshot, options)
        end
        return setmetatable({ state = "running", phase = "state_read", processed = 0,
            token = token, config = config, build = build,
            change = change, library_only = library_only, expected = expected_snapshot,
            options = options }, Job)
    end
    function store.begin_load()
        anchor = nil
        local job = begin(nil, true)
        job.on_loaded = function(snapshot) anchor = snapshot end
        return job
    end
    function store.begin_state_load() return begin() end
    function store.begin_ui_settings(changes)
        if changes == nil then changes = false end
        return begin({ uiSettings = changes })
    end
    function store.begin_favorite(asset_type, asset_name, enabled)
        return begin({ favorite = { assetType = asset_type, assetName = asset_name, enabled = enabled } })
    end
    function store.begin_add(preset) return begin({ preset = preset }, true) end
    function store.begin_save_layout(preset) return begin({ preset = preset, uniqueLayout = true }, true) end
    function store.new_id()
        local value, err = bridge.new_id(token)
        return assert(value, err or "Native UUID generation failed")
    end
    function store.begin_delete(id) return begin({ deleteId = id }, true) end
    function store.begin_import(path) return begin({ import_path = path }) end
    -- Absence never deletes a record. Deletion is an explicit tombstone operation.
    function store.begin_save(_, library)
        local job = begin({ library = library }, true)
        job.expected = expected_snapshot or anchor or false
        anchor = nil
        return job
    end
    function store.verify_snapshot(snapshot)
        if build ~= config.reference_build or type(snapshot) ~= "table" then
            return false, "Validated library snapshot is unavailable"
        end
        if native_worker then return worker_store.verify_snapshot(token, snapshot) end
        local current = bridge.storage_context(token)
        if current.status ~= "ready" or current.loadGeneration ~= snapshot.loadGeneration or current.accountId ~= snapshot.accountId
            or current.libraryPath ~= snapshot.path then return false, current.error or "Storage account changed" end
        local value = bridge.read(token, document.MAX_BYTES)
        if (value.status ~= "present" and value.status ~= "missing")
            or value.raw ~= snapshot.raw or value.scopeId ~= snapshot.scopeId then
            return false, value.error or "Game mirror changed; refresh before applying a configuration"
        end
        return repository.verify_snapshot(snapshot.path, snapshot.localSnapshot, options.repository)
    end
    if native_worker then
        function store.has_verified_preview(snapshot)
            return worker_store.has_verified_preview(token, snapshot)
        end
        function store.begin_verify_snapshot(snapshot, reuse_preview)
            return worker_store.begin(config, token, build, nil, false, snapshot, options, true, reuse_preview)
        end
        function store.release_snapshot(snapshot) return worker_store.release_snapshot(snapshot) end
    end
    return store
end

function Job:get_result() return self.state == "done" and self.result or nil end
function Job:get_progress()
    return { state = self.state, phase = self.phase, processed = self.processed,
        total = self.processed + (self.state == "running" and 1 or 0), error = self.error, defer = self.child_defer == true,
        staged = self.staged == true, localCommitted = self.local_committed == true,
        mirrorPending = self.mirror_pending == true, missing = self.missing,
        revision = self.data and self.data.revision, scopeId = self.scope_id,
        outcome = self.outcome, snapshot = self.state == "done" and self.snapshot or nil,
        matchedPresetId = self.state == "done" and self.matchedPresetId or nil,
        nativeSourceBytes = self.raw and #self.raw or 0, nativeSourceMissing = self.missing,
        nativeSourceMatchesResult = self.native_source_matches,
        nativeSourceLive = self.native_live, nativeSourceDeleted = self.native_deleted }
end
function Job:cancel(reason)
    if self.state ~= "running" then return self:get_progress() end
    if self.child then
        local progress = self.child:cancel(reason)
        if progress and progress.localCommitted then self.local_committed = true end
    end
    if self.publication then self.publication:cancel(reason); self.publication = nil end
    self.mirror_pending = self.local_committed and not self.staged
    self.state, self.phase, self.error, self.result = "cancelled", "cancelled", reason, nil
    self.child = nil
    return self:get_progress()
end
function Job:_child(budget)
    local progress
    if pure_phases[self.phase] then progress = plain_work.run(function() return self.child:step_one() end, budget,
        { group = (self.phase == "state_merge" or self.phase == "import_merge") and "merge_primitives" or "schema_primitives" })
    else progress = self.child:step_one(budget) end
    if not progress then return false, { state = "running" } end
    self.child_defer = progress.defer == true
    if progress.localCommitted then self.local_committed = true end
    if progress.state == "error" and progress.localCommitted and self.phase == "local_publish" then
        self.local_snapshot, self.outcome, self.mirror_pending = progress.snapshot, "local_uncertain", true
        self.error = progress.error or "Local publication could not be verified"
        self:_finish()
        return false, progress
    end
    assert(progress.state == "running" or progress.state == "done" or progress.state == "awaiting_mirror",
        progress.error or "Storage work stopped")
    return progress.state == "done" or progress.state == "awaiting_mirror", progress
end
function Job:_current()
    local current = bridge.storage_context(self.token)
    assert(current.status == "ready", current.error or "Player progress restoration is not ready")
    assert(current.accountId == self.account_id and current.libraryPath == self.path,
        "Storage account changed during reconciliation")
    assert(current.loadGeneration == self.load_generation, "Game load changed during reconciliation")
    local value = bridge.read(self.token, document.MAX_BYTES)
    assert(value.status == "present" or value.status == "missing", value.error or "Game mirror is unavailable")
    assert(value.raw == self.raw and value.scopeId == self.scope_id, "Game mirror changed during reconciliation")
    return true
end
function Job:_finish()
    if self.publication and self.publication:get_progress().state == "awaiting_mirror" then
        local released = self.publication:finish_stage(self.staged and "staged" or "pending")
        if released.state ~= "done" then
            self.error = released.error or "Local writer lease release failed"
            self.mirror_pending = not self.staged
        end
        self.publication = nil
    end
    self.snapshot = { raw = self.staged and self.encoded or self.raw, scopeId = self.scope_id,
        accountId = self.account_id, path = self.path, localSnapshot = self.local_snapshot }
    self.snapshot.loadGeneration = self.load_generation
    self.result = self.library_only and self.data.sections.presets.data or self.data
    if self.on_loaded then self.on_loaded(self.snapshot); self.on_loaded = nil end
    self.state, self.phase, self.child = "done", "done", nil
end
function Job:_unit(budget)
    if self.phase == "state_read" then
        assert(self.build == self.config.reference_build, "Storage is unavailable on this unverified game build")
        assert(self.expected ~= false, "Reload the configuration library before saving it")
        local current = bridge.storage_context(self.token)
        assert(current.status == "ready", current.error or "Player progress restoration is not ready")
        self.account_id, self.path = current.accountId, current.libraryPath
        self.load_generation = current.loadGeneration
        local value = bridge.read(self.token, document.MAX_BYTES)
        assert(value.status == "present" or value.status == "missing", value.error or "Game mirror could not be read")
        self.raw, self.scope_id, self.missing = value.raw, value.scopeId, value.status == "missing"
        if self.expected then
            assert(self.expected.raw == self.raw and self.expected.scopeId == self.scope_id
                and self.expected.accountId == self.account_id and self.expected.path == self.path,
                "Library preview changed; refresh before continuing")
            assert(self.expected.loadGeneration == self.load_generation, "Game loaded again since the library preview")
        end
        if self.missing then self.native, self.phase = document.new(), "local_read"
        else self.child, self.phase = document.begin_decode(self.raw), "native_decode" end
    elseif self.phase == "native_decode" then
        if self:_child(budget) then
            self.encoded = self.child:get_encoded()
            self.native, self.child, self.phase = self.child:get_result(), nil, "local_read"
            self.native_live = #self.native.sections.presets.data.presets
            self.native_deleted = #self.native.sections.presets.data.tombstones
        end
    elseif self.phase == "local_read" then
        self.child, self.phase = repository.begin_read(self.path, self.options.repository), "local_decode"
    elseif self.phase == "local_decode" then
        local done, progress = self:_child(budget)
        if done then
            self.local_snapshot = progress.snapshot
            if self.expected then
                assert(self.local_snapshot.raw == self.expected.localSnapshot.raw
                    and self.local_snapshot.missing == self.expected.localSnapshot.missing
                    and self.local_snapshot.backupRaw == self.expected.localSnapshot.backupRaw,
                    "Local library changed since the preview; refresh before continuing")
            end
            local raw = self.local_snapshot.raw or self.local_snapshot.backupRaw
            if raw == nil or not self.missing and raw == self.raw then
                -- Exact same bytes are already validated by native_decode.
                -- Absence contributes neither a reset nor a deletion.
                self.data, self.child, self.phase = self.native, nil, "state_change"
            else self.child, self.phase = document.begin_decode(raw), "local_document" end
        end
    elseif self.phase == "local_document" then
        if self:_child(budget) then
            local local_data = self.child:get_result()
            if self.missing then
                self.data, self.encoded, self.child, self.phase = local_data, self.child:get_encoded(), nil, "state_change"
            else self.child, self.phase = document.begin_merge(local_data, self.native), "state_merge" end
        end
    elseif self.phase == "state_merge" or self.phase == "import_merge" then
        if self:_child(budget) then
            self.encoded = self.child:get_encoded()
            self.data, self.child, self.phase = self.child:get_result(), nil, "state_change"
        end
    elseif self.phase == "state_change" then
        if self.change and self.change.import_path then
            self.child, self.phase = legacy.begin_load(self.change.import_path), "legacy_read"
            self.change = nil
        elseif self.change and self.change.library then
            self.child = require("paintmanager.library_schema").begin_validate(self.change.library)
            self.change, self.phase = nil, "library_validate"
        elseif self.change then
            self.child, self.phase = document.begin_update(self.data, self.change, self.options.document), "state_update"
            self.change = nil
        elseif self.encoded then self.phase = "state_preflight"
        else self.child, self.phase = document.begin_encode(self.data), "state_encode" end
    elseif self.phase == "library_validate" then
        if self:_child(budget) then
            self.pending_library, self.child, self.update_index, self.phase = self.child:get_result(), nil, 1, "library_update"
            assert(#self.pending_library.tombstones == 0, "Use explicit deletion or replica import for tombstones")
        end
    elseif self.phase == "library_update" then
        local preset = self.pending_library.presets[self.update_index]
        if preset then
            self.child, self.phase = document.begin_update(self.data, { preset = preset }, self.options.document), "library_updated"
            self.update_index = self.update_index + 1
        else self.pending_library, self.phase = nil, "state_change" end
    elseif self.phase == "library_updated" or self.phase == "state_update" then
        if self:_child(budget) then
            self.matchedPresetId = self.child.matchedPresetId
            self.encoded = self.child:get_encoded()
            self.data, self.child = self.child:get_result(), nil
            self.phase = self.phase == "library_updated" and "library_update" or "state_change"
        end
    elseif self.phase == "legacy_read" then
        if self:_child(budget) then
            self.child, self.phase = document.begin_import(self.child:get_result()), "legacy_migrate"
        end
    elseif self.phase == "legacy_migrate" then
        if self:_child(budget) then self.child, self.phase = document.begin_merge(self.data, self.child:get_result()), "import_merge" end
    elseif self.phase == "state_encode" then
        if self:_child(budget) then
            self.encoded, self.child, self.phase = self.child:get_result(), nil, "state_preflight"
        end
    elseif self.phase == "state_preflight" then
        self.native_source_matches = self.raw == self.encoded
        self:_current()
        local options = {}
        for key, value in pairs(self.options.repository or {}) do options[key] = value end
        options.is_current = function() return self:_current() end
        options.encoded, options.hold_for_mirror = self.encoded, true
        options.lease_only = self.encoded == self.local_snapshot.raw
        self.child = repository.begin_validated_save(self.path, self.data, self.local_snapshot, self.encoded, options)
        self.phase = "local_publish"
    elseif self.phase == "local_publish" then
        local done, progress = self:_child(budget)
        if done then
            self.local_committed = self.local_committed or progress.localCommitted == true
            self.local_snapshot, self.publication, self.child = progress.snapshot, self.child, nil
            assert(self.local_snapshot.raw == self.encoded, "Local publication differs from the validated mirror payload")
            self.phase = "state_stage"
        end
    elseif self.phase == "state_stage" then
        -- No rollback after local success. Retry reuses the stored IDs/times.
        self.mirror_pending = true
        local ok, value = pcall(function()
            self:_current()
            local fresh, reason = repository.verify_snapshot(self.path, self.local_snapshot, self.options.repository)
            assert(fresh, reason)
            local uncertain = bridge.uncertainty and bridge.uncertainty(self.token)
            if uncertain then return { status = "uncertain", error = uncertain } end
            if self.raw == self.encoded then return { status = "staged" } end
            return bridge.write(self.token, self.raw, self.encoded, self.scope_id)
        end)
        self.outcome = ok and value.status or "pending"
        self.staged = ok and value.status == "staged"
        self.mirror_pending = not self.staged
        self.error = not self.staged and (ok and value.error or tostring(value)) or nil
        self:_finish()
    end
end
function Job:step_one(budget)
    if self.state ~= "running" then return self:get_progress() end
    self.child_defer = false
    local ok, err = pcall(self._unit, self, budget)
    self.processed = self.processed + 1
    if not ok then
        if self.child then pcall(function() self.child:cancel(tostring(err)) end) end
        if self.publication then pcall(function() self.publication:cancel(tostring(err)) end); self.publication = nil end
        self.mirror_pending = self.local_committed and not self.staged
        self.state, self.phase, self.error = "error", "error", tostring(err)
        self.result, self.child = nil, nil
    end
    return self:get_progress()
end
function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do
        self:step_one(budget); budget:consume()
        if self.child_defer then break end
    end
    return self:get_progress()
end
return M
