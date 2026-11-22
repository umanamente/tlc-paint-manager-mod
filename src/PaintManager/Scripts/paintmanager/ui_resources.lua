-- Original control textures have a separate viewport-owned lifetime. Window
-- close never destroys this cache; native map transitions retire its host.
local safe = require("paintmanager.safe")
local icons_module = require("paintmanager.ui_icons")
local processing = require("paintmanager.processing")
local M = {}; local Resources = {}; Resources.__index = Resources
local HOST_PACKAGE = "/Game/UI/Game/HUD/BP_VoyageIngameEmpty"
local HOST_ASSET = "BP_VoyageIngameEmpty_C"
local HOST_PATH = HOST_PACKAGE .. "." .. HOST_ASSET

local function path_only(value)
    return type(value) == "string" and (value:match("^[^ ]+ (/.+)$") or value) or nil
end

local function current_context()
    local ok, result = pcall(function()
        local pc = FindFirstOf("VoyagePlayerController")
        if not safe.valid(pc) or not pc:IsLocalController() then return nil end
        local world = pc:GetWorld()
        if not safe.valid(world) then return nil end
        -- Decorative resources require no Pawn, inventory or restored account.
        return { pc = pc, key = tostring(pc:GetAddress()) .. ":" .. tostring(world:GetAddress()) }
    end)
    return ok and result or nil
end

local function host_class()
    local class = StaticFindObject(HOST_PATH)
    if not safe.valid(class) then
        local registry = StaticFindObject("/Script/AssetRegistry.Default__AssetRegistryHelpers")
        if not safe.valid(registry) then error("ui_icon_registry_unavailable") end
        class = registry:GetAsset({ PackageName = FName(HOST_PACKAGE, EFindName.FNAME_Add),
            AssetName = FName(HOST_ASSET, EFindName.FNAME_Add) })
    end
    if not safe.valid(class) or not class:IsA("/Script/CoreUObject.Class")
        or path_only(class:GetFullName()) ~= HOST_PATH then error("ui_icon_host_class_unavailable") end
    local parent = class
    for _ = 1, 16 do
        if path_only(parent:GetFullName()) == "/Script/UMG.UserWidget" then return class end
        parent = parent:GetSuperStruct()
        if not safe.valid(parent) then break end
    end
    error("ui_icon_host_not_userwidget")
end

function M.new(config, callbacks)
    config = config or {}
    return setmetatable({ config = config, callbacks = callbacks or {}, serial = 0,
        scheduler_ms = 0, retry_at = 0, suspended = false }, Resources)
end

function Resources:_warn(code, detail)
    local key = code .. ":" .. tostring(detail)
    if self.last_error == key then return end
    self.last_error = key
    if type(self.callbacks.log) == "function" then pcall(self.callbacks.log, code .. ": " .. tostring(detail)) end
end

function Resources:_cleanup(context)
    local host, key = self.cleanup_host, self.cleanup_key
    if not host then return true end
    -- A changed world/controller invalidates the old wrapper graph. Native map
    -- cleanup owns its viewport removal; never dereference it in a new scope.
    -- A temporary missing controller does not prove a new world. Retain the
    -- cleanup claim without touching it until an actual scope can be checked.
    if not context then return false end
    if context.key ~= key then
        self.cleanup_host, self.cleanup_key = nil, nil
        return true
    end
    local ok, err = pcall(function()
        if safe.valid(host) then
            host:SetVisibility(1) -- Collapsed; no focus or input claim.
            host:RemoveFromParent()
            assert(not host:IsInViewport(), "ui_icon_host_not_removed")
        end
    end)
    if ok then self.cleanup_host, self.cleanup_key = nil, nil
    else self:_warn("ui_icon_cleanup_failed", err) end
    return ok
end

function Resources:_retire(context)
    if self.deposit then
        if not context or context.key ~= self.deposit.key or self.suspended then self.deposit = nil
        else self.deposit.settled = false end
    end
    if self.icons then pcall(function() self.icons:close() end) end
    if self.host then
        self.cleanup_host, self.cleanup_key = self.host, self.scope_key
    end
    self.icons, self.host, self.tree, self.root, self.scope_key = nil, nil, nil, nil, nil
    return self:_cleanup(context)
end

function Resources:invalidate(suspend)
    self.suspended = suspend == true
    self.retry_at = 0
    self:_retire(current_context())
end

function Resources:resume()
    self.suspended, self.retry_at = false, 0
end

function Resources:_usable(context)
    if not context or context.key ~= self.scope_key or not self.icons then return false end
    return safe.valid(self.host) and safe.valid(self.tree) and safe.valid(self.root)
        and self.host:IsInViewport() and self.tree.RootWidget:GetAddress() == self.root:GetAddress()
end

function Resources:get()
    if self.suspended then return nil end
    local context = current_context()
    local ok, usable = pcall(function() return self:_usable(context) end)
    if ok and usable then return self.icons end
    if self.host then self:_retire(context); self.retry_at = 0 end
    return nil
end

