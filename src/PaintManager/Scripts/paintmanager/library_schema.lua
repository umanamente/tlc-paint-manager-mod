-- Incremental strict logical-preset validation and independent copies. Exact
-- byte accounting avoids serializing a library that its parent will encode.
local json = require("paintmanager.json")
local reader = require("paintmanager.json_reader")
local M = { VERSION = 2, MAX_PRESETS = 64, MAX_TOMBSTONES = 256 }
local Job = {}; Job.__index = Job
local MAX_INTEGER = 9007199254740991
local roots = { [1] = { schemaVersion = true, presets = true },
    [2] = { schemaVersion = true, presets = true, tombstones = true } }
local records = { id = true, name = true, createdUtc = true, target = true, zones = true,
    gameBuild = true, updatedAt = true }
local target_keys = { kind = true, className = true, paintSetName = true, displayName = true }
local zone_keys = { materialSlotName = true, partName = true, paint = true, displayName = true }
local paint_keys = { assetType = true, assetName = true, displayName = true }
local function object(value, keys)
    assert(type(value) == "table" and value ~= json.null and getmetatable(value) == nil
        and not reader.is_array(value) and not json.is_array(value), "Expected a library object")
    for key in next, value do assert(keys[key], "Unexpected library field: " .. tostring(key)) end
end
local function text(value, maximum, empty)
    assert(type(value) == "string" and #value <= maximum and (empty or #value > 0)
        and not value:find("[%z\1-\31\127]") and utf8.len(value), "Invalid preset text")
    return value
end
local function optional_text(value, maximum)
    if value ~= nil then return text(value, maximum, true) end
end
local function asset(value, maximum)
    value = text(value, maximum)
    assert(value:match("^[A-Za-z0-9_.%-]+$"), "Invalid persistent asset identity")
    return value
end
local function timestamp(value)
    assert(type(value) == "number" and value == value and value % 1 == 0
        and value >= 0 and value <= MAX_INTEGER, "Invalid library timestamp")
    return value
end
local function static_class(value)
    value = text(value, 512)
    assert((value:sub(1,8) == "/Script/" or value:sub(1,6) == "/Game/")
        and value:match("^/[A-Za-z0-9_./]+$") and not value:find("..",1,true), "Invalid static target class")
    return value
end
local function quoted_size(value)
    local size = #value + 2
    for index = 1, #value do
        local byte = value:byte(index)
        if byte == 34 or byte == 92 then size = size + 1 end
    end
    return size
end
local function number_size(value)
    if math.type(value) == "integer" then return #tostring(value) end
    return #(string.format("%.17g",value):gsub(",","."))
end
local function object_size(fields)
    local size, count = 2, 0
    for key, bytes in pairs(fields) do
        size, count = size + #key + 3 + bytes + (count > 0 and 1 or 0), count + 1
    end
    return size
end
local function sizes(value)
    local result = {}
    for key, item in pairs(value) do result[key] = type(item) == "number" and number_size(item) or quoted_size(item) end
    return result
end
function M.begin_validate(value, options)
    options = options or {}
    local maximum = options.max_bytes
    if type(maximum) ~= "number" or maximum ~= maximum or maximum < 1 or maximum == math.huge then maximum = 32768 end
    return setmetatable({ state = "running", phase = "root", input = value, options = options,
        completed = 0, ids = {}, deleted = {}, names = {}, result = { schemaVersion = 2,
            presets = json.array(), tombstones = json.array() }, max_bytes = math.min(math.floor(maximum),32768),
        bytes = object_size({ schemaVersion = 1, presets = 2, tombstones = 2 }) }, Job)
end
function Job:_count(bytes)
    self.bytes = self.bytes + bytes
    assert(self.bytes <= self.max_bytes, "Preset output exceeds the byte limit")
end
function Job:_array(value, maximum, after)
    assert(type(value) == "table" and value ~= json.null and getmetatable(value) == nil, "Expected a library array")
    if self.decoded then assert(reader.is_array(value) or json.is_array(value), "Expected a JSON library array") end
    self.array, self.last, self.count, self.maximum, self.limit = value, nil, 0, 0, maximum
    self.after_array, self.phase = after, "array"
