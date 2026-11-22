-- Read-only collection view. All UObject handles are local to one game-thread
-- call and source IDs are ephemeral. Collection includes the three supported
-- box types within range regardless of their ownership metadata.
local processing = require("paintmanager.processing")
local languages = require("paintmanager.languages")
local safe = require("paintmanager.safe")
local json = require("paintmanager.json")
local ship_scope = require("paintmanager.ship_scope")
local M = {}

local box_kinds = {
    ["/Game/Blueprints/Interactives/Containers/BP_Inventory_PlayerBox_Small.BP_Inventory_PlayerBox_Small_C"] = "Small",
    ["/Game/Blueprints/Interactives/Containers/BP_Inventory_PlayerBox.BP_Inventory_PlayerBox_C"] = "Medium",
    ["/Game/Blueprints/Interactives/Containers/BP_Inventory_PlayerBox_Large.BP_Inventory_PlayerBox_Large_C"] = "Large",
}

local function class_kind(owner)
    local name = safe.name(safe.class(owner))
    if not name then return nil end
    -- An exact full asset path excludes subclasses and a custom package that
    -- happens to reuse one of the stock class short names.
    local path = name:match("^[^ ]+ (/.+)$") or name
    return box_kinds[path], true
end

local function is_a(object, path)
    if not safe.valid(object) then return false end
    local ok, result = pcall(function() return object:IsA(path) end)
    return ok and result == true
end

local function identity(object)
    local name = safe.name(object)
    if not name then return nil end
    local ok, address = pcall(function() return object:GetAddress() end)
    -- GetAddress is supported by the pinned loader. A missing address is not
    -- silently downgraded to an ambiguous path-only identity.
    if not ok or (type(address) ~= "number" and type(address) ~= "string") then return nil end
    return name .. "@" .. tostring(address)
end

local function current()
    local ok, controller = pcall(function() return FindFirstOf("VoyagePlayerController") end)
    if not ok or not safe.valid(controller) then return nil, "No valid player controller" end
    local pawn = safe.get(controller, "Pawn")
    if not is_a(pawn, "/Script/Voyage.VoyageCharacter") and type(ship_scope.character) == "function" then
        pawn = ship_scope.character(controller)
    end
    if not is_a(pawn, "/Script/Voyage.VoyageCharacter") then
        return nil, "No current character or vehicle driver is available."
    end
    local world_ok, world = pcall(function() return controller:GetWorld() end)
    local pawn_world_ok, pawn_world = pcall(function() return pawn:GetWorld() end)
    local controller_id, pawn_id = identity(controller), identity(pawn)
    local world_id = world_ok and identity(world) or nil
    if not controller_id or not pawn_id or not world_id or not pawn_world_ok
        or identity(pawn_world) ~= world_id then
        return nil, "Player world/identity unavailable or changing"
    end
    return { controller = controller, pawn = pawn, world = world,
        token = world_id .. "|" .. controller_id .. "|" .. pawn_id, worldId = world_id }
end

-- Stock uses the ship point and selected fixed radius. Only a ready scope is admitted here;
-- global boat discovery belongs to the separate bounded scheduler job.
local function stock_scope(config, expected)
    local scope, err = ship_scope.current(config, expected)
    assert(scope, err or "Ship scan scope is unavailable")
    return scope
end

-- Cheap guard for the UI lifecycle: no FindAllOf/global UObject enumeration.
function M.context()
    local context, err = current()
    return context and context.token or nil, err
end

function M.character_possessed()
    local context = current()
    return context ~= nil and identity(safe.get(context.controller, "Pawn")) == identity(context.pawn)
end

local function all_of(class_name)
    local ok, result = pcall(function() return FindAllOf(class_name) end)
    if not ok then return {}, tostring(result) end
    return result or {}
end

local function location(actor)
    local ok, point = pcall(function() return actor:K2_GetActorLocation() end)
    if not ok or not safe.distance_cm(point, point) then return nil end
    return { X = point.X, Y = point.Y, Z = point.Z }
end

local function is_test(item)
    local path = (safe.name(item) or ""):lower()
    local short = (safe.short_name(item) or ""):lower()
    return path:find("/paints/test/", 1, true) ~= nil
        or short:match("^da_test_paint_") ~= nil or short:match("^default__") ~= nil
end

local function native_paint(item)
    return is_a(item, "/Script/Voyage.VoyagePaintItem")
end

local function asset_path(name)
    if type(name) ~= "string" then return nil end
    return name:match("^[^ ]+ (/.+)$") or name
end

local function primary_type(item)
    local value = safe.get(item, "Type")
    local name = value and safe.text(safe.get(value, "Name")) or nil
    return type(name) == "string" and name ~= "" and name ~= "None" and name or nil
end

local function loaded_primary_asset(asset_type, asset_name)
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    assert(safe.valid(library), "Primary asset lookup unavailable")
    return library:GetObjectFromPrimaryAssetId({
        PrimaryAssetType = { Name = FName(asset_type, EFindName.FNAME_Add) },
        PrimaryAssetName = FName(asset_name, EFindName.FNAME_Add) })
end

-- Presets may refer to a paint absent from the stock and recipe projection.
-- Resolve only a resident primary asset, and retain only presentation strings.
function M.loaded_paint_icon(asset_type, asset_name)
    local ok, icon_path, display_name = pcall(function()
        local item = loaded_primary_asset(asset_type, asset_name)
        if not native_paint(item) or is_test(item) or primary_type(item) ~= asset_type
            or safe.short_name(item) ~= asset_name then return end
        local name = safe.text(safe.get(item, "Name"))
        return safe.name(safe.get(item, "Icon")), name ~= "" and name or nil
    end)
    if ok then return icon_path, display_name end
end

local function add_error(report, message, scope)
    report.complete = false
    if scope ~= "recipe" and scope ~= "catalog" then report.stockComplete = false end
    if scope == "source_discovery" then report.sourceCountIsLowerBound = true end
    if #report.errors < 16 then report.errors[#report.errors + 1] = message end
end

local function new_model(config)
    return {
        schemaVersion = 2, readOnly = true, status = "no_player", complete = true, paintNameRevision = languages.game_revision,
        contextToken = nil, radiusCm = config.ship_radius_cm,
        sources = json.array(), rows = json.array(), stockLocationSources = json.array(), errors = json.array(), warnings = json.array(),
        unresolvedItems = json.array(), sourceCount = 0, includedSourceCount = 0,
        physicalCount = 0, stockComplete = true, catalogComplete = true, excludedTestAssets = 0,
        stockSourcesComplete = false,
        stockSourcesScope = "Per-paint quantities from every processed eligible nearby stock box; completed interval reads, not atomic inventory state; excludes backpack, installed paint and Vault",
        recipeRole = "recipe_library_not_stock",
        recipeCatalogComplete = true, recipeEntriesVisited = 0, recipeEntriesUnresolved = 0,
        unresolvedRecipes = json.array(),
        recipeScope = "Current character Vault.Items within limits; native paint validation and HasItem queries; unresolved/unloaded entries remain explicit; not an exhaustive game catalog",
        sourceInclusion = "automatic_all_nearby_stock_boxes",
        sourcePolicy = "Automatically count all exact Small/Medium/Large stock boxes in radius; assumed player-built by user policy, not a discovered ownership marker",
        countScope = "Loaded exact stock containers within the current ship-centered sphere; interval reads, excludes loose world items, backpack, installed paint and Vault",
        catalogScope = "Current Vault recipe entries, loaded native VoyagePaintItem assets, and discoveries in the backpack and included boxes",
    }
end

local function object_ref(object)
    local name, id, class_name = safe.name(object), identity(object), safe.name(safe.class(object))
    if not name or not id or not class_name then return nil end
    return { name = name, id = id, className = class_name }
end

local function resolve(ref)
    if not ref then return nil, "Missing runtime descriptor" end
    local path = ref.name:match("^[^ ]+ (/.+)$") or ref.name
    local ok, object = pcall(function() return StaticFindObject(path) end)
    if not ok or not safe.valid(object) then return nil, "Runtime object unavailable: " .. ref.name end
    if identity(object) ~= ref.id or (ref.className and safe.name(safe.class(object)) ~= ref.className) then
        return nil, "Runtime object identity/class changed: " .. ref.name
    end
    return object
end

local function read_clock_ms()
    local ok, value = pcall(os.clock)
    if not ok or type(value) ~= "number" or value ~= value or value < 0 or math.abs(value) == math.huge then
        return nil, "os.clock unavailable or invalid; time metrics omitted, unit limits still apply"
    end
    local milliseconds = value * 1000
    if milliseconds == math.huge then
        return nil, "os.clock conversion overflowed; time metrics omitted, unit limits still apply"
    end
    return milliseconds
end
local function elapsed_ms(started, finished)
    if started and finished and finished >= started then return finished - started end
    return nil
end
local Job = {}
Job.__index = Job

function Job:_now_ms()
    local capture = self.model.capture
    if capture.clockValid == false then return nil end
    local value, err = read_clock_ms()
    if not value or (self.lastClockMs and value < self.lastClockMs) then
        capture.clockValid = false
        capture.clockError = err or "os.clock moved backwards; time metrics omitted, unit limits still apply"
        capture.durationMs, capture.workMs, capture.maxSliceMs = nil, nil, nil
        return nil
    end
    self.lastClockMs = value
    return value
end