function Resources:set_demand(demanded)
    self.demanded = demanded == true
end

function Resources:bind_deposit_icon(image)
    local context = current_context()
    if not context then return nil end
    local request = { image = image, key = context.key }
    self.deposit, self.retry_at = request, 0
    return function()
        if self.deposit == request then self.deposit = nil end
        request.image = nil
    end
end

function Resources:processing_busy()
    return self.deposit ~= nil and not self.deposit.settled or self.demanded
        and (not self.icons or type(self.icons.has_demand) == "function" and self.icons:has_demand()) or false
end

function Resources:_start(context)
    local input = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
    if not safe.valid(input) then error("ui_icon_blueprint_library_unavailable") end
    self.scope_key = context.key
    self.host = input:Create(context.pc, host_class(), context.pc)
    assert(safe.valid(self.host), "ui_icon_host_creation_failed")
    self.tree = self.host.WidgetTree
    assert(safe.valid(self.tree) and self.tree:GetOuter():GetAddress() == self.host:GetAddress(),
        "ui_icon_tree_ownership_failed")
    self.root = self.tree.RootWidget
    assert(safe.valid(self.root) and self.root:IsA("/Script/UMG.CanvasPanel")
        and self.root:GetOuter():GetAddress() == self.tree:GetAddress(), "ui_icon_root_ownership_failed")
    local tree = self.tree
    local function make(name)
        assert(safe.valid(tree), "ui_icon_tree_unavailable")
        local class = StaticFindObject("/Script/UMG." .. name)
        assert(safe.valid(class), "ui_icon_widget_class_unavailable")
        self.serial = self.serial + 1
        local widget = StaticConstructObject(class, tree,
            FName("PaintManager_IconCache_" .. name .. "_" .. self.serial), EObjectFlags.RF_Transient)
        assert(safe.valid(widget), "ui_icon_widget_creation_failed")
        return widget
    end
    self.icons = icons_module.new(tree, self.config, make)
    self.root:AddChildToCanvas(self.icons:host())
    self.host:SetVisibility(1)
    -- A real UserWidget in the viewport owns its native WidgetTree/slot graph.
    -- Outer or retained Lua proxies alone are not GC ownership for the bank.
    self.host:AddToViewport(9799)
    assert(self.host:IsInViewport(), "ui_icon_host_not_in_viewport")
    if processing.get("automatic_preparation", true) and processing.get("preload_icons", true)
        and processing.get("texture_cache_enabled", true) then self.icons:preload_all() end
    self.last_error = nil
end

function Resources:tick(budget, demanded, scheduler_ms, opened)
    if self.suspended or budget and budget:expired() then return end
    if demanded ~= nil then self:set_demand(demanded) end
    local preload = processing.get("automatic_preparation", true) and processing.get("preload_icons", true)
        and processing.get("texture_cache_enabled", true)
    if not self.demanded and not self.deposit and not preload and not self.cleanup_host then
        if self.host and not processing.get("texture_cache_enabled", true) then self:_retire(current_context()) end
        return
    end
    local interval = self.config.pump_ms or 16
    self.scheduler_ms = scheduler_ms or self.scheduler_ms + interval
    local working = self.icons and (self.icons:has_work() or self.deposit and not self.deposit.settled)
    if self.scheduler_ms < self.retry_at and not working and not self.cleanup_host then return end
    local delay = processing.get("resource_retry_ms", 500)
    -- Preserve the closed loop's former skip-N-opportunities cadence. Open
    -- frames share an elapsed-time deadline, including any same-frame drains.
    if not opened then delay = (math.max(1, math.floor(delay / interval)) + 1) * interval end
    local retry_at = self.scheduler_ms + delay
    local context = current_context()
    if budget then budget:consume(1) end
    if not self:_cleanup(context) then return end
    if self.deposit and context and context.key ~= self.deposit.key then self.deposit = nil end
    if not self.demanded and not self.deposit and not preload then return end
    if self.host then
        local ok, usable = pcall(function() return self:_usable(context) end)
        if not ok or not usable then self:_retire(context) end
    end
    if not context then self.retry_at = retry_at; return end
    if budget and budget:expired() then return end
    if not self.host then
        local ok, err = pcall(function() self:_start(context) end)
        if budget then budget:consume(1) end
        if not ok then
            self:_warn("ui_icon_preload_failed", err)
            self:_retire(context)
            self.retry_at = retry_at
            return
        end
    end
    if budget and budget:expired() then return end
    local request = self.deposit
    if request and not request.settled then
        local ok, settled = pcall(self.icons.bind_deposit_icon, self.icons, request.image, budget)
        request.settled = not ok or settled
        if not ok then self:_warn("ui_deposit_icon_failed", settled) end
    end
    if preload and not self.icons.preloaded then self.icons:preload_all() end
    local ok, err = pcall(function() self.icons:pump(budget) end)
    if not ok then
        self:_warn("ui_icon_pump_failed", err)
        self:_retire(context)
    end
    self.retry_at = retry_at
end

return M
