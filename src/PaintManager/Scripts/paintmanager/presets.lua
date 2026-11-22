-- Current paint capture and explicit selected-layout application. Persistent presets
-- contain logical compatibility/asset IDs only; runtime refs stay in interval views.
local safe = require("paintmanager.safe")
local json = require("paintmanager.json")
local collection = require("paintmanager.collection")
local adapter = require("paintmanager.game_adapter")
local paint_state = require("paintmanager.paint_state")
local supply = require("paintmanager.preset_supply")
local ship = require("paintmanager.ship_scope")
local t = require("paintmanager.i18n").text
local display_text = require("paintmanager.display_text")
local M = {}
local uncertain = {}
local PAINT = "/Script/Voyage.VoyagePaintItem"
local INVENTORY = "/Script/Voyage.VoyageInventoryWeightLimitedComponent"
-- UUID bytes must come from the native GUID generator, never Lua's PRNG or a
-- runtime object address. This boundary also accepts an injected test producer.
function M.new_id(produce)
    assert(type(produce) == "function", "Native UUID generation is unavailable")
    local value = produce()
    assert(type(value) == "string", "Native UUID result is not text")
    value = value:lower()
    if value:sub(1, 1) == "{" and value:sub(-1) == "}" then value = value:sub(2, -2) end
    if #value == 32 and value:match("^%x+$") then
        value = value:sub(1, 8) .. "-" .. value:sub(9, 12) .. "-" .. value:sub(13, 16)
            .. "-" .. value:sub(17, 20) .. "-" .. value:sub(21, 32)
    end
    assert(value:match("^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$")
        and value ~= "00000000-0000-0000-0000-000000000000", "Invalid native UUID")
    return value
end
function M.display_name(record, rows)
    local count = 0
    local name = display_text.fold(record.name)
    for _, candidate in ipairs(rows or {}) do
        if display_text.fold(candidate.name) == name then count = count + 1 end
    end
    if count > 1 then return record.name .. " [" .. record.id .. "]" end
    return record.name
end
local function clock_ms()
    local ok, value = pcall(os.clock)
    if ok and type(value) == "number" and value == value and value >= 0 and value < math.huge then return value * 1000 end
end

local function path(name) return type(name) == "string" and (name:match("^[^ ]+ (/.+)$") or name) or nil end
local function ref(object)
    if not safe.valid(object) then return nil end
    local name, class = safe.name(object), safe.name(safe.class(object))
    local ok, address = pcall(function() return object:GetAddress() end)
    if not name or not class or not ok or (type(address) ~= "number" and type(address) ~= "string") then return nil end
    return { name = name, className = class, id = name .. "@" .. tostring(address) }
end
local function resolve(expected)
    assert(type(expected) == "table", "Missing selected runtime identity")
    local object = StaticFindObject(path(expected.name))
    local actual = ref(object)
    assert(actual and actual.id == expected.id and actual.className == expected.className, "Selected runtime identity changed")
    return object
end

-- The stock bar binds to the component whose zones are actually being edited.
-- Keep only descriptors. The selected-target capture resolves both again.
function M.editor_target(bar)
    if not safe.valid(bar) or not bar:IsA("/Script/Voyage.VoyagePaintBarWidget") then return end
    local component = bar:GetSourceComponent()
    if not safe.valid(component) or not component:IsA("/Script/Voyage.VoyagePaintInventoryComponent") then return end
    local owner = component:GetOwner()
    local component_ref, owner_ref = ref(component), ref(owner)
    if not component_ref or not owner_ref then return end
    local kind
    if owner:IsA("/Script/Voyage.VoyageBoatPawn") then kind = "boat"
    elseif owner:IsA("/Script/Voyage.VoyageCharacter") then kind = "character"
    else return end
    return { id = component_ref.id, runtimeRef = component_ref, ownerRuntimeRef = owner_ref, kind = kind }
end
local function current()
    local token, err = collection.context()
    assert(token, err or "Current character unavailable")
    local pc = FindFirstOf("VoyagePlayerController")
    assert(safe.valid(pc) and pc:IsLocalController() == true, "Local player controller unavailable")
    local pawn = safe.get(pc, "Pawn")
    assert(safe.valid(pawn) and pawn:IsA("/Script/Voyage.VoyageCharacter") == true, "Current character unavailable")
    local world = ref(pawn:GetWorld())
    local pawn_ref = ref(pawn)
    assert(world and pawn_ref, "Current world/character identity unavailable")
    return { token = token, pc = pc, pawn = pawn, pawnRef = pawn_ref, worldId = world.id, world = pawn:GetWorld() }
