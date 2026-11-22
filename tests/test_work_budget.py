"""Cooperative stop conditions, including unavailable/platform-dependent clocks."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


class WorkBudgetTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path; budget=require("paintmanager.work_budget")')

    def test_elapsed_time_stops_before_next_unit(self):
        self.lua.execute('''
            now=10
            local b=budget.new(2,8,function() return now end)
            assert(not b:expired())
            b:consume(); now=10.003
            assert(b:expired() and b:metrics().overBudget)
            assert(b:metrics().units==1)
        ''')

    def test_count_cap_stops_even_with_constant_clock(self):
        self.lua.execute('''
            local b=budget.new(2,3,function() return 0 end)
            for i=1,3 do assert(not b:expired());b:consume() end
            assert(b:expired() and b:metrics().units==3)
        ''')

    def test_housekeeping_deadline_overrun_still_admits_one_unit_for_progress(self):
        self.lua.execute('''
            now=0
            local b=budget.new(2,8,function() return now end)
            now=0.010 -- mandatory input/lifecycle work happened before the job
            assert(not b:expired(), "a fresh pump must not starve forever")
            b:consume();assert(b:expired())
            assert(b:metrics().overBudget and b:metrics().minimumProgressUnits==1)
        ''')

    def test_invalid_clock_falls_back_to_unit_cap_without_stalling(self):
        for expression in ('-1', '0/0', 'math.huge', '"bad"'):
            with self.subTest(expression=expression):
                self.lua.execute(f'''
                    local b=budget.new(2,2,function() return {expression} end)
                    assert(not b:metrics().clockValid and not b:expired())
                    b:consume(2);assert(b:expired())
                ''')

    def test_clock_wrap_or_exception_preserves_count_fallback(self):
        for expression in ('9', 'error("clock failed")'):
            with self.subTest(expression=expression):
                self.lua.execute(f'''
                    local b=budget.new(2,2,function() return 10 end)
                    b.clock=function() return {expression} end
                    assert(not b:expired() and not b:metrics().clockValid)
                    b:consume(2);assert(b:expired())
                ''')

    def test_budget_does_not_claim_to_preempt_an_atomic_call(self):
        self.lua.execute('''
            now=0
            local b=budget.new(2,8,function() return now end)
            assert(not b:expired())
            -- Stand-in for a native call that returns after the target elapsed.
            now=0.050;b:consume()
            local m=b:metrics()
            assert(m.overBudget and m.elapsedMs==50 and m.units==1 and b:expired())
        ''')

    def test_each_new_pump_receives_a_fresh_budget(self):
        self.lua.execute('''
            local a=budget.new(2,1,function() return 0 end)
            a:consume();assert(a:expired())
            local b=budget.new(2,1,function() return 0 end)
            assert(not b:expired() and b:metrics().units==0)
        ''')

    def test_invalid_configuration_is_rejected(self):
        for args in ('0,8', '-1,8', 'math.huge,8', '2,0', '2,1.5'):
            with self.subTest(args=args):
                self.assertFalse(self.lua.execute(f'return pcall(function() budget.new({args}) end)')[0])


if __name__ == "__main__":
    unittest.main()
