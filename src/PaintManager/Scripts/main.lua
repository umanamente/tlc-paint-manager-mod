-- PaintManager: game-thread UI, bounded reads and explicit click-only learning.
local config = require("paintmanager.config")
local processing = require("paintmanager.processing")
processing.bind(config)
local developer_profiles = require("paintmanager.developer_profiles")
local json = require("paintmanager.json")
local adapter = require("paintmanager.game_adapter")
local reflection = require("paintmanager.reflection")
local collection = require("paintmanager.collection")
local paint_view = require("paintmanager.paint_view")
local ui_module = require("paintmanager.ui")
local ui_resources_module = require("paintmanager.ui_resources")
local work_budget = require("paintmanager.work_budget")
local learning = require("paintmanager.learn_recipe")
local bulk_learning = require("paintmanager.learn_all")
local native_clock = require("paintmanager.clock")
local report_writer = require("paintmanager.report_writer")
local background = require("paintmanager.background")
local preset_workflow = require("paintmanager.preset_workflow")
local save_store = require("paintmanager.save_store")
local save_lifecycle = require("paintmanager.save_lifecycle")
local save_bridge = require("paintmanager.save_bridge")
local paint_cache_module = require("paintmanager.paint_cache")
local stock_cache_module = require("paintmanager.stock_cache")
local ship_scope = require("paintmanager.ship_scope")
local preferences_module = require("paintmanager.ui_preferences")
local progress_module = require("paintmanager.ui_progress")
local transfers = require("paintmanager.transfer_controls").new(config)
local transfer_stock = require("paintmanager.transfer_stock")
local i18n = require("paintmanager.i18n")
local languages = require("paintmanager.languages")
local t = i18n.text
local player_messages = require("paintmanager.player_messages")

local source = debug.getinfo(1, "S").source
local script_dir = source:match("^@(.*)[/\\]main%.lua$")
assert(script_dir, "PaintManager cannot resolve its Scripts directory")
local mod_dir = script_dir:match("^(.*)[/\\]Scripts$")
assert(mod_dir, "PaintManager must be installed under PaintManager/Scripts")
local developer = { profile_path = mod_dir .. "/PaintManager.processing-profile.json",
    startup_path = mod_dir .. "/PaintManager.processing-next-start.json", scheduler_ms = 0 }
developer.startup_profile, developer.startup_migration = developer_profiles.consume_startup(developer.startup_path, config)
local pending, busy, sequence = {}, false, 0
local active_command, pumping = nil, false
local running_job, paused_open = nil, nil
-- Completed/cancelled mutation receipts contain plain values only and must be
-- allowed to finish writing after the UI or native player context disappears.
local receipt_jobs = {}
local bulk_request = nil
local preset_request, preset_view = nil, nil
local deposit_similar
local storage_snapshot = nil
local sync_event = nil
local preset_cache = require("paintmanager.preset_cache").new()
local pump_sequence = 0
local model, context_token = nil, nil
-- Retain mutation uncertainty across cache/storage errors in the same native
-- world/player scope. A missing token is not evidence of a new runtime scope.
local learning_scope_token = nil
-- The last completed interval may be displayed on a quick reopen. Only model
-- belongs to this opening and may supply candidate locations to native proof.
local recent_snapshot, learned_scope
local window, context_elapsed = nil, 0
local function clear_paints()
    model, recent_snapshot, learned_scope = nil, nil, nil
    if window then window:set_model(nil) end
end
local closing_seen = false
local function window_closing()
    return window and type(window.is_closing) == "function" and window:is_closing() == true or false
end
local ui_settings = { showDeveloperTools = false }
local ui_favorites = {}
local progress_generation = 0
local preferences, cancel_job, progress_view
local progress_tracker = progress_module.new(config)
local session = os.date("!%Y%m%dT%H%M%SZ") .. "-" .. tostring(math.floor(os.clock() * 1000))

local function is_learning(command)
    return command == "learn_recipe" or command == "learn_all"
end
local function is_action(command) return is_learning(command) or command == "preset_apply" or transfers.is_command(command) end
local function learning_preparation(job)
    return job and is_learning(job.command) and job.phase == "collect" and job.collector
        and type(job.collector.is_preparing) == "function" and job.collector:is_preparing() == true
end
local function is_preset(command) return command == "preset_refresh" or command == "preset_save"
    or command == "preset_apply" or command == "preset_import" or command == "preset_delete" end
-- Explicit legacy migration source. The account-local aggregate is stored by
-- the repository outside this deploy-owned directory; this source is untouched.
local preset_path = assert(mod_dir:match("^(.*)[/\\][^/\\]+$"), "PaintManager parent directory unavailable")
    .. "/PaintManager.presets.json"

local function successful_outcome(outcome)
    return outcome == "learned" or outcome == "already_known" or outcome == "completed" or outcome == "none_available"
        or outcome == "applied_verified" or outcome == "already_matches"
end

local function output_error(progress)
    local message = progress.error or "Report output failed"
    if #(progress.cleanupErrors or {}) > 0 then
        message = message .. "; temporary-file cleanup failed: " .. table.concat(progress.cleanupErrors, "; ")
    end
    return message
end

local function log(message)
    -- Diagnostics are optional: a loader log failure must never turn an
    -- already committed action into a rejected UI callback or strand a pump.
    return pcall(print, "[PaintManager " .. config.version .. "] " .. message .. "\n")
end

local ui_resources = ui_resources_module.new(config, { log = log })

if developer.startup_migration and developer.startup_migration.migrated then
    log("Processing startup profile migrated; retired fields: " .. table.concat(developer.startup_migration.retiredKeys, ", "))
end

local function installed_build()
    local normalized = script_dir:gsub("\\", "/")
    local steamapps = normalized:match("^(.*)/common/[^/]+/Voyage/Binaries/Win64/ue4ss/Mods/PaintManager/Scripts$")
    if steamapps == nil then return "unknown" end
    local file = io.open(steamapps .. "/appmanifest_1783560.acf", "r")
    if not file then return "unknown" end
    local data = file:read("a")
    file:close()
    return data:match('"buildid"%s+"(%d+)"') or "unknown"
end

local build = installed_build()
local paint_cache = paint_cache_module.new(config, build)
local stock_cache = stock_cache_module.new(config, build)
local connection = { connected = false }
local function same_snapshot_scope(snapshot, token, scope)
    return snapshot and scope and snapshot.contextToken == token and snapshot.shipToken == scope.token
        and snapshot.shipId == scope.shipId and snapshot.shipRadius == config.ship_radius_cm
end
local last_ship_scope_token, last_connection_key = nil, nil

local function character_possessed()
    return type(collection.character_possessed) ~= "function" or collection.character_possessed()
end

local function connection_check()
    local scope, reason, detail = ship_scope.player_access(config)
    connection = detail or {}
    connection.connected, connection.reason = scope ~= nil, reason
    connection.scope = scope
    connection.shipOwnerId = scope and scope.runtimeRef and scope.runtimeRef.id
    local diagnostic_key = tostring(connection.code) .. "|" .. tostring(reason)
    if not scope and diagnostic_key ~= last_connection_key then
        log("Ship connection unavailable: " .. tostring(connection.code or "unavailable") .. "; " .. tostring(reason))
    end
    last_connection_key = diagnostic_key
    if window and type(window.set_connection) == "function" then window:set_connection(connection) end
    if recent_snapshot and not same_snapshot_scope(recent_snapshot, context_token, scope)
        or learned_scope and (not scope or learned_scope.token ~= scope.token or learned_scope.shipId ~= scope.shipId) then
        clear_paints()
    end
    return connection.connected, reason
end
local function begin_report(kind, data)
    sequence = sequence + 1
    local report = {
        schemaVersion = 1, modVersion = config.version, readOnly = not is_action(kind),
        kind = kind, capturedUtc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        session = session, sequence = sequence, gameBuild = build,
        referenceGameBuild = config.reference_build,
        runtimeCompatibility = "not established by offline tests",
        processingPolicy = processing.snapshot(), processingGeneration = processing.generation(),
        processingMetrics = processing.metrics(),
        data = data,
    }
    local filename = string.format("PaintManager.%s.%s.%03d.json", kind, session, sequence)
    local path = mod_dir .. "/" .. filename
    return report_writer.begin(path, report, { microsteps_per_unit = config.report_steps_per_unit or 32,
        write_bytes = config.report_chunk_bytes or 8192, retain_on_generation_change = is_action(kind) })
end

local function status(command, state, message, path, reason_key)
    local render_message = type(message) == "function" and message or nil
    if render_message then message = render_message() end
    log(command .. " " .. state .. (message and ": " .. message or ""))
    if command == "storage_sync" then return end
    if window then
        local ok, err = pcall(function()
            window:set_status({command = command, state = state, message = message, path = path, reasonKey = reason_key,
                renderMessage = render_message})
            if command ~= "preferences" and (state == "error" or state == "cancelled")
                and type(window.set_progress) == "function" then
                progress_view({ state = state, kind = "stock", phase = state,
                    mode = "indeterminate", coverage = "unknown" })
            end
        end)
        if not ok then
            pcall(function() window:close("status error") end)
            log("UI status failed: " .. tostring(err))
        end
    end
end

-- Keep only the resolved reason and plain counters. Receipts can include long
-- slot lists and native error text; neither belongs to the window's text state.
local function transfer_result_message(progress, cancellation)
    local detail = progress.error or progress.reason
    local display = { moved = progress.moved, skipped = progress.skipped, incomplete = progress.incomplete,
        outcome = progress.outcome, reasonKey = progress.reasonKey or detail and detail ~= "" and player_messages.reason_key(detail) }
    local cancel_key = cancellation and player_messages.reason_key(cancellation, "main.cancel.default")
    return function()
        local line = transfers.result(display)
        return cancel_key and line .. " " .. t(cancel_key) or line
    end
end

progress_view = function(value)
    if not window or not window:is_open() or type(window.set_progress) ~= "function" then return end
    local copy = {}
    for key, item in pairs(value or {}) do copy[key] = item end
    copy.generation = copy.generation or progress_generation
    local emitted = progress_tracker:offer(copy, developer.scheduler_ms)
    if emitted then window:set_progress(emitted) end
end

local function set_learning(active)
    if window and type(window.set_learning) == "function" then window:set_learning(active) end
end

local function persistence(message)
    local render = type(message) == "function" and message or nil
    if render then message = render() end
    if window and type(window.set_persistence) == "function" then window:set_persistence(message, render) end
    log(message)
end

local function storage_message(key, reason_key, keep)
    local function render() return t(key, { error = reason_key and t(reason_key) or "" }) end
    if keep then return render end
    return render()
end

local function publication(progress, keep)
    if progress.publicationPending and not progress.localCommitted then
        return storage_message("main.storage.publication_pending", nil, keep)
    end
    if progress.mirrorPending then
        local key = progress.outcome == "local_uncertain" and "main.storage.local_verification_uncertain"
            or progress.outcome == "uncertain" and "main.storage.local_uncertain" or "main.storage.local_pending"
        if progress.error then log("Storage publication: " .. tostring(progress.error)) end
        return storage_message(key, progress.error and player_messages.reason_key(progress.error, "main.reason.storage"), keep)
    end
    return storage_message("main.storage.local_staged", nil, keep)
end

local function storage_failure(detail, keep)
    if detail then log("Storage error: " .. tostring(detail)) end
    return storage_message("main.storage.unavailable", player_messages.reason_key(detail, "main.reason.storage"), keep)
end

local function current_store(token, expected_snapshot)
    return save_store.new(config, token, build, expected_snapshot)
end

local function cache_presets(token, progress, library, preferences_base)
    local reason = progress.mirrorPending and publication(progress) or t("main.preset.refresh_required")
    preset_cache:publish(token, progress, library, reason, preferences_base)
end

local function editor_library(token, retry)
    if not token then return nil, nil, t("main.error.no_character") end
    if retry and preset_cache.error then preset_cache:clear() end
    if preset_cache.error then return nil, nil, preset_cache.error end
    local syncing = sync_event and sync_event.contextToken == token or running_job and running_job.contextToken == token
        and (running_job.phase == "state_sync" or running_job.phase == "state_load")
    local changing = running_job and (running_job.command == "preset_import" or running_job.command == "preset_delete"
        or running_job.command == "preset_save" or running_job.command == "preset_refresh")
    if syncing or changing then return end
    if retry or preset_cache.token ~= token then
        local current = save_bridge.storage_context(token)
        if current.status ~= "ready" then
            preset_cache:clear(current.error or t("main.preset.refresh_required"))
            return nil, nil, preset_cache.error
        end
        if not preset_cache:same_scope(token, current) then preset_cache:clear() end
    end
    if preset_cache.token == token then
        return preset_cache:preview()
    else
        background.yield_paint_slots()
        sync_event = { contextToken = token }
    end
end