local function progress(job)
    local waiting=job.phase=="collection_worker" or job.phase=="source_filter_worker" or job.phase=="stock_preview_worker"
    local result = { state = job.state, phase = job.phase,
        processed = not waiting and job.processed-(job.transport_steps or 0) or nil,
        total = not waiting and job.total or nil, error = job.error, metrics = job.model.capture,
        defer=job.worker_defer==true }
    local phase = job.phase
    local coverage_complete = job.catalogOnly and job.model.recipeCatalogComplete == true
        and job.model.catalogComplete == true or not job.catalogOnly and job.model.stockComplete == true
    local presentation = { kind = job.catalogOnly and "recipes" or "stock", phase = phase,
        mode = "indeterminate", coverage = coverage_complete and "pending" or "incomplete" }
    local completed, total, unit
    if phase == "recipe_library_resolve" then
        completed, total, unit = job.recipeIndex - 1, #(job.recipeRecords or {}), "recipes"
    elseif phase == "known_paint_verify" then
        completed, total, unit = job.knownIndex - 1, #(job.knownRecords or {}), "paints"
    elseif phase == "catalog_metadata" then
        completed, total, unit = job.assetIndex - 1, #(job.assetRefs or {}), "paints"
    elseif phase == "source_discovery" or phase == "source_filter" or phase == "source_filter_worker" then
        presentation.phase = "source_discovery"
    elseif job.containersChecked ~= nil and job.state == "running" then
        -- Filtering establishes the eligible total, including candidates beyond
        -- max_sources. Only finished inventory reads contribute to the numerator.
        presentation.phase = "container_read"
        completed, unit = job.containersChecked, "containers"
        if not job.model.sourceCountIsLowerBound then total = job.model.sourceCount end
    end
    if unit and (unit == "containers" or total > 0) then
        presentation.completed, presentation.total, presentation.unit = completed, total, unit
        if total ~= nil then presentation.mode = "determinate" end
    end
    if job.state ~= "running" then
        presentation.mode, presentation.outcome = "idle", job.state
        presentation.coverage = coverage_complete and "complete" or "incomplete"
    end
    result.presentation = presentation
    -- Partial rows are private to the job. They must never replace the last
    -- completed view while another source/metadata unit is still outstanding.
    if job.state == "done" then result.model = job.model end
    return result
end

function Job:get_progress() return progress(self) end

function Job:_after_recipes()
    self.recipesPrepared = true
    if self.learnedRowsPending then
        local latest = current()
        local vault_id = self.vaultRef and self.vaultRef.id
        if not latest or latest.token ~= self.contextToken
            or vault_id and identity(safe.get(latest.pawn, "VaultInventory")) ~= vault_id then
            self:_terminate("cancelled", "Player/world or Vault changed during the learned recipe interval", "context_changed")
            return
        end
        self.learnedCapture = { status = "learned_capture", learnedOnly = true, complete = false, paintNameRevision = self.model.paintNameRevision,
            contextToken = self.contextToken, radiusCm = self.model.radiusCm, rows = self.learnedRowsPending,
            recipeCatalogComplete = self.model.recipeCatalogComplete, catalogComplete = false,
            stockComplete = false, stockSourcesComplete = false, recipeLibraryAvailable = self.model.recipeLibraryAvailable }
        self.learnedRowsPending, self.learnedSeen = nil, nil
        -- Return this completed display stage to the caller before any known
        -- paint lookup or class discovery, even in synchronous processing.
        self.worker_defer = true
    end
    if self.awaitingKnown then self.phase = "catalog_wait"
    elseif self.knownRecords and #self.knownRecords > 0 then
        self.knownIndex, self.phase = 1, "known_paint_verify"
        self.total = self.total + #self.knownRecords
    elseif self.catalogOnly then self.phase = self.discoverCatalog and "catalog_discovery" or "catalog_finish"
    else self.phase = "catalog_discovery" end
end

-- A worker cache read can finish after the one prioritized Vault capture. It
-- supplies only known identities; it does not restart native recipe traversal.
function Job:provide_known(entries, discover)
    assert(self.catalogOnly and self.awaitingKnown and self.state == "running", "Catalog is not waiting for cache identities")
    self.knownRecords, self.discoverCatalog, self.awaitingKnown = entries or {}, discover == true, false
    if self.phase == "catalog_wait" then self:_after_recipes() end
end

function Job:_release_pending()
    if self.analysisJob then self.analysisJob:cancel("Collection ownership released") end
    self.analysisJob, self.analysisCaptures = nil, nil
    self.candidateBatch = nil
    self.analysisCatalogEvents = nil
    self.assetRefs, self.componentRefs, self.candidates = nil, nil, nil
    self.recipeRecords, self.vaultRef = nil, nil
    self.learnedSeen, self.learnedRowsPending = nil, nil
    self.knownRecords, self.seedCatalog = nil, nil
    self.slotBuffer, self.newAssetRefs, self.source = nil, nil, nil
    self.currentCandidate, self.sourceEvidence, self.sourceDistribution = nil, nil, nil
    self.sources = nil
    self.byPath, self.byAsset, self.ambiguousAssets, self.rowIdentities = nil, nil, nil, nil
end

function Job:_recipe_error(message, record)
    self.model.recipeCatalogComplete = false
    add_error(self.model, message, "recipe")
    if record then
        self.model.recipeEntriesUnresolved = self.model.recipeEntriesUnresolved + 1
        if #self.model.unresolvedRecipes < 32 then
            self.model.unresolvedRecipes[#self.model.unresolvedRecipes + 1] = {
                assetName = record.assetName, assetType = record.assetType, reason = message }
        end
    end
end

function Job:_finish_clock()
    local capture = self.model.capture
    if not capture.finishedUtc then
        capture.finishedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
        capture.durationMs = elapsed_ms(self.startedMs, self:_now_ms())
    end
end

function Job:_terminate(state, reason, status)
    self.state, self.phase, self.error = state, state, reason
    self.model.status = status or state
    add_error(self.model, reason or state)
    self.model.sources, self.model.rows, self.model.stockLocationSources = json.array(), json.array(), json.array()
    self.model.physicalCount, self.model.includedSourceCount = 0, 0
    self:_release_pending()
    self:_finish_clock()
end

function Job:cancel(reason)
    if self.state == "running" then self:_terminate("cancelled", reason or "Collection cancelled", "cancelled") end
    return progress(self)
end

