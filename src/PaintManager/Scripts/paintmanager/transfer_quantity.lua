-- Desired stock of the selected color. The native inventory APIs use int32 counts.
local M = { default = 5, maximum = 2147483647 }

function M.valid(value)
    return value == "all" or type(value) == "number" and value >= 1
        and value <= M.maximum and value % 1 == 0
end

return M
