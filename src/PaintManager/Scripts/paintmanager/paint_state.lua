-- Direct reads of JMAP-confirmed fields only. This module never applies paint,
-- changes an inventory, or interprets installed counts as research progress.
local safe = require("paintmanager.safe")
local json = require("paintmanager.json")
local M = {}

local PAINT_CLASS = "/Script/Voyage.VoyagePaintInventoryComponent"
local BOAT_CLASS = "/Script/Voyage.VoyageBoatPawn"

local function runtime_ref(object)
    if not safe.valid(object) then return nil end
    local name, class_name = safe.name(object), safe.name(safe.class(object))
    local ok, address = pcall(function() return object:GetAddress() end)
    if not name or not class_name or not ok or (type(address) ~= "number" and type(address) ~= "string") then return nil end
    return { name = name, className = class_name, id = name .. "@" .. tostring(address) }
end

local function fail(record, message)
    if record._analysisEvents then
        record._analysisEvents[#record._analysisEvents + 1] = { kind = "error", message = message }
    end
    record.complete = false
    if record.status == "captured" then record.status = "incomplete" end
    if #record.errors < 8 then record.errors[#record.errors + 1] = message
    else record.errorsTruncated = true end
end

local function call(object, method, ...)
    local arguments = table.pack(...)
    local ok, value = pcall(function()
        return object[method](object, table.unpack(arguments, 1, arguments.n))
    end)
    if ok then return value end
    return nil, tostring(value)
end

local function int32(value)
    return type(value) == "number" and value == value and value % 1 == 0
        and value >= -2147483648 and value <= 2147483647
end

local function text_field(object, field, record)
    local value, err = safe.get(object, field)
    local kind = type(value)
    local text = kind ~= "number" and kind ~= "boolean" and safe.text(value) or nil
    if text == nil then fail(record, field .. " unreadable" .. (err and ": " .. err or "")) end
    return text or json.null
end

local function target_record(component, role, owner, distance)
    return {
        status = "captured", complete = true, role = role,
        owner = safe.name(owner) or json.null,
        ownerClass = safe.name(safe.class(owner)) or json.null,
        component = safe.name(component) or json.null,
        runtimeRef = runtime_ref(component), ownerRuntimeRef = runtime_ref(owner),
        distanceCm = distance,
        minimumItemsToUnlockSet = json.null, allowSetUnlock = json.null,
        paintSetName = json.null, paintSetDisplayName = json.null,
        inventory = json.null, slots = json.array(), unconfiguredItems = json.array(),
        errors = json.array(), slotConfigurationComplete = false,
    }
end

local function read_target(component, role, owner, distance, config, read_inventory, trusted_plain_inventory, defer_analysis)
    local result = target_record(component, role, owner, distance)
    if not safe.valid(component) then
        result.status = "missing"
        fail(result, "Paint inventory unavailable")
        return result
    end
    local is_paint, type_error = call(component, "IsA", PAINT_CLASS)
    if is_paint ~= true then
        result.status = "invalid"
        fail(result, "Component is not a verified paint inventory" .. (type_error and ": " .. type_error or ""))
        return result
    end
    local actual_owner, owner_error = call(component, "GetOwner")
    if not safe.valid(actual_owner) or safe.name(actual_owner) ~= safe.name(owner)
        or safe.name(owner) == nil then
        result.status = "invalid"
        fail(result, "Paint inventory owner mismatch/unavailable" .. (owner_error and ": " .. owner_error or ""))
        return result
    end

    local threshold, threshold_error = safe.get(component, "MinimumItemsToUnlockSet")
    if int32(threshold) then result.minimumItemsToUnlockSet = threshold
    else fail(result, "MinimumItemsToUnlockSet is not a readable int32" .. (threshold_error and ": " .. threshold_error or "")) end
    local enabled, enabled_error = safe.get(component, "bAllowSetUnlock")
    if type(enabled) == "boolean" then result.allowSetUnlock = enabled
    else fail(result, "bAllowSetUnlock is not a readable bool" .. (enabled_error and ": " .. enabled_error or "")) end
    result.paintSetName = text_field(component, "PaintSetName", result)
    result.paintSetDisplayName = text_field(component, "PaintSetDisplayName", result)

    local item_by_slot, inventory_complete = {}, false
    local inventory_config = config
    if defer_analysis or config.defer_inventory_analysis then
        inventory_config = {}
        for key, value in pairs(config) do inventory_config[key] = value end
        inventory_config.defer_inventory_analysis = defer_analysis == true or nil
    end
    local inventory_ok, inventory = pcall(read_inventory, component, role, owner, distance, inventory_config)
    if inventory_ok and type(inventory) == "table" and type(inventory.slots) == "table"
        and type(inventory.complete) == "boolean" then
        -- The caller's existing serializer owns item decoding. Validate its plain
        -- snapshot rather than ever copying a live wrapper into the new section.
        local serializable, serial_error = true, nil
        if not trusted_plain_inventory then serializable, serial_error = pcall(json.encode, inventory) end
        if serializable then
            result.inventory = inventory
            inventory_complete = inventory.complete
            if not inventory.complete then fail(result, "Installed inventory read is incomplete") end
            if not defer_analysis then for _, item in ipairs(inventory.slots) do
                if type(item) ~= "table" then
                    inventory_complete = false
                    fail(result, "Invalid installed inventory row")
                else
                    local index = type(item.slot) == "string" and tonumber(item.slot) or item.slot
                    if not int32(index) or not int32(item.itemCount) or item.itemCount < 0 then
                        inventory_complete = false
                        fail(result, "Invalid installed inventory slot/count")
                    elseif item_by_slot[index] ~= nil then
                        inventory_complete = false
                        fail(result, "Duplicate installed inventory slot " .. tostring(index))
                    else
                        item_by_slot[index] = item
                    end
                end
            end end
        else
            fail(result, "Installed inventory snapshot is not serializable: " .. tostring(serial_error))
        end
    else
        fail(result, "Installed inventory read failed: " .. tostring(inventory))
    end

    local configured, visited = {}, 0
    if defer_analysis then
        local errors = json.array()
        for index, message in ipairs(result.errors) do errors[index] = message end
        result._analysisPrefix = { errors = errors, status = result.status, complete = result.complete,
            errorsTruncated = result.errorsTruncated }
        result._analysisEvents = json.array()
    end
    result.slotConfigurationComplete = true
    local map_ok, map_error = pcall(function()
        component.PaintSlots:ForEach(function(key, wrapped)
            visited = visited + 1
            if visited > config.max_paint_slots then
                result.slotsTruncated = true
                result.slotConfigurationComplete = false
                fail(result, "Paint slot configuration limit reached")
                return true
            end
            local row_ok, row_error = pcall(function()
                local index = safe.unwrap(key)
                if not int32(index) then error("Paint slot key is not int32") end
                if configured[index] then error("Duplicate configured paint slot " .. tostring(index)) end
                local info = safe.unwrap(wrapped)
                if info == nil then error("Paint slot metadata unavailable") end
                local row = {
                    index = index, item = json.null, occupancy = "unknown",
                    displayName = text_field(info, "SlotDisplayName", result),
                    materialSlotName = text_field(info, "MaterialSlotName", result),
                    partName = text_field(info, "PartName", result),
                }
                if row.displayName == json.null or row.materialSlotName == json.null
                    or row.partName == json.null then
                    result.slotConfigurationComplete = false
                end
                configured[index] = true
                local item = item_by_slot[index]
                if defer_analysis then
                    result._analysisEvents[#result._analysisEvents + 1] = { kind = "slot", row = row }
                elseif item and item.itemCount > 0 then
                    row.item = item
                    row.occupancy = "occupied"
                    if type(item.assetName) ~= "string" or item.assetName == "" or item.assetName == "None" then
                        row.itemIdentityComplete = false
                        fail(result, "Installed item identity unavailable in slot " .. tostring(index))
                    end
                elseif inventory_complete then
                    row.occupancy = "empty"
                end
                result.slots[#result.slots + 1] = row
            end)
            if not row_ok then
                result.slotConfigurationComplete = false
                fail(result, "Paint slot metadata read failed: " .. tostring(row_error))
            end
        end)
    end)
    if not map_ok then
        result.slotConfigurationComplete = false
        fail(result, "PaintSlots read failed: " .. tostring(map_error))
    end
    result.paintSlotsVisited = visited
    if defer_analysis then
        result._analysisExpired = not safe.valid(component) or not safe.valid(owner) or nil
        return result
    end
    table.sort(result.slots, function(a, b) return a.index < b.index end)
    for index, item in pairs(item_by_slot) do
        if item.itemCount > 0 and not configured[index] then
            -- Unmatched means absent from the captured configuration; if that
            -- configuration is partial, it does not prove a permanently invalid slot.
            result.unconfiguredItems[#result.unconfiguredItems + 1] = item
            fail(result, "Installed item has no captured configuration for slot " .. tostring(index))
        end
    end
    table.sort(result.unconfiguredItems, function(a, b) return tonumber(a.slot) < tonumber(b.slot) end)
    if not safe.valid(component) or not safe.valid(owner) then
        fail(result, "Paint target expired during capture")
    end
    return result
end

-- Unknown lifecycle objects remain incomplete, with enough identity context to
-- distinguish a real disappearing actor from a default/archetype in a later dump.
-- A name prefix alone never establishes that an ownerless component is expected.
local function noninstance(component)
    if not safe.valid(component) or type(EObjectFlags) ~= "table" then return nil end
    for _, entry in ipairs({ { "RF_ClassDefaultObject", "class_default_object" }, { "RF_ArchetypeObject", "archetype_object" } }) do
        local flag = EObjectFlags[entry[1]]
        if type(flag) == "number" and flag > 0 and flag % 1 == 0 then
            local confirmed = call(component, "HasAnyFlags", flag)
            if confirmed == true then return entry[2], entry[1] end
        end
    end
end

local function exclude_noninstance(result, component)
    local classification, flag_name = noninstance(component)
    if not classification then return false end
    result.excludedNonInstanceCount = (result.excludedNonInstanceCount or 0) + 1
    result.excludedNonInstances = result.excludedNonInstances or json.array()
    if #result.excludedNonInstances < 16 then
        result.excludedNonInstances[#result.excludedNonInstances + 1] = {
            component = safe.name(component) or json.null, componentClass = safe.name(safe.class(component)) or json.null,
            classification = classification, confirmedFlag = flag_name, evidence = "Native UObject.HasAnyFlags returned true",
        }
    else result.excludedNonInstancesTruncated = true end
    return true
end

local function discovery_detail(component, owner, message)
    local valid = safe.valid(component)
    local outer, outer_error
    if valid then outer, outer_error = call(component, "GetOuter") end
    local ok, address = false, nil
    if valid then ok, address = pcall(function() return component:GetAddress() end) end
    return {
        component = valid and safe.name(component) or json.null,
        componentClass = safe.name(safe.class(component)) or json.null,
        address = ok and (type(address) == "number" or type(address) == "string") and tostring(address) or json.null,
        outer = safe.name(outer) or json.null, outerClass = safe.name(safe.class(outer)) or json.null,
        owner = safe.name(owner) or json.null, ownerClass = safe.name(safe.class(owner)) or json.null,
        lifecycle = { classification = "unclassified", validAtRead = safe.valid(component),
            ownerAvailable = safe.valid(owner), outerAvailable = safe.valid(outer),
            outerReadError = outer_error },
        error = message,
    }
end

local function discovery_failure(result, component, owner, message)
    local detail = discovery_detail(component, owner, message)
    result.discoveryErrors = result.discoveryErrors or json.array()
    if #result.discoveryErrors < 16 then result.discoveryErrors[#result.discoveryErrors + 1] = detail
    else result.discoveryErrorsTruncated = true end
    local function label(value) return value == json.null and "<unavailable>" or tostring(value) end
    fail(result, "Boat discovery read failed: " .. message .. " [component=" .. label(detail.component)
        .. "; class=" .. label(detail.componentClass) .. "; outer=" .. label(detail.outer)
        .. "; owner=" .. label(detail.owner) .. "; lifecycle=unclassified]")
end

local function descriptor(object)
    if not safe.valid(object) then return nil end
    local name, class_name = safe.name(object), safe.name(safe.class(object))
    local ok, address = pcall(function() return object:GetAddress() end)
    if not name or not class_name or not ok or (type(address) ~= "number" and type(address) ~= "string") then return nil end
    return { name = name, className = class_name, id = name .. "@" .. tostring(address) }
end

local function resolve(ref)
    if not ref then return nil, "Paint runtime descriptor unavailable" end
    local path = ref.name:match("^[^ ]+ (/.+)$") or ref.name
    local ok, object = pcall(function() return StaticFindObject(path) end)
    local actual = ok and descriptor(object) or nil
    if not actual or actual.id ~= ref.id or actual.className ~= ref.className then
        return nil, "Paint runtime identity/class unavailable or changed: " .. ref.name
    end
    return object
end

local function clock_ms()
    local ok, value = pcall(os.clock)
    if ok and type(value) == "number" and value == value and value >= 0 and value < math.huge then return value * 1000 end
end

local PaintJob = {}
PaintJob.__index = PaintJob

function M.begin(pawn_ref, config, read_inventory, scope)
    local limits = {}
    for key, value in pairs(config) do if type(value) ~= "table" and type(value) ~= "userdata" then limits[key] = value end end
    scope = scope or {}
    local background = require("paintmanager.background")
    local use_analysis = scope.background ~= false and type(background.supports) == "function"
        and background.supports("analysis_collection")
    return setmetatable({ state = "running", phase = "character", config = limits, useAnalysis = use_analysis,
        pawnRef = pawn_ref, read_inventory = read_inventory, worldId = scope.worldId,
        shipOwnerId = scope.shipOwnerId, refs = {}, candidates = {}, seen = {}, index = 1,
        processed = 0, total = 4,
        data = { schemaVersion = 1, complete = true, boats = json.array(), errors = json.array(),
            discoveryErrors = json.array(),
            scope = { loadedOnly = true, boatOwnership = "not established" },
            capture = { atomic = false, startedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
                semantics = "Installed paint reads over an interval; not an atomic world snapshot",
                uninterruptibleStages = json.array() } } }, PaintJob)
end

function PaintJob:_atomic(operation, callback)
    local before = clock_ms()
    local value = callback()
    local after = clock_ms()
    local elapsed = before and after and after >= before and after - before or nil
    self.data.capture.uninterruptibleStages[#self.data.capture.uninterruptibleStages + 1] = {
        phase = self.phase, operation = operation, elapsedMs = elapsed, clockValid = elapsed ~= nil,
        limitation = "Indivisible native read and bounded plain-data copy; no native iterator cursor",
    }
    return value
end

function PaintJob:_discover()
    self:_atomic("FindAllOf(VoyagePaintInventoryComponent) + primitive descriptors", function()
        local query_ok, components = pcall(function() return FindAllOf("VoyagePaintInventoryComponent") end)
        if not query_ok or components ~= nil and type(components) ~= "table" then
            fail(self.data, "Paint component query failed: " .. tostring(components)); return
        end
        for index, component in ipairs(components or {}) do
            if index > (self.config.max_discovery_objects or 100000) then
                self.data.discoveryTruncated = true
                fail(self.data, "Paint discovery descriptor limit reached")
                break
            end
            local ref = descriptor(component)
            if not ref then discovery_failure(self.data, component, nil, "Paint component runtime descriptor unavailable")
            elseif not self.seen[ref.name] then self.refs[#self.refs + 1] = ref; self.seen[ref.name] = true end
        end
    end)
    self.total, self.phase = self.total + #self.refs, "filter"
end

function PaintJob:_filter(ref)
    local component, err = resolve(ref)
    if not component then
        fail(self.data, "Boat discovery read failed: " .. err .. " [component=" .. ref.name .. "; class=" .. ref.className .. "]")
        return
    end
    local owner
    local ok, error = pcall(function()
        local paint_type, paint_error = call(component, "IsA", PAINT_CLASS)
        if paint_type ~= true then error("Unverified paint component type: " .. tostring(paint_error or paint_type)) end
        if exclude_noninstance(self.data, component) then return end
        owner = call(component, "GetOwner")
        if not safe.valid(owner) then error("Paint component owner unavailable") end
        local boat_type, boat_error = call(owner, "IsA", BOAT_CLASS)
        if boat_error or type(boat_type) ~= "boolean" then error("Boat type check failed: " .. tostring(boat_error)) end
        if not boat_type then return end
        local owner_ref = descriptor(owner)
        if not owner_ref then error("Boat runtime identity unavailable") end
        if self.shipOwnerId and owner_ref.id ~= self.shipOwnerId then return end
        local world = call(owner, "GetWorld")
        local world_ref = descriptor(world)
        if not world_ref then error("Boat world unavailable") end
        if world_ref.id ~= self.worldId then return end
        self.data.boatCandidatesInScope = (self.data.boatCandidatesInScope or 0) + 1
        local candidate = { componentRef = ref, ownerRef = owner_ref }
        local at = #self.candidates + 1
        for index, other in ipairs(self.candidates) do
            if ref.name < other.componentRef.name then at = index; break end
        end
        if at <= self.config.max_paint_boats then
            table.insert(self.candidates, at, candidate)
            if #self.candidates > self.config.max_paint_boats then table.remove(self.candidates) end
        end
    end)
    if not ok then discovery_failure(self.data, component, owner, tostring(error)) end
end

function PaintJob:_target(candidate)
    local component, component_error = resolve(candidate.componentRef)
    local owner, owner_error = resolve(candidate.ownerRef)
    if not component or not owner then fail(self.data, component_error or owner_error); return end
    local actual_owner = call(component, "GetOwner")
    local actual_ref, owner_world = descriptor(actual_owner), call(owner, "GetWorld")
    local world_ref = descriptor(owner_world)
    if not actual_ref or actual_ref.id ~= candidate.ownerRef.id or not world_ref or world_ref.id ~= self.worldId then
        discovery_failure(self.data, component, owner, "Boat owner or world changed before installed-paint read")
        return
    end
    local target = self:_atomic("One bounded installed inventory + PaintSlots capture and join", function()
        return read_target(component, "installed_boat_paint", owner, nil, self.config, self.read_inventory, true, self.useAnalysis)
    end)
    if self:_queue_analysis(target, "boat", candidate.componentRef.name) then return end
    self.data.boats[#self.data.boats + 1] = target
    if not target.complete then fail(self.data, "Boat paint state is incomplete: " .. candidate.componentRef.name) end
end

function PaintJob:_queue_analysis(target, role, identity)
    if not target._analysisPrefix then return false end
    local child, err = require("paintmanager.background").begin_task("analysis_collection", { mode = "paint_target", target = target })
    assert(child, err or "Native installed-paint analysis unavailable")
    self.analysisJob, self.analysisRole, self.analysisIdentity, self.phase = child, role, identity, "paint_worker"
    return true
end

function PaintJob:_character_scope(pawn)
    local world_ref = descriptor(call(pawn, "GetWorld"))
    self.worldId = self.worldId or world_ref and world_ref.id
    if not self.worldId then
        fail(self.data, "Boat scope unavailable: player world could not be read"); self.phase = "finish"
    else self.phase = "discovery" end
end

function PaintJob:_unit(pawn,budget)
    if self.phase == "character" then
        local component = safe.get(pawn, "PaintInventory")
        local ref = descriptor(component)
        if ref then self.seen[ref.name] = true end
        self.data.character = self:_atomic("One bounded character installed inventory + PaintSlots capture and join", function()
            return read_target(component, "installed_character_paint", pawn, 0, self.config, self.read_inventory, true, self.useAnalysis)
        end)
        if self:_queue_analysis(self.data.character, "character") then return end
        if not self.data.character.complete then fail(self.data, "Character paint state is incomplete") end
        self:_character_scope(pawn)
    elseif self.phase == "paint_worker" then
        local progress = self.analysisJob:step_one(budget)
        self.worker_defer=progress.defer==true
        if progress.state == "running" then return end
        assert(progress.state == "done", progress.error or "Native installed-paint analysis stopped")
        local target = assert(self.analysisJob:get_result(), "Native installed-paint result missing")
        self.analysisJob = nil
        if self.analysisRole == "character" then
            self.data.character = target
            if not target.complete then fail(self.data, "Character paint state is incomplete") end
            self:_character_scope(pawn)
        else
            self.data.boats[#self.data.boats + 1] = target
            if not target.complete then fail(self.data, "Boat paint state is incomplete: " .. self.analysisIdentity) end
            self.phase = "targets"
        end
    elseif self.phase == "discovery" then self:_discover()
    elseif self.phase == "filter" then
        local ref = self.refs[self.index]
        if ref then self.index = self.index + 1; self:_filter(ref)
        else
            self.refs, self.index, self.phase = nil, 1, "targets"
            self.data.boatCandidatesInScope = self.data.boatCandidatesInScope or 0
            if self.data.boatCandidatesInScope > self.config.max_paint_boats then
                self.data.boatsTruncated = true; fail(self.data, "Loaded boat paint inventory limit reached")
            end
            self.total = self.total + #self.candidates
        end
    elseif self.phase == "targets" then
        local candidate = self.candidates[self.index]
        if candidate then self.index = self.index + 1; self:_target(candidate)
        else self.candidates, self.phase = nil, "finish" end
    elseif self.phase == "finish" then
        self.data.capture.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
        self.state, self.phase = "done", "done"
    end
end

function PaintJob:cancel(reason)
    if self.state ~= "running" then return end
    if self.analysisJob then self.analysisJob:cancel(reason) end
    self.analysisJob = nil
    self.state, self.phase, self.reason = "cancelled", "cancelled", reason
    self.data, self.refs, self.candidates = nil, nil, nil
end

function PaintJob:step_one(budget)
    if self.state=="running" then
            self.worker_defer=false
            local pawn, err = resolve(self.pawnRef)
            local world_ref = pawn and descriptor(call(pawn, "GetWorld"))
            if not pawn or self.worldId and (not world_ref or world_ref.id ~= self.worldId) then
                self:cancel(err or "Installed-paint player/world context changed")
            else
            local ok, error = pcall(function() self:_unit(pawn,budget) end)
            if not ok then
                if self.analysisJob then self.analysisJob:cancel(tostring(error)); self.analysisJob = nil end
                self.state, self.phase, self.error, self.data = "error", "error", tostring(error), nil
                self.refs, self.candidates = nil, nil
            else self.processed=self.processed+1 end
            end
    end
    return { state = self.state, phase = self.phase, processed = self.processed, total = math.max(self.processed, self.total),
        data = self.state == "done" and self.data or nil, error = self.error, reason = self.reason,
        defer=self.worker_defer==true }
end
function PaintJob:step(budget)
    if not budget then return self:step_one() end
    while self.state=="running" and not budget:expired() do
        self:step_one(budget);budget:consume()
        if self.worker_defer then break end
    end
    return {state=self.state,phase=self.phase,processed=self.processed,total=math.max(self.processed,self.total),
        data=self.state=="done" and self.data or nil,error=self.error,reason=self.reason,defer=self.worker_defer==true}
end

function M.capture(pawn, config, read_inventory)
    local result = {
        schemaVersion = 1, complete = true, boats = json.array(), errors = json.array(),
        scope = { loadedOnly = true, boatOwnership = "not established" },
    }
    local component = safe.valid(pawn) and safe.get(pawn, "PaintInventory") or nil
    result.character = read_target(component, "installed_character_paint", pawn, 0, config, read_inventory)
    if not result.character.complete then fail(result, "Character paint state is incomplete") end

    local world, world_error = call(pawn, "GetWorld")
    local world_name = safe.name(world)
    if world_name == nil then
        fail(result, "Boat scope unavailable: player world could not be read"
            .. (world_error and ": " .. world_error or ""))
        return result
    end

    local query_ok, components = pcall(function() return FindAllOf("VoyagePaintInventoryComponent") end)
    if not query_ok or (components ~= nil and type(components) ~= "table") then
        fail(result, "Paint component query failed: " .. tostring(components))
        return result
    end
    local seen, candidates = {}, {}
    local character_id = safe.name(component)
    if character_id then seen[character_id] = true end
    for _, candidate in ipairs(components or {}) do
        local candidate_ok, candidate_error = pcall(function()
            local identity = safe.name(candidate)
            if identity == nil then error("Paint component unavailable") end
            if seen[identity] then return end
            seen[identity] = true
            local paint_type, paint_error = call(candidate, "IsA", PAINT_CLASS)
            if paint_type ~= true then error("Unverified paint component type: " .. tostring(paint_error or paint_type)) end
            if exclude_noninstance(result, candidate) then return end
            local owner = call(candidate, "GetOwner")
            if not safe.valid(owner) then error("Paint component owner unavailable") end
            local boat_type, boat_error = call(owner, "IsA", BOAT_CLASS)
            if boat_error or type(boat_type) ~= "boolean" then error("Boat type check failed: " .. tostring(boat_error)) end
            if not boat_type then return end
            local boat_world = call(owner, "GetWorld")
            if safe.name(boat_world) == nil then error("Boat world unavailable") end
            if safe.name(boat_world) ~= world_name then return end
            candidates[#candidates + 1] = { component = candidate, owner = owner, identity = identity }
        end)
        if not candidate_ok then discovery_failure(result, candidate, call(candidate, "GetOwner"), tostring(candidate_error)) end
    end
    table.sort(candidates, function(a, b) return a.identity < b.identity end)
    result.boatCandidatesInScope = #candidates
    for index, candidate in ipairs(candidates) do
        if index > config.max_paint_boats then
            result.boatsTruncated = true
            fail(result, "Loaded boat paint inventory limit reached")
            break
        end
        local target = read_target(candidate.component, "installed_boat_paint", candidate.owner,
            nil, config, read_inventory)
        result.boats[#result.boats + 1] = target
        if not target.complete then fail(result, "Boat paint state is incomplete: " .. candidate.identity) end
    end
    return result
end

-- A direct bounded read for a freshly resolved, explicitly selected target.
-- It does not discover targets or authorize a later transfer.
function M.read_target(component, role, owner, distance, config, read_inventory)
    return read_target(component, role, owner, distance, config, read_inventory, true)
end
return M
