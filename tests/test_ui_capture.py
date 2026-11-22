"""Reconcile two opening intervals through the ordinary retained UI surface."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE
from test_ui_tab_cache import HELPERS


class UICaptureTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + PLAYER_FIXTURE + HELPERS)

    def test_only_changed_count_is_written_and_selection_and_scroller_survive(self):
        self.lua.execute('''
        open_player();warm_categories();ui:_select_paint("zero",ui.render_generation)
        local amber,zero=tile("amber"),tile("zero")
        local scroll=ui.active_paint_cache.scroll;scroll.native_scroll_offset=39
        function zero.count:SetText() error("Unchanged number was repainted") end
        local sets=0
        function amber.count:SetText(value) sets=sets+1;self.text=value end
        local fresh=player_model();fresh.rows[1].count=12
        ui:reconcile_capture(fresh)
        assert(scroll.visible==0 and amber.count.text=="8")
        drain_render();warm_categories()
        assert(ui:is_open() and ui.model==fresh and tile("amber")==amber and tile("zero")==zero)
        assert(sets==1 and amber.count.text=="12" and ui.selected_paint_id=="zero")
        assert(ui.active_paint_cache.scroll==scroll and scroll.native_scroll_offset==39)
        assert(ui.detail_row==fresh.rows[2] and #commands==0)
        ''')

    def test_new_items_and_category_arrivals_append_even_under_count_sort(self):
        self.lua.execute('''
        open_player();ui:_change_settings({sort="count_desc"});drain_render();warm_categories()
        page("ready");local copper,blue=tile("copper"),tile("blue")
        local fresh=player_model({catalogComplete=true})
        fresh.rows[3].count=14;fresh.rows[5].count=25
        table.insert(fresh.rows,1,{id="add",count=90,assetType="Item",assetName="New",
            displayName="AAA",recipeState="locked"})
        ui:reconcile_capture(fresh);drain_render();warm_categories()
        assert(cache_ids(ui.paint_caches.ready)=="copper|blue|add|delta")
        assert(tile("copper")==copper and tile("blue")==blue and blue.count.text=="14")
        assert(ui.learn_all_ready_count==4 and ui.tab_controls.missing.label.text=="Need Cans (1)")
        page("missing");assert(not tile("delta"));page("ready")
        assert(cache_ids(ui.active_paint_cache)=="copper|blue|add|delta")
        ''')

    def test_partial_omission_keeps_identity_unknown_without_mutation_evidence(self):
        self.lua.execute('''
        open_player();warm_categories()
        local fresh=player_model({complete=false,stockComplete=false,catalogComplete=false})
        table.remove(fresh.rows,1);ui:reconcile_capture(fresh);drain_render();warm_categories()
        assert(#fresh.rows==7 and #ui.model.rows==8)
        ui.checking_expanded=true;page("missing");local amber=tile("amber")
        assert(amber and amber.count.text=="?" and not amber.absent and not amber.row.stockLocationHints)
        assert(not require("paintmanager.paint_view").learn_eligible(ui.model,amber.row))
        assert(not bulk_button().widget.enabled)
        local complete=player_model({catalogComplete=true});table.remove(complete.rows,1)
        ui:reconcile_capture(complete);drain_render();warm_categories()
        assert(ui.model==complete and #ui.model.rows==7 and not tile("amber"))
        ''')

    def test_removed_cell_detaches_and_survivor_keeps_widget_after_move(self):
        self.lua.execute('''
        open_player();warm_categories();local amber,zero=tile("amber"),tile("zero")
        local fresh=player_model({catalogComplete=true});table.remove(fresh.rows,1)
        ui:reconcile_capture(fresh);drain_render()
        assert(#ui.visible_tiles==1 and #ui.paint_list.children==1)
        assert(tile("zero")==zero and zero.position==1 and zero.cell.Slot.column==0)
        assert(not tile("amber") and not amber.cell.slot)
        ''')

    def test_reopen_same_snapshot_restores_cached_order_page_scroll_and_selected_tile(self):
        self.lua.execute('''
        assert(ui:toggle());ui.config.max_catalog=512
        local model=large_model(300,"P");render_model(model);ui:_page(1);drain_render()
        local selected=ui.visible_tiles[7].row.id;ui:_select_paint(selected,ui.render_generation)
        ui.active_paint_cache.scroll.native_scroll_offset=47
        ui:close("user");assert(ui:toggle());drain_render()
        assert(ui.page_offset==128 and ui.selected_paint_id==selected and ui.active_paint_cache.scroll.native_scroll_offset==47)
        ui:close("user");ui:set_model(nil);assert(ui:toggle());drain_render()
        assert(ui.page_offset==0 and not ui.selected_paint_id)
        ''')

    def test_saved_paint_tab_binds_native_switcher_on_initial_open_and_reopen(self):
        self.lua.execute('''
        for _,tab in ipairs({"ready","missing"}) do
            ui:set_preferences({lastPage=tab},{})
            assert(ui:toggle())
            render_model(player_model());warm_categories()
            for _=1,2 do
                local cache=ui.paint_caches[tab]
                assert(ui.active_tab==tab and ui.switcher.active==0)
                assert(ui.paint_views.active==cache.index and ui.active_paint_cache==cache)
                assert(ui.paint_views.children[ui.paint_views.active+1]==cache.scroll and cache.scroll.visible==0)
                assert(tile(tab=="ready" and "copper" or "delta"))
                assert(ui:close("user"))
                assert(ui:toggle());drain_render();warm_categories()
            end
            assert(ui:close("user"))
        end
        ''')

    def test_late_restored_tab_while_disconnected_is_the_page_shown_on_reconnection(self):
        self.lua.execute('''
        ui:set_connection({connected=false})
        assert(ui:toggle())
        local writes=0
        ui.callbacks.change_settings=function() writes=writes+1;return true end
        ui:set_preferences({lastPage="presets"},{})
        assert(ui.active_tab=="presets" and ui.content_panel.visible==1 and ui.preset_refresh_box.visible==1)
        ui:set_connection({connected=true});drain_render()
        assert(ui.content_panel.visible==0 and ui.switcher.active==3 and ui.preset_refresh_box.visible==0)
        assert(writes==0)
        ''')

    def test_search_change_during_patch_uses_new_query_and_fresh_model(self):
        self.lua.execute('''
        open_player();warm_categories();local fresh=player_model();fresh.rows[2].count=17
        ui:reconcile_capture(fresh);ui:set_view({query="zero"});drain_render()
        assert(ui.model==fresh and #ui.visible_tiles==1 and tile("zero").count.text=="17")
        ''')

    def test_vault_result_reveals_completed_learned_page_before_counts(self):
        self.lua.execute('''
        assert(ui:toggle())
        local early=player_model({learnedOnly=true,status="learned_capture",stockComplete=false})
        early.rows={early.rows[1],early.rows[2]}
        assert(ui:show_learned(early))
        local cache=ui.paint_caches.learned
        while not cache.ready do
            assert(cache.scroll.visible==2)
            ui:render_tick(work_budget(1))
            assert(ui:is_open())
        end
        local amber=tile("amber")
        assert(amber and amber.count.text=="?" and not amber.absent and not bulk_button().widget.enabled)
        for _=1,40 do ui:render_tick(work_budget(8)) end
        assert(not ui.paint_caches.ready.ready and not ui.paint_caches.missing.ready)
        local fresh=player_model({catalogComplete=true});ui:reconcile_capture(fresh);drain_render()
        assert(tile("amber")==amber and amber.count.text=="8" and ui.model==fresh)
        assert(not ui:show_learned(early) and ui.model==fresh)
        ''')

    def test_presets_suspend_pending_capture_and_other_preparation(self):
        self.lua.execute('''
        open_player();warm_categories();ui:reconcile_capture(player_model());local pending=ui.capture_patch
        page("presets");assert(not ui:is_rendering())
        local bank=ui.icons.pump
        function ui.icons:pump() error("Presets pumped unrelated icon preparation") end
        for _=1,40 do ui:render_tick(work_budget(8)) end
        assert(ui:is_open() and ui.capture_patch==pending and not ui.render_job)
        ui.icons.pump=bank;page("learned")
        assert(not ui.capture_patch and tile("amber").count.text=="8")
        ''')

    def test_reopened_learned_page_adds_confirmed_recipes_before_box_counts(self):
        self.lua.execute('''
        open_player();warm_categories()
        local old=ui.model
        ui:close("user");assert(ui:toggle(true));drain_render()
        local amber,zero=tile("amber"),tile("zero")
        local scroll=ui.active_paint_cache.scroll;scroll.native_scroll_offset=43
        ui:_select_paint("zero",ui.render_generation)
        function zero.count:SetText() error("Unchanged zero was redrawn") end
        function amber.count:SetText() error("Unchanged stock was redrawn") end
        local early={contextToken=old.contextToken,learnedOnly=true,stockComplete=false,rows={
            {id="new",recipeState="unlocked",displayName="A new paint",count=99},
            {id="blue",recipeState="unlocked",count=100}, {id="zero",recipeState="unlocked"}}}
        assert(ui:show_learned(early,old));drain_render()
        assert(tile("zero")==zero and tile("amber")==amber and zero.absent and amber.count.text=="8")
        assert(tile("blue").count.text=="4" and tile("new").count.text=="?" and tile("new").row.count==nil)
        assert(old.rows[3].recipeState=="locked" and #old.rows==8 and ui.capture_source==old)
        assert(cache_ids(ui.active_paint_cache)=="amber|zero|new|blue")
        assert(ui.selected_paint_id=="zero" and scroll.native_scroll_offset==43)
        page("ready");ui:_select_paint("copper",ui.render_generation)
        assert(not ui.detail_learn_control.widget.enabled and not bulk_button().widget.enabled)
        ui:_request_learning("copper",ui.render_generation);bulk_button().action()
        assert(#learned==0 and bulk_learned==0)
        local fresh=player_model({catalogComplete=true});fresh.rows[3].recipeState="unlocked"
        table.insert(fresh.rows,{id="new",recipeState="unlocked",displayName="A new paint",count=2})
        ui:reconcile_capture(fresh);drain_render()
        assert(ui.model==fresh and not ui:_paint_actions_pending() and bulk_button().widget.enabled)
        page("learned");assert(tile("new").count.text=="2" and tile("amber")==amber)
        ''')

    def test_unchanged_warm_vault_keeps_the_existing_page_without_repainting(self):
        self.lua.execute('''
        open_player();warm_categories()
        local old,cache,proj=ui.model,ui.active_paint_cache,ui.paint_projection
        local visible,generation=cache.visible_tiles,ui.render_generation
        assert(ui:show_learned({contextToken=old.contextToken,learnedOnly=true,stockComplete=false,
            rows={{id="amber",recipeState="unlocked"}}},old))
        drain_render()
        assert(cache.visible_tiles==visible and ui.paint_projection==proj and ui.render_generation==generation)
        assert(ui.waiting_stock and not bulk_button().widget.enabled)
        ''')

    def test_full_warm_catalog_reserves_space_for_unique_vault_ids(self):
        self.lua.execute('''
        ui.config.max_catalog=4;assert(ui:toggle())
        local old=player_model()
        while #old.rows>4 do table.remove(old.rows) end
        render_model(old)
        local early={contextToken=old.contextToken,learnedOnly=true,stockComplete=false,rows={
            {id="blue",recipeState="unlocked",displayName="Blue revised"},
            {id="Paint /Game/New",recipeState="unlocked",displayName="New"},
            {id="/Game/New",recipeState="unlocked",displayName="Duplicate alias"},
            {id="extra",recipeState="unlocked",displayName="Extra"}}}
        assert(ui:show_learned(early,old));drain_render()
        assert(#ui.model.rows==4 and #ui.visible_tiles==4 and tile("amber").count.text=="8")
        assert(tile("blue").row.displayName=="Blue revised" and tile("blue").count.text=="4")
        assert(tile("Paint /Game/New").count.text=="?" and tile("extra").count.text=="?")
        assert(not tile("/Game/New") and not tile("zero") and #old.rows==4)
        ''')

    def test_partial_result_reopens_with_retained_order_and_no_new_projection(self):
        self.lua.execute('''
        open_player();warm_categories()
        local partial=player_model({catalogComplete=false,complete=false,stockComplete=false})
        table.remove(partial.rows,3)
        local extra={id="a",displayName="AAAA",assetType="Item",assetName="New",recipeState="unlocked"}
        table.insert(partial.rows,1,extra)
        ui:reconcile_capture(partial);drain_render();warm_categories()
        assert(ui.capture_source==partial and ui.model~=partial)
        assert(cache_ids(ui.active_paint_cache)=="amber|zero|a")
        local view=ui.model
        ui:close("user");require("paintmanager.paint_view").begin=function() error("Duplicated projection") end
        assert(ui:toggle());drain_render();page("missing");page("learned")
        assert(ui.model==view and ui.capture_source==partial)
        assert(cache_ids(ui.active_paint_cache)=="amber|zero|a")
        ''')

    def test_completed_pass_handles_full_name_and_unresolved_path_as_one_identity(self):
        self.lua.execute('''
        local previous=player_model()
        previous.rows[1].id="Paint /Game/Amber"
        assert(ui:toggle());render_model(previous);warm_categories()
        ui:_select_paint("Paint /Game/Amber",ui.render_generation)
        local existing=tile("Paint /Game/Amber")
        local updated=player_model({complete=false,catalogComplete=false})
        updated.rows[1].id="/Game/Amber"
        ui:reconcile_capture(updated);drain_render()
        assert(ui.model==updated and #ui.model.rows==8 and tile("/Game/Amber")==existing)
        assert(ui.selected_paint_id=="/Game/Amber")
        ''')

    def test_synchronous_profile_limits_early_presentation_to_learned_page(self):
        self.lua.execute('''
        local processing=require("paintmanager.processing")
        assert(processing.request({batching_enabled=false,automatic_preparation=true}))
        assert(processing.commit())
        assert(ui:toggle())
        local early=large_model(70,"Known")
        early.learnedOnly,early.stockComplete,early.status=true,false,"learned_capture"
        assert(ui:show_learned(early))
        ui:render_tick(work_budget(100000))
        assert(not ui:is_rendering() and ui.active_paint_cache.ready and #ui.visible_tiles==70)
        assert(not ui.paint_caches.ready.ready and not ui.paint_caches.missing.ready)
        local captured=large_model(75,"Known");ui:reconcile_capture(captured);drain_render()
        for _,tab in ipairs({"missing","ready","presets","learned"}) do page(tab) end
        assert(ui.model==captured and not ui:show_learned(early) and #commands==0)
        ''')

    def test_first_ready_page_after_learned_pass_honors_sort_and_favorites(self):
        self.lua.execute(r'''
        assert(ui:toggle());ui:set_preferences({sort="count_desc"},{["Item\0B"]=true})
        local early=player_model({learnedOnly=true,status="learned_capture",stockComplete=false})
        early.rows={early.rows[1],early.rows[2]};assert(ui:show_learned(early));drain_render()
        local fresh=player_model({catalogComplete=true});fresh.rows[5].count=12
        ui:reconcile_capture(fresh);drain_render();page("ready")
        assert(cache_ids(ui.active_paint_cache)=="blue|delta|copper")
        ''')

    def test_status_settlement_cannot_reenable_learn_while_reconciliation_is_pending(self):
        self.lua.execute('''
        open_player();warm_categories();page("ready")
        local learn=ui.detail_learn_control;local action=learn.action
        assert(learn.widget.enabled)
        ui:reconcile_capture(player_model({stockComplete=false}))
        ui:set_learning(false)
        assert(not learn.widget.enabled)
        action();assert(#learned==0)
        ''')
