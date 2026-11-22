-- Own one extra control in the open box. The original Deposit Similar action
-- can also queue paint through its named native mouse/keyboard handler.
local collection = require("paintmanager.collection")
local clock = require("paintmanager.clock")
local transfer = require("paintmanager.paint_transfer")
local safe = require("paintmanager.safe")
local messages = require("paintmanager.i18n")
local M = {}
M.__index = M

local MENU = "/Game/UI/Game/HUD/BP_VoyageContainerWidget.BP_VoyageContainerWidget_C"
local CONTAINER = "/Script/Voyage.VoyageContainerWidget:"
local BASE = "/Script/Voyage.VoyageBaseUserWidget:"
local OVERLAY = "/Script/Voyage.VoyageOverlayMenuWidget"

local function same(left, right)
    return safe.valid(left) and safe.valid(right) and left:GetAddress() == right:GetAddress()
end

local function standard_menu(widget)
    local class = safe.name(safe.class(widget))
    return class and class:match("^[^ ]+ (/.+)$") == MENU
end

-- Same lookup used by both stock container loot actions. The active menu may
-- be a child of another layout panel in the overlay.
local function focused_menu(controller)
    local overlay = controller:GetPlayerFocusWidget()
    if not safe.valid(overlay) or not overlay:IsA(OVERLAY) or not overlay:IsVisible() then return nil end
    local menu = overlay:GetCurrentChildWidget()
    if standard_menu(menu) and menu:IsVisible() and same(menu:GetWidgetOwnerController(), controller) then return menu end
end

local function read_endpoints(menu, context)
    assert(same(menu, focused_menu(context.controller)), "The focused container changed")
    local box = menu:GetSourceInventory()
    assert(safe.valid(box), "The focused container has no source inventory")
    local bag = assert(transfer.player_backpack(context.token))
    -- Filtered inventory panes can be unbound while opening/rebuilding. The
    -- stock action uses the controller backpack and container source directly.
    local bag_widget, box_widget = menu.PlayerInventoryWidget, menu.InventoryWidget
    local shown_bag = safe.valid(bag_widget) and bag_widget:GetSourceInventory()
    local shown_box = safe.valid(box_widget) and box_widget:GetSourceInventory()
    assert(not safe.valid(shown_bag) or same(shown_bag, bag),
        "The container's player inventory widget does not show the local backpack")
    assert(not safe.valid(shown_box) or same(shown_box, box),
        "The container's inventory widget does not show its source box")
    assert(not same(box, bag), "The container source resolves to the local backpack")
    assert(transfer.open_writable_box(box, context))
    return bag, box
end

function M:_binding(menu, context)
    context = context or assert(collection.native_context())
    local token = context.token
    assert(self.callbacks.guard(token), "Another action or the current player scope blocks paint transfers")
    local bag, box = read_endpoints(menu, context)
    local menu_ref = assert(collection.runtime_ref(menu))
    local bag_ref, box_ref = assert(collection.runtime_ref(bag)), assert(collection.runtime_ref(box))
    local function endpoints()
        assert(not self.closed and self.callbacks.guard(token), "Deposit is no longer active")
        local latest = assert(collection.native_context())
        assert(latest.token == token, "Deposit player changed")
        local widget = assert(collection.resolve_ref(menu_ref))
        local source, destination = read_endpoints(widget, latest)
        assert(same(source, collection.resolve_ref(bag_ref)) and same(destination, collection.resolve_ref(box_ref)),
            "The deposit inventory endpoints changed")
        return source, destination
    end
    return token, endpoints, bag_ref.id, box_ref.id
end

-- Relinquish temporary ownership before the game's own menu refresh computes
-- the new native value. Never reset a button that the stock UI enabled.
function M:_restore()
    local owned = self.owned
    if not owned then return end
    self.owned = nil
    local button = collection.resolve_ref(owned.button)
    if safe.valid(button) then button:SetIsEnabled(false) end
end

function M:_observe(menu)
    if self.closed or not standard_menu(menu) then return end
    local ref = collection.runtime_ref(menu)
    if not ref then return end
    if self.tracked and self.tracked.id ~= ref.id then
        local context = collection.native_context()
        if not context or not same(menu, focused_menu(context.controller)) then return end
        self:reset()
    end
    if not self.tracked and self.stock_hook then self.stock_frame = clock.frame_count() or false end
    self.tracked, self.wait = ref, 0
end

function M:active()
    return not self.closed and self.tracked ~= nil
end

function M:probe(pc)
    if self.closed or not (self.custom or self.stock_hook) or self.tracked or os.clock() < self.next_probe then return end
    self.next_probe = os.clock() + 0.25
    local found = focused_menu(pc)
    if found then
        self:_observe(found)
        self.visible = self.tracked ~= nil
    end
end

function M:_detach_paint()
    local control = self.paint
    self.paint = nil
    if control then
        if control.release_icon then pcall(control.release_icon) end
        local button = collection.resolve_ref(control.ref)
        if button then button:RemoveFromParent() end
    end
end

