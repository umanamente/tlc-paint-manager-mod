"""Configuration UI fixtures; actual native input/layout still require runtime QA."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import CLICK_TIMING_FIXTURE, FIXTURE, SCRIPTS


PRESET_FIXTURE = r'''
preset_requests={}
for _,command in ipairs({"preset_refresh","preset_save","preset_apply","preset_delete","preset_import"}) do
    local kind=command
    ui.callbacks[kind]=function(...)
        preset_requests[#preset_requests+1]={command=kind,args={...}}
        if reject_preset then return false,"Rejected for test" end
        if throw_preset then error("Request error") end
        return true
    end
end
local json=require("paintmanager.json")
local paint_ids={
    patrol={assetType="Item",assetName="DA_Paint_LifeGuard",displayName="Patrol"},
    beat={assetType="Item",assetName="DA_Paint_WallArt",displayName="Beat Tag"},
    north={assetType="Item",assetName="DA_Paint_Suomi",displayName="North home"},
    flag={assetType="Item",assetName="DA_Paint_Italy",displayName="Old Flag"},
    stamp={assetType="Item",assetName="DA_Paint_Moses",displayName="Exodus Stamp"},
}
local zone_materials={
    Deck="M_Deck",Tower="Tower_Material",Hull="M_Hull",Flag="FlagMat",
    Head="MI_Player_Character_Head",Scarf="MI_Player_Character_Scarf",
    Torso="MI_Player_Character_Torso",Arms="MI_Player_Character_Arms",Legs="MI_Player_Character_Legs",
}
local ship_compat={kind="boat",className="/Game/Boat.Test_C",paintSetName="Barge",displayName="Barge"}
local character_compat={kind="character",className="/Game/Character.Test_C",paintSetName="Caretaker",displayName="Caretaker"}
paint_textures={}
local paths={}
for key,paint in pairs(paint_ids) do
    local path="/Game/Fixture/"..paint.assetName.."."..paint.assetName
    local texture=object("Texture")
    texture.fullname="Texture2D "..path
    paint_textures[key]=texture
    paths[path]=texture
end
local native_find=StaticFindObject
function StaticFindObject(path)
    return paths[path] or native_find(path)
end
local collection=require("paintmanager.collection")
function collection.loaded_paint_icon(asset_type, asset_name)
    for key,paint in pairs(paint_ids) do
        if paint.assetType==asset_type and paint.assetName==asset_name then
            return paint_textures[key]:GetFullName(),paint.displayName
        end
    end
end
local function zone(name, paint)
    return {materialSlotName=zone_materials[name],partName=name,displayName=name,paint=paint_ids[paint] or json.null}
end
local function ship_zones(flag_paint)
    return {zone("Hull","north"),zone("Flag",flag_paint),zone("Deck","patrol"),zone("Tower","beat")}
end
local function character_zones(scarf_paint,arms_paint)
    return {zone("Head","north"),zone("Scarf",scarf_paint),zone("Arms",arms_paint),
        zone("Legs","north"),zone("Torso","flag")}
end
local function target(id, owner, compatibility, logical_zones)
    local display_zones={}
    for index,entry in ipairs(logical_zones) do
        local empty=entry.paint==json.null
        display_zones[index]={label=entry.displayName,paintLabel=empty and "Empty" or entry.paint.displayName,
            occupancy=empty and "empty" or "occupied"}
    end
    return {id=id,ownerRuntimeRef={id=owner},kind=compatibility.kind,label=compatibility.displayName,
        complete=true,zones=display_zones,compatibility=compatibility,logicalZones=logical_zones}
end
function presets_view(count)
    local presets={}
    for i=1,(count or 2) do
        presets[i]={id="Preset"..i,name="Saved "..i,target=ship_compat,zones=ship_zones(i>1 and "flag" or nil)}
    end
    if not count then
        presets[#presets+1]={id="Character1",name="Shore Detail",target=character_compat,zones=character_zones("flag")}
        presets[#presets+1]={id="Character2",name="Deck Runner",target=character_compat,zones=character_zones("flag","stamp")}
    end
    return {contextToken="Context",targets={
        target("Character","PlayerOwner",character_compat,character_zones()),
        target("NearbyBoat","UnrelatedOwner",ship_compat,ship_zones("flag")),
        target("Boat","ShipOwner",ship_compat,ship_zones()),
    },library={schemaVersion=1,presets=presets}}
end
function open_presets(view)
    ui:set_connection({connected=true,shipOwnerId="ShipOwner"})
    assert(ui:toggle());ui:set_presets(view or presets_view());ui:_select_tab("presets");drain_render()
end
function click_control(name)
    local button=assert(ui.preset_controls[name], "Missing control: "..name)
    button.widget.down=true;ui:tick(work_budget(8))
    button.widget.down=false;ui:tick(work_budget(8))
end
function choose_target(control)
    click_control(control or "character");drain_render()
end
function preset_buttons()
    local result={}
    for _,button in ipairs(ui.buttons) do if button.kind=="preset_select" then result[#result+1]=button end end
    return result
end
function preset_button(label)
    for _,button in ipairs(preset_buttons()) do if button.label.text==label then return button end end
    error("Missing preset: "..label)
end
function select_preset(label)
    preset_button(label or "Saved 1").action();drain_render()
end
function nodes(root, predicate)
    local found,seen={},{}
    local function visit(node)
        if not node or seen[node] then return end
        seen[node]=true
        if predicate(node) then found[#found+1]=node end
        visit(node.child)
        for _,slot in ipairs(node.slots or {}) do visit(slot.Content) end
    end
    visit(root)
    return found
end
function preset_has(root, value)
    return #nodes(root,function(node) return type(node.text)=="string" and node.text:find(value,1,true) end)>0
end
function assert_zone_order(root,...)
    local labels=nodes(root,function(node) return type(node.text)=="string" end)
    local index=1
    for _,name in ipairs({...}) do
        while labels[index] and not labels[index].text:find(name,1,true) do index=index+1 end
        assert(labels[index],"Missing or out-of-order zone: "..name)
        index=index+1
    end
end
function shown(widget)
    return widget and widget.visible~=1 and widget.visible~=2
end
function current_label(label)
    local mark=preset_button(label).current_label
    return shown(mark) and mark.text=="Current" or false
end
'''


class UIPresetsTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PRESET_FIXTURE)

    def test_setter_queues_plain_state_and_ship_is_default_without_implicit_preset_selection(self):
        self.lua.execute('''
        ui:set_presets(presets_view());assert(#calls==0 and #widgets==0)
        open_presets()
        assert(ui.active_tab=="presets" and ui.preset_kind=="boat" and ui.selected_preset_id==nil)
        assert(not ui.preset_controls.save.widget:GetIsEnabled() and not ui.preset_controls.apply.widget:GetIsEnabled())
        assert(#preset_buttons()==2 and current_label("Saved 1") and #preset_requests==0)
        ''')

    def test_cold_preset_click_waits_for_visible_rows_and_warm_page_returns_immediately(self):
        self.lua.execute(CLICK_TIMING_FIXTURE + '''
        ui:set_connection({connected=true,shipOwnerId="ShipOwner"})
        assert(ui:toggle());press_tab("presets");drain_render()
        assert(#click_logs()==0,"Initial prompt completed an unrefreshed page")
        ui:set_presets(presets_view(12))
        for _=1,1000 do
            if shown(ui.preset_list) then break end
            assert(#click_logs()==0)
            now_ms=now_ms+1;ui:render_tick(work_budget(1))
        end
        assert(shown(ui.preset_list) and #preset_buttons()==12)
        assert_timing(1,"Presets",now_ms-1000,"list",false)
        press_tab("settings");press_tab("presets")
        assert(shown(ui.preset_list));assert_timing(3,"Presets",0,"list",true)
        ''')

    def test_empty_preset_click_finishes_on_the_final_notice(self):
        self.lua.execute(CLICK_TIMING_FIXTURE + '''
        ui:set_connection({connected=true,shipOwnerId="ShipOwner"})
        assert(ui:toggle());press_tab("presets")
        ui:set_presets(presets_view(0))
        local empty=require("paintmanager.i18n").text("ui.preset.none_saved")
        for _=1,1000 do
            if shown(ui.preset_notice) and ui.preset_notice.text==empty then break end
            assert(#click_logs()==0)
            now_ms=now_ms+1;ui:render_tick(work_budget(1))
        end
        assert(shown(ui.preset_notice) and ui.preset_notice.text==empty)
        assert_timing(1,"Presets",now_ms-1000,"empty",false)
        drain_render();assert(#click_logs()==1)
        ''')

    def test_leaving_presets_parks_unfinished_widgets_until_the_tab_is_visible_again(self):
        self.lua.execute('''
        open_presets()
        ui:set_presets(presets_view(20))
        local parked=assert(ui.preset_render_job)
        local before=parked.completed
        ui:_select_tab("learned")
        for _=1,80 do ui:render_tick(work_budget(8)) end
        assert(not ui:is_rendering() and ui.preset_render_job==parked and parked.completed==before)
        ui:_select_tab("presets");drain_render();assert(#preset_buttons()==20 and not ui.preset_render_job)
        ''')

    def test_save_shows_pending_immediately_and_patches_committed_id_without_rebuilding(self):
        self.lua.execute('''
        local view=presets_view();local base={};view.storageSnapshot=base
        open_presets(view);choose_target();select_preset("Shore Detail")
        local list,row,detail,generation=ui.preset_list,ui.preset_rows.Character1,ui.preset_detail_zones,ui.preset_generation
        ui.preset_name_box:SetText("Shore Detail")
        click_control("save")
        assert(shown(list) and preset_has(list,"Saving Shore Detail") and ui.preset_scroll.scrolled_to_end)
        assert(ui.preset_rows.Character1==row and not ui.preset_projection.by_id.SavedUUID)
        local fresh=presets_view()
        local current=fresh.targets[1]
        fresh.library.presets[#fresh.library.presets+1]={id="SavedUUID",name="Shore Detail",
            target=current.compatibility,zones=current.logicalZones}
        ui:set_presets(fresh,"SavedUUID",base);ui:set_learning(false)
        assert(not ui.preset_pending and ui.preset_render_job==nil and ui.preset_generation==generation)
        assert(shown(list) and ui.preset_rows.Character1==row and ui.preset_detail_zones==detail)
        assert(ui.selected_preset_id=="Character1" and ui.preset_detail_title.text=="Shore Detail [Character1]")
        assert(not preset_has(list,"Saving") and current_label("Shore Detail [SavedUUID]"))
        assert(ui.preset_rows.SavedUUID.widget:GetIsEnabled() and #preset_buttons()==3)
        ''')

    def test_save_rejection_or_fresh_existing_match_never_leaves_a_phantom_row(self):
        self.lua.execute('''
        local view=presets_view();open_presets(view);choose_target();select_preset("Deck Runner")
        local list,row,selected=ui.preset_list,ui.preset_rows.Character1,ui.selected_preset_id
        ui.preset_name_box:SetText("Candidate");click_control("save");assert(preset_has(list,"Saving Candidate"))
        ui:set_status({command="preset_save",state="error",message="Disk error"});ui:set_learning(false)
        assert(not preset_has(list,"Candidate") and #preset_buttons()==2 and ui.selected_preset_id==selected)
        click_control("save");local fresh=presets_view()
        fresh.targets[1].logicalZones=fresh.library.presets[3].zones
        ui:set_presets(fresh,"Character1");ui:set_learning(false)
        assert(shown(list) and ui.preset_rows.Character1==row and current_label("Shore Detail"))
        assert(not preset_has(list,"Candidate") and #preset_buttons()==2)
        drain_render();assert(ui.selected_preset_id==selected and ui.preset_rows.Character1==row)
        ''')

    def test_ship_uses_connected_owner_and_kind_switch_filters_saved_presets(self):
        self.lua.execute('''
        open_presets()
        assert(preset_has(ui.preset_summary,"Saved configurations (2)"))
        assert(current_label("Saved 1") and not current_label("Saved 2"))
        assert(not preset_has(ui.preset_list,"Shore Detail"))
        choose_target("character")
        assert(ui.preset_kind=="character" and #preset_buttons()==2)
        assert(preset_has(ui.preset_list,"Shore Detail") and not preset_has(ui.preset_list,"Saved 1"))
        assert(not current_label("Shore Detail") and not current_label("Deck Runner"))
        assert(shown(ui.preset_save_row) and ui.preset_controls.save.widget:GetIsEnabled())
        choose_target("ship")
        assert(ui.preset_kind=="boat" and current_label("Saved 1") and not shown(ui.preset_save_row))
        assert(#preset_requests==0)
        ''')

    def test_selected_preset_details_use_native_names_and_requested_zone_order(self):
        self.lua.execute('''
        open_presets();select_preset()
        assert(ui.selected_preset_id=="Preset1")
        assert_zone_order(ui.preset_detail_zones,"Deck","Tower","Hull","Flag")
        assert(preset_has(ui.preset_detail_zones,"Patrol") and preset_has(ui.preset_detail_zones,"Empty"))
        assert(not ui.preset_controls.apply.widget:GetIsEnabled())
        choose_target();select_preset("Deck Runner")
        assert_zone_order(ui.preset_detail_zones,"Head","Scarf","Torso","Arms","Legs")
        assert(preset_has(ui.preset_detail_zones,"Exodus Stamp") and ui.preset_controls.apply.widget:GetIsEnabled())
        assert(#preset_requests==0)
        ''')

    def test_game_thumbnails_bind_native_textures_in_rows_and_lower_preview(self):
        self.lua.execute('''
        open_presets();select_preset()
        local function bound_game_textures(root)
            local bound={}
            for _,node in ipairs(nodes(root,function(el) return el.kind=="Image" and el.texture~=nil end)) do
                for _,texture in pairs(paint_textures) do
                    if node.texture==texture then bound[#bound+1]=texture end
                end
            end
            return bound
        end
        local row=bound_game_textures(preset_button("Saved 1").widget)
        local details=bound_game_textures(ui.preset_icon_box)
        local expected={paint_textures.patrol,paint_textures.beat,paint_textures.north}
        assert(#row==3 and #details==3)
        for index,texture in ipairs(expected) do assert(row[index]==texture and details[index]==texture) end
        ''')

    def test_scrollable_list_keeps_save_controls_and_selected_card_outside_list(self):
        self.lua.execute('''
        open_presets(presets_view(64));select_preset("Saved 64")
        local function in_scroll(node)
            while node and node.Slot do
                node=node.Slot.Parent
                if node and node.kind=="ScrollBox" then return node end
            end
        end
        local scroll=assert(in_scroll(ui.preset_list))
        assert(not in_scroll(ui.preset_name_box) and not in_scroll(ui.preset_controls.apply.widget))
        assert(not in_scroll(ui.preset_detail_zones))
        assert(ui.preset_controls.delete.widget.tooltip.child.child.text:find("Permanently delete",1,true))
        assert(#preset_buttons()==64 and preset_has(ui.preset_list,"Saved 64"))
        assert(ui.selected_preset_id=="Preset64" and preset_has(ui.preset_detail_zones,"Old Flag"))
        ui:set_presets(presets_view(2));drain_render()
        assert(scroll==in_scroll(ui.preset_list) and #preset_buttons()==2)
        assert(ui.selected_preset_id==nil)
        assert(#preset_requests==0 and #learned==0)
        ''')

    def test_only_oversized_zone_lists_need_the_inner_scrollbar(self):
        self.lua.execute('''
        local view=presets_view()
        local zones=view.library.presets[2].zones
        for i=1,4 do
            zones[#zones+1]={partName="Extra"..i,materialSlotName="ExtraMat"..i,
                displayName="Extra "..i,paint=require("paintmanager.json").null}
        end
        open_presets(view);select_preset("Saved 2")
        assert(ui.preset_detail_host.child.kind=="ScrollBox")
        assert(preset_has(ui.preset_detail_zones,"Extra 4"))
        select_preset("Saved 1")
        assert(ui.preset_detail_host.child==ui.preset_detail_zones)
        assert(ui.preset_detail_zones.kind=="UniformGridPanel")
        ''')

    def test_current_marker_is_independent_of_selection_and_uses_exact_layout(self):
        self.lua.execute('''
        local view=presets_view()
        local zones=view.library.presets[1].zones
        zones[1],zones[4]=zones[4],zones[1]
        zones[4].displayName="Hull (saved name)"
        open_presets(view)
        assert(current_label("Saved 1") and not current_label("Saved 2"))
        select_preset("Saved 2")
        assert(ui.selected_preset_id=="Preset2" and current_label("Saved 1") and not current_label("Saved 2"))
        assert(not shown(ui.preset_save_row) and ui.preset_controls.apply.widget:GetIsEnabled())
        ui:set_presets(presets_view());drain_render()
        assert(ui.selected_preset_id=="Preset2" and current_label("Saved 1") and not shown(ui.preset_save_row))
        choose_target();select_preset("Shore Detail");choose_target("ship")
        assert(ui.selected_preset_id=="Preset2" and current_label("Saved 1"))
        choose_target();assert(ui.selected_preset_id=="Character1" and shown(ui.preset_save_row))
        ''')

    def test_save_reads_native_ftext_and_sends_only_target_identity_and_exact_name(self):
        self.lua.execute('''
        open_presets();choose_target()
        ui.preset_name_box.text={ToString=function() return "Trip {one} \\u{65E5}" end}
        click_control("save")
        assert(#preset_requests==1 and preset_requests[1].command=="preset_save")
        assert(preset_requests[1].args[1]=="Character" and preset_requests[1].args[2]=="Trip {one} \\u{65E5}")
        assert(#preset_requests[1].args==2 and ui.learning)
        assert(not ui.preset_controls.save.widget:GetIsEnabled() and not bulk_button().widget:GetIsEnabled())
        ''')

    def test_published_matching_configuration_hides_save_and_preserves_selection(self):
        self.lua.execute('''
        open_presets();choose_target();select_preset("Shore Detail")
        assert(shown(ui.preset_save_row))
        local view=presets_view()
        table.insert(view.library.presets,{id="NewCharacter",name="Current outfit",target=view.targets[1].compatibility,
            zones=view.targets[1].logicalZones})
        ui:set_presets(view);drain_render()
        assert(current_label("Current outfit") and ui.selected_preset_id=="Character1")
        assert(not shown(ui.preset_save_row) and not ui.preset_controls.save.widget:GetIsEnabled())
        ui.preset_controls.save.action()
        assert(#preset_requests==0)
        ''')

    def test_apply_dispatches_only_the_explicit_fillable_preset_once_while_held(self):
        self.lua.execute('''
        open_presets();select_preset("Saved 2")
        local button=ui.preset_controls.apply
        button.widget.down=true
        for _=1,10 do ui:tick(work_budget(8)) end
        assert(#preset_requests==1 and preset_requests[1].command=="preset_apply")
        assert(preset_requests[1].args[1]=="Boat" and preset_requests[1].args[2]=="Preset2")
        button.widget.down=false;ui:tick(work_budget(8))
        assert(ui.learning and not ui.preset_controls.character.widget:GetIsEnabled())
        ''')

    def test_apply_enables_occupied_replacement_and_disables_active_match(self):
        self.lua.execute('''
        local view=presets_view()
        local same=view.library.presets[1]
        local zones={}
        for index,zone in ipairs(same.zones) do
            zones[index]={materialSlotName=zone.materialSlotName,partName=zone.partName,
                displayName=zone.displayName,paint=zone.partName=="Deck" and
                    {assetType="Item",assetName="DA_Paint_Unexpected",displayName="Unexpected"} or zone.paint}
        end
        table.insert(view.library.presets,{id="Conflict",name="Conflicting",target=same.target,zones=zones})
        open_presets(view)
        select_preset("Saved 1");assert(not ui.preset_controls.apply.widget:GetIsEnabled())
        ui.preset_controls.apply.action();assert(#preset_requests==0)
        select_preset("Conflicting");assert(ui.preset_controls.apply.widget:GetIsEnabled())
        click_control("apply")
        assert(#preset_requests==1 and preset_requests[1].args[2]=="Conflict")
        ''')

    def test_refresh_is_click_only_and_uses_the_shared_gate(self):
        self.lua.execute('''
        open_presets();assert(#preset_requests==0)
        click_control("refresh")
        assert(#preset_requests==1 and preset_requests[1].command=="preset_refresh" and #preset_requests[1].args==0)
        assert(ui.learning and not ui.preset_controls.refresh.widget:GetIsEnabled())
        ''')

    def test_delete_requires_explicit_saved_selection_without_a_current_target(self):
        self.lua.execute('''
        local view=presets_view();view.targets={};open_presets(view)
        assert(ui.selected_preset_id==nil)
        assert(not ui.preset_controls.delete.widget:GetIsEnabled())
        ui.preset_controls.delete.action();assert(#preset_requests==0)
        select_preset("Saved 2")
        assert(ui.preset_controls.delete.widget:GetIsEnabled())
        assert(not ui.preset_controls.save.widget:GetIsEnabled() and not ui.preset_controls.apply.widget:GetIsEnabled())
        click_control("delete")
        assert(#preset_requests==1 and preset_requests[1].command=="preset_delete")
        assert(#preset_requests[1].args==1 and preset_requests[1].args[1]=="Preset2")
        assert(ui.learning and not ui.preset_controls.delete.widget:GetIsEnabled())
        ''')

    def test_delete_dispatches_once_while_held_and_shares_the_action_gate(self):
        self.lua.execute('''
        open_presets();select_preset()
        local button=ui.preset_controls.delete
        button.widget.down=true
        for _=1,10 do ui:tick(work_budget(8)) end
        assert(#preset_requests==1 and preset_requests[1].command=="preset_delete")
        button.widget.down=false;ui:tick(work_budget(8))
        ui.preset_controls.delete.action();ui.preset_controls.refresh.action()
        assert(#preset_requests==1 and ui.learning)
        assert(not preset_buttons()[1].widget:GetIsEnabled() and not ui.preset_controls.character.widget:GetIsEnabled())
        ui:set_learning(false)
        assert(ui.preset_controls.delete.widget:GetIsEnabled())
        ''')

    def test_delete_rejection_exception_and_missing_callback_release_the_gate(self):
        self.lua.execute('''
        open_presets();select_preset();reject_preset=true
        click_control("delete")
        assert(not ui.learning and ui.status.state=="error" and ui.status.message=="Rejected for test")
        reject_preset=false;throw_preset=true
        click_control("delete")
        assert(not ui.learning and ui.status.message:find("See the log",1,true))
        assert(not ui.status_label.text:find("Request error",1,true) and table.concat(logs):find("Request error",1,true))
        throw_preset=false;ui.callbacks.preset_delete=nil
        click_control("delete")
        assert(not ui.learning and ui.status.message=="Configuration action is unavailable.")
        assert(#preset_requests==2 and ui.preset_controls.delete.widget:GetIsEnabled())
        ''')

    def test_delete_rejects_stale_view_generation_removed_ids_closed_shell_and_world(self):
        self.lua.execute('''
        open_presets();select_preset("Saved 2")
        local generation,shell=ui.preset_generation,ui.preset_shell_generation
        local old_delete=ui.preset_controls.delete.action
        ui:set_presets(presets_view(1));drain_render()
        ui:_preset_action("preset_delete",generation,shell)
        assert(ui.selected_preset_id==nil and not ui.preset_controls.delete.widget:GetIsEnabled())
        ui.preset_controls.delete.action();assert(#preset_requests==0)
        select_preset();ui:close("user");assert(ui:toggle());ui:_select_tab("presets");drain_render()
        assert(ui:needs_preset_refresh())
        ui:set_presets(presets_view(1));drain_render()
        old_delete();assert(#preset_requests==0)
        select_preset();local current_delete=ui.preset_controls.delete.action
        world=object("OtherWorld");current_delete()
        assert(#preset_requests==0 and not ui.learning)
        ''')

    def test_corrupt_library_disables_delete_and_global_learning_gate_blocks_it(self):
        self.lua.execute('''
        local view=presets_view();view.error="Invalid JSON";open_presets(view)
        assert(not ui.preset_controls.delete.widget:GetIsEnabled())
        click_control("delete");assert(#preset_requests==0)
        ui:set_presets(presets_view());drain_render();select_preset()
        assert(ui.preset_controls.delete.widget:GetIsEnabled())
        ui:set_learning(true);ui.preset_controls.delete.action()
        assert(#preset_requests==0 and not ui.preset_controls.delete.widget:GetIsEnabled())
        ui:set_learning(false);assert(ui.preset_controls.delete.widget:GetIsEnabled())
        ''')

    def test_duplicate_names_render_stable_id_suffixes_and_delete_exact_selected_id(self):
        self.lua.execute('''
        local view=presets_view()
        view.library.presets[1].id="same-prefix-a";view.library.presets[1].name="Layout"
        view.library.presets[2].id="same-prefix-b";view.library.presets[2].name="layout"
        open_presets(view)
        assert(#preset_buttons()==2 and preset_has(ui.preset_list,"Layout [same-prefix-a]")
            and preset_has(ui.preset_list,"layout [same-prefix-b]"))
        select_preset("layout [same-prefix-b]")
        assert(ui.selected_preset_id=="same-prefix-b")
        click_control("delete")
        assert(#preset_requests==1 and preset_requests[1].args[1]=="same-prefix-b")
        assert(view.library.presets[1].name=="Layout" and view.library.presets[2].name=="layout")
        ''')

    def test_request_rejection_and_exception_release_the_gate_and_show_reason(self):
        self.lua.execute('''
        open_presets();choose_target();reject_preset=true;click_control("save")
        assert(not ui.learning and ui.status.state=="error" and ui.status.message=="Rejected for test")
        reject_preset=false;throw_preset=true;click_control("save")
        assert(not ui.learning and ui.status.message:find("See the log",1,true))
        assert(not ui.status_label.text:find("Request error",1,true) and table.concat(logs):find("Request error",1,true))
        ''')

    def test_missing_callback_and_text_read_failures_are_visible(self):
        self.lua.execute('''
        open_presets();ui.callbacks.preset_refresh=nil;click_control("refresh")
        assert(not ui.learning and ui.status.message=="Configuration action is unavailable.")
        choose_target();fail_get_text=true;click_control("save")
        assert(not ui:is_open() and ui.status.state=="error" and ui.status.message:find("text read failed",1,true))
        assert(move==0 and look==0)
        ''')

    def test_new_view_retires_old_selection_callbacks_and_clears_missing_selections(self):
        self.lua.execute('''
        open_presets();select_preset()
        local old=preset_buttons()[2].action
        local view=presets_view(1);view.targets={};ui:set_presets(view);old();drain_render()
        assert(ui.selected_preset_id=="Preset1")
        assert(#preset_buttons()==1 and not ui.preset_controls.apply.widget:GetIsEnabled())
        ui:set_presets(nil);drain_render()
        assert(ui.selected_preset_id==nil and #preset_buttons()==0)
        ''')

    def test_switching_kind_retires_old_selection_callbacks(self):
        self.lua.execute('''
        open_presets();local old=preset_button("Saved 2").action
        choose_target();select_preset("Deck Runner");old();drain_render()
        assert(ui.preset_kind=="character" and ui.selected_preset_id=="Character2")
        choose_target("ship")
        assert(ui.selected_preset_id==nil and not ui.preset_controls.apply.widget:GetIsEnabled())
        ''')

    def test_corrupt_library_and_incomplete_targets_disable_save_and_apply(self):
        self.lua.execute('''
        local view=presets_view();view.error="Invalid JSON";open_presets(view);choose_target()
        assert(preset_has(ui.presets_host,"Saved configurations could not be loaded: Saved data could not be verified. Check the log for details."))
        assert(not preset_has(ui.presets_host,"Invalid JSON") and table.concat(logs):find("Invalid JSON",1,true))
        assert(not shown(ui.preset_save_row) and not ui.preset_controls.save.widget:GetIsEnabled())
        assert(not ui.preset_controls.apply.widget:GetIsEnabled())
        view=presets_view();view.targets[1].complete=false;ui:set_presets(view);drain_render();select_preset("Shore Detail")
        assert(not shown(ui.preset_save_row))
        assert(not ui.preset_controls.save.widget:GetIsEnabled() and not ui.preset_controls.apply.widget:GetIsEnabled())
        ''')

    def test_both_lists_share_two_native_construction_items_per_callback(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(30,"A"));ui:set_presets(presets_view(64))
        ui:_select_tab("presets")
        local advance,advance_presets=ui._advance_render,ui._advance_preset_render
        local native_items=0
        function ui:_advance_render(job,budget)
            if job.phase~="prepare" then native_items=native_items+1 end
            return advance(self,job,budget)
        end
        function ui:_advance_preset_render(job,budget)
            if job.phase~="prepare" then native_items=native_items+1 end
            return advance_presets(self,job,budget)
        end
        for _=1,5000 do
            local before=#preset_buttons();local budget=work_budget(8)
            native_items=0
            ui:tick(budget)
            assert(#preset_buttons()-before<=2 and native_items<=2 and budget.units<=8)
            if not ui:is_rendering() then break end
        end
        assert(not ui:is_rendering() and #preset_buttons()==64 and preset_has(ui.preset_list,"Saved 64"))
        assert(ui:render_progress().completed==ui:render_progress().total)
        ''')

    def test_preset_widgets_survive_owned_graph_collection_and_close_discards_callbacks(self):
        self.lua.execute('''
        open_presets();select_preset("Saved 2")
        collect_native();assert(ui.preset_list:IsValid() and ui.preset_name_box:IsValid())
        local old_select=preset_buttons()[2].action
        local old_apply=ui.preset_controls.apply.action
        local host=ui.host;ui:close("user");collect_native()
        assert(not host:IsValid() and not ui:is_rendering() and ui.preset_list==nil and #ui.buttons==0)
        assert(ui:toggle());drain_render();old_select();old_apply()
        assert(#preset_requests==0)
        ''')

    def test_global_learning_gate_disables_and_reenables_preset_controls_without_dispatch(self):
        self.lua.execute('''
        open_presets();select_preset("Saved 2");ui:set_learning(true)
        assert(not ui.preset_controls.apply.widget:GetIsEnabled() and not preset_buttons()[1].widget:GetIsEnabled())
        choose_target();assert(ui.preset_kind=="boat")
        ui:set_presets(presets_view());drain_render();assert(ui.learning)
        assert(not ui.preset_controls.save.widget:GetIsEnabled())
        ui:set_learning(false);assert(ui.preset_controls.apply.widget:GetIsEnabled() and preset_buttons()[1].widget:GetIsEnabled())
        assert(#preset_requests==0 and #learned==0)
        ''')


if __name__ == "__main__":
    unittest.main()
