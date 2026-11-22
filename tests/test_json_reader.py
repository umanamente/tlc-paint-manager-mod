"""Strict bounded parser rejects executable or ambiguous persistent input."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


class JsonReaderTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('''
        package.path=scripts_path.."/?.lua;"..package.path
        json=require("paintmanager.json");reader=require("paintmanager.json_reader")
        function drain(job)
            while job.state=="running" do job:step_one();assert(job.completed<500000) end
            return job
        end
        function budget(n)
            local b={units=0};function b:expired() return self.units>=n end
            function b:consume(x) self.units=self.units+x end;return b
        end
        ''')

    def decode(self, source, options=None):
        self.lua.globals().source = source
        self.lua.execute('job=reader.begin(source,' + (options or '{}') + ');drain(job)')

    def test_roundtrip_values_and_array_tags(self):
        value = {"text": 'Paint 🎨 漆\n\t"\\', "values": [True, False, None, 1, -23, 0.125, 1e20], "empty": [], "object": {}}
        self.decode(json.dumps(value, ensure_ascii=False))
        self.assertEqual(self.lua.eval('job.state'), 'done')
        self.assertEqual(json.loads(self.lua.eval('json.encode(job:get_result())')), value)
        self.lua.execute('assert(reader.is_array(job:get_result().empty));assert(not reader.is_array(job:get_result().object))')
        self.decode('false')
        self.assertIs(self.lua.eval('job:get_result()'), False)

    def test_escaped_unicode_surrogates(self):
        self.decode(r'{"text":"\uD83C\uDFA8\u6f06\u0000"}')
        self.assertEqual(json.loads(self.lua.eval('json.encode(job:get_result())')), {"text": "🎨漆\0"})

    def test_rejects_ambiguous_malformed_or_non_json(self):
        invalid = ['', '01', '-01', '+1', '1.', '1e', '1e999', 'NaN', 'Infinity', '[1,]', '{"a":1,}',
                   '{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{}{}', '[1 2]', '{a:1}',
                   '[', '{"a"}', '"line\nbreak"', r'"\x00"', r'"\uD800"', r'"\uDC00"',
                   r'"\uD800\u0061"', 'return os.execute("bad")', 'null trailing']
        for source in invalid:
            with self.subTest(source=source):
                self.decode(source)
                self.assertEqual(self.lua.eval('job.state'), 'error')
                self.assertIsNone(self.lua.eval('job:get_result()'))

    def test_limits_are_enforced_before_publication(self):
        for source, options in [('"long"', '{max_bytes=3}'), ('"long"', '{max_string=3}'),
                                ('[[[]]]', '{max_depth=2}'), ('[1,2,3]', '{max_nodes=3}'),
                                ('1' * 129, '{}')]:
            with self.subTest(source=source, options=options):
                self.decode(source, options)
                self.assertEqual(self.lua.eval('job.state'), 'error')

    def test_invalid_utf8_is_rejected(self):
        self.lua.execute('job=reader.begin("\\\""..string.char(255).."\\\"");drain(job);assert(job.state=="error")')

    def test_budget_and_cancellation_leave_no_partial_value(self):
        self.lua.execute('''
        job=reader.begin('"'..string.rep("x",4000)..'"')
        assert(job.completed==0 and job:get_result()==nil)
        local b=budget(3);job:step(b)
        assert(b.units==3 and job.completed==3 and job.state=="running" and job:get_result()==nil)
        job:cancel("closed");local n=job.completed;job:step(budget(8))
        assert(job.state=="cancelled" and job:get_result()==nil and job.completed==n)
        ''')

    def test_long_whitespace_is_bounded(self):
        self.lua.execute('''
        job=reader.begin(string.rep(" ",20000).."[]")
        job:step_one();assert(job.index==257 and job.state=="running")
        drain(job);assert(job.state=="done")
        ''')


if __name__ == "__main__":
    unittest.main()
