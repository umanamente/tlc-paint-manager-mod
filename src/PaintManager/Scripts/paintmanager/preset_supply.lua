-- Reserve the physical incoming cans before removing or installing any paint.
-- Only plain slot/box descriptors survive between callbacks.
local collection = require("paintmanager.collection")
local adapter = require("paintmanager.game_adapter")
local transfer = require("paintmanager.paint_transfer")
local ship = require("paintmanager.ship_scope")
local json = require("paintmanager.json")
local safe = require("paintmanager.safe")
local M = {}

function M.validate(data)
    assert(data.complete == true, "Paint source inventory read is incomplete")
    local seen = {}
    for _, row in ipairs(data.slots) do
        local slot = math.tointeger(tonumber(row.slot))
        assert(slot and slot >= 0 and slot <= 2147483647 and not seen[slot], "Invalid or duplicate paint source slot")
        seen[slot] = true
    end
end

function M.read_box(config, context, scope, box)
    local owner = assert(collection.resolve_ref(box.ownerRef))
    local component = owner.VoyageInventoryWeightLimited
    local actual = transfer.accessible_box(component, context)
    assert(actual and actual:GetAddress() == owner:GetAddress(), "Paint source box is locked or unavailable")
    local component_ref = assert(collection.runtime_ref(component))
    assert(not box.componentRef or box.componentRef.id == component_ref.id, "Paint source box inventory changed")
    local live = assert(ship.current(config, scope))
    assert(live.worldId == context.worldId and ship.contains(live, owner:K2_GetActorLocation()),
        "Paint source box left the selected ship radius")
    local data = adapter.read_inventory(component, "preset_source", owner, nil, config)
    M.validate(data)
    return component, data, { ownerRef = box.ownerRef, componentRef = component_ref, kind = collection.box_kind(owner) }
end

local function native_paint(row)
    local item = StaticFindObject(row.itemPath:match("^[^ ]+ (/.+)$") or row.itemPath)
    if not collection.eligible_paint(item) or safe.short_name(item) ~= row.assetName then return end
    local asset_type = collection.primary_type(item)
    if asset_type and asset_type ~= row.assetType then return end
    return item
end

-- Only use locations from this character's ship interval. The owning actor is
-- the hint; read_box resolves its present inventory, contents and access anew.
function M.cached_boxes(model, context, scope, wanted)
    local boxes, by_owner = {}, {}
    if type(model) ~= "table" or model.status ~= "captured" or model.cachePreview or model.learnedOnly
        or model.contextToken ~= context.token or type(model.stockScope) ~= "table"
        or model.stockScope.shipId ~= scope.shipId or not ship.same_scope(model.stockScope, scope)
        or type(model.rows) ~= "table" or type(model.stockLocationSources) ~= "table" then return boxes end
    for _, row in ipairs(model.rows) do
        local key = row.assetType and row.assetName and row.assetType .. "\0" .. row.assetName
        if wanted[key] and type(row.stockLocationHints) == "table" then
            for _, index in ipairs(row.stockLocationHints) do
                local source = model.stockLocationSources[index]
                local owner = type(source) == "table" and source.ownerRef
                if type(owner) == "table" and type(owner.id) == "string" and type(owner.name) == "string" and type(owner.className) == "string" then
                    local hint = by_owner[owner.id]
                    if not hint then
                        hint = { owner = owner, paints = {} }
                        by_owner[owner.id], boxes[#boxes + 1] = hint, hint
                    end
                    hint.paints[key] = true
                end
            end
        end
    end
    return boxes
end

function M.next_box(boxes, index, hinted, seen, wanted)
    while index <= #boxes do
        local box = boxes[index]
        local owner = hinted and box.owner or box
        index = index + 1
        if not seen[owner.id] then
            if not hinted then return owner, index end
            for key in pairs(box.paints) do if wanted[key] then return owner, index end end
        end
    end
    return nil, index
end

function M.reserve(plan, sources, inventory, data, box)
    local remaining, wanted = 0, {}
    for i, desired in ipairs(plan) do
        local paint = desired.paint
        if paint ~= json.null and not sources[i] then
            local key = paint.assetType .. "\0" .. paint.assetName
            local group = wanted[key] or { first = 1 }
            group[#group + 1], wanted[key], remaining = i, group, remaining + 1
        end
    end
    for _, row in ipairs(data.slots) do
        if remaining == 0 then break end
        local key = row.assetType and row.assetName and row.assetType .. "\0" .. row.assetName
        local group = wanted[key]
        local slot = tonumber(row.slot)
        if group and group.first <= #group and row.itemCount > 0 and (row.itemCount == 1 or row.plainStack)
            and row.fingerprint and row.itemResolved and row.itemPath and native_paint(row)
            and inventory:IsValidSlotIndex(slot) == true and inventory:CanRemoveSlot(slot) == true then
            for spent = 0, math.min(row.itemCount, #group - group.first + 1) - 1 do
                sources[group[group.first]] = { box = box, slot = slot, itemPath = row.itemPath, count = row.itemCount - spent,
                    fingerprint = row.fingerprint }
                group.first, remaining = group.first + 1, remaining - 1
            end
            if group.first > #group then wanted[key] = nil end
        end
    end
    return remaining, wanted
end

function M.find(data, choice, paint)
    for _, row in ipairs(data.slots) do
        if tonumber(row.slot) == choice.slot then
            assert(row.itemCount == choice.count and row.itemPath == choice.itemPath and row.itemResolved
                and row.assetType == paint.assetType and row.assetName == paint.assetName
                and (row.itemCount == 1 or row.plainStack)
                and row.fingerprint == choice.fingerprint,
                "Selected paint source slot changed")
            local item = assert(native_paint(row), "Selected native paint identity changed")
            return choice.slot, item, row
        end
    end
    error("Selected paint source slot is empty")
end

return M
