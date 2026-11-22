-- Batch only bounded plain-data primitives inside one shared charged unit.
-- The caller must choose a known pure JSON/schema phase and process every
-- yielded chunk. Native calls, filesystem work and general jobs are excluded.
-- The caller charges the unit once; this helper never owns another budget.
local processing = require("paintmanager.processing")
local M = { MAX_PRIMITIVES = 128, MAX_CONFIGURED_PRIMITIVES = 65536, MINIMUM_QUANTUM_MS = 0.1 }

local function finite(value)
    return type(value) == "number" and value == value and value >= 0 and value < math.huge
end

function M.run(advance, budget, options)
    assert(type(advance) == "function", "Plain work requires an explicit pure primitive callback")
    options = options or {}
    local maximum = options.max_primitives or processing.get(options.group or "pure_primitives", M.MAX_PRIMITIVES)
    assert(finite(maximum) and maximum % 1 == 0 and maximum >= 1 and maximum <= M.MAX_CONFIGURED_PRIMITIVES,
        "Plain work primitive limit must be an integer from 1 to 65536")
    if type(processing.primitive_step) == "function" and processing.primitive_step() then maximum = 1 end
    if budget then
        assert(type(budget) == "table" and type(budget.expired) == "function",
            "Plain work requires the shared scheduler budget")
        if budget:expired() then return nil, 0 end
    end

    -- Count-only admission is also valid. Missing timing never invents a
    -- duration or another clock; the primitive cap remains independent.
    local start = budget and type(budget.elapsed_ms) == "function" and budget:elapsed_ms() or nil
    local timed = budget and budget.batching_enabled ~= false and budget.time_limit_enabled ~= false
        and budget.clock_valid == true and finite(start) and finite(budget.target_ms)
    -- Mandatory housekeeping can already be past the soft target. The first
    -- admitted unit retains a small quantum, checked between bounded primitives,
    -- rather than doing one trivial parser token and starving future phases.
    local deadline = timed and budget.target_ms or nil
    if timed and budget.units == 0 then deadline = math.max(deadline, start + processing.get("minimum_pure_quantum_ms", M.MINIMUM_QUANTUM_MS)) end

    local progress, count = nil, 0
    while count < maximum do
        if count > 0 and timed then
            local now = budget:elapsed_ms()
            if budget.clock_valid ~= true or not finite(now) or now < start then timed = false
            elseif now >= deadline then break end
        end
        progress = advance()
        count = count + 1
        assert(type(progress) == "table" and type(progress.state) == "string",
            "Plain primitive must return job progress")
        if progress.state ~= "running" then break end
    end
    return progress, count
end

return M
