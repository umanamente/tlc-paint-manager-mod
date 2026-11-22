-- Cooperative owned-data jobs. No companion, wire codec or second Lua VM.
local processing = require("paintmanager.processing")
local json = require("paintmanager.json")
local filesystem = require("paintmanager.filesystem")
local plain_work = require("paintmanager.plain_work")
local M, Job = {}, {}; Job.__index = Job
local live, lanes, retiring = {}, {}, {}
local generation, sequence, observer, stopping = 0, 0, nil, false
local ANALYSIS = { analysis_collection = true, analysis_view = true, analysis_preset_plan = true }
local STORAGE = { storage_state = true, storage_verify = true }
local BOX = { container_cache_read = true, container_cache_write = true }
local MAX_NODES, MAX_BYTES = 262144, 8 * 1024 * 1024
local PURE = { native_decode_start = true, native_decode = true, local_document = true,
    state_merge = true, import_merge = true, state_change = true, library_validate = true,
    library_update = true, library_updated = true, state_update = true, legacy_migrate = true,
    state_encode = true, read_decode = true, read_validate = true, write_validate = true,
    write_validate_start = true, write_encode = true }
local function terminal(state) return state == "done" or state == "error" or state == "cancelled" end
local function release(job)
    if job.path ~= "" and lanes[job.path] == job then lanes[job.path] = nil end
    live[job], job.reserved, job.handle = nil, nil, nil
end
local function active_count()
    local count = 0
    for job in pairs(live) do if job.reserved then count = count + 1 end end
    return count
end
local function scoped(job, fn)
    return processing.with_snapshot(job.policy, fn)
end
-- Copy one value/table boundary per primitive, preserving JSON null/arrays.
function M.begin_data_copy(source)
    return coroutine.create(function()
        local nodes, bytes, active = 0, 0, {}
        local function copy(item, depth)
            nodes = nodes + 1
            assert(nodes <= MAX_NODES and depth <= 128, "Lua task structural limit exceeded")
            local kind = type(item)
            if kind == "string" then bytes = bytes + #item; assert(bytes <= MAX_BYTES, "Lua task byte limit exceeded") end
            if item == json.null then coroutine.yield(); return json.null end
            if kind ~= "table" then
                assert(kind == "nil" or kind == "boolean" or kind == "string"
                    or kind == "number" and item == item and math.abs(item) < math.huge,
                    "Lua task accepts primitive data only")
                coroutine.yield(); return item
            end
            assert(getmetatable(item) == nil and not active[item], "Lua task contains a metatable or cycle")
            active[item] = true
            local result = json.is_array(item) and json.array() or {}
            for key, value in pairs(item) do
                assert(type(key) == "string" or type(key) == "number" and key % 1 == 0,
                    "Lua task key must be text or an integer")
                if type(key) == "string" then bytes = bytes + #key; assert(bytes <= MAX_BYTES, "Lua task byte limit exceeded") end
                result[key] = copy(value, depth + 1)
            end
            active[item] = nil; coroutine.yield(); return result
        end
        return copy(source, 0)
    end)
end
function M.describe()
    return { backend = "lua_cooperative", workerEnabled = false, idleWorkerMayExist = false,
        capabilities = { maxJobs = processing.get("job_admission_target", 4), maxNodes = MAX_NODES, maxInputBytes = MAX_BYTES },
        filesystem = filesystem.describe(), semantics = "Bounded Lua jobs on the existing game-thread scheduler" }
end
-- Between bounded steps a parked cursor can give the visible page its
-- execution permit. File leases stay with the cursor until it settles.
function Job:yield_execution()
    if self.reserved and not self.retiring then self.reserved, self.yielded = nil, true end
end
function M.yield_paint_slots()
    for job in pairs(live) do
        if job.kind == "analysis_collection" or job.kind == "analysis_view" or BOX[job.kind] then
            job:yield_execution()
        end
    end
end

function M.configure() return true end
function M.is_busy() return next(live) ~= nil or #retiring > 0 end
function M.monotonic_ms()
    local ok, value, reason = pcall(function() return require("paintmanager.clock").now_ms() end)
    if ok then return value, reason end
    return nil, tostring(value)
end
function M.set_observer(callback)
    assert(callback == nil or type(callback) == "function", "Invalid task observer"); observer = callback
