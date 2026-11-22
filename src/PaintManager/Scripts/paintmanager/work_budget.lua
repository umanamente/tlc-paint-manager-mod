-- Cooperative game-thread budget. A native call cannot be preempted from Lua.
-- Check BETWEEN work units; admit one bounded unit even if mandatory input/UI
-- housekeeping used the deadline, so work cannot starve. The unit cap also works
-- if clock() fails.
local processing = require("paintmanager.processing")
local M = {}
M.__index = M

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

-- The Windows CRT's clock includes elapsed time between game-thread callbacks.
function M.clock_ms()
    local ok, seconds = pcall(os.clock)
    if ok and finite(seconds) and seconds >= 0 and seconds * 1000 < math.huge then
        return seconds * 1000
    end
end

function M.new(milliseconds, max_units, clock, options)
    options = options or {}
    assert(finite(milliseconds) and milliseconds > 0, "work budget must be positive finite milliseconds")
    assert(finite(max_units) and max_units >= 1 and max_units % 1 == 0,
        "work unit limit must be a positive integer")
    clock = clock or os.clock
    assert(type(clock) == "function", "work budget clock must be a function")
    local self = setmetatable({ target_ms = milliseconds, max_units = max_units,
        units = 0, clock = clock, clock_valid = true, elapsed = 0,
        batching_enabled = options.batching_enabled ~= false and (options.batching_enabled == true or processing.batching_enabled()),
        time_limit_enabled = options.time_limit_enabled ~= false and (options.time_limit_enabled == true or processing.get("time_limit_enabled", true)),
        unit_limit_enabled = options.unit_limit_enabled ~= false and (options.unit_limit_enabled == true or processing.get("unit_limit_enabled", true)) }, M)
    local ok, start = pcall(clock)
    if not ok or not finite(start) or start < 0 then
        self.clock_valid, self.clock_error = false, "clock unavailable or invalid"
    else self.started, self.last = start, start end
    return self
end

function M:elapsed_ms()
    if self.clock_valid then
        local ok, now = pcall(self.clock)
        if not ok or not finite(now) or now < 0 or now < self.last then
            self.clock_valid, self.clock_error = false, "clock failed, wrapped or moved backwards"
        else
            self.last = now
            self.elapsed = (now - self.started) * 1000
        end
    end
    return self.elapsed
end

function M:expired()
    local elapsed = self:elapsed_ms()
    if not self.batching_enabled then return false end
    return (self.unit_limit_enabled and self.units >= self.max_units)
        or (self.time_limit_enabled and self.units > 0 and self.clock_valid and elapsed >= self.target_ms)
end

function M:consume(units)
    units = units or 1
    assert(finite(units) and units >= 0 and units % 1 == 0, "work units must be a nonnegative integer")
    self.units = self.units + units
    if type(processing.note_work) == "function" then processing.note_work(units) end
    self:elapsed_ms()
end

function M:metrics()
    local elapsed = self:elapsed_ms()
    return { budgetMs = self.target_ms, maxUnits = self.max_units, units = self.units,
        elapsedMs = elapsed, clockValid = self.clock_valid, clockError = self.clock_error,
        batchingEnabled = self.batching_enabled, timeLimitEnabled = self.time_limit_enabled,
        unitLimitEnabled = self.unit_limit_enabled,
        clockSource = self.clock_source or "os.clock", nativeClock = self.native_clock == true,
        overBudget = self.clock_valid and elapsed > self.target_ms,
        minimumProgressUnits = 1,
        semantics = "cooperative; at least one bounded unit; checked between units; os.clock is platform-dependent" }
end

return M
