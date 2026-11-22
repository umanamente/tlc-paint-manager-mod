"""Streaming JSON semantics and bounded traversal, including long strings/keys."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


class JsonJobTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path; json=require("paintmanager.json")')
        self.lua.execute('''
        function drain(value, options)
            local job=json.begin(value,options)
            local parts,maximum={},0
            while job.state=="running" do
                local p=job:step_one()
                if p.chunk then parts[#parts+1]=p.chunk;maximum=math.max(maximum,#p.chunk) end
                assert(job.completed<1000000,"encoder stalled")
            end
            return job.state,table.concat(parts),job.error,maximum,job.completed
        end
        ''')

    def test_scalar_arrays_objects_and_shared_children_match_existing_encoder(self):
        self.lua.execute('''
        local shared={text="paint",amount=1.2345678901234567}
        cases={json.null,true,false,0,-1,9223372036854775807,{},json.array(),
            {z=false,a=json.array({1,true,json.null,shared}),again=shared}}
        for _,value in ipairs(cases) do
            local state,result=drain(value)
            assert(state=="done" and result==json.encode(value))
        end
        local state,result=drain(nil);assert(state=="done" and result=="null")
        ''')

    def test_long_unicode_and_control_text_crosses_chunks_without_whole_copy(self):
        original = ('Name 🎨 \u6f06' + ''.join(map(chr, range(32))) + '"\\/') * 300
        self.lua.globals().native_text = original
        self.lua.execute('value={text=native_text};expected=json.encode(value)')
        state, encoded, _, maximum, steps = self.lua.execute('return drain(value,{string_bytes=7})')
        self.assertEqual(state, "done")
        self.assertEqual(encoded, self.lua.globals().expected)
        self.assertEqual(json.loads(encoded), {"text": original})
        self.assertLessEqual(maximum, 42)
        self.assertGreater(steps, len(original.encode()) // 7)

    def test_invalid_utf8_split_across_steps_is_rejected(self):
        for expression in (
            'string.char(0xc0,0x80)', 'string.char(0xe0,0x80,0x80)',
            'string.char(0xed,0xa0,0x80)', 'string.char(0xf4,0x90,0x80,0x80)',
            'string.char(0xf0,0x90,0x80)', 'string.char(0x80)',
            'string.char(0xc2,0x41)', 'string.char(0xf5,0x80,0x80,0x80)',
        ):
            with self.subTest(expression=expression):
                self.lua.execute(f'job=json.begin({expression},{{string_bytes=1}});while job.state=="running" do job:step_one() end')
                self.assertEqual(self.lua.eval("job.state"), "error")
                self.assertIn("UTF-8", self.lua.eval("job.error"))

    def test_invalid_types_numbers_array_holes_and_cycles_are_rejected(self):
        for expression in (
            '0/0', 'math.huge', '-math.huge', 'function() end',
            'coroutine.create(function() end)', 'io.stdout', '{[1]=2}',
            'json.array({[1]=1,[3]=3})', 'json.array({[0]=1})',
            'json.array({[1.5]=1})', 'json.array({bad=1})',
        ):
            with self.subTest(expression=expression):
                self.lua.execute(f'job=json.begin({expression});while job.state=="running" do job:step_one() end')
                self.assertEqual(self.lua.eval("job.state"), "error")
        self.lua.execute('local t={};t.self=t;job=json.begin(t);while job.state=="running" do job:step_one() end')
        self.assertIn("cycles", self.lua.eval("job.error"))

    def test_depth_limit_is_identical_and_metamethods_are_ignored(self):
        self.lua.execute('''
        function nested(n) local value={} for i=2,n do value={child=value} end return value end
        local state,result=drain(nested(32));assert(state=="done" and result==json.encode(nested(32)))
        state,result,err=drain(nested(33));assert(state=="error" and err:find("32 tables",1,true))
        local object=setmetatable({}, {__pairs=function() error("pairs used") end,
            __index=function() error("index used") end})
        state,result=drain(object);assert(state=="done" and result=="{}")
        ''')

    def test_keys_are_collected_and_merge_sorted_incrementally(self):
        self.lua.execute('''
        local value={};for i=1,1000 do value[string.format("key_%04d",1001-i)]=i end
        local expected=json.encode(value)
        job=json.begin(value)
        assert(job.completed==0 and #job.stack==1)
        job:step_one();assert(job.stack[1].stage=="keys" and #job.stack[1].keys==0)
        job:step_one();assert(#job.stack[1].keys==1)
        local original=table.sort;table.sort=function() error("synchronous sort") end
        local parts={"{"}
        while job.state=="running" do local p=job:step_one();if p.chunk then parts[#parts+1]=p.chunk end end
        table.sort=original
        assert(job.state=="done" and table.concat(parts)==expected)
        assert(job.completed>10000)
        ''')

    def test_very_long_common_key_prefix_comparisons_yield(self):
        self.lua.execute('''
        local prefix=string.rep("k",4096)
        local value={[prefix.."b"]=1,[prefix.."a"]=2}
        job=json.begin(value,{key_bytes=7,string_bytes=13})
        while not (job.stack[1].kind=="table" and job.stack[1].stage=="sort") do job:step_one() end
        local frame=job.stack[1]
        local before=job.completed
        for i=1,10 do job:step_one();assert(frame.output==1 and frame.stage=="sort") end
        assert(job.completed==before+10 and frame.compare_index==71)
        ''')

    def test_cancel_releases_the_entire_unfinished_value_graph(self):
        self.lua.execute('''
        job=json.begin({large=string.rep("x",50000)})
        job:step_one();job:step_one();job:cancel("closed")
        assert(job.state=="cancelled" and job.stack==nil and job.active==nil)
        local completed=job.completed;job:step_one();assert(job.completed==completed)
        ''')


if __name__ == "__main__":
    unittest.main()
