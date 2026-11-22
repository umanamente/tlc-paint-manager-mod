"""Retained category grids reuse native ownership and retire obsolete actions."""
import unittest

from lupa.lua54 import LuaRuntime

import test_ui as ui_fixture
from test_ui_player import PLAYER_FIXTURE


HELPERS = r'''
function warm_categories()
    for _=1,10000 do
        local ready=true
        for _,name in ipairs({"learned","ready","missing"}) do
            ready=ready and ui.paint_caches[name].ready==true
        end
        if ready and not ui.render_job then return end
        ui:render_tick(work_budget(8))
        assert(ui:is_open(),ui.status and ui.status.message)
    end
    error("category warmup stalled")
end
function cache_ids(cache)
    local ids={}
    for _,entry in ipairs(cache.visible_tiles) do ids[#ids+1]=entry.row.id end
    return table.concat(ids,"|")
end
function cache_snapshot()
    local result={}
    for name,cache in pairs(ui.paint_caches) do
        result[name]={scroll=cache.scroll,list=cache.paint_list,children=cache.paint_list.children,
            tiles=cache.visible_tiles,by_id=cache.visible_tiles_by_id}
    end
    return result
end
function assert_cached(before)
    for name,expected in pairs(before) do
        local actual=ui.paint_caches[name]
        assert(actual.scroll==expected.scroll and actual.paint_list==expected.list)
        assert(actual.paint_list.children==expected.children)
        assert(actual.visible_tiles==expected.tiles and actual.visible_tiles_by_id==expected.by_id)
    end
end
'''


class UITabCacheTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = ui_fixture.SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(ui_fixture.FIXTURE + PLAYER_FIXTURE + HELPERS)

    def test_warmed_category_switches_reuse_grids_and_one_projection(self):
        self.lua.execute('''
        open_player();warm_categories()
        local before=cache_snapshot()
        ui._make=function() error("cached category constructed a widget") end
        require("paintmanager.paint_view").begin=function() error("cached category projected the model") end
        for _=1,8 do
            for _,name in ipairs({"ready","missing","learned"}) do
                assert(ui:_select_tab(name));assert(not ui:is_rendering())
                ui:tick(work_budget(0),true)
                assert(ui.paint_list==before[name].list)
                assert(ui.status.state~="error",ui.status.message)
                assert_cached(before)
            end
        end
        assert(#commands==0 and #learned==0 and bulk_learned==0)
        ''')

    def test_native_gc_retains_all_hidden_grids_and_scrollers(self):
        self.lua.execute('''
        open_player();warm_categories();collect_native()
        for _,cache in pairs(ui.paint_caches) do
            assert(cache.scroll:IsValid() and cache.paint_list:IsValid())
            for _,entry in ipairs(cache.visible_tiles) do
                assert(entry.widget:IsValid() and entry.star:IsValid() and entry.count:IsValid())
            end
        end
        local caches=ui.paint_caches
        ui:close("user");collect_native()
        for _,cache in pairs(caches) do assert(not cache.scroll:IsValid()) end
        ''')

    def test_switches_restore_per_category_selection_offset_and_scroll_owner(self):
        self.lua.execute('''
        assert(ui:toggle());ui.config.max_catalog=512
        local model=large_model(270,"P")
        for index,row in ipairs(model.rows) do
            row.recipeState=index<=140 and "unlocked" or "locked"
            row.count=index<=260 and 4 or 1
        end
        render_model(model);warm_categories()
        ui:_page(1);drain_render()
        local learned_cache=ui.paint_caches.learned
        local selected=ui.visible_tiles[5].row.id
        ui:_select_paint(selected,ui.render_generation)
        learned_cache.scroll.native_scroll_offset=93
        ui:_select_tab("ready");drain_render()
        local ready_selected=ui.visible_tiles[4].row.id
        ui:_select_paint(ready_selected,ui.render_generation)
        ui.paint_caches.ready.scroll.native_scroll_offset=47
        ui:_select_tab("missing");drain_render()
        ui:_select_tab("learned");drain_render()
        assert(ui.page_offset==128 and ui.selected_paint_id==selected)
        assert(ui.paint_caches.learned.scroll==learned_cache.scroll and learned_cache.scroll.native_scroll_offset==93)
        ui:_select_tab("ready");drain_render()
        assert(ui.page_offset==0 and ui.selected_paint_id==ready_selected)
        assert(ui.paint_caches.ready.scroll.native_scroll_offset==47)
        ''')

    def test_hidden_cached_controls_never_poll_and_old_generation_actions_are_inert(self):
        self.lua.execute('''
        open_player();warm_categories();ui:_select_tab("ready")
        local old_select=ui.visible_tiles[2].select_control.action
        local old_favorite=ui.visible_tiles[2].favorite_control.action
        local original_selected=ui.selected_paint_id
        for _,name in ipairs({"learned","missing"}) do
            for _,entry in ipairs(ui.paint_caches[name].visible_tiles) do
                function entry.widget:IsPressed() error("hidden cached tile was polled") end
                function entry.star:IsPressed() error("hidden cached favorite was polled") end
            end
        end
        ui:tick(work_budget(0),true)
        assert(ui:is_open() and ui.selected_paint_id==original_selected)
        local replacement=player_model();replacement.contextToken="new-model-context"
        ui:set_model(replacement)
        old_select();old_favorite()
        assert(#learned==0 and #(favorite_changes or {})==0 and ui.selected_paint_id==nil)
        ''')

    def test_learning_keeps_current_grid_then_refreshes_membership_on_navigation_without_sorting(self):
        self.lua.execute('''
        open_player();warm_categories();ui:_select_tab("ready")
        local current=ui.paint_list.children
        local learned_row=ui.visible_tiles_by_id.blue.row
        learned_row.recipeState="unlocked";ui:update_recipe("blue")
        assert(ui.paint_list.children==current and cache_ids(ui.paint_caches.ready):find("blue",1,true))
        require("paintmanager.paint_view").begin=function() error("membership change repeated projection/sort") end
        ui:_select_tab("learned");drain_render();warm_categories()
        assert(cache_ids(ui.paint_caches.learned):find("blue",1,true))
        ui:_select_tab("ready");drain_render()
        assert(cache_ids(ui.paint_caches.ready)=="copper")
        assert(ui.tab_controls.ready.label.text=="Can Learn (1)" and ui.learn_all_ready_count==1)
        ''')

    def test_favorite_order_changes_only_on_navigation_and_false_nil_settlement_is_stable(self):
        self.lua.execute('''
        open_player();warm_categories();ui:_select_tab("ready")
        local before=ui.paint_list.children
        ui:_toggle_favorite(ui.visible_tiles_by_id.copper.row,ui.render_generation)
        assert(ui.paint_list.children==before and cache_ids(ui.paint_caches.ready)=="blue|copper")
        ui:_select_tab("learned");drain_render();warm_categories()
        ui:_select_tab("ready");drain_render()
        assert(cache_ids(ui.paint_caches.ready)=="copper|blue")
        ui:_toggle_favorite(ui.visible_tiles_by_id.copper.row,ui.render_generation)
        local revision=ui.favorite_order_revision
        ui:set_preferences(ui.preferences,{})
        assert(ui.favorite_order_revision==revision,"false-to-nil settlement changed favorite ordering")
        ui:set_preferences(ui.preferences,{["Item"..string.char(0).."C"]=false})
        assert(ui.favorite_order_revision==revision,"nil-to-false settlement changed favorite ordering")
        ''')

    def test_rejected_favorite_does_not_invalidate_warmed_categories(self):
        self.lua.execute('''
        open_player();warm_categories();ui:_select_tab("ready")
        local before=cache_snapshot();local revision=ui.favorite_order_revision
        ui.callbacks.favorite=function() return false,"Publication rejected" end
        ui:_toggle_favorite(ui.visible_tiles_by_id.copper.row,ui.render_generation)
        assert(ui.favorite_order_revision==revision and not ui.favorites["Item"..string.char(0).."C"])
        require("paintmanager.paint_view").begin=function() error("rejected favorite caused projection") end
        ui._make=function() error("rejected favorite rebuilt tiles") end
        ui:_select_tab("learned");ui:_select_tab("ready");ui:_select_tab("missing")
        assert_cached(before)
        ''')

    def test_new_model_cancels_partial_hidden_build_and_publishes_only_new_rows(self):
        self.lua.execute('''
        assert(ui:toggle());ui.config.max_catalog=512
        local old=large_model(100,"Old")
        for index,row in ipairs(old.rows) do
            row.count=4;row.recipeState=index<=2 and "unlocked" or "locked"
        end
        render_model(old)
        for _=1,1000 do
            ui:render_tick(work_budget(1))
            if ui.render_job and ui.render_job.background and #ui.paint_caches.ready.visible_tiles>0 then break end
        end
        assert(ui.render_job and ui.render_job.background)
        local old_select=ui.paint_caches.ready.visible_tiles[1].select_control.action
        local new=player_model();new.contextToken="replacement"
        ui:set_model(new);old_select();drain_render();warm_categories()
        for _,cache in pairs(ui.paint_caches) do
            for _,entry in ipairs(cache.visible_tiles) do assert(not entry.row.id:find("Old",1,true)) end
        end
        assert(#learned==0 and #(favorite_changes or {})==0)
        collect_native();assert(ui:is_open())
        ''')

    def test_visible_selection_during_hidden_prewarm_cannot_replace_hidden_details(self):
        self.lua.execute('''
        open_player();ui:_select_tab("ready");drain_render()
        for _=1,1000 do
            ui:render_tick(work_budget(1))
            if ui.render_job and ui.render_job.background and ui.render_job.cache.tab=="missing"
                and ui.render_job.detail_row then break end
        end
        local hidden=ui.render_job
        assert(hidden and hidden.background and hidden.cache.tab=="missing" and hidden.detail_row)
        local original=hidden.detail_row
        ui:_select_paint("copper",ui.render_generation)
        assert(ui.detail_row.id=="copper" and hidden.detail_row==original)
        ui:_select_tab("missing");drain_render()
        assert(ui.detail_row.id=="hidden" or ui.detail_row.id=="delta")
        assert(ui.visible_tiles_by_id[ui.detail_row.id])
        ''')

    def test_learning_mid_render_cannot_certify_old_category_membership_as_current(self):
        self.lua.execute('''
        open_player();warm_categories();ui:_select_tab("ready")
        ui:_queue_render(ui.model,true,true)
        for _=1,1000 do
            ui:render_tick(work_budget(1))
            if ui.render_job and ui.render_job.phase=="paints" and ui.visible_tiles_by_id.blue then break end
        end
        local job=ui.render_job
        assert(job and not job.background and job.rows[1].id=="blue")
        local previous=ui.membership_revision or 0
        ui.visible_tiles_by_id.blue.row.recipeState="unlocked"
        ui:update_recipe("blue")
        assert(ui.membership_revision==previous+1)
        drain_render()
        assert(ui.paint_caches.ready.membership_revision~=ui.membership_revision)
        require("paintmanager.paint_view").begin=function() error("membership repair repeated sort/projection") end
        ui:_select_tab("learned");drain_render()
        ui:_select_tab("ready");drain_render()
        assert(cache_ids(ui.paint_caches.ready)=="copper")
        assert(ui.learn_all_ready_count==1)
        ''')

    def test_partial_category_jobs_resume_after_fast_navigation_without_mixing_rows(self):
        self.lua.execute('''
        assert(ui:toggle());ui.config.max_catalog=512
        local model=large_model(90,"P")
        for index,row in ipairs(model.rows) do
            row.recipeState=index<=30 and "unlocked" or "locked"
            row.count=index<=60 and 4 or 1
        end
        render_model(model)
        ui:_select_tab("ready")
        for _=1,8 do ui:render_tick(work_budget(1)) end
        local partial=ui.paint_caches.ready.job
        assert(partial and not ui.paint_caches.ready.ready)
        ui:_select_tab("missing")
        for _=1,8 do ui:render_tick(work_budget(1)) end
        ui:_select_tab("ready")
        assert(ui.render_job==partial,"partial category was restarted")
        drain_render();warm_categories()
        for name,cache in pairs(ui.paint_caches) do
            assert(#cache.visible_tiles==30)
            local seen={}
            for _,entry in ipairs(cache.visible_tiles) do
                assert(not seen[entry.row.id]);seen[entry.row.id]=true
                local category=require("paintmanager.paint_view").classify(model,entry.row)
                assert(category==name,"partial category mixed rows from another tab")
            end
        end
        assert(ui:is_open() and ui.status.state~="error",ui.status.message)
        ''')


if __name__ == "__main__":
    unittest.main()
