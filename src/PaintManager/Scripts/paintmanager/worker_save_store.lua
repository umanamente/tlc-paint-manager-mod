-- Game-thread coordinator for bounded Lua storage execution. Keep the writer
-- lease through the native HUD acknowledgement, including unchanged bytes.
local background = require("paintmanager.background")
local bridge = require("paintmanager.save_bridge")
local document = require("paintmanager.state_document")
local repository = require("paintmanager.local_library")
local plain_work = require("paintmanager.plain_work")
local M = {}
local Job = {}; Job.__index = Job
local leases = {}
local previews = setmetatable({}, { __mode = "k" })
local private_documents = setmetatable({}, { __mode = "k" })

local function remember_preview(job)
    local snapshot = job.snapshot
    local data = private_documents[job]
    private_documents[job] = nil
    if job.error or job.local_uncertain or not (job.staged or job.verify_only)
        or type(job.encoded) ~= "string" or #job.encoded == 0 or #job.encoded > document.MAX_BYTES
        or snapshot.raw ~= job.encoded or snapshot.localSnapshot.missing ~= false
        or snapshot.localSnapshot.raw ~= job.encoded or snapshot.localSnapshot.backupRaw ~= nil then return end
    if job.reuse_preview then data = previews[job.expected].data end
    previews[snapshot] = { token = job.token, data = data, raw = job.encoded,
        scopeId = snapshot.scopeId, accountId = snapshot.accountId, path = snapshot.path, loadGeneration = snapshot.loadGeneration }
end

local function validated_preview(token, snapshot)
    local proof = previews[snapshot]
    assert(proof and proof.token == token and snapshot.raw == proof.raw and snapshot.scopeId == proof.scopeId
        and snapshot.accountId == proof.accountId and snapshot.path == proof.path and snapshot.loadGeneration == proof.loadGeneration
        and type(snapshot.localSnapshot) == "table" and snapshot.localSnapshot.missing == false
        and snapshot.localSnapshot.raw == proof.raw and snapshot.localSnapshot.backupRaw == nil,
        "Reload the validated library preview before applying a configuration")
    local uncertain = bridge.uncertainty and bridge.uncertainty(token)
    assert(not uncertain, uncertain)
    return proof
end

-- Select a candidate without I/O. Freshness and the unmodified issued fields
-- are checked when the proof runs and again before each native mutation.
function M.has_verified_preview(token, snapshot)
    local preview = previews[snapshot]
    return preview ~= nil and preview.token == token
end

function M.backend()
    return "lua_cooperative"
end

function M.available(options)
    if options and options.repository and options.repository.fs then return false end
    return type(background.supports) == "function" and background.supports("storage_state") == true
end

local function native_snapshot(token, snapshot)
    assert(type(snapshot) == "table", "Validated library snapshot is unavailable")
    local current = bridge.storage_context(token)
    assert(current.status == "ready", current.error or "Player progress restoration is not ready")
    assert(current.accountId == snapshot.accountId and current.libraryPath == snapshot.path,
        "Storage account changed during reconciliation")
    assert(current.loadGeneration == snapshot.loadGeneration, "Game load changed during reconciliation")
    local value = bridge.read(token, document.MAX_BYTES)
    assert(value.status == "present" or value.status == "missing", value.error or "Game mirror is unavailable")
    assert(value.raw == snapshot.raw and value.scopeId == snapshot.scopeId,
        "Game mirror changed during reconciliation")
    return true
end

function M.verify_snapshot(token, snapshot)
    local ok, reason = pcall(function()
        local held = leases[snapshot]
        assert(held and held.worker:get_progress().state == "local_committed",
            "Acquire a background library proof lease before applying a configuration")
        if held.lock_token then validated_preview(token, snapshot) end
        native_snapshot(token, snapshot)
        local fresh, error = repository.verify_snapshot(snapshot.path, snapshot.localSnapshot,
            { fs = held.fs, lock_token = held.lock_token })
        assert(fresh, error)
    end)
    return ok, ok and nil or tostring(reason)
