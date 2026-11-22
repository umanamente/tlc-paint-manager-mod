-- Entry point from the stock ship-paint overlay. Called only by the game-thread pump.
local safe = require("paintmanager.safe")
local messages = require("paintmanager.i18n")
local t = messages.text
local player_messages = require("paintmanager.player_messages")
local M = {}
M.__index = M

local DATA = "/Game/Data/UI/OverlayWidgets/DA_Widget_Paint_Customize.DA_Widget_Paint_Customize"
local EDITOR = "/Game/UI/Dialog/BP_Customize_Paint_Dialog_Widget.BP_Customize_Paint_Dialog_Widget_C"
local PLAYER_DATA = "/Game/Data/UI/OverlayWidgets/DA_Widget_Player.DA_Widget_Player"
local PLAYER_EDITOR = "/Game/UI/Game/HUD/BP_VoyageCentralWidget.BP_VoyageCentralWidget_C"
local OVERLAY = "/Game/UI/Game/HUD/BP_VoyageOverlayMenuWidget.BP_VoyageOverlayMenuWidget_C"
local IDLE_POLL_MS, HANDOFF_MS = 128, 2000

local function path(object)
    local name = safe.name(object)
    return name and name:match("^[^ ]+ (/.+)$")
end

local function same(left, right)
    return safe.valid(left) and safe.valid(right) and left:GetAddress() == right:GetAddress()
end

local function reference(object)
    return { path = assert(path(object)), address = object:GetAddress() }
end

local function resolve(ref)
    local object = StaticFindObject(ref.path)
    if safe.valid(object) and object:GetAddress() == ref.address then return object end
end

local function current_editor(overlay, pc, data)
    if path(safe.class(overlay)) ~= OVERLAY or not overlay:IsInViewport() or not overlay:IsVisible()
        or not same(overlay:GetOverlayWidgetData(), data) then return nil end
    local editor = overlay:GetCurrentChildWidget()
    local class = path(data) == PLAYER_DATA and PLAYER_EDITOR or EDITOR
    if path(safe.class(editor)) == class and editor:IsVisible()
        and same(editor:GetWidgetOwnerController(), pc) and same(editor:GetParent(), overlay.RootCanvasPanel) then
        return editor
    end
end

local function find_editor(pc, data)
    local found_overlay, found_editor
    for _, overlay in ipairs(FindAllOf("BP_VoyageOverlayMenuWidget_C") or {}) do
        local editor = current_editor(overlay, pc, data)
        if editor then
            if found_editor then return nil end
            found_overlay, found_editor = overlay, editor
        end
    end
    return found_overlay, found_editor
end

function M.new(config, callbacks)
    return setmetatable({ step_ms = config.pump_ms, elapsed = IDLE_POLL_MS, callbacks = callbacks,
        serial = 0 }, M)
end

local function page_visible(attached, editor)
    return not attached.character or same(editor.WidgetSwitcher:GetActiveWidget(), resolve(attached.page))
end

function M:is_ui_visible()
    return self.popup ~= nil or self.attached and self.attached.visible or self.pending and self.pending.visible or false
end

local function released(record, key, button)
    local before = record[key]
    record[key] = button:IsPressed()
    return before and not record[key] and button:IsHovered()
end

local function cancel_popup_job(popup, reason)
    local job = popup.job
    if not job then return end
    local ok, progress = pcall(job.cancel, job, reason)
    if not ok then progress = nil end
    if job.get_publication then progress = job:get_publication() or progress end
    if progress and (progress.localCommitted or progress.publicationPending or progress.localUncertain) then
        return progress
    end
end

function M:_close_popup()
    local popup = self.popup
    self.popup = nil
    if not popup then return end
    local progress = cancel_popup_job(popup, "Native paint editor closed")
    if safe.valid(popup.root) then popup.root:RemoveFromParent() end
    local editor = resolve(popup.editor)
    if editor and popup.editor_enabled then
        editor:SetIsEnabled(true)
        if editor:IsVisible() and self.attached then
            local button = resolve(self.attached.save_button)
            if button then button:SetKeyboardFocus() end
        end
    end
    if progress then self.callbacks.published(progress) end
