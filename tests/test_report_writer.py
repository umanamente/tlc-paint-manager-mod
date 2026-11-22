"""Atomic publication and bounded cooperative report writes with failure cleanup."""
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
    operations[#operations+1]="open:"..mode
    if mode=="rb" and files[path]==nil then return nil,"missing",2 end
    if mode=="wb" then
        if fail_open then return nil,"open failed" end
        files[path]=""
    end
    local f={path=path,mode=mode,closed=false}
    function f:write(data)
        assert(not self.closed)
        if fail_write then return nil,"write failed" end
        writes[#writes+1]=#data
        files[self.path]=files[self.path]..data
        return self
    end
    function f:flush()
        operations[#operations+1]="flush"
        if fail_flush then return nil,"flush failed" end
        return true
    end
    function f:close()
        operations[#operations+1]="close"
        self.closed=true
        if self.mode=="wb" and fail_close then return nil,"close failed" end
        return true
    end
    return f
end
function fs.remove(path)
    operations[#operations+1]="remove"
    if fail_remove then return nil,"remove failed" end
    assert(files[path]~=nil,"removed an unrelated path")
    files[path]=nil;return true
end
function fs.rename(old,new)
    operations[#operations+1]="rename"
    if fail_rename then return nil,"rename failed" end
    assert(files[new]==nil,"overwrote existing report")
    files[new],files[old]=files[old],nil
    return true
end
function budget(maximum)
    local b={units=0,maximum=maximum or 8}
    function b:expired() return self.units>=self.maximum end
    function b:consume(n) self.units=self.units+(n or 1) end
    return b
end
function begin(value, options)
    options=options or {};options.fs=fs
    return writer.begin("report.json",value,options)
end
function drain(job)
    while job.state=="running" do job:step(budget(8));assert(job.completed<1000000,"writer stalled") end
    return job:get_progress()
end
function count_files() local n=0;for _ in pairs(files) do n=n+1 end;return n end
'''


class ReportWriterTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path; json=require("paintmanager.json");writer=require("paintmanager.report_writer")')
        self.lua.execute(FIXTURE)

    def test_constructor_performs_no_io_and_begin_does_not_walk_input(self):
        self.lua.execute('''
        job=begin({large=string.rep("x",100000)})
        assert(#operations==0 and count_files()==0 and job.encoder.completed==0)
        assert(job:get_result()==nil)
        ''')

    def test_unreadable_destination_or_temporary_is_never_assumed_absent(self):
        for target in ("report.json", "temporary"):
            with self.subTest(target=target):
                self.setUp()
                self.lua.globals().blocked_target = target
                self.lua.execute('''
                job=begin({data=1})
                local blocked=blocked_target=="temporary" and job.temp or blocked_target
                files[blocked]="existing protected bytes"
                local original=fs.open
                fs.open=function(path,mode)
                    if path==blocked and mode=="rb" then return nil,"access denied",13 end
                    return original(path,mode)
                end
                drain(job)
                assert(job.state=="error" and job:get_result()==nil)
                assert(files[blocked]=="existing protected bytes" and #writes==0)
                assert(job.error:find("Cannot inspect report path",1,true))
                ''')

    def test_completed_file_matches_existing_encoder_with_newline(self):
        self.lua.execute(r'''
        local value={modVersion="dev",readOnly=false,data={rows=json.array({{name="Paint",count=4}})}}
        job=begin(value)
        while job.state=="running" do
            assert(files["report.json"]==nil)
            job:step_one()
        end
        assert(job.state=="done" and job:get_result()=="report.json")
        assert(files["report.json"]==json.encode(value).."\n" and count_files()==1)
        assert(job:get_progress().bytesWritten==#files["report.json"])
        ''')

    def test_large_report_streams_bounded_writes_and_small_custom_limits(self):
        self.lua.globals().native_text = ('Paint 🎨 \u6f06' + ''.join(map(chr, range(32)))) * 1000
        for write_bytes in (8192, 17, 1):
            with self.subTest(write_bytes=write_bytes):
                self.lua.execute(rf'''
                files,operations,writes={{}},{{}},{{}}
                job=begin({{text=native_text}},{{write_bytes={write_bytes}}});drain(job)
                assert(job.state=="done" and #writes>1)
                for _,n in ipairs(writes) do assert(n<={write_bytes}) end
                assert(files["report.json"]==json.encode({{text=native_text}}).."\n")
                ''')

    def test_shared_budget_bounds_work_and_checks_between_encoder_microsteps(self):
        self.lua.execute('''
        local value={};for i=1,1000 do value["key"..i]=i end
        job=begin(value);job:step_one();job:step_one()
        local b=budget(3);job:step(b)
        assert(b.units==3 and job.encoder_steps<=96 and job.encoder_steps>32 and job.state=="running")
        local work=0;local original=job.encoder.step_one
        function job.encoder:step_one() work=work+1;return original(self) end
        b=budget(8);function b:expired() return self.units>=8 or work>=3 end
        job:step(b);assert(work==3 and b.units==1 and job.state=="running")
        ''')

    def test_cancel_before_open_does_not_touch_files(self):
        self.lua.execute('''
        job=begin({data=1});job:cancel("closed")
        assert(job.state=="cancelled" and #operations==0 and count_files()==0)
        local n=#operations;job:cancel("again");job:step_one();assert(#operations==n)
        ''')

    def test_production_budget_yields_after_one_microstep_crosses_first_unit_deadline(self):
        self.lua.execute('''
        local work_budget=require("paintmanager.work_budget")
        job=begin({text=string.rep("x",40000)})
        job:step_one();job:step_one()
        assert(job.phase=="encode")
        local now,work=0,0
        local original=job.encoder.step_one
        function job.encoder:step_one()
            work=work+1;now=now+.003
            return original(self)
        end
        local b=work_budget.new(2,8,function() return now end)
        assert(b.units==0 and b.clock_valid)
        job:step(b)
        assert(work==1 and job.encoder_steps==1 and b.units==1 and job.state=="running")
        assert(b:elapsed_ms()>=3 and b:expired())
        ''')

    def test_cancel_after_a_partial_write_removes_only_its_temp(self):
        self.lua.execute('''
        files["unrelated"]="keep"
        job=begin({text=string.rep("x",40000)})
        while job.chunks_written==0 do job:step_one() end
        local temp=job.temp
        assert(files[temp]~=nil and files["report.json"]==nil)
        job:cancel("world changed")
        assert(job.state=="cancelled" and files[temp]==nil and files["unrelated"]=="keep")
        assert(job:get_result()==nil and count_files()==1)
        ''')

    def test_late_invalid_nested_data_leaves_no_final_or_partial_report(self):
        self.lua.execute('''
        job=begin({a=string.rep("x",40000),z=string.char(255)})
        drain(job)
        assert(job.state=="error" and job.chunks_written>0 and count_files()==0)
        assert(job:get_result()==nil and job.error:find("UTF-8",1,true))
        ''')

    def test_all_filesystem_failure_stages_stop_and_clean_up(self):
        for flag in ("fail_open", "fail_write", "fail_flush", "fail_close", "fail_rename"):
            with self.subTest(flag=flag):
                self.setUp()
                self.lua.execute(f'{flag}=true;job=begin({{text="paint"}});drain(job)')
                self.assertEqual(self.lua.eval("job.state"), "error")
                self.assertEqual(self.lua.eval("count_files()"), 0)
                self.assertIsNone(self.lua.eval("job:get_result()"))

    def test_cleanup_failure_is_disclosed_and_never_publishes_success(self):
        self.lua.execute('''
        job=begin({text=string.rep("x",20000)});job:step_one();job:step_one()
        fail_remove=true;job:cancel("closed")
        assert(job.state=="cancelled" and #job:get_progress().cleanupErrors==1)
        assert(files["report.json"]==nil and job:get_result()==nil and count_files()==1)
        ''')

    def test_existing_final_is_never_replaced_even_if_created_before_publication(self):
        self.lua.execute('''
        files["report.json"]="keep"
        job=begin({data=1});drain(job)
        assert(job.state=="error" and files["report.json"]=="keep" and count_files()==1)
        files={};job=begin({data=1})
        while job.phase~="publish" and job.state=="running" do job:step_one() end
        files["report.json"]="arrived late";drain(job)
        assert(job.state=="error" and files["report.json"]=="arrived late" and count_files()==1)
        ''')

    def test_temporary_collision_chooses_a_new_owned_path(self):
        self.lua.execute('''
        job=begin({data=1});local occupied=job.temp;files[occupied]="keep"
        drain(job)
        assert(job.state=="done" and files[occupied]=="keep" and count_files()==2)
        local before=#operations;job:cancel("after success")
        assert(job.state=="done" and #operations==before and files["report.json"]~=nil)
        ''')

    def test_real_filesystem_publishes_a_parseable_completed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            self.lua.globals().output_path = path.as_posix()
            self.lua.execute('job=writer.begin(output_path,{readOnly=true,data={count=4}})')
            while self.lua.eval('job.state=="running"'):
                self.assertFalse(path.exists())
                self.lua.execute('job:step(budget(8))')
            self.assertEqual(self.lua.eval("job.state"), "done")
            self.assertEqual(json.loads(path.read_text()), {"readOnly": True, "data": {"count": 4}})
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
