local safe = require("paintmanager.safe")

local function integer(value, low, high)
    return math.type(value) == "integer" and value >= low and value <= high
end

-- Canonical native payload, without a lossy hash or numeric coercion of int64.
-- The native copy updates Version. Count and the runtime slot are checked separately.
return function(data)
    assert(math.type(data.Timestamp) == "integer" and integer(data.Flags,0,255))
    local remaining = 440
    local function hex(bytes)
        local size = #bytes
        assert(integer(size,0,remaining))
        if size == 0 then return "" end
        remaining = remaining - size
        local parts = {}
        bytes:ForEach(function(_,wrapped)
            local byte = safe.unwrap(wrapped)
            assert(integer(byte,0,255) and #parts < size)
            parts[#parts+1] = string.format("%02x",byte)
        end)
        assert(#parts == size)
        return table.concat(parts)
    end
    local raw = hex(data.ItemData)
    local entries = {}
    local map = data.ItemDataMap
    local size = #map
    assert(integer(size,0,16))
    if size == 0 then
        return string.format("%016x:%02x:%s:",data.Timestamp,data.Flags,raw),raw == ""
    end
    map:ForEach(function(k,v)
        local key = safe.text(safe.unwrap(k))
        assert(key and #key <= remaining and #entries < size)
        remaining = remaining - #key
        local encoded = key:gsub(".",function(c) return string.format("%02x",c:byte()) end)
        entries[#entries+1] = encoded .. "=" .. hex(safe.unwrap(v).Data)
    end)
    assert(#entries == size)
    table.sort(entries)
    return string.format("%016x:%02x:%s:%s",data.Timestamp,data.Flags,raw,table.concat(entries,"/")),
        raw == "" and size == 0
end