end

function M:reset()
    local attached = self.attached
    self.attached, self.pending = nil, nil
    local closed, err = pcall(self._close_popup, self)
    if not closed then self.callbacks.log("Ship paint editor popup cleanup failed: " .. tostring(err)) end
    if attached then
        local ok, failure = pcall(function()
            local button = attached.button and resolve(attached.button)
            if button then button:RemoveFromParent() end
            local save = attached.save_button and resolve(attached.save_button)
            if save then save:RemoveFromParent() end
        end)
        if not ok then self.callbacks.log("Ship paint editor button cleanup failed: " .. tostring(failure)) end
    end
end

function M:_attach(pc, data)
    local overlay, editor = find_editor(pc, data)
    if not editor then return end
    local tree = editor.WidgetTree
    assert(safe.valid(tree) and same(tree:GetOuter(), editor),
        "Ship paint editor widget tree unavailable")
    local character = path(data) == PLAYER_DATA
    local attached = { overlay = reference(overlay), controller = reference(pc),
        editor_address = editor:GetAddress(), character = character, language_revision = messages.revision }
    self.attached = attached
    local panel
    if character then
        -- In the stock character inventory the paint bar is the fourth group
        -- in the equipment WrapBox. Its save control follows the same page.
        local bar = editor.BP_PaintBarWidget
        local box = safe.valid(bar) and bar:GetParent()
        panel = safe.valid(box) and box:GetParent()
        assert(path(safe.class(box)) == "/Script/UMG.SizeBox" and path(safe.class(panel)) == "/Script/UMG.WrapBox"
            and safe.short_name(panel) == "WrapBox_0" and same(panel:GetOuter(), tree),
            "Character paint bar layout changed")
        local column = panel:GetParent()
        local page = safe.valid(column) and column:GetParent()
        assert(safe.short_name(page) == "InventorySB" and same(page:GetParent(), editor.WidgetSwitcher),
            "Character inventory page changed")
        attached.page = reference(page)
    else
        local close = editor.ConfirmBtn
        panel = safe.valid(close) and close:GetParent()
        assert(path(safe.class(panel)) == "/Script/UMG.VerticalBox" and safe.short_name(panel) == "VerticalBox_2"
            and same(panel:GetOuter(), tree), "Ship paint editor Close button layout changed")
        local button
        button, self.serial, attached.refresh_button = self.callbacks.make_button(tree, self.serial)
        attached.button = reference(button)
        local slot = panel:AddChild(button)
        assert(safe.valid(slot), "Ship paint editor button was not attached")
        slot:SetPadding({ Left = 0, Top = 4, Right = 0, Bottom = 0 })
    end
    local save
    save, self.serial, attached.refresh_save = self.callbacks.make_button(tree, self.serial, "ui.editor.save_preset")
    self.attached.save_button = reference(save)
    local save_slot = panel:AddChild(save)
    assert(safe.valid(save_slot), "Ship paint editor Save Preset button was not attached")
    save_slot:SetPadding({ Left = 0, Top = 4, Right = 0, Bottom = 0 })
    attached.visible = page_visible(attached, editor)
    self.callbacks.log(character and "Character inventory paint preset button attached" or "Ship paint editor button attached")
end

function M:_open_popup(editor, overlay)
    local popup
    popup, self.serial = self.callbacks.make_popup(editor.WidgetTree, self.serial)
    popup.editor, popup.phase, popup.character = reference(editor), "inspect", self.attached.character
    self.popup = popup
    local slot = overlay.RootCanvasPanel:AddChildToCanvas(popup.root)
    slot:SetAnchors({ Minimum = { X = 0, Y = 0 }, Maximum = { X = 1, Y = 1 } })
    slot:SetPosition({ X = 0, Y = 0 })
    slot:SetSize({ X = 0, Y = 0 })
    slot:SetZOrder(100)
    popup.editor_enabled = editor:GetIsEnabled()
    editor:SetIsEnabled(false)
    popup.cancel:SetKeyboardFocus()
    popup.token = self.callbacks.context()
    popup.target = self.callbacks.target(editor.BP_PaintBarWidget)
