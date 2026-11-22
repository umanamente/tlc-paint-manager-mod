"""Ship signal and foreground admission through the actual main.lua pump."""
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS
from test_learning_main import LEARNING_FIXTURE
from test_bulk_main import BULK_MAIN_FIXTURE
from test_main_escape import ESCAPE_FIXTURE


SHIP_MAIN = r'''
state_loads,state_load_steps,state_load_cancelled,preference_changes,favorite_changes=0,0,0,0,0
local original_store=package.loaded["paintmanager.save_store"].new
package.loaded["paintmanager.save_store"].new=function(...)
    local repository=original_store(...)
    local original_load=repository.begin_state_load
    repository.begin_state_load=function()
        state_loads=state_loads+1
        local job=original_load();local step,cancel=job.step,job.cancel
        function job:step(budget)
            state_load_steps=state_load_steps+1
            if hold_state_load then budget:consume();return {state="running",phase="state_read"} end
            return step(self,budget)
        end
        function job:cancel(reason)
            state_load_cancelled=state_load_cancelled+1
            return cancel(self,reason)
        end
        return job
    end
    return repository
end
local original_ui=package.loaded["paintmanager.ui"].new
package.loaded["paintmanager.ui"].new=function(c,callbacks)
    local result=original_ui(c,callbacks)
    function result:set_connection(value)
        assert(inGameThread)
        self.connection={connected=value.connected,reason=value.reason,distanceCm=value.distanceCm,radiusCm=value.radiusCm}
        self.connection_updates=(self.connection_updates or 0)+1
    end
    function result:update_stock(value)
        assert(inGameThread)
        self.stock_updates=(self.stock_updates or 0)+1;self.stock_preview=value
        return true
    end
    return result
end
function cycles(count) for _=1,count or 20 do pump_once() end end
function open_ship_window()
    start();registered.F10();cycles(20)
    assert(ui and ui.open)
end
function instrument_preferences()
    function fixture_preferences:change_settings(changes) preference_changes=preference_changes+1;return true end
    function fixture_preferences:favorite(...) favorite_changes=favorite_changes+1;return true end
end

-- A pre-admitted read-only service models lifecycle/scan work independently of
-- foreground collections. Its cursor survives window promotion and close.
function enable_stock_service()
    stock_steps,stock_foreground,stock_refreshes,stock_completions,stock_instances=0,0,0,0,0
    stock_total=1000
    package.loaded["paintmanager.stock_cache"]={new=function(c,build)
        stock_instances=stock_instances+1
        stock={revision=0,work=0,bound={contextToken=ctx}}
        function stock:binding() return self.bound end
        function stock:capture() return self.finished,self.revision end
        function stock:preview() return nil,0 end
        function stock:take_paint_seeds() end
        function stock:invalidate() self.bound,self.finished=nil,nil end
        function stock:admit(event) self.bound=event;return true end
        function stock:cancel_refresh() self.work=0 end
        function stock:refresh()
            stock_refreshes=stock_refreshes+1
            if self.finished then self.finished,self.work=nil,0 end
            return self.revision+1
        end
        function stock:get_progress()
            return {state=self.finished and "ready" or "scanning",presentation={kind="stock",phase="inventory_capture",
                mode="determinate",completed=self.work,total=stock_total,unit="objects"}}
        end
        function stock:step(budget,foreground)
            assert(inGameThread)
            if self.finished then return end
            while not budget:expired() and not self.finished do
                budget:consume();self.work=self.work+1;stock_steps=stock_steps+1
                if foreground then stock_foreground=stock_foreground+1 end
                if self.work>=stock_total then
                    stock_completions=stock_completions+1;self.revision=self.revision+1
                    self.finished={contextToken=ctx,status="captured",stockComplete=true,complete=true,
                        radiusCm=c.ship_radius_cm,stockRadiusCm=c.ship_radius_cm,physicalCount=4,sourceCount=1,includedSourceCount=1,
                        stockScope={token="ship-fixture",radiusCm=c.ship_radius_cm},sources=json.array(),
                        rows=json.array({{id="paint1",count=4,recipeState="locked",complete=true,
                            runtimeRef={name="paint1",id="paint1@123",className="VoyagePaintItem"}}})}
                end
            end
        end
        return stock
    end}
end
'''


class ShipMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + LEARNING_FIXTURE + BULK_MAIN_FIXTURE + ESCAPE_FIXTURE + SHIP_MAIN)

    def test_new_stock_base_survives_older_load_pre_on_character_channel(self):
        self.lua.execute('''
        enable_stock_service();ctx=nil
        local pre={invalidated=true,loadGeneration=1}
        local ready={baseScopeId="restored-at-helm",accountId="76561198000000001",loadGeneration=1,
            libraryPath="C:/Save/76561198000000001/PaintManager.library.json"}
        package.loaded["paintmanager.save_lifecycle"]={
            install=function()return true end,
            consume=function()local event=pre;pre=nil;return event end,
            consume_stock=function()local event=ready;ready=nil;return event end}
        start();cycles(1)
        assert(stock:binding() and stock:binding().baseScopeId=="restored-at-helm")
        assert(ui==nil)
        ''')

    def test_loaded_ship_radius_changes_live_policy_and_retires_previous_stock(self):
        self.lua.execute('''
        open_ship_window()
        assert(config.ship_radius_cm==3500 and ui.model.stockComplete)
        inGameThread=true
        local document=require("paintmanager.state_document").new()
        document.sections.settings.data.shipRadiusCm=5000
        assert(fixture_preferences:load(document))
        inGameThread=false
        assert(config.ship_radius_cm==5000 and ui.preferences.shipRadiusCm==5000)
        assert(ui.model==nil and scans==1)
        inGameThread=true
        document.sections.settings.data.shipRadiusCm=3500
        assert(fixture_preferences:load(document))
        inGameThread=false
        assert(config.ship_radius_cm==3500)
        ''')

    def test_cold_open_far_retains_signal_lost_window_without_foreground_scan(self):
        self.lua.execute('''
        fixtureConnected=false;open_ship_window();cycles(80)
        assert(ui.open and ui.connection.connected==false)
        assert(scans==0 and jobs==0 and state_loads==0 and learn_commits==0 and bulk_steps==0)
        assert(snapshots==0 and discoveries==0 and dumps==0 and #closed==0)
        assert(not invoke("enqueue","refresh"));assert(not invoke("learn_recipe","paint1"))
        assert(ui.open)
        ''')

    def test_reconnect_waits_for_another_opening(self):
        self.lua.execute('''
        fixtureConnected=false;open_ship_window()
        fixtureConnected=true;cycles(100)
        assert(ui.open and ui.connection.connected and state_loads==0 and scans==0)
        reopen_window();cycles(25)
        assert(state_loads==1 and scans==1 and ui.model.stockComplete)
        fixtureConnected=false;cycles(5)
        fixtureConnected=true;cycles(100);assert(scans==1 and state_loads==1)
        ''')

    def test_disconnect_cancels_queued_mutation_before_job_creation(self):
        self.lua.execute('''
        open_ship_window();assert(invoke("learn_all"));assert(bulk_jobs==0 and bulk_steps==0)
        fixtureConnected=false;cycles(20)
        assert(ui.open and not ui.connection.connected)
        assert(bulk_jobs==0 and bulk_steps==0 and learn_commits==0)
        assert(not invoke("learn_recipe","paint1") and not invoke("learn_all"))
        ''')

    def test_disconnect_cancels_created_batch_before_first_native_unit(self):
        self.lua.execute('''
        open_ship_window();start_bulk()
        assert(bulk_jobs==1 and bulk_steps==0)
        fixtureConnected=false;cycles(20)
        assert(ui.open and bulk_cancelled==1 and bulk_steps==0 and learn_commits==0)
        ''')

    def test_direct_callbacks_recheck_signal_before_preference_or_radius_changes(self):
        self.lua.execute('''
        open_ship_window();instrument_preferences()
        assert(invoke("change_settings",{sort="name_desc"}) and preference_changes==1)
        assert(invoke("favorite","Item","DA_Paint_A",true) and favorite_changes==1)
        local radius=config.ship_radius_cm;local cancelled=cancellations
        -- No pump updates the cached connection first: each direct callback
        -- must sample fresh ship access itself.
        fixtureConnected=false
        assert(not invoke("change_settings",{sort="count_desc"}))
        assert(not invoke("favorite","Item","DA_Paint_A",false))
        assert(not invoke("change_settings",{shipRadiusCm=5000}))
        assert(preference_changes==1 and favorite_changes==1 and config.ship_radius_cm==radius and cancellations==cancelled)
        assert(ui.open and not ui.connection.connected)
        ''')

    def test_disconnect_during_state_load_requires_another_opening(self):
        self.lua.execute('''
        hold_state_load=true;open_ship_window()
        assert(state_loads==1 and scans==0)
        fixtureConnected=false;cycles(10)
        assert(ui.open and state_load_cancelled==1 and scans==0)
        hold_state_load=false;fixtureConnected=true;cycles(30)
        assert(ui.open and state_loads==1 and scans==0)
        reopen_window();cycles(50);assert(state_loads==2 and scans==1)
        ''')

    def test_stock_wait_only_advances_the_one_open_request(self):
        self.lua.execute('''
        enable_stock_service();start();cycles(4)
        local prior=stock.work;assert(prior==0 and stock_foreground==0 and scans==0)
        registered.F10();cycles(10)
        assert(ui.open and stock_foreground>0 and stock.work>prior and stock_instances==1)
        assert(stock_refreshes==1 and stock_completions==0 and scans==0 and state_loads==1)
        stock_total=stock.work+3;cycles(25)
        assert(stock_completions==1 and ui.model==stock.finished and ui.model.stockComplete and scans==0)
        cycles(60);assert(stock_refreshes==1 and stock_completions==1 and state_loads==1)
        ''')

    def test_reopen_shows_recent_same_ship_counts_only_as_display(self):
        self.lua.execute('''
        local now=1000
        os.time=function() return now end
        open_ship_window()
        local old=ui.model
        registered.F10();cycles(5)
        now=1059;jobSlices=100
        registered.F10();cycles(12)
        assert(ui.open and ui.model==old and ui.model.rows[1].count==4)
        assert(not invoke("learn_recipe","paint1") and learn_jobs==0)
        registered.F10();cycles(5)
        now=1061;registered.F10();cycles(12);assert(ui.open and ui.model==nil)
        jobSlices=2;cycles(12);assert(ui.model and ui.model~=old)
        registered.F10();cycles(5);fixtureShipToken="other-ship"
        jobSlices=100;registered.F10();cycles(10);assert(ui.open and ui.model==nil)
        ''')

    def test_switching_to_learned_during_a_warm_box_read_shows_new_vault_rows(self):
        self.lua.execute('''
        open_ship_window()
        local old=ui.model
        registered.F10();cycles(4);assert(not ui.open)
        local early={contextToken=ctx,learnedOnly=true,stockComplete=false,
            rows={{id="paint-new",recipeState="unlocked"}}}
        local switched,painted=0,0
        function ui:show_learned(snapshot,retained)
            assert(self.active_tab=="learned" and snapshot==early and retained==old)
            assert(self.model==old and old.rows[1].count==4)
            switched=switched+1;self.early_pending=true
            return true
        end
        function ui:is_rendering() return self.early_pending end
        function ui:render_tick(budget)
            if self.early_pending and not budget:expired() then
                budget:consume();painted=painted+1
                if painted>=3 then self.early_pending=false end
            end
        end
        local original=package.loaded["paintmanager.collection"].begin_catalog
        package.loaded["paintmanager.collection"].begin_catalog=function(...)
            local job=original(...)
            function job:get_learned_capture() return early end
            return job
        end
        local previous=package.loaded["paintmanager.collection"].begin
        local box_steps=0
        package.loaded["paintmanager.collection"].begin=function(...)
            local job=previous(...);local step=job.step
            function job:step(budget) box_steps=box_steps+1;return step(self,budget) end
            return job
        end
        ui.active_tab="ready";jobSlices=100;registered.F10();cycles(15)
        assert(ui.open and ui.model==old and box_steps>0 and switched==0)
        local before=box_steps;ui.active_tab="learned";cycles(3)
        assert(switched==1 and painted==3 and box_steps==before and ui.model==old)
        assert(not invoke("learn_recipe","paint1"))
        jobSlices=2;cycles(12)
        assert(switched==1 and ui.model~=old and ui.model.stockComplete)
        ''')

    def test_one_unit_cold_learned_page_precedes_storage_catalog_and_stock(self):
        self.lua.execute('''
        local constructor=package.loaded["paintmanager.ui"].new
        local built,first_phase,full_catalog=0,0,0
        package.loaded["paintmanager.ui"].new=function(...)
            local view=constructor(...)
            view.active_tab="learned"
            function view:show_learned(learned)
                assert(state_loads==0 and scans==0 and full_catalog==0)
                self.model=learned;self.to_draw=3;return true
            end
            function view:is_rendering() return self.to_draw and self.to_draw>0 end
            function view:render_tick(budget)
                if self:is_rendering() and not budget:expired() then
                    budget:consume();self.to_draw=self.to_draw-1;built=built+1
                end
            end
            return view
        end
        local early={contextToken=ctx,learnedOnly=true,stockComplete=false,rows={{recipeState="unlocked"}}}
        package.loaded["paintmanager.collection"].begin_catalog=function()
            local job={}
            function job:step(budget)
                budget:consume()
                if not self.prefix then
                    self.prefix=early;first_phase=first_phase+1
                    return {state="running",phase="catalog_discovery",defer=true}
                end
                assert(built==3 and state_loads==1)
                full_catalog=full_catalog+1
                return {state="done"}
            end
            function job:get_learned_capture() return self.prefix end
            function job:get_catalog() return {contextToken=ctx,rows={},catalogComplete=true,recipeCatalogComplete=true} end
            function job:cancel() end
            return job
        end
        config.work_units_per_tick=1
        start();registered.F10();cycles(80)
        assert(first_phase==1 and full_catalog==1 and built==3 and scans==1 and ui.model.stockComplete)
        ''')

    def test_entering_learned_during_storage_reads_vault_now_and_resumes_the_same_storage(self):
        self.lua.execute('''
        local constructor=package.loaded["paintmanager.ui"].new
        local early={contextToken=ctx,learnedOnly=true,rows={{recipeState="unlocked"}}}
        local prefix,full,drawn=0,0,0
        package.loaded["paintmanager.ui"].new=function(...)
            local view=constructor(...);view.active_tab="ready"
            function view:show_learned(value)
                assert(value==early and self.active_tab=="learned");self.model=value;self.left=2;return true
            end
            function view:is_rendering() return self.active_tab=="learned" and (self.left or 0)>0 end
            function view:render_tick(budget)
                if self:is_rendering() and not budget:expired() then
                    budget:consume();self.left=self.left-1;drawn=drawn+1
                end
            end
            return view
        end
        package.loaded["paintmanager.collection"].begin_catalog=function()
            local job={}
            function job:step(budget)
                budget:consume()
                if not self.learned then self.learned=early;prefix=prefix+1;return {state="running",phase="recipe_catalog"} end
                assert(not hold_state_load and drawn==2);full=full+1;return {state="done"}
            end
            function job:get_learned_capture() return self.learned end
            function job:get_catalog() return {contextToken=ctx,rows={},catalogComplete=true} end
            function job:cancel() end
            return job
        end
        hold_state_load=true;config.work_units_per_tick=1;open_ship_window()
        assert(state_loads==1 and state_load_steps>0 and prefix==0 and scans==0)
        local previous=state_load_steps
        ui.active_tab="learned";cycles(1)
        assert(prefix==1 and state_load_steps==previous and full==0)
        for i=1,30 do if drawn==2 then break end;cycles(1);assert(state_load_steps==previous) end
        assert(ui.model==early and drawn==2 and state_load_cancelled==0)
        hold_state_load=false;cycles(60)
        assert(state_loads==1 and prefix==1 and full==1 and ui.model.stockComplete and scans==1)
        ''')

    def test_closing_during_cold_vault_read_cancels_parked_storage_and_catalog(self):
        self.lua.execute('''
        local catalog_steps,catalog_cancels=0,0
        package.loaded["paintmanager.collection"].begin_catalog=function()
            return {step=function(_,budget)
                budget:consume();catalog_steps=catalog_steps+1;return {state="running",phase="vault"}
            end,get_learned_capture=function() end,
                cancel=function() catalog_cancels=catalog_cancels+1 end}
        end
        hold_state_load=true;open_ship_window()
        assert(state_loads==1 and state_load_steps>0 and catalog_steps==0)
        ui.active_tab="learned";cycles(1);assert(catalog_steps>0)
        registered.F10();cycles(20)
        assert(not ui.open and state_load_cancelled==1 and catalog_cancels==1 and scans==0)
        local prior=catalog_steps;cycles(20)
        assert(catalog_steps==prior and state_loads==1 and state_load_cancelled==1)
        ''')

    def test_signal_lost_close_hotkey_and_escape_remain_available(self):
        self.lua.execute('''
        fixtureConnected=false;open_ship_window()
        registered.F10();cycles(5);assert(not ui.open and scans==0)
        registered.F10();cycles(5);assert(ui.open and not ui.connection.connected)
        press_escape();assert(ui.open and ui:is_closing())
        cycles(3);assert(not ui.open and scans==0)
        local closes=#closed;press_escape();cycles(10);assert(#closed==closes)
        ''')


if __name__ == "__main__":
    unittest.main()
