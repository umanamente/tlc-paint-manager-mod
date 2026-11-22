-- Map-load completion admission for the fixed reference build. No inventory
-- traversal, carrier/file reads or writes run inside these callbacks. Bounded
-- account identity getters bind the completed load before reconciliation.
local safe = require("paintmanager.safe")
local M = {}
local installed, loading, generation = false, false, 0
local ready, queued, hud_seen, logger, restored
local stock_ready, stock_queued

local function identity(object)
    assert(safe.valid(object), "Lifecycle object is unavailable")
    local name, address = safe.name(object), object:GetAddress()
    assert(name and (type(address) == "number" or type(address) == "string"), "Lifecycle identity is unavailable")
    return name .. "@" .. tostring(address)
end

local function unwrap(parameter)
    -- RemoteUnrealParam uses get() in the pinned loader. Tests also exercise
    -- a plain UObject; no wrapper survives the callback.
    return safe.unwrap(parameter)
end

local function log(key, detail)
    if logger then pcall(logger, key, detail) end
end

local function attach_restored()
    local expected, expected_generation = restored, generation
    local bridge = require("paintmanager.save_bridge")
    local value = bridge.attach_restored_context(expected.baseScopeId)
    if generation ~= expected_generation or loading or restored ~= expected then return nil end
    if value.status == "waiting_pawn" then return nil end
    if value.status == "ready" then
        local account = bridge.capture_restored_account(expected.baseScopeId)
        if generation ~= expected_generation or loading or restored ~= expected then return nil end
        if account.status ~= "ready" or account.accountId ~= expected.accountId or account.libraryPath ~= expected.libraryPath then
            value = { status = "not_ready", error = account.error or "Restored account changed before Pawn attachment" }
        end
    end
    if value.status ~= "ready" then
        restored, ready = nil, nil
        stock_ready = nil
        local event = { invalidated = true, loadGeneration = generation,
            error = value.error or "Restored native base changed before Pawn attachment" }
        log("save_lifecycle_not_ready", event.error)
        stock_queued = event
        return event
    end
    value.accountId, value.libraryPath, value.loadGeneration = expected.accountId, expected.libraryPath, expected_generation
    ready, restored = value, nil
    log("save_lifecycle_ready", "Restored HUD/player scope observed after native LoadMap; current Pawn attached to the same base")
    return value
end

