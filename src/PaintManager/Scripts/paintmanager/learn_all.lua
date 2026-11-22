-- Explicit bulk learning: one fresh capture, then separately budgeted recipes.
-- Cans are never consumed. Only plain descriptors/results survive each step.
local collection = require("paintmanager.collection")
local learning = require("paintmanager.learn_recipe")
local paint_view = require("paintmanager.paint_view")
local json = require("paintmanager.json")
local M, Job = {}, {}
Job.__index = Job

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function copy(value, depth, seen)
    local kind = type(value)
    if kind == "nil" or kind == "boolean" or kind == "string" then return value end
    if kind == "number" then return finite(value) and value or tostring(value) end
    if kind ~= "table" then return "<non-plain " .. kind .. ">" end
    depth, seen = depth or 0, seen or {}
    if depth >= 20 or seen[value] then return "<nested or cyclic diagnostic>" end
    seen[value] = true
    local result = #value > 0 and json.array() or {}
    for key, item in pairs(value) do
        if type(key) == "string" or type(key) == "number" then result[key] = copy(item, depth + 1, seen) end
    end
    seen[value] = nil
    return result
end

local function compact_preflight(value)
    local result = {}
    for key, item in pairs(value or {}) do
        if key ~= "capture" then result[key] = copy(item) end
    end
    result.captureReference = "batch.preflight.capture"
    return result
end

local function compact_receipt(receipt)
    local result = {}
    for key, value in pairs(receipt or {}) do
        if key ~= "events" and key ~= "preflight" then result[key] = copy(value) end
    end
    result.preflight = compact_preflight(receipt and receipt.preflight)
    result.events = json.array()
    for _, event in ipairs(receipt and receipt.events or {}) do
        result.events[#result.events + 1] = { event = event.event, capturedUtc = event.capturedUtc,
            data = event.event == "preflight_complete" and compact_preflight(event.data) or copy(event.data) }
    end
    return result
end

function Job:_now_ms()
    if self.receipt.metrics.clockValid == false then return nil end
    local ok, value = pcall(self.clock)
    if not ok or not finite(value) or value < 0 or (self.lastClock and value < self.lastClock) then
        self.receipt.metrics.clockValid = false
        self.receipt.metrics.clockError = ok and "Clock invalid or moved backwards" or tostring(value)
        self.receipt.metrics.workMs, self.receipt.metrics.maxSliceMs, self.receipt.durationMs = nil, nil, nil
        return nil
    end
    self.lastClock = value
    return value
end

