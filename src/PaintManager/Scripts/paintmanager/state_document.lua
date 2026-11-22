-- Bounded, versioned plain data carried by the native save's optional HUD map.
-- This module performs no native calls or filesystem I/O. Unknown sections are
-- preserved, while known schemas require an explicit, sequential migration.
local processing = require("paintmanager.processing")
local json = require("paintmanager.json")
local reader = require("paintmanager.json_reader")
local library_schema = require("paintmanager.library_schema")
local layout = require("paintmanager.preset_layout")
local transfer_quantity = require("paintmanager.transfer_quantity")
local languages = require("paintmanager.languages")
local M = { FORMAT = "PaintManager.State", VERSION = 3, MAX_BYTES = 32768, MAX_FAVORITES = 256,
    SECTION_VERSIONS = { settings = 4, presets = 2, favorites = 1 },
    -- Future releases register each production migration here once. Callers may
    -- override this trusted registry for isolated compatibility tests.
    migrations = { root = {}, settings = {}, presets = {}, favorites = {} } }
local Job = {}; Job.__index = Job
local MAX_INTEGER, MAX_NODES, MAX_DEPTH, MAX_STRING = 9007199254740991, 12000, 16, 4096
local root_keys = { format = true, schemaVersion = true, revision = true, sections = true }
local section_keys = { schemaVersion = true, data = true }
local setting_names = { "shipRadiusCm", "sort", "lastPage", "showNames", "tileDensity",
    "showDeveloperTools", "paintTransferQuantity", "depositSimilarAllColors", "showDepositAllPaints", "uiLanguage" }
local setting_keys = { updatedAt = true }
for _, key in ipairs(setting_names) do setting_keys[key] = true end
local defaults = { shipRadiusCm = 3500, sort = "name_asc", lastPage = "learned", showNames = false,
    tileDensity = "comfortable", showDeveloperTools = false }
M.optional_setting_defaults = { paintTransferQuantity = transfer_quantity.default,
    depositSimilarAllColors = true, showDepositAllPaints = false, uiLanguage = "auto" }
local sorts = { name_asc = true, name_desc = true, count_asc = true, count_desc = true }
local pages = { learned = true, ready = true, missing = true, settings = true, presets = true }

local function integer(value, minimum, maximum)
    return type(value) == "number" and value == value and value % 1 == 0
        and value >= minimum and value <= maximum
end
local function object(value, keys)
    assert(type(value) == "table" and value ~= json.null and getmetatable(value) == nil
        and not reader.is_array(value) and not json.is_array(value), "Expected a state object")
    if keys then
        for key in next, value do assert(keys[key], "Unexpected state field: " .. tostring(key)) end
    end
end
local function setting_value(key, value)
    local valid
    if key == "shipRadiusCm" then valid = integer(value, 3500, 5000)
    elseif key == "paintTransferQuantity" then valid = transfer_quantity.valid(value)
    elseif key == "uiLanguage" then valid = languages.valid(value)
    elseif key == "sort" then valid = type(value) == "string" and sorts[value]
    elseif key == "lastPage" then valid = type(value) == "string" and pages[value]
    elseif key == "tileDensity" then valid = value == "comfortable" or value == "compact"
    else valid = (type(defaults[key]) == "boolean" or type(M.optional_setting_defaults[key]) == "boolean")
        and type(value) == "boolean" end
    assert(valid, "Invalid saved setting: " .. tostring(key))
end
local function favorite_key(asset_type, asset_name)
    for _, entry in ipairs({ { asset_type, 64 }, { asset_name, 256 } }) do
        assert(type(entry[1]) == "string" and #entry[1] > 0 and #entry[1] <= entry[2]
            and entry[1]:match("^[A-Za-z0-9_.%-]+$"), "Invalid favorite paint identity")
    end
    return asset_type .. "\0" .. asset_name
end
M.favorite_key = favorite_key
local function options_copy(options)
    options = options or {}
    local maximum = options.max_bytes
    if not integer(maximum, 1, M.MAX_BYTES) then maximum = M.MAX_BYTES end
    return { max_bytes = maximum, migrations = options.migrations or M.migrations }
end
M.migrations.root[1] = function(old)
    old.schemaVersion = 2
    return old
end
M.migrations.root[2] = function(old)
    object(old.sections)
    if old.sections.favorites == nil then
        old.sections.favorites = { schemaVersion = 1, data = { records = json.array() } }
    end
    old.schemaVersion = 3
    return old
