-- Fixed owned-data tasks for the isolated worker Lua runtime. This module has
-- no engine globals, reflection, filesystem handles or gameplay callbacks.
local json = require("paintmanager.json")
local t = require("paintmanager.i18n").text
local M = {}
local Job = {}; Job.__index = Job

local function pulse() coroutine.yield() end
local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end
local function add_error(model, message)
    model.complete, model.stockComplete = false, false
    if #model.errors < 16 then model.errors[#model.errors + 1] = message end
end

local function paint_hint(asset_name, item_path, class_path)
    local id, path, class = (asset_name or ""):lower(), (item_path or ""):lower(), (class_path or ""):lower()
    if id:match("^da_paint_") then return "asset_name" end
    if path:find("/paints/", 1, true) then return "asset_path" end
    if class:match("/script/voyage%.voyagepaintitem$") then return "native_class" end
end
local function inventory_analysis(inventory)
    if not inventory._analysisInventory then return end
    inventory._analysisInventory = nil
    for _, row in ipairs(inventory.slots) do
        row.paintCandidateReason = paint_hint(row.assetName, row.itemPath, row.itemClass)
        pulse()
    end
    table.sort(inventory.slots, function(a, b) return a.slot < b.slot end)
end

local function snapshot(payload)
    local data = assert(payload.data, "Copied diagnostic snapshot missing")
    for _, inventory in ipairs(data.inventories) do
        inventory_analysis(inventory)
        local totals = inventory.role == "backpack" and data.paintCandidates.backpack
            or inventory.role == "nearby_container" and data.paintCandidates.nearbyContainers
        if totals then
            for _, row in ipairs(inventory.slots) do
                if row.paintCandidateReason and row.itemCount > 0 and row.assetName and row.assetName ~= "None" and row.assetName ~= "" then
                    totals[row.assetName] = (totals[row.assetName] or 0) + row.itemCount
                end
                pulse()
            end
        end
        pulse()
    end
    table.sort(data.catalog, function(a, b) return a.itemPath < b.itemPath end)
    return data
end

