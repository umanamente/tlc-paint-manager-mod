"""Controller/workflow wiring with bounded fake native reads and persistent storage."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS


PRESET_MAIN_FIXTURE = r'''
debug.getinfo=function() return {source="@D:/Steam/steamapps/common/Voyage/Voyage/Binaries/Win64/ue4ss/Mods/PaintManager/Scripts/main.lua"} end
local original_open=io.open
io.open=function(path,mode)
    if mode=="r" then
        return {read=function() return '"buildid" "'..(testBuild or "25982825")..'"' end,close=function() return true end}
    end
    return original_open(path,mode)
end
preset_loads,preset_stores,preset_captures,preset_apply_jobs,preset_attempts,preset_cancels=0,0,0,0,0,0
preset_refresh_requests=0
preset_views,preset_requests,preset_paths={},{},{}
fixtureShipOwner="primary-ship"
local ship_scope=package.loaded["paintmanager.ship_scope"]
local original_access=ship_scope.player_access
function ship_scope.player_access(c)
    local scope,reason,detail=original_access(c)
    if scope then scope.runtimeRef={id=fixtureShipOwner} end
    return scope,reason,detail
end
local compatibility={kind="boat",className="/Game/Boat.Test_C",paintSetName="Barge"}
local saved_paint={assetType="Item",assetName="DA_Paint_LifeGuard"}
local function deck(paint)
    return json.array({{materialSlotName="M_Deck",partName="Deck",paint=paint}})
end
preset_library={schemaVersion=1,presets=json.array({{id="saved1",name="Saved before restart",
    target=compatibility,zones=deck(saved_paint)}})}
function current_preset_view()
    return {contextToken=ctx,targets=json.array({
        {id="target1",kind="boat",label="Barge",complete=not incompleteTarget,compatibility=compatibility,
         ownerRuntimeRef={id=fixtureTargetOwner or "primary-ship"},zones=json.array(),
         logicalZones=deck(fixtureMatchesSaved and saved_paint or json.null)}})}
end
local function bounded_job(result,error_message,slices,native_read)
    local job={steps=0}
    function job:get_result() return result end
    function job:cancel(reason) self.cancelled=reason;preset_cancels=preset_cancels+1 end
    function job:step(budget)
        if native_read then assert(inGameThread) end
        if self.cancelled then return {state="cancelled",error=self.cancelled} end
        if budget and budget:expired() then return {state="running",phase="bounded_fixture"} end
        if budget then budget:consume() end
        self.steps=self.steps+1
        if self.steps<(slices or 1) then return {state="running",phase="bounded_fixture"} end
        if error_message then return {state="error",error=error_message} end
        return {state="done",view=result}
    end
    return job
end
package.loaded["paintmanager.preset_store"]={
    begin_load=function(path)
        assert(inGameThread);preset_loads=preset_loads+1;preset_paths[#preset_paths+1]=path
        assert(path=="D:/Steam/steamapps/common/Voyage/Voyage/Binaries/Win64/ue4ss/Mods/PaintManager.presets.json")
        return bounded_job(preset_library,loadError and "Invalid preset library" or nil,loadSlices)
    end,
    begin_save=function(path,library)
        assert(inGameThread);preset_stores=preset_stores+1
        local job=bounded_job(library,storeError and "Preset disk error" or nil,storeSlices)
        local original_step=job.step
        function job:step(budget)
            local result=original_step(self,budget)
            if result.state=="done" then preset_library=library end
            return result
        end
        return job
    end,
}
-- These controller tests retain a bounded storage fixture. Native-save storage
-- and its real document jobs are covered separately by test_save_main/store.
package.loaded["paintmanager.save_store"]={new=function()
    local storage=package.loaded["paintmanager.preset_store"]
    storage.new_id=function() return "12345678-1234-4234-8234-123456789abc" end
    storage.begin_state_load=function()
        local job=bounded_job({sections={settings={data={}}}})
        local original=job.step
        function job:step(budget) local p=original(self,budget);p.snapshot={scopeId=ctx};return p end
        return job
    end
    storage.begin_ui_settings=function(changes)
        local data=require("paintmanager.state_document").new()
        for name,value in pairs(changes) do data.sections.settings.data[name]=value end
        return bounded_job(data)
    end
    return storage
end}
local actual_presets=require("paintmanager.presets")
package.loaded["paintmanager.presets"]={
    compare_layout=actual_presets.compare_layout,
    from_target=actual_presets.from_target,
    begin_capture=function()
        assert(inGameThread);preset_captures=preset_captures+1
        return bounded_job(current_preset_view(),captureError and "Current paint read failed" or nil,captureSlices,true)
    end,
    begin_apply=function(c,request)
        assert(inGameThread);preset_apply_jobs=preset_apply_jobs+1;preset_requests[#preset_requests+1]=request
        assert(request.preset.id=="saved1" and request.target.id=="target1")
        local job={steps=0,receipt={attempted=false,outcome="pending"}}
        function job:get_receipt() return self.receipt end
        function job:cancel(reason)
            self.cancelled=reason;preset_cancels=preset_cancels+1
            if not self.receipt.attempted then self.receipt.outcome="cancelled" end
        end
        function job:step(budget)
            assert(inGameThread)
            if self.cancelled then return {state="cancelled",error=self.cancelled} end
            if budget:expired() then return {state="running",phase="preset_preflight"} end
            apply_budget=budget
            budget:consume();self.steps=self.steps+1
            if self.steps<(applySlices or 2) then return {state="running",phase="preset_preflight"} end
            if applyError then return {state="error",error="Native apply fixture error"} end
            local connected,reason=request.connection_guard()
            if not connected then return {state="error",error=reason} end
            self.receipt.attempted=true;self.receipt.outcome=applyOutcome or "applied_verified"
            preset_attempts=preset_attempts+1
            return {state="done",receipt=self.receipt}
        end
        return job
    end,
}
local original_ui=package.loaded["paintmanager.ui"].new
package.loaded["paintmanager.ui"].new=function(c,callbacks)
    local result=original_ui(c,callbacks)
    function result:set_learning(active) assert(inGameThread);self.learning=active end
    function result:set_presets(view)
        assert(inGameThread);self.presets=view;preset_views[#preset_views+1]=view or false
        if not view then self.presetRefreshRequested=false end
    end
    function result:set_connection(connection)
        self.connection=connection
        if not connection.connected then self.presetRefreshRequested=false end
    end
    function result:needs_preset_refresh()
        return self.open and self.active_tab=="presets" and not self.presets and not self.presetRefreshRequested
    end
    function result:mark_preset_refresh_requested()
        self.presetRefreshRequested=true
        preset_refresh_requests=preset_refresh_requests+1
    end
    return result
end
function pump_once()
    inGameThread=true;local ok,err=pcall(pump);inGameThread=false;assert(ok,err)
end
function until_value(callback)
    for _=1,1000 do if callback() then return end;pump_once() end
    error("Fixture condition was not reached")
end
function refresh_presets()
    assert(invoke("preset_refresh"));step();step()
    assert(ui.presets and ui.presets.library and not ui.learning)
end

function track_open_cursor()
    catalog_starts,catalog_steps,stock_starts,stock_steps=0,0,0,0
    local captured={contextToken=ctx,rows={},complete=true,stockComplete=true,radiusCm=config.ship_radius_cm,
        stockScope={token="ship-fixture"}}
    package.loaded["paintmanager.collection"].begin_catalog=function()
        catalog_starts=catalog_starts+1
        return {step=function(_,budget)
            assert(inGameThread);budget:consume();catalog_steps=catalog_steps+1
            return {state=catalog_steps<4 and "running" or "done",phase="catalog_metadata"}
        end,get_catalog=function() return {contextToken=ctx,rows={}} end,get_learned_capture=function() end,
            cancel=function() cancellations=cancellations+1 end}
    end
    local module=require("paintmanager.stock_cache")
    local create=module.new
    module.new=function(...)
        local cache=create(...)
        function cache:binding() return {} end
        function cache:refresh() stock_starts=stock_starts+1;return 1 end
        function cache:step(budget)
            assert(inGameThread);budget:consume();stock_steps=stock_steps+1
        end
        function cache:get_progress()
            return {state="running",phase="container_read",presentation={kind="stock",phase="container_read",
                mode="determinate",unit="containers",completed=stock_steps,total=4}}
        end
        function cache:capture() if stock_steps>=4 then return captured,1 end end
        return cache
    end
end
'''


class PresetMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PRESET_MAIN_FIXTURE)

    def run_lua(self, source):
        return self.lua.execute(source)

    def open_window(self):
        self.run_lua('start();registered.F10();step()')

    def reports(self):
        rows = json.loads(self.run_lua('return json.encode(json.array(written))'))
        return [json.loads(row['text']) for row in rows]

    def test_presets_are_lazy_at_startup_window_open_and_idle(self):
        self.run_lua('start();registered.F10();assert(preset_loads==0 and preset_captures==0 and #written==0);step()')
        self.run_lua('for _=1,30 do step() end')
        self.assertEqual(self.lua.eval('preset_loads+preset_captures+preset_stores+preset_attempts'), 0)

    def test_refresh_queues_before_io_then_loads_library_and_current_targets(self):
        self.open_window()
        self.run_lua('''
        assert(invoke("preset_refresh"));assert(preset_loads==0 and preset_captures==0)
        assert(not invoke("preset_refresh"));step();step()
        assert(preset_loads==1 and preset_captures==1 and preset_stores==0 and preset_attempts==0)
        assert(ui.presets.targets[1].id=="target1" and ui.presets.library.presets[1].id=="saved1")
        assert(ui.status.state=="done" and not ui.learning)
        ''')

    def test_first_presets_view_starts_immediately_and_signal_loss_retires_it(self):
        self.open_window()
        self.run_lua('''
        assert(ui.connection.shipOwnerId=="primary-ship")
        ui.active_tab="presets";loadSlices=3
        pump_once()
        assert(preset_refresh_requests==1 and preset_loads==1 and preset_captures==0)
        fixtureConnected=false;step();assert(ui.connection.shipOwnerId==nil and preset_captures==0)
        fixtureConnected=true
        until_value(function() return preset_captures==1 end)
        step();step()
        assert(ui.presets and preset_loads==2 and preset_captures==1 and preset_refresh_requests==2)
        ''')

    def test_presets_pause_one_catalog_and_stock_cursor_and_prioritize_over_queued_last_page(self):
        self.run_lua('''
        config.ui_progress_delay_ms=0;config.ui_progress_update_ms=0
        track_open_cursor();start();registered.F10()
        until_value(function() return catalog_steps==1 end)
        ui.active_tab="presets";loadSlices=4
        assert(invoke("change_settings",{lastPage="presets"}))
        assert(fixture_preferences:is_busy() and not fixture_preferences.active)
        pump_once()
        assert(preset_loads==1 and catalog_steps==1 and stock_starts==0 and not fixture_preferences.active)
        ui.active_tab="learned";pump_once()
        assert(catalog_steps==2 and preset_cancels==1 and not ui.presets)
        until_value(function() return stock_steps==1 end)
        assert(ui.progress.unit=="containers" and ui.progress.completed==1 and ui.progress.total==4)
        local finishing_writer=fixture_preferences.active
        ui.active_tab="presets";pump_once()
        if finishing_writer and preset_loads==1 then
            assert(stock_steps==1 and not fixture_preferences.active);pump_once()
        end
        assert(preset_loads==2 and stock_steps==1)
        until_value(function() return ui.presets and not ui.learning end)
        for i=1,20 do pump_once() end
        assert(ui.progress.unit~="containers","Parked paint scan continued to show on Presets")
        assert(catalog_steps==4 and stock_steps==1 and preset_loads==2 and preset_captures==1)
        ui.active_tab="ready";pump_once()
        assert(stock_steps==2 and stock_starts==1 and catalog_starts==1)
        assert(ui.progress.unit=="containers" and ui.progress.completed==2 and ui.progress.total==4)
        until_value(function() return ui.model~=nil end)
        ui.active_tab="presets";step();ui.active_tab="learned";step()
        assert(catalog_starts==1 and stock_starts==1 and stock_steps==4 and preset_loads==2)
        ''')

    def test_save_requires_fresh_target_view_and_appends_exact_name_without_game_mutation(self):
        self.open_window()
        self.run_lua('''
        assert(not invoke("preset_save","target1","Trip"));refresh_presets()
        assert(invoke("preset_save","target1","Trip {one}"));assert(preset_stores==0)
        assert(not invoke("preset_save","target1","Duplicate pending"));step();step()
        assert(preset_stores==1 and #preset_library.presets==2 and preset_library.presets[2].name=="Trip {one}")
        assert(preset_library.presets[2].id=="12345678-1234-4234-8234-123456789abc")
        assert(preset_captures==2 and preset_attempts==0 and ui.status.state=="done" and not ui.learning)
        ''')

    def test_fresh_matching_layout_reports_existing_preset_without_saving(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();assert(invoke("preset_save","target1","Duplicate layout"))
        fixtureMatchesSaved=true;step();step()
        assert(preset_captures==2 and preset_stores==0 and #preset_library.presets==1)
        assert(ui.status.state=="done" and ui.status.message:find("already matches",1,true))
        ''')

    def test_ship_mismatch_blocks_stale_targets_at_request_start_and_commit(self):
        self.open_window()
        self.run_lua('''
        fixtureTargetOwner="another-ship";refresh_presets()
        assert(not invoke("preset_save","target1","Other") and not invoke("preset_apply","target1","saved1"))
        fixtureTargetOwner=nil;refresh_presets();assert(invoke("preset_save","target1","Old owner"))
        pump_once();fixtureShipOwner="new-ship";step()
        assert(preset_stores==0 and ui.status.state=="error")
        fixtureShipOwner="primary-ship";step();applySlices=100
        assert(invoke("preset_apply","target1","saved1"))
        until_value(function() return preset_apply_jobs==1 end)
        fixtureShipOwner="new-ship";applySlices=1;step();step()
        assert(preset_attempts==0 and ui.status.state=="error")
        ''')

    def test_invalid_names_and_incomplete_or_missing_targets_never_start_save(self):
        self.open_window()
        self.run_lua('''
        refresh_presets()
        for _,name in ipairs({"", "   ", "Bad\\nName", string.rep("x",129)}) do
            assert(not invoke("preset_save","target1",name))
        end
        assert(not invoke("preset_save","missing","Trip"))
        ui.presets.targets[1].complete=false
        assert(not invoke("preset_save","target1","Trip"))
        assert(preset_stores==0)
        ''')

    def test_apply_uses_reloaded_preset_and_refreshes_stock_when_leaving_presets(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();ui.active_tab="presets";preset_library.presets[1].name="Reloaded version"
        local previous,captures=ui.model,scans
        local policy=require("paintmanager.processing")
        assert(policy.request({open_work_budget_ms=14,unit_limit_enabled=false,time_limit_enabled=false}))
        assert(policy.commit(config))
        assert(invoke("preset_apply","target1","saved1"));assert(preset_apply_jobs==0)
        assert(not invoke("preset_apply","target1","saved1"));step();step()
        assert(preset_apply_jobs==1 and preset_attempts==1 and preset_loads==2)
        assert(apply_budget.target_ms==14 and apply_budget.time_limit_enabled and apply_budget.unit_limit_enabled)
        assert(preset_requests[1].preset.name=="Reloaded version")
        assert(preset_requests[1].stockModel==previous and previous.stockScope.token==fixtureShipToken)
        assert(previous and not ui.model and scans==captures and preset_captures==2)
        assert(ui.presets and not ui.learning and ui.status.state=="done")
        ui.active_tab="learned";until_value(function() return scans>captures and ui.model~=nil end)
        ''')
        report = self.reports()[-1]
        self.assertEqual(report['kind'], 'preset_apply')
        self.assertFalse(report['readOnly'])
        self.assertEqual(report['data']['outcome'], 'applied_verified')
        self.assertTrue(report['data']['attempted'])

    def test_unknown_build_rejects_apply_but_allows_read_and_own_library_save(self):
        self.run_lua('testBuild="different"')
        self.open_window()
        self.run_lua('''
        refresh_presets();assert(invoke("preset_save","target1","Trip"));step();step()
        local accepted,message=invoke("preset_apply","target1","saved1")
        assert(not accepted and message:find("build",1,true))
        assert(preset_stores==1 and preset_apply_jobs==0 and preset_attempts==0)
        ''')

    def test_close_discards_staged_preset_request_and_reopen_remains_usable(self):
        self.open_window()
        self.run_lua('''
        assert(invoke("preset_refresh"));pump_once();registered.F10();step()
        assert(not ui.open and preset_loads==0 and not ui.learning)
        registered.F10();step();refresh_presets();assert(preset_loads==1)
        ''')

    def test_toggle_overtakes_queued_save_without_leaving_a_request_on_reopen(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();assert(invoke("preset_save","target1","Cancelled"))
        registered.F10();step();assert(not ui.open and preset_stores==0)
        registered.F10();step();refresh_presets()
        assert(invoke("preset_save","target1","New"));step();step()
        assert(preset_stores==1 and #preset_library.presets==2)
        ''')

    def test_close_lost_signal_and_context_cancel_apply_preflight_without_attempt(self):
        for action in ('registered.F10()', 'fixtureConnected=false', 'ctx="worldB/pawnB"'):
            with self.subTest(action=action):
                self.setUp()
                self.open_window()
                self.run_lua('''
                refresh_presets();applySlices=100;assert(invoke("preset_apply","target1","saved1"));step()
                assert(preset_apply_jobs==1 and preset_attempts==0)
                ''' + action + ';step();step();assert(preset_attempts==0 and not ui.learning)')
                reports = self.reports()
                self.assertTrue(any(r['kind'] == 'preset_apply' and r['data']['outcome'] == 'cancelled' for r in reports))

    def test_preset_preview_and_apply_do_not_depend_on_a_radius(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();load_ship_radius(5000)
        assert(invoke("preset_apply","target1","saved1"));step();step()
        assert(preset_requests[1].radiusCm==nil and preset_attempts==1)
        ''')

    def test_attempt_receipt_finishes_after_close_without_another_native_mutation(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();assert(invoke("preset_apply","target1","saved1"))
        until_value(function() return preset_attempts==1 end)
        registered.F10();step();step()
        assert(not ui.open and not ui.learning and preset_attempts==1)
        ''')
        report = self.reports()[-1]
        self.assertFalse(report['readOnly'])
        self.assertTrue(report['data']['attempted'])
        self.assertEqual(report['data']['outcome'], 'applied_verified')

    def test_attempt_receipt_finishes_after_world_loss_and_clears_old_target_view(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();assert(invoke("preset_apply","target1","saved1"))
        until_value(function() return preset_attempts==1 end)
        ctx=nil;step();step()
        assert(not ui.open and ui.presets==nil and not ui.learning and preset_attempts==1)
        ''')
        self.assertTrue(self.reports()[-1]['data']['attempted'])

    def test_storage_load_error_releases_controls_and_can_be_retried_explicitly(self):
        self.open_window()
        self.run_lua('''
        loadError=true;assert(invoke("preset_refresh"));step();step()
        assert(ui.open and not ui.learning and ui.status.state=="error")
        assert(ui.status.message:find("Invalid preset library",1,true) and preset_captures==0)
        loadError=false;refresh_presets();assert(preset_loads==2)
        ''')

    def test_context_reset_rejects_old_target_and_pending_save_without_writing(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();assert(invoke("preset_save","target1","Trip"));ctx="worldB/pawnB";step()
        assert(not ui.open and ui.presets==nil and preset_stores==0)
        registered.F10();step();assert(not invoke("preset_save","target1","Trip"))
        refresh_presets();assert(invoke("preset_save","target1","New world"));step();step();assert(preset_stores==1)
        ''')

    def test_uncertain_apply_is_an_error_and_writer_failure_never_repeats_attempt(self):
        self.open_window()
        self.run_lua('''
        refresh_presets();ui.active_tab="presets";applyOutcome="uncertain";assert(invoke("preset_apply","target1","saved1"));step();step()
        assert(ui.status.state=="error" and not ui.learning and preset_attempts==1)
        writeError=true;assert(invoke("preset_apply","target1","saved1"));step();step();step()
        assert(preset_attempts==2 and not ui.learning)
        ''')


if __name__ == '__main__':
    unittest.main()