end
M.migrations.settings[1] = function(old)
    object(old, section_keys); object(old.data, { radiusCm = true })
    assert(integer(old.data.radiusCm, 2500, 25000), "Invalid legacy saved radius")
    return { schemaVersion = 2, data = { radiusCm = old.data.radiusCm, updatedAt = { radiusCm = 0 } } }
end
M.migrations.settings[2] = function(old)
    object(old, section_keys); object(old.data, { radiusCm = true, updatedAt = true })
    assert(integer(old.data.radiusCm, 2500, 25000), "Invalid legacy saved radius")
    object(old.data.updatedAt, { radiusCm = true })
    assert(integer(old.data.updatedAt.radiusCm, 0, MAX_INTEGER), "Invalid setting timestamp")
    for key, value in pairs(defaults) do old.data[key], old.data.updatedAt[key] = value, 0 end
    old.schemaVersion = 3
    return old
end
M.migrations.settings[3] = function(old)
    object(old, section_keys); object(old.data); object(old.data.updatedAt)
    assert(integer(old.data.radiusCm, 2500, 25000), "Invalid legacy saved radius")
    assert(integer(old.data.updatedAt.radiusCm, 0, MAX_INTEGER), "Invalid setting timestamp")
    old.data.radiusCm, old.data.updatedAt.radiusCm = nil, nil
    -- Early v3 documents predate the independent ship radius.
    if old.data.shipRadiusCm == nil and old.data.updatedAt.shipRadiusCm == nil then
        old.data.shipRadiusCm, old.data.updatedAt.shipRadiusCm = defaults.shipRadiusCm, 0
    end
    old.schemaVersion = 4
    return old
end
M.migrations.presets[1] = function(old)
    object(old, section_keys)
    assert(type(old.data) == "table" and old.data.schemaVersion == 1, "Invalid legacy preset library")
    old.schemaVersion = 2
    return old
end
function M.new()
    local settings = { updatedAt = {} }
    for key, value in pairs(defaults) do settings[key], settings.updatedAt[key] = value, 0 end
    return { format = M.FORMAT, schemaVersion = M.VERSION, revision = 0, sections = {
        settings = { schemaVersion = M.SECTION_VERSIONS.settings, data = settings },
        favorites = { schemaVersion = 1, data = { records = json.array() } },
        presets = { schemaVersion = M.SECTION_VERSIONS.presets, data = { schemaVersion = 2,
            presets = json.array(), tombstones = json.array() } },
    } }
end

function Job:_copy_start(value, after)
    self.after_copy = after
    self.scan = { { value = value, depth = 0 } }
    self.active, self.nodes, self.clone_bytes, self.phase = {}, 0, 0, "scan"
end
local function begin(mode, value, options)
    local job = setmetatable({ mode = mode, state = "running", completed = 0,
        options = options_copy(options) }, Job)
    if mode == "decode" then
        job.child, job.phase = reader.begin(value, { max_bytes = job.options.max_bytes,
            max_depth = MAX_DEPTH, max_nodes = MAX_NODES, max_string = MAX_STRING }), "parse"
        job.after_copy = "root_migrate"
    else job:_copy_start(value, "root_migrate") end
    return job
end
function M.begin_decode(raw, options) return begin("decode", raw, options) end
function M.begin_validate(document, options) return begin("validate", document, options) end
function M.begin_encode(document, options) return begin("encode", document, options) end

local function number_text(value)
    if math.type(value) == "integer" then return tostring(value) end
    return (string.format("%.17g", value):gsub(",", "."))
end
local function quoted_bytes(value)
    local bytes = #value + 2
    for index = 1, #value do
        local byte = value:byte(index)
        if byte == 34 or byte == 92 then bytes = bytes + 1
        elseif byte < 32 then
            bytes = bytes + ((byte == 8 or byte == 9 or byte == 10 or byte == 12 or byte == 13) and 1 or 5)
        end
    end
    return bytes
end
function Job:_count_bytes(count)
    self.clone_bytes = self.clone_bytes + count
    assert(self.clone_bytes <= self.options.max_bytes, "State document exceeds the byte limit")
end
function Job:_assign(frame, value)
    if frame.parent then frame.parent[frame.key] = value else self.document = value end
