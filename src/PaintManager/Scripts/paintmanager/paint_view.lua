-- Plain-value paint-list projection. Native collection and learning stay outside
-- this module. Filtering, merge outputs and group appends advance one unit at a
-- time, so replacing a query never starts a synchronous full-list sort.
local processing = require("paintmanager.processing")
local M = {}
local json = require("paintmanager.json")
local plain_work = require("paintmanager.plain_work")
local Job = {}
Job.__index = Job
local NativeJob = {}; NativeJob.__index = NativeJob

local FILTERS = { all = true, unlocked = true, locked = true, ready = true, insufficient = true,
    learned = true, missing = true, checking = true }
local SORTS = { name_asc = true, name_desc = true, count_desc = true, count_asc = true }
local GROUP_NAMES = { "unlocked", "locked", "unknown", "learned", "ready", "missing", "checking", "checking_rows" }
local ROW_NAMES = { "checking_rows" }

local function new_groups()
    return { unlocked = {}, locked = {}, unknown = {}, learned = {}, ready = {}, missing = {}, checking = {} }
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function valid_count(value)
    return finite(value) and value >= 0 and value % 1 == 0
end

-- A nil map belongs to an older box-only snapshot. An explicit false marks a
-- backpack that was checked but could not be read.
local function backpack_count(model, row)
    local counts = model and model.backpackCounts
    if counts == nil then return 0 end
    if type(counts) ~= "table" then return nil end
    local count = counts[row.id]
    if count == nil then return 0 end
    if valid_count(count) then return count end
end

function M.physical_count(model, row)
    if not row then return nil end
    local bag = backpack_count(model, row)
    if valid_count(row.count) then return row.count + (bag or 0) end
    if bag and bag > 0 then return bag end
end

function M.set_backpack_counts(model, counts)
    model.backpackCounts = counts or false
    local bag = counts and 0 or nil
    if counts then
        for _, n in pairs(counts) do bag = bag + n end
    end
    model.backpackPhysicalCount = bag
    model.combinedPhysicalCount = bag and model.physicalCount and bag + model.physicalCount or nil
end

local display_text = require("paintmanager.display_text")
local function normalized_query(value)
    if type(value) ~= "string" then return "" end
    return display_text.fold(value:match("^%s*(.-)%s*$"))
end

local function normalized_options(options)
    options = options or {}
    local max_rows = options.max_rows
    if not finite(max_rows) or max_rows < 1 then max_rows = 128 end
    local max_catalog = options.max_catalog
    if not finite(max_catalog) or max_catalog < 1 then max_catalog = 4096 end
    local filter = options.tab or options.filter
    local favorites = {}
    for key, value in pairs(type(options.favorites) == "table" and options.favorites or {}) do
        if type(key) == "string" and value == true then favorites[key] = true end
    end
    local page_size = math.min(128, math.floor(max_rows))
    local offset = finite(options.offset) and math.max(0, math.floor(options.offset))
        or (finite(options.page) and math.max(0, math.floor(options.page) - 1) or 0) * page_size
    return {
        query = normalized_query(options.query),
        filter = FILTERS[filter] and filter or "all",
        sort = SORTS[options.sort] and options.sort or "name_asc",
        full_catalog = options.full_catalog == true,
        max_rows = options.full_catalog == true and math.min(4096, math.floor(max_catalog)) or page_size,
        offset = options.full_catalog == true and 0 or offset,
        max_catalog = math.min(4096, math.floor(max_catalog)),
        page = math.floor(offset / page_size) + 1,
        favorites = favorites,
        include_groups = options.include_groups ~= false,
    }
end

function M.favorite_key(row)
    if type(row) == "table" and type(row.assetType) == "string" and row.assetType ~= ""
        and type(row.assetName) == "string" and row.assetName ~= "" then
        return row.assetType .. "\0" .. row.assetName
    end
end

function M.stock_current(model, row)
    return model ~= nil and row ~= nil and (model._stockUiRevision == nil
        or row._stockUiRevision == model._stockUiRevision)
end

