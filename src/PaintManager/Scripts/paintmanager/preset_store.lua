-- Versioned mod-owned presets. Persistent records contain logical identities only.
-- Loading, validation and output advance cooperatively; filesystem calls cannot yield.
local processing = require("paintmanager.processing")
local json = require("paintmanager.json")
local reader = require("paintmanager.json_reader")
local writer = require("paintmanager.report_writer")
local M = {}
local Job = {}
Job.__index = Job
local sequence = 0
local default_fs = { open = io.open, remove = os.remove, rename = os.rename }

local function text(value, maximum, empty)
    assert(type(value) == "string" and #value <= maximum and (empty or #value > 0)
        and not value:find("[%z\1-\31\127]") and utf8.len(value), "Invalid preset text")
    return value
end
local function optional_text(value, maximum)
    if value == nil then return nil end
    return text(value, maximum, true)
end
local function object(value, keys)
    assert(type(value) == "table" and value ~= json.null and getmetatable(value) == nil
        and not reader.is_array(value), "Expected a preset object")
    for key in next, value do assert(keys[key] == true, "Unexpected preset field: " .. tostring(key)) end
end
local function identity(value, maximum)
    value = text(value, maximum)
    assert(value:match("^[A-Za-z0-9_.%-]+$"), "Invalid persistent asset identity")
    return value
end
local function static_class(value)
    value = text(value, 512)
    assert((value:sub(1, 8) == "/Script/" or value:sub(1, 6) == "/Game/")
        and value:match("^/[A-Za-z0-9_./]+$") and not value:find("..", 1, true), "Invalid static target class")
    return value
end
local root_keys = { schemaVersion = true, presets = true }
local preset_keys = { id = true, name = true, createdUtc = true, target = true, zones = true, gameBuild = true }
local target_keys = { kind = true, className = true, paintSetName = true, displayName = true }
local zone_keys = { materialSlotName = true, partName = true, paint = true, displayName = true }
local paint_keys = { assetType = true, assetName = true, displayName = true }

local function path_check(path)
    assert(type(path) == "string" and path ~= "" and not path:find("[%z\r\n]"), "Invalid trusted preset path")
end
local function options_copy(options)
    options = options or {}
    return { fs = options.fs or default_fs, max_bytes = math.min(1048576, options.max_bytes or 1048576),
        max_presets = math.min(64, options.max_presets or 64), max_zones = math.min(128, options.max_zones or 128),
        write_bytes = options.write_bytes, microsteps_per_unit = options.microsteps_per_unit }
end
function M.begin_validate(library, options)
    return setmetatable({ state = "running", phase = "validate_root", mode = "validate", input = library,
        options = options_copy(options), completed = 0, names = {}, ids = {}, canonical_bytes = #'{"schemaVersion":1,"presets":[]}\n',
        result = { schemaVersion = 1, presets = json.array() } }, Job)
end
function M.begin_load(path, options)
    path_check(path)
    return setmetatable({ state = "running", phase = "open", mode = "load", path = path,
        options = options_copy(options), completed = 0, pieces = {}, bytes_read = 0, cleanup_errors = json.array() }, Job)
end
function M.begin_save(path, library, options)
    path_check(path)
    local job = setmetatable({ state = "running", phase = "validate_new", mode = "save", path = path,
        options = options_copy(options), completed = 0, cleanup_errors = json.array() }, Job)
    job.child = M.begin_validate(library, job.options)
    sequence = sequence + 1
    local token = tostring(job):match("0x(%x+)") or tostring(sequence)
    job.pending = path .. ".pending-" .. token .. "-" .. sequence
    return job
end

function Job:_array(value, maximum, next_phase)
    assert(type(value) == "table" and value ~= json.null and getmetatable(value) == nil, "Expected a preset array")
    -- Empty objects from JSON are not interchangeable with [] in the schema.
    if self.decoded and not reader.is_array(value) then error("Expected a JSON array") end
    self.array, self.array_count, self.array_max, self.array_last = value, 0, 0, nil
    self.array_limit, self.after_array, self.phase = maximum, next_phase, "validate_array"
