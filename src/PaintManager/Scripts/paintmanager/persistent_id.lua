-- Preserve every UInt64 bit through the pinned loader's signed Lua64 integer
-- representation. Persistent JSON identities are strings, never JSON numbers.
local M = {}

local function hex(value)
    return type(value) == "string" and #value == 16 and value:match("^[0-9a-f]+$") ~= nil
end

function M.is_valid(value)
    return hex(value) and value ~= "0000000000000000"
end

function M.encode(value)
    assert(math.type(value) == "integer", "Native persistent identity must be a Lua64 integer")
    return string.format("%016x", value)
end

function M.decode(value)
    assert(hex(value), "Persistent identity must be canonical UInt64 hexadecimal")
    local result = 0
    for index = 1, 16 do
        local byte = value:byte(index)
        local digit = byte <= 57 and byte - 48 or byte - 87
        -- Lua integer bitwise shifts preserve the unsigned bit pattern. The
        -- returned signed representation is exactly what the loader supplies.
        result = (result << 4) | digit
    end
    return result
end

return M