local function boxes_measured(model, row)
    return M.stock_current(model, row) and model.status == "captured" and model.stockComplete == true and model.cachePreview ~= true
        and row.complete ~= false and valid_count(row.count)
end

function M.stock_measured(model, row)
    return boxes_measured(model, row) and backpack_count(model, row) ~= nil
end

function M.classify(model, row)
    if type(row) ~= "table" then return "checking" end
    if row.recipeState == "unlocked" then return "learned" end
    if row.recipeState == "locked" and boxes_measured(model, row) and type(row.id) == "string" and row.id ~= "" then
        local bag = backpack_count(model, row)
        if row.count + (bag or 0) >= 4 then return "ready" end
        if bag ~= nil then return "missing" end
    end
    return "checking"
end

function M.learn_eligible(model, row)
    return M.classify(model, row) == "ready"
end

local function base_visible(row)
    -- Every admitted known identity survives zero stock and unresolved loading.
    return type(row) == "table"
end

local function matches(model, row, options, category)
    if not base_visible(row, model) then return false end
    local filter = options.filter
    category = category or M.classify(model, row)
    if filter == "learned" and category ~= "learned" then return false end
    if filter == "missing" and category ~= "missing" then return false end
    if filter == "checking" and category ~= "checking" then return false end
    if filter == "unlocked" and row.recipeState ~= "unlocked" then return false end
    if filter == "locked" and row.recipeState ~= "locked" then return false end
    if filter == "ready" and category ~= "ready" then return false end
    if filter == "insufficient" and category ~= "missing" then return false end
    local name = type(row.displayName) == "string" and display_text.fold(row.displayName) or ""
    return options.query == "" or string.find(name, options.query, 1, true) ~= nil
end

-- A convenience predicate for isolated callers. Jobs normalize only once.
function M.matches(model, row, options)
    return type(row) == "table" and matches(model, row, normalized_options(options))
end

-- Loaded objects carry a class-prefixed full name. Unresolved catalog
-- candidates carry the same asset path without that prefix.
function M.capture_key(row)
    local path = row.itemPath or row.id
    if type(path) == "string" and path ~= "" then return path:match("^[^ ]+ (/.+)$") or path end
    return M.favorite_key(row) or row
end

