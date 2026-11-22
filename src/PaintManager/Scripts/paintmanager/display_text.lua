-- Case folding for names and search in the supported interface languages.
local M = {}
local lowercase = {}

local function add_range(first, last, offset)
    for upper = first, last do
        lowercase[utf8.char(upper)] = utf8.char(upper + offset)
    end
end

add_range(0x41, 0x5A, 0x20)   -- ASCII
add_range(0xC0, 0xD6, 0x20)   -- Accented Latin capitals, skipping the multiplication sign
add_range(0xD8, 0xDE, 0x20)
add_range(0x410, 0x42F, 0x20) -- Basic Cyrillic

-- Polish letters, French OE, Finnish S/Z with caron and Ukrainian ghe with upturn.
for _, upper in ipairs({0x104, 0x106, 0x118, 0x141, 0x143, 0x152, 0x15A, 0x160, 0x179, 0x17B, 0x17D, 0x490}) do
    lowercase[utf8.char(upper)] = utf8.char(upper + 1)
end
-- Cyrillic yo, Ukrainian ye, i and yi.
for _, upper in ipairs({0x401, 0x404, 0x406, 0x407}) do
    lowercase[utf8.char(upper)] = utf8.char(upper + 0x50)
end
lowercase[utf8.char(0x178)] = utf8.char(0xFF) -- Latin Y with diaeresis
lowercase[utf8.char(0xDF)] = "ss"
lowercase[utf8.char(0x1E9E)] = "ss"

function M.fold(text)
    -- Look up whole UTF-8 characters. A missing entry keeps the original bytes.
    return (text:gsub(utf8.charpattern, lowercase))
end

-- In the Russian alphabet yo follows ye. Their UTF-8 byte order is different.
function M.sort_key(name)
    return (M.fold(name):gsub(utf8.char(0x451), utf8.char(0x435) .. "\255"))
end

return M