end

function M:_check_source(popup)
    local editor = resolve(popup.editor)
    local target = editor and self.callbacks.target(editor.BP_PaintBarWidget)
    if not target or target.kind ~= (popup.character and "character" or "boat") then return false end
    if popup.character and (not self.attached
        or not same(editor.WidgetSwitcher:GetActiveWidget(), resolve(self.attached.page))) then return false end
    return popup.token and self.callbacks.context() == popup.token and popup.target
        and target.id == popup.target.id and target.ownerRuntimeRef.id == popup.target.ownerRuntimeRef.id
end

local function set_notice(popup, render)
    popup.render_notice = type(render) == "function" and render or nil
    popup.notice:SetText(FText(popup.render_notice and popup.render_notice() or render))
end

local function finish_popup(popup, message, button_key)
    popup.phase, popup.job, popup.layout = "finished", nil, nil
    popup.name_fields:SetVisibility(1)
    popup.confirm:SetVisibility(1)
    popup.cancel:SetIsEnabled(true)
    popup.cancel_key = button_key or "ui.button.close"
    popup.cancel_label:SetText(FText(t(popup.cancel_key)))
    set_notice(popup, message)
    popup.cancel:SetKeyboardFocus()
end

local function show_duplicate(popup, name)
    finish_popup(popup, function() return t("ui.editor.duplicate", { name = name }) end, "ui.button.ok")
end

function M:_popup_input(popup)
    if released(popup, "cancel_pressed", popup.cancel) then self:_close_popup(); return end
    if popup.phase ~= "editing" or not released(popup, "save_pressed", popup.confirm) then return end
    local name = safe.text(popup.entry:GetText())
    if type(name) ~= "string" or name:match("^%s*$") or #name > 128 or name:find("[%z\1-\31\127]") then
        set_notice(popup, function() return t("main.preset.name_invalid") end)
        popup.entry:SetKeyboardFocus()
        return
    end
    popup.name, popup.phase = name, "save"
    popup.entry:SetIsEnabled(false)
    popup.confirm:SetIsEnabled(false)
    popup.cancel:SetIsEnabled(false)
    set_notice(popup, function() return t("ui.preset.saving", { name = name }) end)
end

function M:_inspect_popup(popup, budget)
    local library, snapshot, err, index = self.callbacks.library(popup.token, not popup.library_requested)
    popup.library_requested = true
    assert(not err, err)
    if not popup.layout then
        popup.job = popup.job or self.callbacks.capture(popup.target, popup.token)
        local progress = popup.job:step(budget)
        if progress.state == "running" then return library ~= nil end
        assert(progress.state == "done", progress.error or t("main.error.job_failed"))
        local view = progress.view
        local target = view and view.targets[1]
        assert(view and view.contextToken == popup.token and target and target.id == popup.target.id,
            t("ui.editor.source_changed"))
        assert(target.complete, t("main.preset.target_incomplete"))
        popup.job, popup.layout = nil, target
    end
    if not library then
        if not popup.waiting_library then
            set_notice(popup, function() return t("ui.editor.loading") end)
            popup.waiting_library = true
        end
        -- Give the shared reconciliation/index job this pump's remaining budget.
        return false
    end
    local match, why = index:match(popup.layout)
    assert(not why, t("main.preset.target_incomplete"))
    if popup.snapshot then
        local old = popup.snapshot
        assert(snapshot and snapshot.accountId == old.accountId and snapshot.scopeId == old.scopeId
            and snapshot.path == old.path and snapshot.loadGeneration == old.loadGeneration, t("ui.editor.source_changed"))
    end
    if match then
        show_duplicate(popup, match.name)
    else
        popup.snapshot = assert(snapshot, "Validated preset library snapshot unavailable")
        popup.phase, popup.layout = "editing", nil
        popup.entry:SetText(FText(popup.name or index:suggest_name()))
        set_notice(popup, popup.save_error or "")
        popup.entry:SetIsEnabled(true)
        popup.confirm:SetIsEnabled(true)
        popup.name_fields:SetVisibility(0)
        popup.confirm:SetVisibility(0)
        popup.entry:SetKeyboardFocus()
    end
    return true
