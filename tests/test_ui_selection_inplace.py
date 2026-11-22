"""Selection, synchronous settlement and Escape regressions."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE


HELPERS = r'''
function preserve_grid()
    local tiles={};for i,entry in ipairs(ui.visible_tiles) do tiles[i]=entry end
    return {generation=ui.render_generation,job=ui.render_job,offset=ui.page_offset,
        list=ui.paint_list,children=ui.paint_list.children,slots=ui.paint_list.slots,
        count=#all_objects,widgets=#widgets,tiles=tiles,details=ui.details.children,
        title=ui.detail_title,description=ui.detail_description,learn=ui.detail_learn_control.widget}
end
function same_grid(before)
    assert(ui.render_generation==before.generation and ui.render_job==before.job and ui.page_offset==before.offset)
    assert(ui.paint_list==before.list and ui.paint_list.children==before.children and ui.paint_list.slots==before.slots)
    assert(#all_objects==before.count and #widgets==before.widgets and ui.details.children==before.details)
    assert(ui.detail_title==before.title and ui.detail_description==before.description and ui.detail_learn_control.widget==before.learn)
    for i,entry in ipairs(before.tiles) do assert(ui.visible_tiles[i]==entry) end
end
'''


class UISelectionInPlaceTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PLAYER_FIXTURE)
        self.lua.execute(HELPERS)

    def test_selection_reuses_all_widgets_and_does_not_project_or_queue(self):
        self.lua.execute('''
        open_player();page("ready");local before=preserve_grid()
        ui._queue_render=function() error("selection queued a render") end
        require("paintmanager.paint_view").begin=function() error("selection projected the catalog") end
        for i=1,10 do
            ui:_select_paint("copper",ui.render_generation)
            assert(ui.detail_title.text=="Copper" and ui.selected_paint_id=="copper")
            ui:_select_paint("blue",ui.render_generation)
            assert(ui.detail_title.text=="blue" and ui.selected_paint_id=="blue")
            same_grid(before)
        end
        assert(#commands==0 and #learned==0 and bulk_learned==0)
        ''')

    def test_existing_learn_action_is_rebound_and_old_selection_cannot_learn(self):
        self.lua.execute('''
        open_player();page("ready");local old=ui.detail_learn_control.action
        local before=preserve_grid();ui:_select_paint("copper",ui.render_generation);same_grid(before)
        old();assert(#learned==0)
        ui.detail_learn_control.action();assert(#learned==1 and learned[1]=="copper")
        ''')

    def test_card_fills_three_columns_and_updates_preview_with_selection(self):
        self.lua.execute(r'''
        open_player();page("ready")
        local preview, metadata, learn = table.unpack(ui.details.children)
        local image = ui.detail_image
        assert(ui.details.kind=="HorizontalBox" and #ui.details.children==3)
        local border = ui.details.slot.Parent
        local page_background = ui.switcher.children[1].brush_color
        assert(border.brush_color.B > page_background.B and border.brush_color.A==1)
        assert(preview==ui.detail_preview and preview.width==104 and preview.height==104)
        assert(preview.slot.vertical_alignment==0 and preview.slot.padding.Top==0 and preview.slot.padding.Bottom==0)
        assert(preview.child.slot.horizontal_alignment==0 and preview.child.slot.vertical_alignment==0)
        assert(image.slot.horizontal_alignment==0 and image.slot.vertical_alignment==0)
        assert(image.slot.padding.Left==0 and image.slot.padding.Top==0)
        assert(metadata.slot.size.SizeRule==1 and metadata.children[1]==ui.detail_title)
        assert(learn==ui.detail_learn_box and learn.width==640 and learn.height==nil)
        assert(learn.slot.vertical_alignment==0 and learn.child.slot.vertical_alignment==0)
        assert(learn.slot.padding.Top==0 and learn.slot.padding.Bottom==0)
        assert(learn.child.slot.horizontal_alignment==0 and learn.child.child.slot.horizontal_alignment==2)
        local button_icon = learn.child.child.children[1]
        assert(button_icon.width==56 and button_icon.height==56)
        local book = ui.icons.lod_targets[1]
        assert(book.semantic_id=="learn" and book.entry.id=="ready_detailed")

        assert(image.visible==4 and image.texture==tile("blue").texture)
        assert(ui.detail_description.text=="Recipe locked\nBackpack and ship boxes: 4")
        assert(learn.visible==0 and learn.child.enabled)
        ui:_select_paint("copper",ui.render_generation)
        assert(ui.detail_image==image and image.visible==1 and ui.detail_fallback.visible==4)
        assert(ui.detail_description.text=="Recipe locked\nBackpack and ship boxes: 6")
        ui:_select_paint("blue",ui.render_generation)
        assert(image.visible==4 and ui.detail_fallback.visible==1)

        page("learned");ui:_select_paint("zero",ui.render_generation)
        assert(image.texture==tile("zero").texture and image.enabled==false and preview.opacity==0.4)
        assert(learn.visible==1 and not learn.child.enabled)
        ui:set_view({query="no matches"});drain_render()
        assert(preview.visible==1 and learn.visible==1 and ui.detail_description.text=="")
        ''')

    def test_selection_updates_only_previous_and_current_tints(self):
        self.lua.execute('''
        open_player();local amber,zero=tile("amber"),tile("zero");local changed={}
        for _,entry in ipairs(ui.visible_tiles) do
            function entry.widget:SetBackgroundColor(color) changed[#changed+1]={self,color} end
        end
        ui:_select_paint("zero",ui.render_generation)
        assert(#changed==2 and changed[1][1]==amber.widget and changed[2][1]==zero.widget)
        assert(changed[1][2].G==0.105 and changed[2][2].G==0.25)
        ui:_select_paint("zero",ui.render_generation);assert(#changed==2)
        ''')

    def test_page_metadata_publication_does_not_restart_projection(self):
        self.lua.execute('''
        open_player();page("ready");local before=preserve_grid()
        ui:set_preferences({lastPage="ready",sort="name_asc",shipRadiusCm=3500},ui.favorites)
        same_grid(before)
        ui:set_preferences({lastPage="ready",sort="name_asc",shipRadiusCm=4500,showDeveloperTools=true},ui.favorites)
        same_grid(before);assert(ui.stock_radius_label.text:find("45 m",1,true))
        ''')

    def test_off_page_and_stale_selection_are_ignored(self):
        self.lua.execute('''
        open_player();local before=preserve_grid();local selected=ui.selected_paint_id
        ui:_select_paint("blue",ui.render_generation)
        ui:_select_paint("zero",ui.render_generation-1)
        assert(ui.selected_paint_id==selected);same_grid(before)
        ''')

    def test_selection_on_later_page_keeps_offset_and_scroll_owner(self):
        self.lua.execute('''
        assert(ui:toggle());ui.config.max_catalog=512
        render_model(large_model(260,"P"));ui:_page(1);drain_render()
        local before=preserve_grid();ui:_select_paint(ui.visible_tiles[15].row.id,ui.render_generation)
        same_grid(before);assert(ui.page_offset==128)
        collect_native();assert(ui.detail_title:IsValid() and ui.detail_learn_control.widget:IsValid())
        ''')

    def test_click_during_concealed_render_is_ignored_until_page_publication(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(player_model())
        assert(ui.render_job.view_job.options.include_groups==false)
        while #(ui.visible_tiles or {})<2 do ui:tick(work_budget(1)) end
        local job=ui.render_job;local before=preserve_grid();local selected=ui.selected_paint_id
        ui:_select_paint("zero",ui.render_generation);same_grid(before)
        assert(ui.selected_paint_id==selected and job.detail_row.id~="zero");drain_render()
        ui:_select_paint("zero",ui.render_generation)
        assert(ui.selected_paint_id=="zero" and ui.detail_title.text=="Zero known")
        ''')

    def test_synchronous_learn_settles_and_patches_row_without_building_widgets(self):
        self.lua.execute('''
        open_player();page("ready");local before=preserve_grid()
        ui.callbacks.learn_recipe=function(id)
            assert(id=="blue");tile(id).row.recipeState="unlocked";ui:update_recipe(id)
            return true,"Verified",true
        end
        ui.detail_learn_control.action();same_grid(before)
        assert(not ui.learning and ui.status.state=="done" and ui.detail_learn_control.kind=="paint_detail")
        assert(not ui.detail_learn_control.widget.enabled and ui.detail_learn_box.visible==1)
        assert(ui.detail_description.text:find("Recipe unlocked",1,true))
        assert(tile("blue").tooltip_label.text:find("Recipe unlocked",1,true))
        assert(ui.learn_all_ready_count==1)
        assert(ui.tab_controls.ready.label.text=="Can Learn (1)" and ui.tab_controls.learned.label.text=="Learned (3)")
        ui:update_recipe("blue");assert(ui.learn_all_ready_count==1)
        ''')

    def test_escape_intent_only_opens_no_native_path_while_closed(self):
        self.lua.execute('''
        assert(not ui:request_escape());ui:tick();assert(not ui:is_open())
        assert(event_count("game_only")==0)
        ''')

    def test_escape_closes_before_normal_input_and_restores_game_input_once(self):
        self.lua.execute('''
        open_player()
        ui._fit=function() error("layout ran after Escape press") end
        ui._poll_search=function() error("search ran after Escape press") end
        assert(ui:request_escape() and ui:request_escape() and ui:is_closing())
        assert(ui:is_open() and move==1 and look==1 and event_count("game_only")==0)
        ui:tick(work_budget(0),true)
        assert(not ui:is_open() and move==0 and look==0 and not pc.bShowMouseCursor)
        assert(not ui:is_closing() and ui.status.state~="error")
        assert(not ui:request_escape());ui:tick()
        assert(event_count("game_only")==1 and viewport.added==nil)
        ''')

    def test_escape_intent_cannot_close_reopened_window(self):
        self.lua.execute('''
        open_player()
        ui:request_escape();ui:close("user");assert(ui:toggle())
        ui:tick(work_budget(0),true);assert(ui:is_open() and ui.escape_pending==nil)
        ''')


if __name__ == "__main__":
    unittest.main()
