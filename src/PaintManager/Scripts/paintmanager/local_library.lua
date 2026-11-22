-- Account-local JSON publication before native mirror staging. Windows CRT
-- rename refuses an existing destination; the fixed lock is never replaced.
-- Filesystem primitives are indivisible. Parsing, encoding and reads yield.
local processing = require("paintmanager.processing")
local document = require("paintmanager.state_document")
local json = require("paintmanager.json")
local plain_work = require("paintmanager.plain_work")
local M, Job = {}, {}; Job.__index = Job
local default_fs = require("paintmanager.filesystem")
local sequence = 0

local function path_check(path)
    assert(type(path) == "string" and #path > 0 and not path:find("[%z\r\n]"), "Invalid local library path")
end
function M.account_path(save_root, account_id)
    path_check(save_root)
    assert(type(account_id) == "string" and account_id:match("^765%d+$")
        and #account_id == 17, "Invalid native account identity")
    assert(not save_root:find("..", 1, true), "Invalid native save root")
    return save_root:gsub("[/\\]+$", "") .. "/" .. account_id .. "/PaintManager.library.json"
end
local function begin(path, mode, options)
    path_check(path); options = options or {}
    return setmetatable({ path = path, mode = mode, options = options, fs = options.fs or default_fs,
        state = "running", phase = "open", processed = 0, completed = 0, bytes = 0,
        pieces = {}, cleanup_errors = json.array() }, Job)
end
function M.begin_load(path, options) return begin(path, "load", options) end
-- A raw read proves byte freshness only. Callers must validate unknown bytes
-- or compare them exactly with a document that already passed validation.
function M.begin_read(path, options) return begin(path, "read", options) end
local function read_exact_snapshot(fs, path)
    local file, err, code = fs.open(path, "rb")
    if not file then assert(code == 2, "Cannot inspect local library snapshot: " .. tostring(err)); return nil end
    local ok, value, read_err = pcall(function() return file:read(document.MAX_BYTES + 1) end)
    local closed, close_err = file:close()
    assert(ok and closed and read_err == nil, "Cannot read local library snapshot: " .. tostring(ok and (read_err or close_err) or value))
    value = value or ""
    assert(type(value) == "string" and #value <= document.MAX_BYTES, "Local library snapshot exceeds the native byte limit")
    return value
end
function M.verify_snapshot(path, snapshot, options)
    local ok, err = pcall(function()
        path_check(path); assert(type(snapshot) == "table" and type(snapshot.missing) == "boolean", "Invalid local snapshot")
        local fs = options and options.fs or default_fs
        if options and options.lock_token then
            assert(read_exact_snapshot(fs, path .. ".lock") == options.lock_token, "Local library lock ownership changed")
        end
        local raw = read_exact_snapshot(fs, path)
        assert((raw == nil) == snapshot.missing and raw == snapshot.raw, "Local library snapshot changed")
        if snapshot.missing then
            assert(read_exact_snapshot(fs, path .. ".backup") == snapshot.backupRaw, "Local backup snapshot changed")
        end
    end)
    return ok, ok and nil or tostring(err)
end
local function begin_writer(path, expected_snapshot, options)
    local job = begin(path, "save", options)
    assert(type(expected_snapshot) == "table" and type(expected_snapshot.missing) == "boolean",
        "A validated local source snapshot is required")
    assert(expected_snapshot.missing or type(expected_snapshot.raw) == "string", "Invalid local source snapshot")
    assert(expected_snapshot.raw == nil or type(expected_snapshot.raw) == "string", "Invalid local source bytes")
    assert(expected_snapshot.backupRaw == nil or type(expected_snapshot.backupRaw) == "string", "Invalid local backup bytes")
    sequence = sequence + 1
    -- Lua seeds its PRNG at startup; time, runtime table identity and a process
    -- sequence also prevent ordinary candidate collisions. The fixed lock is
    -- acquired only by the Windows no-replace rename, never by an existence test.
    local token = options and options.temp_token or tostring(os.time()) .. "-" .. (tostring(job):match("0x(%x+)") or "lua")
        .. "-" .. tostring(sequence) .. "-" .. tostring(math.random(0, 2147483647))
    assert(type(token) == "string" and #token > 0 and #token <= 256 and token:match("^[%w%-]+$"), "Invalid private publication token")
    job.pending, job.lock_candidate, job.lock_path = path .. ".pending-" .. token,
        path .. ".lock-pending-" .. token, path .. ".lock"
    job.lock_token = "PaintManager local library lock " .. token .. "\n"
    job.expected = { missing = expected_snapshot.missing, raw = expected_snapshot.raw, backupRaw = expected_snapshot.backupRaw }
    return job
end
function M.begin_save(path, data, expected_snapshot, options)
    local job = begin_writer(path, expected_snapshot, options)
    job.child, job.phase = document.begin_validate(data), "encode"
    return job
end
-- Internal handoff from the storage coordinators: both the document and its
-- encoded bytes have already passed the schema/codec pipeline, and the source
-- snapshot was decoded or matched exactly with those validated bytes. Fresh
-- source checks still compare raw bytes while holding the writer lease.
function M.begin_validated_save(path, data, expected_snapshot, encoded, options)
    assert(type(data) == "table" and type(encoded) == "string" and #encoded <= document.MAX_BYTES,
        "Validated publication requires owned document bytes")
    local job = begin_writer(path, expected_snapshot, options)
    job.validated_new, job.encoded = data, encoded
    job.expected_validated, job.phase = true, "lock_prepare"
    return job
end
-- The coordinator has already validated these exact primary bytes. A proof
-- lease can only check them under the lock and later release that lock.
function M.begin_primary_lease(path, snapshot, options)
    assert(type(snapshot) == "table" and snapshot.missing == false and snapshot.backupRaw == nil
        and type(snapshot.raw) == "string" and #snapshot.raw > 0 and #snapshot.raw <= document.MAX_BYTES,
        "A verified primary snapshot is required for a proof lease")
    local job = begin_writer(path, snapshot, options)
    job.proof_only, job.encoded, job.phase = true, snapshot.raw, "lock_prepare"
    return job
end
function Job:_child(budget)
    local pure = self.phase == "encode" or self.phase == "decode"
    local progress
    if pure then progress = plain_work.run(function() return self.child:step_one() end, budget, { group = "schema_primitives" })
    else progress = self.child:step_one(budget) end
    if not progress then return false end
    assert(progress.state == "running" or progress.state == "done", progress.error or "Local library child stopped")
    return progress.state == "done"
end
function Job:_close()
    local file = self.file; self.file = nil
    if file then local ok, err = file:close(); assert(ok, "Cannot close local library file: " .. tostring(err)) end
end
function Job:_exists(path)
    local file, err, code = self.fs.open(path, "rb")
    if not file then assert(code == 2, "Cannot inspect local library path: " .. tostring(err)); return false end
    local ok, close_err = file:close(); assert(ok, "Cannot close local library path: " .. tostring(close_err))
    return true
end
function Job:_current()
    -- Once the native worker has reserved publication, complete recovery and
    -- readback against the frozen account path even if its Lua request dies.
    if self.commit_reserved then return end
    if self.options.is_current then
        local ok, err = self.options.is_current()
        assert(ok == true, err or "Local library account or native context changed")
    end
end
function Job:_reserve_commit()
    if self.commit_reserved or not self.options.reserve_commit then return end
    self:_current()
    local ok, err = self.options.reserve_commit()
    assert(ok == true, err or "Local publication was cancelled before commit reservation")
    self.commit_reserved = true
end
function Job:_load(budget)
    if self.phase == "open" then
        local file, err, code = self.fs.open(self.path, "rb")
        if not file then
            assert(code == 2, "Cannot open local library: " .. tostring(err))
            self.primary_missing = true
            if self.options.allow_backup ~= false then
                file, err, code = self.fs.open(self.path .. ".backup", "rb")
                if not file then assert(code == 2, "Cannot open local library backup: " .. tostring(err))
                else self.recovered_backup = true end
            end
            if not file then
                self.snapshot, self.missing, self.state, self.phase = { missing = true }, true, "done", "done"
                return
            end
        end
        self.file, self.phase = file, "read"
    elseif self.phase == "read" then
        local chunk, err = self.file:read(processing.get("library_read_bytes", 8192))
        if chunk then
            assert(type(chunk) == "string" and #chunk > 0 and #chunk <= processing.get("library_read_bytes", 8192), "Invalid local library read")
            self.bytes = self.bytes + #chunk
            assert(self.bytes <= document.MAX_BYTES, "Local library exceeds the native byte limit")
            self.pieces[#self.pieces + 1] = chunk
        else assert(err == nil, "Cannot read local library: " .. tostring(err)); self.phase = "close" end
    elseif self.phase == "close" then self:_close(); self.phase = "decode_start"
    elseif self.phase == "decode_start" then
        self.raw, self.pieces = table.concat(self.pieces), nil
        if self.mode == "read" then
            self.snapshot = self.primary_missing and { missing = true, backupRaw = self.raw }
                or { missing = false, raw = self.raw }
            self.result, self.missing, self.state, self.phase = self.raw, self.primary_missing == true, "done", "done"
        else self.child, self.phase = document.begin_decode(self.raw), "decode" end
    elseif self.phase == "decode" and self:_child(budget) then
        self.encoded = self.child:get_encoded()
        self.result, self.child = self.child:get_result(), nil
        self.snapshot = self.primary_missing and { missing = true, backupRaw = self.raw }
            or { missing = false, raw = self.raw }
        self.missing, self.state, self.phase = self.primary_missing == true, "done", "done"
    end
end
local function same_snapshot(left, right)
    return left.missing == right.missing and left.raw == right.raw and left.backupRaw == right.backupRaw
end
function Job:_read_lock()
    local file, err = self.fs.open(self.lock_path, "rb")
    assert(file, "Cannot inspect local library lock: " .. tostring(err))
    local value, read_err = file:read(#self.lock_token + 1)
    local ok, close_err = file:close()
    assert(ok and not read_err, "Cannot read local library lock: " .. tostring(read_err or close_err))
    return value
end
function Job:_rename(source, destination)
    local ok, err = self.fs.rename(source, destination)
    assert(ok, "Cannot publish local library: " .. tostring(err))
end
function Job:_start_check(next_phase)
    self.child = (self.expected_validated and M.begin_read or M.begin_load)(self.path, { fs = self.fs })
    self.after_check, self.phase = next_phase, "check_source"
end
function Job:_save(budget)
    if self.phase == "encode" then
        if self:_child(budget) then
            self.validated_new, self.encoded = self.child:get_result(), self.child:get_encoded()
            assert(type(self.encoded) == "string" and #self.encoded <= document.MAX_BYTES, "Invalid encoded local library")
            assert(not self.options.encoded or self.options.encoded == self.encoded, "Preflighted local payload changed")
            self.child, self.phase = nil, "lock_prepare"
        end
    elseif self.phase == "lock_prepare" then
        self:_current()
        assert(not self:_exists(self.lock_candidate) and not self:_exists(self.pending), "Local library temporary path collision")
        local file, err = self.fs.open(self.lock_candidate, "wb")
        assert(file, "Cannot create local library lock candidate: " .. tostring(err))
        self.file, self.lock_candidate_created, self.phase = file, true, "lock_write"
    elseif self.phase == "lock_write" then
        local ok, err = self.file:write(self.lock_token); assert(ok, "Cannot write local library lock: " .. tostring(err))
        self.phase = "lock_flush"
    elseif self.phase == "lock_flush" then
        local ok, err = self.file:flush(); assert(ok, "Cannot flush local library lock: " .. tostring(err)); self.phase = "lock_close"
    elseif self.phase == "lock_close" then self:_close(); self.phase = "lock_acquire"
    elseif self.phase == "lock_acquire" then
        -- The supported runtime is Windows. os.rename must retain its native
        -- no-replace semantics; injected filesystems must provide that contract.
        local ok, err = self.fs.rename(self.lock_candidate, self.lock_path)
        assert(ok, "Local library is locked by another writer; a leftover crash lock requires explicit recovery: " .. tostring(err))
        self.lock_candidate_created, self.lock_owned = false, true
        assert(self:_read_lock() == self.lock_token, "Local library lock ownership changed")
        if self.proof_only then self.phase = "lease_verify"
        else self:_start_check(self.options.lease_only and "lease_verify" or "pending_open") end
    elseif self.phase == "check_source" then
        if self:_child(budget) then
            assert(same_snapshot(self.child.snapshot, self.expected), "Local library changed while preparing publication")
            self.expected_validated = true
            self.child, self.phase = nil, self.after_check
        end
    elseif self.phase == "lease_verify" then
        self:_current(); assert(self:_read_lock() == self.lock_token, "Local library lock ownership changed")
        assert(not self.expected.missing and self.expected.raw == self.encoded,
            "An unchanged local lease requires the exact validated primary bytes")
        local current, reason = M.verify_snapshot(self.path, self.expected, { fs = self.fs })
        assert(current, reason)
        self:_reserve_commit()
        self.result, self.snapshot = self.validated_new, self.expected
        self.state, self.phase = "awaiting_mirror", "awaiting_mirror"
    elseif self.phase == "pending_open" then
        self:_current()
        local file, err = self.fs.open(self.pending, "wb"); assert(file, "Cannot create pending local library: " .. tostring(err))
        self.file, self.pending_created, self.offset, self.phase = file, true, 1, "pending_write"
    elseif self.phase == "pending_write" then
        if self.offset > #self.encoded then self.phase = "pending_flush"; return end
        local chunk = self.encoded:sub(self.offset, self.offset + processing.get("library_write_bytes", 8192) - 1)
        local ok, err = self.file:write(chunk); assert(ok, "Cannot write pending local library: " .. tostring(err))
        self.offset = self.offset + #chunk
    elseif self.phase == "pending_flush" then
        local ok, err = self.file:flush(); assert(ok, "Cannot flush pending local library: " .. tostring(err)); self.phase = "pending_close"
    elseif self.phase == "pending_close" then
        self:_close(); self.child, self.phase = M.begin_read(self.pending, { fs = self.fs, allow_backup = false }), "pending_verify"
    elseif self.phase == "pending_verify" then
        if self:_child(budget) then
            assert(self.child.raw == self.encoded and not self.child.missing, "Pending local library readback mismatch")
            self.result, self.child = self.validated_new, nil
            self:_start_check("backup_inspect")
        end
    elseif self.phase == "backup_inspect" then
        self:_current(); assert(self:_read_lock() == self.lock_token, "Local library lock ownership changed")
        if not self.expected.missing and self:_exists(self.path .. ".backup") then
            self.child, self.phase = M.begin_load(self.path .. ".backup", { fs = self.fs, allow_backup = false }), "backup_validate"
        else self.phase = self.expected.missing and "publish" or "backup_move" end
    elseif self.phase == "backup_validate" then
        if self:_child(budget) then self.child, self.phase = nil, "backup_remove" end
    elseif self.phase == "backup_remove" then
        self:_reserve_commit()
        local ok, err = self.fs.remove(self.path .. ".backup"); assert(ok, "Cannot rotate validated local backup: " .. tostring(err))
        self.phase = "backup_move"
    elseif self.phase == "backup_move" then
        self:_current(); assert(self:_read_lock() == self.lock_token, "Local library lock ownership changed")
        local current, err = M.verify_snapshot(self.path, self.expected, { fs = self.fs })
        assert(current, err)
        self:_reserve_commit()
        -- Compare and rename in one unit; the cooperative lock also excludes
        -- other supported writers during preparation and publication.
        self:_rename(self.path, self.path .. ".backup"); self.old_moved = true
        self.child, self.phase = M.begin_read(self.path .. ".backup", { fs = self.fs, allow_backup = false }), "backup_verify"
    elseif self.phase == "backup_verify" then
        if self:_child(budget) then
            assert(self.child.raw == self.expected.raw, "Local library changed before backup publication")
            self.child, self.phase = nil, "publish"
        end
    elseif self.phase == "publish" then
        self:_current(); assert(self:_read_lock() == self.lock_token, "Local library lock ownership changed")
        if not self.old_moved then
            local current, err = M.verify_snapshot(self.path, self.expected, { fs = self.fs })
            assert(current, err)
        end
        assert(not self:_exists(self.path), "Local library appeared before publication")
        self:_reserve_commit()
        self:_rename(self.pending, self.path)
        self.pending_created, self.local_committed, self.old_moved = false, true, false
        self.snapshot = { missing = false, raw = self.encoded }
        self.child, self.phase = M.begin_read(self.path, { fs = self.fs, allow_backup = false }), "published_verify"
    elseif self.phase == "published_verify" then
        if self:_child(budget) then
            assert(self.child.raw == self.encoded, "Published local library readback mismatch")
            self.result, self.child = self.validated_new, nil
            if self.options.hold_for_mirror then
                self.state, self.phase = "awaiting_mirror", "awaiting_mirror"
            else self.phase = "unlock" end
        end
    elseif self.phase == "unlock" then
        assert(self:_read_lock() == self.lock_token, "Local library lock ownership changed after publication")
        local ok, err = self.fs.remove(self.lock_path); assert(ok, "Cannot release local library lock: " .. tostring(err))
        self.lock_owned, self.state, self.phase = false, "done", "done"
    end
end
-- Called after the game-thread mirror decision, or by cooperative retirement
-- when no coordinator remains. This only releases the local writer lease.
function Job:finish_stage(outcome)
    assert(self.state == "awaiting_mirror", "Local publication is not awaiting mirror acknowledgement")
    self.mirror_outcome, self.state, self.phase = outcome or "pending", "running", "unlock"
    return self:step_one()
end
function Job:_cleanup()
    local function attempt(fn)
        local ok, err = pcall(fn); if not ok then self.cleanup_errors[#self.cleanup_errors + 1] = tostring(err) end
    end
    if self.file then attempt(function() self:_close() end) end
    if self.child then attempt(function() self.child:cancel("Local library work stopped") end); self.child = nil end
    if self.old_moved and not self.local_committed then
        attempt(function()
            assert(not self:_exists(self.path), "Backup retained because local primary is present")
            self:_rename(self.path .. ".backup", self.path); self.old_moved = false
        end)
    end
    for _, field in ipairs({ "pending_created", "lock_candidate_created" }) do
        if self[field] then
            local path = field == "pending_created" and self.pending or self.lock_candidate
            attempt(function() local ok, err = self.fs.remove(path); assert(ok, tostring(err)); self[field] = false end)
        end
    end
    if self.lock_owned then
        attempt(function()
            assert(self:_read_lock() == self.lock_token, "Foreign local library lock retained")
            local ok, err = self.fs.remove(self.lock_path); assert(ok, tostring(err)); self.lock_owned = false
        end)
    end
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, error = self.error, processed = self.processed,
        completed = self.completed, missing = self.missing, recoveredBackup = self.recovered_backup,
        localCommitted = self.local_committed == true,
        localUncertain = self.local_committed == true and self.state ~= "done" and self.state ~= "awaiting_mirror",
        commitReserved = self.commit_reserved == true, awaitingMirror = self.state == "awaiting_mirror",
        mirrorOutcome = self.mirror_outcome,
        snapshot = self.snapshot, cleanupErrors = self.cleanup_errors, bytesRead = self.bytes }
end
function Job:get_result() return self.state == "done" and self.result or nil end
function Job:get_encoded() return self.state == "done" and self.encoded or nil end
function Job:step_one(budget)
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(function() if self.mode == "load" or self.mode == "read" then self:_load(budget) else self:_save(budget) end end)
    self.processed, self.completed = self.processed + 1, self.completed + 1
    if not ok then
        self.state, self.phase, self.error = "error", "error", tostring(err)
        self:_cleanup(); self.result = nil
    end
    return self:get_progress()
end
function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do self:step_one(budget); budget:consume(1) end
    return self:get_progress()
end
function Job:cancel(reason)
    if self.state == "awaiting_mirror" then return self:finish_stage("pending") end
    if self.state ~= "running" then return self:get_progress() end
    if self.commit_reserved then
        self.cancel_requested = reason or "Local publication request cancelled after reservation"
        return self:get_progress()
    end
    self.state, self.phase, self.error = "cancelled", "cancelled", reason
    self:_cleanup(); self.result = nil; return self:get_progress()
end
return M
