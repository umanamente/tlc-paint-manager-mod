"""Bounded pure-data microbatches share time/count admission with the scheduler."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


class PlainWorkTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute('plain=require("paintmanager.plain_work");budget=require("paintmanager.work_budget")')

    def run_lua(self, source):
        self.lua.execute(source)

    def test_many_simple_primitives_are_one_shared_charge(self):
        self.run_lua('''
        local b=budget.new(2,8,function() return 0 end);local calls=0
        local p,n=plain.run(function() calls=calls+1;return {state="running"} end,b)
        assert(p.state=="running" and n==128 and calls==128 and b.units==0)
        b:consume();assert(b.units==1 and not b:expired())
        ''')

    def test_shared_deadline_stops_between_primitives(self):
        self.run_lua('''
        local now=0;local b=budget.new(2,8,function() return now end);local calls=0
        local p,n=plain.run(function()
            calls=calls+1;now=now+0.0006;return {state="running"}
        end,b)
        assert(n==4 and calls==4 and p.state=="running" and b.units==0)
        b:consume();assert(b:expired() and b.units==1)
        ''')

    def test_housekeeping_overrun_keeps_small_useful_minimum_quantum(self):
        self.run_lua('''
        local now=0;local b=budget.new(2,8,function() return now end)
        now=0.010;local calls=0
        local p,n=plain.run(function()
            calls=calls+1;now=now+0.00002;return {state="running"}
        end,b)
        assert(n>=5 and n<=6 and calls==n and b.units==0)
        b:consume();assert(b:expired() and b:metrics().overBudget)
        ''')

    def test_later_unit_does_not_extend_shared_target(self):
        self.run_lua('''
        local now=0;local b=budget.new(2,8,function() return now end)
        b:consume();now=0.00195
        local p,n=plain.run(function() now=now+0.00004;return {state="running"} end,b)
        assert(n==2 and p.state=="running")
        b:consume();assert(b:expired() and b.units==2)
        ''')

    def test_expired_time_or_count_never_enters_a_callback(self):
        self.run_lua('''
        local now=0;local b=budget.new(2,8,function() return now end)
        b:consume();now=0.010
        local p,n=plain.run(function() error("must not execute") end,b)
        assert(p==nil and n==0)
        local capped=budget.new(2,1,function() return 0 end);capped:consume()
        p,n=plain.run(function() error("must not execute") end,capped)
        assert(p==nil and n==0)
        ''')

    def test_constant_or_invalid_clock_still_has_independent_primitive_cap(self):
        for clock in ('return 0', 'return -1', 'return 0/0', 'error("clock unavailable")'):
            with self.subTest(clock=clock):
                self.run_lua('''
                local b=budget.new(2,8,function() '''+clock+''' end);local calls=0
                local p,n=plain.run(function() calls=calls+1;return {state="running"} end,b)
                assert(p.state=="running" and n==128 and calls==128)
                ''')

    def test_count_only_shared_budget_uses_cap_without_inventing_timing(self):
        self.run_lua('''
        local b={units=0,max_units=8}
        function b:expired() return self.units>=self.max_units end
        function b:consume() self.units=self.units+1 end
        local p,n=plain.run(function() return {state="running"} end,b)
        assert(p.state=="running" and n==128 and b.units==0)
        assert(b.clock_valid==nil and b.elapsed_ms==nil)
        b:consume();assert(b.units==1)
        ''')

    def test_clock_failure_or_rollback_during_batch_falls_back_to_cap(self):
        for change in ('now=-1', 'now=0/0', 'b.clock=function() error("failed") end'):
            with self.subTest(change=change):
                self.run_lua('''
                local now=0;local b=budget.new(2,8,function() return now end);local calls=0
                local p,n=plain.run(function()
                    calls=calls+1;if calls==2 then '''+change+''' end
                    return {state="running"}
                end,b)
                assert(p.state=="running" and n==128 and calls==128 and not b.clock_valid)
                ''')

    def test_done_error_and_cancelled_stop_without_repeating_terminal_step(self):
        for state in ('done', 'error', 'cancelled'):
            with self.subTest(state=state):
                self.lua.globals().terminal = state
                self.run_lua('''
                local calls=0;local b=budget.new(2,8,function() return 0 end)
                local p,n=plain.run(function()
                    calls=calls+1;assert(calls<=3)
                    return {state=calls==3 and terminal or "running",error="test"}
                end,b)
                assert(n==3 and calls==3 and p.state==terminal)
                ''')

    def test_a_single_indivisible_primitive_can_exceed_soft_target(self):
        self.run_lua('''
        local now=0;local b=budget.new(2,8,function() return now end)
        local p,n=plain.run(function() now=0.020;return {state="running"} end,b)
        assert(n==1 and p.state=="running")
        b:consume();assert(b:metrics().overBudget)
        ''')

    def test_streaming_encoder_chunks_are_observed_once_in_order(self):
        self.run_lua('''
        local json=require("paintmanager.json")
        local data={list=json.array({1,2,3}),text=string.rep("plain UTF-8 ",300)}
        local job=json.begin(data);local parts={};local calls=0
        while job.state=="running" do
            local b=budget.new(2,8,function() return 0 end)
            local p,n=plain.run(function()
                local step=job:step_one();calls=calls+1
                if step.chunk then parts[#parts+1]=step.chunk end
                return step
            end,b)
            assert(n<=128 and p.state~="error");b:consume()
        end
        assert(calls>10 and table.concat(parts)==json.encode(data))
        ''')

    def test_general_native_or_filesystem_jobs_are_not_accepted(self):
        self.run_lua('''
        local native_calls,fs_calls=0,0
        local native={step_one=function() native_calls=native_calls+1 end}
        local filesystem={step_one=function() fs_calls=fs_calls+1 end}
        assert(not pcall(plain.run,native) and not pcall(plain.run,filesystem))
        assert(native_calls==0 and fs_calls==0)
        ''')

    def test_callback_exception_is_not_swallowed_or_retried(self):
        self.run_lua('''
        local calls=0
        local ok,err=pcall(plain.run,function() calls=calls+1;error("primitive failed") end)
        assert(not ok and tostring(err):find("primitive failed") and calls==1)
        ''')

    def test_invalid_configuration_fails_before_execution(self):
        for value in ('0','-1','1.5','65537','math.huge','0/0','"bad"'):
            with self.subTest(value=value):
                self.run_lua('''
                local calls=0
                assert(not pcall(plain.run,function() calls=calls+1;return {state="running"} end,nil,
                    {max_primitives='''+value+'''}))
                assert(calls==0)
                ''')


if __name__ == "__main__":
    unittest.main()
