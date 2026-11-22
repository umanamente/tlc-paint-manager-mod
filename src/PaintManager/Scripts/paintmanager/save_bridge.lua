-- Fixed vanilla HUD-map carrier. Call on the game thread after build admission.
-- A staged value is only in memory until the game performs its ordinary save.
local safe = require("paintmanager.safe")
local KEY, MAX_BYTES = "PaintManager.State", 32768
local ORIGINS_KEY = "PaintManager.TransferOrigins"
local M = { key = KEY, max_bytes = MAX_BYTES }
local uncertain, origins_uncertain = {}, {}
local LIBRARY = "/Script/Voyage.Default__VoyageBlueprintUIFunctionLibrary"
local LIBRARY_CLASS = "/Script/Voyage.VoyageBlueprintUIFunctionLibrary"

local function is_a(object, class)
    return safe.valid(object) and object:IsA(class) == true
end
local function identity(object)
    assert(safe.valid(object), "Save carrier object is unavailable")
    local name, address = safe.name(object), object:GetAddress()
    assert(name and (type(address) == "string" or type(address) == "number"), "Save carrier identity is unavailable")
    return name .. "@" .. tostring(address)
end
local function base_context(observed_hud)
    -- During HUD BeginPlay the controller's MyHUD assignment may still be
    -- pending in SpawnActor. The observed HUD's owning controller is the
    -- restoration source; only map-post/normal reads require current GetHUD.
    local controller = observed_hud and observed_hud:GetOwningPlayerController()
        or FindFirstOf("VoyagePlayerController")
    assert(is_a(controller, "/Script/Voyage.VoyagePlayerController"), "Restoration base controller is unavailable")
    assert(controller:IsLocalController() == true, "Restoration base requires the local controller")
    local state = controller.PlayerState
    assert(is_a(state, "/Script/Voyage.VoyagePlayerState"), "Restoration base PlayerState is unavailable")
    assert(identity(state:GetOwner()) == identity(controller), "Restoration base PlayerState owner changed")
    local world, hud = controller:GetWorld(), observed_hud or controller:GetHUD()
    local world_id, controller_id, state_id = identity(world), identity(controller), identity(state)
    assert(identity(state:GetWorld()) == world_id, "Restoration base PlayerState/world changed")
    assert(is_a(hud, "/Script/Voyage.VoyageHUD"), "Restoration base HUD is unavailable")
    assert(identity(hud:GetWorld()) == world_id and identity(hud:GetOwningPlayerController()) == controller_id,
        "Restoration base HUD owner/world changed")
    local hud_id = identity(hud)
    return { controller = controller, world = world, hudId = hud_id, worldId = world_id,
        baseScopeId = world_id .. "|" .. controller_id .. "|" .. state_id .. "|" .. hud_id }
end
local function context(expected)
    assert(type(expected) == "string" and expected ~= "", "Missing expected save context")
    local controller = FindFirstOf("VoyagePlayerController")
    assert(is_a(controller, "/Script/Voyage.VoyagePlayerController"), "Current save controller is unavailable")
    assert(controller:IsLocalController() == true, "Save data requires the current local player")
    local pawn, state = controller.Pawn, controller.PlayerState
    assert(is_a(pawn, "/Script/Voyage.VoyageCharacter"), "Load a character and leave vehicle controls before using saved mod data")
    assert(is_a(state, "/Script/Voyage.VoyagePlayerState"), "Current player progress is unavailable")
    local controller_id = identity(controller)
    assert(identity(pawn:GetController()) == controller_id and identity(state:GetOwner()) == controller_id,
        "Save pawn/player progress owner changed")
    local world = controller:GetWorld()
    local world_id = identity(world)
    assert(identity(pawn:GetWorld()) == world_id and identity(state:GetWorld()) == world_id,
        "Save player/world changed")
    local token = world_id .. "|" .. controller_id .. "|" .. identity(pawn)
    assert(token == expected, "Save context changed")
    -- The library also returns false when there is no valid VoyageHUD. Establish
    -- that native precondition before interpreting false as a missing key.
    local hud = controller:GetHUD()
    assert(is_a(hud, "/Script/Voyage.VoyageHUD"), "Current native save HUD is unavailable")
    assert(identity(hud:GetWorld()) == world_id and identity(hud:GetOwningPlayerController()) == controller_id,
        "Save HUD owner/world changed")
    local hud_id, state_id = identity(hud), identity(state)
    return { controller = controller, world = world, contextToken = token,
        hudId = hud_id, scopeId = token .. "|" .. state_id .. "|" .. hud_id,
        baseScopeId = world_id .. "|" .. controller_id .. "|" .. state_id .. "|" .. hud_id }
