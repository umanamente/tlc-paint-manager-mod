-- Box origins travel with this player's native HUD snapshot in ordinary saves.
-- A pending row reserves its backpack slot before either inventory is touched.
-- Only a verified take makes the row available to a subsequent return.
local bridge = require("paintmanager.save_bridge")
local ids = require("paintmanager.persistent_id")
local lifecycle = require("paintmanager.save_lifecycle")
local M = {}
local Ledger = {}
Ledger.__index = Ledger

local PREFIX = "PaintManager.TransferOrigins/1"
local MAX_BYTES, MAX_FINGERPRINT = bridge.max_bytes, 1024
local BOX_KINDS = { Small = true, Medium = true, Large = true }
local FIELDS = { "boxId", "boxKind", "assetType", "assetName", "itemPath", "slot", "fingerprint", "count" }
local owners = setmetatable({}, { __mode = "v" })

local function integer(value, minimum, maximum)
    return math.type(value) == "integer" and value >= minimum and value <= maximum
end

local function field(value, maximum)
    return type(value) == "string" and #value > 0 and #value <= maximum
        and not value:find("[%c]") and utf8.len(value) ~= nil
end

local function copy_record(r)
    local out = {}
    for _, name in ipairs(FIELDS) do out[name] = r[name] end
    return out
end

local ZERO_STAMP = "0000000000000000"
local function may_restamp(fingerprint)
    local stamp, flags = fingerprint:match("^([0-9a-f]+):([0-9a-f][0-9a-f]):")
    return stamp and #stamp == 16 and (stamp == ZERO_STAMP or tonumber(flags, 16) & 1 == 0)
end

-- Native AddToSlot refreshes a zero timestamp or one without the keep-time
-- flag. All other serialized fields must survive a direct slot transfer.
function M.same_transferred_fingerprint(before, after)
    if type(before) ~= "string" or type(after) ~= "string" then return false end
    if before == after then return true end
    if not may_restamp(before) or before:sub(17) ~= after:sub(17) then return false end
    local stamp = after:sub(1,16)
    return #stamp == 16 and stamp ~= ZERO_STAMP and stamp:find("[^0-9a-f]") == nil
end

local function same_record(a, b, allow_restamp)
    if not a or not b then return false end
    for _, name in ipairs(FIELDS) do
        if a[name] ~= b[name] and not (allow_restamp and name == "fingerprint"
            and M.same_transferred_fingerprint(a.fingerprint, b.fingerprint)) then return false end
    end
    return true
end

local function row(record, state)
    assert(type(record) == "table" and ids.is_valid(record.boxId) and BOX_KINDS[record.boxKind],
        "Transfer origin requires a supported box and its full persistent ID")
    assert(integer(record.slot, 0, 2147483647) and integer(record.count, 1, 2147483647)
        and field(record.assetType, 128) and field(record.assetName, 256)
        and field(record.itemPath, 1024) and field(record.fingerprint, MAX_FINGERPRINT),
        "Transfer origin slot or paint evidence is invalid")
    local r = copy_record(record)
    r.state = state
    -- Rows usually share the same box, native asset path or serialized payload.
    -- Keep exact values and intern them once in each saved snapshot.
    r.box = table.concat({ "B", r.boxId, r.boxKind }, "\t") .. "\n"
    r.paint = table.concat({ "I", r.assetType, r.assetName, r.itemPath }, "\t") .. "\n"
    r.payload = "F\t" .. r.fingerprint .. "\n"
    return r
end

