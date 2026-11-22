-- Checked standard Lua handles. Existing destinations are refused by Windows
-- CRT rename; other hosts need an explicit qualified no-replace adapter.
local M = {}
local windows = package.config:sub(1, 1) == "\\"
local function path(value)
    assert(type(value) == "string" and #value > 0 and #value <= 4096
        and not value:find("[%z\r\n]"), "Invalid local file path")
    return value
end
local function supported(value)
    return not windows or not value:find("[\128-\255]")
end
local function encoding_failure()
    return nil, "Non-ASCII paths are unavailable in this unqualified Windows Lua I/O backend", 95
end
function M.open(filename, mode)
    filename = path(filename)
    if not supported(filename) then return encoding_failure() end
    assert(mode == "rb" or mode == "wb" or mode == "ab", "Unsupported local file mode")
    if mode == "wb" then
        -- Refuse unexpected candidates. Private job names and writer lanes are
        -- still required: standard Lua does not provide atomic fopen("x").
        local existing, err, code = io.open(filename, "rb")
        if existing then
            local closed, why = existing:close()
            if not closed then return nil, why end
            return nil, "Refusing to truncate an existing temporary file", 17
        end
        if code ~= 2 then return nil, err, code end
    end
    return io.open(filename, mode)
end
function M.remove(filename)
    filename = path(filename)
    if not supported(filename) then return encoding_failure() end
    return os.remove(filename)
end
function M.rename(source, destination)
    source, destination = path(source), path(destination)
    if not supported(source) or not supported(destination) then return encoding_failure() end
    if not windows then return nil, "No-replace publication is unavailable on this Lua host", 95 end
    return os.rename(source, destination)
end
function M.describe()
    return { backend = "lua_io", renameNoReplace = windows, exclusiveCreate = false,
        pathEncoding = windows and "ASCII Windows paths; wider paths fail closed" or "host filesystem bytes",
        semantics = "Checked standard handles; filesystem calls are indivisible" }
end
return M