function M:_show_paint(menu, token, endpoints, bag_id, box_id, enabled)
    local tree, panel = menu.WidgetTree, menu.DepositAllPanel
    assert(safe.valid(tree) and same(tree:GetOuter(), menu) and safe.valid(panel)
        and panel:IsA("/Script/UMG.HorizontalBox") and same(panel:GetOuter(), tree),
        "The container DepositAllPanel layout changed")
    local control = self.paint
    local button = control and collection.resolve_ref(control.ref)
    if not button or not same(button:GetParent(), panel) or control.token ~= token
        or control.bag ~= bag_id or control.box ~= box_id then
        self:_detach_paint()
        local release_icon, refresh_language
        button, self.serial, release_icon, refresh_language = self.callbacks.make_button(tree, self.serial, "ui.transfer.deposit_all_paints", menu.DepositAllBtn)
        if not self.custom then
            if release_icon then release_icon() end
            return
        end
        control = { token = token, endpoints = endpoints, bag = bag_id, box = box_id, release_icon = release_icon,
            refresh_language = refresh_language, language_revision = messages.revision }
        self.paint = control
        control.ref = assert(collection.runtime_ref(button), "Could not identify the Deposit all paints button")
        local slot = panel:AddChild(button)
        assert(safe.valid(slot), "Could not attach Deposit all paints")
        slot:SetPadding({ Left = 4, Top = 0, Right = 0, Bottom = 0 })
        if self.callbacks.log then self.callbacks.log("Deposit all paints button attached to the open box") end
    end
    if button:GetIsEnabled() ~= enabled then button:SetIsEnabled(enabled) end
    if not enabled then control.pressed = false end
end

function M:_poll_paint()
    local control = self.paint
    if not control then return end
    local button = collection.resolve_ref(control.ref)
    if not button then
        self:_detach_paint()
        self.wait = 0
        return
    end
    if control.language_revision ~= messages.revision then
        if control.refresh_language then control.refresh_language() end
        control.language_revision = messages.revision
    end
    local pressed = button:GetIsEnabled() and button:IsPressed()
    local released = control.pressed and not pressed and button:GetIsEnabled() and button:IsHovered()
    control.pressed = pressed
    if released and self.paint == control and self.custom then
        -- These endpoints still refer to the exact menu/box displayed at press.
        -- The service revalidates them before taking a snapshot and each move.
        self.callbacks.activate(control.token, control.endpoints)
        self.wait = 0
    end
end

function M:_update_similar(menu, enabled)
    local button = menu.DepositSimilarBtn
    if self.owned and not same(button, collection.resolve_ref(self.owned.button)) then self:_restore() end
    if enabled and safe.valid(button) then
        if not button:GetIsEnabled() then
            self.owned = { button = assert(collection.runtime_ref(button)) }
            button:SetIsEnabled(true)
        end
    else self:_restore() end
end

function M:_update()
    local menu = collection.resolve_ref(self.tracked)
    local context = collection.native_context()
    if not menu or not context or not same(menu, focused_menu(context.controller)) then self:reset(); return end
    self.visible = true
    local bound, token, endpoints, bag_id, box_id = pcall(self._binding, self, menu, context)
    local ready, writable = false, false
    if bound then
        self.binding_error = nil
        ready, writable = self.callbacks.can_offer(token, endpoints)
    elseif token ~= self.binding_error then
        self.binding_error = token
        if self.callbacks.log then self.callbacks.log("Open box paint binding unavailable: " .. tostring(token)) end
    end
    if self.custom and ready then self:_show_paint(menu, token, endpoints, bag_id, box_id, writable ~= false)
    else self:_detach_paint() end
    if self.stock_hook and self.stock_frame == nil then
        pcall(self._update_similar, self, menu, ready and writable ~= false)
    end
end

function M:step(budget, elapsed_ms)
    if not self:active() or budget and budget:expired() then return end
    local ok, err = pcall(function()
        self.wait = self.wait - (elapsed_ms or self.config.pump_ms)
        -- Stock schedules its enabled-state calculation for the next world
        -- tick. Leave its button entirely native until that frame has ended.
        if self.stock_frame ~= nil and (self.stock_frame or self.wait <= 0) then
            local frame = clock.frame_count()
            if not frame then self.stock_frame = false
            elseif not self.stock_frame or frame < self.stock_frame then self.stock_frame = frame
            elseif frame >= self.stock_frame + 2 then self.stock_frame, self.wait = nil, 0 end
        end
        if self.wait <= 0 then
            self.wait = 250
            self:_update()
        elseif self.visible then
            -- Also release the foreground profile on the first frame after a
            -- missed stock close event, without rereading either inventory.
            local menu = collection.resolve_ref(self.tracked)
            if not menu or not same(menu, focused_menu(menu:GetWidgetOwnerController())) then
                self:reset()
                return
            end
        end
        self:_poll_paint()
    end)
    if not ok then
        self:reset()
        if err ~= self.last_failure and self.callbacks.log then
            self.last_failure = err
            self.callbacks.log("Open container paint button failed: " .. tostring(err))
        end
    end
    if budget then budget:consume(1) end
end

