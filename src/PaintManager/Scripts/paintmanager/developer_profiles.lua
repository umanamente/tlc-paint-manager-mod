-- Explicit, bounded developer-profile I/O. These files never carry gameplay
-- state and never enter the authoritative library or native save mirror.
local processing = require("paintmanager.processing")
local filesystem = require("paintmanager.filesystem")
local M = { MAX_BYTES = 32768 }
local marker = "PaintManager.processing-profile"
local function decode(bytes)
    if type(bytes) ~= "string" or #bytes > M.MAX_BYTES then return nil, "processing_profile_too_large" end
    local ok, value = pcall(function()
        local reader = require("paintmanager.json_reader").begin(bytes, { max_bytes = M.MAX_BYTES })
        while reader.state == "running" do reader:step_one() end
        assert(reader.state == "done", reader.error)
        return reader:get_result()
    end)
    if not ok or type(value) ~= "table" or value.owner ~= marker then
        return nil, "processing_invalid_profile"
    end
    return processing.import_profile(value.values, value.schemaVersion)
end
function M.read(path)
    local file, err = filesystem.open(path, "rb")
    if not file then return nil, err end
    local ok, bytes = pcall(file.read, file, M.MAX_BYTES + 1)
    local closed, close_error = file:close()
    if not ok or not closed then return nil, tostring(not ok and bytes or close_error) end
    return decode(bytes)
end
function M.write(path, values)
    local valid, err = processing.validate(values)
    if not valid then return false, err end
    local bytes = require("paintmanager.json").encode({ owner = marker, schemaVersion = 2, values = values })
    if #bytes > M.MAX_BYTES then return false, "processing_profile_too_large" end
    -- Refuse to overwrite an unfamiliar file or backup at either fixed path.
    local existing = filesystem.open(path, "rb")
    if existing then
        existing:close()
        if not M.read(path) then return false, "processing_profile_unfamiliar_file" end
    end
    local backup = filesystem.open(path .. ".backup", "rb")
    if backup then
        backup:close()
        if not M.read(path .. ".backup") then return false, "processing_profile_unfamiliar_backup" end
        local removed, failure = filesystem.remove(path .. ".backup")
        if not removed then return false, failure end
    end
    local temporary = path .. ".pending-" .. tostring({}):gsub("[^%w]", "")
    local collision = filesystem.open(temporary, "rb")
    if collision then collision:close(); return false, "processing_profile_pending_exists" end
    local file, failure = filesystem.open(temporary, "wb")
    if not file then return false, failure end
    local wrote, write_error = file:write(bytes)
    local closed, close_error = file:close()
    if not wrote or not closed then filesystem.remove(temporary); return false, write_error or close_error end
    if existing then
        local moved, move_error = filesystem.rename(path, path .. ".backup")
        if not moved then filesystem.remove(temporary); return false, move_error end
    end
    local published, publish_error = filesystem.rename(temporary, path)
    if not published then
        if existing then filesystem.rename(path .. ".backup", path) end
        filesystem.remove(temporary)
        return false, publish_error
    end
    local result, read_error = M.read(path)
    if not result then return false, read_error end
    return true
end
function M.consume_startup(path, config)
    local ok, values, migration = pcall(M.read, path)
    if not ok or not values then return false end
    -- Consumption is explicit and happens before scheduling. Failure to remove
    -- the one-shot file must not turn it into an automatic recurring profile.
    local removed = filesystem.remove(path)
    if not removed then return false end
    assert(processing.request(values, "processing_startup"))
    processing.commit(config, true)
    return true, migration
end
return M