end

local function same_scope(expected, original)
    local current = context(expected)
    assert(current.scopeId == original.scopeId, "Save HUD/player progress changed")
    return current
end

-- This is the game's account resolver, not a last-save/slot-name heuristic.
-- Only the researched Steam account shape is admitted by this build.
local function account(current)
    local player = current.controller.Player
    assert(is_a(player, "/Script/Engine.LocalPlayer"), "Current native local player is unavailable")
    assert(identity(player.PlayerController) == identity(current.controller), "Native account controller changed")
    local object = StaticFindObject("/Script/Voyage.Default__VoyageSaveGame")
    assert(is_a(object, "/Script/Voyage.VoyageSaveGame"), "Native save-account resolver is unavailable")
    local value = safe.text(object:GetSaveGameUserId(player))
    assert(type(value) == "string" and #value == 17 and value:match("^765%d+$"),
        "Native save account is not a supported Steam identity")
    local root = safe.text(object:GetSaveGameRootDirectory())
    assert(type(root) == "string" and #root > 0 and not root:find("%z"), "Native save root is unavailable")
    root = root:gsub("\\", "/"):gsub("/+$", "")
    -- Both absolute and game-relative native roots are allowed. The root is
    -- returned by the game; only the fixed basename and validated account ID
    -- are appended. No directory enumeration or account guessing is used.
    return value, root .. "/" .. value .. "/PaintManager.library.json"
end

function M.capture_context()
    local ok, result = pcall(function()
        local controller = FindFirstOf("VoyagePlayerController")
        assert(is_a(controller, "/Script/Voyage.VoyagePlayerController"), "Current save controller is unavailable")
        local token = identity(controller:GetWorld()) .. "|" .. identity(controller) .. "|" .. identity(controller.Pawn)
        local current = context(token)
        return { status = "ready", contextToken = token, scopeId = current.scopeId,
            hudId = current.hudId, worldId = identity(current.world), baseScopeId = current.baseScopeId }
    end)
    return ok and result or { status = "not_ready", error = tostring(result) }
end

-- Both sides of the native HUD copy are captured without requiring possession.
-- The base BeginPlay callback supplies its actual HUD; map-post supplies none
-- and independently verifies the controller's now-current HUD.
function M.capture_restored_context(observed_hud)
    local ok, result = pcall(function()
        local current = base_context(observed_hud)
        return { status = "ready", baseScopeId = current.baseScopeId,
            hudId = current.hudId, worldId = current.worldId }
    end)
    return ok and result or { status = "not_ready", error = tostring(result) }
end

function M.capture_restored_account(expected_base)
    local ok, result = pcall(function()
        local current = base_context()
        assert(current.baseScopeId == expected_base, "Restored base scope changed before account capture")
        local account_id, path = account(current)
        assert(base_context().baseScopeId == expected_base, "Restored base scope changed during account capture")
        local second_account, second_path = account(current)
        assert(second_account == account_id and second_path == path, "Native save account/root changed")
        assert(base_context().baseScopeId == expected_base, "Restored base scope changed after account capture")
        return { status = "ready", baseScopeId = expected_base, accountId = account_id, libraryPath = path }
    end)
    return ok and result or { status = "not_ready", error = tostring(result) }
end

-- Pawn presence is only a final runtime-context requirement. It never proves
-- restoration: the expected base was already witnessed around the native copy.
function M.attach_restored_context(expected_base)
    local ok, result = pcall(function()
        local current = base_context()
        assert(current.baseScopeId == expected_base, "Restored base scope changed before Pawn attachment")
        local pawn = current.controller.Pawn
        if not is_a(pawn, "/Script/Voyage.VoyageCharacter") then return { status = "waiting_pawn" } end
        local token = current.worldId .. "|" .. identity(current.controller) .. "|" .. identity(pawn)
        local attached = context(token)
        assert(attached.baseScopeId == expected_base, "Restored base scope changed during Pawn attachment")
        return { status = "ready", contextToken = token, scopeId = attached.scopeId,
            baseScopeId = expected_base, hudId = current.hudId, worldId = current.worldId }
    end)
    return ok and result or { status = "not_ready", error = tostring(result) }
end

-- Used only at the completed map boundary to bind its native save account.
-- No carrier or filesystem read is involved; wrappers remain within this call.
function M.capture_account(expected, expected_scope)
    local ok, result = pcall(function()
        local current = context(expected)
        assert(current.scopeId == expected_scope, "Restored native scope changed before account capture")
        local account_id, path = account(current)
        same_scope(expected, current)
        local second_account, second_path = account(current)
        assert(second_account == account_id and second_path == path, "Native save account/root changed")
        same_scope(expected, current)
        return { status = "ready", contextToken = expected, scopeId = current.scopeId,
            accountId = account_id, libraryPath = path }
    end)
    return ok and result or { status = "not_ready", error = tostring(result) }
end

function M.storage_context(expected)
    local ok, result = pcall(function()
        local current = context(expected)
        local lifecycle = require("paintmanager.save_lifecycle")
        local ready, generation = lifecycle.is_ready(current.contextToken, current.scopeId)
        assert(ready, "HUD restoration has not been observed at a verified map-load boundary")
        local account_id, path = account(current)
        same_scope(expected, current)
        local second_account, second_path = account(current)
        assert(second_account == account_id and second_path == path, "Native save account/root changed")
        same_scope(expected, current)
        local still_ready, current_generation = lifecycle.is_ready(current.contextToken, current.scopeId)
        assert(still_ready and current_generation == generation, "Native load generation changed")
        local admitted, admission_error = lifecycle.admit_account(current.contextToken, current.scopeId, account_id, path)
        assert(admitted, admission_error)
        return { status = "ready", contextToken = expected, scopeId = current.scopeId,
            accountId = account_id, libraryPath = path, loadGeneration = generation }
    end)
    return ok and result or { status = "not_ready", error = tostring(result) }
end

-- Read-only stock admission uses the already-proven restoration base. Vehicle
-- possession does not change this base and must not re-anchor/cancel the scan.
-- This function never admits HUD/library writes or fabricates restoration.
function M.stock_context(binding)
    local ok, result = pcall(function()
        assert(type(binding) == "table" and type(binding.baseScopeId) == "string", "Missing restored stock base")
        local lifecycle = require("paintmanager.save_lifecycle")
        local function admission()
            if type(lifecycle.is_stock_ready) == "function" then
                return lifecycle.is_stock_ready(binding.baseScopeId, binding.accountId, binding.libraryPath)
            end
            return lifecycle.is_ready(binding.contextToken, binding.scopeId)
        end
        local admitted, generation = admission()
        assert(admitted and generation == binding.loadGeneration, "Stock load generation was revoked")
        local current = base_context()
        assert(current.baseScopeId == binding.baseScopeId, "Stock world/controller/HUD base changed")
        local account_id, path = account(current)
        assert(account_id == binding.accountId and path == binding.libraryPath, "Stock account changed")
        assert(base_context().baseScopeId == binding.baseScopeId, "Stock base changed during account read")
        local second_account, second_path = account(current)
        assert(second_account == account_id and second_path == path, "Stock account/root changed during admission")
        assert(base_context().baseScopeId == binding.baseScopeId, "Stock base changed after account confirmation")
        local still_ready, latest = admission()
        assert(still_ready and latest == generation, "Stock load generation changed during admission")
        return { status = "ready", baseScopeId = current.baseScopeId, worldId = current.worldId,
            accountId = account_id, libraryPath = path, loadGeneration = generation }
    end)
    return ok and result or { status = "not_ready", error = tostring(result) }
end

function M.uncertainty(expected)
    return uncertain[expected]
end

function M.new_id(expected)
    local ok, result = pcall(function()
        local current = context(expected)
        local object = StaticFindObject("/Script/Engine.Default__KismetGuidLibrary")
        assert(is_a(object, "/Script/Engine.KismetGuidLibrary"), "Native UUID service is unavailable")
        local value = safe.text(object:Conv_GuidToString(object:NewGuid()))
        assert(type(value) == "string", "Native UUID result is unavailable")
        local digits = value:gsub("%-", ""):lower()
        assert(#digits == 32 and digits:match("^[0-9a-f]+$") and digits ~= string.rep("0", 32), "Native UUID is invalid")
        same_scope(expected, current)
        return digits:sub(1,8) .. "-" .. digits:sub(9,12) .. "-" .. digits:sub(13,16) .. "-" .. digits:sub(17,20) .. "-" .. digits:sub(21,32)
    end)
    if ok then return result end
    return nil, tostring(result)
end
local function library()
    local object = StaticFindObject(LIBRARY)
    assert(is_a(object, LIBRARY_CLASS), "Native HUD persistence library is unavailable")
    return object
end
local function key(name)
    -- The pinned loader publishes a callable FName object, not a plain Lua
    -- function. Invocation below is protected by read/write's outer pcall;
    -- accepting its binding type never bypasses the exact fixed-name check.
    assert(FName ~= nil and type(EFindName) == "table" and EFindName.FNAME_Add ~= nil,
        "Fixed save key construction is unavailable")
    local value = FName(name, EFindName.FNAME_Add)
    assert(safe.text(value) == name, "Fixed save key construction failed")
    return value
end
local function limit(maximum)
    if maximum == nil then maximum = MAX_BYTES end
    assert(type(maximum) == "number" and maximum % 1 == 0 and maximum > 0, "Invalid save payload limit")
    return math.min(maximum, MAX_BYTES)
end
local function payload(value, maximum)
    assert(type(value) == "string" and #value <= maximum and not value:find("\0", 1, true) and utf8.len(value),
        "Save payload must be bounded UTF-8 text")
    return value
end
local function read_native(object, controller, name, maximum)
    local output = {}
    -- Pinned LuaUObject.cpp reuses the struct output table and writes fields
    -- directly. FString remains a wrapper; copy its text before returning.
    local found = object:GetPersistentHudData(controller, name, output)
    assert(type(found) == "boolean", "Unexpected native save lookup result")
    if not found then return { status = "missing" } end
    assert(output.bIsString == true, "Save key contains a non-string native value")
    local value = output.StringValue
    local kind = type(value)
    assert(kind == "string" or kind == "table" or kind == "userdata", "Native save string is unavailable")
    value = kind == "string" and value or value:ToString()
    return { status = "present", raw = payload(value, maximum) }
end

-- For an already bound transfer journal, sample the native account around
-- the carrier call, sharing its scope checks. All handles stay in this call.
local function transfer_account(binding, current)
    local lifecycle = require("paintmanager.save_lifecycle")
    local ready, generation = lifecycle.is_ready(current.contextToken, current.scopeId)
    assert(ready and generation == binding.loadGeneration and current.scopeId == binding.scopeId,
        "Transfer origin save scope was revoked")
    assert(safe.valid(current.controller), "Transfer origin controller changed")
    local id, path = account(current)
    local admitted, reason = lifecycle.admit_account(current.contextToken, current.scopeId, id, path)
    assert(admitted and id == binding.accountId and path == binding.libraryPath, reason or "Transfer origin save account changed")
end

local function read_key(expected, maximum, name, binding)
    local ok, result = pcall(function()
        maximum = limit(maximum)
        local current = context(expected)
        if binding then transfer_account(binding, current) end
        local result = read_native(library(), current.controller, key(name), maximum)
        if binding then transfer_account(binding, same_scope(expected, current)) end
        same_scope(expected, current)
        result.scopeId = current.scopeId
        return result
    end)
    return ok and result or { status = "error", error = tostring(result) }
end

local function write_key(expected, previous, value, expected_scope, key_name, guards, guard_id, binding)
    if guards[guard_id] then return { status = "uncertain", error = guards[guard_id] } end
    local attempted = false
    local ok, result = pcall(function()
        value = payload(value, MAX_BYTES)
        assert(#value > 0, "Cannot stage an empty save document")
        if previous ~= nil then payload(previous, MAX_BYTES) end
        assert(expected_scope == nil or type(expected_scope) == "string", "Invalid expected save scope")
        local current = context(expected)
        if expected_scope and current.scopeId ~= expected_scope then
            return { status = "conflict", error = "Save HUD/player progress changed since loading" }
        end
        local function verify_scope()
            local live = same_scope(expected, current)
            if binding then
                local ready, active = require("paintmanager.save_lifecycle").is_ready(expected, current.scopeId)
                assert(ready and active == binding.loadGeneration, "Transfer origin save scope was revoked")
            end
            return live
        end
        if binding then transfer_account(binding, current) end
        local object, name = library(), key(key_name)
        local existing = read_native(object, current.controller, name, MAX_BYTES)
        if binding then transfer_account(binding, verify_scope()) end
        verify_scope()
        if existing.raw ~= previous then return { status = "conflict", error = "Saved mod data changed since loading" } end
        -- Guard before ProcessEvent: HUD notifications can reenter Lua. Never
        -- automatically retry an inconclusive native attempt in this context.
        attempted = true
        guards[guard_id] = "A native save-carrier attempt is pending or uncertain; reload the game before another write"
        local accepted = object:SetPersistentHudData(current.controller, name,
            { IntValue = 0, FloatValue = 0, StringValue = value, bIsString = true })
        assert(accepted == true, "Native save-carrier setter did not return true")
        verify_scope()
        local actual = read_native(object, current.controller, name, MAX_BYTES)
        if binding then transfer_account(binding, current) end
        verify_scope()
        assert(actual.status == "present" and actual.raw == value, "Native save-carrier readback differs")
        guards[guard_id] = nil
        return { status = "staged", scopeId = current.scopeId }
    end)
    if ok then return result end
    if attempted then guards[guard_id] = tostring(result) end
    return { status = attempted and "uncertain" or "error", error = tostring(result) }
end

function M.read(expected, maximum)
    return read_key(expected, maximum, KEY)
end

function M.write(expected, previous, value, expected_scope)
    return write_key(expected, previous, value, expected_scope, KEY, uncertain, expected)
end

-- Transfer origins belong to the loaded game save. The account library's
-- newest-wins merge would import box/slot associations from unrelated saves.
function M.read_transfer_origins(expected, binding)
    return read_key(expected, MAX_BYTES, ORIGINS_KEY, binding)
end

function M.write_transfer_origins(binding, previous, value)
    local guard_id = binding.scopeId .. "|" .. tostring(binding.loadGeneration)
    return write_key(binding.contextToken, previous, value, binding.scopeId,
        ORIGINS_KEY, origins_uncertain, guard_id, binding)
end
return M
