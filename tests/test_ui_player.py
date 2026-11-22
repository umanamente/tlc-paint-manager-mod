"""Player paint-grid, metadata, progress and hidden-control regressions."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS


PLAYER_FIXTURE = r'''
function player_model(overrides)
    return learn_model({
        {id="amber",assetType="Item",assetName="A",displayName="Amber",count=8,recipeState="unlocked",iconPath="Texture2D /Game/Icon.Real"},
        {id="zero",assetType="Item",assetName="Z",displayName="Zero known",count=0,recipeState="unlocked",iconPath="Texture2D /Game/Icon.Real"},
        {id="blue",assetType="Item",assetName="B",displayName="blue",count=4,recipeState="locked",iconPath="Texture2D /Game/Icon.Real"},
        {id="copper",assetType="Item",assetName="C",displayName="Copper",count=6,recipeState="locked"},
        {id="delta",assetType="Item",assetName="D",displayName="Delta",count=3,recipeState="locked",iconPath="Texture2D /Game/Icon.Real"},
        {id="cyan",assetType="Item",assetName="CY",displayName="Cyan",count=9,recipeState="locked",complete=false},
        {id="echo",assetType="Item",assetName="E",displayName="Echo",count=7,recipeState="unknown"},
        {id="hidden",assetType="Item",assetName="H",displayName="Cached zero",count=0,recipeState="locked",iconPath="Texture2D /Game/Icon.Real"},
    }, overrides)
end
function paint_names()
    local names={}
    for _,tile in ipairs(ui.visible_tiles or {}) do names[#names+1]=tile.row.displayName end
    return table.concat(names,"|")
end
function open_player() assert(ui:toggle());render_model(player_model()) end
function page(name) assert(ui:_select_tab(name));drain_render() end
function tile(id) for _,entry in ipairs(ui.visible_tiles or {}) do if entry.row.id==id then return entry end end end
'''


class UIPlayerTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PLAYER_FIXTURE)

    def run_lua(self, source):
        self.lua.execute(source)

    def test_square_grid_three_disjoint_pages_and_cached_zero(self):
        self.run_lua('''
        open_player();assert(ui.paint_list.kind=="UniformGridPanel" and paint_names()=="Amber|Zero known")
        for _,child in ipairs(ui.paint_list.children) do assert(child.width==child.height) end
        page("ready");assert(paint_names()=="blue|Copper")
        page("missing");assert(paint_names()=="Cached zero|Delta")
        assert(ui.checking_button.visible==0)
        ui.checking_control.action();drain_render()
        assert(paint_names()=="Cached zero|Delta|Cyan|Echo")
        assert(ui.tab_controls.missing.label.text=="Need Cans (2)")
        assert(#commands==0 and #learned==0)
        ''')

    def test_learned_and_unlearned_zero_dim_only_decorative_image(self):
        self.run_lua('''
        open_player();local zero=tile("zero");local positive=tile("amber")
        assert(zero.absent and zero.image.enabled==false and zero.image.opacity==0.4)
        assert(zero.widget.enabled and zero.star.enabled and zero.count.text=="0")
        assert(not positive.absent and positive.image.enabled and positive.image.opacity==nil)
        page("missing");local missing=tile("hidden");local few=tile("delta")
        assert(missing.absent and missing.image.enabled==false and missing.image.opacity==0.4)
        assert(missing.widget.enabled and missing.star.enabled and missing.count.text=="0")
        assert(not few.absent and few.image.enabled and few.image.opacity==nil)
        ''')

    def test_unknown_stock_is_neither_zero_nor_ready_and_stays_checking(self):
        self.run_lua('''
        assert(ui:toggle());render_model(player_model({stockComplete=false,cachePreview=true}))
        assert(paint_names()=="Amber|Zero known")
        for _,entry in ipairs(ui.visible_tiles) do assert(entry.count.text=="?" and not entry.absent and entry.image.enabled) end
        page("ready");assert(paint_names()=="" and #learn_buttons()==0)
        ui.checking_expanded=true;page("missing")
        assert(ui.tab_controls.missing.label.text=="Need Cans (0)" and #ui.visible_tiles==6 and #learn_buttons()==0)
        ''')

    def test_backpack_counts_update_badges_sort_and_absence_across_cached_pages(self):
        self.run_lua('''
        assert(ui:toggle());ui.view.sort="count_desc"
        render_model(player_model({contextToken="player",backpackCounts={amber=2,zero=12,hidden=4}}))
        assert(paint_names()=="Zero known|Amber")
        assert(tile("zero").count.text=="12" and not tile("zero").absent and tile("amber").count.text=="10")
        page("ready");assert(tile("hidden").count.text=="4" and not tile("hidden").absent)
        ui:_select_paint("hidden",ui.render_generation)
        assert(ui.detail_description.text:find("Backpack and ship boxes: 4",1,true))
        assert(ui.detail_learn_box.visible==0 and ui.detail_learn_control.widget.enabled)
        assert(ui.learn_all_ready_count==3)
        local stale=ui.detail_learn_control.action
        ui:update_backpack_counts("player", {});drain_render()
        assert(not tile("hidden") and ui.learn_all_ready_count==2)
        stale();assert(#learned==0)
        page("missing")
        assert(tile("hidden").absent and tile("hidden").count.text=="0")
        page("learned");assert(paint_names()=="Amber|Zero known")
        ui:update_backpack_counts("player", nil);drain_render()
        assert(tile("zero").count.text=="?" and not tile("zero").absent and tile("amber").count.text==">=8")
        assert(ui.tab_controls.missing.label.text=="Need Cans (0)" and ui.learn_all_ready_count==2)
        ''')

    def test_partial_box_capture_and_backpack_changes_during_patch_keep_latest_observation(self):
        self.run_lua('''
        assert(ui:toggle());render_model(player_model({contextToken="player",backpackCounts={zero=2}}))
        assert(ui:update_stock(player_model({contextToken="player",stockComplete=false,backpackCounts={}})))
        ui:update_backpack_counts("player",{zero=3});ui:update_backpack_counts("player",{zero=7})
        for _=1,1000 do ui:tick(work_budget(60)); if not ui:is_rendering() and not ui.pending_backpack and not ui.stock_patch_job then break end end
        assert(tile("zero").count.text==">=7" and not tile("zero").absent)
        assert(tile("amber").count.text==">=8" and ui.model.backpackCounts.zero==7)
        ''')

    def test_total_on_paint_toolbar_handles_captures_transfers_partial_reads_and_reset(self):
        self.run_lua('''
        assert(ui:toggle());assert(ui.stock_total_label.text=="Total cans: ?")
        local view=require("paintmanager.paint_view")
        local captured=player_model({contextToken="player",physicalCount=32})
        view.set_backpack_counts(captured,{zero=5,amber=3})
        render_model(captured)
        assert(ui.stock_total_label.text=="Total cans: 40" and ui.stock_radius_label.text:find("35 m",1,true))
        local label=ui.stock_total_label
        page("ready");assert(ui.stock_total_label==label and label.text=="Total cans: 40")
        page("presets");assert(ui.switcher.active~=0)
        page("learned");ui:update_backpack_counts("player",{zero=9,amber=3})
        assert(label.text=="Total cans: 44")
        local moved=player_model({contextToken="player",physicalCount=27})
        view.set_backpack_counts(moved,{zero=14,amber=3})
        assert(ui:update_stock(moved) and label.text=="Total cans: 44")
        for _=1,500 do ui:tick(work_budget(50)); if not ui.stock_patch_job then break end end
        moved.stockComplete=false;assert(ui:update_stock(moved) and label.text=="Total cans: >=44")
        local empty=player_model({contextToken="player",physicalCount=0})
        view.set_backpack_counts(empty,{});ui:set_model(empty);assert(label.text=="Total cans: 0")
        ui:update_backpack_counts("player",nil);assert(label.text=="Total cans: ?")
        ui:set_model(nil);assert(label.text=="Total cans: ?")
        ''')

    def test_partial_lower_bound_is_distinct_from_confirmed_absence(self):
        self.run_lua('''
        assert(ui:toggle());render_model(player_model({stockComplete=false}))
        assert(tile("amber").count.text==">=8" and tile("zero").count.text=="?")
        assert(not tile("amber").absent and not tile("zero").absent)
        ''')

    def test_native_search_is_literal_page_only_and_counts_stay_unfiltered(self):
        self.run_lua('''
        open_player();page("ready")
        ui.search_box.text="COP";ui:tick(work_budget(8));drain_render()
        assert(ui.view.query=="COP" and paint_names()=="Copper" and ui.tab_controls.ready.label.text=="Can Learn (2)")
        ui.search_box.text=".*";ui:tick(work_budget(8));drain_render();assert(paint_names()=="")
        page("settings");local reads=event_count("get_text:EditableTextBox")
        ui.search_box.text="Amber";ui:tick(work_budget(8));assert(event_count("get_text:EditableTextBox")==reads)
        ''')

    def test_search_never_uses_internal_identity(self):
        self.run_lua('''
        open_player();ui:set_view({query="Item"});drain_render();assert(paint_names()=="")
        ui:set_view({query="A"});drain_render();assert(paint_names()=="Amber")
        ''')

    def test_favorites_sort_over_full_catalog_before_paging(self):
        self.run_lua(r'''
        assert(ui:toggle());ui.config.max_catalog=512
        local model=large_model(260,"P");model.rows[260].displayName="ZZZ favorite"
        ui:set_preferences({sort="name_asc"},{["Item\0P260"]=true});render_model(model)
        assert(#ui.visible_tiles==128 and ui.visible_tiles[1].row.id=="P260" and ui.has_more)
        ui:_page(1);drain_render();assert(ui.page_offset==128 and #ui.visible_tiles==128)
        ui:_page(1);drain_render();assert(ui.page_offset==256 and #ui.visible_tiles==4 and not ui.has_more)
        ui:_page(-1);drain_render();assert(ui.page_offset==128)
        ''')

    def test_sort_changes_persist_and_preserve_filter(self):
        self.run_lua('''
        open_player();page("ready")
        ui:_change_settings({sort="name_desc"});drain_render();assert(paint_names()=="Copper|blue")
        ui:_change_settings({sort="count_asc"});drain_render();assert(paint_names()=="blue|Copper")
        ui:_change_settings({sort="count_desc"});drain_render();assert(paint_names()=="Copper|blue" and #setting_changes>=3)
        ''')

    def test_star_is_metadata_only_and_sorts_on_next_normal_render(self):
        self.run_lua('''
        open_player();ui:_toggle_favorite(tile("zero").row,ui.render_generation);drain_render()
        assert(paint_names()=="Amber|Zero known")
        assert(#favorite_changes==1 and favorite_changes[1][1]=="Item" and favorite_changes[1][2]=="Z" and favorite_changes[1][3])
        assert(#commands==0 and #learned==0 and bulk_learned==0)
        ui:set_model(ui.model);drain_render();assert(paint_names()=="Zero known|Amber")
        ''')

    def test_rejected_star_and_setting_roll_back(self):
        self.run_lua(r'''
        open_player();ui.callbacks.favorite=function() return false,"Storage unavailable" end
        local generation=ui.render_generation;ui:_toggle_favorite(tile("zero").row,generation)
        assert(ui.favorites["Item\0Z"]~=true and ui.render_generation==generation and ui.status.state=="error")
        ui.callbacks.change_settings=function() return false,"Storage unavailable" end
        page("settings");ui.checkboxes.showNames.widget:SetIsChecked(true);ui:tick(work_budget(8))
        assert(not ui.preferences.showNames and not ui.checkboxes.showNames.widget:IsChecked())
        ''')

    def test_search_does_not_narrow_batch_scope(self):
        self.run_lua('''
        open_player();ui:set_view({query="no matches"});drain_render()
        assert(paint_names()=="" and ui.learn_all_ready_count==2)
        bulk_button().action();assert(bulk_learned==1 and ui.learning)
        ''')

    def test_selected_paint_learns_only_on_explicit_action(self):
        self.run_lua('''
        open_player();page("ready")
        assert(#learn_buttons()==1 and #learned==0)
        ui:_select_paint("copper",ui.render_generation);drain_render()
        assert(ui.selected_paint_id=="copper" and #learned==0)
        click("Learn");assert(#learned==1 and learned[1]=="copper" and ui.learning)
        assert(not learn_buttons()[1].widget.enabled and not bulk_button().widget.enabled)
        ''')

    def test_view_and_context_replacement_retire_single_and_bulk_actions(self):
        self.run_lua('''
        open_player();page("ready")
        local old=learn_buttons()[1].action;local batch=bulk_button().action
        ui:set_view({query="Copper"});old();batch()
        assert(#learned==0 and bulk_learned==0 and not ui.learning)
        drain_render();local next_old=learn_buttons()[1].action
        ui:set_model(nil);next_old();drain_render();assert(#learned==0 and ui.selected_paint_id==nil)
        ''')

    def test_close_and_hidden_page_cannot_replay_learning(self):
        self.run_lua('''
        open_player();page("ready");local old=learn_buttons()[1].action
        page("settings");old();assert(#learned==0)
        page("ready");local latest=learn_buttons()[1].action
        ui:close("user");latest();assert(#learned==0 and not ui.learning)
        assert(ui:toggle());drain_render();latest();assert(#learned==0)
        ''')

    def test_learning_rejections_release_gate_and_show_error(self):
        self.run_lua('''
        open_player();page("ready")
        ui.callbacks.learn_recipe=function() return false,"Stock changed" end
        click("Learn");assert(not ui.learning and learn_buttons()[1].widget.enabled and ui.status.state=="error")
        ui.callbacks.learn_all=function() error("Queue unavailable") end
        click("Learn all");assert(not ui.learning and bulk_button().widget.enabled and ui.status.state=="error")
        ''')

    def test_long_unicode_search_and_failed_read_restore_input(self):
        self.run_lua('''
        open_player();ui.search_box.text=string.rep("é",200)
        ui:tick(work_budget(8));drain_render()
        assert(#ui.view.query<=256 and utf8.len(ui.view.query)~=nil and ui.search_box.text==ui.view.query)
        fail_get_text=true;ui:tick(work_budget(8))
        assert(not ui:is_open() and not pc.bShowMouseCursor and move==0 and look==0)
        ''')

    def test_reopen_keeps_search_and_restored_preferences(self):
        self.run_lua('''
        open_player();page("ready");ui:set_view({query="co"});drain_render()
        ui:_change_settings({sort="count_desc",showNames=true});drain_render()
        ui:close("user");assert(ui:toggle());drain_render()
        assert(ui.active_tab=="ready" and ui.search_box.text=="co" and paint_names()=="Copper")
        assert(ui.preferences.showNames and ui.view.sort=="count_desc")
        ''')

    def test_cold_preset_click_survives_delayed_preferences_and_receipts(self):
        self.run_lua('''
        local snapshot,writes=nil,0
        local doc={sections={settings={data={lastPage="ready",sort="count_desc"}},favorites={data={records={}}}}}
        local prefs=require("paintmanager.ui_preferences").new({
            ready=function() if snapshot then return "A",snapshot end end,
            publish=function(settings, favorites) ui:set_preferences(settings,favorites) end,
            store=function() return {begin_ui_settings=function(changes)
                writes=writes+1;assert(changes.lastPage=="presets")
                return {step=function() doc.sections.settings.data.lastPage=changes.lastPage;return {state="done"} end,
                    get_result=function() return doc end}
            end} end,
        })
        ui.callbacks.change_settings=function(changes)
            local ok,code=prefs:change_settings(changes)
            return ok,code,code=="preferences_not_ready" and "preferences_loading" or code
        end
        assert(ui:toggle())
        for _,tab in ipairs({"missing","learned","presets"}) do
            ui.tab_controls[tab].widget.down=true;ui:tick(work_budget(0),true)
            ui.tab_controls[tab].widget.down=false;ui:tick(work_budget(0),true)
        end
        assert(ui.active_tab=="presets" and ui.status.state~="error" and not prefs:is_busy() and writes==0)
        snapshot={accountId="A",scopeId="A",path="prefs",loadGeneration=1}
        assert(prefs:load(doc));ui:flush_page_choice();ui:flush_page_choice()
        assert(ui.active_tab=="presets" and ui.view.sort=="count_desc" and #prefs.pending==1)
        for _=1,3 do prefs:step(work_budget(8),true) end
        assert(not prefs:is_busy() and writes==1 and doc.sections.settings.data.lastPage=="presets")
        assert(prefs:load(doc));ui:flush_page_choice();assert(not prefs:is_busy())
        ui:close("user");assert(ui:toggle() and ui.active_tab=="presets")
        assert(ui:_change_settings({lastPage="missing"}) and ui.active_tab=="missing")
        ''')

    def test_cold_click_on_default_learned_keeps_page_until_next_window(self):
        self.run_lua('''
        local writes=0
        ui.callbacks.change_settings=function() return false,"Loading","preferences_loading" end
        assert(ui:toggle());ui.tab_controls.learned.widget.down=true;ui:tick(work_budget(0),true)
        ui:set_preferences({lastPage="presets"},{})
        assert(ui.active_tab=="learned" and ui.preferences.lastPage=="presets")
        ui.callbacks.change_settings=function(changes) writes=writes+1;assert(changes.lastPage=="learned");return true end
        ui:flush_page_choice();ui:flush_page_choice()
        assert(writes==1 and ui.preferences.lastPage=="learned")
        ui:close("user");assert(ui:toggle() and ui.active_tab=="learned")
        ui:set_preferences({lastPage="ready"},{})
        assert(ui.active_tab=="ready")
        ''')

    def test_pending_click_is_retired_on_close_or_replaced_character(self):
        self.run_lua('''
        ui.callbacks.change_settings=function() return false,"Loading","preferences_loading" end
        assert(ui:toggle());page("presets");ui:close("user")
        ui.callbacks.change_settings=function() error("Retired window attempted to persist") end
        ui:set_preferences({lastPage="ready"},{});ui:flush_page_choice()
        assert(ui:toggle() and ui.active_tab=="ready")
        ui.callbacks.change_settings=function() return false,"Loading","preferences_loading" end
        page("missing");pc.Pawn=object("AnotherPawn")
        ui.callbacks.change_settings=function() error("Retired context attempted to persist") end
        ui:set_preferences({lastPage="ready"},{});ui:flush_page_choice()
        assert(ui.status.state~="error" and not ui.pending_page)
        ''')

    def test_rejection_other_than_cold_storage_is_visible(self):
        self.run_lua('''
        ui.callbacks.change_settings=function() return false,"Queue full","preferences_queue_full" end
        assert(ui:toggle());page("presets")
        assert(ui.active_tab=="presets" and ui.status.state=="error" and ui.status.message=="Queue full" and not ui.pending_page)
        ''')

    def test_developer_checkbox_hides_retires_and_disarms_controls(self):
        self.run_lua('''
        open_player();assert(ui.debug_tab.visible==1 and not ui.debug_built and not ui:_select_tab("debug"))
        click("Settings");assert(ui.active_tab=="settings")
        ui.checkboxes.showDeveloperTools.widget:SetIsChecked(true);ui:tick(work_budget(8))
        assert(ui.preferences.showDeveloperTools and ui.debug_tab.visible==0)
        ui:set_connection({connected=false});assert(ui.debug_tab.visible==1)
        ui:set_connection({connected=true});assert(ui.debug_tab.visible==0)
        ui:tick(work_budget(0),true)
        click("Debug");assert(ui.active_tab=="debug");click("Diagnostics")
        click("Prepare JMAP");assert(ui.jmap_armed)
        local stale;for _,button in ipairs(ui.buttons) do if button.label and button.label.text=="Confirm JMAP" then stale=button.action end end
        ui:_change_settings({showDeveloperTools=false})
        assert(ui.active_tab=="settings" and not ui.jmap_armed and not ui.debug_built and ui.debug_tab.visible==1)
        ui:set_connection({connected=false});ui:set_connection({connected=true});assert(ui.debug_tab.visible==1)
        stale();assert(#commands==0)
        for _,button in ipairs(ui.buttons) do assert(button.scope~="debug") end
        ''')

    def test_jmap_needs_separate_explicit_arm_and_confirm(self):
        self.run_lua('''
        open_player();ui:_change_settings({showDeveloperTools=true});page("debug");click("Diagnostics")
        for _,button in ipairs(ui.buttons) do assert(not button.label or button.label.text~="Confirm JMAP") end
        assert(#commands==0)
        click("Prepare JMAP");assert(ui.jmap_armed and #commands==0)
        click("Confirm JMAP");assert(#commands==1 and commands[1]=="jmap" and not ui.jmap_armed)
        ''')

    def test_no_container_widgets_or_handlers_even_in_debug(self):
        self.run_lua('''
        local model=player_model({sources={{id="PrivateBox",displayName="Never show this box",location={X=12345}}}})
        model.rows[1].stockSources={{displayName="Never show this detail",count=8}}
        assert(ui:toggle());render_model(model)
        assert(ui.source_list==nil and ui._request_stock_details==nil and not ui:_select_tab("sources"))
        ui:_change_settings({showDeveloperTools=true});page("debug");click("Diagnostics")
        assert(not text_contains("Never show this") and not text_contains("PrivateBox"))
        assert(not text_contains("Show containers") and not text_contains("Containers counted"))
        for _,button in ipairs(ui.buttons) do assert(button.kind~="stock_details" and button.scope~="sources") end
        ui:_request("sources");assert(#commands==0)
        ''')

    def test_native_progress_truthful_modes_units_and_stale_generation(self):
        self.run_lua('''
        open_player()
        ui:set_progress({generation=3,state="running",kind="stock",phase="source_discovery",mode="indeterminate"})
        assert(ui.spinner.visible==0 and ui.progress_bar.visible==1)
        ui:set_progress({generation=3,state="running",phase="inventory_capture",mode="determinate",completed=8,total=15,unit="objects"})
        assert(ui.spinner.visible==1 and ui.progress_bar.visible==0 and ui.progress_bar.percent==8/15)
        assert(ui.progress_label.text:find("8/15",1,true) and ui.progress_label.text:find("7 left",1,true))
        assert(ui.progress_label.tooltip~=nil)
        assert(not ui:set_progress({generation=2,state="running",mode="indeterminate"}))
        assert(ui.progress_bar.visible==0)
        ui:set_progress({generation=3,state="error",coverage="partial",mode="determinate",completed=8,total=15})
        assert(ui.spinner.visible==1 and ui.progress_bar.visible==1 and ui.progress_label.text=="Some stock is unavailable")
        assert(ui.progress_label.tooltip==nil)
        ui:set_progress({generation=4,state="done",mode="indeterminate"})
        assert(ui.spinner.visible==1 and ui.progress_bar.visible==1 and ui.progress_label.text=="Updated")
        ''')

    def test_container_progress_label_and_bar_follow_the_scheduled_snapshot(self):
        self.run_lua('''
        open_player()
        local tracker=require("paintmanager.ui_progress").new({ui_progress_delay_ms=0,ui_progress_update_ms=100})
        local function publish(now,checked,total)
            local p={generation=1,kind="stock",state="running",phase=checked and "container_read" or "source_discovery",
                unit=checked and "containers",completed=checked,total=total,mode=total and "determinate" or "indeterminate"}
            local next_view=tracker:offer(p,now)
            if next_view then ui:set_progress(next_view) end
        end
        publish(0,nil,nil)
        assert(ui.progress_label.text=="Finding containers..." and ui.progress_bar.visible==1)
        publish(100,0,0)
        assert(ui.progress_label.text=="Containers checked: 0 / 0" and ui.progress_bar.visible==1)
        publish(200,1,nil)
        assert(ui.progress_label.text:find("1",1,true) and ui.progress_label.text:find("unknown",1,true))
        assert(ui.progress_bar.visible==1)
        publish(300,2,8)
        assert(ui.progress_label.text=="Containers checked: 2 / 8" and ui.progress_bar.percent==0.25)
        publish(340,3,8)
        assert(ui.progress_label.text=="Containers checked: 2 / 8")
        ui:set_progress(tracker:pump(400))
        assert(ui.progress_label.text=="Containers checked: 3 / 8" and ui.progress_bar.percent==3/8)
        ''')

    def test_buttons_use_one_line_ellipsis_clipping_and_bounded_tooltips(self):
        self.run_lua('''
        open_player();page("ready")
        for _,button in ipairs(ui.buttons) do
            if button.label then
                assert(button.label.wrap==false and button.label.overflow==1 and button.label.clipping==1)
            else assert(button.kind=="favorite" or button.kind=="paint_select") end
        end
        local tooltip=tile("blue").widget.tooltip
        assert(tooltip and tooltip.child.width==360 and tooltip.child.child.wrap==true)
        assert(tooltip.child.child.wrap_width==nil)
        assert(tile("blue").widget.enabled)
        ''')

    def test_settings_only_offer_ship_scan_radii_and_presets_are_available_to_players(self):
        self.run_lua('''
        open_player();click("Settings")
        local expected={["35 m"]=true,["40 m"]=true,["45 m"]=true,["50 m"]=true}
        for _,record in ipairs(ui.buttons) do
            if record.scope=="settings" and record.label and record.label.text:match("^%d+ m$") then
                assert(expected[record.label.text]);expected[record.label.text]=nil
            end
        end
        assert(next(expected)==nil and not text_contains("Preset range"))
        click("45 m");assert(ui.preferences.shipRadiusCm==4500)
        local count=0
        for _,record in ipairs(ui.buttons) do
            if record.scope=="settings" and record.label and record.label.text=="?" then
                count=count+1;record.action()
                assert(ui.settings_help_text.visible==0 and #ui.settings_help_text.text>32)
                assert(record.widget.tooltip.child.child.text==ui.settings_help_text.text)
            end
        end
        assert(count==6)
        local tabs=ui.content_panel.children[1]
        assert(#tabs.children==4)
        for i,tab in ipairs({"learned","ready","missing","presets"}) do
            assert(tabs.children[i]==ui.tab_controls[tab].widget.Slot.Parent)
        end
        local title=ui.close_button.Slot.Parent.Slot.Parent
        assert(title.children[2]==ui.tab_controls.settings.widget.Slot.Parent and title.children[3]==ui.debug_tab)
        click("Presets");assert(ui.active_tab=="presets" and not ui.preferences.showDeveloperTools)
        click(ui.tab_controls.learned.label.text);drain_render();assert(ui.active_tab=="learned")
        ''')

    def test_switching_language_preserves_native_host_selection_text_input_and_action_counts(self):
        self.run_lua('''
        local messages=require("paintmanager.i18n")
        local ru=messages.language_name("ru")
        local game_language="en-US"
        open_player()
        local model,host=ui.model,ui.host
        page("settings")
        ui.callbacks.change_settings=function(changes)
            if changes.uiLanguage then messages.select(changes.uiLanguage,game_language) end
            return true
        end
        local function binding_count()
            local n=0;for _ in pairs(ui.language_widgets) do n=n+1 end;return n
        end
        local fixed=binding_count()
        click(ru)
        assert(messages.language=="ru" and ui.preferences.uiLanguage=="ru" and ui.active_tab=="settings")
        assert(ui.host==host and ui.input_claimed and ui.model==model)
        assert(ui.search_box.hint==messages.text("ui.search.hint"))
        assert(ui.tab_controls.settings.label.text==messages.text("ui.tab.settings"))
        page("learned");assert(paint_names()=="Amber|Zero known")
        page("ready");page("missing");page("learned")
        assert(binding_count()==fixed and #commands==0 and #learned==0 and bulk_learned==0)
        page("settings")
        for _,code in ipairs(require("paintmanager.languages").choices) do
            if code~="auto" then
                click(messages.language_name(code))
                assert(messages.language==code and ui.preferences.uiLanguage==code)
                assert(ui.tab_controls.settings.label.text==messages.text("ui.tab.settings"))
            end
        end
        click(messages.text("ui.settings.language_auto"))
        assert(ui.preferences.uiLanguage=="auto" and messages.language=="en" and ui.host==host)
        game_language="ru-RU";messages.select("auto",game_language);ui:tick()
        assert(ui.tab_controls.settings.label.text==messages.text("ui.tab.settings"))
        assert(ui:close("user") and ui.language_widgets==nil and ui.language_choices==nil)
        ''')

    def test_language_refresh_renders_held_status_transfer_and_publication_again(self):
        self.run_lua('''
        open_player();page("presets")
        local messages=require("paintmanager.i18n")
        local function counter() return messages.text("main.transfer.result",{ moved=2, skipped=0 }) end
        local function publication() return messages.text("main.storage.local_staged") end
        ui:set_status({command="return_all",state="done",message=counter(),renderMessage=counter})
        ui:set_persistence(publication(),publication)
        ui:set_status({state="error",message="Native debug details",reasonKey="main.error.context_changed"})
        messages.select("ru","en");ui:tick()
        assert(ui.persistence_label.text==publication() and ui.last_transfer_line:find(counter(),1,true))
        assert(ui.status_label.text:find(messages.text("main.error.context_changed"),1,true))
        assert(not ui.status_label.text:find("Native debug",1,true))
        ''')


if __name__ == "__main__":
    unittest.main()
