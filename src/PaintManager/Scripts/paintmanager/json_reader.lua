-- Strict, cooperative JSON reader for small mod-owned persistent files.
-- Parser byte slices are scheduling policy; validity limits remain independent.
local processing = require("paintmanager.processing")
local json = require("paintmanager.json")
local M = {}
local Job = {}
Job.__index = Job
local array_values = setmetatable({}, { __mode = "k" })
function M.is_array(value) return array_values[value] == true end

local function limit(value, default, maximum)
    if type(value) ~= "number" or value ~= value or value < 1 or value == math.huge then return default end
    return math.min(math.floor(value), maximum)
end

function M.begin(text, options)
    options = options or {}
    local job = setmetatable({ state = "running", phase = "parse", completed = 0,
        text = text, index = 1, stack = {}, nodes = 0,
        max_bytes = limit(options.max_bytes, 1048576, 1048576),
        max_depth = limit(options.max_depth, 16, 32),
        max_nodes = limit(options.max_nodes, 50000, 100000),
        max_string = limit(options.max_string, 4096, 65536),
        text_bytes = limit(options.text_bytes, processing.get("json_read_bytes", 256), 32768) }, Job)
    if type(text) ~= "string" or #text > job.max_bytes then
        job.state, job.phase, job.error = "error", "error", "JSON input exceeds the byte limit or is not text"
        job.text = nil
    end
    return job
end

