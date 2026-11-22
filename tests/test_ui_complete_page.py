"""A category is presented only as a complete native-owned page, without badges."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import CLICK_TIMING_FIXTURE, FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE


HELPERS = r'''
favorite_changes={}
function drawn(node)
    local seen={}
    while node and not seen[node] do
        seen[node]=true
        if node.visible==1 or node.visible==2 then return false end
        local parent=node.slot and node.slot.Parent
        if parent and parent.kind=="WidgetSwitcher" and parent.children[(parent.active or 0)+1]~=node then return false end
        node=parent
    end
    return true
end
function pump_complete(cache,expected)
    local steps=0
    while not cache.ready do
        assert(not drawn(cache.scroll),"partial category became visible")
        ui:render_tick(work_budget(1));steps=steps+1
        assert(steps<10000 and ui:is_open(),ui.status and ui.status.message)
        if not cache.ready then assert(not drawn(cache.scroll),"partial rows were drawn") end
    end
    assert(drawn(cache.scroll) and #cache.visible_tiles==expected)
    return steps
end
function warm_all()
    for _=1,10000 do
        local ready=true
        for _,name in ipairs({"learned","ready","missing"}) do ready=ready and ui.paint_caches[name].ready==true end
        if ready and not ui.render_job then return end
        ui:render_tick(work_budget(8))
        assert(ui:is_open(),ui.status and ui.status.message)
    end
    error("warmup stalled")
end
'''


class UICompletePageTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + PLAYER_FIXTURE + HELPERS)

    def test_large_page_stays_hidden_until_all_rows_and_controls_are_ready(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(128,"P"))
        local cache=ui.paint_caches.learned
        local reveal=0;local visibility=cache.scroll.SetVisibility
        function cache.scroll:SetVisibility(value)
            if value==0 then
                reveal=reveal+1
                assert(cache.ready and #cache.visible_tiles==128,"revealed an incomplete page")
                assert(ui.detail_title.text~="" and ui.view_summary.text:find("128",1,true))
            end
            return visibility(self,value)
        end
        local steps=pump_complete(cache,128)
        assert(steps>64 and reveal==1,"whole page was not published once")
        for _,entry in ipairs(cache.visible_tiles) do assert(drawn(entry.widget)) end
        ''')

    def test_click_on_default_learned_waits_for_vault_page_and_logs_once(self):
        self.lua.execute(CLICK_TIMING_FIXTURE + '''
        assert(ui:toggle(true));press_tab("learned")
        for _=1,100 do ui:render_tick(work_budget(8)) end
        assert(#click_logs()==0,"Initial shell completed a click before Vault data")
        local early=player_model({learnedOnly=true,stockComplete=false})
        early.rows={early.rows[1],early.rows[2]}
        now_ms=1040;assert(ui:show_learned(early))
        local cache=ui.paint_caches.learned
        for _=1,1000 do
            if cache.ready then break end
            assert(not drawn(cache.scroll) and #click_logs()==0)
            ui:render_tick(work_budget(1))
        end
        assert(drawn(cache.scroll) and #cache.visible_tiles==2 and ui.waiting_stock)
        assert(assert_timing(1,"Learned",40,"list",false):find("source=learned_prefix",1,true))
        now_ms=1175;ui:reconcile_capture(player_model());drain_render()
        assert(#click_logs()==1)
        ''')

    def test_replaced_click_waits_for_current_paint_page_and_cached_click_is_immediate(self):
        self.lua.execute(CLICK_TIMING_FIXTURE + '''
        assert(ui:toggle(true));press_tab("ready")
        now_ms=1025;press_tab("missing")
        assert_timing(1,"Can Learn",25,"superseded",false)
        local model=player_model();ui:set_model(model)
        now_ms=1100
        for _=1,1000 do
            if ui.paint_caches.missing.ready then break end
            assert(#click_logs()==1 and not drawn(ui.paint_caches.missing.scroll))
            ui:render_tick(work_budget(1))
        end
        assert(drawn(ui.paint_caches.missing.scroll))
        assert_timing(2,"Need Cans",75,"list",false)
        warm_all();now_ms=1300;press_tab("ready")
        assert(drawn(ui.paint_caches.ready.scroll))
        assert_timing(3,"Can Learn",0,"list",true)
        press_tab("learned");press_tab("missing")
        assert_timing(4,"Learned",0,"list",true)
        assert_timing(5,"Need Cans",0,"list",true)
        assert(#click_logs()==5)
        ''')

    def test_empty_unverified_vault_and_stock_tabs_wait_for_the_ordinary_capture(self):
        for tab, title in (("learned", "Learned"), ("ready", "Can Learn"), ("missing", "Need Cans")):
            with self.subTest(tab=tab):
                self.setUp()
                self.lua.globals().target_tab = tab
                self.lua.globals().target_title = title
                self.lua.execute(CLICK_TIMING_FIXTURE + '''
                assert(ui:toggle(true))
                if target_tab=="learned" then press_tab(target_tab) end
                local early=learn_model({}, {learnedOnly=true,stockComplete=false,recipeCatalogComplete=false})
                assert(ui:show_learned(early));drain_render()
                if target_tab~="learned" then press_tab(target_tab);drain_render() end
                assert(#click_logs()==0)
                now_ms=1035;ui:reconcile_capture(player_model());drain_render()
                assert_timing(1,target_title,35,"list",false)
                assert(#click_logs()==1)
                ''')

    def test_empty_result_controls_and_close_are_bound_to_the_clicked_tab(self):
        self.lua.execute(CLICK_TIMING_FIXTURE + '''
        assert(ui:toggle(true));press_tab("ready")
        now_ms=1090
        ui:set_model(learn_model({}));drain_render()
        assert(drawn(ui.paint_caches.ready.scroll))
        assert_timing(1,"Can Learn",90,"empty",false)
        press_tab("settings")
        assert(drawn(ui.settings_page))
        assert_timing(2,"Settings",0,"controls",true)
        ui.checkboxes.showDeveloperTools.widget:SetIsChecked(true);ui:tick(work_budget(8))
        press_tab("debug")
        assert(drawn(ui.debug_page));assert_timing(3,"Debug",0,"controls",false)
        ui:set_model(nil);press_tab("missing");now_ms=1130;ui:close("user")
        assert_timing(4,"Need Cans",40,"closed",false)
        assert(ui:toggle(true));ui:set_model(player_model());drain_render()
        assert(#click_logs()==4)
        ''')

    def test_tiles_request_no_recipe_glyph_and_learning_needs_no_marker_rebind(self):
        self.lua.execute('''
        assert(ui:toggle())
        local image=ui.icons.image
        function ui.icons:image(id,...)
            assert(id~="learned" and id~="ready" and id~="missing" and id~="learned_detailed" and id~="ready_detailed",
                "a tile requested a recipe glyph")
            return image(self,id,...)
        end
        render_model(player_model());page("ready")
        for _,entry in ipairs(ui.visible_tiles) do assert(entry.status==nil and entry.status_target==nil and entry.mark) end
        local grid=ui.paint_list;local children=grid.children
        ui.icons.rebind=function() error("Learn tried to rebind a tile glyph") end
        tile("blue").row.recipeState="unlocked";ui:update_recipe("blue")
        assert(ui.paint_list==grid and grid.children==children and tile("blue").mark=="learned")
        assert(ui.learn_all_ready_count==1 and ui.detail_description.text:find("unlocked",1,true))
        page("missing")
        for _,entry in ipairs(ui.visible_tiles) do assert(entry.status==nil and entry.status_target==nil) end
        ''')

    def test_partial_tiles_cannot_be_selected_favorited_or_polled(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(40,"P"))
        local cache=ui.paint_caches.learned
        for _=1,10000 do
            ui:render_tick(work_budget(1))
            if #cache.visible_tiles>0 then break end
        end
        assert(not cache.ready and not drawn(cache.scroll))
        local entry=cache.visible_tiles[1]
        local selected=ui.selected_paint_id;local favorites=#favorite_changes
        entry.select_control.action();entry.favorite_control.action()
        ui:_select_paint(entry.row.id,ui.render_generation)
        ui:_toggle_favorite(entry.row,ui.render_generation)
        assert(ui.selected_paint_id==selected and #favorite_changes==favorites)
        function entry.widget:IsPressed() error("hidden row was polled") end
        function entry.star:IsPressed() error("hidden favorite was polled") end
        ui:tick(work_budget(0),true)
        assert(ui:is_open() and ui.status.state~="error",ui.status.message)
        pump_complete(cache,40)
        entry.select_control.action();assert(ui.selected_paint_id==entry.row.id)
        ''')

    def test_promoted_partial_category_is_hidden_but_completed_cache_is_immediate(self):
        self.lua.execute('''
        assert(ui:toggle());local model=large_model(90,"P")
        for index,row in ipairs(model.rows) do row.recipeState=index<=30 and "unlocked" or "locked";row.count=index<=60 and 4 or 1 end
        render_model(model)
        local learned_cache=ui.paint_caches.learned
        assert(learned_cache.ready and drawn(learned_cache.scroll))
        for _=1,12 do ui:render_tick(work_budget(1)) end
        local target=ui.paint_caches.ready
        assert(not target.ready and target.job)
        local job=target.job
        ui:_select_tab("ready")
        assert(ui.render_job==job and not drawn(target.scroll))
        pump_complete(target,30);warm_all()
        ui._make=function() error("ready cache recreated a widget") end
        require("paintmanager.paint_view").begin=function() error("ready cache projected again") end
        for _,name in ipairs({"learned","missing","ready"}) do
            ui:_select_tab(name)
            assert(ui.paint_caches[name].ready and drawn(ui.paint_caches[name].scroll) and not ui:is_rendering())
        end
        ''')

    def test_replaced_model_never_reveals_old_partial_rows_or_old_actions(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(80,"Old"))
        local cache=ui.paint_caches.learned
        for _=1,10000 do ui:render_tick(work_budget(1));if #cache.visible_tiles>0 then break end end
        local old=cache.visible_tiles[1];assert(old and not cache.ready)
        ui:set_model(large_model(12,"New"));assert(not drawn(cache.scroll))
        old.select_control.action();old.favorite_control.action()
        assert(#favorite_changes==0)
        pump_complete(cache,12)
        for _,entry in ipairs(cache.visible_tiles) do assert(entry.row.id:sub(1,3)=="New") end
        old.select_control.action();old.favorite_control.action()
        assert(#favorite_changes==0 and ui.selected_paint_id:sub(1,3)=="New")
        ''')

    def test_hidden_page_retains_native_ownership_and_close_never_late_reveals(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(80,"P"))
        local cache=ui.paint_caches.learned
        for _=1,10000 do ui:render_tick(work_budget(1));if #cache.visible_tiles>0 then break end end
        collect_native();assert(cache.scroll:IsValid() and cache.visible_tiles[1].widget:IsValid())
        ui:close("user");collect_native()
        assert(not cache.scroll:IsValid() and not cache.visible_tiles[1].widget:IsValid())
        for _=1,10 do ui:render_tick(work_budget(8)) end
        assert(not ui:is_open() and move==0 and look==0)
        ''')

    def test_reentrant_model_change_during_summary_cannot_publish_old_page(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(20,"Old"))
        local cache=ui.paint_caches.learned
        for _=1,10000 do
            if ui.render_job and ui.render_job.phase=="finish" then break end
            ui:render_tick(work_budget(1))
        end
        local summary=ui.view_summary.SetText;local armed=true
        function ui.view_summary:SetText(value)
            if armed then armed=false;ui:set_model(large_model(3,"Fresh")) end
            return summary(self,value)
        end
        local shown=cache.scroll.SetVisibility;local reveals=0
        function cache.scroll:SetVisibility(value)
            if value==0 then
                reveals=reveals+1
                assert(cache.ready and #cache.visible_tiles==3 and cache.result.model==ui.model,"old page was revealed")
                for _,entry in ipairs(cache.visible_tiles) do assert(entry.row.id:sub(1,5)=="Fresh") end
            end
            return shown(self,value)
        end
        ui:render_tick(work_budget(1))
        assert(not armed and not cache.ready and not drawn(cache.scroll) and reveals==0)
        pump_complete(cache,3);assert(reveals==1)
        ''')

    def test_reentrant_close_during_summary_has_no_late_native_reveal(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(20,"P"))
        local cache=ui.paint_caches.learned
        for _=1,10000 do
            if ui.render_job and ui.render_job.phase=="finish" then break end
            ui:render_tick(work_budget(1))
        end
        local summary=ui.view_summary.SetText;local armed=true
        function ui.view_summary:SetText(value)
            if armed then armed=false;ui:close("user") end
            return summary(self,value)
        end
        local shown=cache.scroll.SetVisibility
        function cache.scroll:SetVisibility(value)
            assert(value~=0,"closed category received a late reveal")
            return shown(self,value)
        end
        ui:render_tick(work_budget(1))
        assert(not ui:is_open() and move==0 and look==0)
        ''')

    def test_cached_navigation_summary_preserves_reentrant_replacement_job(self):
        self.lua.execute('''
        open_player();warm_all()
        local summary=ui._show_cached_summary;local armed=true
        function ui:_show_cached_summary(cache)
            if armed then armed=false;self:set_model(large_model(5,"Fresh"));return end
            return summary(self,cache)
        end
        ui:_select_tab("ready")
        assert(not armed and ui.render_job and not ui.paint_caches.ready.ready,"replacement render was discarded")
        assert(not drawn(ui.paint_caches.ready.scroll))
        drain_render()
        ui:_select_tab("learned");drain_render()
        assert(ui.paint_caches.learned.ready and #ui.visible_tiles==5)
        for _,entry in ipairs(ui.visible_tiles) do assert(entry.row.id:sub(1,5)=="Fresh") end
        ''')
