"""Verified scalar-out marshalling for actual-call native timing and frame IDs."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
lookups,time_calls,frame_calls,os_clock_calls=0,0,0,0
seconds,partial,frame=123,0.456789,1234
function native_library(class)
    local o={valid=true,class=class}
    function o:IsValid() return self.valid end
    function o:IsA(path) return path==self.class end
    return o
end
gameplay=native_library("/Script/Engine.GameplayStatics")
system=native_library("/Script/Engine.KismetSystemLibrary")
function gameplay:GetAccurateRealTime(a,b)
    time_calls=time_calls+1
    assert(type(a)=="table" and a==b,"both reflected scalar outputs need the same table")
    -- Model the pinned handler: both scalar outputs are written by name to the
    -- first output table; no return value carries the timestamp.
    a.Seconds=seconds;a.PartialSeconds=partial
end
function system:GetFrameCount() frame_calls=frame_calls+1;return frame end
function StaticFindObject(path)
    lookups=lookups+1
    if path=="/Script/Engine.Default__GameplayStatics" then return gameplay end
    if path=="/Script/Engine.Default__KismetSystemLibrary" then return system end
    error("unexpected clock lookup: "..path)
end
os.clock=function() os_clock_calls=os_clock_calls+1;error("silent clock fallback is forbidden") end
clock=require("paintmanager.clock")
'''


class NativeClockTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, source):
        return self.lua.execute(source)

    def sample(self):
        return self.run_lua('local value,err=clock.now_ms();return value,err')

    def test_require_does_not_touch_native_objects_or_clocks(self):
        self.assertEqual(self.lua.eval('lookups+time_calls+frame_calls+os_clock_calls'), 0)

    def test_exact_scalar_output_names_and_double_fraction_convert_to_ms(self):
        value, err = self.sample()
        self.assertIsNone(err)
        self.assertAlmostEqual(value, 123456.789)
        self.assertEqual(self.lua.eval('time_calls'), 1)
        self.assertEqual(self.lua.eval('os_clock_calls'), 0)

    def test_submillisecond_samples_are_preserved_instead_of_rounded(self):
        self.run_lua('seconds=0;partial=0.00012345')
        value, err = self.sample()
        self.assertIsNone(err)
        self.assertAlmostEqual(value, 0.12345)

    def test_zero_application_time_is_a_valid_sample(self):
        self.run_lua('seconds=0;partial=0')
        self.assertEqual(self.sample(), (0, None))

    def test_library_is_resolved_again_and_not_retained_between_calls(self):
        self.sample()
        self.run_lua('''
            gameplay=native_library("/Script/Engine.GameplayStatics")
            function gameplay:GetAccurateRealTime(a,b) assert(a==b);a.Seconds=9;a.PartialSeconds=0.75 end
        ''')
        self.assertEqual(self.sample(), (9750, None))
        self.assertEqual(self.lua.eval('lookups'), 2)

    def test_unavailable_native_lookup_returns_explicit_failure_without_fallback(self):
        for expression in ('StaticFindObject=nil', 'gameplay=nil', 'gameplay.valid=false',
                           'gameplay.class="/Script/Engine.Other"',
                           'StaticFindObject=function() error("lookup failed") end'):
            with self.subTest(expression=expression):
                self.setUp()
                self.run_lua(expression)
                value, err = self.sample()
                self.assertIsNone(value)
                self.assertIn('Native accurate time unavailable', err)
                self.assertEqual(self.lua.eval('os_clock_calls'), 0)

    def test_native_failure_or_unwritten_outputs_never_invent_a_zero_sample(self):
        for body in ('error("native query failed")', 'return 12,0.5',
                     'a.Seconds=5', 'a.PartialSeconds=0.2'):
            with self.subTest(body=body):
                self.setUp()
                self.run_lua('gameplay.GetAccurateRealTime=function(self,a,b) ' + body + ' end')
                value, err = self.sample()
                self.assertIsNone(value)
                self.assertIsNotNone(err)

    def test_invalid_seconds_are_rejected(self):
        for expression in ('nil', '-1', '0/0', 'math.huge', '2147483648', '1.5', '"5"', 'false'):
            with self.subTest(expression=expression):
                self.setUp()
                self.run_lua('seconds=' + expression)
                value, err = self.sample()
                self.assertIsNone(value)
                self.assertIn('Seconds is invalid', err)

    def test_invalid_partial_seconds_are_rejected(self):
        for expression in ('nil', '-0.1', '1', '0/0', 'math.huge', '"0.5"', 'false'):
            with self.subTest(expression=expression):
                self.setUp()
                self.run_lua('partial=' + expression)
                value, err = self.sample()
                self.assertIsNone(value)
                self.assertIn('PartialSeconds is invalid', err)

    def test_time_provider_does_not_hide_backwards_samples_from_the_caller(self):
        before, _ = self.sample()
        self.run_lua('seconds=122')
        after, _ = self.sample()
        self.assertLess(after, before)
        # Stateful budgets/jobs reject this transition. This provider retains no
        # clock epoch or sample state that could leak across a fresh world/job.

    def test_native_frame_counter_returns_zero_and_large_integer_without_time_call(self):
        for frame in (0, 1234, 2**53 + 1):
            with self.subTest(frame=frame):
                self.run_lua('frame=' + str(frame))
                result = self.run_lua('local v,e=clock.frame_count();return v,e')
                self.assertEqual(result, (frame, None))
        self.assertEqual(self.lua.eval('time_calls'), 0)

    def test_invalid_or_failing_frame_counter_is_explicit(self):
        for expression in ('nil', '-1', '1.5', '0/0', 'math.huge', '"5"', 'false'):
            with self.subTest(expression=expression):
                self.setUp()
                self.run_lua('frame=' + expression)
                value, err = self.run_lua('local v,e=clock.frame_count();return v,e')
                self.assertIsNone(value)
                self.assertIn('Native frame count unavailable', err)
        self.run_lua('system.GetFrameCount=function() error("counter failed") end')
        self.assertIsNone(self.run_lua('return clock.frame_count()')[0])

    def test_frame_and_time_failures_are_independent(self):
        self.run_lua('gameplay.valid=false')
        self.assertIsNone(self.sample()[0])
        self.assertEqual(self.run_lua('return clock.frame_count()'), 1234)
        self.run_lua('gameplay.valid=true;system.valid=false')
        self.assertIsNotNone(self.sample()[0])
        self.assertIsNone(self.run_lua('return clock.frame_count()')[0])

    def test_metadata_describes_static_evidence_without_runtime_proof_claim(self):
        self.assertIn('actual-call', self.lua.eval('clock.source'))
        self.assertIn('GFrameCounter', self.lua.eval('clock.frame_source'))
        self.assertIn('runtime result pending', self.lua.eval('clock.validation'))


if __name__ == '__main__':
    unittest.main()
