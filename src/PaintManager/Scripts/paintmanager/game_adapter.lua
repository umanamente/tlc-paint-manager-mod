-- Only read APIs and metadata. No setters, inventory mutation, hooks or spawning.
local safe = require("paintmanager.safe")
local reflection = require("paintmanager.reflection")
local json = require("paintmanager.json")
local paint_state = require("paintmanager.paint_state")
local paint_payload = require("paintmanager.paint_payload")
local ship_scope = require("paintmanager.ship_scope")
local M = {}

local boxes = {
    ["/Game/Blueprints/Interactives/Containers/BP_Inventory_PlayerBox.BP_Inventory_PlayerBox_C"] = true,
    ["/Game/Blueprints/Interactives/Containers/BP_Inventory_PlayerBox_Large.BP_Inventory_PlayerBox_Large_C"] = true,
    ["/Game/Blueprints/Interactives/Containers/BP_Inventory_PlayerBox_Small.BP_Inventory_PlayerBox_Small_C"] = true,
}

local function all_of(class_name)
    local ok, objects = pcall(function() return FindAllOf(class_name) end)
    if not ok then return {}, tostring(objects) end
    if objects == nil then return {} end
    if type(objects) ~= "table" then return {}, "Unexpected FindAllOf result for " .. class_name end
    return objects
end

local function location(actor)
    local ok, value = pcall(function() return actor:K2_GetActorLocation() end)
    if ok then return value end
    return nil
end

local function read_slot(key, wrapped, defer_analysis)
    local entry = safe.unwrap(wrapped)
    if entry == nil then return nil, "nil inventory entry" end
    local data = safe.get(entry, "Data")
    local count = data and safe.get(data, "ItemCount")
    if type(count) ~= "number" then return nil, "unreadable ItemCount" end
    if count ~= count or math.abs(count) == math.huge or count < 0 or count % 1 ~= 0 then
        return nil, "invalid ItemCount (expected a nonnegative integer)"
    end
    local item = safe.unwrap(safe.get(entry, "Item"))
    local asset_name = safe.text(safe.get(entry, "AssetName"))
    local asset_type = safe.get(entry, "AssetType")
    local path = safe.name(item)
    local class_path = safe.name(safe.class(item))
    local row = {
        slot = safe.text(safe.unwrap(key)) or "<unreadable>",
        itemCount = count,
        assetName = asset_name,
        assetType = asset_type and safe.text(safe.get(asset_type, "Name")) or nil,
        itemPath = path,
        itemClass = class_path,
        itemResolved = safe.valid(item),
    }
    if not defer_analysis then row.paintCandidateReason = safe.paint_hint(asset_name, path, class_path) end
    return row
end

