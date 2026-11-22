-- Explicit click-to-learn job. Physical cans are proof only and remain in place.
-- Only plain request/capture/receipt data survive a scheduler callback.
local collection = require("paintmanager.collection")
local paint_view = require("paintmanager.paint_view")
local ship_scope = require("paintmanager.ship_scope")
local safe = require("paintmanager.safe")
local json = require("paintmanager.json")
local M = {}
local Job = {}
Job.__index = Job
local attempts = {}

local function plain(value, depth, seen)
    local kind = type(value)
    if kind == "nil" or kind == "string" or kind == "boolean" then return value end
    if kind == "number" then
        return value == value and math.abs(value) < math.huge and value or tostring(value)
    end
    if kind ~= "table" then return "<non-plain " .. kind .. ">" end
    depth, seen = depth or 0, seen or {}
    if depth >= 20 or seen[value] then return "<nested or cyclic diagnostic>" end
    seen[value] = true
    local result = #value > 0 and json.array() or {}
    for key, item in pairs(value) do
        if type(key) == "string" or type(key) == "number" then result[key] = plain(item, depth + 1, seen) end
    end
    seen[value] = nil
    return result
end

local function descriptor(ref)
    assert(type(ref) == "table" and type(ref.name) == "string" and type(ref.id) == "string"
        and type(ref.className) == "string", "Paint runtime descriptor unavailable")
    return { name = ref.name, id = ref.id, className = ref.className }
end

local function identity(object)
    local name = safe.name(object)
    local ok, address = pcall(function() return object:GetAddress() end)
    if not name or not ok or (type(address) ~= "number" and type(address) ~= "string") then return nil end
    return name .. "@" .. tostring(address)
end

local function resolve(ref)
    assert(ref, "Native runtime descriptor unavailable")
    local path = ref.name:match("^[^ ]+ (/.+)$") or ref.name
    local object = StaticFindObject(path)
    assert(safe.valid(object) and identity(object) == ref.id
        and safe.name(safe.class(object)) == ref.className, "Native identity/class changed: " .. ref.name)
    return object
end

local function now_ms()
    local ok, value = pcall(os.clock)
    if not ok or type(value) ~= "number" or value ~= value or value < 0
        or math.abs(value) == math.huge or value * 1000 == math.huge then return nil end
    return value * 1000
end

function Job:_now_ms()
    if not self.clock_ms then return now_ms() end
    if self.clockBroken then return nil end
    local ok, value = pcall(self.clock_ms)
    if not ok or type(value) ~= "number" or value ~= value or value < 0
        or math.abs(value) == math.huge or (self.lastClockMs and value < self.lastClockMs) then
        self.clockBroken = true
        self.receipt.clockError = ok and "Selected action clock is invalid or moved backwards" or tostring(value)
        return nil
    end
    self.lastClockMs = value
    return value
end

