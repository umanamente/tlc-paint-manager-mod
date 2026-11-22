-- Diagnostic JSON. Tables are objects unless marked as arrays. encode() is the
-- synchronous small-record helper; begin() streams large reports cooperatively.
local processing = require("paintmanager.processing")
local M = {}
local arrays = setmetatable({}, { __mode = "k" })
M.null = {}
function M.is_array(value) return arrays[value] == true end

function M.array(items)
    if items == nil then items = {} end
    if type(items) ~= "table" or items == M.null then
        error("JSON array requires a table", 2)
    end
    arrays[items] = true
    return items
end

local escapes = {
    ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
    ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
}

-- Lua's string comparison follows the process C locale. Persistent canonical
-- ordering uses raw UTF-8 bytes instead. A partial comparison examines at most
-- 1024 bytes and returns nil plus a cursor when the common prefix continues.
function M.compare_bytes(left, right, index, maximum)
    assert(type(left) == "string" and type(right) == "string", "Byte comparison requires strings")
    index, maximum = index or 1, maximum or processing.get("compare_bytes", 1024)
    assert(type(index) == "number" and index % 1 == 0 and index >= 1, "Invalid byte comparison cursor")
    assert(type(maximum) == "number" and maximum % 1 == 0 and maximum >= 1 and maximum <= 32768,
        "Invalid byte comparison limit")
    local length = math.max(#left, #right)
    local last = math.min(length, index + maximum - 1)
    for position = index, last do
        local a, b = left:byte(position), right:byte(position)
        if a ~= b then return (a == nil or b ~= nil and a < b) and -1 or 1 end
    end
    if last >= length then return 0 end
    return nil, last + 1
end
function M.byte_less(left, right)
    local index = 1
    while true do
        local order, next_index = M.compare_bytes(left, right, index)
        if order ~= nil then return order < 0 end
        index = next_index
    end
end

local function quote(value)
    if not utf8.len(value) then error("JSON string is not valid UTF-8") end
    return '"' .. value:gsub('[%z\1-\31\\"]', function(char)
        return escapes[char] or string.format('\\u%04x', string.byte(char))
    end) .. '"'
end

function M.encode(value)
    local active = {}
    local function encode(item, depth)
        if item == M.null or item == nil then return "null" end
        local kind = type(item)
        if kind == "boolean" then return item and "true" or "false" end
        if kind == "string" then return quote(item) end
        if kind == "number" then
            if item ~= item or item == math.huge or item == -math.huge then
                error("JSON number must be finite")
            end
            if math.type(item) == "integer" then return tostring(item) end
            return (string.format("%.17g", item):gsub(",", "."))
        end
        if kind ~= "table" then error("Unsupported JSON type: " .. kind) end
        if active[item] then error("JSON cannot contain cycles") end
        depth = depth + 1
        if depth > 32 then error("JSON nesting exceeds 32 tables") end
        active[item] = true
        local parts = {}
        if arrays[item] then
            local count, maximum = 0, 0
            for key in next, item do
                if type(key) ~= "number" or key % 1 ~= 0 or key < 1 then
                    error("JSON array keys must be positive integers")
                end
                count = count + 1
                if key > maximum then maximum = key end
            end
            if maximum ~= count then error("JSON array must be contiguous") end
            for index = 1, count do
                parts[index] = encode(rawget(item, index), depth)
            end
            active[item] = nil
            return "[" .. table.concat(parts, ",") .. "]"
        end
        local keys = {}
        for key in next, item do
            if type(key) ~= "string" then error("JSON object keys must be strings") end
            keys[#keys + 1] = key
        end
        table.sort(keys, M.byte_less)
        for index, key in ipairs(keys) do
            parts[index] = quote(key) .. ":" .. encode(rawget(item, key), depth)
        end
        active[item] = nil
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return encode(value, 0)
end

-- Reports stream this state machine instead of calling the synchronous encoder.
-- One step scans one key, emits one merge output or processes <=1024 text bytes.
local Job = {}
Job.__index = Job

local function positive_limit(value, fallback, maximum)
    if type(value) ~= "number" or value ~= value or value < 1 or value == math.huge then return fallback end
    return math.min(maximum, math.floor(value))
end

function M.begin(value, options)
    options = options or {}
    return setmetatable({ state = "running", phase = "encode", completed = 0, bytes = 0,
        string_bytes = positive_limit(options.string_bytes, processing.get("json_string_bytes", 1024), 32768),
        key_bytes = positive_limit(options.key_bytes, processing.get("json_key_bytes", 1024), 32768),
        stack = { { kind = "value", value = value, depth = 0 } }, active = {} }, Job)
end

function Job:_push(frame) self.stack[#self.stack + 1] = frame end
function Job:_pop() self.stack[#self.stack] = nil end

function Job:_value(frame)
    local value, kind = frame.value, type(frame.value)
    self:_pop()
    if value == M.null or value == nil then return "null" end
    if kind == "boolean" then return value and "true" or "false" end
    if kind == "number" then
        if value ~= value or value == math.huge or value == -math.huge then error("JSON number must be finite") end
        if math.type(value) == "integer" then return tostring(value) end
        return (string.format("%.17g", value):gsub(",", "."))
    end
    if kind == "string" then
        self:_push({ kind = "string", value = value, index = 1, remaining = 0 })
        return
    end
    if kind ~= "table" then error("Unsupported JSON type: " .. kind) end
    if self.active[value] then error("JSON cannot contain cycles") end
    local depth = frame.depth + 1
    if depth > 32 then error("JSON nesting exceeds 32 tables") end
    self.active[value] = true
    self:_push({ kind = "table", value = value, depth = depth, stage = "keys", keys = {},
        is_array = arrays[value] == true, count = 0, maximum = 0 })
    return arrays[value] and "[" or "{"
end

-- Preserve UTF-8 decoder state across chunk boundaries. Invalid late bytes can
-- reach only the temporary file; the writer never publishes a failed encoding.
local function check_byte(frame, byte)
    if frame.remaining > 0 then
        if byte < 128 or byte > 191 then error("JSON string is not valid UTF-8") end
        frame.codepoint = frame.codepoint * 64 + byte - 128
        frame.remaining = frame.remaining - 1
        if frame.remaining == 0 and (frame.codepoint < frame.minimum or frame.codepoint > 0x10ffff
            or frame.codepoint >= 0xd800 and frame.codepoint <= 0xdfff) then
            error("JSON string is not valid UTF-8")
        end
    elseif byte < 128 then return
    elseif byte >= 194 and byte <= 223 then
        frame.remaining, frame.codepoint, frame.minimum = 1, byte - 192, 128
    elseif byte >= 224 and byte <= 239 then
        frame.remaining, frame.codepoint, frame.minimum = 2, byte - 224, 2048
    elseif byte >= 240 and byte <= 244 then
        frame.remaining, frame.codepoint, frame.minimum = 3, byte - 240, 65536
    else error("JSON string is not valid UTF-8") end
end

function Job:_string(frame)
    if not frame.started then frame.started = true; return '"' end
    if frame.index > #frame.value then
        if frame.remaining ~= 0 then error("JSON string is not valid UTF-8") end
        self:_pop()
        return '"'
    end
    local finish = math.min(#frame.value, frame.index + self.string_bytes - 1)
    local parts = {}
    for index = frame.index, finish do
        local byte = frame.value:byte(index)
        check_byte(frame, byte)
        local char = frame.value:sub(index, index)
        parts[#parts + 1] = escapes[char] or (byte < 32 and string.format("\\u%04x", byte) or char)
    end
    frame.index = finish + 1
    return table.concat(parts)
end

function Job:_keys(frame)
    local key = next(frame.value, frame.last_key)
    if key ~= nil then
        frame.last_key = key
        if frame.is_array then
            if type(key) ~= "number" or key < 1 or key % 1 ~= 0 then
                error("JSON array keys must be positive integers")
            end
            frame.count = frame.count + 1
            frame.maximum = math.max(frame.maximum, key)
        else
            if type(key) ~= "string" then error("JSON object keys must be strings") end
            frame.keys[#frame.keys + 1] = key
        end
        return
    end
    if frame.is_array then
        if frame.maximum ~= frame.count then error("JSON array must be contiguous") end
        frame.stage, frame.index = "emit", 1
    elseif #frame.keys < 2 then
        frame.stage, frame.index = "emit", 1
    else
        frame.source, frame.keys, frame.target = frame.keys, nil, {}
        frame.stage, frame.width, frame.left = "sort", 1, 1
    end
end

-- Compare at most key_bytes bytes. Long common key prefixes yield between units.
-- Byte ordering is deterministic and independent of the process locale.
function Job:_key_before(frame, left, right)
    local index = frame.compare_index or 1
    local last = math.min(math.max(#left, #right), index + self.key_bytes - 1)
    for position = index, last do
        local a, b = left:byte(position), right:byte(position)
        if a ~= b then frame.compare_index = nil; return a == nil or b ~= nil and a < b end
    end
    if last >= math.max(#left, #right) then frame.compare_index = nil; return true end
    frame.compare_index = last + 1
    return nil
end

function Job:_sort(frame)
    local count = #frame.source
    if not frame.output then
        frame.middle = math.min(frame.left + frame.width - 1, count)
        frame.right = math.min(frame.left + frame.width * 2 - 1, count)
        frame.i, frame.j, frame.output = frame.left, frame.middle + 1, frame.left
    end
    local take_left = frame.i <= frame.middle
    if take_left and frame.j <= frame.right then
        take_left = self:_key_before(frame, frame.source[frame.i], frame.source[frame.j])
        if take_left == nil then return end
    end
    if take_left then frame.target[frame.output], frame.i = frame.source[frame.i], frame.i + 1
    else frame.target[frame.output], frame.j = frame.source[frame.j], frame.j + 1 end
    frame.output = frame.output + 1
    if frame.output > frame.right then
        frame.left, frame.output = frame.left + frame.width * 2, nil
        if frame.left > count then
            frame.source, frame.target = frame.target, {}
            frame.width, frame.left = frame.width * 2, 1
            if frame.width >= count then
                frame.keys, frame.source, frame.target = frame.source, nil, nil
                frame.stage, frame.index = "emit", 1
            end
        end
    end
end

function Job:_emit_table(frame)
    local count = frame.is_array and frame.count or #frame.keys
    if frame.index > count then
        self.active[frame.value] = nil
        self:_pop()
        return frame.is_array and "]" or "}"
    end
    local index = frame.index
    frame.index = index + 1
    local key = frame.is_array and index or frame.keys[index]
    self:_push({ kind = "value", value = rawget(frame.value, key), depth = frame.depth })
    if not frame.is_array then
        self:_push({ kind = "literal", value = ":" })
        self:_push({ kind = "string", value = key, index = 1, remaining = 0 })
    end
    return index > 1 and "," or nil
end

function Job:_advance()
    local frame = self.stack[#self.stack]
    if not frame then self.state, self.phase = "done", "done"; return end
    if frame.kind == "value" then return self:_value(frame) end
    if frame.kind == "string" then return self:_string(frame) end
    if frame.kind == "literal" then self:_pop(); return frame.value end
    if frame.stage == "keys" then return self:_keys(frame) end
    if frame.stage == "sort" then return self:_sort(frame) end
    return self:_emit_table(frame)
end

function Job:_progress(chunk)
    return { state = self.state, phase = self.phase, completed = self.completed,
        bytes = self.bytes, chunk = chunk, error = self.error }
end

function Job:step_one()
    if self.state ~= "running" then return self:_progress() end
    local ok, chunk = pcall(self._advance, self)
    self.completed = self.completed + 1
    if not ok then
        self.state, self.phase, self.error = "error", "error", tostring(chunk)
        self.stack, self.active = nil, nil
        return self:_progress()
    end
    if chunk then self.bytes = self.bytes + #chunk end
    return self:_progress(chunk)
end

function Job:cancel(reason)
    if self.state ~= "running" then return end
    self.state, self.phase, self.error = "cancelled", "cancelled", reason
    self.stack, self.active = nil, nil
end

return M
