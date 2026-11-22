-- Pure logical zone comparison shared by UI, explicit actions and the guarded storage update.
local json = require("paintmanager.json")
local M = {}
local function zone_identity(zone)
    if type(zone) == "table" and type(zone.materialSlotName) == "string" and type(zone.partName) == "string"
        and (zone.materialSlotName ~= "" or zone.partName ~= "") then
        return #zone.materialSlotName .. ":" .. zone.materialSlotName .. zone.partName
    end
end
local function paint_equal(a, b)
    if a == json.null or a == nil then return b == json.null or b == nil end
    return type(b) == "table" and a.assetName == b.assetName and a.assetType == b.assetType
end

local function known_paint(paint)
    return paint == json.null or type(paint) == "table" and type(paint.assetType) == "string"
        and paint.assetType ~= "" and type(paint.assetName) == "string" and paint.assetName ~= ""
end

local function known_target(target)
    return type(target) == "table" and (target.kind == "character" or target.kind == "boat")
        and type(target.className) == "string" and target.className ~= ""
        and type(target.paintSetName) == "string" and target.paintSetName ~= ""
end

local function field(value) return #value .. ":" .. value end

-- A byte-exact lookup key for the same logical identities used by compare_layout.
-- Length prefixes keep arbitrary slot/part names unambiguous after sorting.
function M.key(target, zones)
    if not known_target(target) or type(zones) ~= "table" or #zones == 0 then return nil end
    local rows, seen = {}, {}
    for index, zone in ipairs(zones) do
        local id = zone_identity(zone)
        if not id or seen[id] or not known_paint(zone.paint) then return nil end
        seen[id] = true
        local paint = zone.paint
        rows[index] = field(id) .. (paint == json.null and "0" or "1" .. field(paint.assetType) .. field(paint.assetName))
    end
    table.sort(rows)
    return field(target.kind) .. field(target.className) .. field(target.paintSetName) .. table.concat(rows)
end

function M.compare_layout(target, preset)
    if type(target) ~= "table" or target.complete ~= true or not known_target(target.compatibility)
        or type(target.logicalZones) ~= "table" or #target.logicalZones == 0 then return "unknown" end
    local live = target.compatibility
    local saved = type(preset) == "table" and preset.target
    if type(saved) ~= "table" or live.kind ~= saved.kind or live.className ~= saved.className
        or live.paintSetName ~= saved.paintSetName or type(preset.zones) ~= "table"
        or #target.logicalZones ~= #preset.zones then return "incompatible" end

    local paints = {}
    for _, zone in ipairs(target.logicalZones) do
        local identity = zone_identity(zone)
        if not identity or paints[identity] or not known_paint(zone.paint) then return "unknown" end
        paints[identity] = zone.paint
    end
    local state = "match"
    for _, zone in ipairs(preset.zones) do
        local identity = zone_identity(zone)
        local current_paint = identity and paints[identity]
        if not current_paint or not known_paint(zone.paint) then return "incompatible" end
        paints[identity] = nil
        if not paint_equal(current_paint, zone.paint) then
            if current_paint ~= json.null then state = "replace"
            elseif state == "match" then state = "fill" end
        end
    end
    return state
end

M.zone_identity, M.paint_equal = zone_identity, paint_equal
return M
