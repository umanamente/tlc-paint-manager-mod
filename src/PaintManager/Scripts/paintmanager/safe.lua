local M = {}

function M.valid(value)
    if value == nil then return false end
    local ok, result = pcall(function() return value:IsValid() end)
    return ok and result == true
end

function M.text(value)
    local kind = type(value)
    if kind == "string" then return value end
    if kind == "number" or kind == "boolean" then return tostring(value) end
    if value == nil then return nil end
    local ok, result = pcall(function() return value:ToString() end)
    if ok and type(result) == "string" then return result end
    return nil
end

function M.name(value)
    if not M.valid(value) then return nil end
    local ok, result = pcall(function() return value:GetFullName() end)
    if ok and type(result) == "string" then return result end
    return nil
end

function M.get(value, key)
    local ok, result = pcall(function() return value[key] end)
    if ok then return result end
    return nil, tostring(result)
end

function M.unwrap(value)
    if value == nil then return nil end
    local kind = type(value)
    if kind ~= "userdata" and kind ~= "table" then return value end
    local ok, result = pcall(function() return value:get() end)
    if ok then return result end
    return value
end

function M.class(value)
    if not M.valid(value) then return nil end
    local ok, result = pcall(function() return value:GetClass() end)
    if ok and M.valid(result) then return result end
    return nil
end

function M.short_name(value)
    local ok, result = pcall(function() return value:GetFName():ToString() end)
    if ok and type(result) == "string" then return result end
    return nil
end

function M.distance_cm(a, b)
    if a == nil or b == nil then return nil end
    local ok, distance = pcall(function()
        return math.sqrt((a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2)
    end)
    if ok and type(distance) == "number" and distance == distance and distance < math.huge then
        return distance
    end
    return nil
end

-- A discovery hint only: never an authorization to consume or unlock an item.
function M.paint_hint(asset_name, item_path, class_path)
    local id = (asset_name or ""):lower()
    local path = (item_path or ""):lower()
    local class = (class_path or ""):lower()
    if id:match("^da_paint_") then return "asset_name" end
    if path:find("/paints/", 1, true) then return "asset_path" end
    -- Exact class observed in the first live report. Tooltip/grid painters and
    -- similarly named objects are not paint items. Subclasses need further type checks.
    if class:match("/script/voyage%.voyagepaintitem$") then return "native_class" end
    return nil
end

return M
