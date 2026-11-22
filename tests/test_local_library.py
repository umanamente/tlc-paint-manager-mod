"""Actual cooperative account-local repository jobs and publication failures."""
from pathlib import Path
import tempfile
import unittest

from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
json=require("paintmanager.json");codec=require("paintmanager.state_document")
repository=require("paintmanager.local_library")
files,operations={},{ };fs={};read_sizes={};write_sizes={}
function fs.open(path,mode)
    operations[#operations+1]="open:"..mode..":"..path
    if denied then return nil,"denied",13 end
    if mode=="rb" and files[path]==nil then return nil,"missing",2 end
    if mode=="wb" then files[path]="" end
    local file={path=path,mode=mode,offset=1,closed=false}
    function file:read(n)
        assert(n<=32769);read_sizes[#read_sizes+1]=n
        if fail_read_path==self.path then return nil,"read failure" end
        local raw=files[self.path];if self.offset>#raw then return nil end
        local part=raw:sub(self.offset,self.offset+n-1);self.offset=self.offset+#part;return part
    end
    function file:write(value)
        assert(not self.closed and #value<=8192);write_sizes[#write_sizes+1]=#value
        if fail_write then return nil,"write failure" end
        files[self.path]=files[self.path]..value;return self
    end
    function file:flush() if fail_flush then return nil,"flush failure" end;return true end
    function file:close() self.closed=true;return true end
    return file
end
function fs.rename(old,new)
    operations[#operations+1]="rename:"..old..":"..new
    if files[new]~=nil then return nil,"destination exists" end
    if fail_publish and new=="library.json" and old~="library.json.backup" then return nil,"publish failure" end
    assert(files[old]~=nil);files[new],files[old]=files[old],nil;return true
end
function fs.remove(path)
    operations[#operations+1]="remove:"..path
    if fail_remove_path==path then return nil,"remove failure" end
    assert(files[path]~=nil);files[path]=nil;return true
end
function budget(n)
    local b={units=0};function b:expired() return self.units>=n end
    function b:consume(n) self.units=self.units+(n or 1) end;return b
end
function drain(job)
    for _=1,200000 do
        local b=budget(8);local p=job:step(b);assert(b.units<=8)
        if p.state~="running" then return p end
    end
    error("Local library job stalled")
end
function reach(job,phase)
    for _=1,200000 do
        if job.state~="running" or job.phase==phase then break end
        job:step_one()
    end
    assert(job.state=="running" and job.phase==phase,job.error or job.phase)
end
function with_ship_radius(cm)
    local data=codec.new()
    data.sections.settings.data.shipRadiusCm=cm
    return data
end
function canonical(data)
    local job=codec.begin_encode(data);drain(job);assert(job.state=="done",job.error);return job:get_result()
end
function load()
    local job=repository.begin_load("library.json",{fs=fs});drain(job)
    assert(job.state=="done",job.error);return job
end
function prepare(data,options)
    local prior=load();options=options or {};options.fs=fs
    return repository.begin_save("library.json",data or codec.new(),prior.snapshot,options)
end
function count_files() local count=0;for _ in pairs(files) do count=count+1 end;return count end
'''


class LocalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, source):
        self.lua.execute(source)

    def test_missing_is_read_only_and_access_denied_is_an_error(self):
        self.run_lua('''
        job=repository.begin_load("library.json",{fs=fs});assert(#operations==0);drain(job)
        assert(job.state=="done" and job.missing and job:get_result()==nil and count_files()==0)
        assert(job.snapshot.missing and job.snapshot.raw==nil)
        denied=true;job=repository.begin_load("library.json",{fs=fs});drain(job)
        assert(job.state=="error" and not job.missing and count_files()==0)
        ''')

    def test_save_exact_payload_roundtrip_and_validated_backup(self):
        self.run_lua('''
        local old=canonical(with_ship_radius(4000));files["library.json"]=old
        local data=with_ship_radius(4750);local encoded=canonical(data)
        job=prepare(data,{encoded=encoded});drain(job)
        assert(job.state=="done" and job.local_committed and job.snapshot.raw==encoded)
        assert(files["library.json"]==encoded and files["library.json.backup"]==old and count_files()==2)
        local loaded=load();assert(loaded:get_result().sections.settings.data.shipRadiusCm==4750)
        for _,n in ipairs(write_sizes) do assert(n<=8192) end
        ''')

    def test_corrupt_future_duplicate_keys_and_invalid_backups_are_preserved(self):
        for raw in ('broken', '{"format":"PaintManager.State","schemaVersion":999}',
                    '{"format":"PaintManager.State","format":"duplicate"}'):
            with self.subTest(raw=raw):
                self.setUp()
                self.lua.globals().bad = raw
                self.run_lua('''
                files["library.json"]=bad
                job=repository.begin_load("library.json",{fs=fs});drain(job)
                assert(job.state=="error" and files["library.json"]==bad and count_files()==1)
                ''')
        self.setUp()
        self.run_lua('''
        local old=canonical(with_ship_radius(4000));files["library.json"]=old;files["library.json.backup"]="unknown backup"
        job=prepare();drain(job)
        assert(job.state=="error" and not job.local_committed)
        assert(files["library.json"]==old and files["library.json.backup"]=="unknown backup" and count_files()==2)
        ''')

    def test_missing_primary_recovers_verified_backup_without_modifying_it(self):
        self.run_lua('''
        local old=canonical(with_ship_radius(4000));files["library.json.backup"]=old
        local prior=load();assert(prior.recovered_backup and prior.snapshot.missing and prior.snapshot.backupRaw==old)
        assert(prior:get_result().sections.settings.data.shipRadiusCm==4000 and count_files()==1)
        job=repository.begin_save("library.json",with_ship_radius(4750),prior.snapshot,{fs=fs});drain(job)
        assert(job.state=="done" and files["library.json.backup"]==old and count_files()==2)
        ''')

    def test_expected_snapshot_rejects_concurrent_changes_before_writing(self):
        self.run_lua('''
        files["library.json"]=canonical(with_ship_radius(4000));job=prepare()
        local external=canonical(with_ship_radius(4250));files["library.json"]=external
        drain(job);assert(job.state=="error" and not job.local_committed)
        assert(files["library.json"]==external and count_files()==1)
        ''')

    def test_atomic_lock_rejects_other_writer_and_preserves_owner(self):
        self.run_lua('''
        first=prepare(with_ship_radius(4000));second=prepare(with_ship_radius(4750))
        reach(first,"check_source");local token=files["library.json.lock"]
        drain(second);assert(second.state=="error" and second.error:find("locked by another writer",1,true))
        assert(files["library.json.lock"]==token and files["library.json"]==nil)
        drain(first);assert(first.state=="done" and files["library.json.lock"]==nil)
        assert(load():get_result().sections.settings.data.shipRadiusCm==4000 and count_files()==1)
        ''')

    def test_crash_lock_is_not_stolen_or_deleted(self):
        self.run_lua('''
        files["library.json.lock"]="crashed writer token"
        job=prepare();drain(job)
        assert(job.state=="error" and files["library.json.lock"]=="crashed writer token" and count_files()==1)
        ''')

    def test_foreign_lock_replacement_prevents_publish_and_is_retained(self):
        self.run_lua('''
        job=prepare();reach(job,"publish");files["library.json.lock"]="foreign writer"
        drain(job);assert(job.state=="error" and not job.local_committed)
        assert(files["library.json"]==nil and files["library.json.lock"]=="foreign writer" and count_files()==1)
        ''')

    def test_context_change_before_publish_restores_backup(self):
        self.run_lua('''
        local old=canonical(with_ship_radius(4000));files["library.json"]=old;current=true
        job=prepare(with_ship_radius(4750),{is_current=function() return current,"Native account changed" end})
        reach(job,"publish");assert(job.old_moved);current=false;drain(job)
        assert(job.state=="error" and not job.local_committed and files["library.json"]==old and count_files()==1)
        ''')

    def test_local_change_after_preparation_is_not_overwritten(self):
        self.run_lua('''
        files["library.json"]=canonical(with_ship_radius(4000));job=prepare()
        reach(job,"backup_move");files["library.json"]="new external data";drain(job)
        assert(job.state=="error" and not job.local_committed and files["library.json"]=="new external data")
        assert(count_files()==1)
        ''')

    def test_flush_and_failed_publication_preserve_original(self):
        for setup in ('reach(job,"pending_flush");fail_flush=true', 'reach(job,"publish");fail_publish=true'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua('''
                old=canonical(with_ship_radius(4000));files["library.json"]=old;job=prepare(with_ship_radius(4750))
                ''' + setup + '''
                drain(job);assert(job.state=="error" and not job.local_committed)
                assert(files["library.json"]==old and count_files()==1)
                ''')

    def test_failure_after_primary_rename_reports_local_commit_without_rollback(self):
        self.run_lua('''
        local old=canonical(with_ship_radius(4000));files["library.json"]=old
        job=prepare(with_ship_radius(4750));reach(job,"published_verify");local published=files["library.json"]
        fail_read_path="library.json";drain(job)
        assert(job.state=="error" and job:get_progress().localCommitted and job:get_progress().localUncertain)
        assert(job.snapshot.raw==published and files["library.json"]==published and files["library.json.backup"]==old)
        assert(count_files()==2)
        ''')

    def test_cancel_before_or_after_commit_has_honest_outcome(self):
        self.run_lua('''
        local old=canonical(with_ship_radius(4000));files["library.json"]=old;job=prepare()
        reach(job,"publish");job:cancel("Context switched")
        assert(job.state=="cancelled" and not job.local_committed and files["library.json"]==old and count_files()==1)
        job=prepare();reach(job,"published_verify");local published=files["library.json"];job:cancel("Context switched")
        assert(job.state=="cancelled" and job:get_progress().localCommitted and files["library.json"]==published)
        assert(files["library.json.backup"]==old and count_files()==2)
        ''')

    def test_native_size_and_preflight_bytes_are_checked_before_lock_or_writes(self):
        self.run_lua('''
        local data=with_ship_radius(4000);data.sections.extra={schemaVersion=1,data=string.rep("x",4096)}
        for i=1,9 do data.sections["extra"..i]={schemaVersion=1,data=string.rep("y",4096)} end
        job=prepare(data);drain(job);assert(job.state=="error" and count_files()==0)
        job=prepare(with_ship_radius(4000),{encoded="different bytes"});drain(job)
        assert(job.state=="error" and count_files()==0)
        ''')

    def test_synchronous_final_snapshot_guard_is_bounded_and_detects_changes(self):
        self.run_lua('''
        local prior=load();assert(repository.verify_snapshot("library.json",prior.snapshot,{fs=fs}))
        files["library.json"]=canonical(with_ship_radius(4000))
        assert(not repository.verify_snapshot("library.json",prior.snapshot,{fs=fs}))
        prior=load();assert(repository.verify_snapshot("library.json",prior.snapshot,{fs=fs}))
        files["library.json"]=files["library.json"].." "
        assert(not repository.verify_snapshot("library.json",prior.snapshot,{fs=fs}))
        ''')

    def test_account_paths_are_partitioned_and_reject_untrusted_components(self):
        self.run_lua(r'''
        local a=repository.account_path("C:/Users/Local/Voyage/Saved/SaveGames/","76561198000000001")
        local b=repository.account_path("C:/Users/Local/Voyage/Saved/SaveGames","76561198000000002")
        assert(a~=b and a=="C:/Users/Local/Voyage/Saved/SaveGames/76561198000000001/PaintManager.library.json")
        for _,id in ipairs({"slot-A","765/../evil","123","../76561198000000001"}) do
            assert(not pcall(repository.account_path,"C:/SaveGames",id))
        end
        assert(not pcall(repository.account_path,"C:/../SaveGames","76561198000000001"))
        ''')

    def test_native_windows_rename_does_not_replace_an_existing_lock(self):
        # This executable runtime runs on the same Windows CRT as the mod's
        # filesystem primitive. No mocked rename supplies this assertion.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "candidate").write_text("candidate", encoding="utf-8")
            (path / "lock").write_text("owner", encoding="utf-8")
            self.lua.globals().candidate_path = (path / "candidate").as_posix()
            self.lua.globals().lock_path = (path / "lock").as_posix()
            self.run_lua('assert(not os.rename(candidate_path,lock_path))')
            self.assertEqual((path / "lock").read_text(encoding="utf-8"), "owner")
            self.assertEqual((path / "candidate").read_text(encoding="utf-8"), "candidate")


    def test_raw_read_is_only_freshness_and_never_an_implicit_validation(self):
        for raw in ('broken', '{"format":"PaintManager.State","schemaVersion":99}',
                    '{"format":"PaintManager.State","format":"duplicate"}'):
            with self.subTest(raw=raw):
                self.setUp()
                self.lua.globals().untrusted = raw
                self.run_lua('''
                files["library.json"]=untrusted
                local rawjob=repository.begin_read("library.json",{fs=fs});drain(rawjob)
                assert(rawjob.state=="done" and rawjob:get_result()==untrusted)
                assert(rawjob.snapshot.raw==untrusted and rawjob:get_encoded()==nil)
                local checked=repository.begin_load("library.json",{fs=fs});drain(checked)
                assert(checked.state=="error" and files["library.json"]==untrusted and count_files()==1)
                ''')

    def test_forged_matching_snapshot_still_validates_old_source_before_rotation(self):
        self.run_lua('''
        local future='{"format":"PaintManager.State","schemaVersion":99}'
        files["library.json"]=future
        local source={missing=false,raw=future}
        local job=repository.begin_save("library.json",with_ship_radius(4000),source,{fs=fs})
        drain(job)
        assert(job.state=="error" and not job.local_committed)
        assert(files["library.json"]==future and count_files()==1)
        assert(files["library.json.backup"]==nil)
        ''')

    def test_pending_raw_comparison_rejects_changed_bytes_before_publication(self):
        for replacement in ('broken', '{"format":"PaintManager.State","schemaVersion":99}'):
            with self.subTest(replacement=replacement):
                self.setUp()
                self.lua.globals().replacement = replacement
                self.run_lua('''
                local old=canonical(with_ship_radius(4000));files["library.json"]=old
                job=prepare(with_ship_radius(4750));reach(job,"pending_verify")
                files[job.pending]=replacement
                drain(job)
                assert(job.state=="error" and not job.local_committed)
                assert(files["library.json"]==old and count_files()==1)
                ''')

    def test_published_raw_mismatch_retains_backup_and_reports_local_uncertainty(self):
        self.run_lua('''
        local old=canonical(with_ship_radius(4000));files["library.json"]=old
        job=prepare(with_ship_radius(4750));reach(job,"published_verify")
        files["library.json"]='{"format":"PaintManager.State","schemaVersion":99}'
        drain(job)
        assert(job.state=="error" and job.local_committed and job:get_progress().localUncertain)
        assert(files["library.json.backup"]==old and files["library.json"]:find('"schemaVersion":99',1,true))
        assert(files["library.json.lock"]==nil and count_files()==2)
        ''')

    def test_validated_output_and_exact_bytes_do_not_alias_mutable_input(self):
        self.run_lua('''
        local source=with_ship_radius(4000);local expected=canonical(source)
        job=prepare(source);reach(job,"lock_prepare")
        source.schemaVersion=99;source.sections.settings.data.shipRadiusCm=5000
        source.sections.presets.data.tombstones[1]={id="unexpected",deletedAt=200}
        drain(job)
        assert(job.state=="done" and job:get_encoded()==expected and files["library.json"]==expected)
        assert(job:get_result().schemaVersion==codec.VERSION and job:get_result().sections.settings.data.shipRadiusCm==4000)
        assert(#job:get_result().sections.presets.data.tombstones==0)
        ''')

    def test_repository_pure_batches_never_group_filesystem_primitives(self):
        self.run_lua('''
        files["library.json"]=canonical(with_ship_radius(4000))
        job=prepare(with_ship_radius(4750));local examined=0
        for _=1,200000 do
            if job.state~="running" then break end
            local pure=job.phase=="encode" or job.phase=="decode"
            local prior=#operations
            job:step_one(budget(8))
            if pure then examined=examined+1;assert(#operations==prior,"Pure batch performed filesystem work") end
        end
        assert(job.state=="done" and examined>0)
        ''')


if __name__ == "__main__":
    unittest.main()