end
local layout = require("paintmanager.preset_layout")
local function key(zone)
    return assert(layout.zone_identity(zone), "Logical paint zone unavailable")
end
local paint_equal = layout.paint_equal
M.compare_layout = layout.compare_layout

local zone_order = {
    character = {
        Head = { "MI_Player_Character_Head", 1 }, Scarf = { "MI_Player_Character_Scarf", 2 },
        Torso = { "MI_Player_Character_Torso", 3 }, Arms = { "MI_Player_Character_Arms", 4 },
        Legs = { "MI_Player_Character_Legs", 5 },
    },
    boat = {
        Deck = { "M_Deck", 1 }, Tower = { "Tower_Material", 2 }, Hull = { "M_Hull", 3 }, Flag = { "FlagMat", 4 },
    },
}

function M.ordered_zones(kind, zones)
    local ranks = zone_order[kind] or {}
    local entries = {}
    for index, zone in ipairs(zones or {}) do
        local known = ranks[zone.partName]
        local rank = known and known[1] == zone.materialSlotName and known[2] or math.huge
        entries[index] = { zone = zone, index = index, rank = rank }
    end
    table.sort(entries, function(a, b)
        if a.rank == b.rank then return a.index < b.index end
        return a.rank < b.rank
    end)
    local ordered = {}
    for index, entry in ipairs(entries) do ordered[index] = entry.zone end
    return ordered
end
local function asset(row, require_paint)
    assert(type(row) == "table" and type(row.assetName) == "string" and row.assetName ~= ""
        and row.assetName ~= "None" and type(row.assetType) == "string" and row.assetType ~= "", "Paint asset ID unavailable")
    local paint = { assetName = row.assetName, assetType = row.assetType }
    if require_paint then
        assert(row.itemResolved == true and type(row.itemPath) == "string", "Paint asset is not resolved")
        local item = StaticFindObject(path(row.itemPath))
        assert(safe.valid(item) and item:IsA(PAINT) == true and safe.short_name(item) == row.assetName,
            "Serialized/native paint identity mismatch")
        local display = safe.text(safe.get(item, "Name"))
        if display and display ~= "" then paint.displayName = display end
    end
    return paint
end