function M:_clear()
    self.visible = false
    self.press, self.tracked, self.binding_error, self.stock_frame = nil, nil, nil, nil
    pcall(self._detach_paint, self)
    pcall(self._restore, self)
end

function M:reset()
    self:_clear()
    self.callbacks.cancel()
end

function M:_unhook_stock()
    local hooks = self.stock_hook
    self.stock_hook, self.press, self.depth, self.stock_frame = nil, nil, 0, nil
    pcall(self._restore, self)
    for _, hook in ipairs(hooks or {}) do
        local ok, reason = pcall(UnregisterHook, hook.path, hook.pre, hook.post)
        if not ok and self.callbacks.log then self.callbacks.log("Could not unregister " .. hook.path .. ": " .. tostring(reason)) end
    end
end

function M:_hook_stock()
    local callbacks, hooks = self.callbacks, {}
    self.stock_hook = hooks
    -- Native UE4SS removes a registered pair at its next dispatch. A stale
    -- pair may never authorize a later OFF/ON cycle, even between pre/post.
    local function current(callback)
        return function(...)
            if self.stock_hook == hooks and not self.closed then callback(...) end
        end
    end
    local function hook(path, pre, post)
        local a, b = RegisterHook(path, current(pre), current(post))
        assert(type(a) == "number" and type(b) == "number", "Could not observe " .. path)
        hooks[#hooks+1] = { path = path, pre = a, post = b }
    end
    local function observe(context) pcall(self._observe, self, safe.unwrap(context)) end
    local function before_refresh(context)
        pcall(function()
            if self.tracked and same(safe.unwrap(context), collection.resolve_ref(self.tracked)) then
                self.stock_frame = clock.frame_count() or false
                self:_restore()
            end
        end)
    end
    local function before(context, widget)
        self.depth = self.depth + 1
        if self.closed or self.depth ~= 1 then return end
        self.press = nil
        pcall(function()
            local menu, button = safe.unwrap(context), safe.unwrap(widget)
            if not standard_menu(menu) or not same(menu.DepositSimilarBtn, button) or not button:GetIsEnabled() then return end
            local token, endpoints = self:_binding(menu)
            local snapshot = callbacks.prepare(token, endpoints)
            if snapshot and self.stock_hook == hooks then
                self.press = { snapshot = snapshot, endpoints = endpoints, menu = collection.runtime_ref(menu), button = collection.runtime_ref(button) }
            end
        end)
    end
    local function after(context, widget)
        if self.depth == 0 then return end
        self.depth = self.depth - 1
        if self.depth ~= 0 then return end
        local press = self.press
        self.press = nil
        if not self.closed and press then
            local ok, err = pcall(function()
                assert(same(safe.unwrap(context), collection.resolve_ref(press.menu))
                    and same(safe.unwrap(widget), collection.resolve_ref(press.button)), "The original container control changed")
                callbacks.submit(press.snapshot, press.endpoints)
            end)
            if not ok and callbacks.log then callbacks.log("Put similar paint extension could not start: " .. tostring(err)) end
        end
    end
    local ok, err = pcall(function()
        assert(type(RegisterHook) == "function" and type(UnregisterHook) == "function", "Native hook registration unavailable")
        hook(CONTAINER .. "OnDepositSimilarButtonClicked", before, after)
        hook(BASE .. "OnInteractiveSource", function() end, observe)
        hook(BASE .. "OnReleaseInteractiveSource", function(context)
            pcall(function()
                if self.tracked and same(safe.unwrap(context), collection.resolve_ref(self.tracked)) then self:reset() end
            end)
        end, function() end)
        hook(CONTAINER .. "OnInventoryChanged", before_refresh, observe)
        hook(CONTAINER .. "InventoryFiltersChanged", before_refresh, observe)
        hook(CONTAINER .. "PlayerInventoryFiltersChanged", before_refresh, observe)
    end)
    if not ok then
        self:_unhook_stock()
        if callbacks.log then callbacks.log("Container event integration limited: " .. tostring(err)) end
    end
end

function M:set_settings(settings)
    if self.closed then return end
    local stock = settings ~= nil and settings.depositSimilarAllColors == true
    local custom = settings ~= nil and settings.showDepositAllPaints == true
    if self.stock == stock and self.custom == custom then return end
    local stock_changed = stock ~= self.stock
    self.stock, self.custom, self.wait, self.next_probe = stock, custom, 0, 0
    if stock_changed then
        if stock then
            self:_hook_stock()
            if self.stock_hook and self.tracked then self.stock_frame = clock.frame_count() or false end
        else self:_unhook_stock() end
    end
    if not custom then pcall(self._detach_paint, self) end
    if not (custom or self.stock_hook) then self:_clear() end
end

function M:close()
    self.closed = true
    self:_unhook_stock()
    self:reset()
end

function M.install(config, callbacks)
    local self = setmetatable({ config = config, callbacks = callbacks, stock = false, custom = false,
        depth = 0, next_probe = 0, serial = 0 }, M)
    if callbacks.game_build() ~= config.reference_build then self.closed = true end
    return self
end

return M
