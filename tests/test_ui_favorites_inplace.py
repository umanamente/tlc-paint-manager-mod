"""Favorite metadata updates keep the current native grid and its order intact."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE


HELPERS = r'''
function favorite_button(id)
    local entry=assert(tile(id))
    for _,button in ipairs(ui.buttons) do
        if button.widget==entry.star then return button end
    end
    error("Missing favorite button")
end
function remember_grid()
    local entries={}
    for index,entry in ipairs(ui.visible_tiles) do entries[index]=entry end
    return {generation=ui.render_generation,job=ui.render_job,names=paint_names(),
        count=#all_objects,widgets=#widgets,selected=ui.selected_paint_id,
        offset=ui.page_offset,entries=entries,list=ui.paint_list,
        children=ui.paint_list.children,slots=ui.paint_list.slots}
end
function assert_grid_unchanged(before)
    assert(ui.render_generation==before.generation and ui.render_job==before.job)
    assert(paint_names()==before.names and ui.paint_list==before.list)
    assert(ui.paint_list.children==before.children and ui.paint_list.slots==before.slots)
    assert(#all_objects==before.count and #widgets==before.widgets)
    assert(ui.selected_paint_id==before.selected and ui.page_offset==before.offset)
    for index,entry in ipairs(before.entries) do assert(ui.visible_tiles[index]==entry) end
end
function assert_star(id,enabled)
    local button=favorite_button(id)
    assert(button.favorite_enabled==enabled)
    assert(button.icon_states.empty.visible==(enabled and 1 or 4))
    assert(button.icon_states.filled.visible==(enabled and 4 or 1))
    assert(button.tooltip_label.text==(enabled and "Remove from favorites" or "Add to favorites"))
end
function wire_metadata(result_state)
    local value={sections={settings={data=ui.preferences},favorites={data={records={}}}}}
    local snapshot={accountId="account",scopeId="scope",path="library.json",loadGeneration=1}
    metadata_errors={};metadata_writes=0
    metadata=require("paintmanager.ui_preferences").new({
        ready=function() return "current",snapshot end,
        publish=function(settings,favorites) ui:set_preferences(settings,favorites) end,
        error=function(code) metadata_errors[#metadata_errors+1]=code end,
        store=function()
            return {begin_favorite=function(asset_type,asset_name,enabled)
                metadata_writes=metadata_writes+1
                local output={sections={settings={data=ui.preferences},favorites={data={records={
                    {assetType=asset_type,assetName=asset_name,enabled=enabled}}}}}}
                return {step=function() return {state=result_state,error=result_state=="error" and "Write failed" or nil} end,
                    get_result=function() return output end}
            end}
        end,
    })
    assert(metadata:load(value))
    ui.callbacks.favorite=function(...) return metadata:favorite(...) end
end
'''


class UIFavoritesInPlaceTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PLAYER_FIXTURE)
        self.lua.execute(HELPERS)

    def test_repeated_clicks_flip_existing_states_without_render_or_widgets(self):
        self.lua.execute('''
        open_player();local before=remember_grid();local button=favorite_button("zero")
        local empty,filled=button.icon_states.empty,button.icon_states.filled
        assert_star("zero",false)
        button.action();assert_star("zero",true);assert_grid_unchanged(before)
        button.action();assert_star("zero",false);assert_grid_unchanged(before)
        button.action();assert_star("zero",true);assert_grid_unchanged(before)
        assert(button.icon_states.empty==empty and button.icon_states.filled==filled)
        assert(#favorite_changes==3 and favorite_changes[1][3] and not favorite_changes[2][3] and favorite_changes[3][3])
        assert(#commands==0 and #learned==0 and bulk_learned==0)
        ''')

    def test_optimistic_publish_and_successful_save_do_not_reset_grid(self):
        self.lua.execute('''
        open_player();wire_metadata("done");local before=remember_grid()
        favorite_button("zero").action()
        assert_star("zero",true);assert_grid_unchanged(before)
        metadata:step(work_budget(8),true);assert(metadata_writes==1)
        assert_star("zero",true);assert_grid_unchanged(before)
        metadata:step(work_budget(8),true)
        assert(not metadata:is_busy() and #metadata_errors==0)
        assert_star("zero",true);assert_grid_unchanged(before)
        ''')

    def test_both_star_states_and_tooltip_remain_owned_across_native_gc(self):
        self.lua.execute('''
        open_player();collect_native()
        local button=favorite_button("zero")
        assert(button.widget:IsValid() and button.label==nil and button.tooltip_label:IsValid())
        assert(button.icon_states.empty:IsValid() and button.icon_states.filled:IsValid())
        local before=remember_grid()
        button.action();assert_star("zero",true);assert_grid_unchanged(before)
        collect_native()
        button.action();assert_star("zero",false);assert_grid_unchanged(before)
        ''')

    def test_failed_save_rolls_back_only_star_without_rebuilding(self):
        self.lua.execute('''
        open_player();wire_metadata("error");local before=remember_grid()
        favorite_button("zero").action()
        assert_star("zero",true);assert_grid_unchanged(before)
        metadata:step(work_budget(8),true);metadata:step(work_budget(8),true)
        assert(#metadata_errors==1 and metadata_errors[1]=="preferences_save_failed")
        assert_star("zero",false);assert_grid_unchanged(before)
        ''')

    def test_preference_reconciliation_updates_only_changed_visible_stars(self):
        self.lua.execute(r'''
        open_player();local before=remember_grid()
        ui:set_preferences(ui.preferences,{["Item\0Z"]=true,["Item\0OffPage"]=true})
        assert_star("zero",true);assert_star("amber",false);assert_grid_unchanged(before)
        ui:set_preferences(ui.preferences,{["Item\0Z"]=true,["Item\0OffPage"]=false})
        assert_star("zero",true);assert_grid_unchanged(before)
        ui:set_preferences(ui.preferences,{["Item\0Z"]=false})
        assert_star("zero",false);assert_grid_unchanged(before)
        ''')

    def test_favorite_order_is_applied_at_next_normal_render(self):
        self.lua.execute('''
        open_player();local before=remember_grid()
        favorite_button("zero").action();assert_grid_unchanged(before)
        assert(paint_names()=="Amber|Zero known")
        ui:set_model(ui.model);drain_render()
        assert(ui.render_generation>before.generation and paint_names()=="Zero known|Amber")
        assert_star("zero",true)
        ''')

    def test_page_offset_selection_and_query_survive_favorites_publication(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui.config.max_catalog=512
        render_model(large_model(260,"P"));ui:set_view({query="Paint"});drain_render()
        ui:_page(1);drain_render()
        local selected=ui.visible_tiles[4].row.id
        ui.selected_paint_id=selected
        local before=remember_grid();local first=ui.visible_tiles[1]
        local button
        for _,record in ipairs(ui.buttons) do if record.widget==first.star then button=record end end
        button.action();assert_grid_unchanged(before)
        ui:set_preferences(ui.preferences,ui.favorites);assert_grid_unchanged(before)
        assert(ui.page_offset==128 and ui.selected_paint_id==selected and ui.view.query=="Paint")
        ''')

    def test_rejected_click_and_stale_callback_do_not_change_stars(self):
        self.lua.execute('''
        open_player();local before=remember_grid();local button=favorite_button("zero")
        ui.callbacks.favorite=function() return false,"Unavailable" end
        button.action();assert_star("zero",false);assert_grid_unchanged(before)
        local stale=button.action
        ui:set_view({query="Zero"});drain_render()
        local current=remember_grid();local invocations=0
        ui.callbacks.favorite=function() invocations=invocations+1;return true end
        stale();assert(invocations==0);assert_star("zero",false);assert_grid_unchanged(current)
        ''')


if __name__ == "__main__":
    unittest.main()
