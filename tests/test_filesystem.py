"""Actual host Lua I/O behavior; injected filesystem tests cover faults elsewhere."""
from pathlib import Path
import os
import tempfile
import unittest
from lupa.lua54 import LuaRuntime

SCRIPTS=Path(__file__).resolve().parents[1]/"src/PaintManager/Scripts"

class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts=SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts.."/?.lua;"..package.path;fs=require("paintmanager.filesystem")')

    def test_existing_temporary_file_is_never_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/"keep.json"
            target.write_bytes(b"existing")
            self.lua.globals().target=target.as_posix()
            self.lua.execute('local file,err=fs.open(target,"wb");assert(file==nil and err)')
            self.assertEqual(target.read_bytes(),b"existing")

    @unittest.skipUnless(os.name=="nt","Windows CRT no-replace contract")
    def test_actual_windows_rename_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"source.json"
            target=Path(directory)/"destination.json"
            source.write_bytes(b"new");target.write_bytes(b"original")
            self.lua.globals().source=source.as_posix();self.lua.globals().target=target.as_posix()
            self.lua.execute('local ok,err=fs.rename(source,target);assert(not ok and err)')
            self.assertEqual(source.read_bytes(),b"new")
            self.assertEqual(target.read_bytes(),b"original")

    def test_binary_roundtrip_and_checked_close(self):
        with tempfile.TemporaryDirectory() as directory:
            self.lua.globals().target=(Path(directory)/"roundtrip.json").as_posix()
            self.lua.execute('''
            local f=assert(fs.open(target,"wb"));assert(f:write("paint\\0\\255\\r\\n"));assert(f:flush());assert(f:close())
            f=assert(fs.open(target,"rb"));assert(f:read("a")=="paint\\0\\255\\r\\n");assert(f:close())
            assert(fs.remove(target))
            ''')

    @unittest.skipUnless(os.name=="nt","Unqualified Windows narrow path encoding")
    def test_non_ascii_path_is_unavailable_not_misreported_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            parent=Path(directory)/"\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430-\u8def\u5f84"
            parent.mkdir();target=parent/"existing.json";target.write_bytes(b"retained")
            self.lua.globals().target=target.as_posix()
            self.lua.execute('''
            local file,why,code=fs.open(target,"rb")
            assert(file==nil and code==95 and why:find("Non-ASCII",1,true))
            local removed,reason=fs.remove(target);assert(not removed and reason)
            ''')
            self.assertEqual(target.read_bytes(),b"retained")

if __name__=="__main__":unittest.main()