function Job:_event(event, data)
    local values
    if event == "preflight_complete" then
        values = {}
        -- A completed collection never mutates its capture metadata again.
        -- Retain that immutable owned record instead of recursively copying
        -- every native-stage receipt twice on the game thread.
        for key, value in pairs(data or {}) do values[key] = key == "capture" and value or plain(value) end
    else values = plain(data or {}) end
    local item = { event = event, capturedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"), data = values }
    self.receipt.events[#self.receipt.events + 1] = item
    -- Logging failures must not interrupt post-mutation membership checks.
    if self.emit then
        local emitted = item.data
        if event == "preflight_complete" and emitted.capture then
            emitted = {}
            for key, value in pairs(item.data) do if key ~= "capture" then emitted[key] = value end end
            emitted.captureSummary = { slices = item.data.capture.slices, durationMs = item.data.capture.durationMs,
                workMs = item.data.capture.workMs, maxSliceMs = item.data.capture.maxSliceMs,
                clockValid = item.data.capture.clockValid }
        end
        local ok, err = pcall(self.emit, event, emitted)
        if not ok then
            self.receipt.loggingErrors = self.receipt.loggingErrors or json.array()
            self.receipt.loggingErrors[#self.receipt.loggingErrors + 1] = tostring(err)
        end
    end
end

function Job:get_receipt()
    return self.receipt
end

local function progress(self)
    local result = { state = self.state, phase = self.phase, processed = self.processed,
        total = self.total, error = self.error, metrics = self.receipt.metrics,
        defer = self.worker_defer == true }
    if self.state ~= "running" then
        result.receipt = self.receipt
        if self.state == "done" then result.model = self.model end
    elseif self.phase == "preflight" then
        result.phase = self.collectPhase or "preflight"
        result.presentation = self.collectPresentation
    end
    return result
end

-- Preparation may use a fresh post-input work allowance. The final native
-- witness/grant step must retain the controller's mutation admission policy.
function Job:is_preparing()
    return self.state == "running" and (self.phase == "preflight" or self.phase == "learn_prepare")
end

function Job:_finish(outcome, reason)
    if self.collector then pcall(function() self.collector:cancel(reason or outcome) end) end
    self.state, self.phase, self.error = "done", "done", reason
    self.receipt.outcome, self.receipt.reason = outcome, reason
    self.receipt.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
    local finished = self:_now_ms()
    if finished and self.startedMs and finished >= self.startedMs then self.receipt.durationMs = finished - self.startedMs end
    self:_event("finished", { outcome = outcome, attempted = self.receipt.attempted, reason = reason })
    self.collector = nil
end

function Job:cancel(reason)
    if self.state ~= "running" then return progress(self) end
    if self.collector then pcall(function() self.collector:cancel(reason) end) end
    -- AddItem is synchronous; there is no deferred mutation to cancel/replay.
    if self.receipt.attempted then
        self:_finish(self.receipt.afterHasItem == true and "learned" or "unverified", reason)
    else
        self:_finish("cancelled", reason or "Learning cancelled")
        self.state, self.phase, self.model = "cancelled", "cancelled", nil
    end
    return progress(self)
end

function Job:_guard()
    assert(type(self.config.reference_build) == "string" and self.request.gameBuild == self.config.reference_build,
        "Learning game build is unverified")
    assert(self.runtimeConfig.ship_radius_cm == self.request.radiusCm, "Learning radius changed")
    local token, err = collection.context()
    assert(token and token == self.request.contextToken, err or "Learning player/world context changed")
    if self.model and self.model.stockScope then
        local scope, reason = ship_scope.player_access(self.runtimeConfig, self.model.stockScope)
        assert(scope, reason or "Ship connection lost before learning")
    end
end

function Job:_prepare()
    local model = self.model
    assert(model and model.status == "captured" and model.contextToken == self.request.contextToken,
        "Fresh learning capture is unavailable or belongs to another context")
    assert(model.radiusCm == self.request.radiusCm, "Fresh learning radius changed")
    assert(type(model.rows) == "table" and #model.rows <= (self.config.max_catalog or 128),
        "Fresh paint catalog exceeds the processing limit")
    local row
    for _, candidate in ipairs(model.rows) do
        if candidate.id == self.request.itemPath then row = candidate; break end
    end
    self.receipt.preflight = { status = model.status, contextToken = model.contextToken,
        radiusCm = model.radiusCm, stockComplete = model.stockComplete, boxPhysicalCount = model.physicalCount,
        backpackPhysicalCount = model.backpackPhysicalCount, physicalCount = model.combinedPhysicalCount,
        sourceCount = model.sourceCount, includedSourceCount = model.includedSourceCount,
        recipeState = row and row.recipeState or "unknown", count = row and paint_view.physical_count(model, row),
        capture = not self.sharedCapture and model.capture or nil,
        captureReference = self.sharedCapture and "batch.preflight.capture" or nil }
    self:_event("preflight_complete", self.receipt.preflight)
    assert(row and row.runtimeRef and row.runtimeRef.id == self.request.runtimeRef.id
        and row.runtimeRef.name == self.request.runtimeRef.name
        and row.runtimeRef.className == self.request.runtimeRef.className, "Paint runtime identity changed")
    if row.recipeState == "unlocked" then self:_finish("already_known", "Fresh capture already reports the recipe unlocked"); return end
    assert(model.stockComplete == true and row.complete ~= false, "Fresh physical stock or target row is incomplete")
    assert(row.recipeState == "locked", "Fresh recipe membership is unknown")
    assert(paint_view.learn_eligible(model, row), "Fewer than four verified physical cans are available")
    self.phase = "learn_commit"
end

function Job:_fresh_membership(context)
    self:_guard()
    local item = resolve(self.request.runtimeRef)
    local vault = resolve(self.model.vaultRuntimeRef)
    assert(safe.valid(context.pawn)
        and identity(safe.get(context.pawn, "VaultInventory")) == self.model.vaultRuntimeRef.id,
        "Current recipe library changed")
    local value = vault:HasItem(item)
    assert(type(value) == "boolean", "HasItem did not return bool")
    return value, item, vault
end

function Job:_grant(context, item, vault, proof, verify_sources)
    self.receipt.proof = plain(proof)
    local before_ok, before, current_item, current_vault = pcall(function() return self:_fresh_membership(context) end)
    if not before_ok then
        self.receipt.beforeError = tostring(before)
        self:_event("membership_before", { state = "unknown", error = self.receipt.beforeError })
        self:_finish("rejected", self.receipt.beforeError)
        return
    end
    self.receipt.beforeHasItem = before
    self:_event("membership_before", { value = before })
    if before then
        self:_mark_unlocked()
        self:_finish("already_known", "Recipe was unlocked before the native call")
        return
    end
    self:_event("native_call_begin", { itemPath = self.request.itemPath, data = { ItemCount = 1, Timestamp = 0, Flags = 0 },
        allowStacking = true, notifyChanged = true, physicalPolicy = "keep" })
    -- The selected batch timing getter is also a native call. Sample BEFORE
    -- final admission so a reentrant clock callback cannot invalidate the last
    -- guard and still grant. The recorded interval includes this short guard.
    local call_started = self:_now_ms()
    self.receipt.callTimingScope = "Final identity/context/source admission plus native AddItem call"
    -- HasItem/logging/timing can reenter native code. Check once more, with no
    -- later native timing read between this guard and AddItem.
    verify_sources()
    self:_guard()
    assert(identity(safe.get(context.controller, "Pawn")) == identity(context.pawn),
        "Leave vehicle controls before learning a recipe")
    assert(identity(resolve(self.request.runtimeRef)) == identity(current_item)
        and identity(resolve(self.model.vaultRuntimeRef)) == identity(current_vault)
        and identity(safe.get(context.pawn, "VaultInventory")) == self.model.vaultRuntimeRef.id,
        "Paint or Vault changed after the membership query")
    -- A native inventory read can reenter another explicit action. The ledger
    -- must still be clear immediately before this action reserves its attempt.
    local previous = attempts[self.attemptKey]
    if previous then
        self.receipt.blockedByPreviousAttempt, self.receipt.previousAttempt = true, plain(previous)
        self:_finish("unverified", "A previous native attempt blocks retries until the player/world context changes")
        return
    end
    self.receipt.attempted = true
    local attempt = { attempted = true, outcome = "unverified", startedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ") }
    attempts[self.attemptKey] = attempt
    local call_ok, returned = pcall(function()
        return current_vault:AddItem(current_item, { ItemCount = 1, Timestamp = 0, Flags = 0 }, true, true)
    end)
    local call_finished = self:_now_ms()
    self.receipt.callClockValid = call_started ~= nil and call_finished ~= nil and call_finished >= call_started
    if self.receipt.callClockValid then self.receipt.callDurationMs = call_finished - call_started end
    self.receipt.callReturned, self.receipt.callReturnType = call_ok, type(returned)
    if call_ok then self.receipt.callReturn = plain(returned)
    else self.receipt.callException = tostring(returned) end
    self:_event("native_call_returned", { returned = call_ok, value = self.receipt.callReturn,
        valueType = self.receipt.callReturnType, exception = self.receipt.callException,
        elapsedMs = self.receipt.callDurationMs, clockValid = self.receipt.callClockValid })
    -- Always observe membership after an attempted call, including an exception.
    -- The returned slot (including zero/negative values) is diagnostic only.
    local after_ok, after = pcall(function() return self:_fresh_membership(context) end)
    if after_ok then self.receipt.afterHasItem = after else self.receipt.afterError = tostring(after) end
    self:_event("membership_after", { value = self.receipt.afterHasItem, state = after_ok and "known" or "unknown",
        error = self.receipt.afterError })
    local outcome = after_ok and after == true and "learned" or "unverified"
    -- A native call may synchronously trigger the proven load/context hook.
    -- Update this same-call plain record even if that hook retired its ledger;
    -- never restore a retired context or replace the real postcheck diagnostic.
    attempt.outcome = outcome
    if outcome == "learned" then self:_mark_unlocked() end
    self:_finish(outcome, outcome == "unverified" and "Native call attempted, but unlocked membership was not verified; no automatic retry" or nil)
end

function Job:_mark_unlocked()
    for _, row in ipairs(self.model and self.model.rows or {}) do
        if row.id == self.request.itemPath then row.recipeState = "unlocked"; return end
    end
end

function Job:_commit()
    -- An uncertain earlier grant cannot be retried. Reject before rereading
    -- inventories; refresh may independently observe the now-known recipe.
    local previous = attempts[self.attemptKey]
    if previous then
        self.receipt.blockedByPreviousAttempt, self.receipt.previousAttempt = true, plain(previous)
        self:_finish("unverified", "A previous native attempt blocks retries until the player/world context changes")
        return
    end
    local entered = false
    collection.with_recipe_proof(self.config, self.request, self.model, function(context, item, vault, proof, verify_sources)
        assert(not entered, "Learning callback was invoked more than once")
        entered = true
        self:_grant(context, item, vault, proof, verify_sources)
    end, function(event, data) self:_event(event, data) end, { cached = self.cachedLocations == true })
    assert(entered, "Learning proof returned without a grant callback")
end

function Job:step(budget)
    if self.state ~= "running" then return progress(self) end
    budget = budget or { used = false, expired = function(self) return self.used end,
        consume = function(self) self.used = true end }
    assert(type(budget.expired) == "function" and type(budget.consume) == "function", "Learning work budget unavailable")
    if budget:expired() then return progress(self) end
    self.worker_defer = false
    local started, phase = self:_now_ms(), self.phase
    local ok, err = pcall(function()
        self:_guard()
        if phase == "preflight" then
            local result = self.collector:step(budget)
            self.worker_defer, self.collectPresentation = result.defer == true, result.presentation
            self.processed, self.total = result.processed or self.processed, (result.total or self.total) + 2
            if result.state == "running" then self.collectPhase = result.phase; return end
            if result.state ~= "done" then
                self:cancel(result.error or "Fresh collection did not complete")
                return
            end
            self.model, self.collector, self.phase = result.model, nil, "learn_prepare"
        elseif phase == "learn_prepare" then self:_prepare()
        elseif phase == "learn_commit" then self:_commit()
        else error("Unknown learning phase: " .. tostring(phase)) end
    end)
    -- The collector charges its own shared units. Preparation and the bounded
    -- final proof/grant each charge exactly one additional unit.
    if phase ~= "preflight" then budget:consume(1); self.processed = self.processed + 1 end
    local finished, metrics = self:_now_ms(), self.receipt.metrics
    metrics.slices = metrics.slices + 1
    local elapsed
    if started and finished and finished >= started and metrics.clockValid then
        elapsed = finished - started
        metrics.workMs, metrics.maxSliceMs = metrics.workMs + elapsed, math.max(metrics.maxSliceMs, elapsed)
    else
        metrics.clockValid, metrics.workMs, metrics.maxSliceMs = false, nil, nil
    end
    if phase == "learn_commit" then
        metrics.uninterruptibleStages[#metrics.uninterruptibleStages + 1] = {
            phase = phase, elapsedMs = elapsed, clockValid = elapsed ~= nil, maxWitnesses = 4,
            operation = "Local backpack and up to four box witnesses; recheck contributing sources before one AddItem attempt",
            limitation = "Indivisible game-thread call; no hard frame deadline or inventory transaction" }
        self:_event("commit_timing", metrics.uninterruptibleStages[#metrics.uninterruptibleStages])
    end
    if type(budget.metrics) == "function" then
        local observed = budget:metrics()
        metrics.budgetMs, metrics.maxUnits = observed.budgetMs, observed.maxUnits
        if observed.overBudget then metrics.overruns = metrics.overruns + 1 end
    end
    if not ok then
        if self.state == "running" then self:_finish(self.receipt.attempted and "unverified" or "rejected", tostring(err))
        else self.receipt.postOutcomeError = tostring(err) end
    end
    local result = progress(self)
    if self.state == "running" and self.phase == "preflight" then result.phase = self.collectPhase or "preflight" end
    return result
end

local function begin_job(config, request, emit, capture, options)
    options = options or {}
    assert(type(request) == "table" and type(request.itemPath) == "string"
        and type(request.contextToken) == "string" and type(request.radiusCm) == "number", "Learning request unavailable")
    local ref = descriptor(request.runtimeRef)
    assert(ref.name == request.itemPath, "Learning paint path and runtime descriptor disagree")
    local copy = { itemPath = request.itemPath, runtimeRef = ref, contextToken = request.contextToken,
        radiusCm = request.radiusCm, displayName = type(request.displayName) == "string" and request.displayName or nil,
        displayedCount = type(request.displayedCount) == "number" and request.displayedCount or nil,
        gameBuild = type(request.gameBuild) == "string" and request.gameBuild or nil }
    local frozen = {}
    for key, value in pairs(config) do
        if type(value) == "number" or type(value) == "string" or type(value) == "boolean" then frozen[key] = value end
    end
    local started
    if options.clock_ms then
        local ok, value = pcall(options.clock_ms)
        if ok and type(value) == "number" and value == value and value >= 0 and math.abs(value) < math.huge then started = value end
    else started = now_ms() end
    local clock_source = options.clock_source or "os.clock; platform-dependent elapsed observation"
    local receipt = { schemaVersion = 1, readOnly = false, physicalPolicy = "keep", threshold = 4,
        attempted = false, outcome = "pending", request = plain(copy), startedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        events = json.array(), saveRequestedByMod = false, clockSource = clock_source,
        clockValidation = options.clock_validation,
        semantics = "Explicit one-click native recipe grant; no physical cans are consumed, no rollback or save suppression is provided",
        metrics = { slices = 0, workMs = started and 0 or nil, maxSliceMs = started and 0 or nil,
            clockValid = started ~= nil, clockSource = clock_source, overruns = 0, uninterruptibleStages = json.array() } }
    local self = setmetatable({ state = "running", phase = capture and "learn_prepare" or "preflight", processed = 0, total = 2,
        config = frozen, runtimeConfig = options.runtime_config or config, request = copy, model = capture,
        sharedCapture = capture ~= nil, cachedLocations = options.cached_locations == true,
        collector = not capture and collection.begin(config, { catalog = options.catalog, presentation = "paints" }) or nil,
        emit = emit,
        clock_ms = options.clock_ms, clockBroken = options.clock_ms ~= nil and started == nil, lastClockMs = started,
        receipt = receipt, startedMs = started, attemptKey = copy.contextToken .. "|" .. copy.itemPath }, Job)
    if capture then receipt.preflightSource = "Shared fresh batch interval capture; live witnesses are re-read before this action" end
    self:_event("requested", copy)
    return self
end

function M.begin(config, request, emit, options)
    return begin_job(config, request, emit, nil, options)
end

-- A batch may reuse its newly completed plain capture for eligibility. The
-- actual native write still requires this job's same-call live witness proof.
function M.begin_from_capture(config, request, capture, emit, options)
    assert(type(capture) == "table", "Shared batch capture unavailable")
    return begin_job(config, request, emit, capture, options)
end

-- Explicit single-paint action. The caller is already on the game thread;
-- complete preparation, live proof and at most one grant before returning.
-- Never construct a collector, submit a worker task or yield from this path.
function M.run_cached(config, request, capture, emit, options)
    options = options or {}
    local cached_options = {}
    for key, value in pairs(options) do cached_options[key] = value end
    cached_options.cached_locations = true
    -- A missing cache is still a terminal rejected receipt, never a request for
    -- the legacy global preflight or an automatic fallback search.
    local self = begin_job(config, request, emit, capture or {}, cached_options)
    self.receipt.preflightSource = "Last completed capture supplies box location hints; the local backpack and exact boxes require same-call live proof"
    self.receipt.execution = "synchronous_cached_locations"
    local started = self:_now_ms()
    local ok, err = pcall(function()
        self:_guard()
        self:_prepare()
        if self.state == "running" then self:_commit() end
    end)
    if not ok then
        self:_finish(self.receipt.attempted and "unverified" or "rejected", tostring(err))
        if not self.receipt.attempted and self.request.gameBuild == self.config.reference_build then
            self.receipt.reasonCode = "refresh_required"
        end
    end
    local finished, metrics = self:_now_ms(), self.receipt.metrics
    metrics.slices, self.processed, self.total = 1, 1, 1
    local elapsed = started and finished and finished >= started and finished - started or nil
    metrics.clockValid = elapsed ~= nil
    metrics.workMs, metrics.maxSliceMs = elapsed, elapsed
    metrics.uninterruptibleStages[#metrics.uninterruptibleStages + 1] = {
        phase = "cached_locations_commit", elapsedMs = elapsed, clockValid = elapsed ~= nil,
        maxWitnesses = config.max_sources,
        operation = "Current backpack and previously scanned boxes, early stop at four live cans, at most one native grant",
        limitation = "Explicit indivisible game-thread action; no hard frame deadline or inventory transaction" }
    return progress(self)
end

-- Called only when the controller confirms a world/pawn context reset. Refresh,
-- close and radius changes must not clear an uncertain native attempt.
function M.reset_context()
    attempts = {}
end

return M