function Job:_emit(event, data)
    if self.emit then
        local ok, err = pcall(self.emit, event, data)
        if not ok then
            self.receipt.loggingErrors = self.receipt.loggingErrors or json.array()
            self.receipt.loggingErrors[#self.receipt.loggingErrors + 1] = tostring(err)
        end
    end
end

function Job:_event(event, data)
    local values
    if event == "preflight_complete" then
        values = {}
        for key, value in pairs(data or {}) do values[key] = key == "capture" and value or copy(value) end
    else values = copy(data or {}) end
    local entry = { event = event, capturedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"), data = values }
    self.receipt.events[#self.receipt.events + 1] = entry
    if event == "preflight_complete" and entry.data.capture then
        local emitted = compact_preflight(entry.data)
        emitted.captureSummary = { slices = entry.data.capture.slices, durationMs = entry.data.capture.durationMs,
            workMs = entry.data.capture.workMs, maxSliceMs = entry.data.capture.maxSliceMs,
            clockValid = entry.data.capture.clockValid }
        self:_emit(event, emitted)
    else self:_emit(event, entry.data) end
end

function Job:note_scheduler_callback(sequence, frame_count, timing_info)
    local metrics = self.receipt.metrics
    if sequence ~= self.lastCallbackSequence then
        metrics.schedulerCallbacks = metrics.schedulerCallbacks + 1
        metrics.firstCallbackSequence = metrics.firstCallbackSequence or sequence
        metrics.lastCallbackSequence = sequence
        self.lastCallbackSequence = sequence
    end
    if finite(frame_count) and frame_count >= 0 then
        metrics.firstFrameCount = metrics.firstFrameCount or frame_count
        metrics.lastFrameCount = frame_count
        metrics.observedFrameSpan = frame_count - metrics.firstFrameCount
    end
    self.receipt.scheduler = { callbackSequence = sequence, frameCount = frame_count, timing = copy(timing_info or {}) }
end

function Job:get_receipt()
    if self.child then
        local active = self.child:get_receipt()
        if active and active.attempted then self.receipt.attempted = true end
        self.receipt.activeRecipe = active and compact_receipt(active) or nil
    end
    return self.receipt
end

local function progress(self)
    local result = { state = self.state, phase = self.phase, processed = self.processed, total = self.total,
        completedRecipes = self.receipt.counts.processed, totalRecipes = self.receipt.counts.eligible,
        error = self.error, metrics = self.receipt.metrics, defer = self.worker_defer == true }
    if self.state ~= "running" then
        result.receipt = self:get_receipt()
        if self.state == "done" then result.model = self.model end
    elseif self.phase == "preflight" then
        result.phase = self.collectPhase or "preflight"
        result.presentation = self.collectPresentation
    end
    return result
end

function Job:is_preparing()
    return self.state == "running" and (self.phase == "preflight" or self.phase == "bulk_prepare")
end

function Job:_guard()
    assert(self.runtimeConfig.ship_radius_cm == self.request.radiusCm, "Bulk learning radius changed")
    assert(self.request.gameBuild == self.config.reference_build, "Bulk learning game build is unverified")
    local token, err = collection.context()
    assert(token and token == self.request.contextToken, err or "Bulk learning player/world context changed")
end

function Job:_finish(outcome, reason)
    if self.collector then pcall(function() self.collector:cancel(reason or outcome) end) end
    if self.analysisJob then self.analysisJob:cancel(reason or outcome) end
    self.analysisJob, self.candidateRows = nil, nil
    self.state, self.phase, self.error = "done", "done", reason
    self.receipt.outcome, self.receipt.reason = outcome, reason
    self.receipt.partial = outcome ~= "completed" and outcome ~= "none_available" and self.receipt.counts.processed > 0
    self.receipt.remaining = math.max(0, self.receipt.counts.eligible - self.receipt.counts.processed)
    self.receipt.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
    if self.model then
        self.model.recipeUpdate = { kind = "learn_all", finishedUtc = self.receipt.finishedUtc,
            learned = self.receipt.counts.learned, alreadyKnown = self.receipt.counts.already_known,
            semantics = "Recipe rows include individual postchecks; physical counts retain the initial interval capture" }
    end
    local finished = self:_now_ms()
    if finished and self.startedMs then
        self.receipt.durationMs = finished - self.startedMs
        if self.receipt.durationMs > 0 then
            self.receipt.metrics.observedWorkCallbacksPerSecond = self.receipt.metrics.schedulerCallbacks * 1000 / self.receipt.durationMs
            self.receipt.metrics.frequencySemantics = "Bulk work callbacks divided by action elapsed time; excludes callbacks that did not reach batch work, not engine FPS"
        end
    end
    self:_event("finished", { outcome = outcome, reason = reason, counts = self.receipt.counts,
        attempted = self.receipt.attempted, remaining = self.receipt.remaining, partial = self.receipt.partial })
    self.collector, self.candidates, self.seen = nil, nil, nil
end

function Job:_record_child(result, suppress_finish)
    local receipt = result.receipt or self.child:get_receipt()
    local outcome = receipt and receipt.outcome or "unverified"
    local candidate = self.candidates[self.candidateIndex]
    if receipt and receipt.attempted then self.receipt.attempted = true end
    local counts = self.receipt.counts
    counts.processed = counts.processed + 1
    if counts[outcome] ~= nil then counts[outcome] = counts[outcome] + 1
    else counts.rejected = counts.rejected + 1 end
    self.receipt.results[#self.receipt.results + 1] = { itemPath = candidate.itemPath,
        displayName = candidate.displayName, outcome = outcome, attempted = receipt and receipt.attempted == true,
        receipt = compact_receipt(receipt) }
    self.receipt.activeRecipe = nil
    if result.model then self.model = result.model end
    self:_event("recipe_finished", { itemPath = candidate.itemPath, displayName = candidate.displayName,
        outcome = outcome, attempted = receipt and receipt.attempted == true, completed = counts.processed,
        total = counts.eligible, reason = receipt and receipt.reason })
    self.child, self.childProcessed, self.childTotal = nil, nil, nil
    self.candidateIndex = self.candidateIndex + 1
    if suppress_finish then return end
    if outcome == "unverified" then
        self:_finish("stopped_unverified", "A recipe outcome is uncertain; later recipes were not attempted")
    elseif self.candidateIndex > #self.candidates then self:_finish("completed")
    else self.phase = "bulk_start_recipe" end
end

function Job:cancel(reason)
    if self.state ~= "running" then return progress(self) end
    if self.collector then pcall(function() self.collector:cancel(reason) end) end
    if self.child then
        local cancelled = self.child:cancel(reason or "Bulk learning cancelled")
        self:_record_child(cancelled, true)
    end
    self:_finish("cancelled", reason or "Bulk learning cancelled")
    -- Completed grants remain recorded. Cancel does not roll them back or expose
    -- a partially updated model as a new full collection.
    self.state, self.phase = "cancelled", "cancelled"
    return progress(self)
end

function Job:_prepare_row()
    local row = self.model.rows[self.rowIndex]
    if row then
        if paint_view.learn_eligible(self.model, row)
            and row.runtimeRef and type(row.runtimeRef.name) == "string" and type(row.runtimeRef.id) == "string"
            and type(row.runtimeRef.className) == "string" and not self.seen[row.id] then
            self.seen[row.id] = true
            self.candidates[#self.candidates + 1] = { itemPath = row.id, runtimeRef = copy(row.runtimeRef),
                contextToken = self.request.contextToken, radiusCm = self.request.radiusCm,
                displayName = row.displayName, displayedCount = paint_view.physical_count(self.model, row),
                gameBuild = self.request.gameBuild }
        end
        self.rowIndex = self.rowIndex + 1
        return
    end
    self.receipt.counts.eligible = #self.candidates
    self.total = self.total + #self.candidates * 3
    self:_event("selection_complete", { scannedRows = #self.model.rows, eligible = #self.candidates,
        selectionScope = self.receipt.selectionScope })
    if #self.candidates == 0 then self:_finish("none_available")
    else self.candidateIndex, self.phase = 1, "bulk_start_recipe" end
end

function Job:_unit(budget)
    if self.phase == "preflight" then
        local result = self.collector:step(budget)
        self.worker_defer, self.collectPresentation = result.defer == true, result.presentation
        self.processed, self.total = result.processed or self.processed, result.total or self.total
        if result.state == "running" then self.collectPhase = result.phase; return true end
        if result.state ~= "done" then self:cancel(result.error or "Bulk preflight did not finish"); return true end
        local model = result.model
        assert(model and model.status == "captured" and model.contextToken == self.request.contextToken
            and model.radiusCm == self.request.radiusCm, "Bulk preflight context/radius unavailable")
        assert(model.stockComplete == true, "Bulk physical stock is incomplete")
        assert(type(model.rows) == "table" and #model.rows <= self.config.max_catalog, "Bulk catalog exceeds its limit")
        self.model, self.collector = model, nil
        self.receipt.preflight = { capture = model.capture, stockComplete = model.stockComplete,
            catalogComplete = model.catalogComplete, recipeCatalogComplete = model.recipeCatalogComplete,
            sourceCount = model.sourceCount, boxPhysicalCount = model.physicalCount,
            backpackPhysicalCount = model.backpackPhysicalCount, physicalCount = model.combinedPhysicalCount,
            catalogScope = model.catalogScope, countScope = model.combinedCountScope, boxCountScope = model.countScope }
        self:_event("preflight_complete", self.receipt.preflight)
        self.rowIndex, self.phase = 1, "bulk_prepare"
        -- Eligibility is a bounded plain-data predicate. Build one candidate
        -- per admitted unit directly; copying/encoding the entire catalog to a
        -- worker costs more work and callbacks than this selection itself.
        self.total = self.processed + #model.rows + 1
        return true
    elseif self.phase == "bulk_prepare" then self:_prepare_row()
    elseif self.phase == "bulk_start_recipe" then
        local candidate = self.candidates[self.candidateIndex]
        self:_event("recipe_started", { itemPath = candidate.itemPath, displayName = candidate.displayName,
            index = self.candidateIndex, total = #self.candidates })
        local child_options = { clock_ms = self.options.clock_ms, clock_source = self.options.clock_source,
            clock_validation = self.options.clock_validation, runtime_config = self.runtimeConfig }
        self.child = learning.begin_from_capture(self.config, candidate, self.model, function(event, data)
            local detail = event == "preflight_complete" and compact_preflight(data) or copy(data)
            self:_emit("recipe_" .. event, { itemPath = candidate.itemPath, index = self.candidateIndex,
                total = #self.candidates, detail = detail })
        end, child_options)
        self.childProcessed, self.childTotal, self.phase = 0, 2, "bulk_recipe"
    elseif self.phase == "bulk_recipe" then
        local result = self.child:step(budget)
        local child_processed, child_total = result.processed or self.childProcessed, result.total or self.childTotal
        self.processed = self.processed + math.max(0, child_processed - self.childProcessed)
        self.total = self.total + child_total - self.childTotal
        self.childProcessed, self.childTotal = child_processed, child_total
        if result.state ~= "running" then self:_record_child(result) end
        return true
    else error("Unknown bulk learning phase: " .. tostring(self.phase)) end
    return false
end

function Job:step(budget)
    if self.state ~= "running" then return progress(self) end
    budget = budget or { used = false, expired = function(self) return self.used end,
        consume = function(self) self.used = true end }
    assert(type(budget.expired) == "function" and type(budget.consume) == "function", "Bulk work budget unavailable")
    if budget:expired() then return progress(self) end
    self.worker_defer = false
    local started = self:_now_ms()
    local delegated = self.phase == "preflight" or self.phase == "bulk_recipe"
    local ok, err = pcall(function()
        self:_guard()
        delegated = self:_unit(budget)
    end)
    if not delegated then budget:consume(1); self.processed = self.processed + 1 end
    local metrics, finished = self.receipt.metrics, self:_now_ms()
    metrics.workSteps, metrics.slices = metrics.workSteps + 1, metrics.slices + 1
    if started and finished and metrics.clockValid then
        local elapsed = finished - started
        metrics.workMs, metrics.maxSliceMs = metrics.workMs + elapsed, math.max(metrics.maxSliceMs, elapsed)
    end
    if type(budget.metrics) == "function" then
        local observed = budget:metrics()
        metrics.budgetMs, metrics.maxUnits = observed.budgetMs, observed.maxUnits
        metrics.budgetClockSource = observed.clockSource or observed.source
        if observed.overBudget then metrics.overruns = metrics.overruns + 1 end
    end
    if not ok then
        if self.child then
            local cancelled = self.child:cancel(tostring(err))
            self:_record_child(cancelled, true)
        end
        if self.state == "running" then self:_finish(self.receipt.attempted and "stopped_error" or "rejected", tostring(err)) end
    end
    if self.state ~= "running" then self.total = self.processed end
    return progress(self)
end

function M.begin(config, request, emit, options)
    assert(type(request) == "table" and type(request.contextToken) == "string"
        and type(request.radiusCm) == "number", "Bulk learning request unavailable")
    local frozen = {}
    for key, value in pairs(config) do
        if type(value) == "number" or type(value) == "string" or type(value) == "boolean" then frozen[key] = value end
    end
    frozen.max_catalog = frozen.max_catalog or 128
    local request_copy = { contextToken = request.contextToken, radiusCm = request.radiusCm,
        gameBuild = type(request.gameBuild) == "string" and request.gameBuild or nil,
        displayedEligibleCount = finite(request.displayedEligibleCount) and request.displayedEligibleCount or nil }
    options = options or {}
    local frozen_options = { clock_ms = options.clock_ms, clock_source = options.clock_source,
        clock_validation = copy(options.clock_validation) }
    local source = options.clock_source or "os.clock; fallback observation, not an engine profiler"
    local receipt = { schemaVersion = 1, readOnly = false, physicalPolicy = "keep", threshold = 4,
        attempted = false, outcome = "pending", request = request_copy, startedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        clockSource = source, clockValidation = copy(options.clock_validation), saveRequestedByMod = false,
        counts = { eligible = 0, processed = 0, learned = 0, already_known = 0, rejected = 0, unverified = 0, cancelled = 0 },
        results = json.array(), events = json.array(),
        selectionScope = "Known eligible rows in one completed interval capture; every native write requires fresh live witness/membership proof",
        semantics = "Explicit bulk request; completed grants persist in memory when later work is cancelled; no resource consumption, rollback or save suppression",
        metrics = { slices = 0, workSteps = 0, schedulerCallbacks = 0, clockValid = true,
            sliceSemantics = "One bounded batch step; schedulerCallbacks separately counts distinct caller pumps",
            clockSource = source, workMs = 0, maxSliceMs = 0, overruns = 0 } }
    local self = setmetatable({ state = "running", phase = "preflight", processed = 0, total = 0,
        config = frozen, runtimeConfig = config, request = request_copy,
        collector = collection.begin(config, { catalog = options.catalog, presentation = "paints" }),
        candidates = json.array(), seen = {}, options = frozen_options, emit = emit, receipt = receipt,
        clock = options.clock_ms or function() return os.clock() * 1000 end }, Job)
    self.startedMs = self:_now_ms()
    self:_event("requested", request_copy)
    return self
end

return M
