"""Bulk controller admission, native budgets and explicit fallback scheduling."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS
from test_learning_main import LEARNING_FIXTURE


BULK_MAIN_FIXTURE = r'''
native_ms,native_clock_reads,native_frame_reads,engine_frame=0,0,0,10
bulk_jobs,bulk_steps,bulk_cancelled=0,0,0
bulk_total=1000
bulk_cost_ms=0
callback_notes={}
package.loaded["paintmanager.clock"]={
    source="fixture actual-call native clock",
    frame_source="fixture GFrameCounter",
    validation="offline clock fixture",
    now_ms=function()
        assert(inGameThread,"native clock outside game thread")
        native_clock_reads=native_clock_reads+1
        if clockUnavailable or failNextClock then
            failNextClock=false;return nil,"fixture native clock unavailable"
        end
        return native_ms
    end,
    frame_count=function()
        assert(inGameThread,"native frame query outside game thread")
        native_frame_reads=native_frame_reads+1
        if frameUnavailable then return nil,"fixture frame counter unavailable" end
        return engine_frame
    end,
}
package.loaded["paintmanager.learn_all"]={
    begin=function(c,request,emit,options)
        assert(inGameThread and request.gameBuild=="25982825")
        assert(request.contextToken==ctx and request.radiusCm==c.ship_radius_cm)
        bulk_jobs=bulk_jobs+1;received_clock_options=options
        local job={receipt={readOnly=false,attempted=false,outcome="pending",
            counts={eligible=bulk_total,learned=0,already_known=0,rejected=0,unverified=0}},steps=0}
        function job:get_receipt() return self.receipt end
        function job:note_scheduler_callback(sequence,frame,timing)
            callback_notes[#callback_notes+1]={sequence=sequence,frame=frame,timing=timing}
        end
        function job:cancel(reason)
            if self.done or self.cancelled then return end
            bulk_cancelled=bulk_cancelled+1;self.cancelled=true
            self.receipt.outcome=self.steps>0 and "partial" or "cancelled"
            self.receipt.reason=reason
        end
        function job:step(budget)
            assert(inGameThread)
            if budget:expired() then return {state="running",phase="bulk_recipe",completedRecipes=self.steps,totalRecipes=bulk_total} end
            last_bulk_budget=budget
            self.steps=self.steps+1;bulk_steps=bulk_steps+1
            self.receipt.attempted=true;self.receipt.counts.learned=self.steps
            native_ms=native_ms+bulk_cost_ms
            if not noConsume then budget:consume(1) end
            if failClockAfterStep then failClockAfterStep=false;failNextClock=true end
            if self.steps>=bulk_total then
                self.done=true;self.receipt.outcome=bulkOutcome or "completed"
                return {state="done",model=ui.model,receipt=self.receipt}
            end
            return {state="running",phase="bulk_recipe",processed=self.steps,total=bulk_total,
                completedRecipes=self.steps,totalRecipes=bulk_total}
        end
        return job
    end,
}
function pump_once(advance_frame)
    inGameThread=true
    if advance_frame~=false then engine_frame=engine_frame+1 end
    local ok,err=pcall(pump)
    inGameThread=false
    assert(ok,err)
end
function start_bulk()
    assert(invoke("learn_all"))
    pump_once();pump_once()
    assert(bulk_jobs==1 and bulk_steps==0,"bulk creation must not execute a paint")
end
function finish_bulk()
    for _=1,40 do pump_once() end
end
'''


class BulkMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(LEARNING_FIXTURE)
        self.lua.execute(BULK_MAIN_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def value(self, expression):
        return self.lua.eval(expression)

    def open_window(self):
        self.run_lua('start();registered.F10();step()')

    def reports(self):
        rows = json.loads(self.run_lua('return json.encode(json.array(written))'))
        return [json.loads(row['text']) for row in rows]

    def test_native_clock_is_lazy_for_startup_idle_and_single_learning(self):
        self.run_lua('start();for _=1,100 do pump_once() end')
        self.assertEqual(self.value('native_clock_reads+native_frame_reads'), 0)
        self.run_lua('registered.F10();step();assert(invoke("learn_recipe","paint1"));step();step()')
        self.assertEqual(self.value('learn_commits'), 1)
        self.assertEqual(self.value('native_clock_reads+native_frame_reads'), 0)

    def test_native_clock_allows_multiple_steps_but_unit_limit_bounds_each_pump(self):
        self.open_window()
        self.run_lua('start_bulk();pump_once()')
        self.assertEqual(self.value('bulk_steps'), 8)
        self.assertGreater(self.value('native_clock_reads'), 1)
        self.assertEqual(self.value('native_frame_reads'), 1)
        self.run_lua('pump_once()')
        self.assertEqual(self.value('bulk_steps'), 16)

    def test_native_deadline_stops_before_another_recipe_step(self):
        self.open_window()
        self.run_lua('bulk_cost_ms=11;start_bulk();pump_once()')
        self.assertEqual(self.value('bulk_steps'), 3)
        self.assertAlmostEqual(self.value('native_ms'), 33)
        self.run_lua('pump_once(false)')
        self.assertEqual(self.value('bulk_steps'), 3)

    def test_expensive_housekeeping_still_admits_one_bounded_progress_step(self):
        self.open_window()
        self.run_lua('''
            start_bulk()
            local previous=ui.tick
            function ui:tick(budget) native_ms=native_ms+35;return previous(self,budget) end
            pump_once()
        ''')
        self.assertEqual(self.value('bulk_steps'), 1)

    def test_missing_native_time_uses_one_step_per_engine_frame(self):
        self.open_window()
        self.run_lua('clockUnavailable=true;start_bulk();pump_once()')
        self.assertEqual(self.value('bulk_steps'), 1)
        self.assertEqual(self.value('last_bulk_budget.target_ms'), 30)
        self.assertFalse(self.value('last_bulk_budget.native_clock'))
        self.run_lua('pump_once(false);pump_once(false)')
        self.assertEqual(self.value('bulk_steps'), 1)
        self.run_lua('pump_once()')
        self.assertEqual(self.value('bulk_steps'), 2)
        self.assertEqual(self.value('native_clock_reads'), 1)
        self.assertIsNone(self.value('received_clock_options.clock_ms'))

    def test_missing_frame_counter_falls_back_to_one_step_per_callback(self):
        self.open_window()
        self.run_lua('clockUnavailable=true;frameUnavailable=true;start_bulk();pump_once(false);pump_once(false)')
        self.assertEqual(self.value('bulk_steps'), 2)
        self.assertEqual(self.value('callback_notes[#callback_notes].timing.admission'), 'scheduler_callback')

    def test_same_frame_guard_also_prevents_multiple_native_budgets_in_one_frame(self):
        self.open_window()
        self.run_lua('start_bulk();pump_once();pump_once(false);pump_once(false)')
        self.assertEqual(self.value('bulk_steps'), 8)

    def test_mid_pump_native_failure_does_not_execute_an_extra_recipe_step(self):
        self.open_window()
        self.run_lua('start_bulk();failClockAfterStep=true;pump_once()')
        self.assertEqual(self.value('bulk_steps'), 1)
        self.run_lua('pump_once(false);pump_once()')
        self.assertEqual(self.value('bulk_steps'), 2)
        self.assertFalse(self.value('callback_notes[#callback_notes].timing.nativeClock'))

    def test_backwards_time_switches_to_count_fallback(self):
        self.open_window()
        self.run_lua('start_bulk();native_ms=10;pump_once();native_ms=1;pump_once()')
        self.assertEqual(self.value('bulk_steps'), 9)
        self.assertFalse(self.value('callback_notes[#callback_notes].timing.nativeClock'))

    def test_backend_without_progress_charge_cannot_spin_inside_controller(self):
        self.open_window()
        self.run_lua('noConsume=true;start_bulk();pump_once()')
        self.assertEqual(self.value('bulk_steps'), 1)

    def test_duplicate_clicks_cannot_start_a_second_bulk_job(self):
        self.open_window()
        self.run_lua('''
            assert(invoke("learn_all"));assert(not invoke("learn_all"))
            pump_once();pump_once();assert(not invoke("learn_all"));pump_once()
        ''')
        self.assertEqual(self.value('bulk_jobs'), 1)

    def test_close_or_radius_change_cancels_and_preserves_partial_receipt(self):
        for action in ('registered.F10()', 'load_ship_radius(5000)'):
            with self.subTest(action=action):
                self.setUp()
                self.open_window()
                self.run_lua('clockUnavailable=true;start_bulk();pump_once();' + action + ';pump_once();pump_once()')
                self.assertEqual(self.value('bulk_steps'), 1)
                self.assertEqual(self.value('bulk_cancelled'), 1)
                reports = [r for r in self.reports() if r['kind'] == 'learn_all']
                self.assertEqual(len(reports), 1)
                self.assertFalse(reports[0]['readOnly'])
                self.assertEqual(reports[0]['data']['outcome'], 'partial')
                self.assertEqual(reports[0]['data']['counts']['learned'], 1)
                self.assertFalse(self.value('ui.learning'))

    def test_staged_status_failure_cannot_start_bulk_after_closing_ui(self):
        self.open_window()
        self.run_lua('''
            local previous=ui.set_status
            function ui:set_status(status)
                if status.command=="learn_all" and status.state=="running" then error("staged status failed") end
                return previous(self,status)
            end
            assert(invoke("learn_all"));pump_once();pump_once()
        ''')
        self.assertFalse(self.value('ui.open'))
        self.assertEqual(self.value('bulk_jobs+bulk_steps+native_clock_reads+native_frame_reads'), 0)

    def test_completed_bulk_receipt_has_mutation_scope_and_correct_result_counts(self):
        self.open_window()
        self.run_lua('bulk_total=3;start_bulk();finish_bulk()')
        reports = [r for r in self.reports() if r['kind'] == 'learn_all']
        self.assertEqual(len(reports), 1)
        self.assertFalse(reports[0]['readOnly'])
        self.assertEqual(reports[0]['data']['counts']['learned'], 3)
        self.assertEqual(reports[0]['data']['outcome'], 'completed')
        self.assertEqual(self.value('ui.status.state'), 'done')
        self.assertIn('3', self.value('ui.status.message'))
        self.assertFalse(self.value('ui.learning'))


if __name__ == '__main__':
    unittest.main()