local developer_commands = { snapshot = true, discovery = true, jmap = true }
-- Called by the game-thread pump and its preference publications. Local samples
-- never write the game's culture. Native paint names still follow the game with an override.
function developer.sync_language(read_game)
    local preference = ui_settings.uiLanguage or "auto"
    if read_game then
        if languages.observe_game(languages.game_language()) and recent_snapshot then
            recent_snapshot.dirty = true
        end
        developer.next_language_check = developer.scheduler_ms + 1000
    end
    return i18n.select(preference, languages.game_tag)
end

preferences = preferences_module.new({
    ready = function()
        if build ~= config.reference_build or not storage_snapshot then return nil end
        if not character_possessed() then return nil end
        local token = collection.context()
        if token ~= context_token then return nil end
        return token, storage_snapshot
    end,
    store = current_store,
    publish = function(settings, favorites)
        local previously_enabled, previous_language = ui_settings.showDeveloperTools == true, ui_settings.uiLanguage
        ui_settings = settings or { showDeveloperTools = false }
        developer.sync_language(previous_language ~= ui_settings.uiLanguage)
        ui_favorites = favorites or {}
        if deposit_similar then
            deposit_similar:set_settings(preferences.loaded and ui_settings or nil)
        end
        local ship_radius = ui_settings.shipRadiusCm
        local previous_ship_radius = config.ship_radius_cm
        config.ship_radius_cm = type(ship_radius) == "number" and ship_radius % 1 == 0
            and ship_radius >= 3500 and ship_radius <= 5000 and ship_radius or 3500
        if previous_ship_radius ~= config.ship_radius_cm then
            if running_job and running_job.openScope and running_job.phase ~= "state_load" then cancel_job(t("main.cancel.radius_changed"), true) end
            clear_paints()
        end
        if window and type(window.set_preferences) == "function" then window:set_preferences(ui_settings, ui_favorites) end
        if previously_enabled and not ui_settings.showDeveloperTools then
            developer.queued_action = nil
            processing.request(processing.defaults(), "processing_restoring_defaults")
            local retained = {}
            for _, command in ipairs(pending) do if not developer_commands[command] then retained[#retained + 1] = command end end
            pending = retained
            if developer_commands[active_command] then active_command = nil end
            if running_job and developer_commands[running_job.command] and cancel_job then
                cancel_job(t("main.settings.developer_hidden"), true)
            end
        end
    end,
    settled = function(progress, data, previous)
        -- Tab, favorite and display edits share the document with presets.
        -- Follow only a verified preferences-only write from this exact preview.
        if preset_view and previous and preset_view.storageSnapshot == previous and previous.localSnapshot
            and type(previous.raw) == "string" and previous.raw == previous.localSnapshot.raw
            and progress.snapshot and progress.staged and not progress.error then
            preset_view.storageSnapshot = progress.snapshot
            if preset_request and preset_request.storageSnapshot == previous then
                preset_request.storageSnapshot = progress.snapshot
            end
        end
        if progress.snapshot then storage_snapshot = progress.snapshot end
        cache_presets(context_token, progress, data and data.sections and data.sections.presets and data.sections.presets.data, previous)
        if progress.state == "done" then
            persistence(publication(progress, true))
        elseif progress.localCommitted or progress.localUncertain then
            local pending_publication = { mirrorPending = true, error = progress.error,
                outcome = progress.localUncertain and "local_uncertain" or progress.outcome }
            persistence(publication(pending_publication, true))
        else
            persistence(storage_failure(progress.error, true))
        end
    end,
    error = function(code, detail)
        log("Preference change failed: " .. tostring(detail or code))
        local key = player_messages.reason_key(detail or code, "main.reason.storage")
        status("preferences", "error", function() return t("main.settings.failed", { error = t(key) }) end)
    end,
})

local function write_learning_receipt(job)
    if not is_action(job.command) or job.receiptWritten or job.receiptQueued then return end
    local receipt = job.collector:get_receipt()
    if not receipt then return end
    if job.transferTiming then receipt.timing = job.transferTiming end
    local ok, result = pcall(function() return job.writer or begin_report(job.command, receipt) end)
    if ok then
        job.receiptQueued = true
        receipt_jobs[#receipt_jobs + 1] = result
    else log("Learning receipt could not be queued: " .. tostring(result)) end
end

local function pump_receipts(budget)
    local writer = receipt_jobs[1]
    if not writer or budget:expired() then return end
    local ok, progress = pcall(function() return writer:step(budget) end)
    if ok and progress.state == "running" then return end
    table.remove(receipt_jobs, 1)
    if ok and progress.state == "done" then log("Report: " .. progress.path)
    else
        pcall(function() writer:cancel("Receipt output failed") end)
        log("Learning receipt write failed: " .. tostring(ok and output_error(progress) or progress))
    end
end

local commands = { toggle = true, snapshot = true,
    discovery = true, jmap = true, learn_all = true,
    preset_refresh = true, preset_save = true, preset_apply = true, preset_import = true,
    preset_delete = true }
local function refresh_after_preset(receipt)
    transfers:invalidate()
    if receipt and receipt.attempted then
        transfers.needs_capture = true
        clear_paints()
    end
end
cancel_job = function(reason, announce)
    local previous = running_job
    running_job = nil
    if previous then
        if previous.phase == "stock_wait" then stock_cache:cancel_refresh(reason)
        elseif previous.boundCatalog and (previous.phase == "cache_wait" or previous.phase == "state_load") then
            paint_cache:quiesce()
        end
        if previous.openState then
            local ok, saved = pcall(previous.openState.cancel, previous.openState, reason)
            if ok and saved and (saved.localCommitted or saved.publicationPending) then
                preset_cache:clear()
                persistence(publication(saved, true))
            end
        end
        if previous.collector then pcall(function() previous.collector:cancel(reason) end) end
        if previous.command == "preset_apply" then refresh_after_preset(previous.collector:get_receipt()) end
        if transfers.is_command(previous.command) then
            transfers:invalidate()
            local receipt = previous.collector:get_receipt()
            if receipt and (receipt.attempted or (receipt.moved or 0) > 0) then
                transfers.needs_capture = true
                model = nil
                if recent_snapshot then recent_snapshot.dirty = true end
            end
        end
        if previous.collector and previous.collector.get_publication then
            local saved = previous.collector:get_publication()
            if saved and (saved.localCommitted or previous.command == "preset_save" and saved.publicationPending) then
                preset_cache:clear()
                persistence(publication(saved, true))
            end
        elseif previous.collector and previous.collector.get_progress then
            local saved = previous.collector:get_progress()
            if saved.localCommitted or saved.publicationPending then
                preset_cache:clear()
                persistence(publication(saved, true))
            end
        end
        if previous.diagnostics then pcall(function() previous.diagnostics:cancel(reason) end) end
        if is_action(previous.command) then
            write_learning_receipt(previous)
            set_learning(false)
        elseif previous.writer then
            pcall(function() previous.writer:cancel(reason) end)
        end
        if announce then
            local receipt = is_action(previous.command) and previous.collector:get_receipt() or nil
            if receipt and transfers.is_command(previous.command) then
                status(previous.command, "cancelled", transfer_result_message(receipt, reason))
            elseif receipt and receipt.attempted then
                status(previous.command, successful_outcome(receipt.outcome) and "done" or "error",
                    t(previous.command == "preset_apply" and "main.preset.result" or previous.command == "learn_all" and "main.bulk.after_attempt" or "main.learn.after_attempt",
                        { outcome = player_messages.outcome(receipt.outcome) }), previous.path)
            else status(previous.command, "cancelled", reason) end
        end
        if is_preset(previous.command) then set_learning(false) end
        progress_view({ state = "cancelled", kind = "stock", phase = "cancelled", mode = "indeterminate" })
    end
end
-- Key callbacks only queue text; all UObject and UI work stays in the pump.
local function enqueue(command)
    if not commands[command] then return false, t("main.queue.unknown") end
    if window_closing() then return false, t("main.cancel.window_closed") end
    if developer_commands[command] and ui_settings.showDeveloperTools ~= true then
        return false, t("main.settings.developer_disabled")
    end
    if busy and not (command == "toggle" and running_job and running_job.phase == "transfer") then
        return false, t("main.queue.atomic_busy")
    end
    if command ~= "toggle" and window and window:is_open() and not connection.connected then
        return false, t("main.connection.lost")
    end
    if active_command == command then return true, t("main.queue.already_queued") end
    for _, existing in ipairs(pending) do
        if existing == command then return true, t("main.queue.already_queued") end
    end
    if #pending >= config.max_pending and command ~= "toggle" then return false, t("main.queue.full") end
    if command == "toggle" then table.insert(pending, 1, command)
    else pending[#pending + 1] = command end
    if #pending > config.max_pending then table.remove(pending) end
    -- Do not call native UI from a key callback, even to change a label.
    return true, t("main.queue.queued")
end

local native_editor = build == config.reference_build and require("paintmanager.native_editor").new(config, {
    log = log, context = collection.context, make_button = ui_module.make_editor_button,
    observe_container = function(pc) if deposit_similar then deposit_similar:probe(pc) end end,
    make_popup = ui_module.make_editor_popup, target = require("paintmanager.presets").editor_target,
    library = editor_library,
    capture = function(target, token)
        return require("paintmanager.presets").begin_capture(config, { targetId = target.id, target = target,
            contextToken = token })
    end,
    editor_ship = function(expected)
        return ship_scope.editor_ship(config, expected)
    end,
    save = function(target, name, token, snapshot, guard)
        background.yield_paint_slots()
        return preset_workflow.begin(config, preset_path, "preset_save", { targetId = target.id, target = target,
            name = name, contextToken = token, gameBuild = build,
            connection_guard = guard }, current_store(token, preset_cache:save_base(token, snapshot)))
    end,
    published = function(progress, library)
        if not library then preset_cache:clear() end
        if progress.localUncertain or progress.outcome == "local_uncertain" then storage_snapshot = nil
        elseif progress.snapshot then storage_snapshot = progress.snapshot end
        if library then cache_presets(context_token, progress, library) end
        preset_view = nil
        if window and window.set_presets then window:set_presets(nil) end
        if progress.snapshot or progress.localCommitted or progress.publicationPending then persistence(publication(progress, true)) end
    end,
    publication = publication,
    open = function()
        if window and window:is_open() then return true end
        return enqueue("toggle")
    end,
}) or nil

local function discard_paused_open(reason)
    local job = paused_open
    paused_open = nil
    if not job then return end
    if job.phase == "stock_wait" then stock_cache:cancel_refresh(reason)
    elseif job.phase == "cache_wait" and job.boundCatalog then paint_cache:quiesce() end
    if job.collector then job.collector:cancel(reason) end
end

local function reset_context(reason, token)
    discard_paused_open(reason)
    if deposit_similar then deposit_similar:reset() end
    if native_editor then native_editor:reset() end
    background.invalidate_scope()
    paint_cache:invalidate(reason)
    recent_snapshot, learned_scope = nil, nil
    cancel_job(reason, false)
    transfers:reset()
    transfers.needs_capture, transfers.last_result = nil, nil
    preferences:reset()
    progress_tracker:reset()
    if window then
        pcall(function() window:close(reason) end)
        pcall(function() window:set_model(nil) end)
    end
    model, pending, active_command, bulk_request = nil, {}, nil, nil
    preset_request, preset_view, storage_snapshot = nil, nil, nil
    preset_cache:clear()
    sync_event = nil
    if window and type(window.set_presets) == "function" then window:set_presets(nil) end
    if token and learning_scope_token and token ~= learning_scope_token then
        learning.reset_context()
    end
    if token then learning_scope_token = token end
    set_learning(false)
    context_token = token
    log("Cached view cleared: " .. reason)
end

local function check_context()
    local token, err = collection.context()
    if context_token and token ~= context_token then
        reset_context(err or "World, player or pawn changed", token)
    elseif context_token == nil and token then
        if learning_scope_token and token ~= learning_scope_token then
            learning.reset_context()
        end
        learning_scope_token = token
        context_token = token
    end
    return token, err
end

local function start_collection(command, initial_state)
    local request = command == "learn_all" and bulk_request or nil
    local token, err = check_context()
    if not token then error(err or t("main.error.no_character")) end
    local collector
    local clock_options
    if is_learning(command) then
        bulk_request = nil
        assert(window and window:is_open(), t("main.learn.window_closed"))
        assert(request and request.contextToken == token, t("main.learn.context_changed"))
        assert(request.radiusCm == config.ship_radius_cm, t("main.learn.refresh_required"))
        local catalog = paint_cache:catalog()
        if catalog and catalog.contextToken ~= token then catalog = nil end
        assert(command == "learn_all", "Single learning must use its synchronous cached-source path")
        local sample, clock_error = native_clock.now_ms()
        clock_options = { clock_source = sample and native_clock.source or "os.clock; accurate native time unavailable",
            clock_validation = native_clock.validation, clock_ms = sample and native_clock.now_ms or nil,
            clock_error = clock_error, catalog = catalog }
        collector = bulk_learning.begin(config, request, function(event, data)
            if event == "recipe_finished" and model and model.contextToken == token
                and (data.outcome == "learned" or data.outcome == "already_known") then
                for _, row in ipairs(model.rows) do
                    if row.id == data.itemPath then
                        row.recipeState = "unlocked"
                        if window and window.update_recipe then window:update_recipe(row.id) end
                        break
                    end
                end
            end
            log("Learn all " .. event .. ": " .. json.encode(data))
        end, clock_options)
        pcall(function() log("Learn all timing: " .. clock_options.clock_source .. (clock_error and "; " .. clock_error or "")) end)
    else
        if command == "toggle" then
            local scope = assert(connection.scope, t("main.connection.lost"))
            running_job = { command = command, contextToken = token, openScope = scope, initialState = initial_state, radius = config.ship_radius_cm,
                progress_ticks = 0, cancel_on_close = true, phase = "cache_wait" }
            if paint_cache:binding() and character_possessed() then
                running_job.boundCatalog = true
                paint_cache:begin_open()
            else
                running_job.collector = collection.begin_catalog(config, {
                    contextToken = token, learnedFirst = true, discover = true })
            end
            return
        end
        -- An explicit diagnostic owns its full capture. It never replaces the
        -- player-facing interval or starts either ordinary cache service.
        collector = collection.begin(config, { catalog = paint_cache:catalog() })
    end
    running_job = { command = command, collector = collector,
        contextToken = token, radius = config.ship_radius_cm, phase = "collect", progress_ticks = 0,
        clock_options = clock_options, clockError = clock_options and clock_options.clock_error,
        cancel_on_close = is_learning(command) or (window and window:is_open() or false) }
end

local function preset_target_in_ship_scope(target, ship_owner_id)
    return not target or target.kind ~= "boat" or
        (ship_owner_id ~= nil and target.ownerRuntimeRef and target.ownerRuntimeRef.id == ship_owner_id)
end

local function start_presets(command)
    local request = preset_request
    preset_request = nil
    local token, err = check_context()
    assert(token, err or t("main.error.no_character"))
    assert(window and window:is_open(), t("main.learn.window_closed"))
    assert(request and request.contextToken == token, t("main.preset.refresh_required"))
    request.shipOwnerId = connection.shipOwnerId
    request.connection_guard = function()
        if not connection.connected then return false, t("main.connection.lost") end
        if request.shipOwnerId ~= connection.shipOwnerId
            or not preset_target_in_ship_scope(request.target, connection.shipOwnerId) then
            return false, t("main.preset.refresh_required")
        end
        return true
    end
    assert(request.connection_guard())
    set_learning(true)
    -- A preference write already holding the writer may have just settled.
    -- The save transaction rereads both sources and checks the exact snapshot.
    if command == "preset_save" then request.storageSnapshot = storage_snapshot or request.storageSnapshot end
    if command == "preset_apply" then
        -- A previous transfer may have retired the displayed quantities.
        -- Its box identities are still candidates for a fresh native read.
        local stock = model or (stock_cache.capture and stock_cache:capture()) or (recent_snapshot and recent_snapshot.model)
        local scope = connection.scope
        if stock and stock.contextToken == token and stock.stockScope and scope and stock.stockScope.token == scope.token
            and stock.stockScope.shipId == scope.shipId then request.stockModel = stock end
    end
    running_job = { command = command, phase = "preset", contextToken = token,
        saveBase = command == "preset_save" and request.storageSnapshot or nil,
        collector = preset_workflow.begin(config, preset_path, command, request, current_store(token, request.storageSnapshot)),
        progress_ticks = 0, cancel_on_close = true }
end

local function publish_capture(captured, token, retained)
    if captured.contextToken ~= token or captured.status == "context_changed" then
        reset_context("Context changed during collection read", nil)
        error(t("main.error.context_changed"))
    end
    -- Ordinary scans replace the interval/location hints. A single Learn only
    -- rereads this interval's candidate boxes and never publishes a new scan.
    local scope = connection.scope
    assert(scope and captured.stockScope and captured.stockScope.token == scope.token
        and captured.stockScope.shipId == scope.shipId, t("main.connection.lost"))
    local reconcile = same_snapshot_scope(recent_snapshot, token, scope)
    model, learned_scope = captured, nil
    transfers.needs_capture = nil
    transfers:invalidate()
    recent_snapshot = { model = captured, contextToken = token, shipToken = scope.token, shipId = scope.shipId,
        shipRadius = config.ship_radius_cm, at = retained and retained.at or os.time() }
    paint_cache:observe(captured)
    model.capturedUtc = retained and retained.model.capturedUtc or os.date("!%Y-%m-%dT%H:%M:%SZ")
    if window then
        if (reconcile or window.model and window.model.learnedOnly) and window.reconcile_capture then
            window:reconcile_capture(model)
        else window:set_model(model) end
    end
end

local function publish_transfer_stock(current)
    if not model or model ~= current.stockModel or not recent_snapshot or recent_snapshot.dirty
        or recent_snapshot.model ~= model or not current.collector.get_stock_changes then return false end
    local scope = ship_scope.player_access(config, model.stockScope)
    if not same_snapshot_scope(recent_snapshot, current.contextToken, scope) then return false end
    local updated = transfer_stock.reconcile(model, current.collector:get_stock_changes(), scope, current.contextToken, current.collector.backpackCounts)
    if not updated then return false end
    publish_capture(updated, current.contextToken, recent_snapshot)
    if stock_cache:binding() then stock_cache:observe(updated) end
    return true
end

local phase_keys = {
    cache_wait = "main.phase.paint_cache",
    native_decode = "main.phase.storage_check", local_decode = "main.phase.storage_read",
    local_document = "main.phase.storage_check", local_read = "main.phase.storage_read",
    state_merge = "main.phase.storage_merge", import_merge = "main.phase.storage_merge",
    state_preflight = "main.phase.storage_check", local_publish = "main.phase.storage_write",
    state_read = "main.phase.state_read", state_change = "main.phase.state_change",
    state_encode = "main.phase.state_encode", state_stage = "main.phase.state_stage",
    bulk_prepare = "main.phase.bulk_prepare", bulk_start = "main.phase.bulk_learn",
    bulk_copy = "main.phase.bulk_prepare", bulk_worker = "main.phase.bulk_prepare",
    bulk_learn = "main.phase.bulk_learn", bulk_next = "main.phase.bulk_learn",
    bulk_start_recipe = "main.phase.bulk_learn", bulk_recipe = "main.phase.bulk_learn",
    learn_prepare = "main.phase.learn_prepare", learn_commit = "main.phase.learn_commit",
    recipe_library_read = "main.phase.recipe_library", recipe_library_resolve = "main.phase.recipe_resolve",
    known_paint_verify = "main.phase.recipe_resolve",
    context = "main.phase.context", catalog_discovery = "main.phase.catalog_discovery",
    catalog_metadata = "main.phase.paint_metadata", paint_metadata = "main.phase.paint_metadata",
    backpack_metadata = "main.phase.paint_metadata",
    source_discovery = "main.phase.source_discovery", source_filter = "main.phase.source_filter",
    source_filter_worker = "main.phase.source_filter", collection_worker = "main.phase.stock_processing",
    inventory_capture = "main.phase.container_read", inventory = "main.phase.container_read",
    container_read = "main.phase.container_read", container_paint_metadata = "main.phase.paint_metadata",
    aggregate = "main.phase.aggregate", aggregation = "main.phase.aggregate", render = "main.phase.render",
    distribution_finalize = "main.phase.aggregate", open = "main.phase.report_write", encode = "main.phase.report_encode",
    check = "main.phase.report_write", flush = "main.phase.report_write",
    write = "main.phase.report_write", close = "main.phase.report_write", publish = "main.phase.report_write",
    preset_load = "main.phase.preset_load", preset_verify = "main.phase.preset_verify", preset_store = "main.phase.preset_store",
    preset_context = "main.phase.preset_capture", preset_capture = "main.phase.preset_capture",
    preset_targets = "main.phase.preset_capture", preset_recapture = "main.phase.preset_capture",
    preset_preflight = "main.phase.preset_apply", preset_commit = "main.phase.preset_apply", preset_apply = "main.phase.preset_apply",
    preset_plan = "main.phase.preset_apply", preset_supply = "main.phase.preset_apply", preset_ready = "main.phase.preset_apply",
    preset_boxes = "main.phase.source_discovery",
    preset_zone = "main.phase.preset_capture", preset_compare = "main.phase.preset_capture",
}

local function job_progress(current, progress)
    developer.deferred = developer.deferred or progress.defer == true
    current.progress_ticks = current.progress_ticks + 1
    local phase = progress.phase or current.phase
    local presentation = progress.presentation or { kind = is_learning(current.command) and "learning" or "stock",
        phase = phase, mode = "indeterminate", coverage = "unknown" }
    presentation.state = "running"
    if current.command == "learn_all" and (progress.totalRecipes or 0) > 0 then
        presentation = { state = "running", kind = "learning", phase = "learning", mode = "determinate",
            completed = progress.completedRecipes or 0, total = progress.totalRecipes, unit = "recipes" }
    end
    if current.command ~= "storage_sync" then progress_view(presentation) end
    if phase ~= current.last_phase or current.progress_ticks % 16 == 0 then
        current.last_phase = phase
        local label = t(phase_keys[phase] or (current.phase == "legacy_snapshot" and "main.phase.snapshot"
            or current.phase == "write" and "main.phase.report_write" or "main.phase.refresh"))
        local storage_work = current.phase == "state_load" or current.phase == "state_sync"
            or is_preset(current.command)
        local message = current.command == "learn_all" and (progress.totalRecipes or 0) > 0 and
            t("main.bulk.progress", { phase = label, completed = progress.completedRecipes or 0, total = progress.totalRecipes }) or
            label
        if not storage_work or message ~= current.last_storage_message then
            status(current.command, "running", message)
            if storage_work then current.last_storage_message = message end
        end
    end
end

-- Native global discovery, JMAP and fixed UI work retain explicit indivisible
-- stages. Snapshot processing and report encoding/writes use resumable jobs.
local function atomic_operation(label, budget, callback)
    local before = budget:elapsed_ms()
    local result = table.pack(pcall(callback))
    budget:consume()
    local metrics = budget:metrics()
    local elapsed = math.max(0, metrics.elapsedMs - before)
    processing.record(label, elapsed, 1, { clock = metrics.clockSource, indivisible = true })
    log("Atomic " .. label .. " returned; " .. (metrics.clockValid and string.format("%.3f ms", elapsed)
        or "timing clock unavailable") .. "; not preemptible by Lua")
    if not result[1] then error(result[2]) end
    return table.unpack(result, 2, result.n)
end

local function show_learned_prefix(current)
    if current.learnedShown or current.captured or not window or window.active_tab ~= "learned"
        or not current.learned or not window.show_learned then return false end
    current.learnedShown = window:show_learned(current.learned, recent_snapshot and recent_snapshot.model) == true
    if current.learnedShown then
        learned_scope = current.openScope
        background.yield_paint_slots()
    end
    return current.learnedShown
end

-- Called directly by the button and then by the open-window loop if work
-- remains. Spend the slice doing successive steps, including native moves.
local function advance_transfer(current)
    if current.lastCallback == pump_sequence then return end
    current.lastCallback = pump_sequence
    local timing = current.transferTiming
    timing.slices = timing.slices + 1
    busy = true
    local ok, err = pcall(function()
        local allowed, reason = current.guard(true)
        if not allowed then cancel_job(reason, true); return end
        local budget = work_budget.new(config.transfer_slice_ms, config.transfer_slice_units, nil,
            { batching_enabled = true, time_limit_enabled = true, unit_limit_enabled = true })
        local p
        repeat
            if p then
                local active, why = current.guard()
                if not active then cancel_job(why, true); return end
            end
            local units = budget.units
            p = current.collector:step(budget)
            if running_job ~= current then return end
            timing.steps = timing.steps + 1
            if not timing.firstMoveMs and (p.moved or 0) > 0 and current.startedMs then
                local now = work_budget.clock_ms()
                if now and now >= current.startedMs then timing.firstMoveMs = now - current.startedMs end
            end
            -- Every production step consumes work. A stalled or reentrant
            -- provider must also return control to the input loop.
            if units == budget.units then break end
        until p.state ~= "running" or budget:expired()
        if p.state == "running" then
            if current.lastMoved ~= p.moved then
                current.lastMoved = p.moved
                local moved = p.moved or 0
                status(current.command, "running", function() return t("main.transfer.running", { moved = moved }) end)
            end
            return
        end
        local now = work_budget.clock_ms()
        if now and current.startedMs and now >= current.startedMs then timing.elapsedMs = now - current.startedMs end
        transfers:invalidate()
        local message = transfer_result_message(p)
        local state = p.state == "cancelled" and "cancelled" or p.state == "done" and "done" or "error"
        local receipt = current.collector:get_receipt()
        if receipt and (receipt.attempted or (p.moved or 0) > 0) then
            local patched = false
            if p.state == "done" and not p.incomplete and p.outcome ~= "uncertain" then
                local success, value = pcall(publish_transfer_stock, current)
                patched = success and value == true
                if not success then log("Transfer stock refresh: " .. tostring(value)) end
            end
            timing.stockRefresh = patched and "changed_boxes" or "full_capture"
            if not patched then
                model, transfers.needs_capture = nil, true
                if recent_snapshot then recent_snapshot.dirty = true end
                transfers.last_result = { command = current.command, state = state, message = message }
            end
        end
        write_learning_receipt(current)
        set_learning(false)
        status(current.command, state, message)
        running_job = nil
    end)
    if not ok and running_job == current then
        cancel_job("Transfer interrupted", false)
        status(current.command, "error", tostring(err), nil, player_messages.reason_key(err))
    end
    busy = false
end

local function step_collection(budget)
    local current = running_job
    if not current then return end
    -- Also guard post-capture diagnostic/write/render phases. A multi-pump read
    -- must never combine an old-world collection with a new-world debug capture.
    if not current.context_independent then
        local token = collection.context()
        if token ~= current.contextToken then
            reset_context("Context changed during queued work", token)
            return
        end
    end
    if budget:expired() then return end
    if current.radius and current.radius ~= config.ship_radius_cm and not transfers.is_return(current.command) then
        cancel_job(t("main.cancel.radius_changed"), true)
        return
    end
    if (is_learning(current.command) or is_preset(current.command)) and (not window or not window:is_open()) then
        cancel_job(t("main.cancel.window_closed"), true)
        return
    end
    if current.openScope and (not connection.scope or connection.scope.token ~= current.openScope.token
        or connection.scope.shipId ~= current.openScope.shipId) then
        cancel_job(t("main.connection.lost"), true)
        return
    end
    if current.openScope and (show_learned_prefix(current) or window.active_tab == "learned"
        and current.learnedShown and not current.captured and window:is_rendering()) then
        developer.deferred = true
        return
    end
    if current.phase == "stock_wait" then
        stock_cache:step(budget, true, current.catalog, developer.scheduler_ms)
        local progress = stock_cache:get_progress()
        job_progress(current, progress)
        if not stock_cache:binding() or progress.state == "error" then
            error(progress.error or t("main.error.job_failed"))
        end
        local seeds = stock_cache:take_paint_seeds()
        if seeds then paint_cache:seed_advisory(seeds) end
        local captured, revision = stock_cache:capture()
        if not captured or revision < current.stockRevision then return end
        current.captured, current.phase = captured, "finish"
        publish_capture(captured, current.contextToken)
    elseif current.phase == "cache_wait" then
        if current.boundCatalog then
            if not paint_cache:binding() or not character_possessed() then
                paint_cache:quiesce()
                current.boundCatalog = false
                current.collector = collection.begin_catalog(config, {
                    contextToken = current.contextToken, learnedFirst = true, discover = true })
            else
                local p = paint_cache:step(budget)
                current.learned = paint_cache:learned_capture()
                current.catalog = paint_cache:catalog()
                if p and p.state == "error" then error(p.error or t("main.error.job_failed")) end
                if not current.catalog then
                    job_progress(current, { phase = "cache_wait", defer = p and p.defer })
                    if show_learned_prefix(current) then developer.deferred = true end
                    return
                end
            end
        end
        if current.collector then
            local p = current.collector:step(budget)
            current.learned = current.collector:get_learned_capture()
            if p.state == "running" then
                job_progress(current, p)
                if show_learned_prefix(current) then developer.deferred = true end
                return
            end
            assert(p.state == "done", p.error or t("main.error.job_failed"))
            current.catalog = assert(current.collector:get_catalog(), t("main.error.missing_model"))
            current.collector = nil
        end
        show_learned_prefix(current)
        if current.initialState then return end
        if stock_cache:binding() then
            current.phase, current.stockRevision = "stock_wait", stock_cache:refresh()
        else
            current.collector = collection.begin(config, { catalog = current.catalog,
                openCatalog = true, shipScope = current.openScope, presentation = "paints" })
            current.phase = "collect"
        end
    elseif current.phase == "state_load" or current.phase == "state_sync" or current.phase == "preset_import" then
        local phase = current.phase
        local initial_presets
        local storage = current.collector
        if phase == "state_load" then
            current.openState = current.openState or current_store(current.contextToken).begin_state_load()
            storage = current.openState
        end
        local progress = storage:step(budget)
        job_progress(current, progress)
        if progress.state == "running" then return end
        if progress.state ~= "done" then
            preset_cache:clear(progress.error or t("main.error.job_failed"))
            persistence(progress.localCommitted and publication(progress, true)
                or storage_failure(progress.error, true))
            if phase == "state_sync" then running_job = nil; return end
            if phase ~= "state_load" then error(progress.error or t("main.error.job_failed")) end
            -- Broken/future optional data must not disable read-only collection.
            -- Keep it untouched; every write will independently validate again.
        else
            local data = assert(storage:get_result(), "Save document missing")
            storage_snapshot = progress.snapshot
            cache_presets(current.contextToken, progress, data.sections.presets and data.sections.presets.data)
            if preferences:load(data) and window and window.flush_page_choice then window:flush_page_choice() end
            persistence(publication(progress, true))
            -- A verified unchanged primary does not create a local commit.
            -- Matching post-stage bytes identify the same validated document.
            if (phase == "state_load" or phase == "state_sync") and window and window:is_open()
                and window.active_tab == "presets" and progress.staged and not progress.error and storage_snapshot
                and storage_snapshot.localSnapshot and type(storage_snapshot.raw) == "string"
                and storage_snapshot.raw == storage_snapshot.localSnapshot.raw and data.sections.presets then
                initial_presets = { library = data.sections.presets.data, snapshot = storage_snapshot, contextToken = current.contextToken }
            end
            log("Storage source/result: " .. json.encode({
                phase = phase, nativeMissing = progress.nativeSourceMissing,
                nativeBytes = progress.nativeSourceBytes, nativeLive = progress.nativeSourceLive,
                nativeDeleted = progress.nativeSourceDeleted, nativeMatchesResult = progress.nativeSourceMatchesResult,
                localCommitted = progress.localCommitted, staged = progress.staged,
                mirrorPending = progress.mirrorPending, outcome = progress.outcome }))
        end
        if phase == "state_load" and progress.state == "done" and sync_event
            and sync_event.contextToken == current.contextToken then sync_event = nil end
        if phase == "preset_import" then
            preset_view = nil
            if window and type(window.set_presets) == "function" then window:set_presets(nil) end
        end
        set_learning(false)
        if recent_snapshot and not same_snapshot_scope(recent_snapshot, current.contextToken, connection.scope) then
            clear_paints()
        end
        if phase == "state_sync" then running_job = nil; return initial_presets end
        if phase == "state_load" then
            local previous_ship = current.openScope
            if connection_check() and previous_ship.shipId == connection.scope.shipId
                and previous_ship.worldId == connection.scope.worldId
                and (not previous_ship.runtimeRef or connection.shipOwnerId == previous_ship.runtimeRef.id) then
                current.phase, current.openScope, current.radius = "cache_wait", connection.scope, config.ship_radius_cm
                current.initialState, current.openState = nil, nil
                if not window.model then current.learnedShown = false end
            else cancel_job(t("main.connection.lost"), true); return end
            return initial_presets
        end
        running_job = nil
        status(current.command, "done", publication(progress))
    elseif current.phase == "preset" then
        local progress = current.collector:step(budget)
        job_progress(current, progress)
        if progress.state == "running" then return end
        if progress.state ~= "done" then
            local receipt = progress.publication
            if current.command ~= "preset_apply" then preset_cache:clear(progress.error or t("main.error.job_failed")) end
            if current.command ~= "preset_save" or receipt then
                persistence(receipt and (receipt.localCommitted or receipt.publicationPending) and publication(receipt, true)
                    or storage_failure(progress.error, true))
            end
            error(progress.error or t("main.error.job_failed"))
        end
        if progress.publication then
            cache_presets(current.contextToken, progress.publication, progress.view and progress.view.library)
            persistence(publication(progress.publication, true))
        end
        if current.command == "preset_save" and progress.publication and
            (progress.publication.localUncertain or progress.publication.outcome == "local_uncertain") then
            -- The store can return a candidate document even when readback of
            -- the primary failed. Require an explicit refresh before editing.
            local render_failure = publication(progress.publication, true)
            local failure = render_failure()
            if preset_view then preset_view.error = failure end
            if window and window.set_preset_save_error then window:set_preset_save_error(failure, render_failure) end
            error(failure)
        end
        preset_view = progress.view
        storage_snapshot = preset_view.storageSnapshot or storage_snapshot
        if window and type(window.set_presets) == "function" then window:set_presets(preset_view, progress.savedId, current.saveBase) end
        if current.command == "preset_apply" then
            current.data = progress.receipt or current.collector:get_receipt()
            refresh_after_preset(current.data)
            current.phase = "write"
        else
            current.phase, current.report_only = "finish", true
            current.message = t(current.command == "preset_delete" and "main.preset.deleted"
                or progress.saveSkipped and "main.preset.already_saved"
                or current.command == "preset_save" and "main.preset.saved" or "main.preset.refreshed")
        end
    elseif current.phase == "collect" then
        if current.command == "learn_all" then
            local frame, frame_error = native_clock.frame_count()
            if frame and current.lastObservedFrame and frame < current.lastObservedFrame then
                frame, frame_error = nil, "Native frame count moved backwards; using callback admission"
            end
            if frame then current.lastObservedFrame = frame end
            local timing = { clockSource = budget.clock_source, nativeClock = budget.native_clock == true and budget.clock_valid,
                clockError = current.clockError or budget.clock_error, frameSource = native_clock.frame_source,
                frameError = frame_error, admission = frame and "at_most_one_batch_callback_per_engine_frame" or "scheduler_callback" }
            if type(current.collector.note_scheduler_callback) == "function" then
                current.collector:note_scheduler_callback(pump_sequence, frame, timing)
            end
            if frame and current.lastBatchFrame == frame then return end
            current.lastBatchFrame = frame
        end
        local progress
        local preparing = learning_preparation(current) and budget.read_only_preparation == true
        repeat
            local before_units = budget.units
            progress = current.collector:step(budget)
            developer.deferred = progress.defer == true
            if progress.state ~= "running" then break end
            job_progress(current, progress)
            if current.command == "learn_all" and (not window or not window:is_open()) then
                cancel_job(t("main.cancel.window_closed"), true)
                return
            end
            local expired = budget:expired()
            if progress.defer == true then return end
            if preparing then
                -- This allowance is for fresh read-only capture/transport only.
                -- Native commit work starts on a later scheduler callback with
                -- its original timing and frame-admission rules.
                if not learning_preparation(current) or budget.units <= before_units or expired then return end
            elseif current.command ~= "learn_all" or not budget.native_clock or not budget.clock_valid
                or budget.units <= before_units or expired then return end
        until false
        if progress.state == "running" then return end
        if progress.state == "cancelled" then
            local token = collection.context()
            if token ~= current.contextToken then reset_context(progress.error or "Context changed", token)
            else cancel_job(progress.error or t("main.cancel.default"), true) end
            return
        end
        if progress.state ~= "done" then error(progress.error or t("main.error.job_failed")) end
        current.captured = progress.model
        if is_learning(current.command) then
            current.data = progress.receipt or current.collector:get_receipt()
            if current.captured then publish_capture(current.captured, current.contextToken) end
            current.phase = "write"
            return
        end
        current.captured = assert(current.captured, t("main.error.missing_model"))
        if current.command == "toggle" then publish_capture(current.captured, current.contextToken) end
        current.phase = current.command == "snapshot" and "legacy_snapshot"
            or current.command == "sources" and "write" or "finish"
    elseif current.phase == "legacy_snapshot" then
        if not current.diagnostics then current.diagnostics = adapter.begin(config) end
        local progress = current.diagnostics:step(budget)
        job_progress(current, progress)
        if progress.state == "running" then return end
        if progress.state == "cancelled" then cancel_job(progress.error or t("main.cancel.default"), true); return end
        if progress.state ~= "done" then error(progress.error or t("main.error.job_failed")) end
        local after = collection.context()
        if after ~= current.contextToken then
            reset_context("Context changed during diagnostic capture", after)
            return
        end
        current.data = assert(progress.data, "Completed diagnostic job returned no data")
        current.data.uiCollection = current.captured
        current.phase = "write"
    elseif current.phase == "write" then
        if not current.writer then current.writer = begin_report(current.reportKind or current.command, current.data or current.captured) end
        local progress = current.writer:step(budget)
        job_progress(current, progress)
        if progress.state == "running" then return end
        if progress.state ~= "done" then error(output_error(progress)) end
        current.path = progress.path
        log("Report: " .. current.path)
        if is_action(current.command) then current.receiptWritten = true end
        current.phase = "finish"
    else
        if current.command ~= "preset_save" and window and window:is_open() and window:is_rendering() then
            local progress = window:render_progress()
            job_progress(current, { phase = "render", presentation = progress.presentation })
            return
        end
        running_job = nil
        processing.finish_operation(current.command, { complete = current.captured and current.captured.complete,
            scope = current.captured and current.captured.stockScope, callbacks = pump_sequence - (current.started_callback or pump_sequence),
            mutationAdmission = is_action(current.command) and "preserved_native_admission" or nil })
        progress_view({ state = "done", kind = "stock", phase = "done", mode = "indeterminate",
            coverage = current.captured and (current.captured.stockComplete == true and "complete" or "partial") or "complete" })
        if current.command == "preset_apply" then
            set_learning(false)
            local outcome = current.data and current.data.outcome or "uncertain"
            local state = successful_outcome(outcome) and "done" or "error"
            local reason_key = current.data and current.data.error and player_messages.reason_key(current.data.error)
            local function message()
                local line = t("main.preset.result", { outcome = player_messages.outcome(outcome) })
                return reason_key and line .. ". " .. t(reason_key) or line
            end
            if transfers.needs_capture then
                transfers.last_result = { command = current.command, state = state, message = message, path = current.path }
            end
            status(current.command, state, message, current.path)
        elseif is_learning(current.command) then
            set_learning(false)
            local outcome = current.data and current.data.outcome or "unverified"
            local success = successful_outcome(outcome)
            local counts = current.data and current.data.counts or {}
            local key = current.command == "learn_all" and "main.bulk.result" or (success and "main.learn.verified" or "main.learn.failed")
            status(current.command, success and "done" or "error", function()
                return t(key, { outcome = player_messages.outcome(outcome), learned = counts.learned or 0, total = counts.eligible or 0,
                    known = counts.already_known or 0, rejected = counts.rejected or 0, unverified = counts.unverified or 0 })
            end, current.path)
        elseif current.report_only then
            if is_preset(current.command) then set_learning(false) end
            status(current.command, "done", current.message, current.path)
        elseif current.afterTransfer then
            local result = current.afterTransfer
            status(result.command, result.state, result.message, result.path)
        else
            status(current.command, "done", t(current.captured.complete and "main.status.refreshed"
                or "main.status.partial"), current.path)
        end
    end
end

local function request_presets(command, target_id, value)
    if busy or running_job and not (command == "preset_save" and running_job.command == "storage_sync")
        or active_command or #pending > 0 or preset_request or bulk_request
        or command ~= "preset_save" and command ~= "preset_refresh" and #receipt_jobs >= config.max_pending then
        return false, t("main.learn.busy")
    end
    if not window or not window:is_open() or window_closing() then return false, t("main.learn.window_closed") end
    if command == "preset_apply" and build ~= config.reference_build then return false, t("main.learn.build_unverified") end
    local token, err = check_context()
    if not token then
        if err then log("Preset context unavailable: " .. tostring(err)) end
        return false, t("main.error.no_character")
    end
    local target
    if command ~= "preset_refresh" then
        if not preset_view or preset_view.error or preset_view.contextToken ~= token then
            return false, t("main.preset.refresh_required")
        end
        if command ~= "preset_delete" then
            for _, row in ipairs(preset_view.targets or {}) do if row.id == target_id then target = row; break end end
            if not target or not target.complete then return false, t("main.preset.target_incomplete") end
            if not preset_target_in_ship_scope(target, connection.shipOwnerId) then
                return false, t("main.preset.refresh_required")
            end
        end
    end
    if command == "preset_save" then
        if type(value) ~= "string" or value:match("^%s*$") or #value > 128 or value:find("[%z\1-\31\127]") then
            return false, t("main.preset.name_invalid")
        end
    end
    sequence = sequence + 1
    preset_request = { targetId = target_id, target = target, name = command == "preset_save" and value or nil,
        presetId = command == "preset_apply" and value or command == "preset_delete" and target_id or nil,
        view = command ~= "preset_refresh" and preset_view or nil,
        storageSnapshot = command ~= "preset_refresh" and preset_view.storageSnapshot or nil,
        contextToken = token, gameBuild = build }
    local accepted, message = enqueue(command)
    if not accepted then preset_request = nil end
    return accepted, message
end

local function start_initial_preset_view(fresh)
    if not window or not window:is_open() or window_closing() or not connection.connected
        or window.active_tab ~= "presets" or active_command or #pending > 0 or preferences.active
        or not window.needs_preset_refresh or not window:needs_preset_refresh() then return end
    if fresh and running_job and running_job.openScope and running_job.phase ~= "state_load" then
        paused_open, running_job = running_job, nil
    end
    if running_job or not request_presets("preset_refresh") then return end
    if fresh and fresh.snapshot == storage_snapshot and fresh.contextToken == preset_request.contextToken then
        preset_request.initialLibrary, preset_request.storageSnapshot = fresh.library, fresh.snapshot
    end
    table.remove(pending, 1)
    window:mark_preset_refresh_requested()
    progress_generation = progress_generation + 1
    start_presets("preset_refresh")
    running_job.autoPresets = true
end

local function request_learning(item_id)
    if not connection_check() then return false, t("main.connection.lost") end
    if build ~= config.reference_build then return false, t("main.learn.build_unverified") end
    if busy or running_job or active_command or #pending > 0 or bulk_request
        or #receipt_jobs >= config.max_pending then
        return false, t("main.learn.busy")
    end
    if not window or not window:is_open() or window_closing() or not model or model.status ~= "captured"
        or model.stockComplete ~= true or model.radiusCm ~= config.ship_radius_cm then
        return false, t("main.learn.refresh_required")
    end
    local row
    for _, candidate in ipairs(model.rows or {}) do if candidate.id == item_id then row = candidate; break end end
    if not paint_view.learn_eligible(model, row) or not row.runtimeRef then
        return false, t("main.learn.not_eligible")
    end
    local request = { itemPath = row.id, runtimeRef = row.runtimeRef,
        contextToken = model.contextToken, radiusCm = model.radiusCm,
        displayName = row.displayName, displayedCount = paint_view.physical_count(model, row), gameBuild = build }
    -- UI actions are polled by tick() on the game thread. This explicit single
    -- action finishes there: no command queue, global scan, worker or yielding.
    -- Native code still owns the fresh candidate/context/Vault proof and the
    -- once-only attempt guard. Keep reentrant pumps/actions out until it returns.
    busy = true
    local ok, result = pcall(function()
        set_learning(true)
        assert(window and window:is_open(), t("main.learn.window_closed"))
        return learning.run_cached(config, request, model, function(event, data)
            -- The asynchronous receipt retains every proof observation. Keep
            -- only mutation breadcrumbs on this synchronous input path.
            if event == "native_call_begin" or event == "native_call_returned" or event == "finished" then
                pcall(function() log("Learn recipe " .. event .. ": " .. json.encode(data)) end)
            end
        end)
    end)
    busy = false
    pcall(set_learning, false)
    if not ok then
        log("Recipe learning failed: " .. tostring(result))
        local function message() return t("main.learn.failed", { outcome = player_messages.outcome("unverified") }) end
        status("learn_recipe", "error", message)
        return false, message(), true
    end
    local receipt = assert(result.receipt, "Synchronous learning returned no receipt")
    -- Receipts are plain data. Their output continues independently; reporting
    -- cannot hold the successful gameplay action or its UI controls hostage.
    write_learning_receipt({ command = "learn_recipe", collector = {
        get_receipt = function() return receipt end } })
    if window and type(window.update_recipe) == "function" then
        local updated, failure = pcall(function() window:update_recipe(item_id) end)
        if not updated then
            pcall(function() window:close("Learning result UI failed") end)
            pcall(function() log("Learning result UI failed: " .. tostring(failure)) end)
        end
    end
    local success = successful_outcome(receipt.outcome)
    local key = receipt.reasonCode == "refresh_required" and "main.learn.cached_stock_changed"
        or success and "main.learn.verified" or "main.learn.failed"
    local outcome = receipt.outcome
    local function message() return t(key, { outcome = player_messages.outcome(outcome) }) end
    status("learn_recipe", success and "done" or "error", message)
    return success, message(), true
end

local function request_bulk_learning()
    if not connection_check() then return false, t("main.connection.lost") end
    if build ~= config.reference_build then return false, t("main.learn.build_unverified") end
    if busy or running_job or active_command or #pending > 0 or bulk_request
        or #receipt_jobs >= config.max_pending then
        return false, t("main.learn.busy")
    end
    if not window or not window:is_open() or window_closing() or not model or model.status ~= "captured"
        or model.stockComplete ~= true or model.radiusCm ~= config.ship_radius_cm then
        return false, t("main.learn.refresh_required")
    end
    local available = 0
    for _, row in ipairs(model.rows or {}) do
        if paint_view.learn_eligible(model, row) and row.runtimeRef then
            available = available + 1
        end
    end
    if available == 0 then return false, t("main.bulk.none") end
    bulk_request = { contextToken = model.contextToken, radiusCm = model.radiusCm, gameBuild = build,
        displayedEligibleCount = available }
    local accepted, message = enqueue("learn_all")
    if not accepted then bulk_request = nil; return false, message end
    pcall(function() log("Learn all queued: " .. json.encode(bulk_request)) end)
    return true, t("main.bulk.queued")
end

local function request_transfer(command, item_id)
    local started_ms = work_budget.clock_ms()
    if not transfers.is_command(command) then return false, t("main.queue.unknown") end
    if build ~= config.reference_build then return false, t("main.transfer.build_unverified") end
    if not window or not window:is_open() or window_closing() then return false, t("main.learn.window_closed") end
    if busy or running_job or paused_open or active_command or #pending > 0 or #receipt_jobs >= config.max_pending then
        return false, t("main.learn.busy")
    end
    local take = not transfers.is_return(command)
    local scope = take and connection.scope
    local token = collection.context()
    if not token or token ~= context_token then return false, t("main.error.context_changed") end
    local function guard(fresh)
        if not window:is_open() or window_closing() or pending[1] == "toggle" then return false, t("main.cancel.window_closed") end
        if context_token ~= token or fresh and collection.context() ~= token then return false, t("main.error.context_changed") end
        if take then
            local live = connection.scope
            if live and fresh then live = ship_scope.player_access(config, scope) end
            if not live or not scope or live.token ~= scope.token or live.shipId ~= scope.shipId then return false, t("main.connection.lost") end
        end
        return true
    end
    local allowed, reason = guard()
    if not allowed then return false, reason end
    local job, err = transfers:begin(command, item_id, token, model, build, guard, ui_settings.paintTransferQuantity)
    if not job then return false, err end
    running_job = { command = command, collector = job, phase = "transfer", contextToken = token,
        cancel_on_close = true, guard = guard,
        stockModel = model, startedMs = started_ms, transferTiming = { slices = 0, steps = 0 } }
    progress_generation = progress_generation + 1
    if not window.learning then set_learning(true) end
    advance_transfer(running_job)
    -- The click may already have completed; the controller owns its status.
    return true, nil, true
end

local function request_import()
    if busy or running_job or active_command or #pending > 0 or preferences:is_busy() then return false, t("main.storage.busy") end
    if not window or not window:is_open() then return false, t("main.learn.window_closed") end
    if build ~= config.reference_build then return false, t("main.storage.build") end
    if not storage_snapshot then return false, t("main.storage.not_ready") end
    return enqueue("preset_import")
end

function developer.guard()
    return ui_settings.showDeveloperTools == true and window and window:is_open() and not window_closing()
        and (window.active_tab == nil or window.active_tab == "debug") and connection_check()
end
function developer.snapshot(include_metrics)
    local value = processing.describe(include_metrics)
    value.backend = background.describe()
    value.profilePath, value.startupPath = developer.profile_path, developer.startup_path
    value.cadence = "nominal_callback_opportunities"
    value.capabilities = { jobs = processing.get("job_admission_target", 4),
        memoryAccounting = "logical payload limits; shared Lua heap", nativeParameterBytes = 512,
        shipRadiusCm = config.ship_radius_cm }
    value.profileMigration = developer.profile_migration or developer.startup_migration
    value.status = t(processing.pending() and "main.processing.pending" or "main.processing.applied")
    return value
end
function developer.apply(changes)
    if not developer.guard() then return false, t("main.settings.developer_disabled") end
    local accepted, err = processing.request(changes)
    if window and window.processing_changed then window:processing_changed() end
    return accepted, t(accepted and "main.processing.pending" or "main.processing.invalid", { error = err or "" })
end
function developer.action(action, arguments)
    if not developer.guard() then return false, t("main.settings.developer_disabled") end
    arguments = arguments or {}
    if action == "profile_import" then
        local values, detail = developer_profiles.read(developer.profile_path)
        if values then
            developer.profile_migration = detail
            if detail and detail.migrated then
                log("Processing profile migrated; retired fields: " .. table.concat(detail.retiredKeys, ", "))
            end
        end
        return values ~= nil, t(values and (detail and detail.migrated and "main.processing.imported_legacy"
            or "main.processing.imported") or "main.processing.invalid", { error = not values and tostring(detail or "") or "" }), values
    elseif action == "profile_export" or action == "startup_profile" then
        local path = action == "startup_profile" and developer.startup_path or developer.profile_path
        local ok, saved, err = pcall(developer_profiles.write, path, arguments.values or processing.snapshot())
        return ok and saved == true, t(ok and saved and "main.processing.exported" or "main.processing.invalid",
            { path = path, error = tostring(ok and err or saved) })
    elseif action == "reset_metrics" then processing.reset_metrics()
    elseif action == "capture_next" then processing.capture_next()
    elseif action == "pause" then processing.set_paused(true)
    elseif action == "resume" then processing.set_paused(false)
    elseif action == "step_packet" or action == "step_primitive" then
        processing.step(action == "step_primitive" and "primitive" or "packet")
    elseif action == "cold_reset" or action == "rebuild_tab" or action == "fresh_discovery" then
        developer.queued_action = action
    else return false, t("main.processing.invalid", { error = "unknown_action" }) end
    if window and window.processing_changed then window:processing_changed() end
    return true, t("main.processing.action_queued")
end
function developer.settle(budget)
    if not processing.pending() and not developer.queued_action then return end
    if running_job or active_command or preferences:is_busy() or #receipt_jobs > 0 then return end
    local paints_ready = type(paint_cache.quiesce) ~= "function" or paint_cache:quiesce(budget)
    local stock_ready = type(stock_cache.quiesce) ~= "function" or stock_cache:quiesce(budget)
    if not paints_ready or not stock_ready or background.is_busy and background.is_busy() then return end
    if processing.pending() then
        local requested = processing.requested()
        local configured, err = background.configure and background.configure(requested)
        if configured == false then
            log("Processing settings are waiting for Lua task settlement: " .. tostring(err))
            return
        end
        processing.commit(config)
        progress_tracker = progress_module.new(config)
        if window and window.processing_changed then window:processing_changed() end
        log("Processing policy applied: " .. json.encode(processing.snapshot()))
    end
    local action = developer.queued_action
    developer.queued_action = nil
    if action == "cold_reset" then
        paint_cache:reset_derived(); stock_cache:reset_derived()
        clear_paints()
        if window then
            if window.processing_reset_derived then window:processing_reset_derived(action) end
            window:close("Developer cold-cache reset")
        end
        ui_resources:invalidate(false)
        ui_resources:resume()
    elseif action == "rebuild_tab" and window and window.processing_reset_derived then
        window:processing_reset_derived(action)
    elseif action == "fresh_discovery" then
        stock_cache:reset_derived()
        -- The next opening captures the new interval.
    end
end

local function get_window()
    if not window then
        window = ui_module.new(config, {
            enqueue = enqueue, learn_recipe = request_learning,
            learn_all = request_bulk_learning, transfer_paint = request_transfer, log = log,
            processing_descriptors = processing.descriptors, processing_snapshot = developer.snapshot,
            processing_apply = developer.apply, processing_action = developer.action,
            icons = function() return ui_resources:get() end,
            preset_refresh = function() return request_presets("preset_refresh") end,
            preset_save = function(id, name) return request_presets("preset_save", id, name) end,
            preset_apply = function(id, preset_id) return request_presets("preset_apply", id, preset_id) end,
            preset_delete = function(preset_id) return request_presets("preset_delete", preset_id) end,
            preset_import = request_import,
            change_settings = function(changes)
                if not connection_check() then return false, t("main.connection.lost") end
                local accepted, code = preferences:change_settings(changes)
                if code == "preferences_not_ready" and build == config.reference_build
                    and (not storage_snapshot or not preferences.loaded) then
                    return false, t("main.settings.not_ready"), "preferences_loading"
                end
                local key = code == "preferences_queue_full" and "main.settings.queue_full"
                    or code == "preferences_invalid_setting" and "main.settings.invalid"
                    or "main.settings.not_ready"
                return accepted, t(accepted and "main.settings.queued" or key)
            end,
            favorite = function(asset_type, asset_name, enabled)
                if not connection_check() then return false, t("main.connection.lost") end
                local accepted, code = preferences:favorite(asset_type, asset_name, enabled)
                local key = code == "preferences_queue_full" and "main.settings.queue_full"
                    or code == "preferences_invalid_favorite" and "main.settings.invalid_paint"
                    or "main.settings.not_ready"
                return accepted, t(accepted and "main.settings.queued" or key)
            end,
        })
        if type(window.set_preferences) == "function" then window:set_preferences(ui_settings, ui_favorites) end
    end
    return window
end

local function bind(letter, command)
    local key = Key[letter]
    local modifiers = { ModifierKey.CONTROL, ModifierKey.ALT }
    if key == nil then log("Shortcut unavailable: " .. letter); return end
    if type(IsKeyBindRegistered) == "function" then
        local ok, occupied = pcall(IsKeyBindRegistered, key, modifiers)
        if not ok then log("Could not check shortcut; skipped Ctrl+Alt+" .. letter); return end
        if occupied then log("Shortcut occupied; skipped Ctrl+Alt+" .. letter); return end
    end
    local ok, err = pcall(RegisterKeyBind, key, modifiers, function() enqueue(command) end)
    if ok then log("Ctrl+Alt+" .. letter .. " -> " .. command)
    else log("Could not bind " .. command .. ": " .. tostring(err)) end
end

if type(LoopInGameThreadWithDelay) ~= "function" then
    log("Disabled: game-thread loop API unavailable. No shortcuts registered.")
    return
end

-- Each integration records visibility while handling its own native input.
-- Choosing a budget never enumerates widgets or starts work by itself.
local function ui_visible()
    return window and window:is_open() or native_editor and native_editor:is_ui_visible()
        or deposit_similar and deposit_similar:is_ui_visible() or false
end

local function pump_budget()
    local current = running_job
    local target_ms = processing.work_target_ms(ui_visible(), config)
    local options = current and is_action(current.command) and not learning_preparation(current)
        and { batching_enabled = true, time_limit_enabled = true, unit_limit_enabled = true } or nil
    if learning_preparation(current) then
        -- Byte parsing/copying must not perform native timing calls for each
        -- primitive or inherit a deadline already spent polling UI controls.
        local budget = work_budget.new(target_ms, config.work_units_per_tick, nil, options)
        budget.clock_source, budget.native_clock = "os.clock; read-only learning preparation", false
        budget.read_only_preparation = true
        return budget
    end
    if not current or current.command ~= "learn_all" or current.phase ~= "collect" then
        return work_budget.new(target_ms, config.work_units_per_tick, nil, options)
    end
    if current.clock_options and current.clock_options.clock_ms and not current.nativeClockBroken then
        local function read_seconds()
            local value, err = current.clock_options.clock_ms()
            if type(value) ~= "number" or value ~= value or value < 0 or value == math.huge
                or (current.lastNativeMs and value < current.lastNativeMs) then
                current.nativeClockBroken = true
                current.clockError = err or "Native accurate time became invalid or moved backwards"
                error(current.clockError)
            end
            current.lastNativeMs = value
            return value / 1000
        end
        local budget = work_budget.new(target_ms, config.work_units_per_tick, read_seconds, options)
        budget.clock_source, budget.native_clock = native_clock.source, true
        if budget.clock_valid then return budget end
        current.nativeClockBroken = true
        current.clockError = current.clockError or budget.clock_error or "Native action budget clock failed"
    end
    local fallback = work_budget.new(target_ms, config.work_units_per_tick, nil, options)
    fallback.clock_source, fallback.native_clock = "os.clock; count-limited batch fallback", false
    return fallback
end

-- These views debit the same deadline and operation counter. Cache promotion
-- leaves units for progressive rendering instead of monopolizing an open UI.
local function budget_share(budget, maximum)
    if maximum == nil or budget.batching_enabled == false then return budget end
    local initial = budget.units
    return setmetatable({
        expired = function() return budget:expired() or budget.units - initial >= maximum end,
        consume = function(_, units) budget:consume(units) end,
    }, { __index = function(_, key)
        local value = budget[key]
        if type(value) == "function" then return function(_, ...) return value(budget, ...) end end
        return value
    end })
end

local function render_view(budget)
    if window and window:is_open() and type(window.set_progress) == "function" then
        local emitted = progress_tracker:pump(developer.scheduler_ms)
        if emitted then window:set_progress(emitted) end
    end
    if window and type(window.render_tick) == "function" then
        -- A toggle or error may have closed/opened the shell in this callback.
        -- Keep elapsed time and charged units when switching the render target.
        budget.target_ms = processing.work_target_ms(ui_visible(), config)
        window:render_tick(budget)
    end
end

function developer.preparation_budget(budget)
    if not processing.is_paused() then processing.set_primitive_step(false); return budget end
    local admitted, kind = processing.admit_preparation()
    processing.set_primitive_step(kind == "primitive")
    if admitted then
        return work_budget.new(budget.target_ms, 1, nil,
            { batching_enabled = true, time_limit_enabled = false, unit_limit_enabled = true })
    end
    return setmetatable({ expired = function() return true end }, { __index = budget })
end

developer.open_clock_ms = work_budget.clock_ms

function developer.callback_elapsed()
    -- The handoff timeout continues in real time while the camera releases
    -- even if the native editor disappeared before the mod window opened.
    if not ui_visible() and not (native_editor and native_editor.pending) then
        developer.last_open_ms = nil
        return config.pump_ms
    end
    local previous, now = developer.last_open_ms, developer.open_clock_ms()
    developer.last_open_ms = now
    -- Keep housekeeping live if the CRT clock is unavailable. Start a new
    -- baseline after recovery or rollback, never charge the unobserved gap.
    if not now then return config.pump_ms end
    if previous and now < previous then developer.last_open_ms = nil; return 0 end
    return previous and now - previous or 0
end

local function pump_body(draining)
    if busy then return end
    local elapsed
    if not draining then
        pump_sequence = pump_sequence + 1
        elapsed = developer.callback_elapsed()
        developer.scheduler_ms = developer.scheduler_ms + elapsed
        context_elapsed = context_elapsed + elapsed
        processing.callback()
    end
    developer.deferred = false
    -- Native clock/frame queries are lazy and restricted to an explicit batch.
    local budget = pump_budget()
    if not draining then
    local language_visible = ui_visible()
    if pending[1] == "toggle" or language_visible and (not developer.language_visible
        or developer.scheduler_ms >= (developer.next_language_check or 0)) then
        developer.sync_language(true)
    end
    developer.language_visible = language_visible
    local event = save_lifecycle.consume(budget)
    -- An observed map-post can replace map-pre before the scheduler runs.
    -- Same-generation account revocation cannot release an uncertain transfer.
    if event then transfers:observe_load(event.loadGeneration) end
    if event and event.invalidated then
        stock_cache:invalidate(event.error or "Game load started")
        ship_scope.invalidate("Game load started")
        -- This is a proven native load-pre revocation, not a cache-read error.
        learning.reset_context()
        learning_scope_token = nil
        ui_resources:invalidate(true)
        reset_context(event.error or "Game load started", nil)
    elseif event then
        ui_resources:resume()
        local token = collection.context()
        reset_context("Native player progress load completed", token)
        if token == event.contextToken then
            stock_cache:admit(event)
            sync_event = event
            local admitted, reason = paint_cache:admit(event)
            if not admitted then log("Paint cache admission deferred: " .. tostring(reason)) end
        end
    end
    -- A successful map-post may admit stock while character attachment is
    -- still waiting and the ordinary channel retains its load-pre event.
    -- Apply that old invalidation FIRST, then consume the fresh stock channel.
    local stock_event = type(save_lifecycle.consume_stock) == "function" and save_lifecycle.consume_stock() or nil
    if stock_event then
        if stock_event.invalidated then
            stock_cache:invalidate(stock_event.error or "Read-only stock scope revoked")
            ship_scope.invalidate("Read-only stock scope revoked")
        else stock_cache:admit(stock_event) end
    end
    -- Only cheap context reads while a session has been used. No global scan or
    -- heavy diagnostic is triggered by a timer or by merely loading the mod.
    if context_token and context_elapsed >= config.context_poll_ms then
        context_elapsed = 0
        local ok, err = pcall(check_context)
        if not ok then reset_context("Context read failed: " .. tostring(err), nil) end
        local binding = paint_cache:binding()
        local expected = storage_snapshot and storage_snapshot.accountId and {
            scopeId = storage_snapshot.scopeId, accountId = storage_snapshot.accountId,
            libraryPath = storage_snapshot.path, loadGeneration = storage_snapshot.loadGeneration } or binding
        if context_token and expected and character_possessed() then
            local current = save_bridge.storage_context(context_token)
            if current.status ~= "ready" then
                reset_context(current.error or "Storage restoration scope is no longer ready", nil)
            elseif current.scopeId ~= expected.scopeId or current.accountId ~= expected.accountId
                or current.libraryPath ~= expected.libraryPath or current.loadGeneration ~= expected.loadGeneration then
                local token = context_token
                reset_context("Storage account or native HUD/player scope changed", token)
                sync_event = { contextToken = token }
            end
        end
    end
    if native_editor then
        if (window and window:is_open()) or pending[1] == "toggle" or active_command == "toggle" then
            if native_editor.attached or native_editor.pending then native_editor:reset() end
        else native_editor:tick(elapsed) end
        local attachment = native_editor:is_ui_visible() and native_editor.attached or nil
        if attachment ~= preset_cache.editor then
            preset_cache.editor = attachment
            if attachment and not preset_cache.snapshot and not sync_event then
                editor_library(collection.context(), true)
            end
        end
    end
    budget.target_ms = processing.work_target_ms(ui_visible(), config)
    if deposit_similar and deposit_similar:active() and not budget:expired() then
        deposit_similar:step(budget, elapsed)
        budget.target_ms = processing.work_target_ms(ui_visible(), config)
    end
    if window and window:is_open() then
        local connected = connection_check()
        if not connected then
            if running_job and running_job.cancel_on_close and not transfers.is_return(running_job.command) then
                cancel_job(t("main.connection.lost"), false)
            end
            local retained = {}
            for _, command in ipairs(pending) do if command == "toggle" then retained[#retained + 1] = command end end
            pending = retained
            if active_command ~= "toggle" then active_command = nil end
            preset_request = nil
        end
    end
    -- Closed views may still need to retry releasing an input claim after a
    -- native cleanup error. tick() is otherwise a no-op for a closed window.
    local was_open = window and window:is_open() or false
    local closing_before = window_closing()
    if window then
        local ok, err = pcall(function() window:tick(budget, true) end)
        if not ok then
            pcall(function() window:close("UI error") end)
            status("ui", "error", tostring(err), nil, player_messages.reason_key(err, "main.error.render_failed"))
        end
    end
    -- An Escape press arriving during this tick also revokes queued work.
    local closing = closing_before or window_closing()
    local closing_started = closing and not closing_seen
    closing_seen = closing
    if was_open and (not window:is_open() or closing_started) then
        transfers:invalidate()
        transfers.last_result = nil
        local staged = active_command
        pending, active_command, bulk_request = {}, nil, nil
        preset_request = nil
        set_learning(false)
        if staged and not (window.status and window.status.state == "error") then
            status(staged, "cancelled", t("main.cancel.before_start"))
        end
    end
    if running_job and running_job.cancel_on_close and (not window or not window:is_open() or closing) then
        if window and window.status and window.status.state == "error" then
            local command, message, reason_key = running_job.command, window.status.message, window.status.reasonKey
            cancel_job("UI error", false)
            status(command, "error", message or t("main.error.render_failed"), nil, reason_key)
        else cancel_job(t("main.cancel.window_closed"), true) end
    end
    -- Close/open hotkeys remain responsive while scanning. They supersede the
    -- in-flight read; no partial capture is published as a finished snapshot.
    if pending[1] == "toggle" and running_job then
        if running_job.command == "storage_sync" then sync_event = { contextToken = running_job.contextToken } end
        cancel_job(t("main.cancel.toggle"), false)
    end
    if pending[1] == "toggle" and active_command and active_command ~= "toggle" then
        status(active_command, "cancelled", t("main.cancel.toggle_staged"))
        active_command = nil
        bulk_request = nil
        preset_request = nil
        set_learning(false)
    end
    end -- Mandatory input/lifecycle housekeeping runs once per real callback.

    if running_job and running_job.phase == "transfer" then
        advance_transfer(running_job)
        return
    end

    local opened = window and window:is_open() and not window_closing() and connection.connected
    local presets_visible = opened and window.active_tab == "presets"
    if paused_open and (not opened or pending[1] == "toggle" or paused_open.contextToken ~= context_token
        or paused_open.radius ~= config.ship_radius_cm or not connection.scope
        or paused_open.openScope.token ~= connection.scope.token or paused_open.openScope.shipId ~= connection.scope.shipId) then
        discard_paused_open("Window or paint scope changed")
    end
    if opened and running_job and running_job.initialState then
        -- One opening owns both cursors. The last tab selection orders their
        -- next bounded steps, including after a storage lease was acquired.
        local wants_vault = window.active_tab == "learned" and not recent_snapshot and
            (not running_job.learned and not running_job.catalog or
                window.model and window.model.learnedOnly and window:is_rendering())
        local phase = wants_vault and "cache_wait" or "state_load"
        if running_job.phase ~= phase then
            if phase == "cache_wait" then
                local state = running_job.openState
                if state and state.yield_execution then state:yield_execution() end
            else background.yield_paint_slots() end
            running_job.phase = phase
        end
    end
    if presets_visible and running_job and running_job.openScope and not running_job.initialState and pending[1] ~= "toggle" then
        paused_open, running_job = running_job, nil
        progress_view({ state = "idle", mode = "idle" })
    end
    if presets_visible and not developer.presets_visible then background.yield_paint_slots() end
    if developer.presets_visible and not presets_visible and opened then
        if running_job and running_job.autoPresets then
            cancel_job("Preset tab closed", false)
            if not preset_view then window:set_presets(nil) end
        end
    end
    developer.presets_visible = presets_visible
    if paused_open and transfers.needs_capture then discard_paused_open("Paint inventories changed") end
    if paused_open and not presets_visible and not running_job and not active_command and #pending == 0 then
        running_job, paused_open = paused_open, nil
    end
    -- The opening owns just one paint cursor. While parked, no collection,
    -- catalog, or box output can advance behind the visible Presets page.
    if presets_visible then start_initial_preset_view() end
    -- Start read-only preparation after mandatory input housekeeping. Explicit
    -- mutations keep their existing native timing/frame admission contract.
    -- Cache, transport, capture and rendering share the resulting one budget.
    if not (running_job and is_action(running_job.command)) or learning_preparation(running_job) then
        budget = pump_budget()
    end
    -- Native input/close handling above has priority. Remaining budget may drain
    -- plain mutation receipts even with a closed window or changed context.
    local settled_report = background.pump(budget)
    if settled_report then
        if settled_report.kind=="storage_state" or settled_report.kind=="storage_verify" then
            local receipt=settled_report.data or {}
            if receipt.localCommitted or settled_report.localCommitted then
                preset_cache:clear()
                log("Background storage settled after request retirement; localCommitted=true; mirrorOutcome="
                    .. tostring(receipt.mirrorOutcome or "pending") .. "; localUncertain="
                    .. tostring(receipt.localUncertain==true or settled_report.localUncertain==true))
            elseif settled_report.localUncertain then
                preset_cache:clear()
                log("Background storage retired with an uncertain local outcome")
            end
        elseif settled_report.publishedAfterCancellation then
            log("Report publication completed after cancellation: " .. tostring(settled_report.path))
        elseif #(settled_report.cleanupErrors or {}) > 0 then
            log("Retired report cleanup failed: " .. table.concat(settled_report.cleanupErrors, "; "))
        end
    end
    if preset_cache.job and not (running_job and is_action(running_job.command)) then
        local quota = ui_visible() and processing.quota("foreground_units", 6) or 1
        preset_cache:step(budget_share(budget, quota))
    end
    if native_editor and native_editor.popup then
        if native_editor.popup.phase == "inspect" then
            -- Reading the selected zones can overlap an outstanding report or
            -- storage reconciliation. The latter still owns publication.
            native_editor:work(budget_share(budget, processing.quota("foreground_units", 6)))
        elseif not running_job and not active_command and #pending == 0 and not preferences.active
            and native_editor:work(budget) then return end
    end
    local saving_preset = pending[1] == "preset_save" or active_command == "preset_save"
        or running_job and running_job.command == "preset_save"
    local loading_preset = preset_request ~= nil or saving_preset or presets_visible and (window.needs_preset_refresh and window:needs_preset_refresh()
        or running_job and is_preset(running_job.command) or window:is_rendering())
    local loading_learned = opened and window.active_tab == "learned" and running_job and running_job.openScope
        and running_job.phase ~= "state_load" and (not running_job.learnedShown and
            (running_job.phase == "cache_wait" or running_job.learned) or running_job.learnedShown and window:is_rendering())
    if not loading_preset and not loading_learned then
        pump_receipts(budget)
        if not paused_open then developer.settle(budget) end
    end
    local preparation_budget = developer.preparation_budget(budget)
    if not draining and window and window:is_open() and not window_closing()
        and not running_job and not active_command and #pending == 0 and not presets_visible then
        if transfers.needs_capture and opened then
            start_collection("toggle")
            running_job.afterTransfer, transfers.last_result = transfers.last_result, nil
            transfers.needs_capture = nil
        elseif window.set_transfer_state
            and (not opened or not window.active_tab or window.active_tab == "learned"
                or window.active_tab == "ready" or window.active_tab == "missing") then
            if build == config.reference_build then
                transfers:refresh(window, context_token, model, developer.scheduler_ms, preparation_budget)
            elseif not window.transfer_state then
                window:set_transfer_state({ contextToken = context_token, unavailable = t("main.transfer.build_unverified") })
            end
        end
    end
    -- Locate the ship in one bounded unit AFTER input/close housekeeping.
    if not loading_preset and not draining and not (running_job and is_action(running_job.command))
        and (window and window:is_open() and not connection.connected or pending[1] == "toggle" or active_command == "toggle")
        and not budget:expired() then
        local scope = ship_scope.prepare(config, nil, budget_share(budget, 1))
        if scope and scope.token ~= last_ship_scope_token then
            last_ship_scope_token = scope.token
            log("Ship point ready: radiusCm=" .. tostring(scope.radiusCm) .. "; sourcePolicyVersion=2")
        end
    end
    -- Original control images warm independently from the visible window.
    -- Native imports stay on this game-thread pump and share its remaining
    -- budget; explicit gameplay mutations keep priority over cosmetic work.
    local visible_work = window and window:is_open() and window:is_rendering()
    if not presets_visible and not loading_preset and not visible_work and not running_job then
        local idle = not running_job and not active_command and #pending == 0
            and not (window and window:is_open())
        local signal_visible = window and window:is_open() and not connection.connected
        -- The disconnected shell has no foreground gameplay/list work. Give
        -- its requested illustration more of the SAME bounded shared budget.
        local opened = window and window:is_open()
        local demanded = opened and window.icons ~= nil and window.icons == ui_resources.icons
            or not opened and (pending[1] == "toggle" or active_command == "toggle")
        if type(ui_resources.set_demand) == "function" then ui_resources:set_demand(demanded) end
        if not processing.pending() and (not draining or demanded) then
            ui_resources:tick(budget_share(preparation_budget, processing.quota(signal_visible and "resource_signal_units"
                or idle and "resource_idle_units" or "resource_open_units", signal_visible and 7 or idle and 4 or 2)), demanded,
                developer.scheduler_ms, opened)
        end
    end
    if not presets_visible and not running_job and not active_command and #pending == 0 and stock_cache.settle_output then
        stock_cache:settle_output(budget_share(budget, 1), developer.scheduler_ms)
    end
    local storage_busy = running_job and (running_job.phase == "state_load" or running_job.phase == "state_sync"
        or is_preset(running_job.command))
    if loading_learned then
        -- The Vault and its page need no storage lease. Park any preference
        -- cursor between operations and let the visible page use its permit.
        local writer = preferences.active and preferences.active.job
        if writer and writer.yield_execution then writer:yield_execution() end
    elseif not (running_job and is_action(running_job.command)) and (not loading_preset or preferences.active) then
        preferences:step(budget_share(budget, processing.quota("preference_units", 1)),
            not loading_preset and not storage_busy and active_command == nil and pending[1] ~= "toggle")
    end
    if running_job then
        busy = true
        local command = running_job.command
        local preparation = running_job.phase == "collect" or running_job.phase == "cache_wait"
            or running_job.phase == "stock_wait" or running_job.phase == "legacy_snapshot"
        local quota = running_job.phase == "stock_wait" and "stock_open_units"
            or running_job.phase == "cache_wait" and "paint_urgent_units" or "foreground_units"
        local active_budget = is_action(command) and (learning_preparation(running_job) and preparation_budget or budget)
            or budget_share(preparation and preparation_budget or budget, processing.quota(quota, quota == "foreground_units" and 6 or 4))
        running_job.started_callback = running_job.started_callback or pump_sequence
        local before = processing.measuring() and active_budget:elapsed_ms() or nil
        local before_units = active_budget.units
        local ok, result = pcall(step_collection, active_budget)
        processing.record(command, before and math.max(0, active_budget:elapsed_ms() - before) or nil, active_budget.units - before_units,
            { phase = running_job and running_job.phase or "done", clock = active_budget.clock_source or "os.clock" })
        busy = false
        if ok and result then
            start_initial_preset_view(result)
        elseif not ok then
            cancel_job("Collection failed", false)
            if window and command == "toggle" then
                pcall(function() window:close("Collection error") end)
                pending, active_command = {}, nil
            end
            status(command, "error", tostring(result), nil, player_messages.reason_key(result))
        end
        render_view(preparation_budget)
        return
    end
    if not active_command then
        if #pending == 0 then
            if sync_event and build == config.reference_build and not preferences.active
                and (not preferences:is_busy() or not storage_snapshot or native_editor and native_editor.popup)
                and character_possessed() then
                local event = sync_event; sync_event = nil
                local token = check_context()
                if token == event.contextToken then
                    running_job = { command = "storage_sync", phase = "state_sync", contextToken = token,
                        collector = current_store(token).begin_state_load(),
                        progress_ticks = 0, cancel_on_close = false }
                end
            end
            render_view(preparation_budget)
            return
        end
        active_command = table.remove(pending, 1)
        progress_generation = progress_generation + 1
        status(active_command, "running")
        -- Return to the engine so the running label can paint BEFORE an
        -- expensive synchronous discovery/JMAP starts on the following tick.
        render_view(preparation_budget)
        return
    end
    local command = active_command
    if is_preset(command) and preferences.active
        or command == "preset_import" and preferences:is_busy() then
        render_view(preparation_budget); return
    end
    active_command = nil
    busy = true
    local ok, err = pcall(function()
        if is_preset(command) then assert(connection.connected, t("main.connection.lost"))
        elseif command ~= "toggle" then assert(connection_check(), t("main.connection.lost")) end
        if developer_commands[command] and ui_settings.showDeveloperTools ~= true then
            error(t("main.settings.developer_disabled"))
        end
        local path, message
        if command == "toggle" then
            local ui = get_window()
            if ui:is_open() then
                pending, bulk_request, preset_request = {}, nil, nil
                set_learning(false)
            end
            if not ui:is_open() then
                local token, context_err = check_context()
                if not token then error(context_err or t("main.error.load_character")) end
                local connected = connection_check()
                local age = recent_snapshot and os.difftime(os.time(), recent_snapshot.at)
                model = nil
                if connected and age and not recent_snapshot.dirty and age >= 0 and age <= config.reopen_snapshot_seconds
                    and same_snapshot_scope(recent_snapshot, token, connection.scope) then
                    if ui.model ~= recent_snapshot.model and ui.capture_source ~= recent_snapshot.model then
                        ui:set_model(recent_snapshot.model)
                    end
                else clear_paints() end
            end
            local opened, ui_err = atomic_operation("core UI open/close", budget, function() return ui:toggle(true) end)
            developer.last_open_ms = opened and developer.open_clock_ms() or nil
            if ui_err then error(ui_err) end
            if opened then
                transfers:invalidate()
                background.yield_paint_slots()
                local connected = connection_check()
                if not connected then
                    message = t("main.connection.lost")
                elseif build == config.reference_build and not storage_snapshot and character_possessed() then
                    storage_snapshot = nil
                    preset_view = nil
                    if type(ui.set_presets) == "function" then ui:set_presets(nil) end
                    start_collection("toggle", true)
                else
                    if build ~= config.reference_build then persistence(storage_message("main.storage.build", nil, true)) end
                    start_collection(command)
                end
            end
            message = message or t(opened and "main.status.opened" or "main.status.closed")
        elseif command == "preset_import" then
            local token, context_err = check_context()
            assert(token, context_err or t("main.error.no_character"))
            assert(window and window:is_open(), t("main.learn.window_closed"))
            local store = current_store(token, storage_snapshot)
            running_job = { command = command, phase = command, contextToken = token,
                collector = store.begin_import(preset_path), progress_ticks = 0, cancel_on_close = true }
        elseif is_preset(command) then
            start_presets(command)
        elseif is_learning(command) then
            set_learning(true)
            start_collection(command)
        elseif command == "snapshot" then
            start_collection(command)
        elseif command == "sources" then
            start_collection(command)
        elseif command == "discovery" then
            local data = atomic_operation("metadata discovery", budget, function() return reflection.discover(config) end)
            running_job = { command = command, data = data, phase = "write", report_only = true,
                context_independent = true, progress_ticks = 0,
                cancel_on_close = window and window:is_open() or false }
        elseif command == "jmap" then
            if type(DumpJMAP) ~= "function" then error("DumpJMAP unavailable on this loader") end
            -- Pinned loader supports both args. This dump still contains game
            -- metadata/Blueprint bytecode: keep it local, outside source control.
            atomic_operation("JMAP", budget, function() DumpJMAP(true, true) end)
            message = t("main.status.jmap_returned")
            running_job = { command = command, reportKind = "jmap-request", phase = "write", report_only = true,
                context_independent = true, progress_ticks = 0, message = message,
                cancel_on_close = window and window:is_open() or false,
                data = { note = "DumpJMAP(true, true) returned. Find the generated .jmap in UE4SS working directory; confirm completion in UE4SS.log. The path of this receipt is not the JMAP path." } }
        end
        if not running_job then status(command, "done", message, path) end
    end)
    busy = false
    if not ok then
        cancel_job("Command failed", false)
        bulk_request = nil
        preset_request = nil
        set_learning(false)
        -- No stranded cursor/input capture if construction or rendering failed.
        if command == "toggle" then
            if window then pcall(function() window:close("Command error") end) end
        end
        status(command, "error", tostring(err), nil, player_messages.reason_key(err))
    end
    render_view(preparation_budget)
end

function developer.has_demand()
    return running_job ~= nil or active_command ~= nil or #pending > 0 or preferences:is_busy()
        or #receipt_jobs > 0
        or window and window.processing_has_work and window:processing_has_work()
        or ui_resources.processing_busy and ui_resources:processing_busy()
end
function developer.signature()
    return table.concat({ tostring(running_job), running_job and running_job.phase or "", tostring(active_command),
        tostring(#pending), tostring(#receipt_jobs), tostring(processing.work_sequence()),
        paint_cache.progress_signature and paint_cache:progress_signature() or "",
        stock_cache.progress_signature and stock_cache:progress_signature() or "" }, "|")
end
function developer.drain()
    if processing.batching_enabled() or processing.pending() or processing.is_paused() then return end
    developer.draining = true
    while developer.has_demand() do
        -- Let Slate paint the newly accepted Save row once before even the
        -- developer's synchronous drain starts its durable work.
        if active_command == "preset_save" or pending[1] == "preset_save" then break end
        -- Native writes keep their existing per-frame/callback admission.
        if running_job and is_action(running_job.command) and not learning_preparation(running_job) then break end
        if developer.deferred then processing.event("deferred"); break end
        local before = developer.signature()
        pump_body(true)
        if developer.deferred then processing.event("deferred"); break end
        if before == developer.signature() then processing.event("no_progress"); break end
    end
    developer.draining = false
end

local function run_pump()
    -- Native UI/ProcessEvent calls can recurse into the pinned scheduler. Guard
    -- the entire pump, including construction, input polling and status labels.
    pumping = true
    local before = processing.measuring() and background.monotonic_ms and background.monotonic_ms() or nil
    local ok, err = pcall(function() pump_body(false); developer.drain() end)
    developer.draining = false
    processing.set_primitive_step(false)
    local after = before and background.monotonic_ms and background.monotonic_ms() or nil
    processing.record("callback", before and after and math.max(0, after - before) or nil, nil,
        { clock = before and after and native_clock.source or "unavailable" })
    if processing.get("metrics_level") == "detailed" and pump_sequence % processing.get("log_sample_callbacks") == 0 then
        log("Processing measurements: " .. json.encode(processing.metrics()))
    end
    if not ok then
        busy, active_command, bulk_request = false, nil, nil
        cancel_job("Pump error", false)
        pcall(set_learning, false)
        if window then pcall(function() window:close("Pump error") end) end
        pcall(function() status("pump", "error", tostring(err), nil, player_messages.reason_key(err)) end)
    end
    -- Error cleanup/status also call native UI. Retain the guard until every
    -- such call has returned, even if reporting the original error fails.
    pumping = false
end

-- UE4SS queues a loop registered from a callback for a later dispatch.
-- A frame loop decrements its counter on EngineTick only. The timed loop
-- starts the closed-window delay after the preceding callback completes.
local scheduler = {}
local function cancel_loop(loop)
    if loop and loop.handle ~= nil and type(CancelDelayedAction) == "function" then
        local ok, cancelled = pcall(CancelDelayedAction, loop.handle)
        if ok and cancelled then loop.handle = nil end
    end
end

local function set_scheduler_mode(open)
    local previous = scheduler.active
    local register = LoopInGameThreadWithDelay
    local interval = config.pump_ms
    if open then register, interval = LoopInGameThreadAfterFrames, 1 end
    if type(register) ~= "function" or previous and type(CancelDelayedAction) ~= "function" then
        return false, "UE4SS loop API unavailable"
    end
    local loop = { open = open }
    local ok, handle = pcall(register, interval, function()
        if scheduler.stopped or pumping then return end
        if scheduler.active ~= loop then cancel_loop(loop); return end
        if loop.last_closed_ms and not ui_visible() then
            local now = developer.open_clock_ms()
            if now and now >= loop.last_closed_ms and now - loop.last_closed_ms < config.pump_ms then return end
        end
        run_pump()
        if scheduler.stopped then return end
        local visible = ui_visible()
        if visible == loop.open then
            scheduler.retry_at = nil
        elseif not scheduler.retry_at or pump_sequence >= scheduler.retry_at then
            local switched, reason = set_scheduler_mode(visible)
            if not switched then
                if not scheduler.retry_at then log("Keeping current scheduler: " .. tostring(reason)) end
                scheduler.retry_at = pump_sequence + math.max(1, math.ceil(1000 / config.pump_ms))
            end
        end
        -- If the timed API temporarily refuses registration, the frame loop
        -- keeps closed-window work at the usual delay until the retry succeeds.
        if loop.open and scheduler.active == loop then
            loop.last_closed_ms = not visible and developer.open_clock_ms() or nil
        end
    end)
    if not ok or type(handle) ~= "number" then return false, handle or "UE4SS returned no loop handle" end
    loop.handle = handle
    scheduler.active, scheduler.retry_at = loop, nil
    cancel_loop(previous)
    return true
end

local scheduler_ok, scheduler_error = set_scheduler_mode(false)
if not scheduler_ok then
    log("Disabled: game-thread loop registration failed: " .. tostring(scheduler_error))
    return
end

if ModRef ~= nil then
    ModRef.OnUnload = function()
        scheduler.stopped = true
        if deposit_similar then pcall(function() deposit_similar:close() end) end
        cancel_loop(scheduler.active)
        scheduler.active = nil
        if native_editor then pcall(function() native_editor:reset() end) end
        discard_paused_open("Mod unloading")
        if cancel_job then pcall(cancel_job, "Mod unloading") end
        -- Cancelling an action can enqueue its final receipt. No scheduler
        -- remains to finish those writers after unload, so close owned temps.
        for _, writer in ipairs(receipt_jobs) do pcall(function() writer:cancel("Mod unloading") end) end
        receipt_jobs = {}
        if window then pcall(function() window:shutdown("Mod unloading") end) end
        pcall(function() ui_resources:invalidate(true) end)
        pcall(background.invalidate_scope)
        if type(background.shutdown) == "function" then pcall(background.shutdown) end
    end
end

bind(config.snapshot_key, "snapshot")
bind(config.ui_key, "toggle")
bind(config.discovery_key, "discovery")
bind(config.jmap_key, "jmap")
-- UE4SS dispatches all matching bindings, including HumanBuilder's. The
-- callback only sets Lua state on the current opening; its tick owns cleanup.
if Key.ESCAPE ~= nil then
    local bound, failure = pcall(RegisterKeyBind, Key.ESCAPE, {}, function()
        if window then window:request_escape() end
    end)
    if bound then log("Escape -> close the open PaintManager window")
    else log("Could not bind Escape: " .. tostring(failure)) end
else log("Shortcut unavailable: Escape") end
save_lifecycle.install(config, build, function(key, detail) log(key .. ": " .. tostring(detail)) end)
if build == config.reference_build then
    deposit_similar = require("paintmanager.deposit_similar").install(config, {
        game_build = function() return build end,
        make_button = function(tree, serial, key, stock_button)
            return ui_module.make_deposit_button(tree, serial, key, stock_button, ui_resources)
        end,
        guard = function(token)
            if busy or context_token and token ~= context_token or running_job and is_action(running_job.command)
                or active_command and is_action(active_command) then return false end
            for _, command in ipairs(pending) do if is_action(command) then return false end end
            return true
        end,
        changed = function(token)
            if context_token and token ~= context_token then return end
            transfers:invalidate()
            transfers.needs_capture, model = true, nil
            if recent_snapshot then recent_snapshot.dirty = true end
        end,
        log = log,
    })
    if preferences.loaded then deposit_similar:set_settings(ui_settings) end
end
local background_state = background.describe()
log("Report backend=" .. background_state.backend .. (background_state.reason and ": " .. background_state.reason or ""))
log("Ready; build=" .. build .. ". Opening the window requests one ship paint and stock interval. Ship radius defaults to 35 m and Settings allows up to 50 m. The same ship point/radius gates the window and boxes. Explicit recipe learning keeps resources; no automatic grants. Shortcut checks are UE4SS-only.")
if build ~= config.reference_build then
    log("Build differs from research reference; compatibility requires verification.")
end
