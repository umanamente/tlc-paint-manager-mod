-- Session-local processing policy. Keep this module dependency-free: codecs,
-- native worker runtimes and budget constructors all consume it.
local M = {}
local registry, by_key, base, overrides = {}, {}, {}, {}
local revision, requested, requested_reason = 0, nil, nil
local task_policy
local paused, step_credit, capture_next, primitive_step = false, nil, false, false
local work_sequence = 0
local metrics = { callbacks = 0, stages = {}, events = {}, captures = {} }
local function copy(value)
    local result = {}
    for key, item in pairs(value or {}) do
        if type(item) == "table" then result[key] = copy(item) else result[key] = item end
    end
    return result
end
local function add(key, group, kind, default, label, unit, minimum, maximum, step, choices)
    local descriptor = { key = key, group = group, kind = kind, default = default, label = label,
        label_key = "ui.processing.field." .. key, help_key = "ui.processing.help." .. key,
        unit = unit or "", min = minimum, max = maximum, step = step or 1, choices = choices }
    registry[#registry + 1], by_key[key], base[key] = descriptor, descriptor, default
    return descriptor
end
local function flag(key, group, default, label) return add(key, group, "boolean", default, label) end
local function number(key, group, default, label, unit, low, high, step)
    return add(key, group, "number", default, label, unit, low or 1, high or 65536, step)
end
flag("batching_enabled", "execution", true, "Cooperative batching")
flag("automatic_preparation", "execution", true, "Automatic preparation")
flag("time_limit_enabled", "scheduler", true, "Limit elapsed work time")
flag("unit_limit_enabled", "scheduler", true, "Limit work units")
number("open_work_budget_ms", "scheduler", 30, "Work time target (interface visible)", "ms", 0.01, 10000, 0.1)
number("work_budget_ms", "scheduler", 2, "Work time target (background)", "ms", 0.01, 10000, 0.1)
number("work_units_per_tick", "scheduler", 8, "Work units per callback", "units")
number("minimum_pure_quantum_ms", "scheduler", 0.1, "Minimum pure-work quantum", "ms", 0.001, 100, 0.01)
number("pump_ms", "scheduler", 16, "Closed-window callback delay (next start)", "ms", 1, 1000).restart_required = true
for _, item in ipairs({
    {"foreground_units",6,"Foreground job"}, {"paint_urgent_units",4,"Demanded paint preparation"},
    {"stock_open_units",4,"Open-window stock preparation"},
    {"resource_signal_units",7,"Signal illustration resources"}, {"resource_idle_units",4,"Idle resources"},
    {"resource_open_units",2,"Visible resources"}, {"preference_units",1,"Preference settlement"} }) do
    local descriptor = number(item[1], "quotas", item[2], item[3], "units")
    descriptor.allow_inherit, descriptor.allow_unlimited = true, true
end
for _, item in ipairs({
    {"pure_primitives",128,"Pure data"}, {"schema_primitives",128,"Schema validation"},
    {"merge_primitives",128,"Merge and clone"}, {"projection_primitives",128,"View projection"},
    {"aggregate_rows_per_unit",32,"Collection aggregation"}, {"report_steps_per_unit",32,"Report encoding"},
    {"ui_stock_rows_per_unit",32,"Stock patch row preparation"} }) do
    number(item[1], "primitives", item[2], item[3], "primitives")
end
number("source_batch_records", "primitives", 128, "Source-selection submission", "records", 1, 512)
for _, item in ipairs({
    {"json_read_bytes",256,"JSON parser text"}, {"json_string_bytes",1024,"JSON string encoding"},
    {"json_key_bytes",1024,"JSON key encoding"}, {"compare_bytes",1024,"Canonical byte comparison"},
    {"library_read_bytes",8192,"Library file reads"}, {"library_write_bytes",8192,"Library file writes"},
    {"legacy_read_bytes",8192,"Legacy import reads"}, {"report_chunk_bytes",8192,"Report file writes"} }) do
    number(item[1], "bytes", item[2], item[3], "bytes", 1, 32768)
end
for _, item in ipairs({
    {"ui_rows_per_tick",2,"Native page construction"}, {"ui_stock_items_per_tick",2,"Native stock patches"},
    {"ui_icon_units",1,"Visible icon preparation"}, {"icon_pump_units",8,"Icon bank preparation"} }) do
    number(item[1], "ui", item[2], item[3], "items")
end
number("icon_decode_bytes", "ui", 256, "Control image decode", "bytes", 1, 32768)
number("illustration_decode_bytes", "ui", 1024, "Signal illustration decode", "bytes", 1, 32768)
number("job_admission_target", "scheduler", 4, "Admitted Lua jobs", "jobs", 1, 16)
number("job_steps_per_unit", "primitives", 64, "Lua task steps per unit", "steps")
number("max_pending", "scheduler", 4, "Pending commands and receipts", "entries", 1, 64)
for _, item in ipairs({
    {"preload_icons","Preload control images"}, {"prewarm_tabs","Prewarm hidden tabs"} }) do
    flag(item[1], "preparation", true, item[2])
end
for _, item in ipairs({
    {"box_cache_read","Read box disk cache"}, {"box_cache_write","Write box disk cache"},
    {"paint_hints_enabled","Reuse session paint catalog"}, {"box_hints_enabled","Reuse box hints"},
    {"view_cache_enabled","Reuse view projection"}, {"tab_cache_enabled","Reuse tab widgets"},
    {"texture_cache_enabled","Reuse prepared textures"} }) do flag(item[1], "caches", true, item[2]) end
for _, item in ipairs({
    {"stock_write_delay_ms",2000,"Box cache write debounce"}, {"stock_disk_retry_ms",2000,"Box cache disk retry"},
    {"resource_retry_ms",500,"Resource host retry"},
    {"context_poll_ms",500,"Context polling"}, {"ui_progress_delay_ms",200,"Progress display delay"},
    {"ui_progress_update_ms",150,"Progress update interval"} }) do
    number(item[1], "cadence", item[2], item[3], "ms", 1, 3600000)
end
by_key.ui_progress_delay_ms.max, by_key.ui_progress_update_ms.max = 10000, 10000
add("metrics_level", "diagnostics", "enum", "off", "Measurements", "", nil, nil, nil,
    { "off", "summary", "detailed" })
number("slow_unit_ms", "diagnostics", 10, "Slow work threshold", "ms", 0.01, 60000, 0.1)
number("log_sample_callbacks", "diagnostics", 60, "Detailed log sample interval", "callbacks", 1, 1000000)

function M.descriptors() return copy(registry) end
function M.defaults() return copy(base) end
function M.get(key, fallback)
    if task_policy and task_policy[key] ~= nil then return task_policy[key] end
    local value = overrides[key]
    if value == "inherit" then value = nil end
    if value ~= nil then return value end
    if fallback ~= nil then return fallback end
    return base[key]
end
function M.snapshot()
    local result = copy(base)
    for key, value in pairs(overrides) do result[key] = value == "inherit" and base[key] or value end
    return result
end
function M.work_target_ms(open, config)
    local key = open and "open_work_budget_ms" or "work_budget_ms"
    return M.get(key, config and config[key])
end
function M.generation() return revision end
-- A pure task temporarily reads its frozen policy without changing the session
-- editor, revision or requested transition. Always restore after Lua errors.
function M.with_snapshot(policy, fn, ...)
    local previous = task_policy
    task_policy = policy
    local result = table.pack(pcall(fn, ...))
    task_policy = previous
    if not result[1] then error(result[2], 0) end
    return table.unpack(result, 2, result.n)
end
function M.batching_enabled() return M.get("batching_enabled") ~= false end
function M.quota(key, fallback)
    if not M.batching_enabled() then return nil end
    local value = M.get(key, fallback)
    return value ~= "unlimited" and value or nil
end
function M.bind(config)
    assert(revision == 0 and requested == nil, "Bind production defaults before applying a policy")
    for key, descriptor in pairs(by_key) do
        if config[key] ~= nil then base[key], descriptor.default = config[key], config[key] end
    end
end
function M.validate(changes)
    if type(changes) ~= "table" or getmetatable(changes) ~= nil then return false, "processing_invalid_profile" end
    for key, value in pairs(changes) do
        local descriptor = by_key[key]
        if not descriptor then return false, "processing_unknown_key:" .. tostring(key) end
        local valid = descriptor.allow_inherit and value == "inherit" or descriptor.allow_unlimited and value == "unlimited"
        if not valid and descriptor.kind == "boolean" then valid = type(value) == "boolean"
        elseif not valid and descriptor.kind == "number" then
            valid = type(value) == "number" and value == value and value >= descriptor.min and value <= descriptor.max
                and (descriptor.step ~= 1 or value % 1 == 0)
        elseif not valid and descriptor.kind == "enum" then
            for _, choice in ipairs(descriptor.choices) do if value == choice then valid = true end end
        end
        if not valid then return false, "processing_invalid_value:" .. key end
    end
    return true
end
-- Schema 1 profiles contained the retired native backend and paint-file knobs.
-- Accept them only at the explicit import boundary, report every retired key,
-- and preserve the values of still meaningful controls.
local retired_boolean = { worker_enabled=true, preload_paints=true, paint_discovery_enabled=true,
    paint_cache_read=true, paint_cache_write=true }
local retired_number = { worker_input_bytes=true, worker_result_bytes=true, worker_text_bytes=true,
    worker_pack_primitives=true, worker_decode_primitives=true, worker_transfer_quota=true,
    worker_poll_quota=true, worker_result_quota=true, worker_retire_quota=true,
    worker_admission_target=true, native_task_steps=true, native_report_checks=true,
    native_report_bytes=true, native_report_buffer_bytes=true, native_input_decode_bytes=true,
    native_result_page_bytes=true, paint_cache_idle_callbacks=true, paint_cache_discovery_ms=true }
-- Both earlier schemas could enable timed scans. Ignore these validated
-- historical values when importing a profile into the open-only scheduler.
local old_scan_flags = { preload_stock_open=true, preload_stock_closed=true,
    stock_discovery_enabled=true, stock_prefix_enabled=true }
local old_scan_limits = { stock_idle_callbacks=10000, stock_idle_inventory_ms=3600000,
    stock_discovery_ms=3600000, stock_refresh_ms=3600000, stock_incomplete_retry_ms=3600000,
    stock_prefix_first=128, stock_prefix_growth=16 }
local old_scan_quotas = { paint_idle_units=true, stock_closed_units=true }
function M.import_profile(values, schema)
    if type(values) ~= "table" or getmetatable(values) ~= nil then return nil, "processing_invalid_profile" end
    if schema ~= 1 and schema ~= 2 then return nil, "processing_invalid_profile" end
    local result, retired = {}, {}
    for key, value in pairs(values) do
        if by_key[key] then result[key] = value
        elseif old_scan_flags[key] or old_scan_limits[key] or old_scan_quotas[key] then
            local limit = old_scan_limits[key] or (old_scan_quotas[key] and 65536)
            local valid = old_scan_flags[key] and type(value) == "boolean"
                or old_scan_quotas[key] and (value == "inherit" or value == "unlimited")
                or limit and type(value) == "number" and value % 1 == 0
                    and value >= (key == "stock_prefix_growth" and 2 or 1) and value <= limit
            if not valid then return nil, "processing_invalid_value:" .. key end
            retired[#retired + 1] = key
        elseif schema == 1 and (retired_boolean[key] or retired_number[key]) then
            local valid = retired_boolean[key] and type(value) == "boolean"
                or retired_number[key] and type(value) == "number" and value == value
                    and value >= 1 and value <= 3600000 and value % 1 == 0
            if not valid then return nil, "processing_invalid_value:" .. key end
            retired[#retired + 1] = key
            if key == "worker_admission_target" and values.job_admission_target == nil then result.job_admission_target = value end
            if key == "native_task_steps" and values.job_steps_per_unit == nil then result.job_steps_per_unit = value end
        else return nil, "processing_unknown_key:" .. tostring(key) end
    end
    local ok, err = M.validate(result)
    if not ok then return nil, err end
    table.sort(retired)
    return result, { migrated = schema == 1 or #retired > 0, retiredKeys = retired, schemaVersion = 2 }
end
function M.request(changes, reason)
    local ok, err = M.validate(changes)
    if not ok then return false, err end
    local next_values = requested and copy(requested) or M.snapshot()
    for key, value in pairs(changes) do next_values[key] = value end
    requested, requested_reason = next_values, reason or "processing_pending"
    return true, requested_reason
end
function M.pending() return requested ~= nil end
function M.requested() return requested and copy(requested) or nil end
function M.commit(config, startup)
    if not requested then return false end
    local next_values = requested
    local previous_values = M.snapshot()
    overrides = {}
    for key, value in pairs(next_values) do
        local descriptor = by_key[key]
        if descriptor.restart_required and not startup then value = previous_values[key] end
        if value ~= base[key] then overrides[key] = value end
        if config and config[key] ~= nil then config[key] = value == "inherit" and base[key] or value end
    end
    revision, requested, requested_reason = revision + 1, nil, nil
    paused, step_credit = false, nil
    return true
end
-- Only trusted, validated primitive data reaches an isolated fixed-task state.
function M.install_snapshot(snapshot)
    local ok, err = M.validate(snapshot)
    assert(ok, err)
    overrides = copy(snapshot)
    requested, requested_reason = nil, nil
    revision = revision + 1
end
function M.profiles()
    local production = M.defaults()
    local function profile(id, changes)
        local values = copy(production)
        for key, value in pairs(changes) do values[key] = value end
        return { id = id, label_key = "ui.processing.profile." .. id, values = values }
    end
    return { profile("defaults", {}),
        profile("demand", { automatic_preparation = false }),
        profile("synchronous", { batching_enabled = false, automatic_preparation = false }),
        profile("small", { work_units_per_tick = 1, ui_rows_per_tick = 1, ui_stock_items_per_tick = 1,
            pure_primitives = 1, schema_primitives = 1, merge_primitives = 1, projection_primitives = 1,
            report_steps_per_unit = 1, aggregate_rows_per_unit = 1, source_batch_records = 1 }) }
end
function M.set_paused(value) paused, step_credit, primitive_step = value == true, nil, false end
function M.is_paused() return paused end
function M.step(kind) paused, step_credit = true, kind == "primitive" and "primitive" or "packet" end
function M.primitive_step() return primitive_step end
function M.set_primitive_step(value) primitive_step = value == true end
function M.note_work(units) work_sequence = work_sequence + (units or 1) end
function M.work_sequence() return work_sequence end
function M.admit_preparation()
    if not paused then return true end
    if step_credit then local credit = step_credit; step_credit = nil; return true, credit end
    return false
end
function M.capture_next() capture_next = true end
function M.measuring() return capture_next or M.get("metrics_level") ~= "off" end
function M.reset_metrics() metrics = { callbacks = 0, stages = {}, events = {}, captures = {} } end
function M.event(name, amount)
    if not M.measuring() then return end
    metrics.events[name] = (metrics.events[name] or 0) + (amount or 1)
end
function M.record(name, elapsed, units, details)
    if not M.measuring() then return end
    local stage = metrics.stages[name] or { count = 0, totalMs = 0, maxMs = 0, units = 0 }
    metrics.stages[name] = stage
    stage.count, stage.units = stage.count + 1, stage.units + (units or 0)
    if type(elapsed) == "number" and elapsed >= 0 then
        stage.totalMs, stage.maxMs = stage.totalMs + elapsed, math.max(stage.maxMs, elapsed)
        if elapsed >= M.get("slow_unit_ms") then M.event("slow_units") end
    end
    if M.get("metrics_level") == "detailed" and details then stage.last = copy(details) end
end
function M.callback() if M.measuring() then metrics.callbacks = metrics.callbacks + 1 end end
function M.finish_operation(kind, details)
    if not capture_next then return end
    capture_next = false
    metrics.captures[#metrics.captures + 1] = { kind = kind, policy = M.snapshot(), generation = revision,
        details = copy(details), metrics = { callbacks = metrics.callbacks, stages = copy(metrics.stages), events = copy(metrics.events) } }
    while #metrics.captures > 8 do table.remove(metrics.captures, 1) end
end
function M.metrics() return copy(metrics) end
function M.describe(include_metrics)
    local configured = copy(base)
    for key, value in pairs(overrides) do configured[key] = value end
    return { effective = M.snapshot(), configured = configured, requested = M.requested(), pending = M.pending(),
        status = requested_reason or "processing_applied", generation = revision,
        paused = paused, metrics = include_metrics ~= false and M.metrics() or nil }
end
return M