end
function Job:_validate()
    if self.phase == "validate_root" then
        object(self.input, root_keys)
        assert(self.input.schemaVersion == 1, "Unsupported preset schema version")
        self:_array(self.input.presets, self.options.max_presets, "validate_preset")
        self.preset_index = 1
    elseif self.phase == "validate_array" then
        local key = next(self.array, self.array_last)
        if key ~= nil then
            assert(type(key) == "number" and key % 1 == 0 and key >= 1 and key <= self.array_limit, "Invalid preset array index or limit")
            self.array_last, self.array_count, self.array_max = key, self.array_count + 1, math.max(self.array_max, key)
            assert(self.array_count <= self.array_limit, "Preset array limit exceeded")
        else
            assert(self.array_count == self.array_max, "Preset array must be contiguous")
            if self.after_array == "validate_preset" then self.preset_count = self.array_count
            else self.zone_count = self.array_count end
            self.array, self.phase = nil, self.after_array
        end
    elseif self.phase == "validate_preset" then
        if self.preset_index > self.preset_count then self.state, self.phase, self.input = "done", "done", nil; return end
        local preset = self.input.presets[self.preset_index]
        object(preset, preset_keys); object(preset.target, target_keys)
        local id, name = identity(preset.id, 96), text(preset.name, 128)
        assert(not self.ids[id] and not self.names[name:lower()], "Duplicate preset ID or name")
        self.ids[id], self.names[name:lower()] = true, true
        local target = preset.target
        assert(target.kind == "character" or target.kind == "boat", "Invalid preset target kind")
        local created = text(preset.createdUtc, 64)
        assert(created:match("^%d%d%d%d%-%d%d%-%d%d[T ]%d%d:%d%d:%d%d") ~= nil, "Invalid preset creation timestamp")
        local build = optional_text(preset.gameBuild, 16)
        if build then assert(build:match("^%d+$"), "Invalid preset game build") end
        self.current = { id = id, name = name, createdUtc = created, gameBuild = build,
            target = { kind = target.kind, className = static_class(target.className), paintSetName = text(target.paintSetName, 256, true),
                displayName = optional_text(target.displayName, 256) }, zones = json.array() }
        -- Only this fixed, small record is encoded synchronously to account for
        -- exact escaping overhead. Zones remain individual bounded units.
        self.canonical_bytes = self.canonical_bytes + #json.encode(self.current) + (self.preset_index > 1 and 1 or 0)
        assert(self.canonical_bytes <= self.options.max_bytes, "Preset output exceeds the byte limit")
        self.zone_names, self.zone_index = {}, 1
        self:_array(preset.zones, self.options.max_zones, "validate_zone")
    elseif self.phase == "validate_zone" then
        if self.zone_index > self.zone_count then
            assert(self.zone_count > 0, "A preset requires at least one configured zone")
            self.result.presets[#self.result.presets + 1] = self.current
            self.current, self.preset_index, self.phase = nil, self.preset_index + 1, "validate_preset"
            return
        end
        local zone = self.input.presets[self.preset_index].zones[self.zone_index]
        object(zone, zone_keys)
        local material, part = text(zone.materialSlotName, 256, true), text(zone.partName, 256, true)
        assert(material ~= "" or part ~= "", "A preset zone requires a logical identity")
        local key = #material .. ":" .. material .. part
        assert(not self.zone_names[key], "Duplicate logical preset zone")
        self.zone_names[key] = true
        local paint = json.null
        if zone.paint ~= json.null then
            object(zone.paint, paint_keys)
            paint = { assetType = identity(zone.paint.assetType, 64), assetName = identity(zone.paint.assetName, 256),
                displayName = optional_text(zone.paint.displayName, 256) }
        end
        local canonical = { materialSlotName = material, partName = part,
            displayName = optional_text(zone.displayName, 256), paint = paint }
        self.canonical_bytes = self.canonical_bytes + #json.encode(canonical) + (self.zone_index > 1 and 1 or 0)
        assert(self.canonical_bytes <= self.options.max_bytes, "Preset output exceeds the byte limit")
        self.current.zones[#self.current.zones + 1] = canonical
        self.zone_index = self.zone_index + 1
    end
end

function Job:_close()
    if not self.file then return end
    local file = self.file; self.file = nil
    local ok, err = file:close()
    assert(ok, "Cannot close preset file: " .. tostring(err))
end
function Job:_child_one(budget)
    local progress = self.child:step_one(budget)
    if progress.state == "error" or progress.state == "cancelled" then error(progress.error or "Preset child job stopped") end
    return progress.state == "done"
end
function Job:_load(budget)
    if self.phase == "open" then
        local file, err, code = self.options.fs.open(self.path, "rb")
        if not file then
            if code ~= 2 then error("Cannot open preset library: " .. tostring(err)) end
            self.primary_missing = true
            local backup, backup_err, backup_code = self.options.fs.open(self.path .. ".backup", "rb")
            if not backup then
                if backup_code ~= 2 then error("Cannot open preset backup: " .. tostring(backup_err)) end
                self.missing, self.result, self.state, self.phase = true, { schemaVersion = 1, presets = json.array() }, "done", "done"
                return
            end
            self.file, self.recovered_backup = backup, true
        else self.file = file end
        self.phase = "read"
    elseif self.phase == "read" then
        local chunk, err = self.file:read(processing.get("legacy_read_bytes", 8192))
        if chunk then
            assert(type(chunk) == "string" and #chunk > 0, "Invalid preset file read")
            self.bytes_read = self.bytes_read + #chunk
            assert(self.bytes_read <= self.options.max_bytes, "Preset file exceeds the byte limit")
            self.pieces[#self.pieces + 1] = chunk
        else
            assert(err == nil, "Cannot read preset library: " .. tostring(err))
            self.phase = "close"
        end
    elseif self.phase == "close" then
        self:_close(); self.phase = "parse_start"
    elseif self.phase == "parse_start" then
        -- Bounded by max_bytes; this concatenation is an indivisible allocation.
        self.raw = table.concat(self.pieces); self.pieces = nil
        self.child, self.phase = reader.begin(self.raw, { max_bytes = self.options.max_bytes }), "parse"
    elseif self.phase == "parse" then
        if self:_child_one(budget) then
            self.child = M.begin_validate(self.child:get_result(), self.options)
            self.child.decoded, self.phase = true, "validate"
        end
    elseif self.phase == "validate" and self:_child_one(budget) then
        self.result, self.child, self.state, self.phase = self.child:get_result(), nil, "done", "done"
    end
end

function Job:_exists(path)
    local file, err, code = self.options.fs.open(path, "rb")
    if not file then assert(code == 2, "Cannot inspect preset path: " .. tostring(err)); return false end
    local ok, close_err = file:close(); assert(ok, "Cannot close preset path: " .. tostring(close_err))
    return true
end
function Job:_rename(source, destination)
    local ok, err = self.options.fs.rename(source, destination)
    assert(ok, "Cannot publish preset library: " .. tostring(err))
end
function Job:_save(budget)
    if self.phase == "validate_new" then
        if self:_child_one(budget) then
            self.result = self.child:get_result()
            self.child, self.phase = M.begin_load(self.path, self.options), "validate_existing"
        end
    elseif self.phase == "validate_existing" then
        if self:_child_one(budget) then
            self.original_raw, self.original_missing, self.original_backup = self.child.raw, self.child.primary_missing, self.child.recovered_backup
            self.child = writer.begin(self.pending, self.result, self.options)
            self.phase = "write"
        end
    elseif self.phase == "write" then
        if self:_child_one(budget) then
            self.pending_created = true
            assert(self.child:get_progress().bytesWritten <= self.options.max_bytes, "Preset output exceeds the byte limit")
            self.child, self.phase = M.begin_load(self.path, self.options), "recheck_existing"
        end
    elseif self.phase == "recheck_existing" then
        if self:_child_one(budget) then
            assert(self.child.raw == self.original_raw and self.child.primary_missing == self.original_missing
                and self.child.recovered_backup == self.original_backup, "Preset library changed while saving")
            self.child, self.phase = nil, "backup_check"
        end
    elseif self.phase == "backup_check" then
        if not self.original_missing then
            if self:_exists(self.path .. ".backup") then self.phase = "backup_remove"
            else self.phase = "backup_move" end
        else self.phase = "publish" end
    elseif self.phase == "backup_remove" then
        local ok, err = self.options.fs.remove(self.path .. ".backup")
        assert(ok, "Cannot rotate preset backup: " .. tostring(err)); self.phase = "backup_move"
    elseif self.phase == "backup_move" then
        self:_rename(self.path, self.path .. ".backup")
        self.old_moved, self.phase = true, "publish"
    elseif self.phase == "publish" then
        -- The backup protects a crash between the two Windows rename calls.
        assert(not self:_exists(self.path), "Preset library appeared before publication")
        self:_rename(self.pending, self.path)
        self.pending_created, self.state, self.phase = false, "done", "done"
    end
end

function Job:_cleanup()
    if self.file then
        local ok, err = pcall(self._close, self)
        if not ok then self.cleanup_errors[#self.cleanup_errors + 1] = tostring(err) end
    end
    if self.child and self.child.cancel then self.child:cancel("Preset storage stopped") end
    if self.old_moved then
        local checked, exists = pcall(self._exists, self, self.path)
        if not checked or exists then
            self.cleanup_errors[#self.cleanup_errors + 1] = "Backup retained: primary path is present or cannot be inspected"
        else
            local ok, value, err = pcall(self.options.fs.rename, self.path .. ".backup", self.path)
            if not ok or not value then self.cleanup_errors[#self.cleanup_errors + 1] = "Backup retained after failed restore: " .. tostring(ok and err or value) end
        end
        self.old_moved = false
    end
    if self.pending_created then
        local ok, value, err = pcall(self.options.fs.remove, self.pending)
        if not ok or not value then self.cleanup_errors[#self.cleanup_errors + 1] = tostring(ok and err or value) end
        self.pending_created = false
    end
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, completed = self.completed, processed = self.completed,
        error = self.error, missing = self.missing, recoveredBackup = self.recovered_backup,
        path = self.state == "done" and self.path or nil, cleanupErrors = self.cleanup_errors,
        bytesRead = self.bytes_read }
end
function Job:step_one(budget)
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(function()
        if self.mode == "validate" then self:_validate()
        elseif self.mode == "load" then self:_load(budget)
        else self:_save(budget) end
    end)
    self.completed = self.completed + 1
    if not ok then
        self.state, self.phase, self.error = "error", "error", tostring(err)
        self.cleanup_errors = self.cleanup_errors or json.array(); self:_cleanup()
        self.result = nil
    end
    return self:get_progress()
end
function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do self:step_one(budget); budget:consume(1) end
    return self:get_progress()
end
function Job:get_result() return self.state == "done" and self.result or nil end
function Job:cancel(reason)
    if self.state ~= "running" then return self:get_progress() end
    self.state, self.phase, self.error = "cancelled", "cancelled", reason
    self.cleanup_errors = self.cleanup_errors or json.array(); self:_cleanup(); self.result = nil
    return self:get_progress()
end
return M