local function paint_target(payload)
    local target = assert(payload.target, "Captured paint target missing")
    local prefix, events = assert(target._analysisPrefix), assert(target._analysisEvents)
    target.errors, target.complete, target.status = prefix.errors, prefix.complete, prefix.status
    target.errorsTruncated = prefix.errorsTruncated
    target._analysisPrefix, target._analysisEvents = nil, nil
    local function fail(message)
        target.complete = false
        if target.status == "captured" then target.status = "incomplete" end
        if #target.errors < 8 then target.errors[#target.errors + 1] = message else target.errorsTruncated = true end
    end
    local function int32(value)
        return type(value) == "number" and value == value and value % 1 == 0 and value >= -2147483648 and value <= 2147483647
    end
    local inventory = target.inventory
    local by_slot, configured, inventory_complete = {}, {}, inventory ~= json.null and inventory.complete == true
    if inventory ~= json.null then
        inventory_analysis(inventory)
        for _, item in ipairs(inventory.slots) do
            if type(item) ~= "table" then inventory_complete = false; fail("Invalid installed inventory row")
            else
                local index = type(item.slot) == "string" and tonumber(item.slot) or item.slot
                if not int32(index) or not int32(item.itemCount) or item.itemCount < 0 then
                    inventory_complete = false; fail("Invalid installed inventory slot/count")
                elseif by_slot[index] ~= nil then
                    inventory_complete = false; fail("Duplicate installed inventory slot " .. tostring(index))
                else by_slot[index] = item end
            end
            pulse()
        end
    end
    target.slots = json.array()
    for _, event in ipairs(events) do
        if event.kind == "error" then fail(event.message)
        else
            local row = event.row
            configured[row.index] = true
            local item = by_slot[row.index]
            if item and item.itemCount > 0 then
                row.item, row.occupancy = item, "occupied"
                if type(item.assetName) ~= "string" or item.assetName == "" or item.assetName == "None" then
                    row.itemIdentityComplete = false; fail("Installed item identity unavailable in slot " .. tostring(row.index))
                end
            elseif inventory_complete then row.occupancy = "empty" end
            target.slots[#target.slots + 1] = row
        end
        pulse()
    end
    table.sort(target.slots, function(a, b) return a.index < b.index end)
    for index, item in pairs(by_slot) do
        if item.itemCount > 0 and not configured[index] then
            target.unconfiguredItems[#target.unconfiguredItems + 1] = item
            fail("Installed item has no captured configuration for slot " .. tostring(index))
        end
        pulse()
    end
    table.sort(target.unconfiguredItems, function(a, b) return tonumber(a.slot) < tonumber(b.slot) end)
    if target._analysisExpired then fail("Paint target expired during capture") end
    target._analysisExpired = nil
    return target
end

local function collection(payload)
    if payload.mode == "paint_target" then return paint_target(payload) end
    if payload.mode == "snapshot" then return snapshot(payload) end
    if payload.mode == "source_candidates" then
        local rows = payload.selected
        for _, row in ipairs(payload.candidates) do rows[#rows + 1] = row; pulse() end
        table.sort(rows, function(a, b)
            local left = payload.identity == "ref" and a.componentRef.name or a.id
            local right = payload.identity == "ref" and b.componentRef.name or b.id
            return a.distance == b.distance and left < right or a.distance < b.distance
        end)
        while #rows > payload.maximum do rows[#rows] = nil; pulse() end
        return rows
    end
    local model = assert(payload.model, "Captured collection missing")
    local sources = payload.sources or model.sources
    local by_path, by_asset, ambiguous = {}, {}, {}
    for _, row in ipairs(model.rows) do
        by_path[row.itemPath] = row
        if row.assetName and not payload.catalogEvents then
            if by_asset[row.assetName] and by_asset[row.assetName].itemPath ~= row.itemPath then
                by_asset[row.assetName], ambiguous[row.assetName] = nil, true
            elseif not ambiguous[row.assetName] then by_asset[row.assetName] = row end
        end
        pulse()
    end
    local catalog_index = 1
    for _, capture in ipairs(payload.captures) do
        if payload.catalogEvents then
            local stop = capture.catalogEventCount
            assert(type(stop) == "number" and stop % 1 == 0 and stop >= catalog_index - 1 and stop <= #payload.catalogEvents,
                "Invalid captured catalog registration boundary")
            while catalog_index <= stop do
                local event = payload.catalogEvents[catalog_index]
                local row, asset = by_path[event.itemPath], event.assetName
                assert(row, "Captured catalog registration row missing")
                if asset then
                    if by_asset[asset] and by_asset[asset].itemPath ~= row.itemPath then
                        by_asset[asset], ambiguous[asset] = nil, true
                    elseif not ambiguous[asset] then by_asset[asset] = row end
                end
                catalog_index = catalog_index + 1
                pulse()
            end
        end
        local source = assert(sources[capture.sourceIndex], "Captured stock source missing")
        local initially_complete = source.complete
        local distributions, evidence, cache_paints = {}, {}, {}
        local location_source = model.stockLocationSources and model.stockLocationSources[capture.sourceIndex]
        local counts = location_source and location_source.paintCounts
        for _, record in ipairs(capture.records) do
            local row = record.nativePaint and by_path[record.itemPath] or by_asset[record.assetName]
            if row and not record.unresolvedReason then
                row.count = row.count + record.itemCount
                source.paintCount = source.paintCount + record.itemCount
                model.physicalCount = model.physicalCount + record.itemCount
                local distribution = not model.stockSourcesOmitted and distributions[row.itemPath]
                if not model.stockSourcesOmitted and not distribution then
                    distribution = { sourceId = source.id, sourceIndex = capture.sourceIndex, count = 0,
                        kind = source.kind, label = source.label, displayName = source.displayName,
                        runtimeLabel = source.runtimeLabel, distanceCm = source.distanceCm, capturedUtc = source.capturedUtc }
                    row.stockSources[#row.stockSources + 1] = distribution
                    distributions[row.itemPath] = distribution
                end
                if distribution then distribution.count = distribution.count + record.itemCount end
                if record.nativePaint then
                    if counts then counts[row.itemPath] = (counts[row.itemPath] or 0) + record.itemCount end
                    if record.itemCount > 0 and type(row.assetType) == "string" and row.assetType ~= ""
                        and type(row.assetName) == "string" and row.assetName ~= "" then
                        local path = type(row.itemPath) == "string" and (row.itemPath:match("^[^ ]+ (/.+)$") or row.itemPath) or nil
                        cache_paints[row.assetType .. "\0" .. row.assetName] = {
                            assetType = row.assetType, assetName = row.assetName,
                            itemPath = path and path:sub(1, 6) == "/Game/" and path or nil }
                    end
                    local witness = evidence[row.itemPath]
                    if not witness then
                        local candidate = assert(capture.candidate, "Captured witness descriptor missing")
                        witness = { sourceId = source.id, componentRef = candidate.componentRef,
                            ownerRef = candidate.ownerRef, kind = candidate.kind, capturedCount = 0 }
                        row.stockLocationHints = row.stockLocationHints or json.array()
                        row.stockLocationHints[#row.stockLocationHints + 1] = capture.sourceIndex
                        if #row.stockWitnesses < 4 then row.stockWitnesses[#row.stockWitnesses + 1] = witness end
                        evidence[row.itemPath] = witness
                    end
                    if witness then witness.capturedCount = witness.capturedCount + record.itemCount end
                end
            else
                source.complete = false
                if #model.unresolvedItems < 32 then
                    model.unresolvedItems[#model.unresolvedItems + 1] = {
                        sourceId = source.id, assetName = record.assetName, itemCount = record.itemCount,
                        paintCandidate = record.nativePaint == true or type(record.assetName) == "string"
                            and record.assetName:lower():match("^da_paint_") ~= nil,
                        reason = record.unresolvedReason or "Unresolved item type; no unambiguous loaded native paint asset" }
                end
            end
            pulse()
        end
        if initially_complete and not source.complete then
            add_error(model, "Included container read incomplete: " .. tostring(source.label))
        end
        source._cachePaints = cache_paints
    end
    -- No native references/quantities enter the persistent positive cache.
    -- Keep complete empty boxes here: the cache writer removes a prior positive
    -- entry only after this explicit successful negative observation.
    local cache, has_cache, seen_ids, duplicate_ids = json.array(), model.boxCache ~= nil, {}, {}
    for _, source in ipairs(sources) do
        local id = source.persistentId
        if type(id) == "string" then
            has_cache = true
            if seen_ids[id] then duplicate_ids[id] = true end
            seen_ids[id] = true
        end
        pulse()
    end
    for _, source in ipairs(sources) do
        local id, checked = source.persistentId, source.checkedAt
        local kind = type(source.kind) == "string" and source.kind:lower() or nil
        if source.complete == true and type(id) == "string" and #id == 16 and id:match("^[0-9a-f]+$")
            and id ~= "0000000000000000" and not duplicate_ids[id]
            and (kind == "small" or kind == "medium" or kind == "large")
            and finite(checked) and checked >= 0 and checked % 1 == 0 then
            local paints = json.array()
            for _, entry in pairs(source._cachePaints or {}) do paints[#paints + 1] = entry; pulse() end
            table.sort(paints, function(a, b)
                return a.assetType == b.assetType and a.assetName < b.assetName or a.assetType < b.assetType
            end)
            cache[#cache + 1] = { persistentId = id, boxKind = kind, lastCheckedAt = checked, paints = paints }
        end
        source._cachePaints = nil
        pulse()
    end
    if has_cache then model.boxCache = cache end
    table.sort(model.rows, function(a, b)
        local left, right = a.displayName or "", b.displayName or ""
        return left == right and a.id < b.id or left < right
    end)
    model.stockSourcesComplete = not model.stockSourcesOmitted and model.stockComplete == true
    for _, row in ipairs(model.rows) do row.stockSourcesComplete = model.stockSourcesComplete; pulse() end
    return model
end

local function view(payload)
    local projection = require("paintmanager.paint_view").begin(payload.model, payload.options, { background = false })
    local result
    repeat
        local progress = projection:step_one()
        assert(progress.state ~= "error" and progress.state ~= "cancelled", progress.error or progress.reason)
        if progress.state == "done" then result = progress.result else pulse() end
    until result
    local indexes, groups, checking = json.array(), result.groups and {} or nil, json.array()
    for _, row in ipairs(result.rows) do indexes[#indexes + 1] = row.__viewIndex; pulse() end
    if groups then
        for name, rows in pairs(result.groups) do
            groups[name] = json.array()
            for _, row in ipairs(rows) do groups[name][#groups[name] + 1] = row.__viewIndex; pulse() end
        end
    end
    for _, row in ipairs(result.checking_rows or {}) do checking[#checking + 1] = row.__viewIndex; pulse() end
    result.rows, result.groups, result.checking_rows = indexes, groups, checking
    return result
end

local function learn_candidates(payload)
    local view = require("paintmanager.paint_view")
    local model = { status = payload.status or "captured", stockComplete = payload.stockComplete ~= false,
        cachePreview = payload.cachePreview, backpackCounts = payload.backpackCounts, _stockUiRevision = payload._stockUiRevision }
    local candidates, seen = json.array(), {}
    for _, row in ipairs(payload.rows or {}) do
        if view.learn_eligible(model, row)
            and row.runtimeRef and type(row.runtimeRef.name) == "string" and type(row.runtimeRef.id) == "string"
            and type(row.runtimeRef.className) == "string" and not seen[row.id] then
            seen[row.id] = true
            candidates[#candidates + 1] = { itemPath = row.id, runtimeRef = row.runtimeRef,
                contextToken = payload.contextToken, radiusCm = payload.radiusCm,
                displayName = row.displayName, displayedCount = view.physical_count(model, row), gameBuild = payload.gameBuild }
        end
        pulse()
    end
    return candidates
end

local function zone_key(zone)
    assert(type(zone.materialSlotName) == "string" and type(zone.partName) == "string"
        and (zone.materialSlotName ~= "" or zone.partName ~= ""), "Logical paint zone unavailable")
    return #zone.materialSlotName .. ":" .. zone.materialSlotName .. zone.partName
end
local function paint_equal(a, b)
    if a == json.null or a == nil then return b == json.null or b == nil end
    return type(b) == "table" and a.assetName == b.assetName and a.assetType == b.assetType
end
local function asset(row)
    assert(type(row) == "table" and type(row.assetName) == "string" and row.assetName ~= ""
        and row.assetName ~= "None" and type(row.assetType) == "string" and row.assetType ~= "", "Paint asset ID unavailable")
    return { assetName = row.assetName, assetType = row.assetType }
end
local function logical(target)
    assert(target.complete == true and target.slotConfigurationComplete == true
        and #(target.unconfiguredItems or {}) == 0, "Selected paint target is incomplete")
    assert(type(target.paintSetName) == "string" and target.paintSetName ~= "", "Paint set identity unavailable")
    assert(#target.slots > 0, "Selected paint target has no configured zones")
    local zones, seen = json.array(), {}
    for _, slot in ipairs(target.slots) do
        local identity = zone_key(slot)
        assert(not seen[identity], "Ambiguous duplicate logical paint zones")
        seen[identity] = true
        assert(slot.occupancy == "empty" or slot.occupancy == "occupied", "Unknown paint zone occupancy")
        if slot.occupancy == "occupied" then assert(slot.item.itemCount == 1, "Unexpected installed paint quantity") end
        zones[#zones + 1] = { materialSlotName = slot.materialSlotName, partName = slot.partName,
            displayName = type(slot.displayName) == "string" and slot.displayName or nil,
            paint = slot.occupancy == "occupied" and asset(slot.item) or json.null }
        pulse()
    end
    return zones
end

local function preset_capture(payload)
    local targets = json.array()
    for _, captured in ipairs(payload.targets) do
        local target, proofs = captured.target, captured.proofs
        local kind = target.role == "installed_character_paint" and "character" or "boat"
        local view = { id = target.runtimeRef and target.runtimeRef.id, kind = kind, complete = false,
            runtimeRef = target.runtimeRef, ownerRuntimeRef = target.ownerRuntimeRef,
            label = t(kind == "character" and "preset.target.character" or "preset.target.boat", {
                name = type(target.paintSetDisplayName) == "string" and target.paintSetDisplayName or "?",
                owner = type(target.owner) == "string" and (target.owner:match("([^%.:/]+)$") or target.owner) or "?" }),
            zones = json.array(), logicalZones = json.array(), contextToken = payload.contextToken }
        view.complete = target.complete == true and target.slotConfigurationComplete == true
            and #(target.unconfiguredItems or {}) == 0 and #target.slots > 0
            and target.runtimeRef ~= nil and target.ownerRuntimeRef ~= nil
            and type(target.paintSetName) == "string" and target.paintSetName ~= ""
        if view.complete then
            view.compatibility = { kind = kind,
                className = type(target.ownerClass) == "string" and (target.ownerClass:match("^[^ ]+ (/.+)$") or target.ownerClass) or nil,
                paintSetName = target.paintSetName,
                displayName = type(target.paintSetDisplayName) == "string" and target.paintSetDisplayName or nil }
        else view.error = "Selected paint target is incomplete" end
        local seen = {}
        for index, slot in ipairs(target.slots) do
            local display = type(slot.displayName) == "string" and slot.displayName ~= "" and slot.displayName
                or type(slot.partName) == "string" and slot.partName or "?"
            local paint_label = slot.occupancy == "empty" and t("preset.zone.empty") or t("preset.zone.unknown")
            local ok, zone = pcall(function()
                local identity = zone_key(slot)
                assert(not seen[identity], "Ambiguous duplicate logical paint zones")
                seen[identity] = true
                assert(slot.occupancy == "empty" or slot.occupancy == "occupied", "Unknown paint zone occupancy")
                local paint = json.null
                if slot.occupancy == "occupied" then
                    assert(slot.item.itemCount == 1, "Unexpected installed paint quantity")
                    local proof = assert(proofs[index], "Native paint proof missing")
                    assert(not proof.error, proof.error)
                    paint = assert(proof.paint, "Native paint identity proof missing")
                    paint_label = paint.displayName or t("preset.paint.unavailable", { id = paint.assetName })
                end
                return { materialSlotName = slot.materialSlotName, partName = slot.partName,
                    displayName = type(slot.displayName) == "string" and slot.displayName or nil, paint = paint }
            end)
            if ok then view.logicalZones[#view.logicalZones + 1] = zone else view.complete, view.error = false, tostring(zone) end
            view.zones[#view.zones + 1] = { label = display, paintLabel = paint_label, occupancy = slot.occupancy }
            pulse()
        end
        if not view.complete then view.logicalZones, view.compatibility = nil, nil end
        if view.id then targets[#targets + 1] = view end
        pulse()
    end
    return targets
end

local function preset_plan(payload)
    if payload.mode == "capture" then return preset_capture(payload) end
    local target, preset = payload.target, payload.preset
    local owner_class = type(target.ownerClass) == "string" and (target.ownerClass:match("^[^ ]+ (/.+)$") or target.ownerClass)
    assert(preset.target.kind == payload.kind and preset.target.className == owner_class
        and preset.target.paintSetName == target.paintSetName, "Preset is incompatible with the selected target")
    local zones, slots = logical(target), {}
    assert(#zones == #preset.zones, "Preset logical zone count changed")
    for index, zone in ipairs(zones) do slots[zone_key(zone)] = { zone = zone, slot = target.slots[index].index }; pulse() end
    local expected, plan, seen = {}, json.array(), {}
    local inventory_slots = {}
    for _, row in ipairs(payload.backpack.slots) do
        local index = tonumber(row.slot)
        assert(index and index % 1 == 0 and index >= 0 and index <= 2147483647 and not inventory_slots[index],
            "Invalid/duplicate backpack slot")
        inventory_slots[index] = true
        pulse()
    end
    for _, desired in ipairs(preset.zones) do
        local identity = zone_key(desired)
        assert(slots[identity] and not seen[identity], "Preset logical zones do not match the selected target")
        seen[identity] = true
        local live = slots[identity]
        expected[identity] = live.zone.paint
        if not paint_equal(live.zone.paint, desired.paint) then
            plan[#plan + 1] = desired
        end
        pulse()
    end
    return { plan = plan, expected = expected }
end

local TASKS = { analysis_collection = collection, analysis_view = view,
    analysis_learn_candidates = learn_candidates, analysis_preset_plan = preset_plan }
function M.supports(kind) return TASKS[kind] ~= nil end
function M.begin(kind, payload)
    local action = assert(TASKS[kind], "Unsupported owned-data analysis task")
    return setmetatable({ state = "running", phase = kind, completed = 0,
        coroutine = coroutine.create(function() return action(payload) end) }, Job)
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, processed = self.completed,
        completed = self.completed, result = self.state == "done" and self.result or nil, error = self.error }
end
function Job:get_result() return self.state == "done" and self.result or nil end
function Job:step_one()
    if self.state ~= "running" then return self:get_progress() end
    local ok, result = coroutine.resume(self.coroutine)
    self.completed = self.completed + 1
    if not ok then self.state, self.phase, self.error = "error", "error", tostring(result)
    elseif coroutine.status(self.coroutine) == "dead" then self.state, self.phase, self.result = "done", "done", result end
    if self.state ~= "running" then self.coroutine = nil end
    return self:get_progress()
end
function Job:cancel(reason)
    if self.state == "running" then self.state, self.phase, self.error, self.coroutine = "cancelled", "cancelled", reason, nil end
    return self:get_progress()
end
return M
