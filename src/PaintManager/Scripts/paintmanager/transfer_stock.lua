-- Apply final, verified box contents to the last complete nearby-stock interval.
-- Only plain session data is used here. Learn still reads native stock at commit.
local json = require("paintmanager.json")
local ids = require("paintmanager.persistent_id")
local ship = require("paintmanager.ship_scope")
local M = {}

local function copy(record)
    local result = {}
    for key, value in pairs(record) do result[key] = value end
    return result
end

local function valid_count(n)
    return type(n) == "number" and n >= 0 and n < math.huge and n % 1 == 0
end

local function same_ref(a, b)
    return a and b and type(a.id) == "string" and a.id == b.id
end

local function changed_sources(bank, observations, scope)
    local by_id, replacements = {}, {}
    for index, source in ipairs(bank) do
        if type(source.paintCounts) ~= "table" then return nil end
        local id = source.persistentId
        if id then
            if by_id[id] == nil then by_id[id] = index else by_id[id] = false end
        end
    end
    for _, observation in ipairs(observations) do
        local id = observation.persistentId
        if not ids.is_valid(id) then return nil end
        local index = by_id[id]
        local source = bank[index]
        local inside, distance = ship.contains(scope, observation.location)
        if inside then
            if not source or replacements[index] or type(observation.counts) ~= "table"
                or source.kind ~= observation.kind or not same_ref(source.componentRef, observation.componentRef)
                or not same_ref(source.ownerRef, observation.ownerRef) then return nil end
            replacements[index] = copy(source)
            replacements[index].paintCounts = copy(observation.counts)
        elseif not distance or index ~= nil then
            -- A known box leaving the radius invalidates coverage of the old interval.
            return nil
        else
            -- A confirmed return to an unrelated remote box cannot change nearby stock.
            for _, known in ipairs(bank) do
                if same_ref(known.ownerRef, observation.ownerRef) or same_ref(known.componentRef, observation.componentRef) then
                    return nil
                end
            end
        end
    end
    return replacements
end

local function cache_paints(counts, rows)
    local paints, seen = json.array(), {}
    for path, count in pairs(counts) do
        local row = rows[path]
        if count > 0 and row.assetType and row.assetName then
            local key = row.assetType .. "\0" .. row.assetName
            if not seen[key] then
                seen[key] = true
                local asset_path = path:match("^[^ ]+ (/.+)$") or path
                paints[#paints + 1] = { assetType = row.assetType, assetName = row.assetName,
                    itemPath = asset_path:sub(1, 6) == "/Game/" and asset_path or nil }
            end
        end
    end
    table.sort(paints, function(a, b)
        return a.assetType == b.assetType and a.assetName < b.assetName or a.assetType < b.assetType
    end)
    return paints
end

function M.reconcile(model, observations, scope, context_token, backpack_counts)
    if type(model) ~= "table" or type(observations) ~= "table" or #observations == 0
        or model.status ~= "captured" or not model.stockComplete or not model.stockSourcesOmitted
        or model.cachePreview or model.learnedOnly or type(context_token) ~= "string"
        or model.contextToken ~= context_token or not ship.same_scope(model.stockScope, scope)
        or type(model.rows) ~= "table" or type(model.stockLocationSources) ~= "table"
        or #model.stockLocationSources ~= model.includedSourceCount then return nil end
    local replacements = changed_sources(model.stockLocationSources, observations, scope)
    if not replacements then return nil end

    local result, rows, previous, changed_ids = copy(model), {}, {}, {}
    result.rows, result.stockLocationSources, result.physicalCount = json.array(), json.array(), 0
    for index, original in ipairs(model.rows) do
        local row = copy(original)
        if type(row.itemPath) ~= "string" or rows[row.itemPath] then return nil end
        row.count, row.stockLocationHints, row.stockWitnesses = 0, json.array(), json.array()
        rows[row.itemPath], result.rows[index] = row, row
    end
    for index, original in ipairs(model.stockLocationSources) do
        local source = replacements[index] or original
        result.stockLocationSources[index] = source
        if replacements[index] then changed_ids[source.persistentId] = source end
        for path, n in pairs(original.paintCounts) do
            if not rows[path] or not valid_count(n) then return nil end
            previous[path] = (previous[path] or 0) + n
        end
        for path, n in pairs(source.paintCounts) do
            local row = rows[path]
            if not row or not valid_count(n) then return nil end
            if n > 0 then
                row.count = row.count + n
                result.physicalCount = result.physicalCount + n
                row.stockLocationHints[#row.stockLocationHints + 1] = index
                if #row.stockWitnesses < 4 then
                    row.stockWitnesses[#row.stockWitnesses + 1] = { sourceId = source.sourceId,
                        ownerRef = source.ownerRef, componentRef = source.componentRef,
                        kind = source.kind, capturedCount = n }
                end
            end
        end
    end
    -- Weak name-only matches can contribute to a count but never to a witness.
    -- Preserve their existing policy by requesting a fresh interval in that case.
    for _, original in ipairs(model.rows) do
        if (previous[original.itemPath] or 0) ~= original.count then return nil end
    end
    if model.boxCache then
        result.boxCache = json.array()
        local checked = os.time()
        for index, entry in ipairs(model.boxCache) do
            local source = changed_ids[entry.persistentId]
            if source then
                entry = copy(entry)
                entry.paints, entry.lastCheckedAt = cache_paints(source.paintCounts, rows), checked
            end
            result.boxCache[index] = entry
        end
    end
    require("paintmanager.paint_view").set_backpack_counts(result, backpack_counts)
    return result
end

return M