-- The Vault confirms recipe membership before boxes are read. Keep the last
-- displayed stock for matching rows and leave new identities without a count.
local learned_fields = { "recipeState", "assetType", "assetName", "displayName", "iconPath" }
local function merge_learned(previous, snapshot, limit)
    local confirmed, rows, changed, remaining = {}, {}, false, limit
    for _, row in ipairs(snapshot.rows) do
        local key = M.capture_key(row)
        if row.recipeState == "unlocked" and type(key) == "string" and not confirmed[key] and remaining > 0 then
            confirmed[key], remaining = row, remaining - 1
        end
        coroutine.yield()
    end
    for _, old in ipairs(previous.rows or {}) do
        local key = M.capture_key(old)
        local fresh = confirmed[key]
        if fresh then
            confirmed[key] = nil
            local row = old
            for _, field in ipairs(learned_fields) do
                if fresh[field] ~= nil and fresh[field] ~= old[field] then
                    if row == old then
                        row = {}
                        for name, value in pairs(old) do row[name] = value end
                        changed = true
                    end
                    row[field] = fresh[field]
                end
            end
            rows[#rows + 1] = row
        elseif remaining > 0 then
            rows[#rows + 1], remaining = old, remaining - 1
        else changed = true end
        coroutine.yield()
    end
    for _, candidate in ipairs(snapshot.rows) do
        local key = M.capture_key(candidate)
        local fresh = confirmed[key]
        if fresh then
            confirmed[key], changed = nil, true
            rows[#rows + 1] = { id = fresh.id, itemPath = fresh.itemPath, assetType = fresh.assetType,
                assetName = fresh.assetName, displayName = fresh.displayName, iconPath = fresh.iconPath,
                recipeState = "unlocked" }
        end
        coroutine.yield()
    end
    if not changed then return previous end
    local model = {}
    for field, value in pairs(previous) do model[field] = value end
    model.rows = rows
    return model
end

-- Reconcile a completed opening interval against the lists the player saw.
-- The coroutine holds only Lua data and yields after each bounded row operation.
function M.begin_capture(previous, captured, previous_projection, options, limit, learned_overlay)
    local needle = normalized_query(options.query)
    return coroutine.create(function()
        if learned_overlay then
            captured = merge_learned(previous, captured, limit)
            if captured == previous then return previous, previous_projection end
        end
        local rows, by_id = {}, {}
        for _, row in ipairs(captured.rows) do
            rows[#rows + 1] = row
            by_id[M.capture_key(row)] = row
            coroutine.yield()
        end
        local model = captured
        if captured.complete ~= true or captured.catalogComplete ~= true or captured.stockComplete ~= true
            or captured.catalogTruncated or captured.cachePreview or captured.status ~= "captured" then
            for _, old in ipairs(previous.rows or {}) do
                local key = M.capture_key(old)
                if type(key) == "string" and not by_id[key] and #rows < limit then
                    -- Only carry presentation identity. Prior stock, location
                    -- witnesses and Vault membership do not survive this gap.
                    local unknown = { id = old.id, assetType = old.assetType, assetName = old.assetName,
                        displayName = old.displayName, iconPath = old.iconPath, recipeState = "unknown", complete = false }
                    rows[#rows + 1], by_id[key] = unknown, unknown
                end
                coroutine.yield()
            end
            if #rows > #captured.rows then
                model = {}
                for field, value in pairs(captured) do model[field] = value end
                model.rows = rows
            end
        end
        if not previous_projection or previous_projection.sorted then return model end
        local pages = { learned = {}, ready = {}, missing = {}, checking = {} }
        local counts = { learned = 0, ready = 0, missing = 0, checking = 0 }
        local all_rows, added, arrivals = {}, {}, {}
        local function visible(row)
            return needle == "" or display_text.fold(row.displayName or ""):find(needle, 1, true) ~= nil
        end
        local function append(row, category)
            local key = M.capture_key(row)
            local page = pages[category]
            page[#page + 1], all_rows[#all_rows + 1], added[key] = row, row, true
        end
        for _, category in ipairs({ "learned", "ready", "missing", "checking" }) do
            for _, old in ipairs(previous_projection.pages[category]) do
                local row = by_id[M.capture_key(old)]
                if row and not added[M.capture_key(row)] and M.classify(model, row) == category and visible(row) then
                    append(row, category)
                end
                coroutine.yield()
            end
        end
        for _, row in ipairs(rows) do
            local category = M.classify(model, row)
            counts[category] = counts[category] + 1
            if not added[M.capture_key(row)] and visible(row) then arrivals[#arrivals + 1] = row end
            coroutine.yield()
        end
        -- A category first populated after the learned-only pass still honors
        -- the selected sort and favorites. Existing prefixes keep their order.
        if #arrivals > 1 then
            local ordering = M.begin({ rows = arrivals, backpackCounts = model.backpackCounts }, { sort = options.sort, favorites = options.favorites,
                full_catalog = true, include_groups = false, max_catalog = limit }, { background = false })
            local progress
            repeat
                progress = ordering:step_one()
                coroutine.yield()
            until progress.state == "done"
            arrivals = progress.result.rows
        end
        for _, row in ipairs(arrivals) do
            if not added[M.capture_key(row)] then append(row, M.classify(model, row)) end
            coroutine.yield()
        end
        return model, { all_rows = all_rows, pages = pages, category_counts = counts, ready_count = counts.ready,
            favorite_revision = previous_projection.favorite_revision }
    end)
end

local function before(a, b, sort)
    if a.favorite ~= b.favorite then return a.favorite end
    if sort == "count_asc" or sort == "count_desc" then
        if a.count == nil and b.count ~= nil then return false end
        if a.count ~= nil and b.count == nil then return true end
        if a.count ~= b.count then
            if sort == "count_asc" then return a.count < b.count end
            return a.count > b.count
        end
    end
    if a.name ~= b.name then
        if sort == "name_desc" then return a.name > b.name end
        return a.name < b.name
    end
    if a.stable_id ~= b.stable_id then return a.stable_id < b.stable_id end
    if a.id ~= b.id then return a.id < b.id end
    return a.index < b.index
end

local function merge_passes(count)
    local passes, width = 0, 1
    while width < count do passes, width = passes + 1, width * 2 end
    return passes
end

function M.begin(model, options, execution)
    local frozen = normalized_options(options)
    local rows = model and model.rows or {}
    local limit = math.min(#rows, frozen.max_catalog)
    if not (execution and execution.background == false) then
        local background = require("paintmanager.background")
        if type(background.supports) == "function" and background.supports("analysis_view") then
            return setmetatable({ model = model, input = rows, options = frozen, limit = limit,
                state = "running", phase = "view_copy", index = 1, completed = 0,
                total = limit * 2 + 4, plain_rows = json.array(),
                truncated = #rows > limit or (model ~= nil and model.catalogTruncated == true),
                background = background, generation = options and options.generation or 0 }, NativeJob)
        end
    end
    return setmetatable({
        model = model, input = rows, options = frozen, limit = limit,
        state = "running", phase = "filter", index = 1,
        completed = 0, total = limit + 2 + limit * (merge_passes(limit) + 1),
        entries = {}, rows = {}, checking_rows = {}, groups = frozen.include_groups and new_groups() or nil,
        category_counts = { learned = 0, ready = 0, missing = 0, checking = 0 },
        matched_count = 0, checking_matched_count = 0,
        ready_count = 0, base_visible_count = 0, stock = 0,
        truncated = #rows > limit or (model ~= nil and model.catalogTruncated == true),
    }, Job)
end

function NativeJob:cancel(reason)
    if self.state ~= "running" then return end
    if self.child then self.child:cancel(reason) end
    self.state, self.phase, self.reason = "cancelled", "cancelled", reason
    self.input, self.plain_rows, self.child, self.model, self.projection = nil, nil, nil, nil, nil
end
function NativeJob:_progress()
    return { state = self.state, phase = self.phase, completed = self.completed,
        total = self.state == "done" and self.completed or math.max(self.total, self.completed),
        result = self.state == "done" and self.result or nil,
        reason = self.reason, error = self.error, backend = self.backend or "lua_cooperative", defer=self.worker_defer==true }
end
function NativeJob:_unit(budget)
    if self.phase == "view_copy" then
        local row = self.index <= self.limit and self.input[self.index]
        if self.index <= self.limit then
            if type(row) == "table" then
                local count = row.count
                self.plain_rows[#self.plain_rows + 1] = { __viewIndex = self.index,
                    id = type(row.id) == "string" and row.id or nil,
                    displayName = type(row.displayName) == "string" and row.displayName or nil,
                    count = (finite(count) or type(count) == "string") and count or nil,
                    recipeState = type(row.recipeState) == "string" and row.recipeState or nil,
                    assetType = row.assetType, assetName = row.assetName,
                    complete = row.complete ~= false, _stockUiRevision = row._stockUiRevision }
            end
            self.index = self.index + 1
            return
        end
        local source = self.model or {}
        local child, err = self.background.begin_task("analysis_view", {
            model = { status = source.status, stockComplete = source.stockComplete,
                cachePreview = source.cachePreview, _stockUiRevision = source._stockUiRevision, rows = self.plain_rows,
                backpackCounts = source.backpackCounts },
            options = self.options }, { generation = self.generation })
        assert(child, err or "Native paint projection unavailable")
        self.backend = child.backend or "lua_cooperative"
        self.child, self.plain_rows, self.phase = child, nil, "view_worker"
    elseif self.phase == "view_worker" then
        local progress = self.child:step_one(budget)
        self.worker_defer=progress.defer==true
        if progress.state == "running" then return end
        assert(progress.state == "done", progress.error or progress.reason or "Native paint projection stopped")
        self.projection = assert(self.child:get_result(), "Native paint projection result missing")
        self.child, self.phase, self.index = nil, "view_apply", 1
        self.apply_group, self.result = 0, { rows = {}, checking_rows = {},
            groups = self.options.include_groups and new_groups() or nil }
    else
        local names = self.options.include_groups and GROUP_NAMES or ROW_NAMES
        local name = names[self.apply_group]
        local indexes = self.apply_group == 0 and self.projection.rows
            or name == "checking_rows" and self.projection.checking_rows or self.projection.groups[name]
        local destination = self.apply_group == 0 and self.result.rows
            or name == "checking_rows" and self.result.checking_rows or self.result.groups[name]
        local row_index = indexes[self.index]
        if row_index ~= nil then
            assert(type(row_index) == "number" and row_index % 1 == 0 and row_index >= 1 and row_index <= self.limit,
                "Invalid native paint projection index")
            destination[#destination + 1] = assert(self.input[row_index], "Paint projection model changed")
            self.index = self.index + 1
        elseif self.apply_group < #names then self.apply_group, self.index = self.apply_group + 1, 1
        else
            for key, value in pairs(self.projection) do
                if key ~= "rows" and key ~= "groups" and key ~= "checking_rows" then self.result[key] = value end
            end
            self.result.scanned_count, self.result.truncated = self.limit, self.truncated
            self.input, self.model, self.projection = nil, nil, nil
            self.state, self.phase = "done", "done"
        end
    end
end
function NativeJob:step_one(budget)
    if self.state ~= "running" then return self:_progress() end
    self.worker_defer=false
    local phase, advanced = self.phase, 0
    local pure_batch = budget ~= nil and (phase == "view_apply"
        or phase == "view_copy" and self.index <= self.limit)
    local ok, err = pcall(function()
        if pure_batch then
            plain_work.run(function()
                self:_unit(budget)
                advanced = advanced + 1
                -- The copy-to-worker transition checks native bridge capability.
                -- Leave it for its own charged unit; batching admits only the
                -- known plain row-copy/index-application operations here.
                return { state = self.state ~= "running" and self.state
                    or phase == "view_copy" and self.index > self.limit and "copy_complete"
                    or self.phase ~= phase and "phase_complete" or "running" }
            end, budget, { max_primitives = processing.get("projection_primitives", 128) })
        else
            self:_unit(budget)
            advanced = 1
        end
    end)
    self.completed = self.completed + advanced
    if not ok then
        if self.child then self.child:cancel(tostring(err)) end
        self.state, self.phase, self.error = "error", "error", tostring(err)
        self.input, self.model, self.plain_rows, self.child, self.projection = nil, nil, nil, nil, nil
    end
    return self:_progress()
end
function NativeJob:step(budget)
    if not budget then return self:step_one() end
    while self.state=="running" and not budget:expired() do
        self:step_one(budget);budget:consume()
        if self.worker_defer then break end
    end
    return self:_progress()
end

function Job:cancel(reason)
    if self.state ~= "running" then return end
    self.state, self.phase, self.reason = "cancelled", "cancelled", reason
    self.input, self.entries, self.source, self.target, self.rows, self.groups, self.model = nil, nil, nil, nil, nil, nil, nil
    self.checking_rows = nil
end

function Job:_progress()
    return { state = self.state, phase = self.phase, completed = self.completed,
        total = self.total, result = self.state == "done" and self.result or nil, reason = self.reason }
end

function Job:_filter_one()
    if self.index <= self.limit then
        local row = self.input[self.index]
        if type(row) == "table" then
            local category = M.classify(self.model, row)
            self.category_counts[category] = self.category_counts[category] + 1
            if category == "ready" then self.ready_count = self.ready_count + 1 end
            if base_visible(row, self.model) then
                self.base_visible_count = self.base_visible_count + 1
                self.stock = self.stock + (M.physical_count(self.model, row) or 0)
            end
            local checking = self.options.filter == "missing" and category == "checking"
                and (self.options.query == "" or string.find(type(row.displayName) == "string"
                    and display_text.fold(row.displayName) or "", self.options.query, 1, true) ~= nil)
            if matches(self.model, row, self.options, category) or checking then
                local stable_id = M.favorite_key(row)
                self.entries[#self.entries + 1] = {
                    row = row, index = self.index,
                    name = type(row.displayName) == "string" and display_text.sort_key(row.displayName) or "",
                    id = type(row.id) == "string" and row.id or "",
                    count = M.physical_count(self.model, row),
                    state = (row.recipeState == "unlocked" or row.recipeState == "locked") and row.recipeState or "unknown",
                    category = category, auxiliary = checking,
                    stable_id = stable_id or (type(row.id) == "string" and row.id or ""),
                    favorite = stable_id ~= nil and self.options.favorites[stable_id] == true,
                }
            end
        end
        self.index = self.index + 1
        return
    end
    self.input = nil
    self.total = self.limit + 2 + #self.entries * (merge_passes(#self.entries) + 1)
    self.source, self.target, self.width, self.left = self.entries, {}, 1, 1
    self.phase = #self.entries >= 2 and "sort" or "group"
    self.group_index = 1
end

function Job:_merge_one()
    local count = #self.source
    if not self.output then
        self.middle = math.min(self.left + self.width - 1, count)
        self.right = math.min(self.left + self.width * 2 - 1, count)
        self.i, self.j, self.output = self.left, self.middle + 1, self.left
    end
    local take_left = self.i <= self.middle and (self.j > self.right
        or before(self.source[self.i], self.source[self.j], self.options.sort))
    if take_left then
        self.target[self.output], self.i = self.source[self.i], self.i + 1
    else
        self.target[self.output], self.j = self.source[self.j], self.j + 1
    end
    self.output = self.output + 1
    if self.output > self.right then
        self.left, self.output = self.left + self.width * 2, nil
        if self.left > count then
            self.source, self.target = self.target, {}
            self.width, self.left = self.width * 2, 1
            if self.width >= count then
                self.entries, self.source, self.target = self.source, nil, nil
                self.phase = "group"
            end
        end
    end
end

function Job:_group_one()
    local entry = self.entries[self.group_index]
    if entry then
        local counter = entry.auxiliary and "checking_matched_count" or "matched_count"
        self[counter] = self[counter] + 1
        local first = self.options.offset + 1
        if self[counter] >= first and self[counter] < first + self.options.max_rows then
            local rows = entry.auxiliary and self.checking_rows or self.rows
            rows[#rows + 1] = entry.row
            if self.groups then
                local group = self.groups[entry.state]
                if not entry.auxiliary then group[#group + 1] = entry.row end
                self.groups[entry.category][#self.groups[entry.category] + 1] = entry.row
            end
        end
        self.group_index = self.group_index + 1
        return
    end
    self.result = {
        rows = self.rows, groups = self.groups, visible_count = #self.rows,
        checking_rows = self.checking_rows, category_counts = self.category_counts,
        matched_count = self.matched_count, checking_matched_count = self.checking_matched_count,
        page = self.options.page, page_size = self.options.max_rows,
        offset = self.options.offset, paging_count = math.max(self.matched_count, self.checking_matched_count),
        has_more = math.max(self.matched_count, self.checking_matched_count) > self.options.offset + self.options.max_rows,
        page_count = math.max(1, math.ceil(math.max(self.matched_count, self.checking_matched_count) / self.options.max_rows)),
        base_visible_count = self.base_visible_count, ready_count = self.ready_count,
        stock = self.stock, scanned_count = self.limit, truncated = self.truncated,
        query = self.options.query, filter = self.options.filter, sort = self.options.sort,
    }
    self.entries, self.source, self.target, self.model = nil, nil, nil, nil
    self.state, self.phase = "done", "done"
end

function Job:step_one()
    if self.state ~= "running" then return self:_progress() end
    if self.phase == "filter" then self:_filter_one()
    elseif self.phase == "sort" then self:_merge_one()
    elseif self.phase == "group" then self:_group_one() end
    self.completed = self.completed + 1
    return self:_progress()
end

-- Without a supplied budget, one call means one bounded unit. The UI can use
-- step_one under its own render budget without charging the same work twice.
function Job:step(budget)
    if not budget then return self:step_one() end
    while self.state == "running" and not budget:expired() do
        self:step_one()
        budget:consume()
    end
    return self:_progress()
end

return M
