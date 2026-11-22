"""Exercise the original Lua encoder against Python's standard JSON parser."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaError, LuaRuntime


MODULE = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts/paintmanager/json.lua"


class JsonEncoderTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = MODULE.parent.parent.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.encoder = self.lua.execute(MODULE.read_text(encoding="utf-8"))
        self.lua.globals().encoder = self.encoder

    def encode_lua(self, expression):
        return self.lua.execute("return encoder.encode(" + expression + ")")

    def test_empty_object_array_and_null_are_distinct(self):
        self.assertEqual(self.encode_lua("{}"), "{}")
        self.assertEqual(self.encode_lua("encoder.array()"), "[]")
        self.assertEqual(self.encode_lua("encoder.array({})"), "[]")
        self.assertEqual(self.encode_lua("encoder.null"), "null")
        self.assertEqual(self.encode_lua("nil"), "null")

    def test_sorted_keys_and_nested_types(self):
        encoded = self.encode_lua(
            '{z=false, a=encoder.array({1, true, encoder.null, {name="paint"}})}'
        )
        self.assertEqual(encoded, '{"a":[1,true,null,{"name":"paint"}],"z":false}')
        self.assertEqual(json.loads(encoded), {"a": [1, True, None, {"name": "paint"}], "z": False})

    def test_unicode_controls_quotes_and_backslashes_round_trip(self):
        original = "Paint 🎨 \u6f06\u2028\u2029" + ''.join(map(chr, range(32))) + '"\\/'
        encoded = self.encoder.encode(original)
        self.assertEqual(json.loads(encoded), original)
        self.assertFalse(any(ord(char) < 32 for char in encoded))
        self.assertIn(r'\u0000', encoded)
        self.assertIn(r'\u001f', encoded)
        self.assertIn(r'\"', encoded)
        self.assertIn(r'\\', encoded)

    def test_unicode_and_control_characters_in_keys(self):
        key = 'Name\x00\n"\\🎨'
        value = self.lua.table()
        value[key] = "ok"
        self.assertEqual(json.loads(self.encoder.encode(value)), {key: "ok"})

    def test_byte_comparison_yields_on_long_prefixes_and_handles_empty_prefixes(self):
        self.lua.execute('''
        assert(encoder.compare_bytes("","")==0)
        assert(encoder.compare_bytes("","a")==-1 and encoder.compare_bytes("a","")==1)
        assert(encoder.compare_bytes("same","same")==0)
        assert(encoder.compare_bytes("prefix","prefix-long")==-1)
        local left,right=string.rep("x",4096).."a",string.rep("x",4096).."b"
        local index,calls=1,0
        repeat
            local order,next_index=encoder.compare_bytes(left,right,index,1024)
            calls=calls+1
            if order~=nil then assert(order==-1 and calls==5);break end
            assert(next_index==index+1024);index=next_index
        until calls>10
        assert(encoder.byte_less(left,right) and not encoder.byte_less(right,left))
        ''')

    def test_canonical_key_order_is_utf8_byte_order_for_available_process_locales(self):
        self.lua.execute('''
        local original=os.setlocale(nil,"collate")
        local input={ ["z"]=1,["Z"]=2,["ä"]=3,["a"]=4,["Ω"]=5,["日"]=6 }
        local expected='{"Z":2,"a":4,"z":1,"ä":3,"Ω":5,"日":6}'
        for _,locale in ipairs({"C","German_Germany.1252","German","de_DE.UTF-8","en_US.UTF-8",""}) do
            if os.setlocale(locale,"collate") then
                assert(encoder.encode(input)==expected)
                assert(encoder.byte_less("z","ä") and not encoder.byte_less("ä","z"))
            end
        end
        assert(os.setlocale(original,"collate"))
        ''')

    def test_numbers_keep_precision_and_large_integers(self):
        encoded = self.encode_lua("encoder.array({0, -1, 0.1, 1.2345678901234567, 1e100, 9223372036854775807})")
        self.assertEqual(json.loads(encoded), [0, -1, 0.1, 1.2345678901234567, 1e100, 9223372036854775807])

    def test_nonfinite_numbers_are_rejected(self):
        for expression in ("0/0", "math.huge", "-math.huge"):
            with self.subTest(expression=expression), self.assertRaises(LuaError):
                self.encode_lua(expression)

    def test_object_keys_must_be_strings(self):
        for expression in ("{[1]=2}", "{[true]=2}", "{[{}]=2}"):
            with self.subTest(expression=expression), self.assertRaises(LuaError):
                self.encode_lua(expression)

    def test_arrays_reject_holes_and_non_integer_keys(self):
        for table in ("{[1]=1,[3]=3}", "{[0]=1}", "{[-1]=1}", "{[1.5]=1}", '{x=1}'):
            with self.subTest(table=table), self.assertRaises(LuaError):
                self.encode_lua("encoder.array(" + table + ")")

    def test_cycles_are_rejected_but_shared_children_are_allowed(self):
        with self.assertRaises(LuaError):
            self.lua.execute("local t={}; t.self=t; return encoder.encode(t)")
        with self.assertRaises(LuaError):
            self.lua.execute("local t=encoder.array(); t[1]=t; return encoder.encode(t)")
        encoded = self.lua.execute("local t={x=1}; return encoder.encode({a=t,b=t})")
        self.assertEqual(json.loads(encoded), {"a": {"x": 1}, "b": {"x": 1}})

    def test_depth_limit_counts_containers(self):
        self.lua.execute("function nested(n) local t={} for i=2,n do t={child=t} end return t end")
        json.loads(self.lua.execute("return encoder.encode(nested(32))"))
        with self.assertRaises(LuaError):
            self.lua.execute("return encoder.encode(nested(33))")

    def test_unsupported_values_and_invalid_utf8_are_rejected(self):
        for expression in ("function() end", "coroutine.create(function() end)", "io.stdout", "string.char(255)"):
            with self.subTest(expression=expression), self.assertRaises(LuaError):
                self.encode_lua(expression)

    def test_table_metamethods_cannot_invent_snapshot_data(self):
        encoded = self.encode_lua('setmetatable({}, {__pairs=function() error("not allowed") end, __index=function() error("not allowed") end})')
        self.assertEqual(encoded, "{}")
        for expression in ("encoder.array(false)", "encoder.array(1)", "encoder.array(encoder.null)"):
            with self.subTest(expression=expression), self.assertRaises(LuaError):
                self.lua.execute("return " + expression)


if __name__ == "__main__":
    unittest.main()
