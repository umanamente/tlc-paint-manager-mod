"""Bounded stock patches and disconnected-shell action admission."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE
from test_ui_selection_inplace import HELPERS


STOCK_FIXTURE = r'''
function finish_stock()
    for _=1,2000 do
        if not ui.stock_patch_job and not ui.stock_patch_pending then return end
        ui:render_tick(work_budget(4))
    end
    error("Stock patch did not finish")
end
function quiet_render()
    -- Completed-page prewarm is independent from count patching.
    ui._pump_render=function() end
    ui._pump_icons=function() end
end
function illustration_service()
    art_requests,art_imports,art_retires,art_releases=0,0,0,0
    local bank={targets={}}
    function bank:image() return nil end
    function bank:bind_illustration(image,id)
        assert(id=="signal_lost" and image.kind=="Image")
        art_requests=art_requests+1
        if art_bind_error then error("Unavailable illustration binding") end
        local target={image=image,active=true};self.targets[#self.targets+1]=target
        if self.texture then image:SetBrushFromTexture(self.texture,false);image:SetVisibility(4);return true,target end
        return false,target
    end
    function bank:pump(budget)
        if budget:expired() or #self.targets==0 or art_pending or art_failure then return end
        if not self.texture then art_imports=art_imports+1;self.texture=object("ApprovedSignalTexture") end
        for _,target in ipairs(self.targets) do
            if target.active and target.image then
                target.image:SetBrushFromTexture(self.texture,false);target.image:SetVisibility(4)
            end
        end
        self.targets={};budget:consume(1)
    end
    function bank:retire(target)
        art_retires=art_retires+1;target.active,target.image=false,nil
    end
    function bank:release_targets()
        art_releases=art_releases+1
        for _,target in ipairs(self.targets) do target.active,target.image=false,nil end
        self.targets={}
    end
    ui.callbacks.icons=function() return bank end
    return bank
end
'''


class UIStockSignalTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PLAYER_FIXTURE)
        self.lua.execute(HELPERS)
        self.lua.execute(STOCK_FIXTURE)

    def test_partial_stock_updates_reuse_widgets_and_clear_absence(self):
        self.lua.execute('''
        open_player();quiet_render();local before=preserve_grid()
        local snapshot=player_model({stockComplete=false,cachePreview=false})
        snapshot.rows[1].count=3;snapshot.rows[2].count=0
        assert(ui:update_stock(snapshot));assert(not ui.model.stockComplete)
        assert(not require("paintmanager.paint_view").learn_eligible(ui.model,ui.model.rows[3]))
        finish_stock();same_grid(before)
        assert(tile("amber").count.text==">=3" and tile("zero").count.text=="?")
        assert(not tile("zero").absent and tile("zero").image.enabled and tile("zero").image.opacity==1)
        assert(tile("amber").tooltip_label.text:find(">=3",1,true))
        assert(ui.detail_description.text:find(">=3",1,true))
        assert(not ui.learn_all_control.widget.enabled)
        ''')

    def test_restored_old_positive_counts_remain_unknown_and_nonactionable(self):
        self.lua.execute('''
        open_player();page("ready");quiet_render();local before=preserve_grid()
        local snapshot=player_model({stockComplete=false,cachePreview=true})
        snapshot.rows[3].count=999
        assert(ui:update_stock(snapshot));finish_stock();same_grid(before)
        assert(tile("blue").count.text=="?" and not tile("blue").absent)
        assert(not ui.detail_learn_control.widget.enabled)
        assert(not ui.detail_description.text:find("999",1,true))
        assert(not ui.learn_all_control.widget.enabled)
        ''')

    def test_exact_zero_and_positive_transitions_patch_existing_decorations(self):
        self.lua.execute('''
        open_player();quiet_render();local before=preserve_grid()
        local image=tile("amber").image;local zero_image=tile("zero").image
        local snapshot=player_model({stockComplete=true,stockRadiusCm=12345})
        snapshot.rows[1].count=0;snapshot.rows[2].count=2
        assert(ui:update_stock(snapshot));finish_stock();same_grid(before)
        assert(ui.model.stockComplete and tile("amber").count.text=="0")
        assert(tile("amber").absent and image.enabled==false and image.opacity==0.4)
        assert(tile("zero").count.text=="2" and not tile("zero").absent and zero_image.enabled and zero_image.opacity==1)
        assert(tile("amber").image==image and tile("zero").image==zero_image)
        assert(ui.category_counts.ready==2 and ui.category_counts.learned==2)
        ''')

    def test_row_incompleteness_is_preserved_and_missing_rows_are_unknown(self):
        self.lua.execute('''
        open_player();quiet_render();local snapshot=player_model({stockComplete=true})
        snapshot.rows[1].complete=false;table.remove(snapshot.rows,2)
        assert(ui:update_stock(snapshot));finish_stock()
        assert(ui.model.rows[1].complete==false and not ui.model.stockComplete)
        assert(tile("zero").row.count==nil and tile("zero").count.text=="?")
        assert(not tile("zero").absent)
        ''')

    def test_complete_stock_patch_moves_crossing_colors_on_the_open_and_cached_pages(self):
        self.lua.execute('''
        open_player();page("missing");page("ready");page("missing")
        local delta=tile("delta")
        local snapshot=player_model({backpackCounts={hidden=4,blue=2,delta=1}})
        snapshot.rows[3].count=2
        assert(ui:update_stock(snapshot));finish_stock();drain_render()
        assert(not tile("hidden") and not tile("delta") and #ui.visible_tiles==0)
        assert(ui.learn_all_ready_count==4 and ui.tab_controls.ready.label.text=="Can Learn (4)")
        page("ready");assert(tile("blue").count.text=="4" and tile("hidden").count.text=="4")
        assert(tile("delta").count.text=="4" and tile("delta")~=delta)
        ui:_select_paint("hidden",ui.render_generation)
        local old=ui.detail_learn_control.action
        local reduced=player_model({backpackCounts={blue=2}});reduced.rows[3].count=2
        assert(ui:update_stock(reduced));finish_stock();drain_render()
        old();assert(#learned==0 and not tile("hidden") and ui.learn_all_ready_count==2)
        page("missing");assert(tile("hidden").count.text=="0" and tile("delta").count.text=="3")
        ''')

    def test_patch_native_work_is_bounded_and_updates_hidden_complete_tabs(self):
        self.lua.execute('''
        open_player();page("ready");local ready=ui.active_paint_cache;local blue=tile("blue")
        page("learned");quiet_render();local snapshot=player_model({stockComplete=false})
        snapshot.rows[3].count=1
        local texts=0
        for _,cache in pairs(ui.paint_caches) do for _,entry in ipairs(cache.visible_tiles) do
            local original=entry.count.SetText
            function entry.count:SetText(value) texts=texts+1;return original(self,value) end
        end end
        assert(ui:update_stock(snapshot))
        for _=1,100 do
            local before=texts;ui:render_tick(work_budget(8));assert(texts-before<=2)
            if not ui.stock_patch_job then break end
        end
        assert(not ui.stock_patch_job and blue.count.text==">=1")
        assert(ui.active_paint_cache~=ready and ready.ready and ui.paint_views.active==ui.active_paint_cache.index)
        ''')

    def test_frequent_snapshots_coalesce_and_stale_context_or_replacement_cannot_patch(self):
        self.lua.execute('''
        open_player();quiet_render()
        local first=player_model({stockComplete=false});first.rows[1].count=1
        local latest=player_model({stockComplete=false});latest.rows[1].count=9
        assert(ui:update_stock(first));local job=ui.stock_patch_job
        ui:render_tick(work_budget(1));assert(ui:update_stock(latest) and ui.stock_patch_job==job)
        finish_stock();assert(tile("amber").count.text==">=9")
        local invalid=player_model({contextToken="another",stockComplete=false})
        assert(not ui:update_stock(invalid) and not ui.stock_patch_job)
        assert(ui:update_stock(first));ui:set_model(player_model())
        assert(not ui.stock_patch_job and not ui.stock_patch_pending)
        ''')

    def test_late_unpatched_rows_cannot_be_read_as_current_lower_bounds(self):
        self.lua.execute('''
        open_player();quiet_render();local snapshot=player_model({stockComplete=false})
        assert(ui:update_stock(snapshot))
        assert(not require("paintmanager.paint_view").stock_current(ui.model,ui.model.rows[1]))
        assert(not require("paintmanager.paint_view").learn_eligible(ui.model,ui.model.rows[3]))
        finish_stock();assert(require("paintmanager.paint_view").stock_current(ui.model,ui.model.rows[1]))
        ''')

    def test_cached_or_wrong_revision_count_is_not_ready_even_with_complete_flag(self):
        self.lua.execute('''
        local view=require("paintmanager.paint_view")
        local snapshot=player_model({stockComplete=true,cachePreview=true})
        assert(not view.learn_eligible(snapshot,snapshot.rows[3]) and view.classify(snapshot,snapshot.rows[3])=="checking")
        snapshot.cachePreview=false;snapshot._stockUiRevision=2;snapshot.rows[3]._stockUiRevision=1
        assert(not view.learn_eligible(snapshot,snapshot.rows[3]) and view.classify(snapshot,snapshot.rows[3])=="checking")
        ''')

    def test_reentrant_model_replacement_and_close_retire_remaining_native_patches(self):
        self.lua.execute('''
        open_player();quiet_render();local entry=tile("amber");local old=ui.model
        local replacement=player_model();replacement.rows[1].count=17
        function entry.count:SetText(value) self.text=value;ui:set_model(replacement) end
        assert(ui:update_stock(player_model({stockComplete=false})))
        finish_stock();assert(ui.model==replacement and ui.model~=old and not ui.stock_patch_job)
        ui:close("user");assert(not ui.stock_patch_job and not ui.stock_patch_pending)
        ''')

    def test_signal_loss_keeps_shell_open_and_blocks_actions_and_hidden_polling(self):
        self.lua.execute('''
        open_player();page("ready");local before=preserve_grid();local active=ui.active_tab
        local favorites=0;ui.callbacks.favorite=function() favorites=favorites+1;return true end
        assert(not ui:set_connection({connected=false,reason="outside_radius",distanceCm=25000,radiusCm=10000}))
        assert(ui:is_open() and ui.connection_panel.visible==0 and ui.content_panel.visible==1)
        for _,button in ipairs(ui.buttons) do
            if not button.connection_close then function button.widget:IsPressed() error("hidden control polled") end end
        end
        function ui.search_box:GetText() error("hidden search polled") end
        ui:tick(work_budget(0),true)
        ui:_request("refresh");assert(not ui:_change_settings({shipRadiusCm=5000}));ui:_select_tab("settings")
        ui:_select_paint("copper",ui.render_generation);ui:_toggle_favorite(tile("blue").row,ui.render_generation)
        ui:_request_learning("blue",ui.render_generation)
        ui:_request_learning_all(ui.render_generation,ui.model,ui.model.contextToken,ui.context_key)
        ui:_change_settings({sort="count_desc"});ui:_page(1)
        assert(ui.active_tab==active and favorites==0 and #commands==0 and #learned==0 and bulk_learned==0)
        same_grid(before)
        ''')

    def test_signal_lost_can_open_initially_and_reconnect_preserves_prepared_page(self):
        self.lua.execute('''
        ui:set_connection({connected=false})
        assert(ui:toggle());ui:set_model(player_model());drain_render()
        local list=ui.paint_list;local generation=ui.render_generation
        assert(ui.connection_panel.visible==0 and ui.content_panel.visible==1 and ui.active_paint_cache.ready)
        assert(ui:set_connection({connected=true,distanceCm=100,radiusCm=10000}))
        assert(ui:is_open() and ui.connection_panel.visible==1 and ui.content_panel.visible==0)
        assert(ui.paint_list==list and ui.render_generation==generation and ui.active_paint_cache.ready)
        ''')

    def test_disconnected_close_and_escape_keep_normal_cleanup(self):
        self.lua.execute('''
        open_player();ui:set_connection({connected=false})
        ui.buttons[1].widget.down=true;ui:tick(work_budget(0),true)
        assert(not ui:is_open() and move==0 and look==0)
        assert(ui:toggle())
        ui:request_escape();ui:tick(work_budget(0),true)
        assert(not ui:is_open() and move==0 and look==0)
        ''')

    def test_unchanged_connection_poll_has_no_native_visibility_text_or_action_updates(self):
        self.lua.execute('''
        open_player();ui:set_connection({connected=false,distanceCm=25000,radiusCm=10000})
        local setters=0
        for _,widget in ipairs({ui.connection_panel,ui.content_panel,ui.connection_actions[1],ui.connection_actions[2]}) do
            function widget:SetVisibility() setters=setters+1 end
        end
        function ui.connection_distance:SetText() setters=setters+1 end
        function ui.learn_all_control.widget:SetIsEnabled() setters=setters+1 end
        function ui.detail_learn_control.widget:SetIsEnabled() setters=setters+1 end
        for i=1,60 do ui:set_connection({connected=false,distanceCm=25000+i/100,radiusCm=10000}) end
        assert(setters==0)
        ui:set_connection({connected=true,distanceCm=10000,radiusCm=10000});setters=0
        for i=1,60 do ui:set_connection({connected=true,distanceCm=10000-i,radiusCm=10000}) end
        assert(setters==0)
        ''')

    def test_approved_illustration_is_lazy_and_uses_centered_responsive_two_to_one_fit(self):
        self.lua.execute('''
        local bank=illustration_service();open_player()
        assert(art_requests==0 and art_imports==0 and ui.connection_illustration.visible==1)
        local scale=ui.connection_illustration_scale
        assert(scale.stretch==2 and scale.stretch_direction==1)
        local size=scale.children[1]
        assert(size.width==1774 and size.height==887 and size.child==ui.connection_illustration)
        assert(size.Slot.horizontal_alignment==2 and size.Slot.vertical_alignment==2)
        assert(scale.Slot.size.SizeRule==1 and scale.Slot.horizontal_alignment==0 and scale.Slot.vertical_alignment==0)
        local image=ui.connection_illustration
        ui:set_connection({connected=false});assert(art_requests==1 and art_imports==0)
        ui:render_tick(work_budget(8));assert(art_imports==1 and image.visible==4 and image.texture==bank.texture)
        collect_native();assert(image:IsValid() and size:IsValid() and scale:IsValid())
        viewport_width,viewport_height,viewport_dpi=1280,720,1.5
        ui:_fit();assert(ui.connection_illustration==image and ui.connection_illustration_scale==scale)
        assert(size.width/size.height==2 and art_requests==1 and art_imports==1)
        ''')

    def test_pending_failed_or_missing_illustration_keeps_signal_text_and_close(self):
        self.lua.execute('''
        illustration_service();art_pending=true;ui:set_connection({connected=false});assert(ui:toggle())
        local panel=ui.connection_panel.child
        assert(panel.children[1].text=="Signal lost" and panel.children[2].text=="Ship connection unavailable")
        assert(art_requests==1 and art_imports==0 and ui.connection_illustration.visible==1)
        for _=1,5 do ui:render_tick(work_budget(8));ui:set_connection({connected=false}) end
        assert(ui.open and art_requests==1 and art_imports==0)
        ui:close("user");art_pending=false;art_bind_error=true;assert(ui:toggle())
        assert(ui.open and ui.connection_panel.visible==0 and ui.connection_illustration.visible==1)
        assert(ui.connection_illustration_error:find("Unavailable illustration binding",1,true))
        ui.buttons[1].widget.down=true;ui:tick(work_budget(0),true);assert(not ui.open)
        ''')

    def test_reconnect_reuses_illustration_and_close_retires_old_window_target(self):
        self.lua.execute('''
        local bank=illustration_service();ui:set_connection({connected=false});assert(ui:toggle())
        -- Rendering and lazy import share the same allowance. Await the first
        -- bounded import rather than assuming old worker poll left spare units.
        for _=1,100 do ui:render_tick(work_budget(8));if art_imports==1 then break end end
        assert(art_imports==1)
        local image,target=ui.connection_illustration,ui.connection_illustration_target
        local before=#all_objects
        for _=1,5 do ui:set_connection({connected=true});ui:set_connection({connected=false}) end
        assert(art_requests==1 and art_imports==1 and ui.connection_illustration==image and #all_objects==before)
        ui:close("user");assert(art_retires==1 and art_releases==1 and target.active==false and target.image==nil)
        assert(ui:toggle());assert(art_requests==2 and art_imports==1)
        assert(ui.connection_illustration~=image and ui.connection_illustration.texture==bank.texture)
        assert(ui.connection_illustration.visible==4)
        ''')

    def test_pending_target_is_retired_on_map_cleanup_and_cannot_bind_old_image(self):
        self.lua.execute('''
        local bank=illustration_service();art_pending=true;ui:set_connection({connected=false});assert(ui:toggle())
        local image,target=ui.connection_illustration,ui.connection_illustration_target
        ui:close("Game load started");assert(target.active==false and target.image==nil)
        art_pending=false;bank:pump(work_budget(8))
        assert(image.texture==nil and art_imports==0 and not ui.open)
        ''')

    def test_ship_radius_caption_uses_current_gate_then_fixed_preferences_and_never_old_hull_or_preset_range(self):
        self.lua.execute('''
        open_player();render_model(player_model({radiusCm=10000}));assert(ui.model.radiusCm==10000)
        assert(ui.stock_radius_label.text=="Ship scan radius: 35 m")
        ui:set_connection({connected=true,radiusCm=4000,distanceCm=100})
        assert(ui.stock_radius_label.text=="Ship scan radius: 40 m")
        ui:set_connection({connected=true,radiusCm=4567,distanceCm=100})
        assert(ui.stock_radius_label.text=="Ship scan radius: 46 m")
        local setters=0;local text=ui.stock_radius_label.SetText
        function ui.stock_radius_label:SetText(value) setters=setters+1;return text(self,value) end
        ui:set_connection({connected=true,radiusCm=4567,distanceCm=200});assert(setters==0)
        ui.model.stockRadiusCm=6789
        ui:set_preferences({shipRadiusCm=5000},ui.favorites)
        ui:set_connection({connected=true});assert(ui.stock_radius_label.text=="Ship scan radius: 50 m")
        ui.model.stockRadiusCm=nil;ui:set_connection({connected=true})
        assert(ui.stock_radius_label.text=="Ship scan radius: 50 m")
        ''')

    def test_locating_ship_shows_known_position_distance_and_fixed_range(self):
        self.lua.execute('''
        open_player()
        ui:set_connection({connected=false,code="ship_pending",reasonCode="ship_locating",
            reason="private native evidence",distanceCm=1234,distanceReference="ship_center",shipLocated=true})
        assert(ui.connection_distance.text=="Distance to ship: 12.3 m · range 35.0 m")
        assert(ui.connection_description.text=="Locating ship")
        assert(ui.connection.code=="ship_pending" and ui.connection.reasonCode=="ship_locating")
        assert(ui.connection.distanceReference=="ship_center" and ui.connection.shipLocated)
        assert(ui.connection.reason=="private native evidence")
        assert(ui.open and ui.connection_panel.visible==0 and ui.content_panel.visible==1)
        ''')

    def test_zero_distance_is_known_and_obsolete_geometry_codes_are_generic(self):
        self.lua.execute('''
        open_player();ui:set_connection({connected=false,code="ship_geometry_unavailable",distanceCm=0,
            distanceReference="ship_origin",reason="ship_scope.lua:325: degenerate bounds\\nstack traceback: private"})
        assert(ui.connection_distance.text=="Distance to ship: 0.0 m · range 35.0 m")
        assert(ui.connection_description.text=="Ship connection unavailable")
        assert(not ui.connection_description.text:find("Move closer",1,true))
        assert(not ui.connection_description.text:find("ship_scope",1,true) and not ui.connection_distance.text:find("private",1,true))
        ''')

    def test_known_distance_without_native_radius_uses_fixed_setting_and_unknown_distance_is_explicit(self):
        self.lua.execute('''
        open_player();ui:set_connection({connected=false,distanceCm=12,distanceReference="ship_center"})
        assert(ui.connection_distance.text=="Distance to ship: 0.1 m · range 35.0 m")
        assert(ui.connection_description.text=="Ship connection unavailable")
        ui:set_connection({connected=false,code="ship_unavailable"})
        assert(ui.connection_distance.text=="Distance to ship: unavailable · range 35.0 m" and ui.connection_description.text=="Ship not located")
        for _,distance in ipairs({-1,0/0,math.huge,-math.huge}) do
            ui:set_connection({connected=false,code="ship_player_unavailable",distanceCm=distance})
            assert(ui.connection_distance.text=="Distance to ship: unavailable · range 35.0 m")
            assert(ui.connection_description.text=="Player position unavailable")
        end
        ''')

    def test_only_proven_outside_fixed_ship_range_suggests_movement(self):
        self.lua.execute('''
        open_player();ui:set_connection({connected=false,code="signal_lost",distanceCm=3512,radiusCm=3500,distanceReference="ship_center"})
        assert(ui.connection_distance.text=="Distance to ship: 35.1 m · range 35.0 m")
        assert(ui.connection_description.text=="Move closer to the ship to connect.")
        ui:set_connection({connected=false,code="signal_lost",distanceCm=300,radiusCm=3500,distanceReference="ship_center"})
        assert(ui.connection_distance.text=="Distance to ship: 3.0 m · range 35.0 m")
        assert(ui.connection_description.text=="Ship connection unavailable")
        ''')

    def test_ship_location_status_change_updates_friendly_subtitle_without_reloading_art(self):
        self.lua.execute('''
        illustration_service();open_player()
        ui:set_connection({connected=false,code="ship_pending",distanceCm=100,distanceReference="ship_center"})
        local changes=0;local original=ui.connection_description.SetText
        function ui.connection_description:SetText(text) changes=changes+1;return original(self,text) end
        for _=1,10 do ui:set_connection({connected=false,code="ship_pending",distanceCm=100,distanceReference="ship_center"}) end
        assert(changes==0 and art_requests==1)
        ui:set_connection({connected=false,code="ship_unavailable"})
        assert(changes==1 and ui.connection_description.text=="Ship not located" and art_requests==1)
        ui:set_connection({connected=false,code="ship_ambiguous"})
        assert(ui.connection_description.text=="Several ships found" and ui.connection_distance.text=="Distance to ship: unavailable · range 35.0 m")
        ''')

    def test_ship_radius_settings_buttons_default_to_35_and_publish_only_ship_preference(self):
        self.lua.execute('''
        open_player();page("settings");local before=preserve_grid()
        assert(ui.preferences.shipRadiusCm==3500 and ui.ship_radius_controls[3500].ship_radius_selected)
        local originals={};local colors=0
        for cm,record in pairs(ui.ship_radius_controls) do
            originals[cm]=record.widget
            function record.widget:SetBackgroundColor(color) self.color=color;colors=colors+1 end
        end
        ui.ship_radius_controls[5000].action()
        assert(ui.preferences.shipRadiusCm==5000 and ui.ship_radius_controls[5000].ship_radius_selected)
        assert(not ui.ship_radius_controls[3500].ship_radius_selected and colors==2)
        assert(ui.ship_radius_controls[5000].widget.color.G==0.25 and ui.ship_radius_controls[3500].widget.color.G==0.105)
        local change=setting_changes[#setting_changes];assert(change.shipRadiusCm==5000 and change.radiusCm==nil)
        local writes=#setting_changes;ui.ship_radius_controls[5000].action();assert(#setting_changes==writes)
        assert(ui.preferences.radiusCm==nil and ui.stock_radius_label.text=="Ship scan radius: 50 m")
        for cm,record in pairs(ui.ship_radius_controls) do assert(record.widget==originals[cm]) end
        same_grid(before)
        ''')

    def test_ship_radius_hard_bounds_reject_invalid_values_before_callback(self):
        self.lua.execute('''
        open_player();local writes=0
        ui.callbacks.change_settings=function() writes=writes+1;return true end
        for _,radius in ipairs({3499,5001,10000,0,-1,3500.5,math.huge,0/0,false,"4000"}) do
            assert(not ui:_change_settings({shipRadiusCm=radius}))
        end
        assert(writes==0 and ui.preferences.shipRadiusCm==3500)
        assert(ui:_change_settings({shipRadiusCm=3500}) and ui:_change_settings({shipRadiusCm=5000}))
        assert(writes==2 and ui.preferences.shipRadiusCm==5000)
        ''')

    def test_optimistic_ship_radius_selection_and_failure_rollback_patch_existing_controls(self):
        self.lua.execute('''
        open_player();page("settings");local before=preserve_grid()
        ui.ship_radius_controls[4500].action();assert(ui.preferences.shipRadiusCm==4500)
        assert(ui.ship_radius_controls[4500].ship_radius_selected)
        -- The durable coordinator republishes the authoritative value when a
        -- queued write fails; the settings controls must not be rebuilt.
        ui:set_preferences({shipRadiusCm=3500,lastPage="settings"},ui.favorites)
        assert(ui.preferences.shipRadiusCm==3500 and ui.ship_radius_controls[3500].ship_radius_selected)
        assert(not ui.ship_radius_controls[4500].ship_radius_selected);same_grid(before)
        ui.callbacks.change_settings=function() return false,"Storage unavailable" end
        ui.ship_radius_controls[4000].action()
        assert(ui.preferences.shipRadiusCm==3500 and ui.ship_radius_controls[3500].ship_radius_selected);same_grid(before)
        ''')

    def test_fixed_policy_is_visible_while_ship_is_locating_and_native_gate_remains_authoritative(self):
        self.lua.execute('''
        illustration_service();open_player();ui:set_connection({connected=false,code="ship_pending"})
        assert(ui.connection_distance.text=="Distance to ship: unavailable · range 35.0 m")
        ui:set_preferences({shipRadiusCm=4000},ui.favorites)
        assert(ui.connection_distance.text=="Distance to ship: unavailable · range 40.0 m" and art_requests==1)
        ui:set_connection({connected=true,radiusCm=4000,distanceCm=0,distanceReference="ship_center"})
        ui:set_preferences({shipRadiusCm=5000},ui.favorites)
        assert(ui.stock_radius_label.text=="Ship scan radius: 40 m")
        ui:set_connection({connected=true,radiusCm=5000,distanceCm=0,distanceReference="ship_center"})
        assert(ui.stock_radius_label.text=="Ship scan radius: 50 m" and art_requests==1)
        ui:set_connection({connected=false,radiusCm=5000,distanceCm=0,distanceReference="ship_center"})
        assert(ui.connection_distance.text=="Distance to ship: 0.0 m · range 50.0 m" and art_requests==1)
        ''')


if __name__ == "__main__":
    unittest.main()