local function decode(raw)
    if raw == nil then return {}, 0 end
    assert(#raw <= MAX_BYTES and raw:sub(-1) == "\n", "Malformed transfer origin document")
    local version, revision, index = raw:match("^([^\t\n]+)\t(%d+)\n()")
    revision = tonumber(revision)
    assert(version == PREFIX and integer(revision, 1, math.maxinteger), "Unsupported transfer origin document")
    local rows, dictionary = {}, {}
    while index <= #raw do
        local stop = assert(raw:find("\n", index, true), "Truncated transfer origin row")
        local line = raw:sub(index, stop - 1)
        local tag = line:sub(1, 1)
        if tag == "B" then
            local id, kind = line:match("^B\t([^\t]+)\t([^\t]+)$")
            assert(ids.is_valid(id) and BOX_KINDS[kind], "Invalid transfer box entry")
            dictionary[#dictionary + 1] = { boxId = id, boxKind = kind }
        elseif tag == "I" then
            local asset_type, asset_name, path = line:match("^I\t([^\t]+)\t([^\t]+)\t([^\t]+)$")
            assert(field(asset_type, 128) and field(asset_name, 256) and field(path, 1024), "Invalid transfer paint entry")
            dictionary[#dictionary + 1] = { assetType = asset_type, assetName = asset_name, itemPath = path }
        elseif tag == "F" then
            local fingerprint = line:match("^F\t([^\t]+)$")
            assert(field(fingerprint, MAX_FINGERPRINT), "Invalid transfer fingerprint entry")
            dictionary[#dictionary + 1] = { fingerprint = fingerprint }
        else
            local state, slot, count, b, p, f = line:match("^([AP])\t(%d+)\t(%d+)\t(%d+)\t(%d+)\t(%d+)$")
            assert(state, "Malformed transfer origin row")
            local box, paint, data = dictionary[tonumber(b)], dictionary[tonumber(p)], dictionary[tonumber(f)]
            assert(box and paint and data, "Missing transfer origin dictionary entry")
            local value = row({ boxId = box.boxId, boxKind = box.boxKind, assetType = paint.assetType,
                assetName = paint.assetName, itemPath = paint.itemPath, slot = tonumber(slot),
                fingerprint = data.fingerprint, count = tonumber(count) }, state)
            assert(not rows[value.slot], "Duplicate transfer origin slot")
            rows[value.slot] = value
        end
        index = stop + 1
    end
    return rows, revision
end

local function sorted_slots(rows)
    local slots = {}
    for slot in pairs(rows) do slots[#slots + 1] = slot end
    table.sort(slots)
    return slots
end

local function encode(rows, revision, unique_fingerprint_slot, measured_payload)
    local output = { PREFIX .. "\t" .. tostring(revision) .. "\n" }
    local slots = sorted_slots(rows)
    local indexes, size, uses = {}, 0, 0
    local function intern(line, key)
        key = key or line
        local index = indexes[key]
        if index then return index end
        size = size + 1
        indexes[key] = size
        output[#output + 1] = line
        return size
    end
    for _, slot in ipairs(slots) do
        local r = rows[slot]
        if measured_payload == r.payload then uses = uses + 1 end
        -- Use an impossible dictionary key only when measuring a hypothetical
        -- fresh timestamp. The actual saved dictionary always uses exact text.
        local key = slot == unique_fingerprint_slot and ("\0" .. r.payload) or nil
        local box, paint, payload = intern(r.box), intern(r.paint), intern(r.payload, key)
        output[#output + 1] = string.format("%s\t%d\t%d\t%d\t%d\t%d\n", r.state, slot, r.count, box, paint, payload)
    end
    return table.concat(output), uses > 1, #slots
end

local function read(binding, already_bound)
    local snapshot = bridge.read_transfer_origins(binding.contextToken, already_bound and binding or nil)
    assert((snapshot.status == "missing" or snapshot.status == "present") and snapshot.scopeId == binding.scopeId,
        snapshot.error or "Transfer origin HUD changed")
    local ready, generation = lifecycle.is_ready(binding.contextToken, binding.scopeId)
    assert(ready and generation == binding.loadGeneration, "Transfer origin load generation changed")
    return snapshot.raw
end

function M.new(context_token)
    return setmetatable({ context = context_token }, Ledger)
end

function Ledger:load()
    if self.busy or self.ticket then return nil, "A transfer origin operation is active" end
    self.busy = true
    local ok, err = pcall(function()
        local binding = self.binding or bridge.storage_context(self.context)
        assert(binding.status == "ready", binding.error or "Transfer origin storage is unavailable")
        local raw = read(binding, self.binding ~= nil)
        if not self.rows or self.raw ~= raw then self.rows, self.revision = decode(raw) end
        self.raw, self.binding = raw, binding
        self.ownerKey = binding.scopeId .. "|" .. tostring(binding.loadGeneration)
    end)
    self.busy = nil
    if not ok then self.rows = nil; return nil, tostring(err) end
    return true
end

function Ledger:current()
    if not self.rows then return nil, "Transfer origins have not been loaded" end
    local ok, err = pcall(function()
        local actual = read(self.binding, true)
        assert(actual == self.raw, "Transfer origins changed since loading")
    end)
    if not ok then return nil, tostring(err) end
    return true
end

function Ledger:entries()
    local records = {}
    for _, slot in ipairs(sorted_slots(self.rows or {})) do
        local r = self.rows[slot]
        if r.state == "A" then records[#records + 1] = copy_record(r) end
    end
    return records
end

-- Atomically replace the encoded rows named by changes. false removes a row.
-- Cache publication happens only after the carrier's compare-and-set/readback.
local function stage(self, raw, revision)
    local result = bridge.write_transfer_origins(self.binding, self.raw, raw)
    assert(result.status == "staged" and result.scopeId == self.binding.scopeId,
        result.error or "Transfer origin publication failed")
    self.raw, self.revision = raw, revision
end

-- Status/count edits keep dictionary references unchanged. Deletion can also
-- use this representation if compacting would lengthen later references.
local function edit_encoded_row(raw, revision, before, after)
    local header = assert(raw:find("\n", 1, true))
    local prefix = "\n" .. before.state .. "\t" .. before.slot .. "\t" .. before.count .. "\t"
    local first, last = raw:find(prefix, header, true)
    if not first then return nil end -- An older valid document may use padded numbers.
    local fields = ""
    if after then
        fields = after.state .. "\t" .. after.slot .. "\t" .. after.count .. "\t"
    else
        last = assert(raw:find("\n", last + 1, true))
    end
    return PREFIX .. "\t" .. revision .. raw:sub(header, first) .. fields .. raw:sub(last + 1)
end

local function publish(self, changes, prepare_slot, taking, removed)
    assert(self.rows and self.revision < math.maxinteger, "Transfer origin document is unavailable")
    local revision = self.revision + 1
    local next_rows = {}
    for slot, value in pairs(self.rows) do next_rows[slot] = value end
    for slot, value in pairs(changes) do next_rows[slot] = value or nil end
    local pending = prepare_slot ~= nil and next_rows[prepare_slot]
    local measured = taking and may_restamp(pending.fingerprint) and pending.payload
    local raw, shared, count = encode(next_rows, revision, nil, measured)
    if #raw > MAX_BYTES and removed then
        raw = edit_encoded_row(self.raw, revision, removed, nil) or raw
    end
    assert(#raw <= MAX_BYTES, "Transfer origin storage is full")
    if prepare_slot ~= nil then
        -- The verified row may obtain a distinct timestamp of the same width.
        -- Reserve for that row before any mutation, including dictionary
        -- references whose index gains a decimal digit.
        local digit_growth = #tostring(revision + 1) - #tostring(revision)
        local required = #raw + digit_growth
        -- A unique fingerprint can be replaced in place. Splitting a shared
        -- entry adds at most its line and one digit per dictionary reference.
        if shared and required + #pending.payload + 3 * count > MAX_BYTES then
            required = #encode(next_rows, revision + 1, prepare_slot)
        end
        local rollback = self.raw and #self.raw + #tostring(revision + 1) - #tostring(self.revision) or 0
        assert(required <= MAX_BYTES and rollback <= MAX_BYTES, "Transfer origin storage is full")
    end
    stage(self, raw, revision)
    self.rows = next_rows
    return shared
end

function Ledger:prepare(direction, record)
    if self.busy or not self.rows or owners[self.ownerKey] then return nil, "Transfer origin storage is busy or unavailable" end
    owners[self.ownerKey], self.busy = self, true
    local ok, err = pcall(function()
        assert(direction == "take" or direction == "return", "Unknown transfer direction")
        assert(self.revision < math.maxinteger - 1, "Transfer origin revision is exhausted")
        local pending = row(record, "P")
        local old = self.rows[pending.slot]
        if direction == "return" then
            assert(old and old.state == "A" and same_record(old, pending), "Current backpack can has no matching box origin")
        end
        -- The transfer caller has freshly proved an empty backpack slot for a
        -- take. Its previous association cannot authorize the replacement can.
        local revision = self.revision + 1
        local previous_raw = self.raw
        local updated = direction == "return" and edit_encoded_row(self.raw, revision, old, pending)
        local shared
        if updated then
            assert(#updated + #tostring(revision + 1) - #tostring(revision) <= MAX_BYTES, "Transfer origin storage is full")
            stage(self, updated, revision)
            self.rows[pending.slot] = pending
        else
            shared = publish(self, { [pending.slot] = pending }, pending.slot, direction == "take")
        end
        self.ticket = { direction = direction, pending = pending, previous = old,
            previousRaw = previous_raw, sharedPayload = shared }
    end)
    self.busy = nil
    if not ok then owners[self.ownerKey] = nil; return nil, tostring(err) end
    return self.ticket
end

-- The pending document was generated by this ledger and its readback is the
-- CAS input. Confirm its row directly, keeping every unrelated byte intact.
local function confirm_take(self, ticket, next_row)
    local previous, raw = ticket.pending, self.raw
    if previous.payload ~= next_row.payload then
        if ticket.sharedPayload then return false end
        local index = assert(raw:find("\n" .. previous.payload, 1, true))
        raw = raw:sub(1, index) .. next_row.payload .. raw:sub(index + 1 + #previous.payload)
    end
    local revision = self.revision + 1
    stage(self, assert(edit_encoded_row(raw, revision, previous, next_row)), revision)
    self.rows[previous.slot] = next_row
    return true
end

local function settle(self, ticket, verified, remaining, cancel)
    if self.busy or not ticket or ticket ~= self.ticket or owners[self.ownerKey] ~= self then
        return nil, "Transfer origin attempt is no longer current"
    end
    self.busy = true
    local ok, err = pcall(function()
        local next_row
        if cancel then
            local revision, previous = self.revision + 1, ticket.previousRaw
            local start = previous and previous:find("\n", 1, true)
            local body = start and previous:sub(start) or "\n"
            stage(self, PREFIX .. "\t" .. revision .. body, revision)
            self.rows[ticket.pending.slot] = ticket.previous
            return
        elseif ticket.direction == "take" then
            next_row = row(verified, "A")
            assert(same_record(ticket.pending, next_row, true), "Verified transfer differs from the prepared origin")
            if confirm_take(self, ticket, next_row) then return end
        else
            assert(verified == nil or same_record(ticket.pending, verified), "Verified return differs from the prepared origin")
            if remaining ~= nil then
                assert(integer(remaining, 0, ticket.pending.count - 1), "Invalid remaining quantity after return")
                if remaining > 0 then
                    assert(verified, "Partial return requires the original backpack evidence")
                    local remainder = copy_record(ticket.pending)
                    remainder.count = remaining
                    next_row = row(remainder, "A")
                end
            end
        end
        if next_row and ticket.direction == "return" then
            local revision = self.revision + 1
            stage(self, assert(edit_encoded_row(self.raw, revision, ticket.pending, next_row)), revision)
            self.rows[ticket.pending.slot] = next_row
        else
            publish(self, { [ticket.pending.slot] = next_row or false }, nil, nil,
                ticket.direction == "return" and ticket.pending or nil)
        end
    end)
    self.busy = nil
    if not ok then return nil, tostring(err) end
    owners[self.ownerKey], self.ticket = nil, nil
    return true
end

function Ledger:commit(ticket, verified, remaining)
    return settle(self, ticket, verified, remaining, false)
end

-- Only a transfer cancelled before the native inventory call may undo intent.
function Ledger:cancel(ticket)
    return settle(self, ticket, nil, nil, true)
end

local function witness_counts(slots)
    local counts, unknown = {}, {}
    for _, live in pairs(slots) do
        if live.itemPath then
            if live.fingerprint then
                local key = live.itemPath .. "\0" .. live.fingerprint
                counts[key] = (counts[key] or 0) + 1
            else
                unknown[live.itemPath] = true
            end
        end
    end
    return counts, unknown
end

-- The caller supplies a COMPLETE fresh backpack read. Unreadable fingerprints
-- keep the origin dormant. A different slot cannot inherit it by color alone.
function Ledger:reconcile(slots)
    if self.busy or not self.rows or owners[self.ownerKey] then return nil, "Transfer origin storage is busy or unavailable" end
    if type(slots) ~= "table" then return nil, "A complete backpack read is required" end
    local changes, counts, unknown = {}
    for slot, r in pairs(self.rows) do
        local live = slots[slot]
        if not live or live.assetType ~= r.assetType or live.assetName ~= r.assetName or live.itemPath ~= r.itemPath
            or live.fingerprint and live.fingerprint ~= r.fingerprint then
            changes[slot] = false
        elseif live.itemCount ~= r.count then
            changes[slot] = false
            -- Spending cans from this stack can only decrease its claim. An
            -- unobserved payload, another matching slot or any increase makes
            -- that attribution ambiguous.
            if r.state == "A" and live.fingerprint == r.fingerprint and integer(live.itemCount, 1, r.count - 1) then
                if not counts then counts, unknown = witness_counts(slots) end
                if not unknown[r.itemPath] and counts[r.itemPath .. "\0" .. r.fingerprint] == 1 then
                    local remainder = copy_record(r)
                    remainder.count = live.itemCount
                    changes[slot] = row(remainder, "A")
                end
            end
        end
    end
    if not next(changes) then return true end
    owners[self.ownerKey], self.busy = self, true
    local ok, err = pcall(publish, self, changes)
    owners[self.ownerKey], self.busy = nil, nil
    if not ok then return nil, tostring(err) end
    return true
end

return M