local function logical(target, require_paint)
    assert(target.complete == true and target.slotConfigurationComplete == true
        and #(target.unconfiguredItems or {}) == 0, "Selected paint target is incomplete")
    assert(type(target.paintSetName) == "string" and target.paintSetName ~= "", "Paint set identity unavailable")
    local zones, seen = json.array(), {}
    assert(#target.slots > 0, "Selected paint target has no configured zones")
    for _, slot in ipairs(target.slots) do
        local identity = key(slot)
        assert(not seen[identity], "Ambiguous duplicate logical paint zones")
        seen[identity] = true
        assert(slot.occupancy == "empty" or slot.occupancy == "occupied", "Unknown paint zone occupancy")
        local zone = { materialSlotName = slot.materialSlotName, partName = slot.partName,
            displayName = type(slot.displayName) == "string" and slot.displayName or nil,
            paint = slot.occupancy == "occupied" and asset(slot.item, require_paint) or json.null }
        if slot.occupancy == "occupied" then assert(slot.item.itemCount == 1, "Unexpected installed paint quantity") end
        zones[#zones + 1] = zone
    end
    return zones
end

local function read_apply_inventory(component, _, owner, distance, config)
    return adapter.read_inventory(component, "preset_source", owner, distance, config)
end

local function selected(context, request, config, prove_items)
    assert(context.token == request.contextToken, "Preset player context changed")
    local component, owner = resolve(request.target.runtimeRef), resolve(request.target.ownerRuntimeRef)
    assert(ref(component:GetOwner()).id == request.target.ownerRuntimeRef.id, "Selected paint owner changed")
    local world = ref(owner:GetWorld())
    assert(world and world.id == context.worldId, "Selected paint world changed")
    local kind = request.target.kind
    if kind == "character" then
        assert(ref(owner).id == context.pawnRef.id and ref(safe.get(context.pawn, "PaintInventory")).id == request.target.runtimeRef.id,
            "Current character paint inventory changed")
    else assert(kind == "boat" and owner:IsA("/Script/Voyage.VoyageBoatPawn") == true, "Selected target is not a boat") end
    local target = paint_state.read_target(component, kind == "character" and "installed_character_paint" or "installed_boat_paint",
        owner, nil, config, prove_items and read_apply_inventory or adapter.read_inventory)
    return component, owner, target
end

local Capture = {}; Capture.__index = Capture
function M.begin_capture(config, request, ship_owner_id)
    local background = require("paintmanager.background")
    local use_analysis = not request and type(background.supports) == "function" and background.supports("analysis_preset_plan")
    return setmetatable({ state = "running", phase = "preset_context", config = config,
        request = request, shipOwnerId = ship_owner_id, processed = 0, index = 0, targets = json.array(), useAnalysis = use_analysis,
        capturedTargets = use_analysis and json.array() or nil }, Capture)
end
function Capture:cancel(reason)
    if self.child then self.child:cancel(reason) end
    if self.analysisJob then self.analysisJob:cancel(reason) end
    self.analysisJob, self.capturedTargets, self.activeProofs = nil, nil, nil
    self.state, self.error, self.child, self.data = "cancelled", reason, nil, nil
end
function Capture:_unit(budget)
    if self.phase == "preset_capture_worker" then
        local progress = self.analysisJob:step_one(budget)
        self.worker_defer=progress.defer==true
        if progress.state == "running" then return end
        assert(progress.state == "done", progress.error or "Native preset capture preparation stopped")
        assert(current().token == self.token, "Player/world changed during paint capture")
        self.targets = assert(self.analysisJob:get_result(), "Native preset targets missing")
        self.analysisJob, self.state, self.phase = nil, "done", "done"
        return
    end
    local context = current()
    assert(not self.token or context.token == self.token, "Player/world changed during paint capture")
    if self.phase == "preset_context" then
        self.token = context.token
        if self.request then
            -- Re-resolve just the component and owner selected on this click.
            local _, _, target = selected(context, self.request, self.config)
            assert(target.runtimeRef and target.runtimeRef.id == self.request.targetId, "Selected paint target changed")
            self.data = { character = target, boats = {}, complete = target.complete }
            self.phase = "preset_targets"
            return
        end
        self.child = paint_state.begin(context.pawnRef, self.config, adapter.read_inventory,
            { worldId = context.worldId, shipOwnerId = self.shipOwnerId })
        self.phase = "preset_capture"
    elseif self.phase == "preset_capture" then
        local progress = self.child.step_one and self.child:step_one(budget) or self.child:step()
        self.worker_defer=progress.defer==true
        if progress.state == "done" then self.data, self.child, self.phase = progress.data, nil, "preset_targets"
        elseif progress.state ~= "running" then error(progress.error or progress.reason or "Paint capture failed") end
    elseif self.phase == "preset_targets" then
        local target = self.index == 0 and self.data.character or self.data.boats[self.index]
        self.index = self.index + 1
        if not target then
            if self.useAnalysis then
                local child, err = require("paintmanager.background").begin_task("analysis_preset_plan", {
                    mode = "capture", targets = self.capturedTargets, contextToken = self.token })
                assert(child, err or "Native preset capture preparation unavailable")
                self.analysisJob, self.capturedTargets, self.phase = child, nil, "preset_capture_worker"
            else self.state, self.phase = "done", "done" end
            return
        end
        if self.useAnalysis then
            self.activeTarget, self.activeProofs, self.zoneIndex, self.phase = target, json.array(), 1, "preset_zone_proof"
            return
        end
        local kind = target.role == "installed_character_paint" and "character" or "boat"
        local view = { id = target.runtimeRef and target.runtimeRef.id, kind = kind, complete = false,
            runtimeRef = target.runtimeRef, ownerRuntimeRef = target.ownerRuntimeRef,
            label = t(kind == "character" and "preset.target.character" or "preset.target.boat",
                { name = type(target.paintSetDisplayName) == "string" and target.paintSetDisplayName or "?",
                    owner = type(target.owner) == "string" and (target.owner:match("([^%.:/]+)$") or target.owner) or "?" }),
            zones = json.array(), contextToken = self.token }
        view.complete = target.complete == true and target.slotConfigurationComplete == true
            and #(target.unconfiguredItems or {}) == 0 and #target.slots > 0
            and target.runtimeRef ~= nil and target.ownerRuntimeRef ~= nil
            and type(target.paintSetName) == "string" and target.paintSetName ~= ""
        if view.complete then
            view.compatibility = { kind = kind, className = path(target.ownerClass), paintSetName = target.paintSetName,
                displayName = type(target.paintSetDisplayName) == "string" and target.paintSetDisplayName or nil }
        else view.error = "Selected paint target is incomplete" end
        self.activeTarget, self.activeView = target, view
        self.zoneIndex, self.zoneSeen, self.phase = 1, {}, "preset_zone"
        view.logicalZones = json.array()
    elseif self.phase == "preset_zone_proof" then
        local slot = self.activeTarget.slots[self.zoneIndex]
        if not slot then
            self.capturedTargets[#self.capturedTargets + 1] = { target = self.activeTarget, proofs = self.activeProofs }
            self.activeTarget, self.activeProofs, self.phase = nil, nil, "preset_targets"
            return
        end
        local proof = {}
        if slot.occupancy == "occupied" then
            local ok, paint = pcall(function()
                assert(slot.item.itemCount == 1, "Unexpected installed paint quantity")
                return asset(slot.item, true)
            end)
            if ok then proof.paint = paint else proof.error = tostring(paint) end
        end
        self.activeProofs[#self.activeProofs + 1] = proof
        self.zoneIndex = self.zoneIndex + 1
    elseif self.phase == "preset_zone" then
        local target, view = self.activeTarget, self.activeView
        local slot = target.slots[self.zoneIndex]
        if not slot then
            if view.id then self.targets[#self.targets + 1] = view end
            if not view.complete then view.logicalZones, view.compatibility = nil, nil end
            self.activeTarget, self.activeView, self.zoneSeen, self.phase = nil, nil, nil, "preset_targets"
            return
        end
        self.zoneIndex = self.zoneIndex + 1
        local display = type(slot.displayName) == "string" and slot.displayName ~= "" and slot.displayName
            or type(slot.partName) == "string" and slot.partName or "?"
        local paint_label = slot.occupancy == "empty" and t("preset.zone.empty") or t("preset.zone.unknown")
        local ok, zone = pcall(function()
            local identity = key(slot)
            assert(not self.zoneSeen[identity], "Ambiguous duplicate logical paint zones")
            self.zoneSeen[identity] = true
            assert(slot.occupancy == "empty" or slot.occupancy == "occupied", "Unknown paint zone occupancy")
            local paint = json.null
            if slot.occupancy == "occupied" then
                assert(slot.item.itemCount == 1, "Unexpected installed paint quantity")
                paint = asset(slot.item, true)
                paint_label = paint.displayName or t("preset.paint.unavailable", { id = paint.assetName })
            end
            return { materialSlotName = slot.materialSlotName, partName = slot.partName,
                displayName = type(slot.displayName) == "string" and slot.displayName or nil, paint = paint }
        end)
        if ok then view.logicalZones[#view.logicalZones + 1] = zone
        else view.complete, view.error = false, tostring(zone) end
        view.zones[#view.zones + 1] = { label = display, paintLabel = paint_label, occupancy = slot.occupancy }
    end
end
function Capture:step(budget)
    repeat
        if self.state ~= "running" or budget and budget:expired() then break end
        self.worker_defer=false
        local ok, err = pcall(function() self:_unit(budget) end)
        self.processed = self.processed + 1
        if budget then budget:consume() end
        if not ok then
            if self.child then pcall(function() self.child:cancel(tostring(err)) end) end
            if self.analysisJob then self.analysisJob:cancel(tostring(err)); self.analysisJob = nil end
            self.state, self.error, self.data, self.child = "error", tostring(err), nil, nil
        end
        if self.worker_defer then break end
    until not budget
    return { state = self.state, phase = self.phase, processed = self.processed, total = self.processed + 1,
        view = self.state == "done" and { targets = self.targets, contextToken = self.token,
            complete = self.data.complete, paintState = not self.request and self.data or nil } or nil, error = self.error }
end

function M.from_target(target, name, id, build)
    assert(target and target.complete and target.compatibility and target.logicalZones, "Selected paint target is incomplete")
    return { id = id, name = name, createdUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"), gameBuild = type(build) == "string" and build:match("^%d+$") or nil,
        target = target.compatibility, zones = target.logicalZones }
end

local function backpack(context, config, defer_validation)
    local inventory = context.pc:GetInventoryComponent(1)
    local actual, field = ref(inventory), ref(safe.get(context.pawn, "WeightInventory"))
    assert(actual and field and actual.id == field.id and inventory:IsA(INVENTORY) == true,
        "Native PlayerInventory does not match the current character backpack")
    local owner = ref(inventory:GetOwner())
    assert(owner and owner.id == context.pawnRef.id, "Backpack owner mismatch")
    local data = adapter.read_inventory(inventory, "preset_source", context.pawn, 0, config)
    assert(data.complete == true, "Backpack read is incomplete")
    local seen = {}
    if not defer_validation then for _, row in ipairs(data.slots) do
        local index = tonumber(row.slot)
        assert(index and index % 1 == 0 and index >= 0 and index <= 2147483647 and not seen[index], "Invalid/duplicate backpack slot")
        seen[index] = true
    end end
    return inventory, data, actual
end
local function compatible(target, preset, kind)
    assert(preset.target.kind == kind and preset.target.className == path(target.ownerClass)
        and preset.target.paintSetName == target.paintSetName, "Preset is incompatible with the selected target")
    local zones, slots = logical(target, false), {}
    assert(#zones == #preset.zones, "Preset logical zone count changed")
    for index, zone in ipairs(zones) do
        slots[key(zone)] = { zone = zone, slot = target.slots[index].index, item = target.slots[index].item }
    end
    local seen = {}
    for _, zone in ipairs(preset.zones) do
        local identity = key(zone)
        assert(slots[identity] and not seen[identity], "Preset logical zones do not match the selected target")
        seen[identity] = true
    end
    return slots
end
local function source_postcheck(before, after, source_slot, source_row, box)
    local expected, actual = {}, {}
    for _, row in ipairs(before.slots) do
        if row.itemCount > 0 and tonumber(row.slot) ~= source_slot then
            expected[tonumber(row.slot)] = row
        end
    end
    for _, row in ipairs(after.slots) do if row.itemCount > 0 then actual[tonumber(row.slot)] = row end end
    local left = actual[source_slot]
    if source_row.itemCount == 1 then
        assert(left == nil, "Selected can did not leave its source slot")
    else
        assert(left and left.itemCount == source_row.itemCount - 1 and left.itemPath == source_row.itemPath
            and left.assetName == source_row.assetName and left.assetType == source_row.assetType and left.plainStack
            and left.fingerprint == source_row.fingerprint, "Source paint stack did not decrease by one")
        actual[source_slot] = nil
    end
    for index, row in pairs(expected) do
        local found = actual[index]
        assert(found and found.itemCount == row.itemCount and found.assetName == row.assetName and found.assetType == row.assetType
            and found.itemPath == row.itemPath and found.fingerprint == row.fingerprint,
            box and "Unrelated box slot changed during paint transfer" or "Unrelated backpack slot changed during paint transfer")
        actual[index] = nil
    end
    assert(next(actual) == nil, "Unexpected item appeared in the paint source inventory")
end
local function totals(data, target)
    local result = {}
    for _, inventory in ipairs({ data, target.inventory }) do
        for _, row in ipairs(inventory.slots) do
            if row.itemCount > 0 then
                assert(type(row.assetName) == "string" and type(row.assetType) == "string", "Inventory asset ID unavailable")
                local identity = #row.assetType .. ":" .. row.assetType .. row.assetName
                result[identity] = (result[identity] or 0) + row.itemCount
            end
        end
    end
    return result
end
local function equal_totals(a, b)
    for k, v in pairs(a) do if b[k] ~= v then return false end end
    for k, v in pairs(b) do if a[k] ~= v then return false end end
    return true
end
local Apply = {}; Apply.__index = Apply
function M.begin_apply(config, request)
    return setmetatable({ config = config, request = request, state = "running", phase = "preset_preflight",
        index = 1, processed = 0, plan = {}, expected = {}, sources = {},
        receipt = { schemaVersion = 1, readOnly = false, action = "apply_preset", presetId = request.preset.id,
            contextToken = request.contextToken, target = request.target.runtimeRef, attempted = false, outcome = "pending",
            startedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"), transfers = json.array(), completed = 0,
            boxSearch = { hintedCandidates = 0, fallbackCandidates = 0, usedDiscovery = false },
            policy = "Apply the selected layout with physical cans from the backpack and accessible ship boxes",
            limitation = "One bounded live source and target proof, transfer and postchecks are indivisible" } }, Apply)
end
function Apply:_finish(outcome, err)
    if self.analysisJob then self.analysisJob:cancel(err or outcome) end
    self.analysisJob = nil
    self.state, self.phase, self.receipt.outcome, self.receipt.error = "done", "done", outcome, err
    self.receipt.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
end
function Apply:cancel(reason)
    if self.state ~= "running" then return end
    self:_finish(self.receipt.attempted and "partial_cancelled" or "cancelled", reason)
end
function Apply:get_receipt() return self.receipt end
function Apply:_persistence_guard()
    local guard = self.request.persistence_guard
    if guard == nil then return end
    assert(type(guard) == "function", "Invalid saved-document guard")
    local unchanged, err = guard()
    assert(unchanged == true, err or "Saved mod data changed during preset application")
end
function Apply:_incoming(context, desired, bag, bag_data)
    local choice = assert(self.sources[self.index], "Preset paint source was not reserved")
    local inventory, data = bag, bag_data
    if choice.box then inventory, data = supply.read_box(self.config, context, self.shipScope, choice.box) end
    local slot, item, row = supply.find(data, choice, desired.paint)
    return inventory, data, slot, item, row, choice
end

local function occupied_slot(data, slot)
    for _, row in ipairs(data.slots) do
        if tonumber(row.slot) == slot and row.itemCount > 0 then return row end
    end
end

-- One native call moves exactly one can into an empty slot. Replacing a
-- color first stores the removed can, then fills that same zone on a later step.
function Apply:_prepare_move(context, component, target, slots, bag, bag_data, desired, admitted)
    local zone = slots[key(desired)]
    local removing = zone.zone.paint ~= json.null
    local storage, stored, incoming_slot, incoming_item, incoming_row, choice = bag, bag_data
    if desired.paint ~= json.null then
        storage, stored, incoming_slot, incoming_item, incoming_row, choice = self:_incoming(context, desired, bag, bag_data)
        if not admitted then
            assert(storage:IsValidSlotIndex(incoming_slot) == true and storage:CanRemoveSlot(incoming_slot) == true,
                "Source inventory cannot release the selected can")
        end
    end
    local destination, source, from, to, row, item = component, storage, incoming_slot, zone.slot, incoming_row, incoming_item
    assert(math.tointeger(zone.slot) and zone.slot >= 0 and zone.slot <= 2147483647,
        "Invalid current destination paint slot")
    if removing then
        -- The native editor returns removed paint to the player's backpack,
        -- independently of the inventory supplying its replacement.
        storage, stored = bag, bag_data
        row = zone.item
        assert(row and row.itemCount == 1 and row.fingerprint and paint_equal(asset(row, true), zone.zone.paint),
            "Installed paint identity or instance data could not be verified")
        item = StaticFindObject(path(row.itemPath))
        if not admitted then
            assert(component:IsValidSlotIndex(zone.slot) == true and component:CanRemoveSlot(zone.slot) == true,
                "Installed paint cannot be removed")
        end
        destination, source, from, to = storage, component, zone.slot, admitted and admitted.to or storage:GetNextEmptySlot()
        assert(math.tointeger(to) and to >= 0 and to <= 2147483647 and not occupied_slot(stored, to),
            "Your backpack needs an empty slot for the removed paint")
    end
    if admitted then
        assert(source:GetAddress() == admitted.source:GetAddress() and destination:GetAddress() == admitted.destination:GetAddress()
            and from == admitted.from and to == admitted.to and row.itemPath == admitted.row.itemPath
            and row.fingerprint == admitted.row.fingerprint,
            "Paint endpoints changed during admission")
    else
        assert(destination:IsValidSlotIndex(to) == true and destination:CanAddItem(item) == true
            and destination:CanAddToSlot(to,item) == true and destination:EstimateAddAmount(item,1) == 1
            and destination:EstimateAddAmountToSlot(to,item,1) == 1,
            removing and "Your backpack cannot accept the removed paint" or "Native paint-slot admission rejected the selected can")
    end
    return { removing = removing, destination = destination, source = source, from = from, to = to,
        row = row, item = item, storage = stored, choice = choice, target = target }
end
function Apply:_unit(budget)
    local request = self.request
    assert(request.gameBuild == self.config.reference_build, "Preset application requires the reference game build")
    assert(not uncertain[request.contextToken], "A previous paint transfer is uncertain; restart/reload before retrying")
    if self.phase == "preset_worker" then
        local progress = self.analysisJob:step_one(budget)
        if progress.state == "running" then return end
        assert(progress.state == "done", progress.error or "Native preset planning stopped")
        assert(current().token == request.contextToken, "Preset context changed")
        local result = assert(self.analysisJob:get_result(), "Native preset plan missing")
        self.plan, self.expected = result.plan, result.expected
        self.analysisJob, self.phase = nil, "preset_supply"
        return
    end
    local context = current()
    assert(context.token == request.contextToken, "Preset context changed")
    if self.phase == "preset_preflight" then self:_persistence_guard() end
    if self.phase == "preset_plan" then
        local desired = request.preset.zones[self.planIndex]
        if not desired then self.phase = "preset_supply"; return end
        local identity, live = key(desired), self.preflightSlots[key(desired)]
        self.expected[identity] = live.zone.paint
        if not paint_equal(live.zone.paint, desired.paint) then
            self.plan[#self.plan + 1] = desired
        end
        self.planIndex = self.planIndex + 1
        return
    elseif self.phase == "preset_supply" then
        local bag = resolve(self.backpackRef)
        local remaining, wanted = supply.reserve(self.plan, self.sources, bag, self.preflightData)
        if remaining == 0 then
            self.phase = "preset_ready"
        else
            self.shipScope = assert(ship.current(self.config))
            assert(self.shipScope.worldId == context.worldId, "Selected ship belongs to another world")
            self.boxRefs = supply.cached_boxes(request.stockModel, context, self.shipScope, wanted)
            self.hintedBoxes, self.wantedPaints = true, wanted
            self.boxIndex, self.seenBoxes, self.phase = 1, {}, "preset_boxes"
        end
        return
    elseif self.phase == "preset_boxes" then
        local owner_ref
        owner_ref, self.boxIndex = supply.next_box(self.boxRefs, self.boxIndex, self.hintedBoxes, self.seenBoxes, self.wantedPaints)
        if not owner_ref and self.hintedBoxes then
            self.receipt.boxSearch.usedDiscovery = true
            self.boxRefs = require("paintmanager.paint_transfer").stock_actor_refs(self.config.max_discovery_objects)
            self.hintedBoxes, self.boxIndex = false, 1
            return
        end
        assert(owner_ref, "Not enough matching physical cans in the backpack and accessible ship boxes for the entire preset")
        self.seenBoxes[owner_ref.id] = true
        local counter = self.hintedBoxes and "hintedCandidates" or "fallbackCandidates"
        self.receipt.boxSearch[counter] = self.receipt.boxSearch[counter] + 1
        local ok, component, data, box = pcall(supply.read_box, self.config, context, self.shipScope, { ownerRef = owner_ref })
        if ok then
            local remaining, wanted = supply.reserve(self.plan, self.sources, component, data, box)
            self.wantedPaints = wanted
            if remaining == 0 then self.phase = "preset_ready" end
        end
        return
    elseif self.phase == "preset_ready" then
        -- Planning/supply steps do not repeatedly copy the native save string.
        -- Recheck at the admission boundary, including a complete no-op result.
        self:_persistence_guard()
        self.preflightSlots, self.preflightData, self.boxRefs, self.seenBoxes, self.wantedPaints = nil, nil, nil, nil, nil
        if #self.plan == 0 then self:_finish("already_matches") else self.phase = "preset_commit" end
        return
    end
    local component, owner, target = selected(context, request, self.config, true)
    if self.phase == "preset_preflight" then
        local background = require("paintmanager.background")
        if type(background.supports) == "function" and background.supports("analysis_preset_plan") then
            local inventory, data, inventory_ref = backpack(context, self.config, true)
            local child, err = background.begin_task("analysis_preset_plan", { kind = request.target.kind,
                target = target, preset = request.preset, backpack = data })
            assert(child, err or "Native preset planning unavailable")
            self.backpackRef, self.preflightData, self.analysisJob, self.phase = inventory_ref, data, child, "preset_worker"
            return
        end
    end
    local slots = compatible(target, request.preset, request.target.kind)
    local inventory, data, inventory_ref = backpack(context, self.config)
    if self.phase == "preset_preflight" then
        self.backpackRef, self.preflightSlots, self.preflightData = inventory_ref, slots, data
        self.planIndex, self.phase = 1, "preset_plan"
        return
    end
    assert(inventory_ref.id == self.backpackRef.id, "Backpack identity changed")
    for identity, live in pairs(slots) do assert(paint_equal(live.zone.paint, self.expected[identity]), "Paint layout changed before transfer") end
    local desired = self.plan[self.index]
    local move = self:_prepare_move(context, component, target, slots, inventory, data, desired)
    assert(collection.context() == request.contextToken, "Player context changed before paint transfer")
    self:_persistence_guard()
    if request.connection_guard then
        local connected, reason = request.connection_guard()
        assert(connected, reason or "Ship connection lost before paint transfer")
    end

    -- Native admission and persistence checks can call game code. Reread
    -- both endpoints and the reserved incoming can before moving either paint.
    local final_context = current()
    local final_component, _, final_target = selected(final_context, request, self.config, true)
    local final_slots = compatible(final_target, request.preset, request.target.kind)
    for identity, live in pairs(final_slots) do
        assert(paint_equal(live.zone.paint, self.expected[identity])
            and live.slot == slots[identity].slot, "Paint layout changed during admission")
    end
    local final_bag, final_bag_data, final_ref = backpack(final_context, self.config)
    assert(final_ref.id == inventory_ref.id, "Backpack changed during admission")
    move = self:_prepare_move(final_context, final_component, final_target, final_slots, final_bag, final_bag_data, desired, move)
    local before = totals(move.storage, move.target)
    local box = not move.removing and move.choice and move.choice.box
    local storage_kind = box and "ship_box" or "backpack"
    local transfer = { zone = { materialSlotName = desired.materialSlotName, partName = desired.partName },
        direction = move.removing and "remove" or "install", paint = asset(move.row, false),
        sourceSlot = move.from, destinationSlot = move.to, amount = 1,
        sourceKind = move.removing and "installed" or storage_kind,
        destinationKind = move.removing and storage_kind or "installed",
        sourceBox = not move.removing and box and box.ownerRef or nil,
        attempted = true, outcome = "uncertain", beforeSource = move.row, beforeDestination = json.null }
    self.receipt.transfers[#self.receipt.transfers + 1] = transfer
    self.receipt.attempted = true
    uncertain[request.contextToken] = true
    local called, result = pcall(function() return move.destination:TransferSlot(move.source, move.from, move.to, 1) end)
    transfer.nativeReturn = called and (type(result) == "number" or type(result) == "boolean" or type(result) == "string") and result or nil
    transfer.nativeReturnType = called and type(result) or nil
    transfer.nativeError = not called and tostring(result) or nil
    local verified, post_error = pcall(function()
        local after_context = current()
        local _, _, after_target = selected(after_context, request, self.config, true)
        local after_slots = compatible(after_target, request.preset, request.target.kind)
        local _, after_data, after_ref = backpack(after_context, self.config)
        assert(after_ref.id == inventory_ref.id, "Backpack changed during transfer")
        if box then _, after_data = supply.read_box(self.config, after_context, self.shipScope, box) end
        if move.removing then
            local arrived = occupied_slot(after_data, move.to)
            assert(arrived and arrived.itemCount == 1 and arrived.itemPath == move.row.itemPath
                and arrived.fingerprint == move.row.fingerprint
                and paint_equal(asset(arrived, true), transfer.paint), "Removed paint did not arrive in its destination slot")
            source_postcheck(after_data, move.storage, move.to, arrived, box)
        else
            source_postcheck(move.storage, after_data, move.from, move.row, box)
            local installed = after_slots[key(desired)].item
            assert(installed and installed.fingerprint == move.row.fingerprint and installed.itemPath == move.row.itemPath,
                "Installed paint instance data changed during transfer")
        end
        local expected = move.removing and json.null or desired.paint
        for identity, live in pairs(after_slots) do
            assert(paint_equal(live.zone.paint, identity == key(desired) and expected or self.expected[identity])
                and live.slot == final_slots[identity].slot, "Installed paint postcheck failed")
        end
        assert(equal_totals(before, totals(after_data, after_target)), "Inventory conservation postcheck failed")
        assert(collection.context() == request.contextToken, "Context changed during transfer postcheck")
        self:_persistence_guard()
        transfer.postcheck = { sourceRemoved = true, unrelatedSourceRowsUnchanged = true,
            targetLayoutMatches = true, quantitiesConserved = true }
        self.expected[key(desired)] = expected
    end)
    if not verified or not called or result ~= 1 then
        transfer.postcheckError = not verified and tostring(post_error) or transfer.nativeError or "Native transfer did not return exactly one"
        self:_finish("uncertain", transfer.postcheckError)
        return
    end
    uncertain[request.contextToken] = nil
    transfer.outcome = "applied_verified"
    if not move.removing or desired.paint == json.null then
        self.receipt.completed = self.receipt.completed + 1
        self.index = self.index + 1
        if self.index > #self.plan then self:_finish("applied_verified") end
    end
end
function Apply:step(budget)
    -- One proof/attempt/postcheck maximum per call, even with a failed clock.
    if self.state == "running" and (not budget or not budget:expired()) then
        local before = clock_ms()
        local ok, err = pcall(function() self:_unit(budget) end)
        self.processed = self.processed + 1
        if budget then budget:consume() end
        local after = clock_ms()
        if before and after and after >= before then
            self.receipt.maxStepMs = math.max(self.receipt.maxStepMs or 0, after - before)
        else self.receipt.clockUnavailable = true end
        if not ok then self:_finish(self.receipt.attempted and "partial_stopped" or "rejected", tostring(err)) end
    end
    return { state = self.state, phase = self.phase, processed = self.processed, total = math.max(1, #self.plan + 1),
        receipt = self.state == "done" and self.receipt or nil }
end
return M