end

function M.release_snapshot(snapshot)
    local held = leases[snapshot]
    if held then
        leases[snapshot] = nil
        held.worker:cancel("Configuration proof lease released")
    end
    return true
end

function M.begin(config, token, build, change, library_only, expected, options, verify_only, reuse_preview)
    options = options or {}
    local now = options.document and options.document.now or os.time()
    return setmetatable({ state = "running", phase = "state_read", processed = 0,
        config = config, token = token, build = build,
        change = change, library_only = library_only, expected = expected, options = options,
        now = now, verify_only = verify_only == true, reuse_preview = reuse_preview == true, backend = M.backend() }, Job)
end

function Job:get_progress()
    return { state = self.state, phase = self.phase, processed = self.processed,
        total = self.processed + (self.state == "running" and 1 or 0), error = self.error, defer = self.worker_defer == true,
        staged = self.staged == true, localCommitted = self.local_committed == true,
        localUncertain = self.local_uncertain == true,
        mirrorPending = self.mirror_pending == true, missing = self.missing,
        publicationPending = self.publication_pending == true, backend = self.backend,
        revision = self.data and self.data.revision, scopeId = self.scope_id, outcome = self.outcome,
        snapshot = self.state == "done" and self.snapshot or nil,
        matchedPresetId = self.state == "done" and self.matchedPresetId or nil,
        nativeSourceBytes = self.raw and #self.raw or 0, nativeSourceMissing = self.missing,
        nativeSourceMatchesResult = self.native_source_matches,
        nativeSourceLive = self.native_live, nativeSourceDeleted = self.native_deleted }
end
function Job:get_result() return self.state == "done" and self.result or nil end
function Job:yield_execution()
    -- Keep the writer lane and any publication lease. Only its scheduler
    -- permit can be borrowed by the visible Vault page between steps.
    if self.worker then self.worker:yield_execution() end
end
function Job:_snapshot()
    return { raw = self.staged and self.encoded or self.raw, scopeId = self.scope_id,
        accountId = self.account_id, path = self.path, loadGeneration = self.load_generation,
        localSnapshot = self.local_snapshot }
end
function Job:_current()
    return native_snapshot(self.token, { raw = self.raw, scopeId = self.scope_id,
        accountId = self.account_id, path = self.path, loadGeneration = self.load_generation })