function Job:_accept(value)
    local frame = self.stack[#self.stack]
    if not frame then
        if self.has_root then error("Multiple JSON values") end
        self.root, self.has_root = value, true
    elseif frame.kind == "array" then
        frame.value[#frame.value + 1] = value
        frame.stage = "comma"
    else
        frame.value[frame.key] = value
        frame.key, frame.stage = nil, "comma"
    end
end

function Job:_start_string(is_key)
    self.token = { kind = "string", pieces = {}, size = 0, key = is_key }
    self.index = self.index + 1
end

function Job:_text(piece)
    local token = self.token
    token.size = token.size + #piece
    if token.size > self.max_string then error("JSON string exceeds the length limit") end
    token.pieces[#token.pieces + 1] = piece
end

local escaped = { ['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f', n = '\n', r = '\r', t = '\t' }
function Job:_string()
    local token = self.token
    local finish = math.min(#self.text, self.index + self.text_bytes - 1)
    local start = self.index
    while self.index <= finish do
        local byte = self.text:byte(self.index)
        if byte == 34 or byte == 92 or byte < 32 then break end
        self.index = self.index + 1
    end
    if self.index > start then self:_text(self.text:sub(start, self.index - 1)); return end
    local char = self.text:sub(self.index, self.index)
    if char == '"' then
        self.index = self.index + 1
        local value = table.concat(token.pieces)
        if not utf8.len(value) then error("JSON string is not valid UTF-8") end
        self.token = nil
        if token.key then
            local frame = self.stack[#self.stack]
            if frame.keys[value] then error("Duplicate JSON object key") end
            frame.keys[value], frame.key, frame.stage = true, value, "colon"
        else self:_accept(value) end
    elseif char == "\\" then
        local code = self.text:sub(self.index + 1, self.index + 1)
        if escaped[code] then self:_text(escaped[code]); self.index = self.index + 2
        elseif code == "u" then
            local digits = self.text:sub(self.index + 2, self.index + 5)
            if #digits ~= 4 or not digits:match("^%x%x%x%x$") then error("Invalid JSON Unicode escape") end
            local point = tonumber(digits, 16)
            self.index = self.index + 6
            if point >= 0xd800 and point <= 0xdbff then
                local second = self.text:sub(self.index, self.index + 5)
                if not second:match("^\\u%x%x%x%x$") then error("Missing JSON low surrogate") end
                local low = tonumber(second:sub(3), 16)
                if low < 0xdc00 or low > 0xdfff then error("Invalid JSON low surrogate") end
                point = 0x10000 + (point - 0xd800) * 1024 + low - 0xdc00
                self.index = self.index + 6
            elseif point >= 0xdc00 and point <= 0xdfff then error("Unexpected JSON low surrogate") end
            self:_text(utf8.char(point))
        else error("Invalid JSON escape") end
    else error("Unterminated JSON string or unescaped control byte") end
end

function Job:_value()
    local char = self.text:sub(self.index, self.index)
    self.nodes = self.nodes + 1
    if self.nodes > self.max_nodes then error("JSON node limit exceeded") end
    if char == '"' then self:_start_string(false); return end
    if char == "[" or char == "{" then
        if #self.stack >= self.max_depth then error("JSON nesting limit exceeded") end
        local value = char == "[" and json.array() or {}
        if char == "[" then array_values[value] = true end
        self:_accept(value)
        self.stack[#self.stack + 1] = { kind = char == "[" and "array" or "object", value = value,
            stage = char == "[" and "first" or "firstkey", keys = {} }
        self.index = self.index + 1
        return
    end
    for literal, value in pairs({ ["true"] = true, ["false"] = false, ["null"] = json.null }) do
        if self.text:sub(self.index, self.index + #literal - 1) == literal then
            self.index = self.index + #literal; self:_accept(value); return
        end
    end
    if char == "-" or char:match("%d") then
        self.token = { kind = "number", start = self.index }
        return
    end
    error("Expected a JSON value")
end

function Job:_number()
    local start = self.token.start
    local finish = self.index
    while finish <= #self.text and self.text:sub(finish, finish):match("[0-9eE+%.%-]") do
        finish = finish + 1
        if finish - start > 128 then error("JSON number exceeds the length limit") end
    end
    local value = self.text:sub(start, finish - 1)
    local integer, fraction, exponent = value:match("^(%-?%d+)(%.%d+)([eE][%+%-]?%d+)$")
    if not integer then integer, fraction = value:match("^(%-?%d+)(%.%d+)$") end
    if not integer then integer, exponent = value:match("^(%-?%d+)([eE][%+%-]?%d+)$") end
    if not integer then integer = value:match("^(%-?%d+)$") end
    if not integer or integer:match("^%-?0%d") then error("Invalid JSON number") end
    local number = tonumber(value)
    if not number or number ~= number or number == math.huge or number == -math.huge then error("JSON number must be finite") end
    self.index, self.token = finish, nil
    self:_accept(number)
end

function Job:_advance()
    if self.token then
        if self.token.kind == "string" then return self:_string() end
        return self:_number()
    end
    local finish = math.min(#self.text, self.index + self.text_bytes - 1)
    local first = self.index
    while self.index <= finish and self.text:sub(self.index, self.index):match("[ \r\n\t]") do self.index = self.index + 1 end
    if self.index > first then return end
    local frame = self.stack[#self.stack]
    if not frame then
        if self.has_root then
            if self.index <= #self.text then error("Unexpected trailing JSON content") end
            self.state, self.phase, self.result, self.text = "done", "done", self.root, nil
            self.root = nil
            return
        end
        return self:_value()
    end
    local char = self.text:sub(self.index, self.index)
    local closing = frame.kind == "array" and "]" or "}"
    if (frame.stage == "comma" or frame.stage == "first" or frame.stage == "firstkey") and char == closing then
        self.stack[#self.stack], self.index = nil, self.index + 1; return
    end
    if frame.stage == "comma" then
        if char ~= "," then error("Expected JSON comma or closing delimiter") end
        frame.stage = frame.kind == "array" and "value" or "key"
        self.index = self.index + 1
    elseif frame.stage == "key" or frame.stage == "firstkey" then
        if char ~= '"' then error("Expected a JSON object key") end
        self:_start_string(true)
    elseif frame.stage == "colon" then
        if char ~= ":" then error("Expected JSON colon") end
        self.index, frame.stage = self.index + 1, "value"
    else self:_value() end
end

function Job:get_progress()
    return { state = self.state, phase = self.phase, completed = self.completed,
        processed = self.completed, bytesRead = self.index - 1, nodes = self.nodes, error = self.error }
end
function Job:step_one()
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(self._advance, self)
    self.completed = self.completed + 1
    if not ok then
        self.state, self.phase, self.error = "error", "error", tostring(err)
        self.text, self.root, self.stack, self.token = nil, nil, nil, nil
    end
    return self:get_progress()
end
function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do self:step_one(); budget:consume(1) end
    return self:get_progress()
end
function Job:get_result() if self.state == "done" then return self.result end end
function Job:cancel(reason)
    if self.state ~= "running" then return end
    self.state, self.phase, self.error = "cancelled", "cancelled", reason
    self.text, self.root, self.stack, self.token = nil, nil, nil, nil
end
return M
