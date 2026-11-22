"""Exercise the cockpit entry and handoff with separate native UI/input states."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime
from test_main_ui import FIXTURE as MAIN_FIXTURE
from test_preset_main import PRESET_MAIN_FIXTURE


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
local ui = require("paintmanager.ui")
local byPath, nextAddress = {}, 0
local dataPath = "/Game/Data/UI/OverlayWidgets/DA_Widget_Paint_Customize.DA_Widget_Paint_Customize"
local editorClass = "/Game/UI/Dialog/BP_Customize_Paint_Dialog_Widget.BP_Customize_Paint_Dialog_Widget_C"
local overlayClass = "/Game/UI/Game/HUD/BP_VoyageOverlayMenuWidget.BP_VoyageOverlayMenuWidget_C"
local function object(path, class, outer)
    nextAddress = nextAddress + 1
    local address = nextAddress
    local o = { Font = {}, WidgetStyle = { TextStyle = { Font = {} } }, children = {} }
    function o:IsValid() return true end
    function o:GetAddress() return address end
    function o:GetFullName() return "Object " .. path end
    function o:GetFName() return { ToString = function() return path:match("[^.:]+$") end } end
    function o:GetClass() return { IsValid = self.IsValid, GetFullName = function() return "Class " .. class end } end
    function o:GetOuter() return outer end
    function o:GetParent() return self.parent end
    function o:IsVisible() return self.visibility ~= 1 end
    function o:SetVisibility(value) self.visibility = value end
    function o:GetChildrenCount() return #self.children end
    function o:AddChild(child)
        child.parent = self
        table.insert(self.children, child)
        child.Slot = { IsValid = self.IsValid, SetPadding = function() end,
            SetHorizontalAlignment = function() end, SetVerticalAlignment = function() end,
            SetSize = function() end, SetPosition = function() end, SetAnchors = function() end, SetZOrder = function() end }
        return child.Slot
    end
    o.AddChildToCanvas = o.AddChild
    function o:RemoveFromParent()
        if not self.parent then return end
        for i, child in ipairs(self.parent.children) do
            if child == self then table.remove(self.parent.children, i); break end
        end
        self.parent = nil
    end
    function o:SetContent(child) self:AddChild(child) end
    function o:SetText(value) self.text = value end
    function o:GetText() return self.text end
    function o:GetIsEnabled() return self.enabled ~= false end
    function o:SetIsEnabled(enabled) self.enabled = enabled end
    function o:SetKeyboardFocus() focus = self end
    for _, method in ipairs({ "SetAutoWrapText", "SetFont", "SetColorAndOpacity", "SetTextOverflowPolicy",
        "SetClipping", "SetJustification", "SetBackgroundColor", "SetHintText", "SetPadding",
        "SetBrushColor", "SetWidthOverride", "SetHeightOverride" }) do o[method] = function() end end
    function o:IsPressed() return self.pressed == true end
    function o:IsHovered() return self.hovered == true end
    byPath[path] = o
    return o
end

local data, camera = object(dataPath), object("/PreviewCamera")
local gameCamera = object("/GameCamera")
local pc = object("/Player")
pc.bShowMouseCursor = true
function pc:IsLocalController() return true end
function pc:IsOverlayVisible(asset) return asset == data and nativeVisible == true end
function pc:GetViewTarget() return nextCamera end
function pc:HasActiveDialog() return dialogOpen == true end

local overlay = object("/Overlay", overlayClass)
function overlay:GetCurrentChildWidget() return self.child end
function overlay:GetOverlayWidgetData() return nativeData end
function overlay:IsInViewport() return inViewport == true end
editor = object("/Overlay:Editor", editorClass)
local tree = object("/Overlay:Editor.WidgetTree", nil, editor)
overlay.child = editor
overlay.RootCanvasPanel = object("/Overlay:Root")
overlay.RootCanvasPanel:AddChild(editor)
editor.WidgetTree = tree
editor.ConfirmBtn = object("/Overlay:Editor.WidgetTree.Close", nil, tree)
editor.BP_PaintBarWidget = { PreviewCamera = camera }
function editor:GetWidgetOwnerController() return pc end
function editor:RequestOverlayRemoval() closes = closes + 1 end
panel = object("/Overlay:Editor.WidgetTree.VerticalBox_2", "/Script/UMG.VerticalBox", tree)
panel:AddChild(editor.ConfirmBtn)

FName = function(s) return s end
FText = function(s) return s end
EObjectFlags = { RF_Transient = 64 }
for _, name in ipairs({ "Button", "TextBlock", "EditableTextBox", "HorizontalBox", "VerticalBox", "SizeBox", "Border" }) do
    byPath["/Script/UMG." .. name] = object("/Script/UMG." .. name)
end
StaticFindObject = function(path) return byPath[path] end
StaticConstructObject = function(class, owner, name)
    return object(owner:GetFullName():match(" (/.+)$") .. "." .. name, class:GetFullName(), owner)
end
FindFirstOf = function() return pc end
FindAllOf = function() discoveries = discoveries + 1; return { overlay } end

nativeVisible, inViewport, nativeData, nextCamera = true, true, data, camera
worldToken, opens, closes, discoveries = "world-a", 0, 0, 0
logs = {}
function layout_for(kind, color)
    return { kind = kind, className = kind, paintSetName = "Paint" },
        { { materialSlotName = "Body", partName = "", paint = {assetType = "Item", assetName = color} } }
end
function record(id, name, kind, color)
    local target, zones = layout_for(kind, color)
    return {id = id, name = name, target = target, zones = zones}
end
library = { presets = { record("i1", "PRESET 1", "boat", "Red"), record("i3", "Preset 3", "boat", "Gold") } }
function prepare_index()
    local job = require("paintmanager.preset_index").begin(library)
    while job:step().state == "running" do end
    index = assert(job:get_result())
end
prepare_index()
current_color = "Blue"
bound_target = { id = "hull", kind = "boat", ownerRuntimeRef = { id = "barge" } }
snapshot = {}
saves = 0
bridge = require("paintmanager.native_editor").new({ pump_ms = 16 }, {
    make_button = ui.make_editor_button,
    make_popup = ui.make_editor_popup, target = function() return bound_target end,
    library = function(token)
        assert(token == worldToken)
        if not waiting_library then return library, snapshot, nil, index end
    end,
    capture = function(target, token)
        assert(target == bound_target and token == worldToken)
        local compatibility, zones = layout_for(target.kind, current_color)
        local captured = {id = target.id, compatibility = compatibility, logicalZones = zones, complete = not incomplete}
        return {step = function() return {state = "done", view = {contextToken = token, targets = {captured}}} end}
    end,
    editor_ship = function(expected) return expected or {shipRef = {id = "barge"}} end,
    save = function(target, name, token, snap, guard)
        assert(target == bound_target and token == worldToken and snap == snapshot and guard())
        saves = saves + 1
        library.presets[#library.presets + 1] = record("n" .. saves, name, target.kind, current_color)
        return { step = function() return { state = "done", view = { library = library },
            savedId = "n" .. saves, publication = { snapshot = snapshot } } end }
    end,
    published = function(progress, committed)
        if committed then assert(progress.snapshot == snapshot and committed == library); prepare_index() end
    end,
    context = function() return worldToken end,
    log = function(msg) logs[#logs + 1] = msg end,
    open = function() opens = opens + 1; return true end,
})
function tick(count) for _ = 1, count or 1 do bridge:tick() end end
function press_button()
    panel.children[2].pressed, panel.children[2].hovered = true, true
    tick()
end
function release_button(hovered)
    panel.children[2].pressed, panel.children[2].hovered = false, hovered
    tick()
end
function click_button()
    press_button()
    release_button(true)
end
function click(control)
    control.hovered, control.pressed = true, true
    tick()
    control.pressed = false
    tick()
end
function work() bridge:work({expired = function() return false end, consume = function() end}) end
function release_cursor() pc.bShowMouseCursor = false end
function leave_camera() nextCamera = gameCamera end
function show_character_inventory()
    local player_data = object("/Game/Data/UI/OverlayWidgets/DA_Widget_Player.DA_Widget_Player")
    function pc:IsOverlayVisible(asset) return nativeVisible and asset == player_data end
    nativeData = player_data
    editor = object("/Overlay:Central", "/Game/UI/Game/HUD/BP_VoyageCentralWidget.BP_VoyageCentralWidget_C")
    overlay.child = editor
    overlay.RootCanvasPanel:AddChild(editor)
    function editor:GetWidgetOwnerController() return pc end
    local tree = object("/Central:WidgetTree", nil, editor)
    editor.WidgetTree = tree
    local page = object("/Central:WidgetTree.InventorySB", "/Script/UMG.SizeBox", tree)
    editor.WidgetSwitcher = object("/Central:WidgetTree.WidgetSwitcher", "/Script/UMG.WidgetSwitcher", tree)
    editor.WidgetSwitcher:AddChild(page)
    function editor.WidgetSwitcher:GetActiveWidget() if not inventory_hidden then return page end end
    local column = object("/Central:WidgetTree.VerticalBox_2", "/Script/UMG.VerticalBox", tree)
    page:AddChild(column)
    panel = object("/Central:WidgetTree.WrapBox_0", "/Script/UMG.WrapBox", tree)
    column:AddChild(panel)
    local box = object("/Central:WidgetTree.SizeBox", "/Script/UMG.SizeBox", tree)
    panel:AddChild(box)
    editor.BP_PaintBarWidget = object("/Central:WidgetTree.PaintBar", nil, tree)
    box:AddChild(editor.BP_PaintBarWidget)
    bound_target = {id = "suit", kind = "character", ownerRuntimeRef = {id = "player"}}
end
'''


class NativeEditorTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime()
        self.lua.globals().scripts = SCRIPTS.as_posix()
        self.lua.execute('package.path = scripts .. "/?.lua;" .. package.path')
        self.lua.execute(FIXTURE)

    def test_click_hands_off_once_after_overlay_input_and_camera_release(self):
        self.lua.execute(r'''
            tick(3)
            assert(#panel.children == 3 and discoveries == 1)
            assert(panel.children[2].children[1].text == "Open PaintManager")
            press_button()
            tick(5)
            assert(closes == 0 and opens == 0)
            release_button(true)
            assert(closes == 1 and opens == 0 and #panel.children == 1)
            nativeVisible = false
            tick()
            release_cursor()
            leave_camera()
            tick()
            assert(opens == 0)
            inViewport, dialogOpen = false, true
            tick()
            assert(opens == 0)
            dialogOpen = false
            nextCamera = nil
            tick()
            assert(opens == 0)
            leave_camera()
            tick(16)
            assert(opens == 1 and closes == 1)
        ''')

    def test_visibility_follows_character_paint_page_without_new_overlay_search(self):
        self.lua.execute(r'''
            show_character_inventory()
            inventory_hidden=true
            tick()
            assert(bridge.attached and not bridge:is_ui_visible())
            inventory_hidden=false;tick();assert(bridge:is_ui_visible())
            local before=discoveries
            inventory_hidden=true;tick();assert(not bridge:is_ui_visible())
            inventory_hidden=false;tick();assert(bridge:is_ui_visible() and discoveries==before)
            bridge:reset();assert(not bridge:is_ui_visible())
        ''')

    def test_visible_editor_and_handoff_reuse_controller_and_honor_elapsed_timeout(self):
        self.lua.execute(r'''
            tick()
            assert(bridge:is_ui_visible())
            FindFirstOf=function() error("Unexpected controller discovery after attachment") end
            click_button()
            assert(bridge.pending and bridge:is_ui_visible())
            nativeVisible=false
            for i=1,100 do bridge:tick(4) end
            assert(bridge.pending and not bridge:is_ui_visible() and opens==0)
            bridge:tick(1599);assert(bridge.pending)
            bridge:tick(1);assert(not bridge.pending and opens==0)
        ''')

    def test_release_outside_the_button_does_not_close_the_native_editor(self):
        self.lua.execute(r'''
            tick()
            press_button()
            release_button(false)
            panel.children[2].hovered = true
            tick()
            assert(closes == 0 and opens == 0 and #panel.children == 3)
            click_button()
            assert(closes == 1)
        ''')

    def test_scope_change_cancels_pending_handoff(self):
        self.lua.execute(r'''
            tick()
            click_button()
            tick()
            nativeVisible, inViewport, worldToken = false, false, "world-b"
            release_cursor()
            leave_camera()
            tick(16)
            assert(closes == 1 and opens == 0)
        ''')

    def test_foreign_overlay_is_untouched_and_manual_close_allows_reopen(self):
        self.lua.execute(r'''
            nativeVisible = false
            tick(16)
            assert(#panel.children == 1 and discoveries == 0)
            nativeVisible = true
            release_cursor()
            tick(16)
            assert(#panel.children == 3)
            nativeVisible = false
            tick()
            assert(#panel.children == 1 and opens == 0 and closes == 0)
            nativeVisible = true
            tick(16)
            assert(#panel.children == 3)
            bridge:reset()
            assert(#panel.children == 1 and opens == 0 and closes == 0)
        ''')

    def test_stalled_native_close_can_be_retried_only_by_another_click(self):
        self.lua.execute(r'''
            tick()
            click_button()
            tick(150)
            assert(closes == 1 and opens == 0 and #panel.children == 3)
            click_button()
            tick()
            assert(closes == 2 and opens == 0)
        ''')

    def test_popup_saves_edited_or_unused_default_name_without_leaving_editor(self):
        self.lua.execute(r'''
            tick()
            assert(panel.children[3].children[1].text == "Save Preset")
            for sequence, name in ipairs({"Midnight", "Preset 2", "Preset 4"}) do
                current_color = "Custom paint " .. sequence
                click(panel.children[3])
                local p = bridge.popup
                work()
                assert(not editor:GetIsEnabled() and p.name_fields:IsVisible() and p.confirm:IsVisible())
                assert(p.entry.text == (sequence == 3 and "Preset 4" or "Preset 2"))
                assert(focus == p.entry and p.entry:GetIsEnabled() and p.confirm:GetIsEnabled())
                if sequence == 1 then
                    p.entry:SetText("   ")
                    click(p.confirm)
                    assert(p.phase == "editing" and saves == 0)
                    p.entry:SetText(name)
                end
                click(p.confirm)
                work()
                assert(saves == sequence and p.notice.text:find(name, 1, true))
                click(p.cancel)
                assert(not bridge.popup and editor:GetIsEnabled() and #panel.children == 3)
                assert(opens == 0 and closes == 0)
            end
        ''')

    def test_attached_controls_and_open_popup_relabel_without_resaving_or_replacing_name(self):
        self.lua.execute(r'''
            local messages=require("paintmanager.i18n")
            tick();local save=panel.children[3]
            click(save);work()
            local p=bridge.popup
            p.entry:SetText("My undecided name")
            messages.select("auto","ru-RU");tick()
            assert(bridge.popup==p and p.entry.text=="My undecided name" and p.phase=="editing")
            assert(save.children[1].text==messages.text("ui.editor.save_preset"))
            assert(p.cancel_label.text==messages.text("ui.editor.cancel") and saves==0 and opens==0)
            click(p.cancel);current_color="Red";click(save);work()
            p=bridge.popup;assert(p.phase=="finished")
            messages.select("auto","en-US");tick()
            assert(p.notice.text==messages.text("ui.editor.duplicate",{name="PRESET 1"}))
            assert(p.cancel_label.text=="OK" and saves==0 and closes==0)
        ''')

    def test_existing_layout_only_shows_named_acknowledgement_and_restores_focus(self):
        self.lua.execute(r'''
            tick()
            current_color = "Red"
            local save_button = panel.children[3]
            click(save_button)
            work()
            local p = bridge.popup
            assert(p.phase == "finished" and not p.name_fields:IsVisible() and not p.confirm:IsVisible())
            assert(p.notice.text == "This configuration is already saved as “PRESET 1”.")
            assert(focus == p.cancel and p.cancel_label.text == "OK" and p.cancel:GetIsEnabled())
            click(p.cancel)
            assert(not bridge.popup and editor:GetIsEnabled() and focus == save_button and saves == 0)
        ''')

    def test_cold_wait_is_cancellable_without_showing_inactive_name_fields(self):
        self.lua.execute(r'''
            tick()
            waiting_library = true
            click(panel.children[3])
            work()
            local p = bridge.popup
            assert(p.phase == "inspect" and not p.name_fields:IsVisible() and not p.confirm:IsVisible())
            assert(p.notice.text:find("Loading saved presets", 1, true) and focus == p.cancel)
            click(p.cancel)
            assert(not bridge.popup and saves == 0)
            click(panel.children[3])
            work()
            waiting_library = false
            work()
            p = bridge.popup
            assert(p.phase == "editing" and p.name_fields:IsVisible() and focus == p.entry)
        ''')

    def test_matching_layout_found_at_commit_shows_existing_name(self):
        self.lua.execute(r'''
            tick()
            click(panel.children[3])
            work()
            local p = bridge.popup
            p.entry:SetText("My new preset")
            bridge.callbacks.save = function()
                return {step = function() return {state = "done", savedId = "i3", saveSkipped = true,
                    view = {library = library}, publication = {snapshot = snapshot}} end}
            end
            click(p.confirm)
            work()
            assert(p.phase == "finished" and not p.confirm:IsVisible() and not p.name_fields:IsVisible())
            assert(p.notice.text == "This configuration is already saved as “Preset 3”." and p.cancel_label.text == "OK")
        ''')

    def test_stale_save_reconciles_without_resubmitting_and_uses_new_library(self):
        self.lua.execute(r'''
            tick()
            local attempts = 0
            bridge.callbacks.save = function()
                attempts = attempts + 1
                return {step = function()
                    return {state = "error", phase = "preset_store", error = "Library preview changed", publication = {}}
                end}
            end
            click(panel.children[3])
            work()
            local p = bridge.popup
            p.entry:SetText("My paint")
            click(p.confirm)
            work()
            assert(p.phase == "inspect" and not p.confirm:IsVisible())
            work()
            assert(p.phase == "editing" and p.notice.text:find("Library preview changed", 1, true))
            assert(p.entry.text == "My paint" and attempts == 1 and p.confirm:GetIsEnabled())
            click(p.confirm)
            work()
            library.presets[#library.presets+1] = record("extra", "From another save", "boat", current_color)
            prepare_index()
            work()
            assert(p.phase == "finished" and attempts == 2 and p.cancel_label.text == "OK")
            assert(p.notice.text == "This configuration is already saved as “From another save”.")
        ''')

    def test_incomplete_configuration_does_not_offer_save(self):
        self.lua.execute(r'''
            tick()
            incomplete = true
            click(panel.children[3])
            work()
            local p = bridge.popup
            assert(p.phase == "finished" and not p.confirm:IsVisible() and not p.name_fields:IsVisible())
            assert(p.notice.text:find("incomplete", 1, true) and saves == 0)
        ''')

    def test_popup_cancel_and_changed_native_target_do_not_save(self):
        self.lua.execute(r'''
            tick()
            click(panel.children[3])
            work()
            click(bridge.popup.cancel)
            assert(not bridge.popup and saves == 0 and editor:GetIsEnabled())
            click(panel.children[3])
            work()
            bound_target = { id = "other", ownerRuntimeRef = { id = "other boat" } }
            click(bridge.popup.confirm)
            work()
            assert(bridge.popup.phase == "finished" and saves == 0)
            assert(bridge.popup.notice.text:find("target changed", 1, true))
            nativeVisible = false
            tick()
            assert(not bridge.popup and not bridge.attached and #panel.children == 1)
            assert(editor:GetIsEnabled() and saves == 0 and opens == 0 and closes == 0)
        ''')

    def test_character_inventory_saves_without_ship_and_cancels_on_hidden_page(self):
        self.lua.execute(r'''
            show_character_inventory()
            bridge.callbacks.editor_ship = function() error("Character editor must not search for a ship") end
            tick()
            assert(#panel.children == 2 and panel.children[2].children[1].text == "Save Preset")
            click(panel.children[2])
            work()
            click(bridge.popup.confirm)
            work()
            assert(saves == 1 and bridge.popup.notice.text:find("Preset 2", 1, true))
            click(bridge.popup.cancel)
            current_color = "New paint"
            click(panel.children[2])
            work()
            click(bridge.popup.confirm)
            inventory_hidden = true
            work()
            assert(saves == 1 and bridge.popup.phase == "finished")
        ''')

    def test_editor_change_retires_pending_save_before_another_storage_step(self):
        self.lua.execute(r'''
            local steps, cancelled = 0, 0
            bridge.callbacks.save = function()
                return {step = function() steps = steps + 1; return {state = "running"} end,
                    cancel = function() cancelled = cancelled + 1 end}
            end
            tick()
            click(panel.children[3])
            work()
            click(bridge.popup.confirm)
            work()
            assert(steps == 1)
            bound_target = { id = "different", ownerRuntimeRef = {id = "barge"} }
            work()
            assert(cancelled == 1 and steps == 1 and bridge.popup.phase == "finished")
            tick(10)
            work()
            assert(steps == 1 and cancelled == 1)
        ''')

    def test_main_pump_saves_from_cold_stock_editor_with_mod_window_closed(self):
        lua = LuaRuntime()
        lua.globals().scripts_path = SCRIPTS.as_posix()
        lua.execute('package.path=scripts_path.."/?.lua;"..package.path; editorUI=require("paintmanager.ui")')
        lua.execute(MAIN_FIXTURE)
        lua.execute(PRESET_MAIN_FIXTURE)
        lua.execute('''
            local module = require("paintmanager.ui")
            module.make_editor_button, module.make_editor_popup = editorUI.make_editor_button, editorUI.make_editor_popup
        ''')
        lua.execute(FIXTURE)
        lua.execute('''
            local module = require("paintmanager.native_editor")
            local new = module.new
            function module.new(config, callbacks) bridge = new(config, callbacks); return bridge end
            require("paintmanager.presets").editor_target = function() return current_preset_view().targets[1] end
            require("paintmanager.ship_scope").editor_ship = function(_, expected)
                assert(inGameThread)
                return expected or {shipRef = current_preset_view().targets[1].ownerRuntimeRef}
            end
            local loads = 0
            local function replica(value)
                return {scopeId = "scope", accountId = "account", path = "library.json", loadGeneration = 2,
                    raw = value, localSnapshot = {raw = value, missing = false}}
            end
            snapshot = replica("first")
            require("paintmanager.save_bridge").storage_context = function(token)
                assert(token == ctx and inGameThread)
                return {status = "ready", scopeId = snapshot.scopeId, accountId = snapshot.accountId,
                    libraryPath = snapshot.path, loadGeneration = snapshot.loadGeneration}
            end
            local store = {
                new_id = function() return "editor-id" end,
                begin_state_load = function()
                    assert(inGameThread)
                    loads = loads + 1
                    local data = require("paintmanager.state_document").new()
                    data.sections.presets.data = library
                    return {step = function() return {state = "done", staged = true, snapshot = snapshot} end,
                        get_result = function() return data end}
                end,
                begin_save_layout = function(record)
                    assert(inGameThread)
                    return {step = function()
                        saves = saves + 1
                        library = {presets = {library.presets[1], library.presets[2], record}}
                        snapshot = replica("updated")
                        return {state = "done", staged = true, snapshot = snapshot}
                    end, get_result = function() return library end}
                end
            }
            require("paintmanager.save_store").new = function(_, token, _, expected)
                assert(token == ctx and (expected == nil or expected == snapshot))
                return store
            end
            function tick(count) for _ = 1, count or 1 do pump_once() end end
            start()
            tick()
            click(panel.children[3])
            until_value(function() return bridge.popup and bridge.popup.phase == "editing" end)
            assert(bridge.popup.entry.text == "Preset 2")
            click(bridge.popup.confirm)
            until_value(function() return bridge.popup and bridge.popup.phase == "finished" end)
            assert(saves == 1 and bridge.popup.notice.text:find("Preset 2", 1, true), bridge.popup.notice.text)
            assert(ui == nil and opens == 0 and closes == 0 and preset_captures == 2 and loads == 1)
            click(bridge.popup.cancel)
            click(panel.children[3])
            until_value(function() return bridge.popup and bridge.popup.phase == "finished" end)
            assert(bridge.popup.notice.text == "This configuration is already saved as “Preset 2”.")
            assert(bridge.popup.cancel_label.text == "OK" and preset_captures == 3 and saves == 1 and loads == 1)
        ''')