function M.install(config, build, log_function)
    if installed then return true end
    logger = log_function
    if tostring(build) ~= "25982825" then
        log("save_lifecycle_unsupported", "Automatic reconciliation requires reference build 25982825")
        return false, "Unsupported native storage lifecycle build"
    end
    if type(RegisterLoadMapPreHook) ~= "function" or type(RegisterLoadMapPostHook) ~= "function"
        or type(RegisterBeginPlayPostHook) ~= "function" then
        log("save_lifecycle_unavailable", "Required map/actor lifecycle hooks are unavailable")
        return false, "Required native storage lifecycle hooks are unavailable"
    end
    local ok, failure = pcall(function()
        RegisterLoadMapPreHook(function()
            generation = generation + 1
            loading, ready, hud_seen, restored = true, nil, nil, nil
            stock_ready = nil
            -- Revoke owned Lua publication permits at map-pre itself.
            -- File cleanup is deferred to the next scheduler pump.
            local revoked,reason=require("paintmanager.background").invalidate_scope()
            if not revoked then log("save_lifecycle_task_revoke_failed",reason) end
            queued = { invalidated = true, loadGeneration = generation }
            stock_queued = queued
            -- Returning nil preserves the original engine return value.
        end)
        RegisterBeginPlayPostHook(function(parameter)
            if not loading then return end
            local captured, value = pcall(function()
                local actor = unwrap(parameter)
                if not safe.valid(actor) or actor:IsA("/Script/Voyage.VoyageHUD") ~= true then return nil end
                local base = require("paintmanager.save_bridge").capture_restored_context(actor)
                assert(base.status == "ready", base.error or "HUD restoration source is unavailable at BeginPlay")
                return base
            end)
            if captured and value then hud_seen = value
            elseif not captured then hud_seen = { error = tostring(value) } end
            -- Base AActor::BeginPlay has returned here, but VoyageHUD's
            -- derived restoration has NOT. This event alone never admits work.
        end)
        RegisterLoadMapPostHook(function(_, world_parameter, _, _, error_text)
            if not loading then return end
            loading = false
            local completed_generation = generation
            local observed = hud_seen
            hud_seen = nil
            local captured, value = pcall(function()
                assert(error_text == nil or error_text == "", "Native map load reported an error")
                local world = unwrap(world_parameter)
                assert(observed and observed.baseScopeId, observed and observed.error or "No matching valid HUD restoration source during this map load")
                assert(identity(world) == observed.worldId, "Loaded world does not match the observed HUD")
                local bridge = require("paintmanager.save_bridge")
                local current = bridge.capture_restored_context()
                assert(current.status == "ready" and current.worldId == observed.worldId, current.error or "Loaded world changed")
                assert(current.baseScopeId == observed.baseScopeId, "HUD restoration source changed before map completion")
                local account = bridge.capture_restored_account(current.baseScopeId)
                assert(account.status == "ready", account.error or "Restored native account is unavailable")
                current.accountId, current.libraryPath = account.accountId, account.libraryPath
                current.loadGeneration = generation
                return current
            end)
            if generation ~= completed_generation or loading then
                log("save_lifecycle_not_ready", "A new map load began during restoration identity capture")
                return
            end
            if captured then
                restored = value
                -- The same proven HUD-copy/account boundary admits read-only
                -- stock independently of later character possession. It never
                -- authorizes the authoritative library/HUD write route.
                stock_ready, stock_queued = value, value
                -- This bounded identity check also preserves immediate admission
                -- when possession already exists at the completed map boundary.
                local attached = attach_restored()
                if attached then queued = attached
                else log("save_lifecycle_restored", "HUD restoration base/account verified; waiting only for a current character on that same base") end
            else
                ready, restored = nil, nil
                stock_ready = nil
                queued = { invalidated = true, loadGeneration = generation, error = tostring(value) }
                stock_queued = queued
                log("save_lifecycle_not_ready", tostring(value))
            end
        end)
    end)
    if not ok then
        ready, loading = nil, false
        stock_ready, stock_queued, restored, hud_seen = nil, nil, nil, nil
        log("save_lifecycle_unavailable", tostring(failure))
        return false, tostring(failure)
    end
    installed = true
    return true
end

function M.consume(budget)
    local value = queued
    queued = nil
    if not value and restored and not loading then
        if budget and budget:expired() then return nil end
        value = attach_restored()
        if budget then budget:consume() end
    end
    if not value then return nil end
    local copy = {}
    for key, item in pairs(value) do copy[key] = item end
    return copy
end

function M.is_ready(token, scope)
    return installed and not loading and ready ~= nil and ready.contextToken == token and ready.scopeId == scope,
        generation
end

function M.consume_stock()
    local value = stock_queued
    stock_queued = nil
    if not value then return nil end
    local copy = {}
    for key, item in pairs(value) do copy[key] = item end
    return copy
end

function M.is_stock_ready(base, account_id, path)
    return installed and not loading and stock_ready ~= nil
        and stock_ready.baseScopeId == base and stock_ready.accountId == account_id
        and stock_ready.libraryPath == path, generation
end

function M.admit_account(token, scope, account_id, path)
    if not M.is_ready(token, scope) then return false, "Native storage lifecycle is not ready" end
    if ready.accountId == account_id and ready.libraryPath == path then return true end
    -- A different account on the same restored HUD cannot inherit that HUD's
    -- previous account document. Only another proven load can admit it again.
    ready = nil
    stock_ready = nil
    queued = { invalidated = true, loadGeneration = generation, error = "Native save account changed without a restored game context" }
    stock_queued = queued
    log("save_lifecycle_account_changed", queued.error)
    return false, queued.error
end

return M