end
function M.supports(kind) return ANALYSIS[kind] == true or STORAGE[kind] == true or BOX[kind] == true end
function M.begin_task(kind, payload, options)
    if stopping then return nil, "Lua executor is unloading" end
    if not M.supports(kind) then return nil, "Unsupported Lua task: " .. tostring(kind) end
    options = options or {}; sequence = sequence + 1
    local job = setmetatable({ kind = kind, state = "running", phase = "admit", completed = 0,
        policy = processing.snapshot(), generation = generation, request_generation = options.generation,
        path = options.path or ((STORAGE[kind] or BOX[kind]) and type(payload) == "table" and rawget(payload, "path")) or "",
        retain = options.retain_on_generation_change == true,
        options = options, source = payload, backend = "lua_cooperative", handle = sequence,
        cleanup_errors = json.array() }, Job)
    live[job] = true; return job
end
function M.begin_report(path, report, options)
    local copy = {}; for key, value in pairs(options or {}) do copy[key] = value end
    copy.background = false
    return require("paintmanager.report_writer").begin(path, report, copy)
end
function Job:_current() return self.generation == generation and not self.cancel_requested end
function Job:_retire()
    if not self.retiring then self.retiring = true; retiring[#retiring + 1] = self end
end
function Job:_adopt()
    local p = self.driver:get_progress()
    self.state, self.phase = p.state, p.phase
    self.local_committed = self.local_committed or p.localCommitted == true
    self.local_uncertain = self.local_uncertain or p.localUncertain == true
    self.error = p.error or self.error; self.cleanup_errors = p.cleanupErrors or self.cleanup_errors
    if self.state == "done" or self.state == "prepared" or self.state == "local_committed" then self.result = self.driver:get_result() end
    if terminal(self.state) then release(self) end
end
function Job:_construct()
    if STORAGE[self.kind] or BOX[self.kind] then
        assert(type(self.path) == "string" and self.path ~= "" and self.payload.path == self.path,
            "Lua file task path differs from its admitted writer lane")
    end
    local control = { fs = self.options.fs or filesystem, allow_test_paths = self.options.allow_test_paths == true,
        temp_token = self.options.temp_token,
        is_current = function() return self:_current(), "Lua task generation was invalidated" end,
        reserve_commit = function()
            if not self:_current() then return false, "Lua publication was revoked" end
            self.commit_reserved = true; return true
        end }
    if ANALYSIS[self.kind] then self.driver = require("paintmanager.worker_analysis").begin(self.kind, self.payload)
    elseif STORAGE[self.kind] then
        if self.kind == "storage_verify" then self.payload.operation = "verify" end
        self.driver = require("paintmanager.worker_storage").begin(self.payload, control)
    else self.driver = require("paintmanager.worker_container_cache").begin(self.kind, self.payload, control) end
    self.payload = nil
    if observer then pcall(observer, { kind = self.kind, backend = self.backend }) end
    self:_adopt()
end
function Job:_advance()
    if not self.commit_reserved then assert(self:_current(), "Lua task generation was invalidated") end
    if self.yielded then
        if active_count() >= (self.policy.job_admission_target or 4) then self.defer = true; return end
        self.reserved, self.yielded = true, nil
    end
    if self.phase == "admit" then
        if active_count() >= (self.policy.job_admission_target or 4)
            or self.path ~= "" and lanes[self.path] and lanes[self.path] ~= self then self.defer = true; return end
        self.reserved = true; if self.path ~= "" then lanes[self.path] = self end
        self.copier, self.source = M.begin_data_copy(self.source), nil; self.phase = "snapshot"
    elseif self.phase == "snapshot" then
        local ok, value = coroutine.resume(self.copier); assert(ok, value)
        if coroutine.status(self.copier) == "dead" then self.payload, self.copier = value, nil; self:_construct() end
    elseif self.command == "publish" then self.command = nil; self.driver:authorize_publish(true); self:_adopt()
    elseif self.command == "finish" then self.command = nil; self.driver:finish_stage(self.stage_outcome); self:_adopt()
    else self.driver:step_one(); self:_adopt() end
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, completed = self.completed, processed = self.completed,
        backend = self.backend, error = self.error, defer = self.defer == true,
        localCommitted = self.local_committed == true, localUncertain = self.local_uncertain == true,
        cleanupErrors = self.cleanup_errors, data = self.result, kind = self.kind, requestGeneration = self.request_generation,
        semantics = "Finite Lua stages; filesystem/native primitives are indivisible" }
end
function Job:get_result() return self.result end
function Job:_step_primitive()
    if self.state ~= "running" then return self:get_progress() end
    self.defer = false
    local ok, err = pcall(function() scoped(self, function() self:_advance() end) end)
    self.completed = self.completed + 1
    if not ok then
        self.error = tostring(err)
        if self.driver then self.driver:cancel(self.error); self:_adopt() end
        if self.commit_reserved and self.driver and not terminal(self.state) then self:_retire()
        else self.state, self.phase = "error", "error"; release(self) end
        self.source, self.copier, self.payload = nil, nil, nil
    end
    return self:get_progress()