local function capture_refs(class_name, limit)
    local objects, err = all_of(class_name)
    local refs, seen, missing, truncated = {}, {}, 0, false
    -- FindAllOf and this descriptor compaction are one indivisible stage. No
    -- native array, iterator, object or wrapper escapes this function.
    for _, object in ipairs(objects) do
        if safe.valid(object) then
            local ref = object_ref(object)
            if ref and not seen[ref.id] then
                if #refs >= limit then truncated = true; break end
                refs[#refs + 1], seen[ref.id] = ref, true
            elseif not ref then missing = missing + 1 end
        end
    end
    return refs, err, missing, truncated
end

function Job:_register_row(row, ref)
    self.byPath[row.itemPath], self.rowIdentities[row.itemPath] = row, ref.id
    local asset = row.assetName
    if asset then
        if self.byAsset[asset] and self.byAsset[asset].itemPath ~= row.itemPath then
            self.byAsset[asset], self.ambiguousAssets[asset] = nil, true
        elseif not self.ambiguousAssets[asset] then self.byAsset[asset] = row end
    end
    if self.analysisCatalogEvents then
        self.analysisCatalogEvents[#self.analysisCatalogEvents + 1] = { itemPath = row.itemPath, assetName = asset }
    end
end

function Job:_paint_metadata(ref, context, from_stock, from_recipe)
    local model = self.model
    local existing = self.byPath[ref.name] or self.byPath[asset_path(ref.name)]
    if existing and self.rowIdentities[ref.name] == ref.id then return existing end
    local item, err = resolve(ref)
    if item and (not native_paint(item) or is_test(item)) then
        if is_test(item) and not self.excludedTests[ref.name] then
            self.excludedTests[ref.name] = true
            model.excludedTestAssets = model.excludedTestAssets + 1
        end
        return
    end
    if not item and not from_stock then
        model.catalogComplete = false
        if from_recipe then self:_recipe_error(err, from_recipe) else add_error(model, err, "catalog") end
        return
    end
    if not existing and #model.rows >= self.config.max_catalog then
        model.catalogTruncated, model.catalogComplete = true, false
        add_error(model, "Paint catalog reached max_catalog", "catalog")
        if from_recipe then self:_recipe_error("Recipe paint excluded by max_catalog", from_recipe) end
        return
    end
    -- A native paint observed atomically in a container remains known physical
    -- paint even if its data asset disappears before this later metadata unit.
    -- Keep a labelled unavailable name/icon/recipe instead of inventing values.
    local row = { id = ref.name, itemPath = ref.name, count = 0,
        stockWitnesses = json.array(), stockLocationHints = json.array(), stockSources = json.array(), stockSourcesComplete = false }
    if existing then for key, value in pairs(existing) do row[key] = value end end
    row.id, row.itemPath = ref.name, ref.name
    row.runtimeRef = { name = ref.name, id = ref.id, className = ref.className }
    row.assetName = item and safe.short_name(item) or ref.assetName
    -- VoyageBaseDataAsset.Type is a public primary-asset type. Serialized IDs
    -- are an additional exact-name fallback only after native paint validation.
    row.assetType = item and primary_type(item) or nil
    if not row.assetType and item and from_recipe and from_recipe.assetName == row.assetName then
        row.assetType = from_recipe.assetType
    end
    row.displayName = item and safe.text(safe.get(item, "Name")) or nil
    row.paintNameLanguage = languages.game_tag
    if row.displayName == "" then row.displayName = nil end
    row.nameAvailable, row.metadataComplete = row.displayName ~= nil, row.displayName ~= nil
    row.iconPath = item and safe.name(safe.get(item, "Icon")) or nil
    row.iconAvailable = row.iconPath ~= nil
    row.recipeState, row.complete, row.errors = "unknown", true, json.array()
    if not row.nameAvailable then
        row.errors[#row.errors + 1] = "Native display text Name unavailable; internal ID is diagnostic only"
    end
    if not item then
        row.complete, row.metadataComplete = false, false
        row.errors[#row.errors + 1] = err
        add_error(model, "Paint metadata no longer available: " .. tostring(row.assetName or ref.name))
    else
        local vault = safe.get(context.pawn, "VaultInventory")
        if is_a(vault, "/Script/Voyage.VoyageBaseInventoryComponent") then
            local query_ok, has_item = pcall(function() return vault:HasItem(item) end)
            if query_ok and type(has_item) == "boolean" then
                row.recipeState = has_item and "unlocked" or "locked"
            else
                row.complete = false
                row.errors[#row.errors + 1] = query_ok and "HasItem did not return bool" or tostring(has_item)
                self:_recipe_error("Recipe query incomplete for " .. tostring(row.assetName), from_recipe)
            end
        else
            row.complete, model.recipeLibraryAvailable = false, false
            if not self.vaultWarning then
                self.vaultWarning = true
                model.warnings[#model.warnings + 1] = "Vault unavailable: recipe states remain unknown"
            end
            self:_recipe_error("Recipe library unavailable", from_recipe)
        end
    end
    local row_index = existing and (self.rowIndexes[ref.name] or self.rowIndexes[asset_path(ref.name)]) or (#model.rows + 1)
    model.rows[row_index] = row
    self.rowIndexes[ref.name], self.rowIndexes[asset_path(ref.name)] = row_index, row_index
    self.learnedRows = (self.learnedRows or 0) + (row.recipeState == "unlocked" and 1 or 0)
        - (existing and existing.recipeState == "unlocked" and 1 or 0)
    if not existing or existing.cachePending then self.verifiedRows = self.verifiedRows + 1 end
    if existing and existing.cachePending then self.pendingRows = self.pendingRows - 1 end
    row.cachePending = nil
    self:_register_row(row, ref)
    self.catalogRevision = (self.catalogRevision or 0) + 1
    return row
end

function Job:_pending_paint(record, reason)
    local path = asset_path(record.itemPath)
    if not path or self.byPath[path] then return end
    if #self.model.rows >= self.config.max_catalog then
        self.model.catalogTruncated = true
        self:_recipe_error("Cached paint excluded by max_catalog", record)
        return
    end
    -- An unloaded object remains a known candidate. Neither cached metadata nor
    -- a failed loaded-only lookup establishes a current membership verdict.
    local name = record.paintNameLanguage == languages.game_tag and (record.name or record.displayName) or nil
    local row = { id = path, itemPath = path, assetType = record.assetType, assetName = record.assetName,
        displayName = name, nameAvailable = name ~= nil, paintNameLanguage = record.paintNameLanguage, iconPath = record.iconPath,
        iconAvailable = record.iconPath ~= nil, metadataComplete = false, metadataCached = true,
        recipeState = "unknown", cachePending = true, count = 0, complete = false,
        stockWitnesses = json.array(), stockLocationHints = json.array(), stockSources = json.array(), stockSourcesComplete = false,
        errors = json.array({ reason or "Known paint is unresolved or not loaded" }) }
    self.model.rows[#self.model.rows + 1], self.byPath[path] = row, row
    self.rowIndexes[path] = #self.model.rows
    self.pendingRows = self.pendingRows + 1
    self:_recipe_error(reason or "Known paint is unresolved or not loaded", record)
    self.catalogRevision = (self.catalogRevision or 0) + 1
end

function Job:_known_paint(record, context)
    local item, reason
    local runtime_ref = record.ref or record.runtimeRef
    if runtime_ref then item, reason = resolve(runtime_ref) end
    if not item and record.assetType and record.assetName then
        local ok, value = pcall(loaded_primary_asset, record.assetType, record.assetName)
        if ok and safe.valid(value) then item = value
        else reason = ok and "Known primary asset is unresolved or not loaded" or tostring(value) end
    end
    if not item and record.itemPath then
        -- A persisted path is only a lookup hint. Revalidate class, native
        -- primary type/name and exact path before reading it as a paint.
        local ok, value = pcall(StaticFindObject, asset_path(record.itemPath))
        if ok and safe.valid(value) then item = value end
    end
    if item then
        local actual_type, actual_name, actual_path = primary_type(item), safe.short_name(item), asset_path(safe.name(item))
        if not native_paint(item) or is_test(item) or actual_name ~= record.assetName
            or (record.itemPath and actual_path ~= asset_path(record.itemPath))
            or (actual_type and record.assetType and actual_type ~= record.assetType)
            or (not actual_type and not runtime_ref) then
            item, reason = nil, "Known paint native identity/type/path did not match"
        end
    end
    if not item then self:_pending_paint(record, reason); return end
    local ref = object_ref(item)
    if not ref then self:_pending_paint(record, "Known paint runtime identity unavailable"); return end
    self:_paint_metadata(ref, context, false, record)
end

function Job:_read_recipes(context)
    local vault = safe.get(context.pawn, "VaultInventory")
    self.recipeRecords, self.recipeIndex = {}, 1
    self.vaultRef = is_a(vault, "/Script/Voyage.VoyageBaseInventoryComponent") and object_ref(vault) or nil
    self.model.vaultRuntimeRef = self.vaultRef
    if not self.vaultRef then
        self:_recipe_error("Recipe library runtime identity unavailable")
        self:_after_recipes()
        return
    end
    self.model.recipeCapturedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
    local visited = 0
    local ok, err = pcall(function()
        vault.Items:ForEach(function(_, wrapped)
            visited = visited + 1
            if visited > self.config.max_slots then
                self.model.recipeEntriesTruncated = true
                self:_recipe_error("Vault recipe entries reached max_slots")
                return true
            end
            -- Capture only primitive identities/serialized IDs. Counts in the
            -- recipe library are never physical stock or a membership verdict.
            local entry = safe.unwrap(wrapped)
            local item = safe.unwrap(safe.get(entry, "Item"))
            local asset_type = safe.get(entry, "AssetType")
            self.recipeRecords[#self.recipeRecords + 1] = {
                ref = safe.valid(item) and object_ref(item) or nil,
                assetName = safe.text(safe.get(entry, "AssetName")),
                assetType = asset_type and safe.text(safe.get(asset_type, "Name")) or nil }
            -- Nil continues the pinned native iterator safely.
        end)
    end)
    self.model.recipeEntriesVisited = visited
    if not ok then self:_recipe_error("Vault recipe traversal failed: " .. tostring(err)) end
    self.total = self.total + #self.recipeRecords
    if #self.recipeRecords > 0 then self.phase = "recipe_library_resolve" else self:_after_recipes() end
    return "Vault.Items.ForEach bounded recipe capture (no cursor/resume API)"
end

function Job:_recipe_record(record, context)
    local item, err
    if record.ref then
        item, err = resolve(record.ref)
    else
        -- This verified Pure lookup only returns an already loaded object. Do
        -- not guess package paths, synchronously load assets or grant recipes.
        local ok, result = pcall(function()
            assert(type(record.assetName) == "string" and record.assetName ~= ""
                and type(record.assetType) == "string" and record.assetType ~= "", "Serialized primary asset ID unavailable")
            local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
            assert(safe.valid(library), "Primary asset lookup unavailable")
            return library:GetObjectFromPrimaryAssetId({
                PrimaryAssetType = { Name = FName(record.assetType, EFindName.FNAME_Add) },
                PrimaryAssetName = FName(record.assetName, EFindName.FNAME_Add) })
        end)
        if ok and safe.valid(result) then item = result
        else err = ok and "Primary asset is unresolved or not loaded" or tostring(result) end
        if item and safe.short_name(item) ~= record.assetName then
            item, err = nil, "Primary asset lookup returned an unexpected asset name"
        end
    end
    if not item then
        self:_recipe_error("Recipe entry unresolved: " .. tostring(err), record)
        return
    end
    if not native_paint(item) or is_test(item) then return end
    local ref = object_ref(item)
    if not ref then self:_recipe_error("Recipe paint runtime identity unavailable", record); return end
    -- HasItem remains authoritative, including false after an interval change.
    -- The native entry and its ItemCount alone never imply an unlocked recipe.
    local row = self:_paint_metadata(ref, context, false, record)
    if self.learnedRowsPending and row and row.recipeState == "unlocked" and not self.learnedSeen[row.id] then
        -- Copy only display metadata while this Vault entry is already in the
        -- budget. The later full catalog owns its rows and native evidence.
        self.learnedSeen[row.id] = true
        self.learnedRowsPending[#self.learnedRowsPending + 1] = { id = row.id, itemPath = row.itemPath,
            assetType = row.assetType, assetName = row.assetName, displayName = row.displayName, iconPath = row.iconPath,
            nameAvailable = row.nameAvailable, iconAvailable = row.iconAvailable, metadataComplete = row.metadataComplete,
            recipeState = "unlocked", complete = false }
    end
end

local function before(a, b)
    return a.distance == b.distance and a.id < b.id or a.distance < b.distance
end

function Job:_candidate(ref)
    local component, err = resolve(ref)
    if not component then add_error(self.model, err, "source_discovery"); return end
    local owner_ok, owner = pcall(function() return component:GetOwner() end)
    if not owner_ok then self.model.sourceCountIsLowerBound = true; return end
    if not safe.valid(owner) then return end
    local kind, identified = class_kind(owner)
    if not identified then self.model.sourceCountIsLowerBound = true; return end
    if not kind then return end
    local world_ok, world = pcall(function() return owner:GetWorld() end)
    if not world_ok or not safe.valid(world) then
        add_error(self.model, "Container world unavailable; excluded", "source_discovery"); return
    end
    local world_id = identity(world)
    if not world_id then self.model.sourceCountIsLowerBound = true; return end
    if world_id ~= self.worldId then return end
    local point = location(owner)
    local scope = stock_scope(self.runtimeConfig, self.model.stockScope)
    local distance = safe.distance_cm(point, scope.center)
    if not distance then add_error(self.model, "Container location unavailable; excluded", "source_discovery"); return end
    if distance > scope.radiusCm then return end
    local owner_ref = object_ref(owner)
    if not owner_ref then add_error(self.model, "Container runtime identity unavailable; excluded", "source_discovery"); return end
    local id = owner_ref.id .. "|" .. ref.id .. "|" .. tostring(owner_ref.className)
    local candidate = { id = id, componentRef = ref, ownerRef = owner_ref, kind = kind,
        distance = distance, point = point,
        persistentId = ship_scope.persistent_actor_id and ship_scope.persistent_actor_id(owner) or nil }
    self.model.sourceCount = self.model.sourceCount + 1
    if self.candidateBatch then self.candidateBatch[#self.candidateBatch + 1] = candidate; return end
    -- Retain only the nearest max_sources while counting every eligible source.
    -- This bounded insertion replaces a large one-tick sort of all candidates.
    local index = #self.candidates + 1
    for i, existing in ipairs(self.candidates) do if before(candidate, existing) then index = i; break end end
    table.insert(self.candidates, index, candidate)
    if #self.candidates > self.config.max_sources then table.remove(self.candidates) end
end

function Job:_finish_source_filter()
    self.componentRefs = nil
    if self.model.sourceCount > #self.candidates then
        self.model.sourcesTruncated = true
        add_error(self.model, "Container candidates reached max_sources; counts are partial")
    end
    self.total = self.total + #self.candidates
    self.sourceIndex, self.containersChecked = 1, 0
    self.phase = #self.candidates > 0 and "container_read" or "finalize"
end

function Job:_queue_source_selection(finished)
    local child, err = require("paintmanager.background").begin_task("analysis_collection", {
        mode = "source_candidates", selected = json.array(self.candidates), candidates = self.candidateBatch,
        maximum = self.config.max_sources, identity = "id" })
    assert(child, err or "Native nearest-container selection unavailable")
    self.analysisJob, self.filterFinished, self.candidateBatch, self.phase = child, finished, json.array(), "source_filter_worker"
end

function Job:_source_error(source, message)
    source.complete = false
    if #source.errors < 8 then source.errors[#source.errors + 1] = message end
    add_error(self.model, "Included container read incomplete: " .. tostring(source.label or source.id))
end

function Job:_next_source()
    if self.source and not self.source.complete then
        add_error(self.model, "Included container read incomplete: " .. tostring(self.source.label))
    end
    self.slotBuffer, self.newAssetRefs, self.source = nil, nil, nil
    self.currentCandidate, self.sourceEvidence, self.sourceDistribution = nil, nil, nil
    self.sourceIndex = self.sourceIndex + 1
    self.phase = self.sourceIndex <= #self.candidates and "container_read" or "finalize"
end

function Job:_read_source(candidate)
    local model = self.model
    local source = { id = candidate.id, ownerPath = candidate.ownerRef.name,
        componentPath = candidate.componentRef.name, kind = candidate.kind,
        distanceCm = candidate.distance, location = candidate.point,
        included = true, complete = true, paintCount = 0, errors = json.array(),
        ownership = "assumed_player_built_by_user_policy", persistentId = candidate.persistentId,
        checkedAt = os.time() }
    self.sources[#self.sources + 1] = source
    -- One ephemeral source bank avoids repeating full native descriptors for
    -- every paint present in a box. Row hints contain only interval indexes.
    model.stockLocationSources[#self.sources] = { sourceId = source.id,
        componentRef = candidate.componentRef, ownerRef = candidate.ownerRef, kind = candidate.kind,
        persistentId = candidate.persistentId, paintCounts = self.leanPresentation and {} or nil }
    model.includedSourceCount = model.includedSourceCount + 1
    self.source, self.slotBuffer, self.newAssetRefs = source, {}, {}
    self.currentCandidate, self.sourceEvidence, self.sourceDistribution = candidate, {}, {}
    self.slotIndex, self.newAssetIndex = 1, 1
    local component, err = resolve(candidate.componentRef)
    if not component then self:_source_error(source, err); self:_next_source(); return end
    local owner_ok, owner = pcall(function() return component:GetOwner() end)
    if not owner_ok or identity(owner) ~= candidate.ownerRef.id or class_kind(owner) ~= candidate.kind then
        self:_source_error(source, "Container owner identity/class changed"); self:_next_source(); return
    end
    local world_ok, world = pcall(function() return owner:GetWorld() end)
    local point = location(owner)
    local scope = stock_scope(self.runtimeConfig, self.model.stockScope)
    local distance = safe.distance_cm(point, scope.center)
    if not world_ok or identity(world) ~= self.worldId or not distance or distance > scope.radiusCm
        or (candidate.persistentId and ship_scope.persistent_actor_id(owner) ~= candidate.persistentId) then
        self:_source_error(source, "Container left the captured world/radius or location became unavailable")
        self:_next_source(); return
    end
    if not self.leanPresentation then
        local label = safe.text(safe.get(component, "Name"))
        if label == "" then label = nil end
        source.displayName, source.runtimeLabel = label, safe.short_name(owner)
        source.label = label or source.runtimeLabel
        source.location, source.distanceCm = point, distance
    end
    if self.leanPresentation then source.ownerPath, source.componentPath, source.distanceCm, source.location = nil, nil, nil, nil end
    source.capturedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ")
    local seen_refs, visited = {}, 0
    local read_ok, read_error = pcall(function()
        component.Items:ForEach(function(_, wrapped)
            visited = visited + 1
            if visited > self.config.max_slots then
                source.complete, source.slotsTruncated = false, true
                -- Pinned TMap iterator supports true to stop, nil to continue.
                -- Never yield through this native callback or return false.
                return true
            end
            local entry = safe.unwrap(wrapped)
            local data = safe.get(entry, "Data")
            local count = data and safe.get(data, "ItemCount")
            if type(count) ~= "number" or count ~= count or math.abs(count) == math.huge
                or count < 0 or count % 1 ~= 0 then
                source.complete = false
                if #source.errors < 8 then source.errors[#source.errors + 1] = "Invalid or unreadable ItemCount" end
                return
            end
            if count == 0 then return end
            local item = safe.unwrap(safe.get(entry, "Item"))
            local asset = safe.text(safe.get(entry, "AssetName"))
            if native_paint(item) then
                if is_test(item) then return end
                local ref = object_ref(item)
                if not ref then
                    self.slotBuffer[#self.slotBuffer + 1] = { assetName = asset, itemCount = count,
                        unresolvedReason = "Native paint runtime identity unavailable" }
                    return
                end
                ref.assetName = safe.short_name(item)
                self.slotBuffer[#self.slotBuffer + 1] = { assetName = asset, itemCount = count,
                    itemPath = ref.name, nativePaint = true }
                if self.rowIdentities[ref.name] ~= ref.id and not seen_refs[ref.id] then
                    seen_refs[ref.id] = true
                    self.newAssetRefs[#self.newAssetRefs + 1] = ref
                end
            elseif not safe.valid(item) then
                self.slotBuffer[#self.slotBuffer + 1] = { assetName = asset, itemCount = count }
            end
        end)
    end)
    source.slotsVisited = visited
    if not read_ok then self:_source_error(source, tostring(read_error))
    elseif source.complete then self.containersChecked = self.containersChecked + 1 end
    self.total = self.total + #self.newAssetRefs + math.ceil(#self.slotBuffer / self.config.aggregate_rows_per_unit)
    if #self.newAssetRefs > 0 then self.phase = "container_paint_metadata"
    elseif #self.slotBuffer > 0 then self.phase = "aggregate"
    else self:_next_source() end
    return true
end

function Job:_aggregate(record)
    local row
    if record.nativePaint then row = self.byPath[record.itemPath]
    else row = self.byAsset[record.assetName] end
    if row and not record.unresolvedReason then
        row.count = row.count + record.itemCount
        self.source.paintCount = self.source.paintCount + record.itemCount
        self.model.physicalCount = self.model.physicalCount + record.itemCount
        -- The display distribution covers every counted source, independently
        -- of the smaller native witness set used only for fresh learning proof.
        -- Source metadata was already captured; no new UObject read is needed.
        local distribution = not self.leanPresentation and self.sourceDistribution[row.itemPath]
        if not self.leanPresentation and not distribution then
            local source = self.source
            distribution = { sourceId = source.id, sourceIndex = #self.sources, count = 0, kind = source.kind,
                label = source.label, displayName = source.displayName, runtimeLabel = source.runtimeLabel,
                distanceCm = source.distanceCm, capturedUtc = source.capturedUtc }
            row.stockSources[#row.stockSources + 1] = distribution
            self.sourceDistribution[row.itemPath] = distribution
        end
        if distribution then distribution.count = distribution.count + record.itemCount end
        -- Retain every native-positive box as a location index for single
        -- actions. Keep a separate first-four witness list for batch proof.
        -- Neither cache authorizes a grant; weak-name matches supply neither.
        if record.nativePaint then
            if self.leanPresentation then
                local counts = self.model.stockLocationSources[#self.sources].paintCounts
                counts[row.itemPath] = (counts[row.itemPath] or 0) + record.itemCount
            end
            local witness = self.sourceEvidence[row.itemPath]
            if not witness then
                local candidate = self.currentCandidate
                witness = { sourceId = self.source.id, componentRef = candidate.componentRef,
                    ownerRef = candidate.ownerRef, kind = candidate.kind, capturedCount = 0 }
                row.stockLocationHints[#row.stockLocationHints + 1] = #self.sources
                if #row.stockWitnesses < 4 then row.stockWitnesses[#row.stockWitnesses + 1] = witness end
                self.sourceEvidence[row.itemPath] = witness
            end
            if witness then witness.capturedCount = witness.capturedCount + record.itemCount end
        end
    else
        self.source.complete = false
        if #self.model.unresolvedItems < 32 then
            self.model.unresolvedItems[#self.model.unresolvedItems + 1] = {
                sourceId = self.source.id, assetName = record.assetName, itemCount = record.itemCount,
                paintCandidate = record.nativePaint == true or safe.paint_hint(record.assetName) ~= nil,
                reason = record.unresolvedReason or "Unresolved item type; no unambiguous loaded native paint asset" }
        end
    end
end

-- Read only the requested loaded paint from one native inventory. The iterator
-- may stop on reaching the threshold; the resulting count is a lower bound.
local function learning_count(component, target_id, needed, max_slots)
    local count, visited, capped = 0, 0, false
    local slots = {}
    local ok, reason = pcall(function()
        component.Items:ForEach(function(key, wrapped)
            if count >= needed or capped then return true end
            visited = visited + 1
            if visited > max_slots then capped = true; return true end
            local entry = safe.unwrap(wrapped)
            local item = safe.unwrap(safe.get(entry, "Item"))
            if identity(item) ~= target_id or not native_paint(item) then return end
            local slot = math.tointeger(safe.unwrap(key))
            assert(slot and slot >= 0 and slot <= 2147483647 and not slots[slot], "Invalid or duplicate learning slot")
            slots[slot] = true
            local quantity = safe.get(safe.get(entry, "Data"), "ItemCount")
            assert(type(quantity) == "number" and quantity >= 0 and quantity < math.huge and quantity % 1 == 0,
                "Witness target count invalid")
            count = count + math.min(quantity, 4)
            if count >= needed then return true end
        end)
    end)
    return { count = count, countIsLowerBound = true, recordsVisited = visited, truncated = capped,
        complete = ok and not capped, error = not ok and tostring(reason) or (capped and "Learning witness reached the record limit" or nil) }
end

local function learning_box(config, model, context, witness)
    local component, component_error = resolve(witness.componentRef)
    local owner, owner_error = resolve(witness.ownerRef)
    assert(component and owner, component_error or owner_error)
    local kind = class_kind(owner)
    assert(is_a(component, "/Script/Voyage.VoyageInventoryWeightLimitedComponent")
        and kind and kind == witness.kind, "Witness container type changed")
    assert(identity(component:GetOwner()) == witness.ownerRef.id and identity(owner:GetWorld()) == context.worldId,
        "Witness owner/world changed")
    local point = location(owner)
    local scope, reason = ship_scope.player_access(config, stock_scope(config, model.stockScope))
    assert(scope, reason or "Ship connection lost before commit")
    local distance = safe.distance_cm(point, scope.center)
    assert(distance and distance <= scope.radiusCm, "Witness left the current ship radius")
    return component, distance
end

-- The callback is the deliberately narrow mutation boundary. It runs in this
-- same game-thread call only after fresh bounded witness reads and final
-- native identity/context checks. No UObject escapes into retained job state.
-- This whole proof + callback is an explicit indivisible commit exception.
function M.with_recipe_proof(config, request, model, callback, audit, options)
    options = options or {}
    local function event(name, data)
        if audit then audit(name, data) end
    end
    assert(type(callback) == "function", "Learning callback unavailable")
    assert(model and model.status == "captured" and model.stockComplete == true, "Fresh physical stock is incomplete")
    assert(config.ship_radius_cm == request.radiusCm and model.radiusCm == request.radiusCm, "Learning radius changed")
    local context, context_error = current()
    assert(context and context.token == request.contextToken and model.contextToken == context.token,
        context_error or "Learning context changed")
    local row
    for _, candidate in ipairs(model.rows or {}) do
        if candidate.id == request.itemPath then row = candidate; break end
    end
    assert(row and require("paintmanager.paint_view").learn_eligible(model, row),
        "Fresh paint row is not eligible for learning")
    assert(row.runtimeRef and request.runtimeRef and row.runtimeRef.id == request.runtimeRef.id,
        "Paint runtime identity changed")
    local item, item_error = resolve(request.runtimeRef)
    assert(item and native_paint(item) and not is_test(item), item_error or "Native paint unavailable")
    local vault, vault_error = resolve(model.vaultRuntimeRef)
    assert(vault and is_a(vault, "/Script/Voyage.VoyageBaseInventoryComponent")
        and identity(safe.get(context.pawn, "VaultInventory")) == model.vaultRuntimeRef.id,
        vault_error or "Recipe library changed")
    assert(identity(safe.get(context.controller, "Pawn")) == identity(context.pawn),
        "Leave vehicle controls before learning a recipe")
    local scope = stock_scope(config, model.stockScope)
    local connected, connection_error = ship_scope.player_access(config, scope)
    assert(connected, connection_error or "Ship signal is unavailable")
    scope = connected
    local origin = scope.center
    local character_origin = location(context.pawn)
    assert(character_origin, "Current character location unavailable")
    local proof = { threshold = 4, physicalPolicy = "keep", count = 0, countIsLowerBound = true, witnesses = json.array(),
        maxWitnesses = 4, origin = origin, radiusCm = scope.radiusCm, shipScopeToken = scope.token,
        semantics = "Same-call bounded live reads; no engine transaction/locking or hard frame deadline" }
    -- Cached single actions visit only the native-positive boxes recorded by
    -- the last ordinary capture. All hints are retained, unlike the first four
    -- batch witnesses; if early boxes emptied, a later known box can qualify.
    -- These ephemeral descriptors never authorize a grant on their own.
    local witnesses = row.stockWitnesses
    if options.cached then witnesses = row.stockLocationHints end
    local maximum = options.cached and config.max_sources or 4
    proof.maxWitnesses = maximum
    local seen, contributions = {}, {}
    local transfer = require("paintmanager.paint_transfer")
    local bag, bag_error = transfer.player_backpack(context.token)
    local bag_id, bag_needed
    if bag then
        bag_id = assert(identity(bag), "Learning backpack identity unavailable")
        seen[bag_id] = true
        local observed = learning_count(bag, request.runtimeRef.id, 4, config.max_slots)
        observed.componentId = bag_id
        proof.backpack = observed
        if observed.complete and observed.count > 0 then
            bag_needed = math.min(observed.count, 4)
            proof.count = observed.count
        end
        event("backpack_rechecked", observed)
    else
        proof.backpack = { complete = false, error = bag_error }
    end

    -- Empty hints are normal when the player carries the entire supply. A
    -- missing or malformed bank may never trigger discovery or a global scan.
    if proof.count < 4 then
        assert(type(witnesses) == "table" and #witnesses <= (config.max_sources or 512),
            "Cached paint locations are unavailable; refresh the paint list")
        if options.cached then
            assert(type(model.stockLocationSources) == "table" and #model.stockLocationSources <= maximum,
                "Cached paint source bank is unavailable; refresh the paint list")
        end
        for index = 1, math.min(#witnesses, maximum) do
            local witness = witnesses[index]
            if options.cached then
                assert(type(witness) == "number" and witness >= 1 and witness % 1 == 0,
                    "Cached paint source index is invalid; refresh the paint list")
                witness = assert(model.stockLocationSources[witness], "Cached paint location is unavailable; refresh the paint list")
            end
            local id = witness.componentRef.id
            assert(not seen[id], "Duplicate learning witness")
            seen[id] = true
            local component, distance = learning_box(config, model, context, witness)
            local needed = 4 - proof.count
            local observed = learning_count(component, request.runtimeRef.id, needed, config.max_slots)
            observed.sourceId, observed.distanceCm = witness.sourceId, distance
            proof.witnesses[#proof.witnesses + 1] = observed
            event("witness_rechecked", observed)
            assert(observed.complete, "Learning witness read failed: " .. tostring(observed.error))
            if observed.count > 0 then
                contributions[#contributions + 1] = { witness = witness, needed = math.min(needed, observed.count) }
                proof.count = proof.count + observed.count
                if proof.count >= 4 then break end
            end
        end
    end
    assert(proof.count >= 4, "Fewer than four native physical cans remain in the backpack and previously scanned boxes; refresh the paint list")

    -- Reuse the actual contributors identified above. Membership, logging and
    -- timing callbacks can run game logic before AddItem. Read each confirmed
    -- contributor again, stopping at its original share of the threshold.
    local function verify_sources(recheck_quantities)
        for _, part in ipairs(contributions) do
            local component = learning_box(config, model, context, part.witness)
            if recheck_quantities then
                local observed = learning_count(component, request.runtimeRef.id, part.needed, config.max_slots)
                assert(observed.complete and observed.count >= part.needed, "Box paint changed before learning; refresh the paint list")
            end
        end
        if bag_needed then
            local current_bag = transfer.player_backpack(request.contextToken)
            assert(current_bag and identity(current_bag) == bag_id, "Backpack identity changed before learning")
            if recheck_quantities then
                local observed = learning_count(current_bag, request.runtimeRef.id, bag_needed, config.max_slots)
                assert(observed.complete and observed.count >= bag_needed, "Backpack paint changed before learning; refresh the paint list")
                current_bag = transfer.player_backpack(request.contextToken)
                assert(current_bag and identity(current_bag) == bag_id, "Backpack changed during final learning proof")
            end
        end
        if recheck_quantities then
            -- A native map read can move a box that was previously in range.
            for _, part in ipairs(contributions) do learning_box(config, model, context, part.witness) end
        end
        local latest, reason = current()
        assert(latest and latest.token == request.contextToken, reason or "Learning context changed before commit")
        assert(config.ship_radius_cm == request.radiusCm, "Learning radius changed before commit")
        assert(safe.distance_cm(location(latest.pawn), character_origin) == 0, "Character moved during final proof")
        local access, access_error = ship_scope.player_access(config, stock_scope(config, model.stockScope))
        assert(access, access_error or "Ship connection lost before commit")
        return latest
    end
    local latest = verify_sources(false)
    item, item_error = resolve(request.runtimeRef)
    vault, vault_error = resolve(model.vaultRuntimeRef)
    assert(item and native_paint(item) and not is_test(item), item_error or "Native paint changed before commit")
    assert(vault and is_a(vault, "/Script/Voyage.VoyageBaseInventoryComponent")
        and identity(safe.get(latest.pawn, "VaultInventory")) == model.vaultRuntimeRef.id,
        vault_error or "Recipe library changed before commit")
    event("stock_proof_ready", proof)
    return callback(latest, item, vault, proof, function() verify_sources(true) end)
end

function Job:_unit(context, budget)
    local phase = self.phase
    if phase == "context" then
        self.contextToken, self.worldId = context.token, context.worldId
        self.model.contextToken, self.model.status = context.token, "captured"
        if self.catalogOnly then
            self.origin = location(context.pawn)
        else
            local scope = stock_scope(self.runtimeConfig, self.expectedShipScope)
            assert(scope.worldId == context.worldId, "Ship and character worlds differ")
            self.model.stockScope, self.model.stockRadiusCm = scope, scope.radiusCm
            self.origin = scope.center
        end
        if not self.origin then self:_terminate("error", "Capture origin unreadable", "error"); return end
        self.model.capture.origin = self.origin
        local vault = safe.get(context.pawn, "VaultInventory")
        self.model.recipeLibraryAvailable = is_a(vault, "/Script/Voyage.VoyageBaseInventoryComponent")
        if self.catalogOnly or self.reuseCatalog then
            self.vaultRef = self.model.recipeLibraryAvailable and object_ref(vault) or nil
            self.model.vaultRuntimeRef = self.vaultRef
        end
        if not self.model.recipeLibraryAvailable then
            self.vaultWarning = true
            self.model.warnings[#self.model.warnings + 1] = "Vault unavailable: recipe states remain unknown"
            self:_recipe_error("Recipe library unavailable")
        end
        if self.seedCatalog then
            assert(self.seedCatalog.contextToken == context.token, "Prepared paint catalog belongs to another character/world")
            self.knownRecords = self.seedCatalog.rows or {}
            self.seedCatalog = nil
        end
        if self.catalogOnly and self.prioritizeRecipes then
            self.phase = "recipe_library_read"
        elseif self.knownRecords and #self.knownRecords > 0 then
            self.knownIndex, self.phase = 1, "known_paint_verify"
            self.total = self.total + #self.knownRecords
        elseif self.catalogOnly and self.recipesPrepared then
            self.phase = self.discoverCatalog and "catalog_discovery" or "catalog_finish"
        elseif self.catalogOnly and not self.discoverCatalog then self.phase = "catalog_finish"
        elseif self.reuseCatalog then self.phase = "source_discovery"
        else self.phase = "recipe_library_read" end
    elseif phase == "known_paint_verify" then
        self:_known_paint(self.knownRecords[self.knownIndex], context)
        self.knownIndex = self.knownIndex + 1
        if self.knownIndex > #self.knownRecords then
            self.knownRecords = nil
            if self.reuseCatalog then self.phase = "source_discovery"
            elseif self.recipesPrepared then self.phase = self.discoverCatalog and "catalog_discovery" or "catalog_finish"
            elseif self.discoverCatalog then self.phase = "recipe_library_read"
            else self.phase = "catalog_finish" end
        end
    elseif phase == "recipe_library_read" then
        return self:_read_recipes(context)
    elseif phase == "recipe_library_resolve" then
        self:_recipe_record(self.recipeRecords[self.recipeIndex], context)
        self.recipeIndex = self.recipeIndex + 1
        if self.recipeIndex > #self.recipeRecords then self.recipeRecords = nil; self:_after_recipes() end
    elseif phase == "catalog_wait" then
        -- The controller polls the separate bounded worker read at this point.
        return
    elseif phase == "catalog_discovery" then
        local refs, err, missing, truncated = capture_refs("VoyagePaintItem", self.config.max_discovery_objects)
        self.assetRefs, self.assetIndex = refs, 1
        self.total = self.total + #refs
        if err or missing > 0 or truncated then
            self.model.catalogComplete = false
            if err then add_error(self.model, "Loaded paint catalog unavailable: " .. err, "catalog") end
            if missing > 0 then add_error(self.model, "Loaded paint catalog contains unreadable runtime identities", "catalog") end
            if truncated then self.model.catalogTruncated = true; add_error(self.model, "Paint descriptor processing limit reached", "catalog") end
        end
        self.phase = #refs > 0 and "catalog_metadata" or (self.catalogOnly and "catalog_finish" or "source_discovery")
        return "FindAllOf(VoyagePaintItem) + descriptor compaction"
    elseif phase == "catalog_metadata" then
        self:_paint_metadata(self.assetRefs[self.assetIndex], context, false)
        self.assetIndex = self.assetIndex + 1
        if self.assetIndex > #self.assetRefs then
            self.assetRefs = nil
            self.phase = self.catalogOnly and "catalog_finish" or "source_discovery"
        end
    elseif phase == "catalog_finish" then
        self.model.stockComplete, self.model.stockSourcesComplete = false, false
        self.model.status = "catalog_prepared"
        self.model.catalogReadiness = "Known paints checked over an interval; physical stock not captured; catalog is not exhaustive"
        self.state, self.phase = "done", "done"
        self:_release_pending()
    elseif phase == "source_discovery" then
        if self.registrySeed then
            self.componentRefs, self.componentIndex = self.registrySeed, 1
            self.registrySeed = nil
            self.total = self.total + #self.componentRefs
            self.model.reusedBoxRegistry = true
            if #self.componentRefs > 0 then self.phase = "source_filter" else self:_finish_source_filter() end
            return
        end
        local refs, err, missing, truncated = capture_refs("VoyageInventoryWeightLimitedComponent", self.config.max_discovery_objects)
        self.componentRefs, self.componentIndex = refs, 1
        self.total = self.total + #refs
        if err then add_error(self.model, "Container discovery failed: " .. err, "source_discovery") end
        if missing > 0 then add_error(self.model, "Container runtime identity unavailable; excluded", "source_discovery") end
        if truncated then
            self.model.sourceDiscoveryTruncated = true
            add_error(self.model, "Container descriptor processing limit reached; counts are partial", "source_discovery")
        end
        if #refs > 0 then self.phase = "source_filter" else self:_finish_source_filter() end
        return "FindAllOf(VoyageInventoryWeightLimitedComponent) + descriptor compaction"
    elseif phase == "source_filter" then
        self:_candidate(self.componentRefs[self.componentIndex])
        self.componentIndex = self.componentIndex + 1
        local finished = self.componentIndex > #self.componentRefs
        if self.candidateBatch and (#self.candidateBatch >= self.sourceBatchRecords or finished and #self.candidateBatch > 0) then
            self:_queue_source_selection(finished)
        elseif finished then self:_finish_source_filter() end
    elseif phase == "source_filter_worker" then
        local result = self.analysisJob:step_one(budget)
        self.worker_defer=result.defer==true
        if result.state == "running" then return end
        assert(result.state == "done", result.error or "Native nearest-container selection stopped")
        self.candidates = assert(self.analysisJob:get_result(), "Native nearest-container result missing")
        self.analysisJob = nil
        if self.filterFinished then self:_finish_source_filter() else self.phase = "source_filter" end
    elseif phase == "container_read" then
        if self:_read_source(self.candidates[self.sourceIndex]) then
            return "Container revalidation + Items.ForEach bounded capture (no cursor/resume API)"
        end
    elseif phase == "container_paint_metadata" then
        self:_paint_metadata(self.newAssetRefs[self.newAssetIndex], context, true)
        self.newAssetIndex = self.newAssetIndex + 1
        if self.newAssetIndex > #self.newAssetRefs then self.phase = "aggregate" end
    elseif phase == "aggregate" then
        if self.analysisCaptures then
            -- Capture already contains only primitive descriptors. Transfer its
            -- ownership to the final worker input instead of counting/sorting on Lua's
            -- game-thread interpreter. Native metadata proof has already run.
            self.analysisCaptures[#self.analysisCaptures + 1] = { sourceIndex = #self.sources,
                candidate = self.currentCandidate, records = json.array(self.slotBuffer),
                catalogEventCount = #self.analysisCatalogEvents }
            self:_next_source()
            if self.progressiveStock and self.phase == "container_read"
                and #self.analysisCaptures >= self.nextPreviewAt then
                local frozen, capture = {}, {}
                for key, value in pairs(self.model) do if key ~= "capture" then frozen[key] = value end end
                for key, value in pairs(self.model.capture) do capture[key] = value end
                frozen.capture, frozen.stockComplete, frozen.complete = capture, false, false
                local child, err = require("paintmanager.background").begin_task("analysis_collection", {
                    model = frozen, captures = self.analysisCaptures, catalogEvents = self.analysisCatalogEvents,
                    sources = self.sources })
                if child then
                    -- Pause native capture until the frozen prefix has crossed
                    -- the worker boundary; future arrays cannot mutate in flight.
                    self.analysisJob, self.phase = child, "stock_preview_worker"
                else self.model.warnings[#self.model.warnings + 1] = tostring(err) end
                self.nextPreviewAt = self.nextPreviewAt * self.previewGrowth
            end
            return
        end
        local batch = self.config.aggregate_rows_per_unit
        local before_units = math.ceil((#self.slotBuffer - self.slotIndex + 1) / batch)
        local count = 0
        repeat
            self:_aggregate(self.slotBuffer[self.slotIndex])
            self.slotIndex, count = self.slotIndex + 1, count + 1
        until self.slotIndex > #self.slotBuffer or count >= batch or budget:expired()
        local after_units = math.ceil((#self.slotBuffer - self.slotIndex + 1) / batch)
        -- A time checkpoint can split an aggregation batch early. Adjust the
        -- remaining progress estimate instead of pretending every batch was full.
        self.total = self.total + 1 + after_units - before_units
        if self.slotIndex > #self.slotBuffer then self:_next_source() end
    elseif phase == "stock_preview_worker" then
        local result = self.analysisJob:step_one(budget)
        self.worker_defer = result.defer == true
        if result.state == "running" then return end
        if result.state == "done" then
            stock_scope(self.runtimeConfig, self.model.stockScope)
            local preview = self.analysisJob:get_result()
            preview.status, preview.stockComplete, preview.stockSourcesComplete = "preparing", false, false
            preview.cachePreview = false
            self.stockPreview, self.stockPreviewRevision = preview, self.stockPreviewRevision + 1
        end
        self.analysisJob, self.phase = nil, "container_read"
    elseif phase == "backpack_metadata" then
        local row = self.backpackMissing[self.backpackIndex]
        local item = StaticFindObject(asset_path(row.itemPath))
        local ref = object_ref(item)
        if ref and native_paint(item) and not is_test(item) then
            local paint = self:_paint_metadata(ref, context, true)
            if paint and not paint.assetType and paint.assetName == row.assetName then paint.assetType = row.assetType end
        else
            add_error(self.model, "Backpack paint asset changed during capture", "catalog")
        end
        self.backpackIndex = self.backpackIndex + 1
        if self.backpackIndex > #self.backpackMissing then self.backpackMissing, self.phase = nil, "finalize" end
    elseif phase == "finalize" then
        if not self.backpackChecked then
            -- A prepared catalog can predate a newly acquired backpack-only
            -- color. Resolve each missing asset in its own bounded unit.
            local bag = require("paintmanager.paint_transfer").backpack(self.runtimeConfig, self.contextToken)
            require("paintmanager.paint_view").set_backpack_counts(self.model, bag and bag.counts)
            self.model.combinedCountScope = "Physical cans in the current backpack and eligible ship boxes"
            self.backpackChecked = true
            local missing, seen = {}, {}
            for _, row in ipairs(bag and bag.paints or {}) do
                if not self.byPath[row.itemPath] and not seen[row.itemPath] then
                    missing[#missing + 1], seen[row.itemPath] = row, true
                end
            end
            if #missing > 0 then
                self.backpackMissing, self.backpackIndex, self.phase = missing, 1, "backpack_metadata"
                return
            end
        end
        if self.analysisCaptures then
            -- Metrics keep changing while transport/polling advances. Freeze a
            -- separate shallow metrics record; all native-stage arrays are now
            -- complete and remain immutable. Keep live timings on the caller.
            local frozen, capture = {}, {}
            for key, value in pairs(self.model) do if key ~= "capture" then frozen[key] = value end end
            for key, value in pairs(self.model.capture) do capture[key] = value end
            frozen.capture = capture
            local background = require("paintmanager.background")
            local child, err = background.begin_task("analysis_collection", {
                model = frozen, captures = self.analysisCaptures, catalogEvents = self.analysisCatalogEvents,
                sources = self.leanPresentation and self.sources or nil })
            assert(child, err or "Native collection analysis unavailable")
            self.analysisJob, self.analysisCaptures, self.phase = child, nil, "collection_worker"
            return
        end
        table.sort(self.model.rows, function(a, b)
            local left, right = a.displayName or "", b.displayName or ""
            return left == right and a.id < b.id or left < right
        end)
        self.model.stockSourcesComplete = not self.leanPresentation and self.model.stockComplete == true
        self.distributionIndex = 1
        self.total = self.total + #self.model.rows
        if #self.model.rows > 0 then self.phase = "distribution_finalize"
        else
            self.state, self.phase = "done", "done"
            self:_release_pending()
        end
    elseif phase == "collection_worker" then
        local result = self.analysisJob:step_one(budget)
        self.worker_defer=result.defer==true
        if result.state == "running" then return end
        assert(result.state == "done", result.error or result.reason or "Native collection analysis stopped")
        local latest, latest_error = current()
        assert(latest and latest.token == self.contextToken, latest_error or "Collection context changed before worker result acceptance")
        assert(not self.vaultRef or identity(safe.get(latest.pawn, "VaultInventory")) == self.vaultRef.id,
            "Collection Vault changed before worker result acceptance")
        stock_scope(self.runtimeConfig, self.model.stockScope)
        local capture = self.model.capture
        self.model = assert(self.analysisJob:get_result(), "Native collection result missing")
        require("paintmanager.paint_view").set_backpack_counts(self.model, self.model.backpackCounts)
        self.model.capture = capture
        self.model.capture.backgroundAnalysis = false
        self.model.capture.executionBackend = self.executionBackend
        self.analysisJob = nil
        self.state, self.phase = "done", "done"
        self:_release_pending()
    elseif phase == "distribution_finalize" then
        -- A later source error makes the full distribution a lower bound for
        -- every row. Publish that final verdict one bounded plain row at a time.
        self.model.rows[self.distributionIndex].stockSourcesComplete = self.model.stockSourcesComplete
        self.distributionIndex = self.distributionIndex + 1
        if self.distributionIndex > #self.model.rows then
            self.state, self.phase = "done", "done"
            self:_release_pending()
        end
    else error("Unknown collection job phase: " .. tostring(phase)) end
end

local function fallback_budget()
    return { expired = function() return false end, consume = function() end,
        metrics = function() return { clockValid = true, semantics = "unlimited synchronous compatibility call" } end }
end

function Job:step(budget)
    if self.state ~= "running" then return progress(self) end
    budget = budget or { used = false, expired = function(self) return self.used end,
        consume = function(self) self.used = true end }
    assert(type(budget.expired) == "function" and type(budget.consume) == "function", "Invalid work budget")
    local started, units, transport_checked = self:_now_ms(), 0, false
    while self.state == "running" and not budget:expired() do
        local phase, unit_started = self.phase, self:_now_ms()
        self.worker_defer=false
        local ok, atomic = pcall(function()
            if not self.catalogOnly and self.runtimeConfig.ship_radius_cm ~= self.config.ship_radius_cm then
                self:_terminate("cancelled", "Radius changed during collection; restart the job", "cancelled")
                return
            end
            -- Worker transfer/poll/decode touches owned primitives only. The
            -- lifecycle revokes its generation; native identity is checked on
            -- the next native unit and once before accepting a final result.
            if phase == "collection_worker" or phase == "source_filter_worker" or phase == "stock_preview_worker" then
                if not transport_checked then
                    local latest, reason = current()
                    if not latest or latest.token ~= self.contextToken
                        or (self.vaultRef and identity(safe.get(latest.pawn, "VaultInventory")) ~= self.vaultRef.id) then
                        self:_terminate("cancelled", reason or "Collection context/Vault changed during worker transport", "context_changed")
                        return
                    end
                    transport_checked = true
                end
                return self:_unit(nil, budget)
            end
            local context, err = current()
            if not context then
                self:_terminate(self.contextToken and "cancelled" or "error", err,
                    self.contextToken and "context_changed" or "no_player")
                return
            end
            if self.contextToken and context.token ~= self.contextToken then
                self:_terminate("cancelled", "Player/world changed during collection; restart the job", "context_changed")
                return
            end
            if self.vaultRef and identity(safe.get(context.pawn, "VaultInventory")) ~= self.vaultRef.id then
                self:_terminate("cancelled", "Recipe library changed during collection; restart the job", "context_changed")
                return
            end
            if not self.catalogOnly and self.model.stockScope then
                stock_scope(self.runtimeConfig, self.model.stockScope)
            end
            return self:_unit(context, budget)
        end)
        local duration = elapsed_ms(unit_started, self:_now_ms())
        processing.record("collection." .. phase, duration, 1,
            { clock = "os.clock; platform-dependent", indivisible = ok and atomic ~= nil, backend = self.executionBackend })
        budget:consume(1)
        units, self.processed = units + 1, self.processed + 1
        if phase=="collection_worker" or phase=="source_filter_worker" then
            self.transport_steps=(self.transport_steps or 0)+1
        end
        if ok and atomic then
            self.model.capture.uninterruptibleStages[#self.model.capture.uninterruptibleStages + 1] = {
                phase = phase, operation = atomic, elapsedMs = duration, clockValid = duration ~= nil,
                limitation = "Cannot preempt this native call/descriptor copy from Lua" }
        end
        if not ok then self:_terminate("error", tostring(atomic), "error") end
        if self.worker_defer or self.phase == "catalog_wait" then break end
    end
    if units > 0 then
        local capture, duration = self.model.capture, elapsed_ms(started, self:_now_ms())
        capture.slices = capture.slices + 1
        if duration and capture.workMs and capture.maxSliceMs then
            capture.workMs = capture.workMs + duration
            capture.maxSliceMs = math.max(capture.maxSliceMs, duration)
        end
        local metrics = type(budget.metrics) == "function" and budget:metrics() or nil
        if metrics then
            capture.budgetMs, capture.maxUnitsPerSlice = metrics.budgetMs, metrics.maxUnits
            capture.budgetClockValid = metrics.clockValid
            if metrics.overBudget then capture.overruns = capture.overruns + 1 end
        end
    end
    if self.state ~= "running" then self:_finish_clock() end
    return progress(self)
end

-- No UObject work here. The live radius is checked for cancellation only; every
-- actual work option is a frozen numeric/plain snapshot of the requested config.
function M.begin(config, execution)
    local prepared_catalog = execution and execution.catalog
    -- The catalog just completed for this opening is part of this interval.
    -- The hints switch controls reuse of older catalogs by other callers.
    if not (execution and execution.openCatalog == true and prepared_catalog and prepared_catalog.status == "catalog_prepared")
        and not processing.get("paint_hints_enabled", true) then prepared_catalog = nil end
    local snapshot = {}
    for _, key in ipairs({ "max_catalog", "max_sources", "max_slots", "max_discovery_objects", "aggregate_rows_per_unit",
        "ship_radius_cm", "ship_min_radius_cm", "ship_max_radius_cm", "ship_max_candidates" }) do
        snapshot[key] = config[key]
    end
    snapshot.max_discovery_objects = snapshot.max_discovery_objects or 100000
    snapshot.aggregate_rows_per_unit = processing.get("aggregate_rows_per_unit", snapshot.aggregate_rows_per_unit or 32)
    local model = new_model(snapshot)
    local lean = execution and execution.presentation == "paints"
    if lean then model.presentation, model.stockSourcesOmitted = "paints", true end
    if prepared_catalog then
        model.paintNameRevision = prepared_catalog.paintNameRevision
        model.preparedCatalog = true
        -- This pass checks the identities supplied by the catalog. Missing
        -- identities from a partial catalog cannot become proven absences.
        model.catalogComplete = prepared_catalog.catalogComplete == true
        model.recipeCatalogComplete = prepared_catalog.recipeCatalogComplete == true
        if not model.catalogComplete or not model.recipeCatalogComplete then model.complete = false end
        model.catalogScope = "Prepared native paint identities plus discoveries in the backpack and included boxes"
        model.recipeScope = "Prepared known paints revalidated against the current native Vault:HasItem; no Vault.Items enumeration in this capture"
    end
    local started, clock_error = read_clock_ms()
    model.capture = { atomic = false, startedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        slices = 0, workMs = started and 0 or nil, maxSliceMs = started and 0 or nil, overruns = 0,
        clockValid = started ~= nil, clockError = clock_error,
        clock = "os.clock; platform-dependent, not an engine frame-time measurement",
        uninterruptibleStages = json.array(),
        semantics = "Completed bounded reads over an interval; inventories may change between containers. Never an atomic save/world snapshot." }
    local background = require("paintmanager.background")
    local use_worker = not (execution and execution.background == false)
        and type(background.supports) == "function" and background.supports("analysis_collection")
    return setmetatable({ state = "running", phase = "context", processed = 0, total = 5,
        config = snapshot, runtimeConfig = config, model = model, startedMs = started, lastClockMs = started,
        byPath = {}, byAsset = {}, ambiguousAssets = {}, rowIdentities = {}, rowIndexes = {},
        excludedTests = {}, candidates = {}, sources = lean and json.array() or model.sources,
        executionBackend = use_worker and (type(background.describe) == "function" and background.describe().backend or "lua_cooperative") or "cooperative",
        analysisCaptures = use_worker and json.array() or nil,
        candidateBatch = use_worker and json.array() or nil,
        analysisCatalogEvents = use_worker and json.array() or nil,
        seedCatalog = prepared_catalog,
        expectedShipScope = execution and execution.shipScope,
        registrySeed = processing.get("box_hints_enabled", true) and execution and execution.sourceRefs or nil,
        progressiveStock = execution and execution.progressiveStock == true and processing.get("stock_prefix_enabled", true),
        sourceBatchRecords = processing.get("source_batch_records", 128),
        nextPreviewAt = processing.get("stock_prefix_first", 8), previewGrowth = processing.get("stock_prefix_growth", 2), stockPreviewRevision = 0,
        leanPresentation = lean,
        reuseCatalog = prepared_catalog ~= nil,
        catalogRevision = 0, verifiedRows = 0, pendingRows = 0 }, Job)
end

-- Session-only discovery descriptors. Empty boxes remain in the registry;
-- neither this bank nor any native address is persisted to disk.
function Job:get_registry()
    if self.state ~= "done" then return nil end
    local refs = {}
    for _, source in ipairs(self.model.stockLocationSources or {}) do refs[#refs + 1] = source.componentRef end
    return refs
end

-- Current-pass preview for bounded in-place count patches. Worker aggregation
-- may defer all quantities until completion; no historical count enters here.
function Job:get_stock_preview()
    return self.stockPreview, self.stockPreviewRevision
end

-- This projection never resolves engine objects. Each entry carries a primary
-- ID and an observed asset path, not runtime identity or recipe/stock state.
function M.cache_entry(row)
    if type(row) ~= "table" or type(row.assetType) ~= "string" or row.assetType == ""
        or type(row.assetName) ~= "string" or row.assetName == "" then return nil end
    local path = asset_path(row.itemPath)
    if not path or path:sub(1, 6) ~= "/Game/" then return nil end
    return { assetType = row.assetType, assetName = row.assetName, itemPath = path,
        name = row.displayName, paintNameLanguage = row.paintNameLanguage, iconPath = asset_path(row.iconPath) }
end

function M.cache_entries(model)
    local result = json.array()
    for _, row in ipairs(model and model.rows or {}) do
        local entry = M.cache_entry(row)
        if entry then result[#result + 1] = entry end
    end
    return result
end

function M.begin_catalog(config, options)
    options = options or {}
    local job = M.begin(config, { background = false })
    job.catalogOnly, job.discoverCatalog = true, options.discover ~= false
    job.prioritizeRecipes, job.recipesPrepared = options.skipRecipes ~= true, options.skipRecipes == true
    job.awaitingKnown = options.awaitingKnown == true
    if options.learnedFirst and job.prioritizeRecipes then
        job.learnedRowsPending, job.learnedSeen = json.array(), {}
    end
    -- Bind before the first native unit, so a Pawn switch between lifecycle
    -- admission and the first scheduler opportunity cannot attach this job to
    -- the replacement character.
    job.contextToken = options.contextToken
    job.knownRecords = options.entries or {}
    job.model.stockComplete, job.model.stockSourcesComplete = false, false
    job.model.catalogScope = "Known cached native paint identities plus bounded current Vault and loaded-paint discovery; not an exhaustive game catalog"
    return job
end

function Job:get_catalog()
    if self.state == "done" then return self.model end
end

function Job:get_learned_capture()
    if self.state == "running" or self.state == "done" then return self.learnedCapture end
end

function Job:get_cache_entries()
    return M.cache_entries(self.model)
end

function Job:get_preview()
    -- A preview is explicitly an interval, read-only preparation view. Stock
    -- and mutation eligibility remain unavailable until a completed box scan.
    self.model.readiness = { phase = self.phase, verifiedRows = self.verifiedRows, pendingRows = self.pendingRows,
        complete = self.state == "done", recipeComplete = self.recipesPrepared == true,
        learnedRows = self.learnedRows or 0, exhaustive = false, revision = self.catalogRevision }
    return self.model
end

-- Shared native identity and eligibility checks used by explicit stock transfers.
M.native_context = current
M.runtime_ref = object_ref
M.resolve_ref = resolve
M.box_kind = class_kind
M.primary_type = primary_type
function M.inventory_refs(limit)
    return capture_refs("VoyageInventoryWeightLimitedComponent", limit)
end
function M.eligible_paint(item)
    return native_paint(item) and not is_test(item)
end

-- Compatibility/testing entry point. Runtime UI commands use begin/step with
-- the shared per-tick budget; this explicit wrapper deliberately does not yield.
function M.scan(config)
    local job = M.begin(config, { background = false })
    local result = job:step(fallback_budget())
    return result.model or job.model
end

return M
