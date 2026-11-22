"""Processing policies and explicit profiles, using the actual Lua modules."""
from pathlib import Path
import tempfile
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS


class ProcessingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path;p=require("paintmanager.processing")')

    def test_registry_and_defaults_are_copied_and_validate(self):
        self.lua.execute('''
        local values=p.defaults();assert(p.validate(values));values.automatic_preparation=false
        assert(p.get("automatic_preparation"));local descriptors=p.descriptors();descriptors[1].key="broken"
        assert(p.descriptors()[1].key=="batching_enabled")
        assert(p.get("work_budget_ms",17)==17 and p.get("work_budget_ms")==2)
        assert(p.work_target_ms(false)==2 and p.work_target_ms(true)==30)
        ''')

    def test_invalid_and_unknown_values_never_change_effective_policy(self):
        self.lua.execute('''
        for _,values in ipairs({{job_admission_target=17},{work_budget_ms=0},{open_work_budget_ms=0},
            {work_units_per_tick=1.1},{batching_enabled=1},{unknown=4},{pure_primitives=math.huge},
            {metrics_level="wrong"},{job_steps_per_unit=0/0},{worker_enabled=false},{native_task_steps=64}}) do
            assert(not p.request(values));assert(not p.pending() and p.generation()==0)
        end
        ''')

    def test_atomic_pending_snapshot_and_explicit_unlimited_quota(self):
        self.lua.execute('''
        assert(p.request({automatic_preparation=false,foreground_units="unlimited"}))
        assert(p.pending() and p.get("automatic_preparation"));local requested=p.requested();requested.automatic_preparation=true
        p.commit();assert(not p.get("automatic_preparation") and p.quota("foreground_units")==nil)
        assert(p.generation()==1);assert(p.request({foreground_units="inherit"}));p.commit()
        assert(p.quota("foreground_units",3)==3)
        ''')

    def test_startup_cadence_is_retained_across_later_session_applies(self):
        self.lua.execute('''
        local c={pump_ms=16};p.bind(c)
        assert(p.request({pump_ms=40}));p.commit(c,true);assert(c.pump_ms==40)
        assert(p.request({work_budget_ms=5,pump_ms=2}));p.commit(c)
        assert(c.pump_ms==40 and p.get("pump_ms")==40 and p.get("work_budget_ms")==5)
        assert(p.request(p.defaults()));p.commit(c);assert(c.pump_ms==40)
        ''')

    def test_task_snapshot_is_scoped_and_restored_after_nested_calls_and_errors(self):
        self.lua.execute('''
        assert(p.request({pure_primitives=11}));p.commit()
        assert(p.request({work_units_per_tick=3}))
        local generation=p.generation()
        local a,b,c=p.with_snapshot({pure_primitives=7},function()
            assert(p.get("pure_primitives",128)==7 and p.snapshot().pure_primitives==11)
            assert(p.pending() and p.requested().work_units_per_tick==3)
            p.with_snapshot({pure_primitives=2},function()assert(p.get("pure_primitives")==2)end)
            assert(p.get("pure_primitives")==7)
            return "ok",nil,3
        end)
        assert(a=="ok" and b==nil and c==3 and p.get("pure_primitives")==11)
        assert(not pcall(p.with_snapshot,{pure_primitives=1},function()error("fixture task failure")end))
        assert(p.get("pure_primitives")==11 and p.pending() and p.generation()==generation)
        ''')

    def test_schema_one_import_reports_retired_fields_and_maps_lua_controls(self):
        self.lua.execute('''
        local values,info=p.import_profile({worker_enabled=false,preload_paints=true,paint_cache_read=false,
            worker_admission_target=3,native_task_steps=19,work_budget_ms=4,paint_hints_enabled=false},1)
        assert(values and info.migrated and info.schemaVersion==2 and #info.retiredKeys==5)
        assert(values.job_admission_target==3 and values.job_steps_per_unit==19 and values.work_budget_ms==4)
        assert(values.paint_hints_enabled==false and values.worker_enabled==nil and values.paint_cache_read==nil)
        assert(info.retiredKeys[1]=="native_task_steps" and info.retiredKeys[5]=="worker_enabled")
        local explicit=p.import_profile({worker_admission_target=2,job_admission_target=6,
            native_task_steps=19,job_steps_per_unit=23},1)
        assert(explicit.job_admission_target==6 and explicit.job_steps_per_unit==23)
        assert(not p.pending() and p.generation()==0 and p.get("work_budget_ms")==2)
        ''')

    def test_old_stock_timers_are_retired_on_import(self):
        self.lua.execute('''
        for _,schema in ipairs({1,2}) do
            local saved,info=p.import_profile({stock_refresh_ms=200,preload_stock_closed=true,
                paint_idle_units="unlimited",work_units_per_tick=3},schema)
            assert(saved.work_units_per_tick==3 and not saved.stock_refresh_ms and not saved.preload_stock_closed)
            assert(info.migrated and #info.retiredKeys==3)
            assert(not p.validate({stock_refresh_ms=200}))
        end
        ''')

    def test_legacy_profile_preserves_independent_open_window_preference(self):
        self.lua.execute('''
        local c={open_work_budget_ms=12.5,work_budget_ms=2}
        p.bind(c)
        for _,schema in ipairs({1,2}) do
            local values=p.import_profile({work_budget_ms=4.5},schema)
            assert(values and p.request(values));p.commit(c,true)
            assert(c.open_work_budget_ms==12.5 and c.work_budget_ms==4.5)
        end
        local values=p.import_profile({open_work_budget_ms=18,work_budget_ms=3},2)
        assert(p.request(values));p.commit(c)
        assert(p.work_target_ms(false,c)==3 and p.work_target_ms(true,c)==18)
        ''')

    def test_profile_import_rejects_unknown_and_malformed_retired_fields(self):
        self.lua.execute('''
        for _,values in ipairs({{unknown=1},{worker_enabled=1},{native_task_steps=0},
            {worker_admission_target=1.5},{paint_cache_read="yes"},{worker_result_bytes=math.huge}})do
            assert(p.import_profile(values,1)==nil)
        end
        assert(p.import_profile({worker_enabled=false},2)==nil)
        assert(p.import_profile({work_units_per_tick=8},3)==nil)
        local values,info=p.import_profile({job_admission_target=4},2)
        assert(values.job_admission_target==4 and not info.migrated and #info.retiredKeys==0)
        ''')

    def test_pause_step_and_resume_clear_primitive_override(self):
        self.lua.execute('''
        p.set_paused(true);assert(not p.admit_preparation());p.step("primitive")
        local ok,kind=p.admit_preparation();assert(ok and kind=="primitive")
        p.set_primitive_step(true);assert(not p.admit_preparation())
        p.set_paused(false);assert(not p.primitive_step() and p.admit_preparation())
        ''')

    def test_profiles_expose_only_real_lua_execution_dimensions(self):
        self.lua.execute('''
        local byid={};local profiles=p.profiles();assert(#profiles==4)
        for _,v in ipairs(profiles)do byid[v.id]=v.values;assert(p.validate(v.values));assert(v.values.worker_enabled==nil)end
        assert(byid.cooperative==nil and byid.defaults.batching_enabled and byid.defaults.automatic_preparation)
        assert(not byid.demand.automatic_preparation and byid.demand.batching_enabled)
        assert(not byid.synchronous.batching_enabled and not byid.synchronous.automatic_preparation)
        assert(byid.small.work_units_per_tick==1 and byid.small.pure_primitives==1)
        for _,d in ipairs(p.descriptors())do
            assert(d.key~="worker_enabled" and not d.key:match("^native_") and not d.key:match("^worker_"))
            assert(d.key~="paint_cache_read" and d.key~="paint_cache_write" and d.group~="native" and d.group~="transport")
        end
        ''')

    def test_profile_roundtrip_and_one_shot_consumption(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.lua.globals().profile_path = (Path(temporary) / "profile.json").as_posix()
            self.lua.execute('''
            local profiles=require("paintmanager.developer_profiles")
            local values=p.defaults();values.automatic_preparation=false;values.pump_ms=23
            assert(profiles.write(profile_path,values));local loaded=assert(profiles.read(profile_path))
            assert(loaded.automatic_preparation==false and loaded.pump_ms==23 and loaded.worker_enabled==nil)
            local config={pump_ms=16};assert(profiles.consume_startup(profile_path,config))
            assert(config.pump_ms==23 and not p.get("automatic_preparation"))
            assert(not profiles.consume_startup(profile_path,config))
            ''')

    def test_legacy_profile_file_migrates_to_schema_two_and_consumes_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.lua.globals().profile_path = (Path(temporary) / "legacy.json").as_posix()
            self.lua.execute('''
            local json=require("paintmanager.json")
            local profiles=require("paintmanager.developer_profiles")
            local file=assert(io.open(profile_path,"wb"))
            assert(file:write(json.encode({owner="PaintManager.processing-profile",schemaVersion=1,
                values={worker_enabled=false,preload_paints=false,native_task_steps=17,
                    worker_admission_target=3,work_units_per_tick=12}})))
            assert(file:close())
            local loaded,migration=profiles.read(profile_path)
            assert(loaded.job_steps_per_unit==17 and loaded.job_admission_target==3 and loaded.work_units_per_tick==12)
            assert(migration.migrated and #migration.retiredKeys==4 and loaded.worker_enabled==nil)
            assert(profiles.write(profile_path,loaded))
            local bytes=assert(io.open(profile_path,"rb"));local saved=bytes:read("*a");bytes:close()
            assert(saved:find('"schemaVersion":2',1,true) and not saved:find('worker_enabled',1,true))
            local config={pump_ms=16,work_units_per_tick=8};assert(profiles.consume_startup(profile_path,config))
            assert(config.work_units_per_tick==12 and p.get("job_steps_per_unit")==17)
            assert(not profiles.consume_startup(profile_path,config))
            ''')

    def test_profile_export_preserves_unfamiliar_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.json"
            path.write_text("unfamiliar", encoding="utf-8")
            self.lua.globals().profile_path = path.as_posix()
            self.lua.execute('assert(not require("paintmanager.developer_profiles").write(profile_path,p.defaults()))')
            self.assertEqual(path.read_text(encoding="utf-8"), "unfamiliar")

    def test_capture_collects_metrics_even_when_continuous_measurements_are_off(self):
        self.lua.execute('''
        assert(p.get("metrics_level")=="off");p.record("ignored",3,2)
        assert(p.metrics().stages.ignored==nil);p.capture_next();p.callback()
        p.record("collection",4,3,{clock="fixture"});p.finish_operation("refresh",{complete=true})
        local metrics=p.metrics();assert(#metrics.captures==1)
        assert(metrics.captures[1].metrics.stages.collection.units==3)
        assert(metrics.captures[1].policy.job_steps_per_unit==64 and metrics.captures[1].policy.worker_enabled==nil and not p.measuring())
        ''')


class ProcessingMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute('start();registered.F10();step();p=require("paintmanager.processing")')

    def test_apply_is_pending_then_atomic_and_hiding_restores_defaults(self):
        self.lua.execute('''
        assert(invoke("processing_apply",{automatic_preparation=false,work_units_per_tick=2}))
        assert(p.pending() and p.get("automatic_preparation"));step()
        assert(not p.pending() and not p.get("automatic_preparation") and config.work_units_per_tick==2)
        inGameThread=true;seed_fixture_developer_tools(false);inGameThread=false
        assert(p.pending());step();assert(p.get("automatic_preparation") and config.work_units_per_tick==8)
        assert(not invoke("processing_apply",{automatic_preparation=false}))
        ''')

    def test_sync_refresh_and_report_finish_within_one_real_callback(self):
        self.lua.execute('''
        assert(invoke("processing_apply",{batching_enabled=false,automatic_preparation=false}))
        step();jobSlices=80;local before=scans;local reports=#written
        registered.F6();inGameThread=true;pump();inGameThread=false
        assert(scans==before+1 and snapshots==1 and #written==reports+1)
        assert(ui.status.state=="done" and ui.status.command=="snapshot")
        ''')

    def test_disabled_developer_gate_rejects_stale_and_disconnected_callbacks(self):
        self.lua.execute('''
        ui.active_tab="learned";assert(not invoke("processing_apply",{automatic_preparation=false}))
        ui.active_tab="debug";fixtureConnected=false
        assert(not invoke("processing_action","pause"));assert(not p.is_paused())
        ''')

    def test_synchronous_external_wait_defers_without_retry_spin(self):
        self.lua.execute('''
        assert(invoke("processing_apply",{batching_enabled=false,automatic_preparation=false}))
        step();local original=require("paintmanager.collection").begin;wait_attempts=0
        require("paintmanager.collection").begin=function(...)
            local job=original(...)
            function job:step(budget)
                wait_attempts=wait_attempts+1;budget:consume()
                return {state="running",phase="waiting_external_lease",defer=true}
            end
            return job
        end
        registered.F6();inGameThread=true;pump();inGameThread=false
        assert(wait_attempts==1 and ui.status.state=="running")
        ''')

    def test_read_only_packet_pause_preserves_input_and_one_step(self):
        self.lua.execute('''
        assert(invoke("processing_action","pause"));jobSlices=20;registered.F6();step()
        local before=scans;local work=p.work_sequence()
        assert(invoke("processing_action","step_packet"));inGameThread=true;pump();inGameThread=false
        assert(p.work_sequence()-work<=2);assert(p.is_paused())
        assert(invoke("processing_action","resume"));step();step();assert(scans>=before)
        ''')


if __name__ == "__main__":
    unittest.main()
