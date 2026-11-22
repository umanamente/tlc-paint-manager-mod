-- Advisory box observations only. This Lua task owns its bounded file/JSON
-- work and receives no native objects or gameplay callbacks.
local processing = require("paintmanager.processing")
local json = require("paintmanager.json")
local reader = require("paintmanager.json_reader")
local ids = require("paintmanager.persistent_id")
local M = { SCHEMA_VERSION = 1, MAX_BOXES = 128, MAX_PAINTS = 512,
    MAX_TOTAL_PAINTS = 16384, MAX_BYTES = 1048576 }
local Job = {}; Job.__index = Job

local function text(value, maximum)
    return type(value) == "string" and #value > 0 and #value <= maximum
        and not value:find("[%z\1-\31]") and utf8.len(value) ~= nil
end
local function integer(value, maximum)
    return math.type(value) == "integer" and value >= 0 and value <= maximum
end
local function exact_keys(value, allowed)
    assert(type(value) == "table" and getmetatable(value) == nil and value ~= json.null, "Container cache requires plain data")
    for key in pairs(value) do assert(allowed[key], "Unexpected container cache field") end
end
local function array(value, maximum, persisted)
    assert(type(value) == "table" and getmetatable(value) == nil and value ~= json.null, "Container cache requires an array")
    assert(#value <= maximum, "Container cache array limit exceeded")
    local count = 0
    for key in pairs(value) do
        assert(math.type(key) == "integer" and key >= 1 and key <= #value, "Container cache array is not contiguous")
        count = count + 1
    end
    assert(count == #value, "Container cache array is not contiguous")
    if persisted then assert(reader.is_array(value), "Persisted container cache requires a JSON array") end
end
local function asset_path(value)
    if not text(value, 1024) then return false end
    local path = value:match("^[%w_]+ (/.+)$") or value
    return path:match("^/Game/[%w_/]+%.[%w_]+$") ~= nil
        and not path:find("//", 1, true) and not path:lower():find("/maps/", 1, true)
end
local function paint_copy(value)
    exact_keys(value, { assetType = true, assetName = true, itemPath = true })
    assert(text(value.assetType, 128) and value.assetType:match("^[%w_]+$"), "Invalid cached paint asset type")
    assert(text(value.assetName, 256) and value.assetName:match("^[%w_]+$"), "Invalid cached paint asset name")
    assert(value.itemPath == nil or asset_path(value.itemPath), "Invalid cached paint asset path")
    return { assetType = value.assetType, assetName = value.assetName, itemPath = value.itemPath }
end
local function paint_key(value) return value.assetType .. ":" .. value.assetName end
local function trusted_path(request, control)
    assert(text(request.path, 4096), "Invalid frozen container cache path")
    assert(type(request.accountId) == "string" and #request.accountId == 17
        and request.accountId:match("^765%d+$"), "Invalid frozen native account identity")
    if not control.allow_test_paths then
        local path = request.path:gsub("\\", "/")
        assert(path:match("^%a:/") or path:sub(1, 2) == "//", "Container cache path must be absolute")
        assert(not path:find("/../", 1, true) and not path:find("/./", 1, true), "Container cache path contains traversal")
        local suffix = "/" .. request.accountId .. "/PaintManager.container-cache.json"
        assert(path:sub(-#suffix) == suffix, "Container cache path is not bound to the frozen account")
    end
    return request.path
end

function M.begin(kind, request, control)
    assert(kind == "container_cache_read" or kind == "container_cache_write", "Unknown container cache task")
    exact_keys(request, { path = true, accountId = true, gameBuild = true, sourcePolicyVersion = true,
        shipId = true, lineageHint = true, boxes = true })
    control = control or {}
    assert(type(control.fs) == "table" and type(control.is_current) == "function"
        and type(control.reserve_commit) == "function", "Container cache requires publication controls")
    assert(text(request.gameBuild, 64) and request.gameBuild:match("^%d+$"), "Invalid frozen game build")
    assert(integer(request.sourcePolicyVersion, 65535) and request.sourcePolicyVersion > 0, "Invalid source policy version")
    assert(ids.is_valid(request.shipId), "Invalid frozen ship identity")
    assert(request.lineageHint == nil or text(request.lineageHint, 256), "Invalid unverified lineage hint")
    local path = trusted_path(request, control)
    local temp_token = control.temp_token
    if kind == "container_cache_write" then
        assert(type(temp_token) == "string" and #temp_token > 0 and #temp_token <= 256
            and temp_token:match("^[%w%-]+$"), "Container cache write requires a frozen private publication token")
    end
    return setmetatable({ kind = kind, request = request, control = control, fs = control.fs, path = path,
        phase = kind == "container_cache_read" and "read_open" or "write_validate_start", state = "running",
        boxes = json.array(), seeds = json.array(), seen = {}, seed_by_key = {}, pieces = {}, bytes = 0,
        processed = 0, total_paints = 0, write_allowed = true,
        pending = temp_token and path .. ".pending-" .. temp_token or nil,
        cleanup_errors = json.array() }, Job)
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, processed = self.processed, error = self.error,
        localCommitted = self.local_committed == true, localUncertain = self.local_uncertain == true }
end
function Job:get_result() if self.state == "done" then return self.result end end
function Job:_close()
    if self.file then
        local file = self.file; self.file = nil
        local ok, err = file:close(); assert(ok, "Cannot close container cache: " .. tostring(err))
    end
end
function Job:_cleanup()
    if self.file then pcall(function() self:_close() end) end
    if self.pending_owned then
        if self.fs.remove(self.pending) then self.pending_owned = nil
        else self.cleanup_errors[#self.cleanup_errors + 1] = "Container cache temporary cleanup failed" end
    end
end
function Job:_finish(status)
    self.result = { status = status, boxes = self.boxes, paintSeeds = self.seeds,
        advisoryOnly = true, boxMappingUsable = false, restoredCounts = false, lineageVerified = false,
        namespaceMatched = self.namespace_matched == true, writeAllowed = self.write_allowed,
        cleanupErrors = self.cleanup_errors }
    self.state, self.phase = "done", "done"
end
function Job:_miss(status, preserve)
    self.boxes, self.seeds = json.array(), json.array()
    if preserve then self.write_allowed = false end
    self:_finish(status)
end
function Job:_read_failed(status)
    self:_close()
    if not self.backup and (status == "missing" or status == "invalid") then
        self.primary_status, self.backup, self.phase = status, true, "read_open"
        self.pieces, self.bytes, self.boxes, self.seeds = {}, 0, json.array(), json.array()
        self.seen, self.seed_by_key, self.total_paints, self.child, self.document = {}, {}, 0, nil, nil
    else self:_miss(self.backup and self.primary_status or status) end
end
function Job:_validate_start(document, reading)
    assert(type(document) == "table" and getmetatable(document) == nil and document ~= json.null, "Invalid container cache document")
    if reading and document.format ~= "PaintManager.ContainerCache" then self:_miss("incompatible_format", true); return end
    assert(document.format == "PaintManager.ContainerCache", "Invalid container cache format")
    if reading and document.schemaVersion ~= M.SCHEMA_VERSION then
        self:_miss("stale_schema", true); return
    end
    exact_keys(document, { format = true, schemaVersion = true, accountId = true, gameBuild = true,
        sourcePolicyVersion = true, shipId = true, lineageHint = true, boxes = true })
    assert(document.schemaVersion == M.SCHEMA_VERSION, "Invalid container cache schema")
    if reading and document.accountId ~= self.request.accountId then self:_miss("stale_account"); return end
    assert(document.accountId == self.request.accountId, "Invalid cached account")
    if reading and document.gameBuild ~= self.request.gameBuild then self:_miss("stale_build"); return end
    assert(document.gameBuild == self.request.gameBuild, "Invalid container cache build")
    if reading and document.sourcePolicyVersion ~= self.request.sourcePolicyVersion then self:_miss("stale_policy"); return end
    assert(document.sourcePolicyVersion == self.request.sourcePolicyVersion, "Invalid cached source policy")
    assert(ids.is_valid(document.shipId), "Invalid cached ship identity")
    assert(document.lineageHint == nil or text(document.lineageHint, 256), "Invalid cached unverified lineage hint")
    array(document.boxes, M.MAX_BOXES, reading)
    self.namespace_matched = document.shipId == self.request.shipId and document.lineageHint == self.request.lineageHint
    self.document, self.index = document, 1
    self.phase = reading and "read_validate" or "write_validate"
end
function Job:_validate_box()
    local value = self.document.boxes[self.index]
    if value == nil then return false end
    exact_keys(value, { persistentId = true, boxKind = true, lastCheckedAt = true, paints = true })
    assert(ids.is_valid(value.persistentId) and not self.seen[value.persistentId], "Invalid or duplicate cached box identity")
    assert(value.boxKind == "small" or value.boxKind == "medium" or value.boxKind == "large", "Invalid cached exact box kind")
    assert(integer(value.lastCheckedAt, 9007199254740991), "Invalid cached observation timestamp")
    array(value.paints, M.MAX_PAINTS, self.kind == "container_cache_read")
    assert(#value.paints > 0, "Only positive box observations belong in the disk cache")
    local row = { persistentId = value.persistentId, boxKind = value.boxKind,
        lastCheckedAt = value.lastCheckedAt, paints = json.array() }
    local seen = {}
    for _, paint in ipairs(value.paints) do
        local entry = paint_copy(paint); local key = paint_key(entry)
        assert(not seen[key], "Duplicate paint identity within cached box")
        seen[key] = true; row.paints[#row.paints + 1] = entry
        local prior = self.seed_by_key[key]
        assert(not prior or not prior.itemPath or not entry.itemPath or prior.itemPath == entry.itemPath,
            "Conflicting paint paths for one cached identity")
        if not prior then
            assert(#self.seeds < M.MAX_PAINTS, "Container cache distinct paint limit exceeded")
            prior = paint_copy(entry); self.seed_by_key[key] = prior; self.seeds[#self.seeds + 1] = prior
        elseif not prior.itemPath and entry.itemPath then prior.itemPath = entry.itemPath end
        self.total_paints = self.total_paints + 1
        assert(self.total_paints <= M.MAX_TOTAL_PAINTS, "Container cache total paint limit exceeded")
    end
    table.sort(row.paints, function(a, b) return json.byte_less(paint_key(a), paint_key(b)) end)
    self.seen[row.persistentId] = true; self.boxes[#self.boxes + 1] = row; self.index = self.index + 1
    return true
end
function Job:_document()
    return { format = "PaintManager.ContainerCache", schemaVersion = M.SCHEMA_VERSION,
        accountId = self.request.accountId, gameBuild = self.request.gameBuild,
        sourcePolicyVersion = self.request.sourcePolicyVersion, shipId = self.request.shipId,
        lineageHint = self.request.lineageHint, boxes = self.boxes }
end
function Job:_exists(path)
    local file, err, code = self.fs.open(path, "rb")
    if not file then assert(code == 2, "Cannot inspect container cache: " .. tostring(err)); return false end
    assert(file:close(), "Cannot close container cache inspection"); return true
end
function Job:_guard_next()
    self.child, self.pieces, self.bytes = nil, {}, 0
    if self.guard_backup then
        self.child = json.begin(self:_document()); self.phase = "write_open"
    else self.guard_backup, self.phase = true, "guard_open" end
end
function Job:_write_piece()
    local data = self.writePending
    if not data then return end
    local finish = math.min(#data, self.writeOffset + processing.get("library_write_bytes", 8192) - 1)
    assert(self.file:write(data:sub(self.writeOffset, finish)), "Cannot write container cache temporary")
    self.writeOffset = finish + 1
    if self.writeOffset > #data then self.writePending, self.writeOffset = nil, nil end
end

function Job:_unit()
    if not self.commit_reserved then assert(self.control.is_current() == true, "Container cache generation was invalidated") end
    if self.phase == "read_open" then
        local file, err, code = self.fs.open(self.path .. (self.backup and ".backup" or ""), "rb")
        if not file then
            if code == 2 then self:_read_failed("missing") else self:_miss("unavailable") end
        else self.file, self.phase = file, "read_chunks" end
    elseif self.phase == "read_chunks" or self.phase == "guard_chunks" then
        local guarding = self.phase == "guard_chunks"
        local part, err = self.file:read(processing.get("library_read_bytes", 8192))
        if part then
            self.bytes = self.bytes + #part
            if self.bytes > M.MAX_BYTES then
                if guarding then error("Existing container cache exceeds preservation inspection limit") else self:_read_failed("invalid") end
            else self.pieces[#self.pieces + 1] = part end
        elseif err then
            if guarding then error("Cannot inspect existing container cache") else self:_read_failed("invalid") end
        else
            self:_close(); self.child = reader.begin(table.concat(self.pieces),
                { max_bytes = M.MAX_BYTES, max_nodes = 100000, max_string = 4096 })
            self.pieces, self.phase = nil, guarding and "guard_decode" or "read_decode"
        end
    elseif self.phase == "read_decode" then
        local progress = self.child:step_one()
        if progress.state == "error" then self:_read_failed("invalid")
        elseif progress.state == "done" then
            local document = self.child:get_result(); self.child = nil
            if not pcall(function() self:_validate_start(document, true) end) then self:_read_failed("invalid") end
        end
    elseif self.phase == "read_validate" then
        local ok, more = pcall(function() return self:_validate_box() end)
        if not ok then self:_read_failed("invalid")
        elseif not more then
            table.sort(self.seeds, function(a, b) return json.byte_less(paint_key(a), paint_key(b)) end)
            if not self.namespace_matched then self.boxes = json.array(); self:_finish("paint_seeds_only")
            else self:_finish(self.backup and "recovered" or "hit") end
        end
    elseif self.phase == "write_validate_start" then
        self.document = self:_document(); self.document.boxes = self.request.boxes
        self:_validate_start(self.document, false)
    elseif self.phase == "write_validate" then
        if not self:_validate_box() then
            table.sort(self.boxes, function(a, b) return json.byte_less(a.persistentId, b.persistentId) end)
            table.sort(self.seeds, function(a, b) return json.byte_less(paint_key(a), paint_key(b)) end)
            self.document, self.pieces, self.bytes, self.phase = nil, {}, 0, "guard_open"
        end
    elseif self.phase == "guard_open" then
        local file, err, code = self.fs.open(self.path .. (self.guard_backup and ".backup" or ""), "rb")
        if not file then assert(code == 2, "Cannot preserve existing container cache: " .. tostring(err)); self:_guard_next()
        else self.file, self.phase = file, "guard_chunks" end
    elseif self.phase == "guard_decode" then
        local progress = self.child:step_one()
        if progress.state == "error" then self:_guard_next()
        elseif progress.state == "done" then
            local document = self.child:get_result()
            if type(document) == "table" and document ~= json.null and document.format ~= nil
                and (document.format ~= "PaintManager.ContainerCache" or document.schemaVersion ~= M.SCHEMA_VERSION) then
                self:_miss("preserved_incompatible", true)
            else self:_guard_next() end
        end
    elseif self.phase == "write_open" then
        local file, err = self.fs.open(self.pending, "wb")
        assert(file, "Cannot create container cache temporary: " .. tostring(err))
        self.file, self.pending_owned, self.phase = file, true, "write_encode"
    elseif self.phase == "write_encode" then
        if self.writePending then
            self:_write_piece()
            if not self.writePending and self.encodeDone then self.phase = "write_close" end
            return
        end
        local progress = self.child:step_one(); assert(progress.state ~= "error", progress.error)
        if progress.chunk then
            self.bytes = self.bytes + #progress.chunk; assert(self.bytes <= M.MAX_BYTES, "Container cache byte limit exceeded")
            self.writePending, self.writeOffset = progress.chunk, 1
            self:_write_piece()
        end
        if progress.state == "done" then
            self.child, self.encodeDone = nil, true
            if not self.writePending then self.phase = "write_close" end
        end
    elseif self.phase == "write_close" then
        assert(self.file:flush(), "Cannot flush container cache temporary"); self:_close(); self.phase = "write_reserve"
    elseif self.phase == "write_reserve" then
        assert(self.control.reserve_commit() == true, "Container cache publication was revoked")
        self.commit_reserved, self.phase = true, "write_rotate"
    elseif self.phase == "write_rotate" then
        if self:_exists(self.path) then
            if self:_exists(self.path .. ".backup") then assert(self.fs.remove(self.path .. ".backup"), "Cannot rotate container cache backup") end
            assert(self.fs.rename(self.path, self.path .. ".backup"), "Cannot preserve previous container cache")
            self.rotated = true
        end
        self.phase = "write_publish"
    elseif self.phase == "write_publish" then
        local ok, err = self.fs.rename(self.pending, self.path)
        if not ok then
            if self.rotated then
                assert(self.fs.rename(self.path .. ".backup", self.path), "Container cache publication and recovery failed")
                self.rotated = nil
            end
            error("Cannot publish container cache: " .. tostring(err))
        end
        self.pending_owned, self.local_committed = nil, true
        self:_finish("written")
    end
end
function Job:step_one()
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(function() self:_unit() end)
    self.processed = self.processed + 1
    if not ok then
        self:_cleanup(); self.state, self.phase, self.error = "error", "error", tostring(err)
        self.local_uncertain = self.commit_reserved == true and self.rotated == true and not self.local_committed
    end
    return self:get_progress()
end
function Job:cancel(reason)
    if self.state ~= "running" or self.commit_reserved then return self:get_progress() end
    self:_cleanup(); self.state, self.phase, self.error = "cancelled", "cancelled", reason or "Container cache cancelled"
    return self:get_progress()
end
return M