end
function Job:step_one(budget)
    if self.state ~= "running" or budget and budget:expired() then return self:get_progress() end
    if budget and (self.phase == "snapshot" or ANALYSIS[self.kind] or PURE[self.phase]) then
        local phase = self.phase
        plain_work.run(function()
            local p = self:_step_primitive()
            if p.state == "running" and (p.defer or self.phase ~= phase) then return { state = "phase_boundary" } end
            return p
        end, budget, { max_primitives = self.policy.job_steps_per_unit or 64 })
    else self:_step_primitive() end
    return self:get_progress()
end
function Job:step(budget)
    if self.state ~= "running" or budget and budget:expired() then return self:get_progress() end
    local p = self:step_one(budget)
    if budget then budget:consume(1) end
    return p
end
function Job:authorize_publish()
    assert(self.state == "prepared" and self:_current(), "Lua storage task is not currently prepared")
    self.command, self.state, self.phase = "publish", "running", "authorize_publish"; return self:get_progress()
end
function Job:finish_stage(outcome)
    assert(self.state == "local_committed", "Lua storage task has no held publication")
    self.stage_outcome, self.command = outcome or "pending", "finish"
    self.state, self.phase = "running", "finish_stage"; return self:get_progress()
end
function Job:cancel(reason, defer_cleanup)
    if terminal(self.state) then return self:get_progress() end
    self.cancel_requested, self.error = true, reason or "Lua task cancelled"
    self.source, self.copier, self.payload = nil, nil, nil
    if self.driver then
        if defer_cleanup then
            self.retire_cancel_pending = true; self:_retire(); return self:get_progress()
        end
        scoped(self, function() self.driver:cancel(self.error) end); self:_adopt()
        if not terminal(self.state) then self:_retire(); return self:get_progress() end
    end
    self.state, self.phase = "cancelled", "cancelled"; release(self); return self:get_progress()
end
function M.invalidate_scope()
    generation = generation + 1
    local stale = {}; for job in pairs(live) do if not job.retain then stale[#stale + 1] = job end end
    -- Lifecycle hooks revoke permission immediately. File cleanup is deferred
    -- to the scheduler's retirement lane rather than performed in the hook.
    for _, job in ipairs(stale) do job:cancel("Lua task scope invalidated", true) end
    return true
end
function M.pump(budget)
    if budget and budget:expired() then return end
    local job = table.remove(retiring, 1); if not job then return end
    job.retiring = nil
    if job.driver and not terminal(job.state) then
        scoped(job, function()
            local p = job.driver:get_progress()
            if job.retire_cancel_pending then
                job.retire_cancel_pending = nil; job.driver:cancel(job.error)
            elseif p.state == "local_committed" then job.driver:finish_stage("pending")
            elseif p.state == "prepared" then job.driver:cancel(job.error)
            else job.driver:step_one() end
        end)
        job:_adopt()
    end
    if budget then budget:consume(1) end
    if not terminal(job.state) then job:_retire() end
    return job:get_progress()
end
function M.shutdown(options)
    options = options or {}
    stopping = true
    M.invalidate_scope()
    local remaining = {}; for job in pairs(live) do remaining[#remaining + 1] = job end
    for _, job in ipairs(remaining) do job:cancel("Lua executor unloading") end
    local maximum = options.max_steps or 1024
    assert(type(maximum) == "number" and maximum >= 0 and maximum <= 65536 and maximum % 1 == 0,
        "Invalid Lua shutdown step limit")
    local steps = 0
    while #retiring > 0 and steps < maximum do
        M.pump(); steps = steps + 1
    end
    local pending, uncertain = 0, 0
    for job in pairs(live) do
        pending = pending + 1
        if job.commit_reserved then job.local_uncertain = true; uncertain = uncertain + 1 end
        -- A budget-exhausted unload preserves pending/backup/lock evidence.
        -- Close only known owned handles; never claim the publication settled.
        for _, owner in ipairs({ job.driver or {}, job.driver and job.driver.child or {} }) do
            if owner.file then
                local file = owner.file; owner.file = nil
                local ok, why = pcall(function() assert(file:close()) end)
                if not ok then job.cleanup_errors[#job.cleanup_errors + 1] = tostring(why) end
            end
        end
    end
    return pending == 0, { complete = pending == 0, pending = pending, uncertain = uncertain,
        steps = steps, reason = pending > 0 and "Reserved publication needs next-start recovery" or nil }
end
return M
