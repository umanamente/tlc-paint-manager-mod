-- Bounded Lua JSON/file output. Publish only a completed owned temporary file.
local processing = require("paintmanager.processing")
local json = require("paintmanager.json")
local filesystem = require("paintmanager.filesystem")
local M = {}
local Job = {}
Job.__index = Job
local sequence = 0

local function limit(value, fallback, maximum)
    if type(value) ~= "number" or value ~= value or value < 1 or value == math.huge then return fallback end
    return math.min(maximum, math.floor(value))
end

local function microstep_deadline_reached(budget)
    -- work_budget intentionally grants one work unit when units==0 even after
    -- the time target. Here the minimum progress is ONE encoder primitive,
    -- rather than all 32 primitives in the writer's charged batching unit.
    if budget.batching_enabled == false or budget.time_limit_enabled == false then return false end
    if budget.units == 0 and type(budget.elapsed_ms) == "function"
        and type(budget.target_ms) == "number" and budget.target_ms > 0 then
        local elapsed = budget:elapsed_ms()
        return budget.clock_valid == true and type(elapsed) == "number" and elapsed >= budget.target_ms
    end
    return budget:expired()
end

function M.begin(path, report, options)
    assert(type(path) == "string" and path ~= "" and not path:find("\0", 1, true), "Invalid report path")
    options = options or {}
    sequence = sequence + 1
    local job = setmetatable({ state = "running", phase = "check", path = path,
        encoder = json.begin(report, options), pieces = {}, buffer_bytes = 0, bytes_written = 0,
        chunks_written = 0, encoder_steps = 0, completed = 0,
        max_write_bytes = limit(options.write_bytes, processing.get("report_chunk_bytes", 8192), 32768),
        microsteps = limit(options.microsteps_per_unit, processing.get("report_steps_per_unit", 32), 65536),
        fs = options.fs or filesystem,
        cleanup_errors = json.array(), collision_attempts = 0,
        backend = "lua_cooperative" }, Job)
    local token = tostring(job):match("0x(%x+)") or tostring(sequence)
    job.temp_prefix = path .. ".tmp-" .. token .. "-"
    job.temp = job.temp_prefix .. sequence
    return job
end

function Job:_exists(path)
    local file, err, code = self.fs.open(path, "rb")
    if not file then
        assert(code == 2, "Cannot inspect report path: " .. tostring(err))
        return false
    end
    local ok, err = file:close()
    if not ok then error("Cannot close report existence check: " .. tostring(err)) end
    return true
end