end
-- One value or one key per primitive. Copying plain data directly avoids an
-- encode/parse roundtrip while preserving the source's array/object markers.
function Job:_scan_one()
    local frame = self.scan[#self.scan]
    if not frame then
        self.phase = self.after_copy
        self.scan, self.active = nil, nil
        return
    end
    if not frame.started then
        self.nodes = self.nodes + 1
        assert(self.nodes <= MAX_NODES, "State node limit exceeded")
        local value, kind = frame.value, type(frame.value)
        if value == json.null or kind == "boolean" then
            self:_assign(frame, value); self:_count_bytes(value == json.null and 4 or value and 4 or 5)
            self.scan[#self.scan] = nil; return
        end
        if kind == "string" then
            assert(#value <= MAX_STRING and utf8.len(value), "Invalid or oversized state string")
            self:_assign(frame, value); self:_count_bytes(quoted_bytes(value))
            self.scan[#self.scan] = nil; return
        end
        if kind == "number" then
            assert(value == value and math.abs(value) < math.huge, "State numbers must be finite")
            self:_assign(frame, value); self:_count_bytes(#number_text(value))
            self.scan[#self.scan] = nil; return
        end
        assert(kind == "table" and getmetatable(value) == nil, "State requires plain JSON values")
        assert(not self.active[value], "State cannot contain cycles")
        assert(frame.depth < MAX_DEPTH, "State nesting limit exceeded")
        frame.is_array = json.is_array(value) or reader.is_array(value)
        frame.copy = frame.is_array and json.array() or {}
        frame.count, frame.maximum = 0, 0
        self:_assign(frame, frame.copy); self:_count_bytes(2)
        self.active[value], frame.started = true, true
    end
    local key, value = next(frame.value, frame.last)
    if key == nil then
        if frame.is_array then assert(frame.count == frame.maximum, "State arrays must be contiguous") end
        self.active[frame.value], self.scan[#self.scan] = nil, nil; return
    end
    if frame.is_array then
        assert(integer(key, 1, MAX_NODES), "Invalid state array index")
        frame.maximum = math.max(frame.maximum, key)
    else
        assert(type(key) == "string", "State object keys must be text")
        assert(#key <= MAX_STRING and utf8.len(key), "Invalid or oversized state key")
        self:_count_bytes(quoted_bytes(key) + 1)
    end
    if frame.count > 0 then self:_count_bytes(1) end
    frame.count = frame.count + 1
    frame.last = key
    self.scan[#self.scan + 1] = { value = value, depth = frame.depth + 1, parent = frame.copy, key = key }
end
function Job:_child_one()
    local progress = self.child:step_one()
    assert(progress.state ~= "error" and progress.state ~= "cancelled", progress.error or "State child job stopped")
    return progress
end
function Job:_encode_one(next_phase)
    local progress = self:_child_one()
    if progress.chunk then
        self.bytes = self.bytes + #progress.chunk
        assert(self.bytes <= self.options.max_bytes, "State document exceeds the byte limit")
        self.parts[#self.parts + 1] = progress.chunk
    end
    if progress.state == "done" then
        -- The allocation is indivisible and bounded by MAX_BYTES.
        self.raw, self.parts, self.child, self.phase = table.concat(self.parts), nil, nil, next_phase
    end
end

function Job:_migrate(scope, value)
    object(value)
    local version = value.schemaVersion
    local current = scope == "root" and M.VERSION or M.SECTION_VERSIONS[scope]
    assert(integer(version, 0, MAX_INTEGER), "Invalid " .. scope .. " schema version")
    assert(version <= current, "Unsupported future " .. scope .. " schema version")
    if version == current then return value, true end
    local registry = self.options.migrations[scope]
    local migrate = type(registry) == "table" and registry[version]
        or M.migrations[scope] and M.migrations[scope][version]
    assert(type(migrate) == "function", "Missing " .. scope .. " migration from version " .. tostring(version))
    -- A trusted migration is one bounded, non-yielding transformation. It must
    -- return the next version, never skip versions or replace errors with defaults.
    local upgraded = migrate(value)
    object(upgraded)
    assert(upgraded.schemaVersion == version + 1, "Migration must advance exactly one schema version")
    self.migrated, self.migration_dirty = true, true
    if scope == "presets" then self.legacy_presets = true end
    return upgraded, false
end
function Job:_advance()
    if self.phase == "scan" then return self:_scan_one() end
    if self.phase == "parse" then
        if self:_child_one().state == "done" then
            self.document, self.child, self.phase = self.child:get_result(), nil, self.after_copy
        end
        return
    end
    if self.phase == "root_migrate" then
        object(self.document)
        assert(self.document.format == M.FORMAT, "Unexpected state document format")
        local done
        self.document, done = self:_migrate("root", self.document)
        if done then self.phase = "root_validate" end
    elseif self.phase == "root_validate" then
        object(self.document, root_keys)
        assert(integer(self.document.revision, 0, MAX_INTEGER), "Invalid state revision")
        object(self.document.sections)
        assert(self.document.sections.settings ~= nil and self.document.sections.presets ~= nil
            and self.document.sections.favorites ~= nil, "Required state section missing")
        self.section_count, self.section_last, self.phase = 0, nil, "sections"
    elseif self.phase == "sections" then
        local key = next(self.document.sections, self.section_last)
        if key then
            assert(type(key) == "string" and #key <= 64 and key:match("^[A-Za-z][A-Za-z0-9_.%-]*$"), "Invalid state section name")
            self.section_count, self.section_last = self.section_count + 1, key
            assert(self.section_count <= 32, "State section limit exceeded")
            if not M.SECTION_VERSIONS[key] then
                local section = self.document.sections[key]
                object(section)
                assert(integer(section.schemaVersion, 1, MAX_INTEGER) and section.data ~= nil, "Invalid unknown state section")
            end
        else self.phase = "settings_migrate" end
    elseif self.phase == "settings_migrate" or self.phase == "presets_migrate" or self.phase == "favorites_migrate" then
        local scope = self.phase:match("^(.-)_migrate$")
        local done
        self.document.sections[scope], done = self:_migrate(scope, self.document.sections[scope])
        if done then self.phase = scope .. "_validate" end
    elseif self.phase == "settings_validate" then
        local section = self.document.sections.settings
        object(section, section_keys); object(section.data, setting_keys)
        object(section.data.updatedAt, setting_keys)
        assert(section.data.updatedAt.updatedAt == nil, "Invalid setting timestamp key")
        self.setting_index, self.phase = 1, "settings_fields"
    elseif self.phase == "settings_fields" then
        local key = setting_names[self.setting_index]
        if key then
            local data = self.document.sections.settings.data
            self.setting_index = self.setting_index + 1
            -- Untouched optional settings take no bytes, even at the native size limit.
            if M.optional_setting_defaults[key] ~= nil and data[key] == nil and data.updatedAt[key] == nil then return end
            setting_value(key, data[key])
            assert(integer(data.updatedAt[key], 0, MAX_INTEGER), "Invalid setting timestamp")
        else self.phase = "favorites_migrate" end
    elseif self.phase == "favorites_validate" then
        local section = self.document.sections.favorites
        object(section, section_keys); object(section.data, { records = true })
        local rows = section.data.records
        assert(type(rows) == "table" and rows ~= json.null and getmetatable(rows) == nil
            and (json.is_array(rows) or reader.is_array(rows)), "Expected a favorites array")
        self.favorite_rows, self.favorite_last, self.favorite_count, self.favorite_maximum = rows, nil, 0, 0
        self.favorite_seen, self.phase = {}, "favorites_array"
    elseif self.phase == "favorites_array" then
        local key = next(self.favorite_rows, self.favorite_last)
        if key then
            assert(integer(key, 1, M.MAX_FAVORITES), "Invalid favorites array index or limit")
            self.favorite_count, self.favorite_maximum, self.favorite_last = self.favorite_count + 1,
                math.max(self.favorite_maximum, key), key
        else
            assert(self.favorite_count == self.favorite_maximum, "Favorites array must be contiguous")
            self.favorite_index, self.phase = 1, "favorites_records"
        end
    elseif self.phase == "favorites_records" then
        local row = self.favorite_rows[self.favorite_index]
        if row then
            object(row, { assetType = true, assetName = true, enabled = true, updatedAt = true })
            local key = favorite_key(row.assetType, row.assetName)
            assert(not self.favorite_seen[key], "Duplicate favorite paint identity")
            assert(type(row.enabled) == "boolean" and integer(row.updatedAt, 0, MAX_INTEGER), "Invalid favorite record")
            self.favorite_seen[key], self.favorite_index = true, self.favorite_index + 1
        else
            self.favorite_rows, self.favorite_seen, self.phase = nil, nil, "presets_migrate"
        end
    elseif self.phase == "presets_validate" then
        local section = self.document.sections.presets
        object(section, section_keys)
        assert(type(section.data) == "table" and (section.data.schemaVersion == 2
            or self.legacy_presets and section.data.schemaVersion == 1), "Invalid synchronized preset schema")
        self.child = library_schema.begin_validate(section.data, { max_bytes = self.options.max_bytes })
        self.child.decoded, self.phase = true, "presets_data"
    elseif self.phase == "presets_data" then
        if self:_child_one().state == "done" then
            self.document.sections.presets.data, self.child = self.child:get_result(), nil
            if self.migration_dirty then
                self.migration_dirty = false
                self:_copy_start(self.document, "root_migrate")
            else
                if self.mode == "fields" then self.phase = "finish"
                else self.child, self.parts, self.bytes, self.phase = json.begin(self.document), {}, 0, "final_encode" end
            end
        end
    elseif self.phase == "final_encode" then
        self:_encode_one("finish")
    elseif self.phase == "finish" then
        self.validated_document, self.encoded = self.document, self.raw
        self.result = self.mode == "encode" and self.raw or self.document
        self.document, self.raw, self.state, self.phase = nil, nil, "done", "done"
    end
end
function Job:get_progress()
    return { state = self.state, phase = self.phase, completed = self.completed, processed = self.completed,
        error = self.error, bytes = self.bytes, migrated = self.migrated == true }
end
function Job:step_one()
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(self._advance, self)
    self.completed = self.completed + 1
    if not ok then
        self.state, self.phase, self.error = "error", "error", tostring(err)
        self.result, self.document, self.input, self.raw, self.parts, self.scan, self.active, self.child = nil, nil, nil, nil, nil, nil, nil, nil
    end
    return self:get_progress()
end
function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do self:step_one(); budget:consume(1) end
    return self:get_progress()
end
function Job:get_result() return self.state == "done" and self.result or nil end
function Job:get_document() return self.state == "done" and self.validated_document or nil end
function Job:get_encoded() return self.state == "done" and self.encoded or nil end
function Job:cancel(reason)
    if self.state ~= "running" then return self:get_progress() end
    if self.child and self.child.cancel then self.child:cancel(reason) end
    self.state, self.phase, self.error = "cancelled", "cancelled", reason
    self.result, self.document, self.input, self.raw, self.parts, self.scan, self.active, self.child = nil, nil, nil, nil, nil, nil, nil, nil
    return self:get_progress()
end
-- Reconciliation never stamps data. Deletions form a permanent grow-only set;
-- live records/settings select the greatest (timestamp, canonical JSON) pair.
local ChangeJob = {}; ChangeJob.__index = ChangeJob
local function change_job(mode, value, options)
    return setmetatable({ mode = mode, state = "running", phase = "left", options = options or {},
        completed = 0, child = begin("fields", value, options) }, ChangeJob)
end
function M.begin_merge(left, right, options)
    local job = change_job("merge", left, options)
    job.right_input = right
    return job
end
function M.begin_update(document, change, options)
    local job = change_job("update", document, options)
    job.change = change
    return job
end
function M.begin_import(library, options)
    local job = setmetatable({ mode = "import", state = "running", phase = "import", options = options or {},
        completed = 0, child = library_schema.begin_validate(library, options) }, ChangeJob)
    return job
end
function ChangeJob:_child()
    local progress = self.child:step_one()
    assert(progress.state ~= "error" and progress.state ~= "cancelled", progress.error or "State operation child stopped")
    return progress.state == "done"
end
function ChangeJob:_canonical(value, after)
    self.child, self.parts, self.canonical_bytes, self.phase, self.after_canonical = json.begin(value), {}, 0, "canonical", after
end
function ChangeJob:_tie(previous, candidate, apply, after)
    self.previous, self.candidate, self.tie_apply, self.after_tie = previous, candidate, apply, after
    self:_canonical(previous, "tie_previous")
end
function ChangeJob:_stamp(previous)
    local now = self.options.now
    if now == nil then now = os.time() end
    assert(integer(now, 0, MAX_INTEGER), "Invalid current Unix timestamp")
    assert(previous < MAX_INTEGER, "Timestamp cannot advance beyond the integer limit")
    return math.max(now, previous + 1)
end
function ChangeJob:_finish_change()
    if self.changed then
        assert(self.document.revision < MAX_INTEGER, "State revision cannot advance")
        self.document.revision = self.document.revision + 1
    end
    self.child, self.phase = M.begin_validate(self.document, self.options), "final"
end
function ChangeJob:_advance()
    if self.phase == "canonical" then
        local p = self.child:step_one()
        assert(p.state ~= "error", p.error)
        if p.chunk then
            self.canonical_bytes = self.canonical_bytes + #p.chunk
            assert(self.canonical_bytes <= M.MAX_BYTES, "Canonical state value exceeds byte limit")
            self.parts[#self.parts + 1] = p.chunk
        end
        if p.state == "done" then
            self.canonical, self.parts, self.child, self.phase = table.concat(self.parts), nil, nil, self.after_canonical
        end
        return
    elseif self.phase == "tie_previous" then
        self.previous_canonical = self.canonical
        self:_canonical(self.candidate, "tie_candidate"); return
    elseif self.phase == "tie_candidate" then
        self.compare_index, self.phase = 1, "tie_compare"
        return
    elseif self.phase == "tie_compare" then
        local order, next_index = json.compare_bytes(self.canonical, self.previous_canonical, self.compare_index, processing.get("compare_bytes", 1024))
        if order == nil then self.compare_index = next_index; return end
        self[self.tie_apply][self.tie_key] = order > 0 and self.candidate or self.previous
        self.previous, self.candidate, self.previous_canonical, self.canonical, self.compare_index, self.phase = nil, nil, nil, nil, nil, self.after_tie
        return
    elseif self.phase == "left" then
        if self:_child() then
            self.document = self.child:get_result()
            if self.mode == "merge" then
                self.child, self.phase = begin("fields", self.right_input, self.options), "right"
            else
                object(self.change, { uiSettings = true, favorite = true, preset = true,
                    uniqueLayout = true, deleteId = true })
                assert(self.change.uniqueLayout == nil or type(self.change.uniqueLayout) == "boolean" and self.change.preset,
                    "Preset creation requires a preset to save")
                assert(not (self.change.preset and self.change.deleteId), "Cannot save and delete in one operation")
                assert(not (self.change.favorite and (self.change.preset or self.change.deleteId)), "Cannot edit a favorite and preset in one operation")
                if self.change.uiSettings ~= nil then
                    object(self.change.uiSettings, setting_keys)
                    assert(self.change.uiSettings.updatedAt == nil, "Timestamps are not UI settings")
                end
                self.child, self.setting_index, self.phase = nil, 1, "update_setting"
            end
        end
    elseif self.phase == "right" then
        if self:_child() then
            self.right, self.child, self.right_input = self.child:get_result(), nil, nil
            self.document.revision = math.max(self.document.revision, self.right.revision)
            self.setting_index, self.phase = 1, "merge_settings"
        end
    elseif self.phase == "merge_settings" then
        local key = setting_names[self.setting_index]
        if key then
            local a, b = self.document.sections.settings.data, self.right.sections.settings.data
            local at, bt = a.updatedAt[key], b.updatedAt[key]
            if bt and (not at or bt > at or bt == at and json.byte_less(json.encode(a[key]), json.encode(b[key]))) then
                a[key], a.updatedAt[key] = b[key], bt
            end
            self.setting_index = self.setting_index + 1
        else self.section_last, self.phase = nil, "merge_sections" end
    elseif self.phase == "merge_sections" then
        local name, value = next(self.right.sections, self.section_last)
        if not name then
            self.favorite_records, self.source_index, self.index, self.phase = {}, 1, 1, "merge_favorites"
            return
        end
        self.section_last = name
        if not M.SECTION_VERSIONS[name] then
            local previous = self.document.sections[name]
            if not previous then self.document.sections[name] = value
            else
                self.sections, self.tie_key = self.document.sections, name
                self:_tie(previous, value, "sections", "merge_sections")
            end
        end
    elseif self.phase == "merge_favorites" then
        local rows = (self.source_index == 1 and self.document or self.right).sections.favorites.data.records
        local record = rows[self.index]
        if record then
            local key = favorite_key(record.assetType, record.assetName)
            local previous = self.favorite_records[key]
            self.index = self.index + 1
            if not previous or record.updatedAt > previous.updatedAt then self.favorite_records[key] = record
            elseif record.updatedAt == previous.updatedAt then
                self.tie_key = key
                self:_tie(previous, record, "favorite_records", "merge_favorites")
            end
        elseif self.source_index == 1 then self.source_index, self.index = 2, 1
        else
            self.document.sections.favorites.data.records = json.array()
            self.records, self.markers, self.source_index, self.index, self.phase = {}, {}, 1, 1, "merge_records"
        end
    elseif self.phase == "merge_records" then
        local source = (self.source_index == 1 and self.document or self.right).sections.presets.data
        local record = source.presets[self.index]
        if not record then
            self.index, self.phase = 1, "merge_markers"; return
        end
        self.index = self.index + 1
        local previous = self.records[record.id]
        if not previous or record.updatedAt > previous.updatedAt then self.records[record.id] = record
        elseif record.updatedAt == previous.updatedAt then
            self.tie_key = record.id
            self:_tie(previous, record, "records", "merge_records")
        end
    elseif self.phase == "merge_markers" then
        local source = (self.source_index == 1 and self.document or self.right).sections.presets.data
        local marker = source.tombstones[self.index]
        if marker then
            local previous = self.markers[marker.id]
            if not previous or marker.deletedAt > previous.deletedAt then self.markers[marker.id] = marker end
            self.index = self.index + 1
        elseif self.source_index == 1 then self.source_index, self.index, self.phase = 2, 1, "merge_records"
        else
            self.document.sections.presets.data = { schemaVersion = 2, presets = json.array(), tombstones = json.array() }
            self.sort_source, self.sort_keys, self.sort_last, self.phase = "records", {}, nil, "sort_collect"
        end
    elseif self.phase == "sort_collect" then
        local key = next(self[self.sort_source], self.sort_last)
        if key then
            self.sort_last = key
            if self.sort_source == "records" and self.markers[key] then return end
            self.sort_key, self.sort_index, self.phase = key, #self.sort_keys + 1, "sort_insert"
        else self.index, self.phase = 1, "sort_publish" end
    elseif self.phase == "sort_insert" then
        local at = self.sort_index
        if at > 1 and json.byte_less(self.sort_key, self.sort_keys[at - 1]) then
            self.sort_keys[at], self.sort_index = self.sort_keys[at - 1], at - 1
        else self.sort_keys[at], self.phase = self.sort_key, "sort_collect" end
    elseif self.phase == "sort_publish" then
        local key = self.sort_keys[self.index]
        if key then
            local output = self.sort_source == "records" and self.document.sections.presets.data.presets
                or self.sort_source == "markers" and self.document.sections.presets.data.tombstones
                or self.document.sections.favorites.data.records
            output[#output + 1], self.index = self[self.sort_source][key], self.index + 1
        elseif self.sort_source == "records" then
            self.sort_source, self.sort_keys, self.sort_last, self.phase = "markers", {}, nil, "sort_collect"
        elseif self.sort_source == "markers" then
            self.sort_source, self.sort_keys, self.sort_last, self.phase = "favorite_records", {}, nil, "sort_collect"
        else self.child, self.phase = M.begin_validate(self.document, self.options), "final" end
    elseif self.phase == "update_setting" then
        local key = setting_names[self.setting_index]
        if key then
            local value = self.change.uiSettings and self.change.uiSettings[key]
            self.setting_index = self.setting_index + 1
            if value == nil then return end
            setting_value(key, value)
            local data = self.document.sections.settings.data
            local previous = data[key]
            if previous == nil then previous = M.optional_setting_defaults[key] end
            if value ~= previous then
                data.updatedAt[key], data[key], self.changed = self:_stamp(data.updatedAt[key] or 0), value, true
            end
            return
        end
        if self.change.favorite ~= nil then
            local favorite = self.change.favorite
            object(favorite, { assetType = true, assetName = true, enabled = true })
            self.favorite_key = favorite_key(favorite.assetType, favorite.assetName)
            assert(type(favorite.enabled) == "boolean", "Invalid favorite membership")
            self.index, self.phase = 1, "update_favorite"
        elseif self.change.preset or self.change.deleteId then self.index, self.phase = 1, "update_find"
        else self:_finish_change() end
    elseif self.phase == "update_favorite" then
        local rows = self.document.sections.favorites.data.records
        local record, candidate = rows[self.index], self.change.favorite
        if record and favorite_key(record.assetType, record.assetName) ~= self.favorite_key then
            self.index = self.index + 1; return
        end
        if not record or record.enabled ~= candidate.enabled then
            assert(record or #rows < M.MAX_FAVORITES, "Favorite record limit exceeded")
            rows[self.index] = { assetType = candidate.assetType, assetName = candidate.assetName,
                enabled = candidate.enabled, updatedAt = self:_stamp(record and record.updatedAt or 0) }
            self.changed = true
        end
        self:_finish_change()
    elseif self.phase == "update_find" then
        local library = self.document.sections.presets.data
        local id = self.change.preset and self.change.preset.id or self.change.deleteId
        local marker = library.tombstones[self.index]
        if marker then
            if marker.id == id then
                assert(not self.change.preset, "Deleted preset IDs cannot be reused")
                self:_finish_change(); return
            end
            self.index = self.index + 1
        else self.index, self.phase = 1, "update_live" end
    elseif self.phase == "update_live" then
        local library = self.document.sections.presets.data
        local id = self.change.preset and self.change.preset.id or self.change.deleteId
        local record = library.presets[self.index]
        if self.change.uniqueLayout ~= nil and record then
            local source = self.change.preset
            if self.change.uniqueLayout and layout.compare_layout({ complete = true, compatibility = source.target, logicalZones = source.zones },
                record) == "match" then
                self.matchedPresetId = record.id
                self:_finish_change()
                return
            end
            assert(record.id ~= id, "New configuration ID already exists")
        end
        if record and record.id ~= id then self.index = self.index + 1; return end
        self.previous_record, self.record_index = record, self.index
        if self.change.deleteId then
            assert(record ~= nil, "Preset to delete was not found")
            library.tombstones[#library.tombstones + 1] = { id = record.id, deletedAt = self:_stamp(record.updatedAt) }
            self.remove_index, self.phase = self.index, "delete_shift"
        else
            local source = self.change.preset
            object(source, { id = true, name = true, createdUtc = true, target = true, zones = true,
                gameBuild = true, updatedAt = true })
            local candidate = { id = source.id, name = source.name, createdUtc = source.createdUtc,
                target = source.target, zones = source.zones, gameBuild = source.gameBuild,
                updatedAt = record and record.updatedAt or 0 }
            self.child = library_schema.begin_validate({ schemaVersion = 2, presets = json.array({ candidate }),
                tombstones = json.array() }, self.options)
            self.phase = "update_validate"
        end
    elseif self.phase == "delete_shift" then
        local rows = self.document.sections.presets.data.presets
        rows[self.remove_index] = rows[self.remove_index + 1]
        if self.remove_index < #rows then self.remove_index = self.remove_index + 1
        else self.changed = true; self:_finish_change() end
    elseif self.phase == "update_validate" then
        if self:_child() then
            self.candidate_record, self.child = self.child:get_result().presets[1], nil
            if self.previous_record then self:_canonical(self.previous_record, "update_previous")
            else self.phase = "update_commit" end
        end
    elseif self.phase == "update_previous" then
        self.previous_canonical = self.canonical
        self:_canonical(self.candidate_record, "update_compare")
    elseif self.phase == "update_compare" then
        local order, next_index = json.compare_bytes(self.canonical, self.previous_canonical, self.compare_index, processing.get("compare_bytes", 1024))
        if order == nil then self.compare_index = next_index; return end
        self.compare_index = nil
        if order == 0 then self:_finish_change()
        else self.phase = "update_commit" end
    elseif self.phase == "update_commit" then
        self.candidate_record.updatedAt = self:_stamp(self.previous_record and self.previous_record.updatedAt or 0)
        self.document.sections.presets.data.presets[self.record_index] = self.candidate_record
        self.changed = true
        self:_finish_change()
    elseif self.phase == "import" then
        if self:_child() then
            self.document = M.new()
            self.document.sections.presets.data, self.child = self.child:get_result(), nil
            self.child, self.phase = M.begin_validate(self.document, self.options), "final"
        end
    elseif self.phase == "final" then
        if self:_child() then
            self.encoded = self.child:get_encoded()
            self.result, self.document, self.right, self.child, self.state, self.phase = self.child:get_result(), nil, nil, nil, "done", "done"
        end
    end
end
function ChangeJob:get_progress()
    return { state = self.state, phase = self.phase, completed = self.completed, processed = self.completed,
        changed = self.changed == true, error = self.error }
end
function ChangeJob:step_one()
    if self.state ~= "running" then return self:get_progress() end
    local ok, err = pcall(self._advance, self)
    self.completed = self.completed + 1
    if not ok then self.state, self.phase, self.error, self.result, self.document, self.child = "error", "error", tostring(err), nil, nil, nil end
    return self:get_progress()
end
function ChangeJob:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do self:step_one(); budget:consume(1) end
    return self:get_progress()
end
function ChangeJob:get_result() return self.state == "done" and self.result or nil end
function ChangeJob:get_document() return self:get_result() end
function ChangeJob:get_encoded() return self.state == "done" and self.encoded or nil end
function ChangeJob:cancel(reason)
    if self.state == "running" then
        if self.child and self.child.cancel then self.child:cancel(reason) end
        self.state, self.phase, self.error, self.result, self.document, self.child = "cancelled", "cancelled", reason, nil, nil, nil
    end
    return self:get_progress()
end
return M
