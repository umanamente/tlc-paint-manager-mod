-- Constant-size presentation scheduling over controller-supplied nominal time.
-- This module performs no clock, engine, widget, filesystem or save calls.
local M = {}
local Adapter = {}; Adapter.__index = Adapter
local FIELDS = { "generation", "kind", "phase", "mode", "completed", "total", "unit", "coverage", "state" }
local IMMEDIATE = { "kind", "phase", "mode", "unit", "coverage", "state" }

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function duration(value, fallback)
    value = tonumber(value)
    return finite(value) and value >= 0 and math.min(value, 10000) or fallback
end

local function copy(value)
    local result = {}
    for _, key in ipairs(FIELDS) do result[key] = value[key] end
    return result
end

local function equal(left, right, fields)
    if not left or not right then return left == right end
    for _, key in ipairs(fields or FIELDS) do if left[key] ~= right[key] then return false end end
    return true
end

function M.new(config)
    config = config or {}
    return setmetatable({ delay_ms = duration(config.ui_progress_delay_ms, 200),
        update_ms = duration(config.ui_progress_update_ms, 150),
        done_ms = duration(config.ui_progress_done_ms, 600) }, Adapter)
end

function Adapter:reset()
    self.generation, self.pending, self.applied, self.started_at = nil, nil, nil, nil
    self.visible, self.last_emit_at, self.clear_at, self.last_now = false, nil, nil, nil
end

function Adapter:_time(now_ms)
    local now = finite(now_ms) and now_ms or self.last_now or 0
    if self.last_now and now < self.last_now then now = self.last_now end
    self.last_now = now
    return now
end

function Adapter:_emit(snapshot, now)
    self.applied, self.last_emit_at = copy(snapshot), now
    return copy(snapshot)
end

function Adapter:_clear(now)
    self.visible, self.clear_at = false, nil
    local idle = { generation = self.generation, state = "idle", mode = "idle" }
    if equal(self.applied, idle) then return nil end
    return self:_emit(idle, now)
end

function Adapter:_advance(now)
    local latest = self.pending
    if not latest then return nil end
    local state = latest.state
    if state == "idle" then
        if self.visible then return self:_clear(now) end
        return nil
    end
    local success = state == "done" or state == "cancelled"
    if success then
        -- A completed short operation never flashes a late success indicator.
        if not self.visible then return nil end
        if not self.clear_at then self.clear_at = now + self.done_ms end
        if now >= self.clear_at then return self:_clear(now) end
    elseif state == "error" then
        self.clear_at = nil -- Errors and partial coverage remain visible.
    else
        self.clear_at = nil
        if not self.visible and now < self.started_at + self.delay_ms then return nil end
    end
    if equal(self.applied, latest) then return nil end
    local immediate = not self.visible or not equal(self.applied, latest, IMMEDIATE)
    if immediate or not self.last_emit_at or now >= self.last_emit_at + self.update_ms then
        self.visible = true
        return self:_emit(latest, now)
    end
    return nil
end

function Adapter:offer(progress, now_ms)
    if type(progress) ~= "table" then return nil end
    local generation = progress.generation
    if not finite(generation) or generation < 0 or generation % 1 ~= 0 then return nil end
    if self.generation and generation < self.generation then return nil end
    local now = self:_time(now_ms)
    local incoming = copy(progress)
    incoming.state = incoming.state or "running"
    if incoming.state ~= "running" and incoming.state ~= "done" and incoming.state ~= "error"
        and incoming.state ~= "cancelled" and incoming.state ~= "idle" then return nil end
    if incoming.coverage == "partial" then incoming.state = "error" end
    if incoming.state ~= "running" then incoming.mode = "idle"
    elseif incoming.mode ~= "determinate" then incoming.mode = "indeterminate"
    elseif not finite(incoming.completed) or not finite(incoming.total)
        or incoming.total == 0 and incoming.unit ~= "containers"
        or incoming.completed < 0 or incoming.completed > incoming.total then
        incoming.mode, incoming.completed, incoming.total = "indeterminate", nil, nil
    end
    local old_visible = self.visible == true
    local replaced = self.generation ~= generation
    if replaced then
        self.generation, self.started_at = generation, now
        self.applied, self.clear_at, self.last_emit_at, self.visible = nil, nil, nil, false
    end
    self.pending = incoming
    if replaced and old_visible and incoming.state ~= "error" then
        -- Clear a preceding warning/completion while a new operation receives
        -- its own appearance delay; never leave an old outcome on new work.
        return self:_clear(now)
    end
    return self:_advance(now)
end

function Adapter:pump(now_ms)
    return self:_advance(self:_time(now_ms))
end

return M