local function read_inventory(component, role, owner, distance, config)
    local result = {
        component = safe.name(component), componentClass = safe.name(safe.class(component)),
        role = role, owner = safe.name(owner), ownerClass = safe.name(safe.class(owner)),
        distanceCm = distance, slots = json.array(), errors = json.array(),
        complete = true,
    }
    local visited = 0
    local ok, err = pcall(function()
        component.Items:ForEach(function(key, wrapped)
            visited = visited + 1
            -- The pinned iterator can stop with true but has no resume cursor.
            if visited > config.max_slots then
                result.complete = false
                result.slotsTruncated = true
                return true
            end
            local row_ok, row, row_error = pcall(read_slot, key, wrapped, config.defer_inventory_analysis)
            if row_ok and row then
                if role == "preset_source" and row.itemCount > 0 and row.itemResolved then
                    local entry = safe.unwrap(wrapped)
                    local item = safe.unwrap(entry.Item)
                    local known, fingerprint, plain = pcall(function()
                        if item:IsA("/Script/Voyage.VoyagePaintItem") then return paint_payload(entry.Data) end
                    end)
                    if known then row.fingerprint, row.plainStack = fingerprint, plain end
                end
                result.slots[#result.slots + 1] = row
            else
                result.complete = false
                if #result.errors < 8 then
                    result.errors[#result.errors + 1] = row_ok and row_error or tostring(row)
                end
            end
        end)
    end)
    if not ok then
        result.complete = false
        result.errors[#result.errors + 1] = tostring(err)
    end
    result.slotsVisited = visited
    if config.defer_inventory_analysis then result._analysisInventory = true end
    if not config.defer_inventory_analysis then table.sort(result.slots, function(a, b) return a.slot < b.slot end) end
    return result
end

-- Runtime jobs retain only plain identities/results between game-thread calls.
local Job = {}
Job.__index = Job

local function object_ref(object)
    if not safe.valid(object) then return nil end
    local name = safe.name(object)
    local ok, address = pcall(function() return object:GetAddress() end)
    local class_name = safe.name(safe.class(object))
    if not name or not class_name or not ok or (type(address) ~= "number" and type(address) ~= "string") then return nil end
    return { name = name, id = name .. "@" .. tostring(address), className = class_name }
end

local function resolve(ref)
    if not ref then return nil, "Runtime descriptor unavailable" end
    local path = ref.name:match("^[^ ]+ (/.+)$") or ref.name
    local ok, object = pcall(function() return StaticFindObject(path) end)
    local actual = ok and object_ref(object) or nil
    if not actual or actual.id ~= ref.id or actual.className ~= ref.className then
        return nil, "Runtime identity/class unavailable or changed: " .. ref.name
    end
    return object
end

local function point(actor)
    local value = location(actor)
    if safe.distance_cm(value, value) == nil then return nil end
    return { X = value.X, Y = value.Y, Z = value.Z }
end

local function current()
    local ok, controller = pcall(function() return FindFirstOf("VoyagePlayerController") end)
    local pawn = ok and safe.valid(controller) and safe.get(controller, "Pawn") or nil
    if not safe.valid(pawn) then return nil, "No valid current player pawn" end
    local world_ok, world = pcall(function() return controller:GetWorld() end)
    local pawn_world_ok, pawn_world = pcall(function() return pawn:GetWorld() end)
    local pc_ref, pawn_ref = object_ref(controller), object_ref(pawn)
    local world_ref, pawn_world_ref = world_ok and object_ref(world), pawn_world_ok and object_ref(pawn_world)
    if not pc_ref or not pawn_ref or not world_ref or not pawn_world_ref or pawn_world_ref.id ~= world_ref.id then
        return nil, "Player world/runtime identities unavailable or changing"
    end
    return { controller = controller, pawn = pawn, world = world, controllerRef = pc_ref,
        pawnRef = pawn_ref, worldId = world_ref.id,
        token = world_ref.id .. "|" .. pc_ref.id .. "|" .. pawn_ref.id }
end

local function clock_ms()
    local ok, value = pcall(os.clock)
    if ok and type(value) == "number" and value == value and value >= 0 and value < math.huge then return value * 1000 end
end

local function new_report(config)
    return {
        scope = { radiusCm = config.ship_radius_cm, containers = "three exact stock PlayerBox classes; loaded only" },
        status = "no_player", inventories = json.array(), catalog = json.array(),
        observations = json.array(), warnings = json.array(),
        paintCandidates = { backpack = {}, nearbyContainers = {} }, complete = true,
        classification = "Paint candidates are name/path/class heuristics, not verified ownership or unlock eligibility.",
        capture = { atomic = false, startedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
            semantics = "Completed bounded reads over an interval; not an atomic inventory/world snapshot",
            clock = "os.clock; platform-dependent, not an engine frame-time measurement",
            slices = 0, workUnits = 0, uninterruptibleStages = json.array() },
    }
end

function M.begin(config, execution)
    local frozen = {}
    for key, value in pairs(config) do if type(value) ~= "table" and type(value) ~= "userdata" then frozen[key] = value end end
    local background = require("paintmanager.background")
    local use_analysis = not (execution and execution.background == false)
        and type(background.supports) == "function" and background.supports("analysis_collection")
    frozen.defer_inventory_analysis = use_analysis or nil
    return setmetatable({ config = frozen, runtime_config = config, phase = "context", state = "running", useAnalysis = use_analysis,
        data = new_report(frozen), seen = {}, catalog_seen = {}, candidates = {}, index = 1,
        processed = 0, total = 10, started = clock_ms(), candidateBatch = use_analysis and json.array() or nil }, Job)
end

function Job:_warn(message)
    self.data.complete = false
    if #self.data.warnings < 32 then self.data.warnings[#self.data.warnings + 1] = message
    else self.data.warningsTruncated = true end
end

function Job:_atomic(operation, callback)
    local started = clock_ms()
    local result = table.pack(callback())
    local finished = clock_ms()
    local duration = started and finished and finished >= started and finished - started or nil
    self.data.capture.uninterruptibleStages[#self.data.capture.uninterruptibleStages + 1] = {
        phase = self.phase, operation = operation, elapsedMs = duration, clockValid = duration ~= nil,
        limitation = "Indivisible native read plus bounded descriptor/row copy; cannot preempt this call from Lua",
    }
    return table.unpack(result, 1, result.n)
end

function Job:_discover(class_name, scope)
    local refs, seen = {}, {}
    self:_atomic("FindAllOf(" .. class_name .. ") + primitive descriptor compaction", function()
        local objects, err = all_of(class_name)
        if err then self:_warn(err) end
        if scope == "catalog" then self.data.catalogComplete = err == nil end
        local maximum = self.config.max_discovery_objects or 100000
        for index, object in ipairs(objects) do
            if index > maximum then
                self:_warn("Diagnostic " .. scope .. " discovery descriptor limit reached")
                self.data[scope .. "DiscoveryTruncated"] = true
                if scope == "catalog" then self.data.catalogComplete = false end
                break
            end
            local ref = object_ref(object)
            if ref and not seen[ref.name] then
                refs[#refs + 1], seen[ref.name] = ref, true
            elseif not ref then
                self:_warn("Diagnostic " .. scope .. " runtime identity unavailable: " ..
                    tostring(safe.valid(object) and safe.name(object) or "<invalid>"))
                if scope == "catalog" then self.data.catalogComplete = false end
            end
        end
    end)
    self.refs, self.index = refs, 1
    self.total = self.total + #refs
end

function Job:_observe(ref)
    local object, err = resolve(ref)
    if not object then self:_warn(err); return end
    self:_atomic("Bounded reflected scalar observations", function()
        local fields, status = reflection.observations(object, self.config)
        if not status.complete then self.data.complete = false end
        self.data.observations[#self.data.observations + 1] = { object = safe.name(object), fields = fields, status = status }
    end)
end

function Job:_inventory(component, role, owner, distance, next_phase)
    if not safe.valid(component) then
        if role == "backpack" then
            self:_warn("Current pawn has no readable WeightInventory; no substitute character was selected.")
        end
        self.phase = next_phase
        return
    end
    local identity = safe.name(component)
    if not identity or self.seen[identity] then self.phase = next_phase; return end
    self.seen[identity] = true
    if #self.data.inventories >= self.config.max_sources then
        self.data.sourcesTruncated = true
        self:_warn("Diagnostic inventory source limit reached")
        self.phase = next_phase
        return
    end
    local inventory = self:_atomic("Bounded inventory Items.ForEach + plain row copy", function()
        return read_inventory(component, role, owner, distance, self.config)
    end)
    inventory.capturedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
    self.data.inventories[#self.data.inventories + 1] = inventory
    if not inventory.complete then self.data.complete = false end
    local totals = role == "backpack" and self.data.paintCandidates.backpack
        or role == "nearby_container" and self.data.paintCandidates.nearbyContainers or nil
    if totals and not self.useAnalysis then
        self.aggregate = { slots = inventory.slots, totals = totals, index = 1, next_phase = next_phase }
        self.phase = "aggregate"
        self.total = self.total + math.ceil(#inventory.slots / (self.config.aggregate_rows_per_unit or 32))
    else self.phase = next_phase end
end

function Job:_filter_source(ref, context)
    local component, err = resolve(ref)
    if not component then self:_warn(err); return end
    local ok, owner = pcall(function() return component:GetOwner() end)
    if not ok or not safe.valid(owner) then return end
    local owner_ref = object_ref(owner)
    local class_path = owner_ref and (owner_ref.className:match("^[^ ]+ (/.+)$") or owner_ref.className)
    if not boxes[class_path] then return end
    local world_ok, world = pcall(function() return owner:GetWorld() end)
    local world_ref = world_ok and object_ref(world)
    if not world_ref or world_ref.id ~= context.worldId then return end
    local scope, scope_error = ship_scope.current(self.runtime_config, self.data.stockScope)
    if not scope then self:_warn(scope_error or "Ship scan scope unavailable"); return end
    local distance = safe.distance_cm(point(owner), scope.center)
    if not distance or distance > scope.radiusCm then return end
    self.data.nearbyStockContainerCandidates = (self.data.nearbyStockContainerCandidates or 0) + 1
    local candidate = { componentRef = ref, ownerRef = owner_ref, distance = distance }
    if self.candidateBatch then self.candidateBatch[#self.candidateBatch + 1] = candidate; return end
    local at = #self.candidates + 1
    for index, other in ipairs(self.candidates) do
        if distance < other.distance or distance == other.distance and ref.name < other.componentRef.name then at = index; break end
    end
    if at <= self.config.max_sources then
        table.insert(self.candidates, at, candidate)
        if #self.candidates > self.config.max_sources then table.remove(self.candidates) end
    end
end

function Job:_finish_source_filter()
    self.refs, self.index, self.phase = nil, 1, "sources"
    self.data.nearbyStockContainerCandidates = self.data.nearbyStockContainerCandidates or 0
    if self.data.nearbyStockContainerCandidates + #self.data.inventories > self.config.max_sources then
        self.data.sourcesTruncated = true; self:_warn("Diagnostic inventory source limit reached")
    end
    self.total = self.total + #self.candidates
end

function Job:_queue_source_selection(finished)
    local child, err = require("paintmanager.background").begin_task("analysis_collection", {
        mode = "source_candidates", selected = json.array(self.candidates), candidates = self.candidateBatch,
        maximum = self.config.max_sources, identity = "ref" })
    assert(child, err or "Native diagnostic source selection unavailable")
    self.analysisJob, self.filterFinished, self.candidateBatch, self.phase = child, finished, json.array(), "source_filter_worker"
end

function Job:_read_source(candidate, context)
    local component, err = resolve(candidate.componentRef)
    local owner, owner_error = resolve(candidate.ownerRef)
    if not component or not owner then self:_warn(err or owner_error); return end
    local ok, actual_owner = pcall(function() return component:GetOwner() end)
    local actual_ref = ok and object_ref(actual_owner)
    local world_ok, world = pcall(function() return owner:GetWorld() end)
    local world_ref = world_ok and object_ref(world)
    local scope, scope_error = ship_scope.current(self.runtime_config, self.data.stockScope)
    if not scope then self:_warn(scope_error or "Ship scan scope unavailable"); return end
    local distance = safe.distance_cm(point(owner), scope.center)
    if not actual_ref or actual_ref.id ~= candidate.ownerRef.id or not world_ref or world_ref.id ~= context.worldId
        or not distance or distance > scope.radiusCm then
        self:_warn("Diagnostic source context/radius changed before read: " .. candidate.componentRef.name)
        return
    end
    self:_inventory(component, "nearby_container", owner, distance, "sources")
end

function Job:_catalog(ref)
    local item, err = resolve(ref)
    if not item then self.data.catalogComplete = false; self:_warn(err); return end
    local path, class = safe.name(item), safe.name(safe.class(item))
    local hint = safe.paint_hint(safe.short_name(item), path, class)
    if not hint or not path or self.catalog_seen[path] then return end
    self.catalog_seen[path] = true
    if #self.data.catalog >= self.config.max_catalog then
        self.data.catalogTruncated, self.data.catalogComplete = true, false
        self:_warn("Diagnostic paint catalog limit reached")
        return
    end
    self:_atomic("Bounded paint candidate reflected observations", function()
        local fields, status = reflection.observations(item, self.config)
        if not status.complete then self.data.complete = false end
        local row = { itemPath = path, itemClass = class, paintCandidateReason = hint, fields = fields, fieldStatus = status }
        if self.useAnalysis then self.data.catalog[#self.data.catalog + 1] = row
        else
            local at = #self.data.catalog + 1
            for index, other in ipairs(self.data.catalog) do if path < other.itemPath then at = index; break end end
            table.insert(self.data.catalog, at, row)
        end
    end)
end

function Job:_unit(context,budget)
    local phase = self.phase
    if phase == "context" then
        self.contextToken, self.worldId = context.token, context.worldId
        self.pawnRef, self.controllerRef = context.pawnRef, context.controllerRef
        self.origin = point(context.pawn)
        if not self.origin then self:_warn("Current pawn location unavailable"); self.phase = "finish"; return end
        self.data.status, self.data.contextToken = "captured", context.token
        self.data.context = { controller = safe.name(context.controller), pawn = safe.name(context.pawn),
            pawnClass = safe.name(safe.class(context.pawn)), world = safe.name(context.world) }
        self.data.capture.origin = self.origin
        local scope, scope_error = ship_scope.current(self.runtime_config)
        if not scope then self:_warn(scope_error or "Ship scan scope unavailable"); self.phase = "finish"; return end
        self.data.stockScope, self.data.scope.radiusCm = scope, scope.radiusCm
        self.data.scope.origin = "measured_ship_center"
        self.data.capture.characterOrigin, self.data.capture.stockOrigin = self.origin, scope.center
        self.phase = "observe_controller"
    elseif phase == "observe_controller" then self:_observe(self.controllerRef); self.phase = "observe_pawn"
    elseif phase == "observe_pawn" then self:_observe(self.pawnRef); self.phase = "backpack"
    elseif phase == "backpack" then self:_inventory(safe.get(context.pawn, "WeightInventory"), "backpack", context.pawn, 0, "vault")
    elseif phase == "vault" then self:_inventory(safe.get(context.pawn, "VaultInventory"), "recipe_library_not_stock", context.pawn, 0, "source_discovery")
    elseif phase == "aggregate" then
        local aggregate = self.aggregate
        local last = math.min(#aggregate.slots, aggregate.index + (self.config.aggregate_rows_per_unit or 32) - 1)
        for index = aggregate.index, last do
            local slot = aggregate.slots[index]
            if slot.paintCandidateReason and slot.itemCount > 0 and slot.assetName and slot.assetName ~= "None" and slot.assetName ~= "" then
                aggregate.totals[slot.assetName] = (aggregate.totals[slot.assetName] or 0) + slot.itemCount
            end
        end
        aggregate.index = last + 1
        if aggregate.index > #aggregate.slots then self.phase, self.aggregate = aggregate.next_phase, nil end
    elseif phase == "source_discovery" then self:_discover("VoyageInventoryWeightLimitedComponent", "source"); self.phase = "source_filter"
    elseif phase == "source_filter" then
        local ref = self.refs[self.index]
        if ref then self:_filter_source(ref, context); self.index = self.index + 1 end
        local finished = self.index > #self.refs
        if self.candidateBatch and (#self.candidateBatch >= require("paintmanager.processing").get("source_batch_records", 128)
            or finished and #self.candidateBatch > 0) then
            self:_queue_source_selection(finished)
        elseif finished then self:_finish_source_filter() end
    elseif phase == "source_filter_worker" then
        local progress = self.analysisJob:step_one(budget)
        self.worker_defer=progress.defer==true
        if progress.state == "running" then return end
        assert(progress.state == "done", progress.error or "Native diagnostic source selection stopped")
        self.candidates = assert(self.analysisJob:get_result(), "Native diagnostic sources missing")
        self.analysisJob = nil
        if self.filterFinished then self:_finish_source_filter() else self.phase = "source_filter" end
    elseif phase == "sources" then
        local candidate = self.candidates[self.index]
        if candidate then self.index = self.index + 1; self:_read_source(candidate, context)
        else self.candidates, self.phase = nil, "catalog_discovery" end
    elseif phase == "catalog_discovery" then self:_discover("VoyageItem", "catalog"); self.phase = "catalog_read"
    elseif phase == "catalog_read" then
        local ref = self.refs[self.index]
        if ref then self:_catalog(ref); self.index = self.index + 1
        else self.refs, self.phase = nil, "paint_begin" end
    elseif phase == "paint_begin" then
        local ok, job = pcall(paint_state.begin, self.pawnRef, self.config, read_inventory,
            { worldId = self.worldId, background = self.useAnalysis })
        if ok then self.paint_job, self.phase = job, "paint_capture"
        else
            self.data.paintState = { schemaVersion = 1, status = "error", complete = false, errors = json.array({ tostring(job) }) }
            self.data.complete, self.phase = false, "finish"
        end
    elseif phase == "paint_capture" then
        local stepped, progress = pcall(function()
            return self.paint_job.step_one and self.paint_job:step_one(budget) or self.paint_job:step()
        end)
        self.worker_defer=stepped and progress.defer==true
        if not stepped then progress = { state = "error", error = tostring(progress) } end
        if progress.state == "done" then
            self.data.paintState, self.paint_job = progress.data, nil
            if not self.data.paintState.complete then self.data.complete = false end
            self.phase = "finish"
        elseif progress.state == "error" or progress.state == "cancelled" then
            self.data.paintState = { schemaVersion = 1, status = "error", complete = false,
                errors = json.array({ tostring(progress.error or progress.reason or "Paint capture cancelled") }) }
            self.data.complete, self.paint_job, self.phase = false, nil, "finish"
        else self.total = math.max(self.total, self.processed + (progress.total or 1) - (progress.processed or 0) + 2) end
    elseif phase == "finish" then
        if self.useAnalysis then
            local frozen, capture = {}, {}
            for key, value in pairs(self.data) do if key ~= "capture" then frozen[key] = value end end
            for key, value in pairs(self.data.capture) do capture[key] = value end
            frozen.capture = capture
            local child, err = require("paintmanager.background").begin_task("analysis_collection", { mode = "snapshot", data = frozen })
            assert(child, err or "Native snapshot analysis unavailable")
            self.analysisJob, self.phase = child, "snapshot_worker"
            return
        end
        self.data.capture.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
        local finished = clock_ms()
        if self.started and finished and finished >= self.started then self.data.capture.durationMs = finished - self.started end
        self.state, self.phase = "done", "done"
    elseif phase == "snapshot_worker" then
        local progress = self.analysisJob:step_one(budget)
        self.worker_defer=progress.defer==true
        if progress.state == "running" then return end
        assert(progress.state == "done", progress.error or "Native snapshot analysis stopped")
        local capture = self.data.capture
        self.data = assert(self.analysisJob:get_result(), "Native snapshot analysis result missing")
        self.data.capture, self.analysisJob = capture, nil
        self.data.capture.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
        local finished = clock_ms()
        if self.started and finished and finished >= self.started then self.data.capture.durationMs = finished - self.started end
        self.data.capture.backgroundAnalysis = true
        self.state, self.phase = "done", "done"
    end
end

function Job:cancel(reason)
    if self.state ~= "running" then return end
    if self.paint_job then self.paint_job:cancel(reason) end
    if self.analysisJob then self.analysisJob:cancel(reason) end
    self.analysisJob = nil
    self.candidateBatch = nil
    self.state, self.phase, self.reason = "cancelled", "cancelled", reason
    self.data, self.refs, self.candidates, self.aggregate, self.paint_job = nil, nil, nil, nil, nil
end

function Job:_progress()
    return { state = self.state, phase = self.phase, processed = self.processed,
        total = math.max(self.processed, self.total), data = self.state == "done" and self.data or nil,
        error = self.error, reason = self.reason, defer=self.worker_defer==true }
end

function Job:step(budget)
    if self.state ~= "running" then return self:_progress() end
    local started = clock_ms()
    self.data.capture.slices = self.data.capture.slices + 1
    repeat
        if budget and budget:expired() then break end
        local context, err = current()
        if self.runtime_config.ship_radius_cm ~= self.config.ship_radius_cm then self:cancel("Diagnostic radius changed"); break end
        if not context then
            if self.phase == "context" then
                self:_warn((err or "No current player") .. ". Load a save, leave vehicle controls and try again.")
                self.phase, self.state = "done", "done"
                self.data.capture.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
            else self:cancel(err or "Diagnostic context unavailable") end
            break
        end
        if self.contextToken and context.token ~= self.contextToken then self:cancel("Diagnostic player/world context changed"); break end
        self.worker_defer=false
        local ok, error = pcall(function() self:_unit(context,budget) end)
        if not ok then
            if self.paint_job then pcall(function() self.paint_job:cancel("Diagnostic capture error") end) end
            if self.analysisJob then self.analysisJob:cancel("Diagnostic capture error"); self.analysisJob = nil end
            self.state, self.phase, self.error, self.data = "error", "error", tostring(error), nil
            self.refs, self.candidates, self.aggregate, self.paint_job = nil, nil, nil, nil
            break
        end
        self.processed = self.processed + 1
        if budget then budget:consume() end
        if self.worker_defer then break end
        if self.state == "done" then
            local after = current()
            if not after or after.token ~= self.contextToken then
                self.state = "running"; self:cancel("Diagnostic context changed during finalization")
            end
            break
        end
    until not budget
    local finished = clock_ms()
    if self.data then
        self.data.capture.workUnits = self.processed
        if started and finished and finished >= started then
            local elapsed = finished - started
            self.data.capture.workMs = (self.data.capture.workMs or 0) + elapsed
            self.data.capture.maxSliceMs = math.max(self.data.capture.maxSliceMs or 0, elapsed)
        end
    end
    return self:_progress()
end

-- Compatibility/offline wrapper; runtime controller advances begin()/step().
function M.snapshot(config)
    local job, progress = M.begin(config, { background = false })
    repeat progress = job:step() until progress.state ~= "running"
    if progress.state ~= "done" then error(progress.error or progress.reason or "Diagnostic capture failed") end
    return progress.data
end

-- Shared bounded decoder for the separately authorized preset workflow. This
-- function only reads; callers must establish their own current owner/context.
M.read_inventory = read_inventory
M.read_slot = read_slot
return M
