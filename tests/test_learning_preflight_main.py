"""Batch preparation has a fresh allowance; single Learn has no preflight.

Actual main.lua runs with native boundary fixtures. Grant implementation and
fresh witness proofs remain covered by the learning module tests.
"""
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS
from test_learning_main import LEARNING_FIXTURE
from test_bulk_main import BULK_MAIN_FIXTURE


PREFLIGHT_FIXTURE = r'''
os.clock=function() return native_ms/1000 end
prep_steps,prep_callbacks,commit_in_read_only=0,{},0
prep_total=40
prep_cost_ms=.1
catalog_fixture={contextToken=ctx,rows={}}
package.loaded["paintmanager.paint_cache"]={new=function()
    return {
        binding=function() return nil end,
        catalog=function() return catalog_fixture end,
        invalidate=function() end,
        observe=function() end,
        preview=function() return nil end,
        admit=function() return true end,
    }
end}
local function preparing_job(job,kind)
    job.preparing_left=prep_total
    local original_step=job.step
    function job:is_preparing() return self.preparing_left>0 end
    function job:step(budget)
        assert(inGameThread)
        if self:is_preparing() then
            assert(budget.read_only_preparation==true,"Preparation used the mutation/old UI budget")
            if budget:expired() then return {state="running",phase="preflight"} end
            self.preparing_left=self.preparing_left-1;prep_steps=prep_steps+1
            prep_callbacks[#prep_callbacks+1]={units=budget.units,remaining=self.preparing_left}
            native_ms=native_ms+prep_cost_ms;budget:consume(1)
            return {state="running",phase=self:is_preparing() and "preflight" or "learn_commit",
                processed=prep_total-self.preparing_left,total=prep_total,defer=worker_wait==true}
        end
        if budget.read_only_preparation then
            commit_in_read_only=commit_in_read_only+1
            error("Commit admitted with a read-only preparation budget")
        end
        return original_step(self,budget)
    end
    return job
end
local single_begin=package.loaded["paintmanager.learn_recipe"].begin
package.loaded["paintmanager.learn_recipe"].begin=function(c,request,emit,options)
    received_single_options=options
    single_job=preparing_job(single_begin(c,request,emit,options),"single")
    return single_job
end
local batch_begin=package.loaded["paintmanager.learn_all"].begin
package.loaded["paintmanager.learn_all"].begin=function(c,request,emit,options)
    received_bulk_options=options
    batch_job=preparing_job(batch_begin(c,request,emit,options),"bulk")
    return batch_job
end
function expensive_input()
    local ordinary_tick=ui.tick
    function ui:tick(budget)
        native_ms=native_ms+35
        return ordinary_tick(self,budget)
    end
end
'''


class LearningPreflightMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + LEARNING_FIXTURE + BULK_MAIN_FIXTURE + PREFLIGHT_FIXTURE)
        self.lua.execute('start();registered.F10();step()')

    def test_single_action_skips_preflight_worker_catalog_and_scheduler_entirely(self):
        self.lua.execute('''
        expensive_input();assert(invoke("learn_recipe","paint1"))
        assert(prep_steps==0 and learn_commits==1 and received_single_options==nil)
        assert(single_job==nil and ui.status.state=="done")
        assert(native_clock_reads==0 and native_frame_reads==0)
        ''')

    def test_batch_preflight_progresses_after_expensive_input_and_keeps_unit_cap(self):
        self.lua.execute('''
        expensive_input();start_bulk()
        pump_once();assert(prep_steps==8 and bulk_steps==0)
        pump_once();assert(prep_steps==16 and bulk_steps==0)
        assert(received_bulk_options.catalog==catalog_fixture)
        assert(commit_in_read_only==0)
        ''')

    def test_preflight_time_target_stops_before_another_read_only_step(self):
        for start in ("start_bulk()",):
            with self.subTest(start=start):
                self.setUp()
                self.lua.execute('expensive_input();prep_cost_ms=11;' + start + '''
                local frames,clocks=native_frame_reads,native_clock_reads
                pump_once();assert(prep_steps==3 and learn_commits==0 and bulk_steps==0)
                assert(native_frame_reads==frames+1 and native_clock_reads==clocks)
                ''')

    def test_worker_defer_does_not_spin_through_remaining_budget(self):
        for start in ("start_bulk()",):
            with self.subTest(start=start):
                self.setUp()
                self.lua.execute('expensive_input();worker_wait=true;' + start + '''
                pump_once();assert(prep_steps==1 and learn_commits==0 and bulk_steps==0)
                pump_once();assert(prep_steps==2 and learn_commits==0 and bulk_steps==0)
                ''')

    def test_batch_commit_retains_native_frame_and_expensive_input_admission(self):
        self.lua.execute('''
        prep_total=2;expensive_input();start_bulk()
        pump_once();assert(prep_steps==2 and not batch_job:is_preparing() and bulk_steps==0)
        pump_once();assert(bulk_steps==1 and commit_in_read_only==0)
        pump_once(false);assert(bulk_steps==1)
        pump_once();assert(bulk_steps==2)
        assert(native_frame_reads>0 and native_clock_reads>0)
        ''')

    def test_close_cancels_preflight_without_committing(self):
        for start in ("start_bulk()",):
            with self.subTest(start=start):
                self.setUp()
                self.lua.execute(start + '''
                pump_once();assert(prep_steps>0)
                registered.F10();pump_once();pump_once()
                assert(not ui.open and learn_commits==0 and bulk_steps==0)
                assert(learn_cancelled+bulk_cancelled==1 and commit_in_read_only==0)
                ''')

    def test_stale_catalog_is_not_reused_by_learning_factories(self):
        for start, options in (("start_bulk()", "received_bulk_options"),):
            with self.subTest(start=start):
                self.setUp()
                self.lua.execute('catalog_fixture.contextToken="stale/other-world";' + start +
                                 ';assert(' + options + '.catalog==nil)')


if __name__ == "__main__":
    unittest.main()