end

function M:_save_popup(popup, budget)
    if not popup.job then
        local guard = function()
            if not self:_check_source(popup) then return false, t("ui.editor.source_changed") end
            if popup.character then return true end
            local ship, err = self.callbacks.editor_ship(popup.ship)
            if not ship then return false, err end
            popup.ship = ship
            return ship.shipRef.id == popup.target.ownerRuntimeRef.id, t("ui.editor.source_changed")
        end
        assert(guard())
        if not popup.character then budget:consume(1) end
        popup.job = self.callbacks.save(popup.target, popup.name, popup.token, popup.snapshot, guard)
    end
    local progress = popup.job:step(budget)
    if progress.state == "running" then return end
    local published = progress.publication or {}
    if progress.state == "error" and progress.phase == "preset_store" and not (published.localCommitted
        or published.publicationPending or published.localUncertain or published.outcome == "local_uncertain") then
        -- A guarded write can reject a changed local file or HUD preview. Reconcile
        -- both sources and capture the target again; another write needs a new click.
        self.callbacks.published(published)
        popup.job, popup.library_requested, popup.waiting_library = nil, nil, nil
        local reason_key = player_messages.reason_key(progress.error, "main.reason.storage")
        if progress.error then self.callbacks.log("Ship paint editor save failed: " .. tostring(progress.error)) end
        popup.phase, popup.save_error = "inspect", function() return t(reason_key) end
        popup.name_fields:SetVisibility(1)
        popup.confirm:SetVisibility(1)
        popup.cancel:SetIsEnabled(true)
        popup.cancel:SetKeyboardFocus()
        set_notice(popup, function() return t("ui.editor.checking") end)
        return
    end
    assert(progress.state == "done", progress.error or t("main.error.job_failed"))
    local library = progress.view and progress.view.library
    self.callbacks.published(published, library)
    if published.localUncertain or published.outcome == "local_uncertain" then
        finish_popup(popup, function() return self.callbacks.publication(published) end)
        return
    end
    local saved
    for _, record in ipairs(library.presets) do
        if record.id == progress.savedId then saved = record; break end
    end
    assert(saved, "Saved preset unavailable in the committed library")
    if progress.saveSkipped then show_duplicate(popup, saved.name); return end
    local name = saved.name
    finish_popup(popup, function()
        local message = t("ui.editor.saved", { name = name })
        if published.mirrorPending then message = message .. " " .. self.callbacks.publication(published) end
        return message
    end)
end

function M:work(budget)
    local popup = self.popup
    if not popup or (popup.phase ~= "inspect" and popup.phase ~= "save") then return false end
    if budget:expired() then return true end
    local ok, handled = pcall(function()
        assert(self:_check_source(popup), t("ui.editor.source_changed"))
        if popup.phase == "inspect" then return self:_inspect_popup(popup, budget) end
        self:_save_popup(popup, budget)
        return true
    end)
    if not ok then
        self.callbacks.log("Ship paint editor preset failed: " .. tostring(handled))
        local reason_key = player_messages.reason_key(handled)
        local published = cancel_popup_job(popup, tostring(handled))
        local notice = function() return t(reason_key) end
        if published then
            self.callbacks.published(published)
            notice = function() return self.callbacks.publication(published) end
        elseif popup.phase == "save" and popup.job then
            self.callbacks.published({})
        end
        finish_popup(popup, notice)
        return true
    end
    return handled
end