end
function Job:_advance()
    if self.phase == "root" then
        assert(type(self.input) == "table" and self.input ~= json.null and getmetatable(self.input) == nil
            and not json.is_array(self.input) and not reader.is_array(self.input), "Expected a preset library")
        self.version = self.input.schemaVersion
        assert(self.version == 1 or self.version == 2, "Unsupported preset schema version")
        object(self.input, roots[self.version])
        self.migrated = self.version == 1
        self.record_index = 1
        self:_count(0)
        self:_array(self.input.presets, M.MAX_PRESETS, "presets")
    elseif self.phase == "array" then
        local key = next(self.array, self.last)
        if key then
            assert(type(key) == "number" and key % 1 == 0 and key >= 1 and key <= self.limit,
                "Invalid library array index or limit")
            self.count, self.maximum, self.last = self.count + 1, math.max(self.maximum,key), key
        else
            assert(self.count == self.maximum, "Library arrays must be contiguous")
            if self.after_array == "zones" then self.zone_count = self.count end
            self.array, self.phase = nil, self.after_array
        end
    elseif self.phase == "presets" then
        local record = self.input.presets[self.record_index]
        if not record then
            self.marker_index = 1
            if self.version == 1 then self.state, self.phase, self.input = "done", "done", nil
            else self:_array(self.input.tombstones,M.MAX_TOMBSTONES,"tombstones") end
            return
        end
        object(record,records); object(record.target,target_keys)
        assert(self.version ~= 1 or record.updatedAt == nil, "Unexpected legacy preset timestamp")
        local id, name = asset(record.id,96), text(record.name,128)
        assert(not self.ids[id], "Duplicate preset ID")
        if self.version == 1 then assert(not self.names[name:lower()], "Duplicate legacy preset name") end
        self.ids[id], self.names[name:lower()] = true, true
        local target = record.target
        assert(target.kind == "character" or target.kind == "boat", "Invalid preset target kind")
        local created = text(record.createdUtc,64)
        assert(created:match("^%d%d%d%d%-%d%d%-%d%d[T ]%d%d:%d%d:%d%d"), "Invalid preset creation timestamp")
        local build = optional_text(record.gameBuild,16)
        if build then assert(build:match("^%d+$"), "Invalid preset game build") end
        self.current = { id = id, name = name, createdUtc = created, gameBuild = build,
            updatedAt = self.version == 1 and 0 or timestamp(record.updatedAt),
            target = { kind = target.kind, className = static_class(target.className),
                paintSetName = text(target.paintSetName,256,true), displayName = optional_text(target.displayName,256) }, zones = json.array() }
        local fields = sizes({ id = id, name = name, createdUtc = created, gameBuild = build, updatedAt = self.current.updatedAt })
        fields.target, fields.zones = object_size(sizes(self.current.target)), 2
        self:_count(object_size(fields) + (self.record_index > 1 and 1 or 0))
        self.zone_names, self.zone_index = {}, 1
        self:_array(record.zones,128,"zones")
    elseif self.phase == "zones" then
        if self.zone_index > self.zone_count then
            assert(self.zone_count > 0, "A preset requires at least one configured zone")
            self.result.presets[#self.result.presets+1] = self.current
            self.current, self.record_index, self.phase = nil, self.record_index+1, "presets"
            return
        end
        local zone = self.input.presets[self.record_index].zones[self.zone_index]
        object(zone,zone_keys)
        local material, part = text(zone.materialSlotName,256,true), text(zone.partName,256,true)
        assert(material ~= "" or part ~= "", "A preset zone requires a logical identity")
        local key = #material..":"..material..part
        assert(not self.zone_names[key], "Duplicate logical preset zone");self.zone_names[key] = true
        local paint = json.null
        if zone.paint ~= json.null then
            object(zone.paint,paint_keys)
            paint = { assetType = asset(zone.paint.assetType,64), assetName = asset(zone.paint.assetName,256),
                displayName = optional_text(zone.paint.displayName,256) }
        end
        local copied = { materialSlotName = material, partName = part, displayName = optional_text(zone.displayName,256), paint = paint }
        local fields = sizes({ materialSlotName = material, partName = part, displayName = copied.displayName })
        fields.paint = paint == json.null and 4 or object_size(sizes(paint))
        self:_count(object_size(fields)+(self.zone_index > 1 and 1 or 0))
        self.current.zones[#self.current.zones+1] = copied
        self.zone_index = self.zone_index+1
    elseif self.phase == "tombstones" then
        local marker = self.input.tombstones[self.marker_index]
        if not marker then self.state, self.phase, self.input = "done", "done", nil;return end
        object(marker,{ id = true, deletedAt = true })
        local id = asset(marker.id,96)
        assert(not self.deleted[id] and not self.ids[id], "Duplicate or live deletion ID")
        self.deleted[id] = true
        local copied = { id = id, deletedAt = timestamp(marker.deletedAt) }
        self:_count(object_size(sizes(copied))+(self.marker_index > 1 and 1 or 0))
        self.result.tombstones[#self.result.tombstones+1] = copied
        self.marker_index = self.marker_index+1
    end
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, completed = self.completed, processed = self.completed,
        error = self.error, migrated = self.migrated == true, bytes = self.bytes }
end
function Job:step_one()
    if self.state ~= "running" then return self:get_progress() end
    local ok,err = pcall(self._advance,self)
    self.completed = self.completed+1
    if not ok then self.state,self.phase,self.error,self.result,self.input = "error","error",tostring(err),nil,nil end
    return self:get_progress()
end
function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do self:step_one();budget:consume(1) end
    return self:get_progress()
end
function Job:get_result() return self.state == "done" and self.result or nil end
function Job:cancel(reason)
    if self.state == "running" then self.state,self.phase,self.error,self.result,self.input = "cancelled","cancelled",reason,nil,nil end
    return self:get_progress()
end
return M
