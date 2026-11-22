"""Hybrid coordinator uses real codecs, merge jobs and account-local publication."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from lupa.lua54 import LuaRuntime
from test_local_library import FIXTURE as LOCAL_FIXTURE, SCRIPTS

FIXTURE = LOCAL_FIXTURE + r'''
raw_by_context={};scope_by_context={A="A:hud",B="B:hud",C="C:hud"}
account_by_context={A="76561198000000001",B="76561198000000001",C="76561198000000002"}
path_by_context={A="library.json",B="library.json",C="other-library.json"}
generation_by_context={A=1,B=1,C=1}
reads,writes,attempts,context_reads=0,0,0,0;blocked={}
package.loaded["paintmanager.save_bridge"]={
    storage_context=function(token)
        context_reads=context_reads+1
        if notReady then return {status="waiting",error="Player progress restoration pending"} end
        return {status="ready",accountId=account_by_context[token],libraryPath=path_by_context[token],
            scopeId=scope_by_context[token],loadGeneration=generation_by_context[token]}
    end,
    read=function(token,max_bytes)
        reads=reads+1;assert(max_bytes==32768)
        if readError then return {status="error",error="Native read failed"} end
        local raw=raw_by_context[token]
        return {status=raw and "present" or "missing",raw=raw,scopeId=scope_by_context[token]}
    end,
    write=function(token,expected_raw,new_raw,scope_id)
        writes=writes+1;assert(type(new_raw)=="string" and #new_raw<=32768)
        assert(files[path_by_context[token]]==new_raw,"Mirror attempted before exact local commit")
        if blocked[token] then return {status="uncertain",error="Previous uncertain setter"} end
        if scope_id~=scope_by_context[token] or raw_by_context[token]~=expected_raw then
            return {status="conflict",error="Save carrier changed"}
        end
        if writeError then return {status="error",error="Native setter unavailable"} end
        attempts=attempts+1
        if writeUncertain then
            if uncertain_applied then raw_by_context[token]=new_raw end
            blocked[token]=true;return {status="uncertain",error="Native result uncertain"}
        end
        raw_by_context[token]=new_raw;return {status="staged"}
    end,
    uncertainty=function(token) return blocked[token] and "Native result remains uncertain" or nil end,
}
save_store=require("paintmanager.save_store");config={reference_build="25982825"}
settings={repository={fs=fs},document={now=100}}
function factory(token,expected,opts)
    return save_store.new(config,token or "A","25982825",expected,opts or settings)
end
store=factory()
function preset(id,name,time)
    return {id=id or "preset-1",name=name or "Saved character",updatedAt=time or 0,
        createdUtc="2026-11-17T12:00:00Z",gameBuild="25982825",
        target={kind="character",className="/Script/Voyage.VoyageCharacter",paintSetName="Character"},
        zones=json.array({{materialSlotName="Body",partName="Body",paint={assetType="Item",assetName="DA_Paint_Canada"}},
            {materialSlotName="Head",partName="Head",paint=json.null}})}
end
function saved(id,name,time,radius)
    local data=with_ship_radius(radius or 4000);data.revision=4
    data.sections.settings.data.updatedAt.shipRadiusCm=time or 0
    if id then data.sections.presets.data.presets[1]=preset(id,name,time) end
    return data
end
function sync(token)
    local job=factory(token).begin_state_load();drain(job);assert(job.state=="done",job.error);return job
end
function find(data,id)
    for _,record in ipairs(data.sections.presets.data.presets) do if record.id==id then return record end end
end
function live_count(data) return #data.sections.presets.data.presets end
'''


class SaveStoreTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, source):
        self.lua.execute(source)

    def test_missing_sources_publish_local_before_exact_mirror(self):
        self.run_lua("""
        job=store.begin_state_load();assert(reads==0 and #operations==0);drain(job)
        assert(job.state=="done" and job:get_progress().localCommitted and job:get_progress().staged)
        assert(files["library.json"]==raw_by_context.A and writes==1 and attempts==1)
        local s=job:get_progress().snapshot
        assert(s.raw==raw_by_context.A and s.localSnapshot.raw==s.raw)
        assert(s.accountId==account_by_context.A and s.path=="library.json" and store.verify_snapshot(s))
        """)

    def test_fallback_holds_local_lease_until_hud_ack_and_releases_on_cancel(self):
        self.run_lua('''
        local bridge=package.loaded["paintmanager.save_bridge"];local write=bridge.write
        bridge.write=function(...)
            assert(files["library.json.lock"],"Local writer lease was released before HUD acknowledgement")
            return write(...)
        end
        local j=store.begin_ui_settings({shipRadiusCm=4000});drain(j);assert(j.state=="done",j.error)
        assert(j:get_progress().staged and files["library.json.lock"]==nil)
        local next=store.begin_ui_settings({shipRadiusCm=4250})
        while next.phase~="state_stage" do
            local p=next:step_one();assert(p.state=="running",p.error)
        end
        assert(files["library.json.lock"] and next.publication)
        next:cancel("Configuration window closed")
        assert(next.state=="cancelled" and files["library.json.lock"]==nil)
        assert(next:get_progress().localCommitted and next:get_progress().mirrorPending)
        assert(attempts==1)
        ''')

    def test_source_diagnostics_distinguish_restored_mirror_from_local_reseeding(self):
        self.run_lua("""
        local expected=canonical(saved("persisted","Persisted",10))
        files["library.json"]=expected
        job=sync();local p=job:get_progress()
        assert(p.nativeSourceMissing and p.nativeSourceBytes==0 and not p.nativeSourceMatchesResult)
        local encoded=files["library.json"]
        job=sync();p=job:get_progress()
        assert(not p.nativeSourceMissing and p.nativeSourceBytes==#encoded and p.nativeSourceMatchesResult)
        assert(p.nativeSourceLive==1 and p.nativeSourceDeleted==0)
        job=store.begin_delete("persisted");drain(job);local after=files["library.json"]
        raw_by_context.A=encoded;job=sync();p=job:get_progress()
        assert(p.nativeSourceLive==1 and p.nativeSourceDeleted==0 and not p.nativeSourceMatchesResult)
        assert(files["library.json"]==after and live_count(job:get_result())==0)
        """)

    def test_equal_counts_merge_both_directions_and_keep_same_name_records(self):
        self.run_lua("""
        local a=saved("local-only","Same name",10);local b=saved("save-only","Same name",20)
        a.sections.extra={schemaVersion=90,data={enabled=false,list=json.array({json.null,"kept"})},extra="retained"}
        b.sections.other={schemaVersion=5,data="also retained"}
        files["library.json"]=canonical(a);raw_by_context.A=canonical(b)
        job=sync();local d=job:get_result()
        assert(live_count(d)==2 and find(d,"local-only") and find(d,"save-only"))
        assert(d.sections.extra.extra=="retained" and d.sections.other.data=="also retained")
        assert(find(d,"local-only").updatedAt==10 and find(d,"save-only").updatedAt==20)
        assert(files["library.json"]==raw_by_context.A)
        """)

    def test_repeat_and_reverse_slot_order_converge_without_timestamp_changes(self):
        self.run_lua("""
        local a=canonical(saved("alpha","Alpha",10));local b=canonical(saved("beta","Beta",20))
        raw_by_context.A=a;raw_by_context.B=b;sync("A");sync("B");sync("A")
        local expected=files["library.json"];local previous_writes=writes
        sync("A");assert(files["library.json"]==expected and writes==previous_writes)
        files={};raw_by_context.A=a;raw_by_context.B=b;sync("B");sync("A");sync("B")
        assert(files["library.json"]==expected and raw_by_context.A==expected and raw_by_context.B==expected)
        """)

    def test_old_save_after_deletion_never_resurrects_and_recreation_needs_new_id(self):
        self.run_lua("""
        local old=canonical(saved("old-id","Original",900));raw_by_context.A=old;sync()
        job=store.begin_delete("old-id");drain(job)
        assert(job.state=="done" and #job:get_result().presets==0 and #job:get_result().tombstones==1)
        local marker=job:get_result().tombstones[1];assert(marker.id=="old-id" and marker.deletedAt==901)
        local deleted=files["library.json"];raw_by_context.B=old;job=sync("B")
        assert(live_count(job:get_result())==0 and files["library.json"]==deleted and raw_by_context.B==deleted)
        job=store.begin_add(preset("old-id","Forbidden reuse",9999));drain(job);assert(job.state=="error")
        job=store.begin_add(preset("new-id","Original",0));drain(job)
        assert(job.state=="done" and #job:get_result().presets==1 and #job:get_result().tombstones==1)
        """)

    def test_newer_whole_record_and_equal_time_deterministic_tie(self):
        self.run_lua("""
        local a=saved("one","Local",50);local b=saved("one","Newer",51)
        b.sections.presets.data.presets[1].zones[1].paint.assetName="DA_Paint_Other"
        files["library.json"]=canonical(a);raw_by_context.A=canonical(b)
        local p=find(sync():get_result(),"one")
        assert(p.name=="Newer" and p.zones[1].paint.assetName=="DA_Paint_Other" and p.updatedAt==51)
        a=saved("tie","A",70);b=saved("tie","Z",70)
        files={};files["library.json"]=canonical(a);raw_by_context.A=canonical(b);sync()
        local expected=files["library.json"]
        files={};files["library.json"]=canonical(b);raw_by_context.A=canonical(a);sync()
        assert(files["library.json"]==expected)
        """)

    def test_real_edit_monotonic_time_noop_and_mirror_repeat_keep_timestamps(self):
        self.run_lua("""
        raw_by_context.A=canonical(saved("one","Original",100));sync();settings.document.now=99
        job=store.begin_ui_settings({shipRadiusCm=4750});drain(job);assert(job.state=="done")
        assert(job:get_result().sections.settings.data.updatedAt.shipRadiusCm==101)
        job=store.begin_ui_settings({shipRadiusCm=4750});drain(job)
        assert(job:get_result().sections.settings.data.updatedAt.shipRadiusCm==101)
        job=store.begin_ui_settings({shipRadiusCm=4900});drain(job)
        assert(job:get_result().sections.settings.data.updatedAt.shipRadiusCm==102)
        settings.document.now=1;job=store.begin_add(preset("one","Edited",9999));drain(job)
        assert(job.state=="done" and job:get_result().presets[1].updatedAt==101)
        job=store.begin_add(preset("one","Edited",9999));drain(job)
        assert(job:get_result().presets[1].updatedAt==101)
        local expected=files["library.json"];raw_by_context.B=nil;sync("B")
        assert(files["library.json"]==expected and raw_by_context.B==expected)
        """)

    def test_account_switches_never_share_libraries(self):
        self.run_lua("""
        raw_by_context.A=canonical(saved("first","One",10));sync("A")
        raw_by_context.C=canonical(saved("second","Two",20));local d=sync("C"):get_result()
        assert(live_count(d)==1 and find(d,"second") and not find(d,"first"))
        assert(files["library.json"]~=files["other-library.json"])
        d=sync("B"):get_result();assert(live_count(d)==1 and find(d,"first"))
        """)

    def test_unverified_build_or_unready_restoration_never_publishes(self):
        for build in ('nil', '"unknown"', '"25982826"'):
            with self.subTest(build=build):
                self.setUp()
                self.run_lua('job=save_store.new(config,"A",'+build+',nil,settings).begin_state_load();drain(job);assert(job.state=="error" and reads+writes==0 and count_files()==0)')
        self.setUp()
        self.run_lua('notReady=true;job=store.begin_state_load();drain(job);assert(job.state=="error" and reads+writes==0 and count_files()==0)')

    def test_corrupt_future_native_or_local_data_are_preserved(self):
        for source in ('native', 'local'):
            for raw in ('broken', 'null', '{}', '{"format":"PaintManager.State","schemaVersion":999,"revision":0,"sections":{}}'):
                with self.subTest(source=source, raw=raw):
                    self.setUp()
                    self.lua.globals().bad = raw
                    self.run_lua(('raw_by_context.A=bad' if source == 'native' else 'files["library.json"]=bad') + """
                    job=store.begin_ui_settings({shipRadiusCm=4750});drain(job)
                    assert(job.state=="error" and writes==0 and not job:get_progress().localCommitted)
                    """ + ('assert(raw_by_context.A==bad and count_files()==0)' if source == 'native' else 'assert(files["library.json"]==bad and raw_by_context.A==nil and count_files()==1)'))

    def test_v1_native_migration_keeps_ids_and_stable_zero_timestamps(self):
        self.run_lua("""
        local d=saved("legacy-id","Legacy",0);d.schemaVersion=1
        d.sections.settings={schemaVersion=1,data={radiusCm=7500}}
        d.sections.presets.schemaVersion=1;d.sections.presets.data.schemaVersion=1
        d.sections.presets.data.tombstones=nil;d.sections.presets.data.presets[1].updatedAt=nil
        raw_by_context.A=json.encode(d);job=sync();d=job:get_result()
        assert(d.schemaVersion==codec.VERSION and find(d,"legacy-id").updatedAt==0)
        assert(d.sections.settings.data.updatedAt.shipRadiusCm==0 and find(d,"legacy-id").id=="legacy-id")
        local expected=files["library.json"];sync();assert(files["library.json"]==expected)
        """)

    def test_union_overflow_never_partially_publishes(self):
        self.run_lua("""
        local a=saved("a","A",1);local b=saved("b","B",2)
        for i=1,4 do a.sections["local"..i]={schemaVersion=1,data=string.rep("a",4096)} end
        for i=1,4 do b.sections["native"..i]={schemaVersion=1,data=string.rep("b",4096)} end
        local prior=canonical(a);local native=canonical(b);files["library.json"]=prior;raw_by_context.A=native
        job=store.begin_state_load();drain(job)
        assert(job.state=="error" and writes==0 and not job:get_progress().localCommitted)
        assert(files["library.json"]==prior and raw_by_context.A==native and count_files()==1)
        """)

    def test_native_scope_account_raw_or_readiness_changes_stop_before_local_commit(self):
        for edit in ('scope_by_context.A="new-hud"', 'account_by_context.A=account_by_context.C;path_by_context.A="other-library.json"',
                     'raw_by_context.A="externally changed mirror"', 'generation_by_context.A=2', 'notReady=true'):
            with self.subTest(edit=edit):
                self.setUp()
                self.run_lua("""
                raw_by_context.A=canonical(saved("old","Old",10));job=store.begin_ui_settings({shipRadiusCm=4750})
                reach(job,"local_publish");""" + edit + """
                local native=raw_by_context.A;drain(job)
                assert(job.state=="error" and not job:get_progress().localCommitted and writes==0)
                assert(raw_by_context.A==native and count_files()==0)
                """)

    def test_concurrent_local_edit_during_preparation_is_preserved(self):
        self.run_lua("""
        files["library.json"]=canonical(saved("local","Local",10));raw_by_context.A=canonical(saved("native","Native",20))
        job=store.begin_state_load();reach(job,"local_publish")
        local external=canonical(saved("external","External",30));files["library.json"]=external
        local native=raw_by_context.A;drain(job)
        assert(job.state=="error" and writes==0 and not job:get_progress().localCommitted)
        assert(files["library.json"]==external and raw_by_context.A==native and count_files()==1)
        """)

    def test_change_after_local_commit_reports_pending_and_preserves_local_success(self):
        for edit in ('scope_by_context.A="new-hud"', 'raw_by_context.A="external mirror"', 'files["library.json"]="external local file"'):
            with self.subTest(edit=edit):
                self.setUp()
                self.run_lua("""
                job=store.begin_add(preset("new-id","New",0));reach(job,"state_stage")
                assert(job:get_progress().localCommitted);""" + edit + """
                local disk=files["library.json"];local native=raw_by_context.A;drain(job)
                assert(job.state=="done" and job:get_progress().localCommitted and job:get_progress().mirrorPending)
                assert(not job:get_progress().staged and files["library.json"]==disk and raw_by_context.A==native and writes==0)
                assert(job:get_result().presets[1].id=="new-id")
                """)

    def test_local_flush_failure_never_attempts_mirror(self):
        self.run_lua("""
        raw_by_context.A=canonical(saved("old","Old",10));local native=raw_by_context.A
        job=store.begin_ui_settings({shipRadiusCm=4750});reach(job,"local_publish")
        while job.state=="running" and job.child.phase~="pending_flush" do job:step_one() end
        assert(job.state=="running");fail_flush=true;drain(job)
        assert(job.state=="error" and not job:get_progress().localCommitted and writes==0)
        assert(raw_by_context.A==native and count_files()==0)
        """)

    def test_failure_after_local_rename_retains_commit_and_pending_state(self):
        self.run_lua("""
        job=store.begin_add(preset("new-id","New",0));reach(job,"local_publish")
        while job.state=="running" and job.child.phase~="published_verify" do job:step_one() end
        assert(job.state=="running");local committed=files["library.json"];fail_read_path="library.json";drain(job)
        assert(job.state=="done" and job:get_progress().localCommitted and job:get_progress().mirrorPending)
        assert(job:get_progress().outcome=="local_uncertain")
        assert(files["library.json"]==committed and raw_by_context.A==nil and writes==0)
        """)

    def test_mirror_failure_retry_keeps_same_id_time_and_local_bytes(self):
        self.run_lua("""
        writeError=true;job=store.begin_add(preset("stable-id","Saved once",0));drain(job)
        assert(job.state=="done" and job:get_progress().localCommitted and job:get_progress().mirrorPending)
        assert(job:get_progress().outcome=="error" and not job:get_progress().staged)
        local disk=files["library.json"];local timestamp=job:get_result().presets[1].updatedAt
        settings.document.now=9999;writeError=false;job=store.begin_add(preset("stable-id","Saved once",0));drain(job)
        assert(job.state=="done" and job:get_progress().staged and #job:get_result().presets==1)
        assert(job:get_result().presets[1].updatedAt==timestamp and files["library.json"]==disk)
        assert(raw_by_context.A==disk and attempts==1)
        """)

    def test_native_uncertainty_preserves_local_success_and_blocks_another_attempt(self):
        self.run_lua("""
        writeUncertain=true;job=store.begin_add(preset("stable-id","Saved once",0));drain(job)
        assert(job.state=="done" and job:get_progress().outcome=="uncertain" and job:get_progress().mirrorPending)
        assert(job:get_progress().localCommitted and attempts==1)
        local disk=files["library.json"];local timestamp=job:get_result().presets[1].updatedAt
        job:step(budget(8));assert(attempts==1)
        writeUncertain=false;settings.document.now=9999;job=store.begin_add(preset("stable-id","Saved once",0));drain(job)
        assert(job.state=="done" and job:get_progress().outcome=="uncertain" and attempts==1)
        assert(files["library.json"]==disk and job:get_result().presets[1].updatedAt==timestamp)
        """)

    def test_preview_checks_both_local_and_native_sources(self):
        self.run_lua("""
        job=store.begin_load();drain(job);local snapshot=job:get_progress().snapshot
        assert(store.verify_snapshot(snapshot))
        files["library.json"]=canonical(saved("external","External",30));assert(not store.verify_snapshot(snapshot))
        job=factory("A",snapshot).begin_delete("anything");drain(job);assert(job.state=="error")
        """)

    def test_uncertain_write_that_changed_memory_is_not_reported_staged_on_retry(self):
        self.run_lua("""
        writeUncertain=true;uncertain_applied=true
        job=store.begin_add(preset("stable-id","Saved once",0));drain(job)
        assert(job.state=="done" and job:get_progress().mirrorPending and attempts==1)
        assert(raw_by_context.A==files["library.json"])
        writeUncertain=false;job=store.begin_state_load();drain(job)
        assert(job.state=="done" and job:get_progress().outcome=="uncertain" and job:get_progress().mirrorPending)
        assert(not job:get_progress().staged and attempts==1)
        """)

    def test_missing_replica_does_not_compete_with_present_legacy_zero_settings(self):
        self.run_lua("""
        raw_by_context.A=canonical(saved("native","Native",0,3600));job=sync()
        assert(job:get_result().sections.settings.data.shipRadiusCm==3600)
        files={};raw_by_context.A=nil;files["library.json"]=canonical(saved("local","Local",0,5000))
        job=sync();assert(job:get_result().sections.settings.data.shipRadiusCm==5000)
        """)

    def test_preview_factory_freezes_source_snapshot_and_load_generation(self):
        self.run_lua("""
        job=store.begin_load();drain(job);local snapshot=job:get_progress().snapshot
        local protected=factory("A",snapshot)
        snapshot.raw="caller mutation";snapshot.localSnapshot.raw="caller local mutation"
        job=protected.begin_load();drain(job);assert(job.state=="done")
        generation_by_context.A=2;job=protected.begin_load();drain(job);assert(job.state=="error")
        """)

    def test_invalid_new_values_and_libraries_do_not_publish(self):
        for action in ('store.begin_ui_settings({shipRadiusCm=3499})', 'store.begin_ui_settings({shipRadiusCm=4000.5})',
                       'store.begin_add({id="bad",name="Incomplete"})',
                       'store.begin_save("unused",{schemaVersion=99,presets=json.array()})',
                       'store.begin_save("unused",{schemaVersion=2,presets=json.array(),tombstones=json.array({{id="any",deletedAt=0}})})'):
            with self.subTest(action=action):
                self.setUp()
                self.run_lua('''
                job=store.begin_load();drain(job);local disk=files["library.json"];local native=raw_by_context.A
                local previous=writes;job=''' + action + ''';drain(job)
                assert(job.state=="error" and files["library.json"]==disk and raw_by_context.A==native and writes==previous)
                ''')

    def test_legacy_import_merges_and_leaves_source_and_timestamp_unchanged(self):
        raw = self.lua.execute('local p=preset("legacy","Imported",0);p.updatedAt=nil;return json.encode({schemaVersion=1,presets=json.array({p})})')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'PaintManager.presets.json'
            path.write_text(raw, encoding='utf-8')
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            self.lua.globals().source_path = path.as_posix()
            self.run_lua("""
            raw_by_context.A=canonical(saved("native","Already present",10))
            job=store.begin_import(source_path);drain(job)
            assert(job.state=="done" and live_count(job:get_result())==2 and find(job:get_result(),"legacy").updatedAt==0)
            local bytes=files["library.json"];job=store.begin_import(source_path);drain(job)
            assert(job.state=="done" and files["library.json"]==bytes)
            """)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_cancellation_before_and_after_local_commit_reports_truthfully(self):
        self.run_lua("""
        job=store.begin_add(preset("before","Before",0));reach(job,"state_preflight");job:cancel("World changed")
        assert(job.state=="cancelled" and not job:get_progress().localCommitted and writes==0 and count_files()==0)
        job=store.begin_add(preset("after","After",0));reach(job,"state_stage");local disk=files["library.json"]
        job:cancel("World changed")
        assert(job.state=="cancelled" and job:get_progress().localCommitted and job:get_progress().mirrorPending)
        assert(files["library.json"]==disk and raw_by_context.A==nil and writes==0)
        """)


    def test_equal_untrusted_local_and_native_bytes_cannot_bypass_validation(self):
        for raw in ('broken', '{"format":"PaintManager.State","schemaVersion":99}',
                    '{"format":"PaintManager.State","schemaVersion":2,"schemaVersion":2}'):
            with self.subTest(raw=raw):
                self.setUp()
                self.lua.globals().untrusted = raw
                self.run_lua('''
                files["library.json"]=untrusted;raw_by_context.A=untrusted
                job=store.begin_state_load();drain(job)
                assert(job.state=="error" and writes==0 and files["library.json"]==untrusted)
                assert(raw_by_context.A==untrusted and count_files()==1)
                ''')

    def test_missing_primary_matching_validated_mirror_recovers_backup_exactly(self):
        self.run_lua('''
        local expected=canonical(saved("from-backup","Recovered",12))
        files["library.json.backup"]=expected;raw_by_context.A=expected
        job=sync()
        assert(job:get_progress().localCommitted and job:get_progress().staged)
        assert(files["library.json"]==expected and files["library.json.backup"]==expected)
        assert(raw_by_context.A==expected and writes==0 and live_count(job:get_result())==1)
        ''')

    def test_exact_byte_shortcut_does_not_normalize_unknown_source_into_valid_data(self):
        self.run_lua('''
        local native=canonical(saved("native","Native",12));raw_by_context.A=native
        local untrusted=native:gsub('"schemaVersion":3','"schemaVersion":99',1)
        files["library.json"]=untrusted
        job=store.begin_state_load();drain(job)
        assert(job.state=="error" and writes==0 and files["library.json"]==untrusted)
        assert(raw_by_context.A==native and count_files()==1)
        ''')

    def test_changed_published_raw_never_reaches_native_after_local_commit(self):
        self.run_lua('''
        raw_by_context.A=canonical(saved("original","Original",12));sync()
        local before_native=raw_by_context.A;local before_writes=writes
        job=store.begin_ui_settings({shipRadiusCm=4750});reach(job,"local_publish")
        while job.state=="running" and job.child.phase~="published_verify" do job:step_one() end
        assert(job.child.phase=="published_verify" and job.child.local_committed)
        files["library.json"]='{"format":"PaintManager.State","schemaVersion":99}'
        drain(job);local p=job:get_progress()
        assert(job.state=="done" and p.localCommitted and p.mirrorPending and p.outcome=="local_uncertain")
        assert(writes==before_writes and raw_by_context.A==before_native)
        assert(files["library.json"]:find('"schemaVersion":99',1,true) and files["library.json.backup"]==before_native)
        ''')

    def test_coordinator_pure_batches_do_no_native_or_filesystem_work(self):
        self.run_lua('''
        files["library.json"]=canonical(saved("local","Local",12))
        raw_by_context.A=canonical(saved("native","Native",13))
        local pure={native_decode=true,local_document=true,state_merge=true,import_merge=true,
            library_validate=true,library_updated=true,state_update=true,legacy_migrate=true,state_encode=true}
        job=store.begin_state_load();local examined=0
        for _=1,200000 do
            if job.state~="running" then break end
            local phase=job.phase;local fs_before,native_before,context_before=#operations,reads,context_reads
            job:step_one(budget(8))
            if pure[phase] then
                examined=examined+1
                assert(#operations==fs_before and reads==native_before and context_reads==context_before,
                    "Pure coordinator batch performed native or filesystem work")
            end
        end
        assert(job.state=="done" and examined>0 and live_count(job:get_result())==2)
        ''')


if __name__ == "__main__":
    unittest.main()
