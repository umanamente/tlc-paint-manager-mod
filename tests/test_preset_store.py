"""Persistent preset validation, safe replacement, recovery and cooperative I/O."""
import json
from pathlib import Path
import tempfile
import unittest

from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
files,operations,writes={},{},{}
fs={}
function fs.open(path,mode)
    operations[#operations+1]="open:"..mode..":"..path
    if denied then return nil,"permission denied",13 end
    if mode=="rb" and files[path]==nil then return nil,"missing",2 end
    if mode=="wb" then
        if fail_open then return nil,"open failed",13 end
        files[path]=""
    end
    local f={path=path,mode=mode,pos=1,closed=false}
    function f:read(n)
        assert(n<=8192)
        if fail_read then return nil,"read failed" end
        if self.pos>#files[self.path] then return nil end
        local data=files[self.path]:sub(self.pos,self.pos+n-1);self.pos=self.pos+#data;return data
    end
    function f:write(data)
        assert(not self.closed)
        if fail_write then return nil,"write failed" end
        writes[#writes+1]=#data;files[self.path]=files[self.path]..data;return self
    end
    function f:flush() if fail_flush then return nil,"flush failed" end;return true end
    function f:close()
        self.closed=true
        if self.mode=="wb" and fail_close then return nil,"close failed" end
        return true
    end
    return f
end
function fs.remove(path)
    if fail_remove then return nil,"remove failed" end
    assert(files[path]~=nil);files[path]=nil;return true
end
function fs.rename(old,new)
    operations[#operations+1]="rename:"..old..":"..new
    if fail_rename or fail_publish and new=="presets.json" and old~="presets.json.backup" then return nil,"rename failed" end
    assert(files[new]==nil,"overwrote existing path");assert(files[old]~=nil)
    files[new],files[old]=files[old],nil;return true
end
function budget(n)
    local b={units=0};function b:expired() return self.units>=n end
    function b:consume(x) self.units=self.units+x end;return b
end
function drain(job)
    while job.state=="running" do job:step(budget(8));assert(job.completed<200000) end
    return job:get_progress()
end
function preset(name,id)
    return {id=id or "preset-1",name=name or "My character",createdUtc="2026-11-17T12:00:00Z",gameBuild="25982825",
        target={kind="character",className="/Script/Voyage.VoyageCharacter",paintSetName="Character",displayName="Character"},
        zones=json.array({{materialSlotName="Body",partName="Body",displayName="Body",paint={assetType="Item",assetName="DA_Paint_Canada",displayName="Last Leaf"}},
            {materialSlotName="Head",partName="Head",paint=json.null}})}
end
function library() return {schemaVersion=1,presets=json.array({preset()})} end
function count_files() local n=0;for _ in pairs(files) do n=n+1 end;return n end
'''


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path;json=require("paintmanager.json");store=require("paintmanager.preset_store")')
        self.lua.execute(FIXTURE)

    def test_validation_returns_a_canonical_copy_without_io(self):
        self.lua.execute('''
        input=library();job=store.begin_validate(input)
        assert(#operations==0 and job.completed==0 and job:get_result()==nil)
        drain(job);assert(job.state=="done" and #operations==0)
        assert(json.encode(job:get_result())==json.encode(input) and job:get_result()~=input)
        input.presets[1].zones[1].paint.assetName="Changed"
        assert(job:get_result().presets[1].zones[1].paint.assetName=="DA_Paint_Canada")
        ''')

    def test_invalid_identity_unknown_fields_duplicate_ids_names_and_zones_rejected(self):
        edits = ['x.schemaVersion=2', 'x.presets[1].address="0x123"', 'x.presets[1].target.className="/Game/Boat:PersistentLevel.Boat_1"',
                 'x.presets[1].zones[1].slotIndex=0', 'x.presets[1].zones[1].paint.assetName="../../evil"',
                 'x.presets[1].zones[1].paint=nil', 'x.presets[1].zones[1].materialSlotName="";x.presets[1].zones[1].partName=""',
                 'x.presets[2]=preset("Different")', 'x.presets[2]=preset("MY CHARACTER","other-id")',
                 'x.presets[1].zones[2]=x.presets[1].zones[1]', 'x.presets[1].zones={}',
                 'x.presets[1].zones[5]=x.presets[1].zones[1]', 'x.presets[1].gameBuild="unknown"']
        for edit in edits:
            with self.subTest(edit=edit):
                self.lua.execute('x=library();' + edit + ';job=store.begin_validate(x);drain(job)')
                self.assertEqual(self.lua.eval('job.state'), 'error')
                self.assertIsNone(self.lua.eval('job:get_result()'))

    def test_missing_library_returns_empty_without_writing(self):
        self.lua.execute('''
        job=store.begin_load("presets.json",{fs=fs});assert(#operations==0);drain(job)
        assert(job.state=="done" and job.missing and #job:get_result().presets==0 and count_files()==0)
        ''')

    def test_denied_missing_does_not_silently_become_empty(self):
        self.lua.execute('denied=true;job=store.begin_load("presets.json",{fs=fs});drain(job);assert(job.state=="error" and job:get_result()==nil)')

    def test_save_load_roundtrip_and_replacement_keep_backup(self):
        self.lua.execute('''
        local old=library();files["presets.json"]=json.encode(old)
        local next=library();next.presets[1].name="Updated"
        job=store.begin_save("presets.json",next,{fs=fs});assert(#operations==0)
        while job.state=="running" do
            if job.phase~="publish" then assert(files["presets.json"]==json.encode(old)) end
            job:step_one()
        end
        assert(job.state=="done" and files["presets.json.backup"]==json.encode(old))
        assert(files["presets.json"]==json.encode(next).."\\n" and count_files()==2)
        job=store.begin_load("presets.json",{fs=fs});drain(job)
        assert(job.state=="done" and job:get_result().presets[1].name=="Updated")
        ''')

    def test_invalid_existing_file_is_never_replaced(self):
        for content in ('{"schemaVersion":2,"presets":[]}', '{"schemaVersion":1,"presets":{}}', 'return 1', '{"schemaVersion":1,"schemaVersion":1,"presets":[]}'):
            with self.subTest(content=content):
                self.setUp()
                self.lua.globals().invalid_content = content
                self.lua.execute('files["presets.json"]=invalid_content;job=store.begin_save("presets.json",library(),{fs=fs});drain(job);assert(job.state=="error" and files["presets.json"]==invalid_content and count_files()==1)')

    def test_backup_recovery_only_when_primary_is_missing(self):
        self.lua.execute('''
        files["presets.json.backup"]=json.encode(library())
        job=store.begin_load("presets.json",{fs=fs});drain(job)
        assert(job.state=="done" and job.recovered_backup and #job:get_result().presets==1 and files["presets.json"]==nil)
        files["presets.json"]="invalid"
        job=store.begin_load("presets.json",{fs=fs});drain(job);assert(job.state=="error")
        ''')

    def test_cancellation_after_backup_move_restores_primary(self):
        self.lua.execute('''
        local original=json.encode(library());files["presets.json"]=original
        job=store.begin_save("presets.json",library(),{fs=fs})
        while not job.old_moved and job.state=="running" do job:step_one() end
        assert(job.old_moved and files["presets.json"]==nil)
        job:cancel("closed")
        assert(job.state=="cancelled" and files["presets.json"]==original and count_files()==1 and job:get_result()==nil)
        ''')

    def test_failed_publication_restores_previous_library(self):
        self.lua.execute('''
        local original=json.encode(library());files["presets.json"]=original;fail_publish=true
        job=store.begin_save("presets.json",library(),{fs=fs});drain(job)
        assert(job.state=="error" and files["presets.json"]==original and count_files()==1)
        ''')

    def test_failed_restore_preserves_backup_for_next_load(self):
        self.lua.execute('''
        local original=json.encode(library());files["presets.json"]=original
        job=store.begin_save("presets.json",library(),{fs=fs})
        while not job.old_moved and job.state=="running" do job:step_one() end
        fail_rename=true;job:cancel("closed")
        assert(files["presets.json"]==nil and files["presets.json.backup"]==original)
        assert(#job:get_progress().cleanupErrors>0 and count_files()==1)
        fail_rename=false;job=store.begin_load("presets.json",{fs=fs});drain(job)
        assert(job.state=="done" and job.recovered_backup)
        ''')

    def test_rollback_never_overwrites_a_new_primary(self):
        self.lua.execute('''
        local original=json.encode(library());files["presets.json"]=original
        job=store.begin_save("presets.json",library(),{fs=fs})
        while not job.old_moved and job.state=="running" do job:step_one() end
        files["presets.json"]="external arrival";job:cancel("closed")
        assert(files["presets.json"]=="external arrival" and files["presets.json.backup"]==original)
        assert(#job:get_progress().cleanupErrors>0 and count_files()==2)
        ''')

    def test_multiple_updates_rotate_backup_and_recovered_save_keeps_it(self):
        self.lua.execute('''
        local original=json.encode(library());files["presets.json.backup"]=original
        local updated=library();updated.presets[1].name="Updated"
        job=store.begin_save("presets.json",updated,{fs=fs});drain(job)
        assert(job.state=="done" and files["presets.json.backup"]==original)
        local previous=files["presets.json"];updated.presets[1].name="Third"
        job=store.begin_save("presets.json",updated,{fs=fs});drain(job)
        assert(job.state=="done" and files["presets.json.backup"]==previous and count_files()==2)
        ''')

    def test_oversized_canonical_output_is_rejected_before_any_io(self):
        self.lua.execute('''
        job=store.begin_save("presets.json",library(),{fs=fs,max_bytes=100})
        drain(job);assert(job.state=="error" and #operations==0 and count_files()==0)
        ''')

    def test_cancel_after_partial_write_removes_only_owned_pending_data(self):
        self.lua.execute('''
        local original=json.encode(library());files["presets.json"]=original;files["other"]="keep"
        job=store.begin_save("presets.json",library(),{fs=fs,write_bytes=17})
        while (#writes==0) and job.state=="running" do job:step_one() end
        assert(job.phase=="write" and #writes>0)
        job:cancel("closed")
        assert(job.state=="cancelled" and files["presets.json"]==original and files["other"]=="keep" and count_files()==2)
        ''')

    def test_production_deadline_stops_before_the_next_parser_primitive(self):
        self.lua.execute('''
        files["presets.json"]=string.rep(" ",20000)..json.encode(library())
        job=store.begin_load("presets.json",{fs=fs})
        while job.phase~="parse" and job.state=="running" do job:step_one() end
        local now=0;local original=job.child.step_one
        function job.child:step_one() now=now+.003;return original(self) end
        local budget=require("paintmanager.work_budget").new(2,8,function() return now end)
        job:step(budget)
        assert(budget.units==1 and job.child.completed==1 and job.child.index==257 and job.state=="running")
        ''')

    def test_write_failures_leave_primary_intact_and_no_pending_file(self):
        for flag in ('fail_open', 'fail_write', 'fail_flush', 'fail_close', 'fail_rename'):
            with self.subTest(flag=flag):
                self.setUp()
                self.lua.execute(f'''
                local original=json.encode(library());files["presets.json"]=original;{flag}=true
                job=store.begin_save("presets.json",library(),{{fs=fs}});drain(job)
                assert(job.state=="error" and files["presets.json"]==original and count_files()==1)
                ''')

    def test_external_edit_during_save_is_preserved(self):
        self.lua.execute('''
        files["presets.json"]=json.encode(library())
        job=store.begin_save("presets.json",library(),{fs=fs})
        while job.phase~="recheck_existing" and job.state=="running" do job:step_one() end
        local changed=library();changed.presets[1].name="External edit";local bytes=json.encode(changed)
        files["presets.json"]=bytes;drain(job)
        assert(job.state=="error" and files["presets.json"]==bytes and count_files()==1)
        ''')

    def test_limits_and_shared_budget_are_respected(self):
        self.lua.execute('''
        job=store.begin_validate(library(),{max_zones=1});drain(job);assert(job.state=="error")
        files["presets.json"]=string.rep(" ",9000)
        job=store.begin_load("presets.json",{fs=fs,max_bytes=8192})
        local b=budget(2);job:step(b);assert(b.units==2 and job.completed==2 and job.state=="running")
        drain(job);assert(job.state=="error")
        ''')

    def test_real_filesystem_roundtrip_survives_new_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presets.json"
            self.lua.globals().output_path = path.as_posix()
            self.lua.execute('job=store.begin_save(output_path,library());drain(job);assert(job.state=="done")')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['presets'][0]['name'], 'My character')
            self.setUp()
            self.lua.globals().output_path = path.as_posix()
            self.lua.execute('job=store.begin_load(output_path);drain(job);assert(job.state=="done" and job:get_result().presets[1].name=="My character")')


if __name__ == "__main__":
    unittest.main()