function Job:_cleanup()
    if self.file then
        local file = self.file
        self.file = nil
        local ok, value, err = pcall(function() return file:close() end)
        if not ok or not value then self.cleanup_errors[#self.cleanup_errors + 1] = tostring(ok and err or value) end
    end
    if self.temp_created and self.temp then
        local ok, value, err = pcall(self.fs.remove, self.temp)
        if not ok or not value then self.cleanup_errors[#self.cleanup_errors + 1] = tostring(ok and err or value)
        else self.temp_created = false end
    end
    self.encoder:cancel("Report output stopped")
    self.pieces, self.pending_chunk = nil, nil
end

function Job:_fail(err)
    self.state, self.phase, self.error = "error", "error", tostring(err)
    self:_cleanup()
end

function Job:_append(chunk)
    if not chunk or chunk == "" then return true end
    if self.buffer_bytes + #chunk > self.max_write_bytes then
        self.pending_chunk, self.pending_offset = chunk, 1
        return false
    end
    self.pieces[#self.pieces + 1] = chunk
    self.buffer_bytes = self.buffer_bytes + #chunk
    return true
end

function Job:_fill_pending()
    if not self.pending_chunk then return end
    local available = self.max_write_bytes - self.buffer_bytes
    if available > 0 then
        local finish = math.min(#self.pending_chunk, self.pending_offset + available - 1)
        local piece = self.pending_chunk:sub(self.pending_offset, finish)
        self.pieces[#self.pieces + 1] = piece
        self.buffer_bytes = self.buffer_bytes + #piece
        self.pending_offset = finish + 1
        if self.pending_offset > #self.pending_chunk then self.pending_chunk, self.pending_offset = nil, nil end
    end
end

function Job:_advance(budget)
    if self.phase == "check" then
        if self:_exists(self.path) then error("Report already exists; refusing replacement") end
        self.phase = "open"
    elseif self.phase == "open" then
        if self:_exists(self.temp) then
            self.collision_attempts = self.collision_attempts + 1
            if self.collision_attempts > 16 then error("Cannot choose a unique report temporary path") end
            sequence = sequence + 1
            self.temp = self.temp_prefix .. sequence
            return
        end
        local file, err = self.fs.open(self.temp, "wb")
        if not file then error("Cannot create diagnostic report: " .. tostring(err)) end
        self.file, self.temp_created, self.phase = file, true, "encode"
    elseif self.phase == "encode" then
        self:_fill_pending()
        if self.buffer_bytes >= self.max_write_bytes or self.pending_chunk then self.phase = "write"; return end
        if self.encoding_done then
            if not self.newline_added then self:_append("\n"); self.newline_added = true end
            self.phase = self.buffer_bytes > 0 and "write" or "flush"
            return
        end
        for index = 1, self.microsteps do
            if index > 1 and budget and microstep_deadline_reached(budget) then return end
            local progress = self.encoder:step_one()
            self.encoder_steps = self.encoder_steps + 1
            if progress.state == "error" then error(progress.error) end
            if progress.state == "cancelled" then error("Report encoder was cancelled") end
            if progress.state == "done" then self.encoding_done = true end
            if not self:_append(progress.chunk) or self.buffer_bytes >= self.max_write_bytes then self.phase = "write"; return end
            if self.encoding_done then return end
        end
    elseif self.phase == "write" then
        if self.buffer_bytes == 0 then self.phase = "encode"; return end
        local data = table.concat(self.pieces)
        assert(#data <= self.max_write_bytes, "Report write bound exceeded")
        local wrote, err = self.file:write(data)
        if not wrote then error("Report write failed: " .. tostring(err)) end
        self.bytes_written = self.bytes_written + #data
        self.chunks_written = self.chunks_written + 1
        self.pieces, self.buffer_bytes = {}, 0
        self.phase = "encode"
    elseif self.phase == "flush" then
        local flushed, err = self.file:flush()
        if not flushed then error("Report flush failed: " .. tostring(err)) end
        self.phase = "close"
    elseif self.phase == "close" then
        local file = self.file
        self.file = nil
        local closed, err = file:close()
        if not closed then error("Report close failed: " .. tostring(err)) end
        self.phase = "publish"
    elseif self.phase == "publish" then
        if self:_exists(self.path) then error("Report already exists; refusing replacement") end
        -- Windows CRT rename refuses an existing target. Unique caller paths
        -- and the final check also protect ordinary report-name collisions.
        local renamed, err = self.fs.rename(self.temp, self.path)
        if not renamed then error("Report rename failed: " .. tostring(err)) end
        self.temp_created, self.temp, self.state, self.phase = false, nil, "done", "done"
        self.pieces, self.encoder = nil, nil
    end
end

function Job:get_progress()
    return { state = self.state, phase = self.phase, processed = self.completed, completed = self.completed,
        bytesWritten = self.bytes_written, chunksWritten = self.chunks_written, encoderSteps = self.encoder_steps,
        maxWriteBytes = self.max_write_bytes, microstepsPerUnit = self.microsteps,
        path = self.state == "done" and self.path or nil, error = self.error,
        temporaryPath = self.temp_created and self.temp or nil,
        cleanupErrors = self.cleanup_errors,
        backend = self.backend, fallbackReason = self.fallback_reason,
        semantics = "Cooperative JSON and bounded writes; native filesystem operations are indivisible" }
end

function Job:step_one(budget)
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(self._advance, self, budget)
    self.completed = self.completed + 1
    if not ok then self:_fail(err) end
    return self:get_progress()
end

function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do
        self:step_one(budget)
        budget:consume(1)
    end
    return self:get_progress()
end

function Job:cancel(reason)
    if self.state ~= "running" then return self:get_progress() end
    self.state, self.phase, self.error = "cancelled", "cancelled", reason
    self:_cleanup()
    return self:get_progress()
end

function Job:get_result() return self.state == "done" and self.path or nil end

return M
