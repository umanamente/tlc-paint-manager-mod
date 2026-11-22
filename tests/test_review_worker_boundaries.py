"""Regression coverage for review fixes at worker/native publication boundaries."""
import unittest

import test_paint_cache as cache_tests
from test_presets import PRESET_FIXTURE
import test_worker_analysis as analysis_tests


class CatalogReviewTests(unittest.TestCase):
    def setUp(self):
        cache_tests.PaintCacheControllerTests.setUp(self)

    def test_repeated_observation_updates_memory_without_serialization_or_disk_rotation(self):
        self.lua.execute('''
        discovered={stable(1)};assert(cache:admit(event()));until_ready();until_idle()
        local completed=cache:catalog();completed.status="captured"
        assert(cache:observe(completed));step(true,1);until_idle()
        assert(#requests==0 and #collectors==1)
        completed.rows[1].displayName="Changed native name"
        assert(cache:observe(completed));step(true,1);until_idle()
        assert(cache:catalog().rows[1].displayName=="Changed native name" and #requests==0)
        ''')

    def test_new_observed_identity_remains_in_session_without_periodic_discovery(self):
        self.lua.execute('''
        discovered={stable(1)};assert(cache:admit(event()));until_ready();until_idle()
        local completed={status="captured",contextToken=token,rows=json.array({stable(1),stable(2)})}
        assert(cache:observe(completed));step(true,1);until_idle()
        assert(cache:catalog()==completed and #completed.rows==2 and #requests==0 and #collectors==1)
        ''')


class PresetWorkerBoundaryReviewTests(unittest.TestCase):
    def setUp(self):
        analysis_tests.WorkerCollectionTests.setUp(self)
        self.lua.execute(PRESET_FIXTURE)
        self.lua.execute('''
        native_context_reads=0
        local original_first=FindFirstOf
        function FindFirstOf(...)
            native_context_reads=native_context_reads+1
            return original_first(...)
        end
        ''')

    def test_pending_capture_worker_skips_native_context_reads_and_rejects_stale_completion(self):
        self.lua.execute('''
        local capture=presets.begin_capture(config)
        until_phase(capture,"preset_capture_worker")
        analysis_block=true
        local before=native_context_reads
        for _=1,10 do capture:step() end
        assert(native_context_reads==before)
        pawn.address=pawn.address+1
        analysis_block=false
        local p=drain(capture)
        assert(p.state=="error" and p.view==nil and nativeCalls==0)
        assert(analysis_jobs[#analysis_jobs].cancelled)
        ''')

    def test_pending_apply_worker_skips_native_context_reads_and_cannot_grant_after_context_change(self):
        self.lua.execute('''
        start();until_phase(job,"preset_worker");analysis_block=true
        local before=native_context_reads
        for _=1,10 do job:step() end
        assert(native_context_reads==before and nativeCalls==0)
        pawn.address=pawn.address+1;analysis_block=false
        drain(job)
        assert(job:get_receipt().outcome=="rejected" and nativeCalls==0)
        assert(analysis_jobs[#analysis_jobs].cancelled)
        ''')


if __name__ == "__main__":
    unittest.main()
