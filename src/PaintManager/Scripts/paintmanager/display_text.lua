-- Case folding for names and search in the shipped English and Russian UIs.
-- Work on complete letter encodings so other UTF-8 text passes through intact.
local M = {}
local lowercase = {}
for upper = 65, 90 do lowercase[string.char(upper)] = string.char(upper + 32) end
for upper = 0x410, 0x42F do lowercase[utf8.char(upper)] = utf8.char(upper + 0x20) end
lowercase[utf8.char(0x401)] = utf8.char(0x451)

function M.fold(text)
    return (text:gsub("[A-Z]", lowercase):gsub("\208[\128-\191]", lowercase))
end

-- In the Russian alphabet yo follows ye. Their UTF-8 byte order is different.
function M.sort_key(name)
    return (M.fold(name):gsub(utf8.char(0x451), utf8.char(0x435) .. "\255"))
end

return M
