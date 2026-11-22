"""Storage coordinator stages, owned-data isolation and final byte proofs."""
import unittest

from lupa.lua54 import LuaRuntime

from test_save_store import FIXTURE, SCRIPTS


BACKGROUND = r'''
on_worker=false;retired={};worker_jobs={}
local worker_driver=require("paintmanager.worker_storage")
local background=require("paintmanager.background")
local bridge=package.loaded["paintmanager.save_bridge"]
for _,name in ipairs({"storage_context","read","write"}) do
    local original=bridge[name]
    bridge[name]=function(...) assert(not on_worker,"native access on worker");return original(...) end
end
local underlying_fs=fs
-- The coordinator's final byte proof uses the same virtual filesystem as its
-- scheduled data job. Both now run on the cooperative Lua scheduler.
local filesystem=require("paintmanager.filesystem")
for _,name in ipairs({"open","remove","rename"}) do filesystem[name]=underlying_fs[name] end
worker_fs={}
for _,name in ipairs({"open","remove","rename"}) do
    worker_fs[name]=function(...) assert(on_worker,"filesystem work on game thread");return underlying_fs[name](...) end
end
background.supports=function(kind) return kind=="storage_state" or kind=="storage_verify" end
background.begin_task=function(kind,payload,options)
    assert(not on_worker and options.path==payload.path and options.generation==0)
    assert(kind=="storage_state" or kind=="storage_verify")
    local task={state="running",handle=#worker_jobs+1}
    local control={fs=worker_fs,allow_test_paths=true,is_current=function() return true end,
        reserve_commit=function() return true end}
    task.driver=worker_driver.begin(payload,control)
    function task:get_progress() return self.driver:get_progress() end
    function task:get_result() return self.driver:get_result() end
    function task:authorize_publish() assert(not on_worker);self.control="publish" end
    function task:finish_stage(outcome)
        assert(not on_worker)
        assert(outcome=="staged" or outcome=="pending" or outcome=="applied" or outcome=="uncertain",
            "Invalid native acknowledgement outcome")
        self.control="finish";self.outcome=outcome
    end
    function task:cancel(reason) self.cancel_reason=reason or "cancelled";retired[#retired+1]=self end
    function task:step(budget)
        assert(not on_worker);on_worker=true
        if self.control=="publish" then self.driver:authorize_publish(true)
        elseif self.control=="finish" then self.driver:finish_stage(self.outcome) end
        self.control=nil
        for _=1,128 do
            local p=self.driver:step_one()
            if p.state~="running" then break end
        end
        on_worker=false
        if budget then budget:consume() end
        local progress=self:get_progress()
        if result_metadata_failure and progress.state=="local_committed" then
            return {state="error",error="Result transport unavailable after publication",
                localCommitted=true,localUncertain=true}
        end
        return progress
    end
    worker_jobs[#worker_jobs+1]=task
    return task
end
function retire_workers()
    for _,task in ipairs(retired) do
        on_worker=true;task.driver:cancel(task.cancel_reason)
        for _=1,200000 do
            local p=task.driver:step_one()
            if p.state=="local_committed" then task.driver:finish_stage("pending")
            elseif p.state~="running" then break end
        end
        on_worker=false
    end
    retired={}
end
function worker_store(expected)
    return factory("A",expected,{document={now=100}})
end
function until_phase(job,phase)
    for _=1,200000 do
        if job.phase==phase then return end
        local p=job:step(budget(8));assert(p.state=="running",p.error)
    end
    error("coordinator phase stalled: "..phase)
end
'''


class WorkerSaveStoreTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(BACKGROUND)

    def test_selected_lua_backend_stages_data_before_native_acknowledgement(self):
        self.lua.execute('''
        local selected=worker_store();assert(selected.backend=="lua_cooperative")
        job=selected.begin_ui_settings({shipRadiusCm=4000})
        assert(#operations==0 and #worker_jobs==0 and reads==0)
        drain(job);assert(job.state=="done",job.error)
        assert(job:get_progress().staged and job:get_progress().localCommitted)
        assert(job:get_result().sections.settings.data.shipRadiusCm==4000)
        assert(files["library.json"]==raw_by_context.A and files["library.json.lock"]==nil)
        assert(attempts==1 and #worker_jobs==1)
        ''')

    def test_explicit_save_checks_fresh_merged_layout_in_the_one_publication(self):
        self.lua.execute('''
        local expected=canonical(saved("existing","Different name",50))
        files["library.json"],raw_by_context.A=expected,expected
        local candidate=preset("new-id","Current",99)
        candidate.zones[1],candidate.zones[2]=candidate.zones[2],candidate.zones[1]
        candidate.zones[2].paint.displayName="Localized display name"
        local first=worker_store().begin_save_layout(candidate);drain(first)
        assert(first.state=="done",first.error)
        assert(first:get_progress().matchedPresetId=="existing" and #worker_jobs==1)
        assert(#first:get_result().presets==1 and files["library.json"]==expected and writes==0)
        candidate.zones[2].paint.assetName="DA_Paint_Suomi"
        local second=worker_store(first:get_progress().snapshot).begin_save_layout(candidate);drain(second)
        assert(second.state=="done",second.error)
        assert(second:get_progress().matchedPresetId==nil and #worker_jobs==2)
        assert(#second:get_result().presets==2 and second:get_progress().localCommitted and second:get_progress().staged)
        assert(files["library.json"]==raw_by_context.A and writes==1)
        ''')

    def test_named_save_reports_original_and_rejects_stale_preview_and_id_collision(self):
        self.lua.execute('''
        local before=canonical(saved("existing","Midnight",50))
        files["library.json"],raw_by_context.A=before,before
        local preview=worker_store().begin_load()
        drain(preview)
        local snapshot=preview:get_progress().snapshot
        local duplicate=worker_store(snapshot).begin_save_layout(preset("new-id","Preset 1",99))
        drain(duplicate)
        assert(duplicate.state=="done",duplicate.error)
        assert(duplicate:get_progress().matchedPresetId=="existing")
        assert(#duplicate:get_result().presets==1 and duplicate:get_result().presets[1].name=="Midnight")
        assert(files["library.json"]==before and raw_by_context.A==before and writes==0)
        local different=preset("new-id","Preset 1",99)
        different.zones[1].paint.assetName="DA_Paint_Suomi"
        local create=worker_store(snapshot).begin_save_layout(different)
        drain(create);assert(create.state=="done",create.error)
        local committed=raw_by_context.A
        assert(committed==files["library.json"] and committed:find("Midnight") and committed:find("Preset 1"))
        local stale=worker_store(snapshot).begin_save_layout(preset("stale","Never",100))
        drain(stale)
        assert(stale.state=="error" and files["library.json"]==committed)
        local conflicting=preset("existing","Rename",100)
        conflicting.zones[1].paint.assetName="DA_Paint_Other"
        local collision=worker_store(create:get_progress().snapshot).begin_save_layout(conflicting)
        drain(collision)
        assert(collision.state=="error" and files["library.json"]==committed and raw_by_context.A==committed)
        ''')

    def test_commit_and_final_acknowledgements_do_not_retransfer_prepared_document(self):
        self.lua.execute('''
        job=worker_store().begin_ui_settings({shipRadiusCm=4000})
        until_phase(job,"worker_permit")
        local prepared=worker_jobs[1].driver:get_result()
        assert(prepared.data and prepared.encoded and prepared.localSnapshot)
        until_phase(job,"state_stage")
        local committed=worker_jobs[1].driver:get_result()
        assert(committed.data==nil and committed.encoded==nil and committed.localSnapshot)
        assert(committed.localSnapshot.raw==prepared.encoded and committed.localCommitted)
        drain(job);assert(job.state=="done",job.error)
        local finished=worker_jobs[1].driver:get_result()
        assert(finished.data==nil and finished.encoded==nil and finished.localSnapshot==nil)
        assert(finished.localCommitted and finished.mirrorOutcome=="staged")
        assert(job:get_result().sections.settings.data.shipRadiusCm==4000)
        assert(job:get_progress().snapshot.localSnapshot.raw==prepared.encoded)
        assert(attempts==1 and files["library.json.lock"]==nil)
        ''')

    def test_mirror_source_change_before_permit_cancels_without_local_publication(self):
        self.lua.execute('''
        job=worker_store().begin_ui_settings({shipRadiusCm=4000});until_phase(job,"worker_permit")
        assert(next(files)==nil)
        raw_by_context.A=canonical(saved(nil,nil,0,4750))
        drain(job);assert(job.state=="error" and attempts==0)
        retire_workers();assert(next(files)==nil)
        ''')

    def test_local_success_is_retained_when_scope_changes_before_native_staging(self):
        self.lua.execute('''
        job=worker_store().begin_ui_settings({shipRadiusCm=4000});until_phase(job,"state_stage")
        local committed=files["library.json"];assert(committed and files["library.json.lock"])
        generation_by_context.A=2
        drain(job);assert(job.state=="done",job.error)
        local p=job:get_progress()
        assert(p.localCommitted and p.mirrorPending and p.outcome=="pending" and attempts==0)
        assert(files["library.json"]==committed and files["library.json.lock"]==nil and raw_by_context.A==nil)
        ''')

    def test_uncertain_native_setter_keeps_local_success_and_never_retries(self):
        self.lua.execute('''
        writeUncertain=true;uncertain_applied=true
        job=worker_store().begin_ui_settings({shipRadiusCm=4000});drain(job);assert(job.state=="done",job.error)
        assert(job:get_progress().outcome=="uncertain" and job:get_progress().mirrorPending)
        assert(files["library.json"]==raw_by_context.A and attempts==1 and files["library.json.lock"]==nil)
        job2=worker_store().begin_state_load();drain(job2);assert(job2.state=="done",job2.error)
        assert(job2:get_progress().outcome=="uncertain" and attempts==1)
        ''')

    def test_postcommit_failure_metadata_preserves_local_outcome_without_result_dto(self):
        self.lua.execute('''
        result_metadata_failure=true
        job=worker_store().begin_ui_settings({shipRadiusCm=4000});drain(job)
        assert(job.state=="error" and attempts==0)
        local p=job:get_progress()
        assert(p.localCommitted and p.localUncertain and p.mirrorPending and p.outcome=="local_uncertain")
        assert(files["library.json"]~=nil and raw_by_context.A==nil)
        retire_workers();assert(files["library.json.lock"]==nil)
        ''')

    def test_uncertain_local_readback_releases_native_lease_as_pending(self):
        self.lua.execute('''
        local rename=worker_fs.rename
        worker_fs.rename=function(old,new)
            local result,err=rename(old,new)
            if result and new=="library.json" then fail_read_path="library.json" end
            return result,err
        end
        job=worker_store().begin_ui_settings({shipRadiusCm=4000});drain(job)
        assert(job.state=="done",job.error)
        local p=job:get_progress()
        assert(p.localCommitted and p.localUncertain and p.mirrorPending and p.outcome=="local_uncertain")
        assert(worker_jobs[1].outcome=="pending" and worker_jobs[1].driver:get_result().mirrorOutcome=="pending")
        assert(files["library.json"]~=nil and files["library.json.lock"]==nil)
        assert(raw_by_context.A==nil and attempts==0)
        ''')

    def test_native_setter_error_releases_pending_lease_without_losing_ui_outcome(self):
        self.lua.execute('''
        writeError=true
        job=worker_store().begin_ui_settings({shipRadiusCm=4000});drain(job)
        assert(job.state=="done",job.error)
        local p=job:get_progress()
        assert(p.localCommitted and p.mirrorPending and p.outcome=="error")
        assert(worker_jobs[1].outcome=="pending" and files["library.json.lock"]==nil)
        assert(raw_by_context.A==nil and attempts==0 and writes==1)
        ''')

    def test_changed_local_preview_is_rejected_by_worker(self):
        self.lua.execute('''
        job=worker_store().begin_state_load();drain(job);assert(job.state=="done",job.error)
        local snapshot=job:get_progress().snapshot
        files["library.json"]=canonical(saved(nil,nil,200,4750))
        job=worker_store(snapshot).begin_ui_settings({shipRadiusCm=4000});drain(job)
        assert(job.state=="error" and job.error:find("since the preview",1,true))
        assert(files["library.json.lock"]==nil and attempts==1)
        retire_workers()
        ''')

    def test_corrupt_or_future_documents_never_reach_publication_or_native_write(self):
        for raw in ('"{bad}"', '"{\\"schemaVersion\\":999}"'):
            with self.subTest(raw=raw):
                self.setUp()
                self.lua.execute(f'raw_by_context.A={raw};job=worker_store().begin_state_load();drain(job)')
                self.lua.execute('assert(job.state=="error" and next(files)==nil and attempts==0);retire_workers()')

    def test_apply_proof_lease_rechecks_primary_bytes_and_releases_asynchronously(self):
        self.lua.execute('''
        local selected=worker_store()
        local initial=selected.begin_state_load();drain(initial);assert(initial.state=="done",initial.error)
        local proof=selected.begin_verify_snapshot(initial:get_progress().snapshot)
        drain(proof);assert(proof.state=="done",proof.error)
        local snapshot=proof:get_progress().snapshot
        assert(snapshot.workerLease and files["library.json.lock"])
        local before=#operations
        assert(selected.verify_snapshot(snapshot))
        assert(selected.verify_snapshot(snapshot))
        assert(#operations==before+2,"Each final proof must read the exact primary bytes")
        selected.release_snapshot(snapshot);retire_workers()
        assert(files["library.json.lock"]==nil and not selected.verify_snapshot(snapshot))
        ''')

    def test_full_preset_workflow_holds_background_lease_through_final_native_proofs(self):
        self.lua.execute('''
        files["library.json"]=canonical(saved("paint-preset"));raw_by_context.A=files["library.json"]
        apply_calls,guard_calls=0,0
        package.loaded["paintmanager.presets"]={
            begin_apply=function(config,request)
                assert(files["library.json.lock"] and request.persistence_guard())
                local job={steps=0}
                function job:step(budget)
                    assert(not on_worker and files["library.json.lock"])
                    local before=#operations;assert(request.persistence_guard())
                    local read_primary=false
                    for i=before+1,#operations do
                        if operations[i]=="open:rb:library.json" then read_primary=true end
                    end
                    assert(read_primary,"Each transfer must recheck the exact primary bytes")
                    guard_calls=guard_calls+1;self.steps=self.steps+1;budget:consume()
                    if self.steps<2 then return {state="running"} end
                    apply_calls=apply_calls+1;return {state="done"}
                end
                function job:get_receipt() return {outcome="applied_verified",attempted=true} end
                function job:cancel() end
                return job
            end,
            begin_capture=function()
                return {step=function(_,budget) budget:consume();return {state="done",view={targets={}}} end,
                    cancel=function() end}
            end,
        }
        local workflow=require("paintmanager.preset_workflow")
        job=workflow.begin(config,"unused","preset_apply",{presetId="paint-preset",target={},contextToken="A"},worker_store())
        drain(job);assert(job.state=="done",job.error)
        retire_workers()
        assert(apply_calls==1 and guard_calls==2 and files["library.json.lock"]==nil)
        local view=job.view
        local function apply_preview()
            local request={presetId="paint-preset",target={},contextToken="A",
                view=view,storageSnapshot=view.storageSnapshot}
            local start=#worker_jobs
            local apply=workflow.begin(config,"unused","preset_apply",request,worker_store(view.storageSnapshot))
            assert(apply.phase=="preset_verify")
            drain(apply)
            assert(#worker_jobs==start+1,"Apply should only acquire a fresh proof lease")
            retire_workers()
            assert(not files["library.json.lock"])
            return apply
        end
        for _=1,2 do
            local next_apply=apply_preview()
            assert(next_apply.state=="done",next_apply.error)
            view=next_apply.view
        end
        assert(apply_calls==3 and guard_calls==6)
        files["library.json"]=canonical(saved("some-other-preset"))
        local changed=apply_preview()
        assert(changed.state=="error" and apply_calls==3 and guard_calls==6)
        ''')

    def test_issued_equal_preview_uses_read_only_proof_and_rechecks_lock_bytes_and_native_context(self):
        self.lua.execute('''
        local selected=worker_store()
        local raw=canonical(saved("paint","Kept",100));files["library.json"],raw_by_context.A=raw,raw
        local initial=selected.begin_state_load();drain(initial);assert(initial.state=="done",initial.error)
        local current=initial:get_progress().snapshot
        for _,op in ipairs({"begin_decode","begin_merge","begin_validate","begin_encode","begin_update"}) do
            codec[op]=function() error("Unexpected schema work in primary proof: "..op) end
        end
        local before=#operations
        local function acquire(source)
            local proof=selected.begin_verify_snapshot(source,true);drain(proof);assert(proof.state=="done",proof.error)
            assert(proof:get_result()~=initial:get_result() and not proof:get_progress().localCommitted)
            local held=proof:get_progress().snapshot
            assert(files["library.json.lock"] and selected.verify_snapshot(held))
            return held
        end
        local held=acquire(current)
        local lock=files["library.json.lock"]
        files["library.json.lock"]="foreign";assert(not selected.verify_snapshot(held));files["library.json.lock"]=lock
        files["library.json"]="external";assert(not selected.verify_snapshot(held));files["library.json"]=raw
        raw_by_context.A=nil;assert(not selected.verify_snapshot(held));raw_by_context.A=raw
        generation_by_context.A=2;assert(not selected.verify_snapshot(held));generation_by_context.A=1
        blocked.A=true;assert(not selected.verify_snapshot(held));blocked.A=nil
        assert(selected.verify_snapshot(held))
        selected.release_snapshot(held);retire_workers();assert(not selected.verify_snapshot(held))
        local second=acquire(held);selected.release_snapshot(second);retire_workers()
        assert(files["library.json"]==raw and attempts==0 and count_files()==1)
        local primary_reads=0
        for i=before+1,#operations do
            local op=operations[i]
            if op=="open:rb:library.json" then primary_reads=primary_reads+1
            else assert(op:find("library.json.lock",1,true) or op:find("open:rb:library.json.pending-",1,true),op) end
        end
        assert(primary_reads>=2)
        ''')

    def test_ui_edits_cannot_replace_the_issued_preset_or_seed_a_later_proof(self):
        self.lua.execute('''
        local selected=worker_store()
        local raw=canonical(saved("paint","Original",100))
        files["library.json"],raw_by_context.A=raw,raw
        local initial=selected.begin_load();drain(initial)
        local snapshot=initial:get_progress().snapshot
        local record=initial:get_result().presets[1]
        local original=record.zones[1].paint.assetName
        record.name="Edited in UI";record.zones[1].paint.assetName="DA_Paint_Changed"
        for _=1,2 do
            local proof=selected.begin_verify_snapshot(snapshot,true);drain(proof);assert(proof.state=="done",proof.error)
            local protected=proof:get_result().sections.presets.data.presets[1]
            assert(protected.name=="Original" and protected.zones[1].paint.assetName==original)
            snapshot=proof:get_progress().snapshot;assert(selected.verify_snapshot(snapshot))
            protected.zones[1].paint.assetName="DA_Paint_Other"
            selected.release_snapshot(snapshot);retire_workers()
        end
        assert(files["library.json"]==raw and raw_by_context.A==raw and attempts==0)
        ''')

    def test_fast_proof_rejects_unissued_changed_bounded_or_locked_sources(self):
        self.lua.execute('''
        local selected=worker_store()
        local raw=canonical(saved("paint","Kept",100));files["library.json"],raw_by_context.A=raw,raw
        local initial=selected.begin_load();drain(initial);local snapshot=initial:get_progress().snapshot
        local n,contexts,mirror=#operations,context_reads,reads
        assert(selected.has_verified_preview(snapshot) and not selected.has_verified_preview(nil))
        local other=factory("B",nil,{document={now=100}})
        assert(not other.has_verified_preview(snapshot) and #operations==n and context_reads==contexts and reads==mirror)
        local function reject(candidate)
            local proof=selected.begin_verify_snapshot(candidate,true);drain(proof);assert(proof.state=="error",proof.state)
            retire_workers();assert(not proof:get_progress().snapshot and attempts==0)
        end
        local copy={};for k,v in pairs(snapshot) do copy[k]=v end
        assert(not selected.has_verified_preview(copy));reject(copy)
        files["library.json.lock"]="other writer";reject(snapshot)
        assert(files["library.json.lock"]=="other writer");files["library.json.lock"]=nil
        blocked.A=true;reject(snapshot);blocked.A=nil
        files["library.json"]=nil;files["library.json.backup"]=raw;reject(snapshot)
        files["library.json.backup"]=nil
        for _,value in ipairs({"{broken}",string.rep("x",32769)}) do
            files["library.json"]=value;reject(snapshot)
            raw_by_context.A=value;snapshot.raw=value;snapshot.localSnapshot.raw=value
            assert(selected.has_verified_preview(snapshot));reject(snapshot)
            snapshot.raw=raw;snapshot.localSnapshot.raw=raw;raw_by_context.A=raw
        end
        assert(not files["library.json.lock"] and files["library.json"]==string.rep("x",32769))
        ''')


if __name__ == "__main__":
    unittest.main()
