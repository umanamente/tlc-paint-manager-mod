"""Real streaming writer integration with controller cancellation and receipts."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS
from test_learning_main import LEARNING_FIXTURE
from test_report_writer import FIXTURE as FILESYSTEM_FIXTURE
from test_bulk_main import BULK_MAIN_FIXTURE


INTEGRATION = r'''
package.loaded["paintmanager.report_writer"]=nil
local actual=require("paintmanager.report_writer")
writers={}
package.loaded["paintmanager.report_writer"]={begin=function(path,value,options)
    options=options or {};options.fs=fs;options.write_bytes=128;options.microsteps_per_unit=2
    local job=actual.begin(path,value,options)
    writers[#writers+1]=job
    return job
end}
local original=package.loaded["paintmanager.collection"].begin
package.loaded["paintmanager.collection"].begin=function(c)
    local job=original(c);local step=job.step
    function job:step(budget)
        local progress=step(self,budget)
        if progress.model then progress.model.note=string.rep("x",50000) end
        return progress
    end
    return job
end
function pump_one()
    inGameThread=true;local ok,err=pcall(pump);inGameThread=false;assert(ok,err)
end
function until_condition(predicate)
    for _=1,5000 do
        if predicate() then return end
        pump_one()
    end
    error("controller did not reach expected state")
end
function final_count()
    local count=0
    for path in pairs(files) do if path:sub(-5)==".json" then count=count+1 end end
    return count
end
function final_text()
    for path,value in pairs(files) do if path:sub(-5)==".json" then return value end end
end
function partial_report()
    return writers[1] and writers[1].bytes_written>0 and writers[1].state=="running"
end
function finish_writer()
    until_condition(function() return writers[1] and writers[1].state~="running" end)
end
'''


class ReportMainTests(unittest.TestCase):
    def setUp(self, extra=""):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(LEARNING_FIXTURE)
        if extra:
            self.lua.execute(extra)
        self.lua.execute(FILESYSTEM_FIXTURE)
        self.lua.execute(INTEGRATION)
        self.lua.execute('start();registered.F10();step()')

    def test_snapshot_report_yields_and_publishes_only_the_complete_document(self):
        self.lua.execute('''
        assert(#operations==0 and #writers==0)
        assert(invoke("enqueue","snapshot"));assert(#operations==0)
        until_condition(partial_report)
        assert(final_count()==0 and ui.open)
        finish_writer();pump_one()
        assert(final_count()==1 and writers[1].chunks_written>1)
        for _,size in ipairs(writes) do assert(size<=128) end
        assert(ui.status.state=="done" and ui.status.path:sub(-5)==".json")
        ''')
        report = json.loads(self.lua.eval("final_text()"))
        self.assertEqual(report["kind"], "snapshot")
        self.assertTrue(report["readOnly"])
        self.assertEqual(len(report["data"]["uiCollection"]["note"]), 50000)

    def test_closing_cancels_an_unfinished_read_only_report_and_removes_its_temp(self):
        self.lua.execute('''
        assert(invoke("enqueue","snapshot"));until_condition(partial_report)
        local temp=writers[1].temp
        registered.F10();step()
        assert(not ui.open and writers[1].state=="cancelled")
        assert(files[temp]==nil and count_files()==0 and final_count()==0)
        for _=1,30 do pump_one() end
        assert(final_count()==0)
        ''')

    def test_world_change_drops_partial_ordinary_report(self):
        self.lua.execute('''
        assert(invoke("enqueue","snapshot"));until_condition(partial_report)
        ctx="worldB/pawnB";pump_one()
        assert(not ui.open and writers[1].state=="cancelled")
        assert(count_files()==0 and final_count()==0)
        ''')

    def test_rejected_cached_learning_receipt_finishes_after_window_and_context_loss(self):
        self.lua.execute('''
        cachedStockChanged=true
        assert(not invoke("learn_recipe","paint1"));step()
        assert(learn_commits==0)
        registered.F10();pump_one();ctx="worldB/pawnB"
        until_condition(function() return final_count()==1 end)
        assert(not ui.open and learn_commits==0 and #writers==1)
        assert(count_files()==1 and writers[1].state=="done")
        ''')
        report = json.loads(self.lua.eval("final_text()"))
        self.assertEqual(report["kind"], "learn_recipe")
        self.assertFalse(report["readOnly"])
        self.assertEqual(report["data"]["outcome"], "rejected")

    def test_completed_grant_receipt_continues_the_same_writer_after_close(self):
        self.lua.execute('''
        learnNote=string.rep("r",50000)
        assert(invoke("learn_recipe","paint1"));until_condition(partial_report)
        local existing=writers[1]
        assert(learn_commits==1 and final_count()==0)
        registered.F10();pump_one();ctx="worldB/pawnB"
        until_condition(function() return final_count()==1 end)
        assert(not ui.open and learn_commits==1 and #writers==1 and writers[1]==existing)
        assert(count_files()==1)
        ''')
        report = json.loads(self.lua.eval("final_text()"))
        self.assertEqual(report["data"]["outcome"], "learned")
        self.assertTrue(report["data"]["attempted"])
        self.assertEqual(len(report["data"]["note"]), 50000)

    def test_streaming_write_failure_after_grant_cleans_up_without_repeating_it(self):
        self.lua.execute('''
        fail_write=true
        assert(invoke("learn_recipe","paint1"))
        until_condition(function() return writers[1] and writers[1].state=="error" end)
        for _=1,20 do pump_one() end
        assert(learn_commits==1 and learn_jobs==1 and not ui.learning)
        assert(final_count()==0 and count_files()==0)
        assert(ui.status.state=="done")
        ''')

    def test_completed_bulk_keeps_success_status_when_closed_during_report_output(self):
        self.setUp(BULK_MAIN_FIXTURE + '''
        local original=package.loaded["paintmanager.learn_all"].begin
        package.loaded["paintmanager.learn_all"].begin=function(...)
            local job=original(...);job.receipt.note=string.rep("b",50000);return job
        end
        ''')
        self.lua.execute('''
        bulk_total=1;start_bulk();until_condition(partial_report)
        assert(bulk_steps==1 and final_count()==0)
        closeFromTick=true;pump_one()
        assert(not ui.open and ui.status.command=="learn_all" and ui.status.state=="done")
        until_condition(function() return final_count()==1 end)
        assert(bulk_steps==1 and #writers==1)
        ''')
        report = json.loads(self.lua.eval("final_text()"))
        self.assertEqual(report["data"]["outcome"], "completed")
        self.assertEqual(report["data"]["counts"]["learned"], 1)


if __name__ == "__main__":
    unittest.main()
