local safe = require("paintmanager.safe")
local json = require("paintmanager.json")
local M = {}

local terms = { "paint", "skin", "customiz", "research", "unlock", "vault", "scanned" }
local scalar_types = {
    BoolProperty = true, IntProperty = true, Int64Property = true,
    UInt32Property = true, FloatProperty = true, DoubleProperty = true,
    StrProperty = true, NameProperty = true, TextProperty = true,
    ByteProperty = true, EnumProperty = true,
}

function M.relevant(name)
    local lower = (name or ""):lower()
    for _, term in ipairs(terms) do
        if lower:find(term, 1, true) then return true end
    end
    return false
end

local function property_info(property)
    local result = { name = safe.short_name(property) or "<unknown>" }
    local ok, full = pcall(function() return property:GetFullName() end)
    if ok and type(full) == "string" then result.descriptor = full end
    local type_ok, type_name = pcall(function()
        return property:GetClass():GetFName():ToString()
    end)
    if type_ok and type(type_name) == "string" then result.type = type_name end
    return result
end

-- The reference loader consumes boolean callback results twice on continuation.
-- Continue with nil; only true is safe as an explicit early-stop result.
-- Metadata only. Function fields can include locals; Lua has no CPF_Parm flags
-- on this loader. These descriptors must not be used to invoke a function.
function M.contract(class, config)
    local result = { class = safe.name(class) or "<invalid>", hierarchy = json.array(), complete = true }
    if not safe.valid(class) then
        result.complete = false
        result.error = "Class unavailable"
        return result
    end
    local seen = {}
    local current = class
    for _ = 1, config.max_super_depth do
        local name = safe.name(current)
        if name == nil or seen[name] then break end
        seen[name] = true
        local level = { class = name, properties = json.array(), functions = json.array() }
        result.hierarchy[#result.hierarchy + 1] = level
        local ok, err = pcall(function()
            current:ForEachProperty(function(property)
                if #level.properties >= config.max_properties then
                    level.propertiesTruncated = true
                    result.complete = false
                    return true
                end
                level.properties[#level.properties + 1] = property_info(property)
                return
            end)
        end)
        if not ok then
            level.propertyError = tostring(err)
            result.complete = false
        end
        ok, err = pcall(function()
            current:ForEachFunction(function(fn)
                if #level.functions >= config.max_functions then
                    level.functionsTruncated = true
                    result.complete = false
                    return true
                end
                local entry = { name = safe.name(fn) or "<unknown>", fields = json.array() }
                level.functions[#level.functions + 1] = entry
                local fields_ok, fields_err = pcall(function()
                    fn:ForEachProperty(function(property)
                        if #entry.fields >= config.max_properties then
                            entry.fieldsTruncated = true
                            result.complete = false
                            return true
                        end
                        entry.fields[#entry.fields + 1] = property_info(property)
                        return
                    end)
                end)
                if not fields_ok then
                    entry.fieldError = tostring(fields_err)
                    result.complete = false
                end
                return
            end)
        end)
        if not ok then
            level.functionError = tostring(err)
            result.complete = false
        end
        local super_ok, super = pcall(function() return current:GetSuperStruct() end)
        if not super_ok then
            result.complete = false
            result.superError = tostring(super)
            break
        end
        current = super
    end
    if safe.valid(current) and not seen[safe.name(current)] then
        result.hierarchyTruncated = true
        result.complete = false
    end
    return result
end

-- Reads only reflected scalar fields with relevant names. Complex values are
-- listed as descriptors instead of recursively walking native memory.
function M.observations(object, config)
    local result = json.array()
    local status = { complete = true, errors = json.array() }
    local current = safe.class(object)
    if not safe.valid(current) then
        status.complete = false
        status.errors[#status.errors + 1] = "Object class unavailable"
        return result, status
    end
    local seen_classes, seen_fields = {}, {}
    for _ = 1, config.max_super_depth do
        local class_name = safe.name(current)
        if class_name == nil or seen_classes[class_name] then break end
        seen_classes[class_name] = true
        local properties_ok, properties_error = pcall(function()
            current:ForEachProperty(function(property)
                if #result >= config.max_properties then
                    status.complete = false
                    status.propertiesTruncated = true
                    return true
                end
                local info = property_info(property)
                if M.relevant(info.name) and not seen_fields[info.name] then
                    seen_fields[info.name] = true
                    info.ownerClass = class_name
                    if scalar_types[info.type] then
                        local value, err = safe.get(object, info.name)
                        local kind = type(value)
                        if kind == "number" and value == value and math.abs(value) < math.huge then
                            info.value = value
                        elseif kind == "boolean" or kind == "string" then
                            info.value = value
                        else
                            info.value = safe.text(value)
                        end
                        if err then
                            info.readError = err
                            status.complete = false
                        end
                    else
                        info.valueNotRead = "complex_property"
                    end
                    result[#result + 1] = info
                end
                return
            end)
        end)
        if not properties_ok then
            status.complete = false
            status.errors[#status.errors + 1] = tostring(properties_error)
        end
        local ok, parent = pcall(function() return current:GetSuperStruct() end)
        if not ok then
            status.complete = false
            status.errors[#status.errors + 1] = tostring(parent)
            break
        end
        current = parent
    end
    if safe.valid(current) and not seen_classes[safe.name(current)] then
        status.complete = false
        status.hierarchyTruncated = true
    end
    return result, status
end

function M.discover(config)
    local result = {
        classes = json.array(), relatedFunctions = json.array(), errors = json.array(),
        objectsVisited = 0, objectsInspected = 0,
        complete = true,
        note = "Loaded objects only; output/work capped. ForEachUObject traversal cannot be stopped on the reference loader.",
        signatureLimit = "Function fields are descriptors, not verified parameter/return signatures.",
    }
    if type(ForEachUObject) ~= "function" then
        result.error = "ForEachUObject unavailable"
        result.complete = false
        return result
    end
    local started = os.clock()
    local classes_seen = {}
    local function add_class(class)
        local name = safe.name(class)
        if name == nil or classes_seen[name] then return end
        classes_seen[name] = true
        if #result.classes >= config.max_classes then
            result.classesTruncated = true
            result.complete = false
            return
        end
        local contract = M.contract(class, config)
        if not contract.complete then result.complete = false end
        result.classes[#result.classes + 1] = contract
    end
    local ok, err = pcall(function()
        ForEachUObject(function(object)
            result.objectsVisited = result.objectsVisited + 1
            if result.objectsInspected >= config.max_discovery_objects
                or os.clock() - started > config.max_discovery_seconds then
                result.workTruncated = true
                result.complete = false
                return -- Pinned loader ignores callback return: do no further work.
            end
            result.objectsInspected = result.objectsInspected + 1
            local item_ok, item_err = pcall(function()
                if not safe.valid(object) then return end
                if object:IsAnyClass() then
                    local name = safe.name(object)
                    if name and name:find("Voyage", 1, true) and M.relevant(name) then
                        add_class(object)
                    end
                elseif object:IsA("/Script/CoreUObject.Function") then
                    local name = safe.name(object)
                    if name and name:find("Voyage", 1, true) and M.relevant(name) then
                        if #result.relatedFunctions < config.max_functions then
                            result.relatedFunctions[#result.relatedFunctions + 1] = name
                        else
                            result.functionsTruncated = true
                            result.complete = false
                        end
                    end
                end
            end)
            if not item_ok and #result.errors < 8 then
                result.errors[#result.errors + 1] = tostring(item_err)
                result.complete = false
            end
        end)
    end)
    if not ok then
        result.error = tostring(err)
        result.complete = false
    end
    -- Always include known base contracts even if global discovery exhausted its budget.
    for _, path in ipairs({
        "/Script/Voyage.VoyageItem", "/Script/Voyage.VoyageBaseInventoryComponent",
        "/Script/Voyage.VoyageBaseCharacter", "/Script/Voyage.VoyageCharacter",
        "/Script/Voyage.VoyagePlayerController",
        "/Script/Voyage.VoyagePaintItem", "/Script/Voyage.VoyagePaintInventoryComponent",
    }) do
        local found, object = pcall(function() return StaticFindObject(path) end)
        if found and safe.valid(object) then add_class(object) end
    end
    result.cpuSeconds = os.clock() - started
    return result
end

return M