end
function Job:_accept(value)
    assert(type(value) == "table", "Invalid worker storage result")
    self.local_committed = self.local_committed or value.localCommitted == true
    if self.data == nil and not self.reuse_preview then
        assert(type(value.data) == "table" and type(value.encoded) == "string"
            and #value.encoded <= document.MAX_BYTES, "Invalid worker storage result")
        self.data, self.encoded = value.data, value.encoded
        self.matchedPresetId = value.matchedPresetId
        self.native_source_matches = value.nativeSourceMatchesResult == true
        self.native_live, self.native_deleted = value.nativeSourceLive, value.nativeSourceDeleted
    else
        -- Preparation already transferred the owned validated document. A
        -- commit acknowledgement carries only its fresh exact local snapshot.
        assert(value.data == nil and value.encoded == nil, "Unexpected repeated worker storage payload")
    end
    self.local_snapshot = value.localSnapshot or value.localUncertain and self.local_snapshot
    assert(type(self.local_snapshot) == "table", "Worker local snapshot is missing")
    if value.localUncertain then self.local_uncertain, self.error = true, value.error or "Local publication could not be verified" end
end
function Job:_finish()
    self.snapshot = self:_snapshot()
    remember_preview(self)
    self.result = self.library_only and self.data.sections.presets.data or self.data
    if self.on_loaded then self.on_loaded(self.snapshot); self.on_loaded = nil end
    self.state, self.phase = "done", "done"
end
function Job:_worker_step(budget)
    self.worker_stepped = true
    local progress = self.worker:step(budget)
    self.worker_defer = progress.defer == true
    self.local_committed = self.local_committed or progress.localCommitted == true
    self.local_uncertain = self.local_uncertain or progress.localUncertain == true
    return progress
end
function Job:_unit(budget)
    if self.phase == "state_read" then
        assert(self.build == self.config.reference_build, "Storage is unavailable on this unverified game build")
        assert(self.expected ~= false, "Reload the configuration library before saving it")
        if self.reuse_preview then
            assert(self.verify_only and not self.change, "A primary proof cannot perform a storage edit")
            local preview = validated_preview(self.token, self.expected)
            self.encoded = preview.raw
            self.preview_copy = background.begin_data_copy(preview.data)
        end
        local current = bridge.storage_context(self.token)
        assert(current.status == "ready", current.error or "Player progress restoration is not ready")
        self.account_id, self.path, self.load_generation = current.accountId, current.libraryPath, current.loadGeneration
        local value = bridge.read(self.token, document.MAX_BYTES)
        assert(value.status == "present" or value.status == "missing", value.error or "Game mirror could not be read")
        self.raw, self.scope_id, self.missing = value.raw, value.scopeId, value.status == "missing"
        if type(bridge.new_id) == "function" then
            local id, failure = bridge.new_id(self.token)
            self.temp_token = assert(id, failure or "Native publication UUID is unavailable")
        end
        if self.expected then
            assert(self.expected.raw == self.raw and self.expected.scopeId == self.scope_id
                and self.expected.accountId == self.account_id and self.expected.path == self.path
                and self.expected.loadGeneration == self.load_generation,
                "Library preview changed; refresh before continuing")
        end
        local payload = { path = self.path, accountId = self.account_id, nativeRaw = self.raw,
            change = self.change, now = self.now,
            tempToken = self.temp_token,
            compactResults = true,
            reusePreview = self.reuse_preview,
            expectedLocalSnapshot = self.reuse_preview and { missing = false, raw = self.encoded }
                or self.expected and self.expected.localSnapshot,
            operation = self.verify_only and "verify" or nil }
        local kind = self.verify_only and "storage_verify" or "storage_state"
        local worker, reason = background.begin_task(kind, payload, { path = self.path, generation = 0,
            fs = self.options.repository and self.options.repository.fs,
            allow_test_paths = self.options.repository and self.options.repository.fs ~= nil })
        assert(worker, reason or "Native storage worker is unavailable")
        self.worker, self.phase = worker, "worker_prepare"
    elseif self.phase == "worker_prepare" then
        local progress = self:_worker_step(budget)
        if progress.state == "running" then return end
        assert(progress.state == "prepared", progress.error or "Storage preparation failed")
        self:_accept(self.worker:get_result())
        self.preview_copy = self.preview_copy or background.begin_data_copy(self.data)
        self.phase = "preview_copy"
    elseif self.phase == "preview_copy" then
        -- The UI may modify a loaded document. Keep an independent validated
        -- copy at issuance; each fast proof returns its own public copy.
        local copied = plain_work.run(function()
            local ok, data = coroutine.resume(self.preview_copy)
            assert(ok, data)
            return data and { state = "done", data = data } or { state = "running" }
        end, budget, { group = "schema_primitives" })
        if not copied or copied.state ~= "done" then return end
        if self.reuse_preview then self.data = copied.data else private_documents[self] = copied.data end
        self.preview_copy = nil
        self.phase = "worker_permit"
    elseif self.phase == "worker_permit" then
        self:_current()
        local progress = self.worker:authorize_publish()
        assert(not progress or progress.state ~= "error", progress and progress.error)
        self.phase = "worker_publish"
    elseif self.phase == "worker_publish" then
        local progress = self:_worker_step(budget)
        if progress.state == "running" or progress.state == "prepared" then return end
        assert(progress.state == "local_committed", progress.error or "Local publication failed")
        local result = self.worker:get_result()
        self:_accept(result)
        self.publication_pending = false
        if self.verify_only then
            self.snapshot = self:_snapshot()
            if self.reuse_preview then
                assert(not self.local_uncertain and not self.local_committed and not self.error
                    and type(result.lockToken) == "string" and result.lockToken ~= "" and self.local_snapshot.raw == self.encoded,
                    "Could not verify the unchanged primary under its writer lock")
                validated_preview(self.token, self.expected)
                self:_current()
            end
            self.snapshot.workerLease = tostring(self.worker) .. ":" .. tostring(self.worker.handle)
            leases[self.snapshot] = { worker = self.worker, fs = self.options.repository and self.options.repository.fs,
                lock_token = result.lockToken }
            remember_preview(self)
            self.result, self.state, self.phase = self.data, "done", "done"
        else self.phase = "state_stage" end
    elseif self.phase == "state_stage" then
        self.mirror_pending = true
        if self.local_uncertain then self.outcome = "local_uncertain"
        else
            local ok, value = pcall(function()
                self:_current()
                local fresh, error = repository.verify_snapshot(self.path, self.local_snapshot, self.options.repository)
                assert(fresh, error)
                local uncertain = bridge.uncertainty and bridge.uncertainty(self.token)
                if uncertain then return { status = "uncertain", error = uncertain } end
                if self.raw == self.encoded then return { status = "staged" } end
                return bridge.write(self.token, self.raw, self.encoded, self.scope_id)
            end)
            self.outcome, self.staged = ok and value.status or "pending", ok and value.status == "staged"
            self.mirror_pending = not self.staged
            self.error = not self.staged and (ok and value.error or tostring(value)) or nil
        end
        self.phase = "worker_acknowledge"
    elseif self.phase == "worker_acknowledge" then
        -- The worker acknowledgement reports mirror admission, not the richer
        -- coordinator outcome. Keep local uncertainty/conflict visible here
        -- while releasing the parked writer as mirror-pending.
        local acknowledgement = self.staged and "staged" or self.outcome == "uncertain" and "uncertain" or "pending"
        local progress = self.worker:finish_stage(acknowledgement)
        assert(not progress or progress.state ~= "error", progress and progress.error)
        self.phase = "worker_release"
    elseif self.phase == "worker_release" then
        local progress = self:_worker_step(budget)
        if progress.state == "running" or progress.state == "local_committed" then return end
        assert(progress.state == "done", progress.error or "Storage lease release failed")
        local value = self.worker:get_result()
        if value and value.error then
            self.error = value.error
            self.mirror_pending = not self.staged
        end
        self:_finish()
    end
end
function Job:step_one(budget)
    if self.state ~= "running" then return self:get_progress() end
    self.worker_stepped, self.worker_defer = false, false
    local ok, err = pcall(self._unit, self, budget)
    self.processed = self.processed + 1
    if budget and not self.worker_stepped then budget:consume(1) end
    if not ok then
        self.preview_copy, private_documents[self] = nil, nil
        if self.worker then self.worker:cancel(tostring(err)) end
        self.mirror_pending = self.local_committed and not self.staged
        if self.local_committed then self.outcome = self.local_uncertain and "local_uncertain" or "pending" end
        self.state, self.phase, self.error, self.result = "error", "error", tostring(err), nil
    end
    return self:get_progress()
end
function Job:step(budget)
    if self.state ~= "running" or budget and budget:expired() then return self:get_progress() end
    -- A pending worker is polled once, never until a shared callback budget is
    -- exhausted. Native capture/admission/staging also remain bounded units.
    return self:step_one(budget)
end
function Job:cancel(reason)
    if self.state ~= "running" then return self:get_progress() end
    self.preview_copy, private_documents[self] = nil, nil
    if self.worker then
        self.publication_pending = not self.reuse_preview and self.worker.handle ~= nil
        self.worker:cancel(reason or "Storage request cancelled")
    end
    self.mirror_pending = (self.local_committed or self.publication_pending) and not self.staged
    self.outcome = self.publication_pending and "cancelled_pending" or "cancelled"
    self.state, self.phase, self.error, self.result = "cancelled", "cancelled", reason, nil
    return self:get_progress()
end
return M
