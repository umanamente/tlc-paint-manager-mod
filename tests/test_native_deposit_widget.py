"""Dedicated paint button and original Deposit Similar behavior in the focused box."""
import unittest

from lupa.lua54 import LuaRuntime

from test_paint_transfer import FIXTURE, SCRIPTS, TRANSFER


WIDGET_FIXTURE = r'''
local data_path="/Game/Data/UI/OverlayWidgets/DA_Widget_Container.DA_Widget_Container"
overlay_data=obj("VoyageOverlayWidgetData "..data_path,"VoyageOverlayWidgetData")
menu=obj("UserWidget /Game/Map.Map.ContainerMenu","BP_VoyageContainerWidget_C")
menu.class_path="WidgetBlueprintGeneratedClass /Game/UI/Game/HUD/BP_VoyageContainerWidget.BP_VoyageContainerWidget_C"
overlay=obj("UserWidget /Game/Map.Map.Overlay","VoyageOverlayMenuWidget")
local tree=obj("WidgetTree /Game/Map.Map.Overlay.Tree","WidgetTree")
local panel=obj("Widget /Game/Map.Map.Overlay.Root","CanvasPanel")
overlay.RootCanvasPanel=panel
function panel:GetOuter() return tree end
function tree:GetOuter() return overlay end
menu_open=true
function menu:IsVisible() return true end
function menu:GetWidgetOwnerController() return controller end
function menu:IsInViewport() return false end
function menu:GetParent() return panel end
function overlay:IsInViewport() return menu_open end
function overlay:IsVisible() return menu_open end
function overlay:GetOverlayWidgetData() return overlay_data end
function overlay:GetCurrentChildWidget() return menu end
function controller:IsOverlayVisible(data) return data==overlay_data and menu_open end
focus_queries=0
function controller:GetPlayerFocusWidget()
    focus_queries=focus_queries+1
    return menu_open and (focus_override or overlay) or nil
end
local old_find=FindAllOf
overlay_queries=0
function FindAllOf(kind)
    if kind=="BP_VoyageOverlayMenuWidget_C" then overlay_queries=overlay_queries+1;return {overlay} end
    return old_find(kind)
end
local function button(id,enabled)
    local b=obj("Widget /Game/Map.Map.ContainerMenu."..id,"VoyageFocusableButtonWidget")
    b.enabled=enabled
    function b:GetIsEnabled() return self.enabled end
    function b:SetIsEnabled(value) self.enabled=value end
    return b
end
similar=button("DepositSimilarBtn",false);all=button("DepositAllBtn",true)
menu.DepositSimilarBtn,menu.DepositAllBtn=similar,all
menu.WidgetTree=obj("WidgetTree /Game/Map.Map.ContainerMenu.Tree","WidgetTree")
function menu.WidgetTree:GetOuter() return menu end
deposit_row=obj("HorizontalBox /Game/Map.Map.ContainerMenu.DepositAllPanel","HorizontalBox")
function deposit_row:IsA(path) return path=="/Script/UMG.HorizontalBox" end
function deposit_row:GetOuter() return menu.WidgetTree end
deposit_row.children={similar,all}
function deposit_row:AddChild(child)
    assert(child.parent==nil)
    self.children[#self.children+1]=child;child.parent=self
    local slot=obj("HorizontalBoxSlot /Game/Map.Map.ContainerMenu.DynamicSlot","HorizontalBoxSlot")
    function slot:SetPadding(pad) assert(pad.Left==4) end
    return slot
end
menu.DepositAllPanel=deposit_row
local kinds={}
for _,kind in ipairs({"Button","Border","Image","HorizontalBox","SizeBox","TextBlock"}) do
    kinds[obj("Class /Script/UMG."..kind,"Class")]=kind
end
all.MainTB=obj("TextBlock /Game/Map.Map.ContainerMenu.DepositAll.MainTB","TextBlock")
all.MainTB.Font={Size=16,TypefaceFontName="Stock regular"}
EObjectFlags={RF_Transient=64}
function FText(value) return value end
function StaticConstructObject(class,tree,name,flags)
    assert(tree==menu.WidgetTree and flags==EObjectFlags.RF_Transient)
    local kind=assert(kinds[class])
    local b=kind=="Button" and button(name,true) or obj(kind.." /Game/Map.Map.ContainerMenu."..name,kind)
    b.Font,b.children={},{}
    b.WidgetStyle={Normal={},Disabled={},Hovered={OutlineSettings={}},Pressed={OutlineSettings={}}}
    b.Background={OutlineSettings={}}
    function b:AddChild(child)
        self.children[#self.children+1]=child
        child.Slot={
            SetHorizontalAlignment=function(s,x) s.horizontal=x end,
            SetVerticalAlignment=function(s,x) s.vertical=x end,
            SetPadding=function(s,x) s.padding=x end,
        }
        return child.Slot
    end
    function b:SetContent(child) self.child=child;return self:AddChild(child) end
    function b:SetText(value) self.caption=value end
    function b:SetColorAndOpacity(value) self.color=value.SpecifiedColor end
    function b:SetFont(font) self.Font=font end
    function b:SetWidthOverride(value) self.width=value end
    function b:SetHeightOverride(value) self.height=value end
    for _,method in ipairs({"SetAutoWrapText","SetTextOverflowPolicy","SetClipping","SetPadding",
        "SetVisibility","SetJustification"}) do b[method]=function() end end
    function b:IsPressed() return self.pressed==true end
    function b:IsHovered() return self.hovered~=false end
    function b:GetParent() return self.parent end
    function b:RemoveFromParent()
        if self.parent then
            for i,c in ipairs(self.parent.children) do
                if c==self then table.remove(self.parent.children,i);break end
            end
            self.parent=nil
        end
    end
    return b
end
box_images,retired_images=0,0
local resources={bind_deposit_icon=function(_,image)
    assert(image.kind=="Image")
    box_images=box_images+1
    return function() retired_images=retired_images+1 end
end}
function make_deposit_button(tree,serial,key,stock_control)
    assert(tree==menu.WidgetTree and key=="ui.transfer.deposit_all_paints")
    local release,relabel
    paint_button,serial,release,relabel=require("paintmanager.ui").make_deposit_button(tree,serial,key,stock_control,resources)
    return paint_button,serial,release,relabel
end
stock_box,stock_owner=stock("Opened",id1,3,{[0]=can(A,800)})
stock_box.Access=2
-- Opening the stock box disables world interaction while its lid is open.
stock_owner.InteractEnabled=false
bag.slots={[4]=can(B,900)}
menu.InventoryWidget=obj("InventoryWidget /Game/Map.Map.ContainerMenu.InventoryWidget","VoyageInventoryWidget")
menu.PlayerInventoryWidget=obj("InventoryWidget /Game/Map.Map.ContainerMenu.PlayerInventoryWidget","VoyageInventoryWidget")
function menu.InventoryWidget:GetSourceInventory() return stock_box end
function menu.PlayerInventoryWidget:GetSourceInventory() return bag end
function menu:GetSourceInventory() return stock_box end
hooks,unhooked,hook_counts={},{},{}
local next_frame=0
require("paintmanager.clock").frame_count=function()
    if unobserved_frame then return nil end
    if controlled_frame then return controlled_frame end
    next_frame=next_frame+1
    return next_frame
end
function RegisterHook(k,pre,post)
    assert(not hooks[k], "Duplicate hook on "..k)
    hook_counts[k]=(hook_counts[k] or 0)+1
    local id=2*hook_counts[k]
    hooks[k]={pre=pre,post=post,id=id};return id,id+1
end
function UnregisterHook(k,a,b)
    assert(hooks[k] and a==hooks[k].id and b==a+1)
    hooks[k]=nil;unhooked[#unhooked+1]=k
end
deposit_key="/Script/Voyage.VoyageContainerWidget:OnDepositSimilarButtonClicked"
inventory_key="/Script/Voyage.VoyageContainerWidget:OnInventoryChanged"
release_key="/Script/Voyage.VoyageBaseUserWidget:OnReleaseInteractiveSource"
function cycle(w)
    for _=1,8 do if w:active() then w:step() end end
end
function click_paint(w)
    assert(paint_button and paint_button.parent and paint_button:GetIsEnabled())
    paint_button.pressed=true;w:step()
    paint_button.pressed=false;w:step()
end
function settings(stock,custom)
    return {depositSimilarAllColors=stock,showDepositAllPaints=custom}
end
'''


class NativeDepositWidgetTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + TRANSFER + WIDGET_FIXTURE)
        self.lua.execute('''
        local widget=require("paintmanager.native_deposit_widget")
        submitted,offers,prepared,cancelled,activated=0,0,0,0,0
        callbacks={
            game_build=function() return config.reference_build end,
            guard=function(t) return t==token and allowed end,
            can_offer=function(_,endpoints)
                offers=offers+1
                local src,dst=endpoints()
                return transfer.inventory_has_paint(src,config) and transfer.inventory_has_paint(dst,config),
                    dst:GetNextEmptySlot()>=0
            end,
            make_button=make_deposit_button,
            activate=function(t,endpoints)
                local ok,src,dst=pcall(endpoints)
                if ok and t==token and src==bag and dst==stock_box then
                    activated=activated+1;last_endpoints=endpoints
                end
            end,
            prepare=function(t,endpoints)
                prepared=prepared+1;local src,dst=endpoints()
                assert(t==token and src==bag and dst==stock_box)
                return {source=src.address,destination=dst.address}
            end,
            submit=function(snapshot,endpoints)
                local src,dst=endpoints()
                assert(snapshot.source==src.address and snapshot.destination==dst.address)
                submitted=submitted+1;last_endpoints=endpoints
            end,
            cancel=function() cancelled=cancelled+1 end}
        w=widget.install(config,callbacks)
        w:set_settings(settings(true,true))
        ''')

    def test_first_open_without_exact_match_enables_only_actual_button_for_key_and_mouse(self):
        self.lua.execute('''
        assert(not w:active() and not similar.enabled and all.enabled)
        w:probe(controller);cycle(w)
        assert(focus_queries>0 and overlay_queries==0 and similar.enabled and all.enabled and prepared==0)
        assert(not hooks["/Script/Voyage.VoyageContainerWidget:OnDepositAllButtonClicked"])
        for _,mouse in ipairs({false,true}) do
            local event=hooks[deposit_key]
            event.pre(wrap(menu),wrap(all),{bMouseInput=mouse});event.post(wrap(menu),wrap(all))
            assert(prepared==submitted)
            event.pre(wrap(menu),wrap(similar),{bMouseInput=mouse});assert(prepared==submitted+1)
            event.post(wrap(menu),wrap(similar));assert(submitted==prepared)
        end
        assert(submitted==2 and w:active())
        hooks[release_key].pre(wrap(menu))
        assert(not similar.enabled and all.enabled and not w:active())
        ''')

    def test_poll_releases_icon_when_native_button_disappears(self):
        self.lua.execute('''
        w:set_settings(settings(false,true));w:probe(controller);w:step(nil,16)
        assert(box_images==1 and retired_images==0 and paint_button.parent==deposit_row)
        paint_button:RemoveFromParent();paint_button.valid=false
        w:step(nil,16)
        assert(not w.paint and retired_images==1)
        w:step(nil,16)
        assert(box_images==2 and retired_images==1 and paint_button.parent==deposit_row)
        w:set_settings(nil);w:close()
        assert(retired_images==2)
        ''')

    def test_language_change_updates_existing_native_box_button_without_a_transfer(self):
        self.lua.execute('''
        local messages=require("paintmanager.i18n")
        w:probe(controller);cycle(w)
        local original=paint_button
        local label=paint_button.children[1].children[2]
        assert(label.caption==messages.text("ui.transfer.deposit_all_paints"))
        messages.select("ru","en-US");cycle(w)
        assert(paint_button==original and label.caption==messages.text("ui.transfer.deposit_all_paints"))
        assert(activated==0 and submitted==0 and box_images==1 and retired_images==0)
        ''')

    def test_creation_releases_icon_if_new_button_has_no_runtime_identity(self):
        self.lua.execute('''
        w:set_settings(settings(false,true))
        local factory=callbacks.make_button
        callbacks.make_button=function(...)
            local b,serial,release=factory(...)
            function b:GetAddress() error("Native widget identity unavailable") end
            return b,serial,release
        end
        w:probe(controller);w:step(nil,16)
        assert(box_images==1 and retired_images==1 and not w.paint and #deposit_row.children==2)
        w:set_settings(nil);w:close();assert(retired_images==1)
        ''')

    def test_pending_profile_and_all_toggle_combinations_in_one_open_box(self):
        self.lua.execute('''
        w:close()
        local widget=require("paintmanager.native_deposit_widget")
        w=widget.install(config,callbacks)
        local registered_before=hook_counts[deposit_key]
        w:set_settings(nil);w:probe(controller);w:step()
        assert(not next(hooks) and focus_queries==0 and not w:active() and #deposit_row.children==2)
        w:set_settings(settings(false,true));w:probe(controller);cycle(w)
        assert(not next(hooks) and #deposit_row.children==3 and not similar.enabled)
        local first=paint_button
        w:set_settings(settings(true,true));cycle(w)
        assert(hook_counts[deposit_key]==registered_before+1 and similar.enabled and paint_button==first)
        local stale=hooks[deposit_key]
        stale.pre(wrap(menu),wrap(similar));assert(prepared==1)
        w:set_settings(settings(false,true))
        assert(not next(hooks) and not similar.enabled and first.parent==deposit_row)
        w:set_settings(settings(true,false));w:set_settings(settings(true,false));cycle(w)
        assert(not first.parent and hook_counts[deposit_key]==registered_before+2 and similar.enabled)
        stale.pre(wrap(menu),wrap(similar));stale.post(wrap(menu),wrap(similar))
        assert(prepared==1 and submitted==0)
        hooks[deposit_key].pre(wrap(menu),wrap(similar));hooks[deposit_key].post(wrap(menu),wrap(similar))
        assert(prepared==2 and submitted==1)
        w:set_settings(settings(false,false))
        local old_queries=focus_queries
        for n=1,10 do w:probe(controller);w:step() end
        assert(not w:active() and not w.visible and not next(hooks) and not similar.enabled and focus_queries==old_queries)
        w:set_settings(settings(true,true));w:probe(controller);cycle(w);assert(paint_button.parent and similar.enabled)
        w:set_settings(nil);assert(not next(hooks) and not paint_button.parent and not similar.enabled)
        ''')

    def test_native_state_wins_when_exact_overlap_becomes_available(self):
        self.lua.execute('''
        w:probe(controller);cycle(w);assert(similar.enabled and w.owned)
        local refresh=hooks[inventory_key]
        refresh.pre(wrap(menu));assert(not similar.enabled and not w.owned)
        similar:SetIsEnabled(true) -- the game found its own exact-item match
        refresh.post(wrap(menu));cycle(w)
        assert(similar.enabled and not w.owned and paint_button.parent==deposit_row)
        w:close();assert(similar.enabled and not next(hooks))
        ''')

    def test_focus_loss_without_hook_and_refresh_timing_across_frame_rates(self):
        self.lua.execute('''
        w:set_settings(settings(false,true))
        assert(not w.visible)
        w:probe(controller);w:step(nil,16)
        assert(w.visible and offers==1)
        for i=1,30 do w:step(nil,4) end
        w:step(nil,129);assert(offers==1)
        w:step(nil,1);assert(offers==2)
        menu_open=false;w:step(nil,1)
        assert(not w.visible and not w:active() and offers==2)
        assert(overlay_queries==0)
        ''')

    def test_deferred_native_result_survives_disable_on_open_and_after_inventory_refresh(self):
        self.lua.execute('''
        controlled_frame=10
        w:probe(controller);cycle(w)
        assert(not similar.enabled and not w.owned and paint_button.parent)
        controlled_frame=11;w:step(nil,16)
        similar:SetIsEnabled(true) -- stock's next-world-tick calculation
        controlled_frame=12;w:step(nil,16)
        assert(not w.owned and similar.enabled)
        similar:SetIsEnabled(false);w:step(nil,300);assert(w.owned)
        local h=hooks[inventory_key]
        controlled_frame=20
        h.pre(wrap(menu));h.post(wrap(menu));w:step(nil,16)
        assert(not w.owned and not similar.enabled)
        controlled_frame=21;w:step(nil,16)
        similar:SetIsEnabled(true)
        w:set_settings(settings(false,true))
        assert(similar.enabled and not w.owned and not hooks[deposit_key])
        ''')

    def test_missing_frame_counter_keeps_stock_enablement_native_and_independent_button_usable(self):
        self.lua.execute('''
        unobserved_frame=true
        w:probe(controller);w:step(nil,16);w:step(nil,500)
        assert(not w.owned and not similar.enabled and paint_button.parent)
        click_paint(w);assert(activated==1)
        similar:SetIsEnabled(true)
        local h=hooks[deposit_key]
        h.pre(wrap(menu),wrap(similar));h.post(wrap(menu),wrap(similar))
        assert(submitted==1)
        similar:SetIsEnabled(false)
        unobserved_frame=false;controlled_frame=100;w:step(nil,500);assert(not w.owned)
        controlled_frame=102;w:step(nil,16);assert(w.owned and similar.enabled)
        ''')

    def test_closed_missing_marker_wrong_backpack_locked_and_read_only_never_offer(self):
        self.lua.execute('''
        menu_open=false;w:probe(controller)
        assert(overlay_queries==0 and not w:active())
        menu_open=true
        local h=hooks[inventory_key]
        for _,fail in ipairs({"marker","locked","access","bag","identity"}) do
            local saved=stock_box.slots
            if fail=="marker" then stock_box.slots={}
            elseif fail=="locked" then stock_owner.IsLocked=true
            elseif fail=="access" then stock_box.Access=0
            elseif fail=="bag" then menu.PlayerInventoryWidget.GetSourceInventory=function() return stock_box end
            else menu.InventoryWidget.GetSourceInventory=function() return bag end end
            h.post(wrap(menu));cycle(w);assert(not similar.enabled,fail)
            stock_box.slots=saved;stock_owner.IsLocked=false;stock_box.Access=2;all.enabled=true
            menu.PlayerInventoryWidget.GetSourceInventory=function() return bag end
            menu.InventoryWidget.GetSourceInventory=function() return stock_box end
        end
        assert(submitted==0 and prepared==0)
        ''')

    def test_rebinding_between_pre_and_post_or_closing_after_submit_rejects_endpoints(self):
        self.lua.execute('''
        w:probe(controller);cycle(w)
        local h=hooks[deposit_key]
        h.pre(wrap(menu),wrap(similar));assert(prepared==1)
        local old=stock_box
        stock_box=stock("Replacement",id2,1,{[0]=can(B,1)});stock_box.Access=2
        h.post(wrap(menu),wrap(similar));assert(submitted==0)
        stock_box=old
        h.pre(wrap(menu),wrap(similar));h.post(wrap(menu),wrap(similar));assert(submitted==1)
        menu_open=false;assert(not pcall(last_endpoints));w:close()
        assert(not similar.enabled and not next(hooks))
        ''')

    def test_dedicated_button_uses_owned_row_and_direct_release_with_both_stock_controls_disabled(self):
        self.lua.execute('''
        w:close();RegisterHook=nil
        w=require("paintmanager.native_deposit_widget").install(config,callbacks)
        w:set_settings(settings(true,true))
        all.enabled=false
        -- The focused menu can sit under a different layout than the root canvas.
        function menu:GetParent() error("The stock focus path has no parent-layout dependency") end
        function controller:IsOverlayVisible() error("The focused widget identifies the container") end
        w:probe(controller);cycle(w)
        assert(not next(hooks) and not similar.enabled and not all.enabled)
        assert(#deposit_row.children==3 and deposit_row.children[1]==similar
            and deposit_row.children[2]==all and deposit_row.children[3]==paint_button)
        paint_button.pressed=true;w:step();w:step();assert(activated==0)
        paint_button.pressed=false;w:step();w:step();assert(activated==1)
        paint_button.pressed=true;w:step();paint_button.hovered=false;paint_button.pressed=false;w:step()
        assert(activated==1 and prepared==0 and submitted==0)
        w:close();assert(not last_endpoints or not pcall(last_endpoints))
        assert(#deposit_row.children==2 and not paint_button.parent and not similar.enabled and not all.enabled)
        ''')

    def test_inventory_events_add_remove_and_preserve_one_button_with_full_box_visible(self):
        self.lua.execute('''
        local changed=hooks[inventory_key]
        local function refresh()
            for i=1,4 do changed.pre(wrap(menu));changed.post(wrap(menu)) end
            w:step()
        end
        local saved=bag.slots
        bag.slots={};w:probe(controller);cycle(w);assert(#deposit_row.children==2)
        bag.slots=saved;refresh()
        assert(#deposit_row.children==3 and paint_button.enabled)
        local first=paint_button
        stock_box.limit=1;refresh()
        assert(paint_button==first and paint_button.parent==deposit_row and not paint_button.enabled)
        stock_box.limit=12;refresh()
        assert(paint_button==first and paint_button.enabled)
        stock_box.slots[0]=nil;refresh();assert(#deposit_row.children==2 and not paint_button.parent)
        stock_box.slots[1]=can(A,12);refresh();assert(#deposit_row.children==3 and paint_button~=first)
        menu_open=false
        for i=1,36 do w:step() end
        assert(not w:active() and #deposit_row.children==2)
        ''')

    def test_dedicated_release_rejects_changed_box_and_unfocused_menu_without_waiting_for_refresh(self):
        self.lua.execute('''
        w:probe(controller);cycle(w)
        paint_button.pressed=true;w:step()
        local first=stock_box
        stock_box=stock("Replacement",id2,1,{[0]=can(B,1)});stock_box.Access=2
        paint_button.pressed=false;w:step();assert(activated==0)
        hooks[inventory_key].post(wrap(menu));w:step()
        click_paint(w);assert(activated==1 and pcall(last_endpoints))
        focus_override=menu
        assert(not pcall(last_endpoints))
        paint_button.pressed=true;w:step();paint_button.pressed=false;w:step()
        assert(activated==1)
        focus_override=nil;stock_box=first;assert(not pcall(last_endpoints))
        ''')

    def test_focused_nested_overlay_unbound_panes_and_missing_original_controls_can_deposit(self):
        self.lua.execute('''
        function overlay:IsInViewport() return false end
        menu.PlayerInventoryWidget=nil
        function menu.InventoryWidget:GetSourceInventory() return nil end
        menu.DepositAllBtn,menu.DepositSimilarBtn=nil,nil
        w:probe(controller);cycle(w)
        assert(paint_button.parent==deposit_row and paint_button.enabled)
        click_paint(w);assert(activated==1)
        function menu.InventoryWidget:GetSourceInventory() return bag end
        assert(not pcall(last_endpoints))
        local logs={}
        callbacks.log=function(s) logs[#logs+1]=s end
        local refresh=hooks[inventory_key]
        for i=1,4 do refresh.post(wrap(menu));w:step() end
        assert(not paint_button.parent and #logs==1 and logs[1]:find("does not show its source box",1,true))
        ''')

    def test_focused_writable_box_accepts_paint_after_world_interaction_is_disabled(self):
        self.lua.execute('''
        w:close()
        assert(stock_owner.InteractEnabled==false)
        assert(not transfer.accessible_box(stock_box,collection.native_context()))
        local can_blue=bag.slots[4]
        local iron=entry(obj("VoyageItem /Game/Items.Iron","VoyageItem"),3)
        bag.slots[1]=iron
        local logs={}
        local service=require("paintmanager.deposit_similar").install(config,{
            game_build=function() return config.reference_build end,
            guard=function(t) return t==token end, make_button=make_deposit_button,
            log=function(s) logs[#logs+1]=s end,
        })
        service:set_settings(settings(false,true))
        local editor=require("paintmanager.native_editor").new(config,{
            observe_container=function(pc) service:probe(pc) end,log=function(s) logs[#logs+1]=s end})
        editor:tick();cycle(service)
        assert(paint_button and paint_button.parent==deposit_row and #deposit_row.children==3,
            table.concat(logs," / "))
        local tile,label=table.unpack(paint_button.child.children)
        assert(label.caption=="Deposit all paints" and paint_button.enabled and moves==0)
        assert(label.Font==all.MainTB.Font and label.Slot.padding.Left==9 and label.color.A==1)
        assert(tile.width==42 and tile.height==44 and tile.child.Background.OutlineSettings.CornerRadii.X==5)
        assert(tile.child.Background.TintColor.SpecifiedColor.R==0.78354)
        assert(tile.child.child.width==32 and tile.child.child.height==32 and tile.child.child.child.kind=="Image")
        local style=paint_button.WidgetStyle
        assert(not paint_button.IsFocusable and style.Normal.DrawAs==0 and style.Disabled.DrawAs==0)
        assert(style.Hovered.DrawAs==4 and style.Pressed.DrawAs==4 and style.Pressed.TintColor.SpecifiedColor.R>style.Hovered.TintColor.SpecifiedColor.R)
        assert(box_images==1 and retired_images==0)
        click_paint(service);cycle(service)
        assert(moves==1 and bag.slots[4]==nil and bag.slots[1]==iron)
        assert(stock_box.slots[1]==can_blue and stock_box.slots[0].Item:get()==A and transfer.can_transfer(token))
        for i=1,35 do service:step() end
        assert(not paint_button.parent and #deposit_row.children==2 and retired_images==1)
        service:close()
        ''')

    def test_original_put_similar_deposits_other_colors_after_vanilla_processes_matching_items(self):
        for exact_match in (True, False):
            with self.subTest(exact_match=exact_match):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.globals().scripts_path = SCRIPTS.as_posix()
                lua.globals().exact_match = exact_match
                lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
                lua.execute(FIXTURE + TRANSFER + WIDGET_FIXTURE)
                lua.execute('''
                local amber=can(A,18)
                local iron=obj("VoyageItem /Game/Items.Iron","VoyageItem")
                local copper=obj("VoyageItem /Game/Items.Copper","VoyageItem")
                local matching_iron,unmatched_copper=entry(iron,3),entry(copper,5)
                local blue=bag.slots[4]
                bag.slots[1],bag.slots[2]=matching_iron,unmatched_copper
                if exact_match then
                    stock_box.slots[5]=entry(iron,1)
                    bag.slots[0]=amber
                end
                similar.enabled=exact_match
                local changes=0
                local service=require("paintmanager.deposit_similar").install(config,{
                    game_build=function() return config.reference_build end,
                    guard=function(t) return t==token end, make_button=make_deposit_button,
                    changed=function() changes=changes+1 end,
                })
                assert(stock_owner.InteractEnabled==false)
                service:set_settings(settings(true,false))
                service:probe(controller);cycle(service)
                assert(similar.enabled and (service.widget.owned~=nil)==(not exact_match))
                local event=hooks[deposit_key]
                event.pre(wrap(menu),wrap(similar),{bMouseInput=true})
                assert(moves==0 and not service.job)
                if exact_match then
                    -- The game processes its exact paint and ordinary-item matches.
                    stock_box:TransferSlot(bag,0,1,1)
                    stock_box:TransferSlot(bag,1,2,3)
                end
                local native_moves=moves
                event.post(wrap(menu),wrap(similar));assert(moves==native_moves)
                cycle(service)
                assert(changes==1 and moves==native_moves+1)
                assert(not bag.slots[4] and stock_box.slots[exact_match and 3 or 1]==blue)
                assert(bag.slots[2]==unmatched_copper and bag.slots[2].Data.ItemCount==5)
                if exact_match then
                    assert(native_moves==2 and not bag.slots[0] and not bag.slots[1])
                    assert(stock_box.slots[1]==amber and stock_box.slots[2]==matching_iron)
                else
                    assert(native_moves==0 and bag.slots[1]==matching_iron)
                end
                assert(transfer.can_transfer(token))
                service:close()
                ''')
