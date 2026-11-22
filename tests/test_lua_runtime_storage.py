"""Actual Lua executor plus storage handshake, serialization and retirement."""
import unittest
from lupa.lua54 import LuaRuntime
from test_save_store import FIXTURE, SCRIPTS

RUNTIME = r'''
background=require("paintmanager.background");coordinator=require("paintmanager.worker_save_store")
function lua_store(change,verify,expected,reuse_preview)
    return coordinator.begin(config,"A","25982825",change,false,expected,
        {repository={fs=fs},document={now=100}},verify,reuse_preview)
end
function until_state(j,wanted)
    for _=1,200000 do
        local p=j:step(budget(8));if p.state==wanted then return p end
        assert(p.state=="running",p.error)
    end
    error("State did not settle: "..wanted)
end
function storage_task(change)
    return background.begin_task("storage_state",{path="library.json",
        change=change,now=100,compactResults=true},{path="library.json",fs=fs,allow_test_paths=true})
end
'''

class LuaRuntimeStorageTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path=SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE+RUNTIME)

    def test_exact_hud_acknowledgement_holds_writer_lease(self):
        self.lua.execute('''
        local j=lua_store({uiSettings={shipRadiusCm=4000}})
        while j.phase~="state_stage" do local p=j:step(budget(8));assert(p.state=="running",p.error) end
        assert(files["library.json.lock"] and files["library.json"] and raw_by_context.A==nil)
        local original=package.loaded["paintmanager.save_bridge"].write
        package.loaded["paintmanager.save_bridge"].write=function(...)
            assert(files["library.json.lock"],"Lease released before HUD staging");return original(...)
        end
        drain(j);assert(j.state=="done",j.error)
        assert(j.backend=="lua_cooperative" and files["library.json"]==raw_by_context.A)
        assert(files["library.json.lock"]==nil and attempts==1 and not background.is_busy())
        ''')

    def test_same_path_defers_until_held_lease_finishes(self):
        self.lua.execute('''
        local first=storage_task({uiSettings={shipRadiusCm=4000}});until_state(first,"prepared")
        first:authorize_publish();until_state(first,"local_committed")
        local second=storage_task({uiSettings={shipRadiusCm=4250}});local p=second:step(budget(8))
        assert(p.state=="running" and p.defer and files["library.json.lock"])
        first:finish_stage("staged");until_state(first,"done")
        until_state(second,"prepared");second:cancel("not applied")
        assert(files["library.json.lock"]==nil and not background.is_busy())
        ''')

    def test_paused_storage_yields_one_execution_permit_and_keeps_its_writer_lease(self):
        self.lua.execute('''
        local processing=require("paintmanager.processing")
        local policy=processing.snapshot();policy.job_admission_target=1;processing.install_snapshot(policy)
        local first=lua_store({uiSettings={shipRadiusCm=4000}})
        while first.phase~="state_stage" do assert(first:step(budget(8)).state=="running",first.error) end
        assert(first.worker.reserved and files["library.json.lock"])
        first:yield_execution()
        local second=storage_task({uiSettings={shipRadiusCm=4250}})
        assert(second:step(budget(8)).defer and not second.reserved)
        local view=background.begin_task("analysis_collection",{mode="source_candidates",selected=json.array(),
            candidates=json.array({{id="a",distance=1}}),maximum=2})
        assert(until_state(view,"done").state=="done" and files["library.json.lock"] and attempts==0)
        drain(first);assert(first.state=="done" and attempts==1 and not files["library.json.lock"])
        until_state(second,"prepared");second:cancel("Test finished");assert(not background.is_busy())
        ''')

    def test_revocation_after_reservation_drains_frozen_publication(self):
        self.lua.execute('''
        local j=storage_task({uiSettings={shipRadiusCm=4000}});until_state(j,"prepared");j:authorize_publish()
        for _=1,200000 do if j.commit_reserved then break end;local p=j:step(budget(8));assert(p.state=="running",p.error) end
        assert(j.commit_reserved);background.invalidate_scope()
        for _=1,200000 do if not background.is_busy() then break end;background.pump(budget(8)) end
        assert(not background.is_busy() and files["library.json.lock"]==nil)
        local decoded=codec.begin_decode(files["library.json"]);drain(decoded)
        assert(decoded:get_result().sections.settings.data.shipRadiusCm==4000)
        assert(raw_by_context.A==nil and attempts==0)
        ''')

    def test_scope_invalidation_defers_owned_file_cleanup_to_pump(self):
        self.lua.execute('''
        local j=storage_task({uiSettings={shipRadiusCm=4000}});until_state(j,"prepared");j:authorize_publish()
        for _=1,200000 do
            if j.driver.child and j.driver.child.phase=="lock_write" then break end
            local p=j:step(budget(8));assert(p.state=="running",p.error)
        end
        local owned=j.driver.child.file;assert(owned and not owned.closed)
        local before=#operations;background.invalidate_scope()
        assert(#operations==before and not owned.closed,"Lifecycle revocation performed filesystem cleanup")
        assert(not j:_current() and background.is_busy())
        background.pump(budget(8))
        assert(owned.closed and not background.is_busy() and count_files()==0)
        assert(raw_by_context.A==nil and attempts==0)
        ''')

    def test_scope_loss_before_hud_preserves_local_success(self):
        self.lua.execute('''
        local j=lua_store({uiSettings={shipRadiusCm=4000}})
        while j.phase~="state_stage" do local p=j:step(budget(8));assert(p.state=="running",p.error) end
        generation_by_context.A=2;drain(j);assert(j.state=="done",j.error)
        local p=j:get_progress();assert(p.localCommitted and p.mirrorPending and p.outcome=="pending")
        assert(files["library.json"] and files["library.json.lock"]==nil and attempts==0)
        ''')

    def test_external_primary_change_before_hud_preserves_new_bytes_without_mirror(self):
        self.lua.execute('''
        local j=lua_store({uiSettings={shipRadiusCm=4000}})
        while j.phase~="state_stage" do local p=j:step(budget(8));assert(p.state=="running",p.error) end
        local external=canonical(saved("external","Kept",500));files["library.json"]=external
        drain(j);assert(j.state=="done",j.error)
        assert(j:get_progress().localCommitted and j:get_progress().mirrorPending)
        assert(files["library.json"]==external and files["library.json.lock"]==nil)
        assert(attempts==0 and raw_by_context.A==nil)
        ''')

    def test_unchanged_proof_lease_does_not_rewrite_primary(self):
        self.lua.execute('''
        local raw=canonical(saved("existing","Kept",50));files["library.json"]=raw;raw_by_context.A=raw
        local load=lua_store();drain(load);assert(load.state=="done",load.error)
        local verify=lua_store(nil,true,load:get_progress().snapshot);drain(verify);assert(verify.state=="done",verify.error)
        local s=verify:get_progress().snapshot
        assert(s.workerLease and files["library.json.lock"] and files["library.json"]==raw)
        assert(coordinator.verify_snapshot("A",s));coordinator.release_snapshot(s)
        for _=1,100 do if not background.is_busy() then break end;background.pump(budget(8)) end
        assert(not background.is_busy() and files["library.json.lock"]==nil)
        ''')

    def test_proof_lease_detects_external_primary_change(self):
        self.lua.execute('''
        local raw=canonical(saved("existing","Kept",50));files["library.json"]=raw;raw_by_context.A=raw
        local load=lua_store();drain(load)
        local verify=lua_store(nil,true,load:get_progress().snapshot);drain(verify)
        assert(verify.state=="done",verify.error)
        local snapshot=verify:get_progress().snapshot;files["library.json"]="external bytes"
        local ok,error=coordinator.verify_snapshot("A",snapshot)
        assert(not ok and error:find("snapshot changed",1,true))
        coordinator.release_snapshot(snapshot)
        assert(files["library.json"]=="external bytes" and files["library.json.lock"]==nil)
        ''')

    def test_fast_primary_proof_holds_real_writer_lane_and_refuses_a_native_change_during_lock_acquisition(self):
        self.lua.execute('''
        local raw=canonical(saved("paint","Kept",50));files["library.json"],raw_by_context.A=raw,raw
        local initial=lua_store();drain(initial);local preview=initial:get_progress().snapshot
        local fast=lua_store(nil,true,preview,true)
        until_state(fast,"done");local held=fast:get_progress().snapshot
        assert(coordinator.verify_snapshot("A",held))
        local competing=storage_task({uiSettings={shipRadiusCm=4000}});assert(competing:step(budget(8)).defer)
        coordinator.release_snapshot(held);assert(not files["library.json.lock"])
        until_state(competing,"prepared");competing:cancel("Test finished")
        local changed=lua_store(nil,true,preview,true)
        for _=1,200 do
            local p=changed:step(budget(1));assert(p.state=="running",p.error)
            if files["library.json.lock"] then break end
        end
        assert(files["library.json.lock"]);generation_by_context.A=2;drain(changed)
        for _=1,100 do if not background.is_busy() then break end;background.pump(budget(8)) end
        assert(changed.state=="error" and not files["library.json.lock"] and not background.is_busy())
        assert(files["library.json"]==raw and raw_by_context.A==raw and attempts==0)
        ''')

    def test_shutdown_finishes_reserved_work_with_no_future_callback(self):
        self.lua.execute('''
        local j=storage_task({uiSettings={shipRadiusCm=4000}});until_state(j,"prepared");j:authorize_publish()
        for _=1,200000 do if j.commit_reserved then break end;j:step(budget(8)) end
        local complete,result=background.shutdown()
        assert(complete and result.pending==0 and result.steps>0)
        assert(files["library.json"] and files["library.json.lock"]==nil and attempts==0)
        local rejected,why=background.begin_task("analysis_view",{})
        assert(rejected==nil and why:find("unloading",1,true))
        ''')

    def test_shutdown_limit_preserves_uncertain_publication_evidence(self):
        self.lua.execute('''
        local j=storage_task({uiSettings={shipRadiusCm=4000}});until_state(j,"prepared");j:authorize_publish()
        for _=1,200000 do if j.commit_reserved then break end;j:step(budget(8)) end
        local complete,result=background.shutdown({max_steps=0})
        assert(not complete and result.pending==1 and result.uncertain==1 and result.steps==0)
        assert(files["library.json.lock"] and j:get_progress().localUncertain)
        ''')

if __name__=="__main__":unittest.main()
