"""Owned-data storage stages retain exact CAS, commit and writer-lease outcomes."""
import unittest

from lupa.lua54 import LuaRuntime

from test_local_library import FIXTURE, SCRIPTS


WORKER = r'''
worker=require("paintmanager.worker_storage")
current=true;reserve_allowed=true;reservations=0
control={fs=fs,allow_test_paths=true,
    is_current=function() return current,"old generation" end,
    reserve_commit=function() reservations=reservations+1;return reserve_allowed,"reservation revoked" end}
function begin_worker(change,raw,expected)
    return worker.begin({path="library.json",nativeRaw=raw,
        change=change,now=100,expectedLocalSnapshot=expected},control)
end
function work(job)
    for _=1,200000 do
        local p=job:step_one()
        if p.state~="running" then return p end
    end
    error("worker storage stalled")
end
function publish(job)
    assert(work(job).state=="prepared",job.error)
    job:authorize_publish(true)
    local p=work(job);assert(p.state=="local_committed",job.error)
    return p
end
function pending_files()
    local n=0
    for path in pairs(files) do if path:find("pending",1,true) then n=n+1 end end
    return n
end
'''


class WorkerStorageTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + WORKER)

    def test_prepare_performs_no_publication_then_permit_holds_exact_writer_lease(self):
        self.lua.execute('''
        job=begin_worker({uiSettings={shipRadiusCm=4000}});assert(work(job).state=="prepared",job.error)
        assert(files["library.json"]==nil and files["library.json.lock"]==nil and reservations==0)
        local expected=job:get_result().encoded
        publish(job)
        assert(files["library.json"]==expected and files["library.json.lock"]~=nil)
        assert(job:get_result().localCommitted and not job:get_result().localUncertain and reservations==1)
        assert(job:get_result().data.sections.settings.data.shipRadiusCm==4000)
        job:finish_stage("staged")
        assert(job.state=="done" and files["library.json.lock"]==nil and pending_files()==0)
        assert(job:get_result().mirrorOutcome=="staged")
        ''')

    def test_unchanged_primary_is_verified_and_leased_without_rewriting_it(self):
        self.lua.execute('''
        local raw=canonical(codec.new());files["library.json"]=raw
        job=begin_worker(nil,raw);publish(job)
        assert(not job:get_result().localCommitted and files["library.json"]==raw)
        assert(files["library.json.backup"]==nil and files["library.json.lock"]~=nil)
        for _,op in ipairs(operations) do assert(op~="rename:library.json:library.json.backup") end
        job:finish_stage("staged");assert(files["library.json.lock"]==nil)
        ''')

    def test_cancellation_before_permit_does_not_create_files(self):
        self.lua.execute('''
        job=begin_worker({uiSettings={shipRadiusCm=4000}});assert(work(job).state=="prepared")
        job:cancel("closed");assert(job.state=="cancelled" and next(files)==nil and reservations==0)
        ''')

    def test_revoked_reservation_preserves_original_and_removes_owned_preparation(self):
        self.lua.execute('''
        local raw=canonical(codec.new());files["library.json"]=raw
        job=begin_worker({uiSettings={shipRadiusCm=4000}},raw);assert(work(job).state=="prepared")
        reserve_allowed=false;job:authorize_publish(true);assert(work(job).state=="error")
        assert(files["library.json"]==raw and files["library.json.lock"]==nil and pending_files()==0)
        assert(files["library.json.backup"]==nil and reservations==1)
        ''')

    def test_context_loss_after_reservation_finishes_frozen_publication_then_releases_pending(self):
        self.lua.execute('''
        local raw=canonical(codec.new());files["library.json"]=raw
        job=begin_worker({uiSettings={shipRadiusCm=4000}},raw);assert(work(job).state=="prepared")
        job:authorize_publish(true)
        for _=1,200000 do if job.commit_reserved then break end;job:step_one() end
        assert(job.commit_reserved and reservations==1)
        current=false;job:cancel("world changed")
        assert(work(job).state=="local_committed",job.error)
        assert(job:get_result().localCommitted and files["library.json"]==job:get_result().encoded)
        assert(files["library.json.backup"]==raw and files["library.json.lock"]~=nil)
        job:cancel("native session stopped")
        assert(job.state=="done" and job:get_result().mirrorOutcome=="pending")
        assert(files["library.json.lock"]==nil and pending_files()==0)
        ''')

    def test_local_source_cas_rejects_changed_file_after_native_permit(self):
        self.lua.execute('''
        local raw=canonical(codec.new());files["library.json"]=raw
        job=begin_worker({uiSettings={shipRadiusCm=4000}},raw);assert(work(job).state=="prepared")
        local arrived=canonical(with_ship_radius(4750));files["library.json"]=arrived
        job:authorize_publish(true);assert(work(job).state=="error")
        assert(files["library.json"]==arrived and files["library.json.lock"]==nil)
        assert(files["library.json.backup"]==nil and pending_files()==0)
        ''')

    def test_invalid_and_future_native_or_local_sources_are_never_overwritten(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.setUp()
                self.lua.execute('bad="{bad}"')
                if native:
                    self.lua.execute('job=begin_worker(nil,bad)')
                else:
                    self.lua.execute('files["library.json"]=bad;job=begin_worker()')
                self.lua.execute('assert(work(job).state=="error" and reservations==0)')
                if not native:
                    self.assertEqual(self.lua.eval('files["library.json"]'), "{bad}")

    def test_frozen_edit_time_and_tombstones_match_reference_document_pipeline(self):
        self.lua.execute('''
        local original=codec.new()
        local expected=codec.begin_update(original,{uiSettings={shipRadiusCm=4000}},{now=100});drain(expected)
        job=begin_worker({uiSettings={shipRadiusCm=4000}},canonical(original));publish(job)
        assert(job:get_result().encoded==expected:get_encoded())
        assert(job:get_result().data.sections.settings.data.updatedAt.shipRadiusCm==100)
        job:finish_stage("staged")
        ''')

    def test_expected_preview_local_snapshot_is_checked_before_preparation(self):
        self.lua.execute('''
        files["library.json"]=canonical(codec.new())
        job=begin_worker(nil,nil,{missing=false,raw="changed"})
        assert(work(job).state=="error" and job.error:find("since the preview",1,true))
        assert(files["library.json.lock"]==nil and reservations==0)
        ''')

    def test_corrupt_backup_and_foreign_lock_are_preserved(self):
        self.lua.execute('''
        local raw=canonical(codec.new());files["library.json"]=raw
        files["library.json.backup"]="corrupt"
        job=begin_worker({uiSettings={shipRadiusCm=4000}},raw);assert(work(job).state=="prepared")
        job:authorize_publish(true);assert(work(job).state=="error")
        assert(files["library.json"]==raw and files["library.json.backup"]=="corrupt")
        files["library.json.lock"]="foreign"
        job=begin_worker({uiSettings={shipRadiusCm=4000}},raw);assert(work(job).state=="prepared")
        job:authorize_publish(true);assert(work(job).state=="error")
        assert(files["library.json.lock"]=="foreign" and pending_files()==0)
        ''')

    def test_production_driver_requires_exact_account_path_and_native_controls(self):
        self.lua.execute('''
        local production={fs=fs,is_current=control.is_current,reserve_commit=control.reserve_commit}
        local accepted=worker.begin({path="C:/saves/76561198000000001/PaintManager.library.json",
            accountId="76561198000000001",now=100},production)
        assert(accepted.state=="running")
        local ok=pcall(worker.begin,{path="library.json",accountId="76561198000000001",now=100},production)
        assert(not ok)
        ok=pcall(worker.begin,{path="C:/saves/76561198000000001/PaintManager.library.json",
            accountId="76561198000000002",now=100},production)
        assert(not ok)
        ''')


if __name__ == "__main__":
    unittest.main()
