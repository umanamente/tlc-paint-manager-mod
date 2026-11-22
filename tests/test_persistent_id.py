"""Lossless native UInt64 bit patterns through the actual Lua 5.4 codec."""
import unittest

from lupa.lua54 import LuaRuntime

from test_local_library import SCRIPTS


class PersistentIdTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path; ids=require("paintmanager.persistent_id")')

    def test_every_boundary_keeps_exact_native_bits(self):
        values = (0, 1, 2**53-1, 2**53, 2**53+1, 2**63-1, 2**63, 2**63+1, 2**64-1)
        for unsigned in values:
            signed = unsigned if unsigned < 2**63 else unsigned - 2**64
            with self.subTest(value=unsigned):
                encoded = self.lua.globals().ids.encode(signed)
                self.assertEqual(encoded, f"{unsigned:016x}")
                self.assertEqual(self.lua.globals().ids.decode(encoded), signed)
                self.assertEqual(self.lua.globals().ids.is_valid(encoded), unsigned != 0)

    def test_rejects_lossy_floats_noncanonical_and_runtime_id_text(self):
        self.lua.execute('''
        for _,value in ipairs({1.0, 9007199254740992.0, "123", false, {}}) do
            assert(not pcall(ids.encode,value))
        end
        for _,value in ipairs({"1", "FFFFFFFFFFFFFFFF", "0xffffffffffffffff",
            "000000000000000g", "12345678901234567", "/Game/Map.Box", "0x1234", false}) do
            assert(not ids.is_valid(value) and not pcall(ids.decode,value))
        end
        assert(not ids.is_valid("0000000000000000"))
        ''')


if __name__ == "__main__":
    unittest.main()
