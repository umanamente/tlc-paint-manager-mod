"""Offline UI lifecycle tests. Native UMG rendering still needs an in-game check."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"

FIXTURE = r'''
calls, widgets, commands, all_objects, hosts, logs, learned = {}, {}, {}, {}, {}, {}, {}
bulk_learned=0
fail_ui, fail_game, fail_text, fail_remove = false, false, false, false
function event(kind) calls[#calls+1]=kind end
key_event_hooks={}
function RegisterHook(path,callback) key_event_hooks[path]=callback;return 1,1 end
function UnregisterHook(path) key_event_hooks[path]=nil end
function object(kind)
    local o={kind=kind, valid=true, Font={},children={},slots={},enabled=true,visible=0,address=#all_objects+1,
        WidgetStyle={Normal={DrawAs=1},Hovered={DrawAs=1},Pressed={DrawAs=1},Disabled={DrawAs=1},
            TextStyle={Font={Size=24}},NormalPadding={},PressedPadding={}}}
    all_objects[#all_objects+1]=o
    function o:IsValid() return self.valid end
    function o:IsA(path) return kind=="Texture" and path=="/Script/Engine.Texture2D" or kind=="CanvasPanel" and path=="/Script/UMG.CanvasPanel" or self.is_class and path=="/Script/CoreUObject.Class" end
    function o:GetFullName() return self.fullname or kind end
    function o:GetClass() return object("Class /Script/UMG."..kind) end
    function o:GetAddress() return self.address end
    function o:GetOuter() return self.outer end
    function o:GetSuperStruct() return self.super end
    function o:SetText(text)
        if fail_text then error("text failed") end
        native_time=(native_time or 0)+(native_text_cost or 0);self.text=text
    end
    function o:GetText()
        event("get_text:"..self.kind)
        if fail_get_text then error("text read failed") end
        return self.text
    end
    function o:SetHintText(text) self.hint=text end
    function o:SetAutoWrapText(value) self.wrap=value end
    function o:SetTextOverflowPolicy(value) self.overflow=value end
    function o:SetClipping(value) self.clipping=value end
    function o:SetWrapTextAt() error("SetWrapTextAt is not a reflected callable") end
    function o:SetToolTip(value) self.tooltip=value end
    function o:SetFont(value) self.Font=value end
    function o:SetStyle(value) self.WidgetStyle=value end
    function o:SetStretch(value) self.stretch=value end
    function o:SetStretchDirection(value) self.stretch_direction=value end
    function o:SetColorAndOpacity() end
    function o:SetJustification() end
    function o:SetBrushColor(color) self.brush_color=color end
    function o:SetBackgroundColor() end
    function o:SetContent(child) self.child=child;return self:AddChild(child) end
    function o:SetPadding(value) self.padding=value end
    function o:SetSize(value) self.size=value end
    function o:SetPosition(value) self.position=value end
    function o:SetAnchors(value) self.anchors=value end
    function o:SetAlignment(value) self.alignment=value end
    function o:SetAutoSize(value) self.auto_size=value end
    function o:SetWidthOverride(value) self.width=value end
    function o:SetMinDesiredWidth(value) self.min_width=value end
    function o:SetMaxDesiredWidth(value) self.max_width=value end
    function o:SetHeightOverride(value) self.height=value end
    function o:SetHorizontalAlignment(value) self.horizontal_alignment=value end
    function o:SetVerticalAlignment(value) self.vertical_alignment=value end
    function o:SetRenderOpacity(value) self.opacity=value end
    function o:SetPeriod(value) self.period=value end
    function o:SetRadius(value) self.radius=value end
    function o:SetNumberOfPieces(value) self.pieces=value end
    function o:SetIsMarquee(value) self.marquee=value end
    function o:SetPercent(value) self.percent=value end
    function o:SetIsChecked(value) self.checked=value end
    function o:IsChecked() return self.checked end
    if kind=="ComboBoxString" then
        o.ForegroundColor={SpecifiedColor={R=0,G=0,B=0,A=1},ColorUseRule=0}
        o.ItemStyle={SelectorFocusedBrush={DrawAs=2,TintColor={ColorUseRule=1}}}
        for _,name in ipairs({"EvenRowBackgroundBrush","OddRowBackgroundBrush",
            "EvenRowBackgroundHoveredBrush","OddRowBackgroundHoveredBrush",
            "ActiveBrush","InactiveBrush","ActiveHoveredBrush","InactiveHoveredBrush"}) do
            o.ItemStyle[name]={DrawAs=0}
        end
        o.options={}
        function o:AddOption(value) self.options[#self.options+1]=value end
        function o:SetSelectedIndex(index)
            if self.options[index+1] then self.chosen=index end
        end
        function o:GetSelectedIndex() return self.chosen or -1 end
    end
    function o:SetRenderScale(value) self.scale=value;if fail_layout then error("layout failed") end end
    function o:SetRenderTransformPivot(value) self.pivot=value end
    function o:SetVisibility(value) self.visible=value end
    function o:ScrollToEnd() self.scrolled_to_end=true end
    function o:GetScrollOffset() return self.native_scroll_offset or 0 end
    function o:SetScrollOffset(value) self.native_scroll_offset=value end
    function o:SetActiveWidgetIndex(index) self.active=index end
    function o:SetKeyboardFocus() event("focus_ui") end
    function o:SetBrushFromTexture(texture,match) self.texture=texture;event("icon") end
    function o:SetIsEnabled(value) self.enabled=value end
    function o:GetIsEnabled() return self.enabled end
    function o:IsPressed() return self.down==true end
    function o:AddChild(child)
        self.children[#self.children+1]=child
        local slot=object("Slot");slot.collectable=true;slot.Content=child;slot.Parent=self
        child.slot=slot;child.Slot=slot;self.slots[#self.slots+1]=slot
        return slot
    end
    o.AddChildToCanvas=o.AddChild
    function o:AddChildToUniformGrid(child,row,column)
        local slot=self:AddChild(child);slot.row=row;slot.column=column;return slot
    end
    function o:ClearChildren() self.children={};self.slots={};self.cleared=true end
    function o:RemoveFromParent()
        event("remove_parent")
        if fail_remove then error("remove failed") end
        if viewport and viewport.added==self then viewport.added=nil end
        local slot=self.slot
        if slot and slot.Parent then
            local parent=slot.Parent
            for i=#parent.slots,1,-1 do if parent.slots[i]==slot then table.remove(parent.slots,i) end end
            for i=#parent.children,1,-1 do if parent.children[i]==self then table.remove(parent.children,i) end end
            slot.Content,slot.Parent,self.slot=nil,nil,nil
        end
    end
    function o:IsInViewport() return viewport.added==self end
    function o:AddToViewport(z)
        assert(z==9800 and self.WidgetTree and self.WidgetTree.RootWidget and self.WidgetTree.RootWidget.kind=="CanvasPanel")
        self.slate_root=self.slate_root or self.WidgetTree.RootWidget
        viewport.added=self;event("add")
    end
    function o:SetDesiredSizeInViewport(size) event("host_size");self.viewport_size=size;self.viewport_anchors={X=0,Y=0} end
    function o:SetAnchorsInViewport(anchors) event("host_anchors");self.viewport_anchors=anchors.Minimum end
    function o:SetAlignmentInViewport(alignment) event("host_alignment");self.viewport_alignment=alignment end
    function o:SetPositionInViewport(position,remove_dpi)
        assert(position.X==0 and position.Y==0 and remove_dpi==false)
        -- Verified game binary resets all viewport anchors in this helper.
        event("host_position");self.viewport_position=position;self.viewport_anchors={X=0,Y=0}
    end
    return o
end
world=object("World")
pc=object("Controller");pc.Pawn=object("Pawn");pc.bShowMouseCursor=false
function pc:GetWorld() return world end
function pc:IsLocalController() return true end
function pc:SetIgnoreMoveInput(value) move=(move or 0)+(value and 1 or -1);event("move:"..tostring(value)) end
function pc:SetIgnoreLookInput(value) look=(look or 0)+(value and 1 or -1);event("look:"..tostring(value)) end
input=object("Input")
userwidget_class=object("Class /Script/UMG.UserWidget");userwidget_class.fullname=userwidget_class.kind;userwidget_class.is_class=true
host_type=object("WidgetBlueprintGeneratedClass /Game/UI/Game/HUD/BP_VoyageIngameEmpty.BP_VoyageIngameEmpty_C")
host_type.fullname=host_type.kind;host_type.super=userwidget_class;host_type.is_class=true
host_loaded=true
function input:Create(context,cls,owner)
    assert(context==pc and owner==pc and cls==host_type)
    event("create_host")
    local host=object("Host");host.collectable=true
    local tree=object("WidgetTree");tree.collectable=true;tree.outer=foreign_tree and instance or host;host.WidgetTree=tree
    local default=object(wrong_root and "Border" or "CanvasPanel");default.collectable=true;default.outer=tree;tree.RootWidget=not missing_root and default or nil
    host.default_root=default;hosts[#hosts+1]=host
    if native_decoration then
        local child=object("NativeDecoration");child.collectable=true;child.outer=tree
        default:AddChild(child);host.default_child=child
    end
    if prebuilt_host then host.slate_root=tree.RootWidget end
    return host
end
function input:SetInputMode_UIOnlyEx(controller,focus,lock,flush)
    assert(controller==pc and focus.kind=="Button" and lock==0 and flush==true)
    event("ui_only");if fail_ui then error("capture failed") end
end
function input:SetInputMode_GameOnly(controller,flush)
    assert(controller==pc and flush==true);event("game_only")
    if fail_game then error("restore failed") end
end
function input:SetFocusToGameViewport() event("focus_game") end
viewport=object("Viewport")
function viewport:AddWidget(widget,slot) self.added=widget;event("add");return true end
function viewport:IsWidgetAdded(widget) return self.added==widget end
function viewport:RemoveWidget(widget) assert(self.added==widget);self.added=nil;event("remove") end
registry=object("Registry")
function registry:GetAsset(data)
    event("load_host");assert(data.PackageName=="/Game/UI/Game/HUD/BP_VoyageIngameEmpty" and data.AssetName=="BP_VoyageIngameEmpty_C")
    if host_unavailable then return nil end
    host_loaded=true;return host_type
end
layout=object("Layout")
function layout:GetViewportSize() return {X=viewport_width or 1920,Y=viewport_height or 1080} end
function layout:GetViewportScale() return viewport_dpi or 1 end
gameplay=object("Gameplay")
function gameplay:IsGamePaused() return paused==true end
instance=object("Instance")
package.preload.UEHelpers=function() return {GetGameInstance=function() return instance end} end
EObjectFlags={RF_Transient=64}
EFindName={FNAME_Add=1}
function FName(s) return s end
function FText(s) return s end
function FindFirstOf(class)
    event("find:"..class)
    if class=="VoyagePlayerController" then return pc end
    if class=="GameViewportSubsystem" then return viewport end
end
function StaticFindObject(path)
    if path=="/Game/UI/Game/HUD/BP_VoyageIngameEmpty.BP_VoyageIngameEmpty_C" then return host_loaded and host_type or nil end
    if path=="/Script/AssetRegistry.Default__AssetRegistryHelpers" then return registry end
    if path=="/Script/UMG.Default__WidgetBlueprintLibrary" then return input end
    if path=="/Script/UMG.Default__WidgetLayoutLibrary" then return layout end
    if path=="/Script/Engine.Default__GameplayStatics" then return gameplay end
    if path=="/Game/Icon.Real" then return object("Texture") end
    if path:find("^/Script/UMG%.") then return object(path:match("%.([^%.]+)$")) end
end
function StaticConstructObject(class,outer,name,flags)
    assert((outer.kind=="WidgetTree" or outer==instance) and flags==64)
    local w=object(class.kind);w.collectable=true;w.outer=outer;widgets[#widgets+1]=w;return w
end
-- A graph model of native strong references. Lua arrays/proxies and Outer do
-- NOT keep widgets alive. Plain viewport CanvasPanel keys do NOT keep them
-- alive. An attached UserWidget has an SObjectWidget FGCObject owner; reflected
-- WidgetTree/RootWidget and panel slot/content references preserve descendants.
function collect_native()
    local marked={}
    local function mark(o)
        if not o or marked[o] or not o.valid then return end
        marked[o]=true
        mark(o.WidgetTree);mark(o.RootWidget);mark(o.child);mark(o.Content);mark(o.Parent);mark(o.slot);mark(o.texture);mark(o.tooltip)
        for _,slot in ipairs(o.slots or {}) do mark(slot) end
    end
    if viewport.added and viewport.added.kind=="Host" then mark(viewport.added) end
    for _,o in ipairs(all_objects) do if o.collectable and not marked[o] then o.valid=false end end
end
local config={max_sources=128,max_catalog=128,ui_rows_per_tick=2,work_budget_ms=2}
ui=require("paintmanager.ui").new(config,{
    enqueue=function(command) commands[#commands+1]=command;return true end,
    learn_recipe=function(item_id) learned[#learned+1]=item_id;return true end,
    learn_all=function() bulk_learned=bulk_learned+1;return true end,
    change_settings=function(changes) setting_changes=setting_changes or {};setting_changes[#setting_changes+1]=changes;return true end,
    favorite=function(asset_type,asset_name,enabled) favorite_changes=favorite_changes or {};favorite_changes[#favorite_changes+1]={asset_type,asset_name,enabled};return true end,
    log=function(message) logs[#logs+1]=message end,
})
function click(label)
    for _,scope in ipairs({ui.active_tab,"global"}) do
        for _,b in ipairs(ui.buttons) do
            if b.label and b.label.text==label and (b.scope==scope or scope=="global" and b.scope==nil
                or scope==ui.active_tab and b.scope=="paint" and (scope=="learned" or scope=="ready" or scope=="missing")) then
                b.widget.down=true;ui:tick();b.widget.down=false;ui:tick();return
            end
        end
    end
    error("missing button: "..label)
end
function text_contains(value)
    for _,w in ipairs(widgets) do if w.text and w.text:find(value,1,true) then return true end end
    return false
end
function event_count(value)
    local count=0;for _,kind in ipairs(calls) do if kind==value then count=count+1 end end;return count
end
function work_budget(limit,deadline)
    local budget={units=0,limit=limit or 8,deadline=deadline}
    function budget:expired() return self.units>=self.limit or self.deadline and (native_time or 0)>=self.deadline end
    function budget:consume(n) self.units=self.units+n end
    return budget
end
function drain_render()
    local ticks=0
    while ui:is_rendering() do
        ui:tick(work_budget(8));ticks=ticks+1
        assert(ticks<5000,"render failed to finish")
    end
    return ticks
end
function render_model(model) ui:set_model(model);return drain_render() end
function learn_model(rows, overrides)
    local model={status="captured",complete=true,stockComplete=true,sources={},rows=rows}
    for key,value in pairs(overrides or {}) do model[key]=value end
    return model
end
function learn_buttons()
    local found={}
    for _,button in ipairs(ui.buttons) do
        if button.kind=="learn_recipe" then found[#found+1]=button end
    end
    return found
end
function bulk_button()
    for _,button in ipairs(ui.buttons) do if button.kind=="learn_all" then return button end end
    error("bulk button unavailable")
end
function visible_text_contains(value)
    local visited={}
    local function contains(node)
        if not node or visited[node] then return false end
        visited[node]=true
        if node.text and node.text:find(value,1,true) then return true end
        if contains(node.child) then return true end
        for _,slot in ipairs(node.slots or {}) do if contains(slot.Content) then return true end end
        return false
    end
    return contains(ui.root)
end
function large_model(count,prefix)
    local model={status="captured",complete=true,stockComplete=true,includedSourceCount=count,sourceCount=count,physicalCount=count,sources={},rows={}}
    for i=1,count do
        model.sources[i]={id=prefix.."Box"..i,label=prefix.."Box"..i,kind="Small",included=true,paintCount=1}
        model.rows[i]={id=prefix..i,assetType="Item",assetName=prefix..i,displayName=prefix.."Paint"..i,count=1,recipeState="unlocked",iconPath="Texture2D /Game/Icon.Real"}
    end
    return model
end
function advance_to_paint_rows()
    local count=0
    while not (ui.render_job.phase=="paints") do
        ui:tick(work_budget(1));count=count+1;assert(count<5000)
    end
end
function configured_frame_rect()
    local slot=ui.frame_slot
    local width,height=(viewport_width or 1920)/(viewport_dpi or 1),(viewport_height or 1080)/(viewport_dpi or 1)
    local left=width*slot.anchors.Minimum.X+slot.position.X-slot.size.X*slot.alignment.X
    local top=height*slot.anchors.Minimum.Y+slot.position.Y-slot.size.Y*slot.alignment.Y
    local pivot_x,pivot_y=left+slot.size.X*ui.frame.pivot.X,top+slot.size.Y*ui.frame.pivot.Y
    return {left=pivot_x-slot.size.X*ui.frame.pivot.X*ui.frame.scale.X,
        top=pivot_y-slot.size.Y*ui.frame.pivot.Y*ui.frame.scale.Y,
        width=slot.size.X*ui.frame.scale.X,height=slot.size.Y*ui.frame.scale.Y,
        logicalWidth=width,logicalHeight=height}
end
'''


CLICK_TIMING_FIXTURE = r'''
now_ms=1000
os.clock=function() return now_ms/1000 end
function press_tab(tab)
    local widget=ui.tab_controls[tab].widget
    widget.down=true;ui:tick(work_budget(0),true)
    widget.down=false;ui:tick(work_budget(0),true)
    assert(ui:is_open() and ui.active_tab==tab)
end
function click_logs()
    local entries={}
    for _,message in ipairs(logs) do
        if message:find("Tab click:",1,true) then entries[#entries+1]=message end
    end
    return entries
end
function assert_timing(index,tab,ms,outcome,cached)
    local entries=click_logs()
    local message=assert(entries[index],"Missing click log #"..index)
    for _,field in ipairs({"tab="..tab..";","elapsedMs="..ms..";","outcome="..outcome..";",
        "cached="..tostring(cached)}) do
        assert(message:find(field,1,true),message.." missing "..field)
    end
    return message
end
'''


class UITests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def test_constructor_has_no_native_work(self):
        self.assertEqual(self.lua.eval("#calls"), 0)
        self.assertFalse(self.lua.eval("ui:is_open()"))

    def test_input_polling_skips_hidden_rows_but_polls_active_and_global_controls(self):
        self.lua.execute('''
        assert(ui:toggle())
        local model=large_model(40,"Paint");model.status="captured";model.stockComplete=true
        for index,row in ipairs(model.rows) do row.id="Paint"..index;row.count=4 end
        render_model(model);ui:_select_tab("presets")
        local active,global,hidden,polled_active,polled_global=0,0,0,0,0
        for _,button in ipairs(ui.buttons) do
            if button.scope~=nil and button.scope~="presets" then
                hidden=hidden+1;button.pressed=true
                function button.widget:IsPressed() error("Hidden button must not be polled") end
                function button.widget:GetIsEnabled() error("Hidden button must not be queried") end
            elseif button.scope==nil then
                global=global+1
                function button.widget:IsPressed() polled_global=polled_global+1;return false end
            else
                active=active+1
                function button.widget:IsPressed() polled_active=polled_active+1;return false end
            end
        end
        ui:tick(work_budget(0))
        assert(ui:is_open() and hidden>=40 and active>0 and global>0)
        assert(polled_active==active and polled_global==global)
        for _,button in ipairs(ui.buttons) do
            if button.scope~=nil and button.scope~="presets" then assert(button.pressed==false) end
        end
        ''')

    def test_scope_switch_preserves_held_click_gate_until_active_release(self):
        self.lua.execute('''
        assert(ui:toggle());drain_render()
        local fired=0
        ui:_button("Scoped control",function() fired=fired+1 end,"paint")
        local button=ui.buttons[#ui.buttons]
        button.widget.down=true;ui:tick(work_budget(0));assert(fired==1 and button.pressed)
        ui:_select_tab("presets");ui:tick(work_budget(0))
        assert(button.pressed==false and button.release_required)
        ui:_select_tab("paint")
        ui:tick(work_budget(0));ui:tick(work_budget(0));assert(fired==1)
        button.widget.down=false;ui:tick(work_budget(0));assert(not button.release_required)
        button.widget.down=true;ui:tick(work_budget(0));ui:tick(work_budget(0));assert(fired==2)
        ''')

    def test_first_click_after_activating_an_unpressed_scope_is_not_dropped(self):
        self.lua.execute('''
        assert(ui:toggle());drain_render();ui:_select_tab("presets")
        local fired=0
        ui:_button("Fresh scoped control",function() fired=fired+1 end,"paint")
        local button=ui.buttons[#ui.buttons]
        ui:tick(work_budget(0));assert(button.pressed==false and not button.release_required)
        ui:_select_tab("paint");button.widget.down=true
        ui:tick(work_budget(0));ui:tick(work_budget(0));assert(fired==1)
        ''')

    def test_open_close_pairs_input_and_forgets_widgets(self):
        self.lua.execute("assert(ui:toggle());render_model({rows={},sources={}});assert(move==1 and look==1);ui:close('world_changed')")
        self.assertEqual(self.lua.eval("move"), 0)
        self.assertEqual(self.lua.eval("look"), 0)
        self.assertFalse(self.lua.eval("pc.bShowMouseCursor"))
        self.assertTrue(self.lua.eval("viewport.added==nil and ui.root==nil and ui.pc==nil and #ui.buttons==0 and ui.model==nil"))

    def test_close_returns_focus_before_removing_owned_slate_host(self):
        self.lua.execute('''
        assert(ui:toggle());calls={};ui:close("user")
        local mode,focus,removed
        for index,call in ipairs(calls) do
            if call=="game_only" then mode=index end
            if call=="focus_game" then focus=index end
            if call=="remove_parent" and not removed then removed=index end
        end
        assert(mode and focus and removed and mode<focus and focus<removed)
        ''')

    def test_failed_capture_also_restores_input(self):
        self.lua.execute("fail_ui=true;opened,err=ui:toggle()")
        self.assertFalse(self.lua.eval("opened"))
        self.assertIn("capture failed", self.lua.eval("err"))
        self.assertTrue(self.lua.eval("move==0 and look==0 and not pc.bShowMouseCursor and viewport.added==nil"))

    def test_restore_failure_retries_without_double_decrement(self):
        self.lua.execute("assert(ui:toggle());owned_host=ui.host;fail_game=true;ui:close('user')")
        self.assertTrue(self.lua.eval("ui.input_claimed and ui.cleanup_host==owned_host and viewport.added==owned_host"))
        self.lua.execute("fail_game=false;ui:tick()")
        self.assertTrue(self.lua.eval("move==0 and look==0 and not ui.input_claimed and ui.pc==nil"))

    def test_existing_native_menu_is_not_taken_over(self):
        self.lua.execute("pc.bShowMouseCursor=true;opened,err=ui:toggle()")
        self.assertFalse(self.lua.eval("opened"))
        self.assertEqual(self.lua.eval("#widgets"), 0)
        self.assertTrue(self.lua.eval("pc.bShowMouseCursor"))


    def test_native_unicode_names_and_braces_are_not_translated(self):
        self.lua.execute(r'''
        assert(ui:toggle())
        local native_name="\u{65E5}\u{672C} {native_name}"
        render_model({complete=true,includedSourceCount=1,sourceCount=1,physicalCount=1,
            sources={{id="A",displayName=native_name,kind="Small",included=true,paintCount=1}},
            rows={{assetName="DA_Paint_Canada",displayName=native_name,count=1,recipeState="unlocked"}}})
        assert(visible_text_contains(native_name))
        assert(visible_text_contains("Recipe unlocked") and not visible_text_contains("Containers counted"))
        ''')










    def test_holding_button_dispatches_once(self):
        self.lua.execute(r'''
        assert(ui:toggle())
        ui:set_preferences({showDeveloperTools=true})
        ui:_select_tab("debug")
        click("Diagnostics")
        for _,b in ipairs(ui.buttons) do if b.label and b.label.text=="Snapshot" then b.widget.down=true end end
        ui:tick();ui:tick();ui:tick()
        ''')
        self.assertEqual(self.lua.eval("#commands"), 1)













    def test_bulk_close_reopen_and_context_replacement_cannot_replay_stale_actions(self):
        self.lua.execute(r'''
        assert(ui:toggle());render_model(learn_model({{id="Ready",count=4,recipeState="locked"}}))
        local old=bulk_button().action
        ui:close("user");old();assert(bulk_learned==0)
        assert(ui:toggle());drain_render();old();assert(bulk_learned==0)
        local current=bulk_button().action
        pc.Pawn=object("ReplacementPawn");current();assert(bulk_learned==0)
        ''')

    def test_bulk_pending_gate_survives_model_replacement_and_recounts_after_release(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_learning(true)
        render_model(learn_model({{id="Ready",count=4,recipeState="locked"}}))
        assert(ui.learn_all_ready_count==1 and not bulk_button().widget:GetIsEnabled())
        ui:set_learning(false);assert(bulk_button().widget:GetIsEnabled())
        render_model(learn_model({{id="Ready",count=4,recipeState="unlocked"}}))
        ui:set_learning(false);assert(ui.learn_all_ready_count==0 and not bulk_button().widget:GetIsEnabled())
        ''')


    def test_lost_viewport_closes_and_releases_input(self):
        self.lua.execute("assert(ui:toggle());viewport.added=nil;ui:tick()")
        self.assertFalse(self.lua.eval("ui:is_open()"))
        self.assertTrue(self.lua.eval("move==0 and look==0 and not pc.bShowMouseCursor"))


    def test_gc_model_reproduces_unowned_plain_canvas_failure(self):
        self.lua.execute(r'''
        local class=StaticFindObject("/Script/UMG.CanvasPanel")
        local plain=StaticConstructObject(class,instance,"OldPlainRoot",64)
        viewport:AddWidget(plain,{})
        collect_native()
        assert(not plain:IsValid(), "viewport identity map or Lua proxies must not be modeled as GC owners")
        ''')

    def test_userwidget_tree_survives_gc_after_repeated_refresh(self):
        self.lua.execute(r'''
        assert(ui:toggle())
        local host,tree,root=ui.host,ui.tree,ui.root
        assert(tree.RootWidget==root and tree.outer==host)
        for refresh=1,8 do
            render_model({complete=true,includedSourceCount=1,sourceCount=1,sources={{id="A",label="Small",included=true}},rows={
                {assetName="DA_Paint_Canada",displayName="Last Leaf",count=12,recipeState="locked",iconPath="Texture2D /Game/Icon.Real"}}})
            collect_native()
            ui:tick()
            assert(ui:is_open() and host:IsValid() and tree:IsValid() and root:IsValid())
            for _,button in ipairs(ui.buttons) do assert(button.widget:IsValid() and (not button.label or button.label:IsValid())) end
        end
        for _,w in ipairs(widgets) do if w:IsValid() then assert(w.outer==tree) end end
        assert(not host.default_root.cleared, "never mutate default root's contents")
        ''')

    def test_replaced_paint_row_widgets_are_collectable(self):
        self.lua.execute(r'''
        assert(ui:toggle())
        render_model(learn_model({{id="A",assetName="A",displayName="Paint A",count=12,recipeState="unlocked"}}))
        local old=ui.paint_list.children[#ui.paint_list.children]
        render_model({sources={{id="B",included=true}},rows={{assetName="B",displayName="Paint B",count=8,recipeState="unlocked"}}})
        collect_native()
        assert(old and not old:IsValid())
        assert(ui.root:IsValid() and ui:is_open())
        ''')

    def test_close_releases_graph_and_reopen_uses_a_new_host(self):
        self.lua.execute(r'''
        assert(ui:toggle())
        local host,tree,root=ui.host,ui.tree,ui.root
        assert(ui:close("user"))
        assert(tree.RootWidget==root and #root.slots==0 and viewport.added==nil and ui.host==nil and ui.tree==nil)
        collect_native()
        assert(not host:IsValid() and not tree:IsValid() and not root:IsValid())
        assert(ui:toggle())
        collect_native();ui:tick()
        assert(ui:is_open() and ui.host~=host and ui.host:IsValid() and ui.root:IsValid())
        assert(move==1 and look==1)
        ''')

    def test_build_failure_releases_all_owned_native_objects(self):
        self.lua.execute(r'''
        fail_text=true
        local opened,err=ui:toggle()
        assert(not opened and err:find("text failed",1,true))
        assert(hosts[1].WidgetTree.RootWidget==hosts[1].default_root and viewport.added==nil)
        collect_native()
        for _,o in ipairs(all_objects) do if o.collectable then assert(not o:IsValid()) end end
        assert(ui.host==nil and ui.tree==nil and ui.root==nil and ui.pc==nil)
        ''')

    def test_layout_failure_after_attachment_releases_the_host(self):
        self.lua.execute(r'''
        fail_layout=true
        local opened,err=ui:toggle()
        assert(not opened and err:find("layout failed",1,true))
        assert(viewport.added==nil and hosts[1].WidgetTree.RootWidget==hosts[1].default_root)
        collect_native();assert(not hosts[1]:IsValid())
        assert(not pc.bShowMouseCursor and (move or 0)==0 and (look or 0)==0)
        ''')

    def test_host_removal_failure_retries_before_detaching_tree(self):
        self.lua.execute(r'''
        assert(ui:toggle())
        local host,tree,root=ui.host,ui.tree,ui.root
        fail_remove=true
        assert(not ui:close("user"))
        assert(ui.cleanup_host==host and tree.RootWidget==root and viewport.added==host)
        collect_native();assert(host:IsValid() and root:IsValid())
        assert(move==0 and look==0)
        fail_remove=false;ui:tick()
        assert(ui.cleanup_host==nil and tree.RootWidget==root and #root.slots==0 and viewport.added==nil)
        collect_native();assert(not host:IsValid() and not root:IsValid())
        assert(move==0 and look==0)
        ''')

    def test_old_context_cleanup_handles_are_discarded_without_retry(self):
        self.lua.execute(r'''
        assert(ui:toggle());fail_remove=true;ui:close("user")
        local removals=event_count("remove_parent")
        pc.Pawn=object("NewPawn")
        ui:tick()
        assert(event_count("remove_parent")==removals)
        assert(ui.cleanup_host==nil and ui.cleanup_tree==nil and ui.cleanup_root==nil and ui.pc==nil)
        ''')

    def test_loaded_host_class_does_not_load_an_asset(self):
        self.lua.execute("assert(ui:toggle());assert(event_count('create_host')==1 and event_count('load_host')==0)")

    def test_unloaded_host_loads_only_the_exact_asset(self):
        self.lua.execute("host_loaded=false;assert(ui:toggle());assert(event_count('load_host')==1 and event_count('create_host')==1)")

    def test_unavailable_host_fails_without_taking_input(self):
        self.lua.execute(r'''
        host_loaded=false;host_unavailable=true
        local opened,err=ui:toggle()
        assert(not opened and err:find("host class unavailable",1,true))
        assert(event_count("load_host")==1 and event_count("create_host")==0)
        assert(not pc.bShowMouseCursor and (move or 0)==0 and viewport.added==nil)
        ''')

    def test_unexpected_host_class_is_rejected_before_creation(self):
        self.lua.execute(r'''
        host_type.fullname="WidgetBlueprintGeneratedClass /Game/Other.Other_C"
        local opened,err=ui:toggle()
        assert(not opened and err:find("unexpected class",1,true))
        assert(event_count("create_host")==0 and not pc.bShowMouseCursor)
        ''')

    def test_foreign_widget_tree_is_not_modified(self):
        self.lua.execute(r'''
        foreign_tree=true
        local opened,err=ui:toggle()
        assert(not opened and err:find("not owned",1,true))
        assert(hosts[1].WidgetTree.RootWidget==hosts[1].default_root)
        assert(not hosts[1].default_root.cleared)
        ''')

    def test_invalid_host_and_root_have_distinct_diagnostics(self):
        for field, expected in (("pc", "controller"), ("host", "UserWidget host"),
                                ("tree", "WidgetTree"), ("root", "CanvasPanel root"),
                                ("frame", "panel frame")):
            with self.subTest(field=field):
                self.setUp()
                self.lua.execute(f"assert(ui:toggle());ui.{field}.valid=false;ui:tick()")
                self.assertIn(expected, self.lua.eval("ui.status.message"))
                self.assertFalse(self.lua.eval("ui:is_open()"))

    def test_set_model_only_schedules_and_large_preparation_yields(self):
        self.lua.execute(r'''
        assert(ui:toggle());drain_render()
        local native_objects=#widgets
        ui:set_model(large_model(120,"Many"))
        assert(ui:is_rendering() and ui:render_progress().completed==0)
        assert(#widgets==native_objects, "set_model must not synchronously construct rows")
        local budget=work_budget(8);ui:tick(budget)
        assert(budget.units==8 and ui:render_progress().completed==8)
        assert(ui:is_rendering() and not visible_text_contains("ManyPaint1"))
        assert(visible_text_contains("Building list…"))
        assert(not visible_text_contains("complete within the stated limits"))
        ''')

    def test_exhausted_shared_budget_still_allows_close(self):
        self.lua.execute(r'''
        assert(ui:toggle());drain_render();ui:set_model(large_model(120,"Many"))
        local budget=work_budget(8);budget.units=8
        local progress=ui:render_progress().completed
        ui:tick(budget)
        assert(ui:render_progress().completed==progress and budget.units==8)
        ui.close_button.down=true
        ui:tick(budget)
        assert(not ui:is_open() and not ui:is_rendering() and move==0 and look==0)
        collect_native();assert(not hosts[1]:IsValid())
        ''')

    def test_incremental_rows_survive_gc_between_every_tick(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(7,"GC"))
        local host,tree,root=ui.host,ui.tree,ui.root
        local ticks=0
        while ui:is_rendering() do
            local budget=work_budget(1);ui:tick(budget)
            assert(budget.units<=1)
            collect_native();ticks=ticks+1;assert(ticks<100)
            assert(ui:is_open() and host:IsValid() and tree:IsValid() and root:IsValid())
        end
        assert(ticks>7 and ui.visible_tiles_by_id.GC7.widget:IsValid() and not visible_text_contains("GCBox7"))
        assert(not visible_text_contains("GCBox"))
        assert(ui:render_progress().phase=="done" and ui:render_progress().completed==ui:render_progress().total)
        ''')

    def test_new_render_generation_cannot_mix_with_old_partial_rows(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(5,"Old"));advance_to_paint_rows()
        ui:tick(work_budget(1))
        assert(ui.visible_tiles_by_id.Old1 and ui.active_paint_cache.scroll.visible==2 and not ui.active_paint_cache.ready)
        local old=ui.paint_list.children[#ui.paint_list.children]
        local generation=ui:render_progress().generation
        ui:set_model(large_model(1,"New"))
        assert(ui:render_progress().generation>generation)
        ui:tick(work_budget(1)) -- clear both old lists before any new row
        assert(not visible_text_contains("OldPaint1") and not visible_text_contains("NewPaint1"))
        drain_render();collect_native()
        assert(visible_text_contains("NewPaint1") and not visible_text_contains("NewBox1"))
        assert(not visible_text_contains("OldPaint") and not old:IsValid())
        ''')

    def test_close_cancels_pending_render_before_context_cleanup(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(120,"Old"));ui:tick(work_budget(1))
        local host=ui.host
        ui:close("world_changed")
        assert(not ui:is_rendering() and ui.model==nil and ui.render_job==nil)
        local native_objects=#widgets
        for i=1,3 do ui:tick(work_budget(8)) end
        collect_native()
        assert(#widgets==native_objects and not host:IsValid() and move==0 and look==0)
        ''')

    def test_render_failure_after_attaching_partial_row_cleans_the_host(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(2,"Fault"));advance_to_paint_rows()
        local host,tree=ui.host,ui.tree
        fail_text=true;ui:tick(work_budget(1));fail_text=false
        assert(not ui:is_open() and not ui:is_rendering() and ui.status.message:find("text failed",1,true))
        assert(tree.RootWidget==host.default_root and #tree.RootWidget.slots==0 and viewport.added==nil and move==0 and look==0)
        collect_native();assert(not host:IsValid())
        for _,w in ipairs(widgets) do assert(not w:IsValid()) end
        ''')

    def test_nil_model_reset_cancels_the_previous_generation(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(3,"Old"));advance_to_paint_rows();ui:tick(work_budget(1))
        ui:set_model(nil);drain_render()
        assert(ui.model==nil and not ui:is_rendering() and not visible_text_contains("OldPaint"))
        assert(visible_text_contains("No paint snapshot yet"))
        ''')

    def test_native_atomic_row_may_overrun_time_but_no_next_row_starts(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(5,"Timed"));advance_to_paint_rows()
        native_time=0;native_text_cost=.001
        local budget=work_budget(8,.002)
        ui:tick(budget)
        assert(native_time>.002, "an atomic native row cannot be interrupted")
        assert(budget.units==1 and ui:render_progress().paintRows==1 and ui:is_rendering())
        native_text_cost=0;drain_render()
        assert(ui.visible_tiles_by_id.Timed5 and ui.active_paint_cache.ready)
        ''')

    def test_render_without_caller_budget_uses_open_window_deadline(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(6,"Timed"));advance_to_paint_rows()
        local ms=0
        os.clock=function() ms=ms+5;return ms/1000 end
        ui:render_tick()
        assert(ui:render_progress().paintRows==2,"a 5ms clock step fits the default open target")
        ui.config.open_work_budget_ms=3
        ui:render_tick()
        assert(ui:render_progress().paintRows==2,"the explicit open time target must stop the next row")
        ''')

    def test_native_row_limit_applies_with_an_unlimited_shared_budget(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(20,"Rows"));advance_to_paint_rows()
        local budget=work_budget(1000)
        ui:tick(budget)
        assert(budget.units>=2 and budget.units<=3 and ui:render_progress().paintRows==2 and ui:is_rendering())
        ''')

    def test_shared_budget_does_not_depend_on_the_ui_local_clock(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(20,"Clock"))
        clock_reads=0
        os.clock=function() clock_reads=clock_reads+1;error("clock unavailable") end
        ui:tick(work_budget(1))
        assert(ui:is_open() and ui:render_progress().completed==1 and clock_reads==0)
        ''')

    def test_missing_fallback_clock_keeps_the_render_count_bound(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_model(large_model(20,"Clock"))
        os.clock=function() error("clock unavailable") end
        ui:tick()
        assert(ui:is_open() and ui:render_progress().completed==8 and ui:is_rendering())
        assert(ui:render_progress().paintRows==0, "clock failure retains the plain-work count bound")
        ''')

    def test_previous_viewport_setter_sequence_reproduces_negative_origin(self):
        self.lua.execute(r'''
        local host=input:Create(pc,host_type,pc)
        host:SetDesiredSizeInViewport({X=1100,Y=760})
        host:SetAnchorsInViewport({Minimum={X=.5,Y=.5},Maximum={X=.5,Y=.5}})
        host:SetAlignmentInViewport({X=.5,Y=.5})
        host:SetPositionInViewport({X=0,Y=0},false)
        local left=1920*host.viewport_anchors.X+host.viewport_position.X-host.viewport_size.X*host.viewport_alignment.X
        local top=1080*host.viewport_anchors.Y+host.viewport_position.Y-host.viewport_size.Y*host.viewport_alignment.Y
        assert(left==-550 and top==-380)
        ''')

    def test_centered_frame_geometry_matches_canvas_contract_across_dpi(self):
        cases = ((1920, 1080, 1), (3840, 2160, 2), (1920, 1080, 1.5),
                 (1280, 720, 1), (800, 600, 2), (2560, 1600, 1),
                 (2560, 1600, 1.25), (2560, 1600, 1.481))
        for width, height, dpi in cases:
            with self.subTest(width=width, height=height, dpi=dpi):
                self.setUp()
                self.lua.globals().viewport_width = width
                self.lua.globals().viewport_height = height
                self.lua.globals().viewport_dpi = dpi
                self.lua.execute("assert(ui:toggle())")
                fit = self.lua.eval('require("paintmanager.ui").fit_geometry')(width, height, dpi)
                configured = self.lua.eval("configured_frame_rect()")
                for field in ("left", "top", "width", "height"):
                    self.assertAlmostEqual(configured[field], fit[field])
                self.assertAlmostEqual(fit["left"] + fit["width"] / 2, width / dpi / 2)
                self.assertAlmostEqual(fit["top"] + fit["height"] / 2, height / dpi / 2)
                self.assertGreaterEqual(fit["left"], 12 - 1e-8)
                self.assertGreaterEqual(fit["top"], 12 - 1e-8)
                self.assertLessEqual(fit["scale"], 1)
                learn_width = self.lua.eval("ui.detail_learn_box.width") * fit["scale"] * dpi
                self.assertGreaterEqual(learn_width + 1e-8, width / 3)
                self.assertLessEqual(learn_width - width / 3, fit["scale"] * dpi + 1e-8)
                self.assertTrue(self.lua.eval("ui.root==ui.host.default_root and ui.frame_slot.auto_size==false"))
                self.assertTrue(self.lua.eval("ui.root.scale==nil and ui.root.pivot==nil"))
                self.assertEqual(self.lua.eval("event_count('host_position')+event_count('host_size')+event_count('host_anchors')+event_count('host_alignment')"), 0)

    def test_prebuilt_native_root_is_retained_and_only_our_panel_is_removed(self):
        self.lua.execute(r'''
        prebuilt_host=true;native_decoration=true
        assert(ui:toggle())
        local host,root,frame=ui.host,ui.root,ui.frame
        assert(host.slate_root==root and root==host.default_root and host.WidgetTree.RootWidget==root)
        assert(frame.slot.Parent==root and #root.children==2 and not root.cleared)
        assert(ui:close("user"))
        assert(host.WidgetTree.RootWidget==root and not root.cleared)
        assert(#root.children==1 and root.children[1]==host.default_child and frame.slot==nil)
        collect_native();assert(not host:IsValid() and not root:IsValid() and not frame:IsValid())
        ''')

    def test_geometry_logs_once_per_viewport_or_dpi_change(self):
        self.lua.execute(r'''
        assert(ui:toggle())
        assert(#logs==1 and logs[1]:find("nativeRoot=CanvasPanel",1,true))
        assert(logs[1]:find("viewportPx=1920x1080",1,true) and logs[1]:find("expectedLogicalRect=",1,true))
        ui:tick(work_budget(0));ui:tick(work_budget(0));assert(#logs==1)
        viewport_width=1280;viewport_height=720;ui:tick(work_budget(0))
        assert(#logs==2 and logs[2]:find("viewportPx=1280x720",1,true))
        local learn_width = ui.detail_learn_box.width * ui.frame.scale.X
        assert(learn_width>=1280/3 and learn_width<1280/3+1)
        viewport_dpi=1.5;ui:tick(work_budget(0))
        assert(#logs==3 and logs[3]:find("dpi=1.500",1,true))
        ''')

    def test_optional_geometry_logging_failure_does_not_close_the_window(self):
        self.lua.execute(r'''
        ui.callbacks.log=function() error("logger unavailable") end
        assert(ui:toggle());ui:tick(work_budget(1));assert(ui:is_open())
        ''')

    def test_missing_or_non_canvas_native_root_fails_explicitly(self):
        for flag in ("missing_root", "wrong_root"):
            with self.subTest(flag=flag):
                self.setUp()
                self.lua.execute(f"{flag}=true;opened,err=ui:toggle()")
                self.assertFalse(self.lua.eval("opened"))
                self.assertIn("no native CanvasPanel root", self.lua.eval("err"))
                self.assertEqual(self.lua.eval("#widgets"), 0)
                self.assertTrue(self.lua.eval("not pc.bShowMouseCursor and viewport.added==nil"))


if __name__ == "__main__":
    unittest.main()