function M:_handoff(pc, visible, elapsed_ms)
    local pending = self.pending
    pending.visible = visible == true
    local token = self.callbacks.context()
    if token ~= pending.token then
        self.pending = nil
        self.callbacks.log("Ship paint editor handoff cancelled: player context changed")
        return
    end
    if visible == false and pc.bShowMouseCursor == false and pc:HasActiveDialog() == false then
        local target = pending.camera and pc:GetViewTarget() or nil
        local old_overlay = resolve(pending.overlay)
        local offscreen = not old_overlay or not old_overlay:IsInViewport() or not old_overlay:IsVisible()
        if offscreen and (not pending.camera or safe.valid(target) and target:GetAddress() ~= pending.camera) then
            self.pending = nil
            local accepted, reason = self.callbacks.open()
            self.callbacks.log(accepted and "Ship paint editor handed off to PaintManager"
                or "Ship paint editor handoff rejected: " .. tostring(reason))
            return
        end
    end
    pending.remaining = pending.remaining - elapsed_ms
    if pending.remaining <= 0 then
        self.pending = nil
        self.callbacks.log("Ship paint editor handoff timed out waiting for native input and camera")
    end
end

function M:_tick(elapsed_ms)
    local data = StaticFindObject(DATA)
    local tracked = self.attached or self.pending
    local pc
    if tracked then pc = resolve(tracked.controller)
    else pc = FindFirstOf("VoyagePlayerController") end
    if not safe.valid(pc) or not pc:IsLocalController() then self:reset(); return end
    if self.callbacks.observe_container then pcall(self.callbacks.observe_container, pc) end
    local visible = safe.valid(data) and pc:IsOverlayVisible(data)
    if self.pending then self:_handoff(pc, visible, elapsed_ms); return end
    if visible ~= true then
        data = StaticFindObject(PLAYER_DATA)
        if not safe.valid(data) or pc:IsOverlayVisible(data) ~= true then self:reset(); return end
    end

    local attached = self.attached
    if not attached then self:_attach(pc, data); return end
    local overlay = resolve(attached.overlay)
    local editor = current_editor(overlay, pc, data)
    if not safe.valid(editor) or editor:GetAddress() ~= attached.editor_address then self:reset(); return end
    if attached.language_revision ~= messages.revision then
        attached.language_revision = messages.revision
        if attached.refresh_button then attached.refresh_button() end
        if attached.refresh_save then attached.refresh_save() end
        local popup = self.popup
        if popup and popup.refresh_language then
            popup.refresh_language()
            if popup.render_notice then set_notice(popup, popup.render_notice)
            elseif popup.phase == "inspect" then set_notice(popup, function() return t("ui.editor.checking") end) end
            if popup.cancel_key then popup.cancel_label:SetText(FText(t(popup.cancel_key))) end
        end
    end
    attached.visible = page_visible(attached, editor)
    if self.popup then self:_popup_input(self.popup); return end
    if not attached.visible then attached.save_pressed = false; return end
    local button = attached.button and resolve(attached.button)
    local save = resolve(attached.save_button)
    if attached.button and not button or not save then self:reset(); return end
    -- The loader's multicast delegate binding can crash before widget attachment.
    -- Poll the same native pressed state as the ordinary mod window, then
    -- wait for a release over the button before closing the stock overlay.
    if released(attached, "save_pressed", save) then self:_open_popup(editor, overlay); return end
    if not button or not released(attached, "pressed", button) then return end
    local token = self.callbacks.context()
    if not token then self:reset(); return end
    local target = pc:GetViewTarget()
    local camera = safe.get(editor.BP_PaintBarWidget, "PreviewCamera")
    self:reset()
    self.pending = { token = token, remaining = HANDOFF_MS, visible = true, overlay = attached.overlay, controller = attached.controller,
        camera = same(target, camera) and camera:GetAddress() or nil }
    editor:RequestOverlayRemoval()
    self.callbacks.log("Ship paint editor native close requested")
end

function M:tick(elapsed_ms)
    elapsed_ms = elapsed_ms or self.step_ms
    self.elapsed = self.elapsed + elapsed_ms
    if not self.attached and not self.pending and self.elapsed < IDLE_POLL_MS then return end
    self.elapsed = 0
    local ok, failure = pcall(self._tick, self, elapsed_ms)
    if not ok then
        self:reset()
        if failure ~= self.last_failure then
            self.last_failure = failure
            self.callbacks.log("Ship paint editor integration failed: " .. tostring(failure))
        end
    end
end

return M
