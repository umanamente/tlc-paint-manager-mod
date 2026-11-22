"""Controller wiring for explicit learning; native grants have separate tests."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS


LEARNING_FIXTURE = r'''
debug.getinfo=function() return {source="@D:/Steam/steamapps/common/Voyage/Voyage/Binaries/Win64/ue4ss/Mods/PaintManager/Scripts/main.lua"} end
local original_open=io.open
io.open=function(path,mode)
    if mode=="r" then
        return {read=function() return '"buildid" "'..(testBuild or "25982825")..'"' end,close=function() return true end}
    end
    return original_open(path,mode)
end
local original_begin=package.loaded["paintmanager.collection"].begin
package.loaded["paintmanager.collection"].begin=function(c)
    local job=original_begin(c)
    local original_step=job.step
    function job:step(budget)
        local result=original_step(self,budget)
        if result.model then
            result.model.stockComplete=not partial
            local row=result.model.rows[1]
            row.recipeState="locked";row.complete=true
            row.runtimeRef={name="paint1",id="paint1@123",className="VoyagePaintItem"}
        end
        return result
    end
    return job
end
learn_jobs,learn_commits,learn_cancelled,learn_resets=0,0,0,0
package.loaded["paintmanager.learn_recipe"]={
    reset_context=function() learn_resets=learn_resets+1 end,
    run_cached=function(c,request,model,emit)
        assert(inGameThread)
        assert(request.itemPath=="paint1" and request.runtimeRef.id=="paint1@123")
        assert(model==ui.model and request.gameBuild=="25982825")
        learn_jobs=learn_jobs+1
        if reenterLearn then reenterLearn() end
        if cachedStockChanged or request.contextToken~=ctx then
            return {state="done",model=model,receipt={attempted=false,outcome="rejected",reasonCode="refresh_required"}}
        end
        learn_commits=learn_commits+1
        local receipt={attempted=true,outcome=learnOutcome or "learned",note=learnNote}
        if receipt.outcome=="learned" then model.rows[1].recipeState="unlocked" end
        emit("result",receipt)
        return {state="done",model=model,receipt=receipt}
    end,
    begin=function(c,request,emit)
        assert(inGameThread)
        assert(request.itemPath=="paint1" and request.runtimeRef.id=="paint1@123")
        learn_jobs=learn_jobs+1
        local job={receipt={readOnly=false,attempted=false,outcome="pending"},steps=0}
        function job:get_receipt() return self.receipt end
        function job:cancel(reason)
            if self.done then return end
            self.cancelled=true;self.receipt.outcome="cancelled";learn_cancelled=learn_cancelled+1
        end
        function job:step(budget)
            assert(inGameThread)
            if budget:expired() then return {state="running",phase="learn_prepare"} end
            budget:consume();self.steps=self.steps+1
            if self.steps<(learnSlices or 2) then return {state="running",phase="learn_prepare",processed=self.steps,total=learnSlices or 2} end
            self.done=true;learn_commits=learn_commits+1
            self.receipt.attempted=true;self.receipt.outcome=learnOutcome or "learned"
            local m=ui.model
            if self.receipt.outcome=="learned" then m.rows[1].recipeState="unlocked" end
            emit("result",self.receipt)
            return {state="done",model=m,receipt=self.receipt}
        end
        return job
    end,
}
local original_ui=package.loaded["paintmanager.ui"].new
package.loaded["paintmanager.ui"].new=function(c,callbacks)
    local result=original_ui(c,callbacks)
    function result:set_learning(active) self.learning=active end
    return result
end
'''


class LearningMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(LEARNING_FIXTURE)

    def run_lua(self, source):
        return self.lua.execute(source)

    def open_window(self):
        self.run_lua('start();registered.F10();step()')

    def test_button_request_finishes_synchronously_without_scan_or_model_publication(self):
        self.open_window()
        self.run_lua('''
            local old_model,old_scans,old_models=ui.model,scans,#models
            local ok,message,terminal=invoke("learn_recipe","paint1")
            assert(ok and terminal and learn_jobs==1 and learn_commits==1)
            assert(ui.model==old_model and scans==old_scans and #models==old_models)
            assert(not ui.learning and ui.status.state=="done" and #written==0)
            assert(not invoke("learn_recipe","paint1"))
            step()
            assert(learn_jobs==1 and learn_commits==1 and ui.learning==false)
            assert(ui.model.rows[1].recipeState=="unlocked")
        ''')
        reports = json.loads(self.run_lua('return json.encode(json.array(written))'))
        report = json.loads(reports[-1]['text'])
        self.assertEqual(report['kind'], 'learn_recipe')
        self.assertFalse(report['readOnly'])
        self.assertEqual(report['data']['outcome'], 'learned')

    def test_unload_cancels_queued_receipts_and_releases_ui_resources(self):
        self.run_lua('''
            ModRef={};local writers={};local released=0
            package.loaded["paintmanager.report_writer"].begin=function()
                local writer={open=true}
                function writer:step() error("Unloaded receipt was pumped") end
                function writer:cancel(reason) self.open=false;self.reason=reason end
                writers[#writers+1]=writer;return writer
            end
            local resources=require("paintmanager.ui_resources")
            local create=resources.new
            resources.new=function(...)
                local bank=create(...);local invalidate=bank.invalidate
                function bank:invalidate(suspend)
                    if suspend then released=released+1 end
                    return invalidate(self,suspend)
                end
                return bank
            end
            start();registered.F10();step()
            function ui:shutdown(reason) self:close(reason);self.shutdown_called=true end
            assert(invoke("learn_recipe","paint1"))
            assert(#writers==1 and writers[1].open and #written==0)
            local before=released
            inGameThread=true;ModRef.OnUnload();inGameThread=false
            assert(not writers[1].open and writers[1].reason=="Mod unloading")
            assert(ui.shutdown_called and not ui.open and released==before+1)
            assert(not require("paintmanager.background").is_busy())
        ''')

    def test_ineligible_rows_and_partial_stock_never_queue(self):
        for edit in ('ui.model.rows[1].count=3', 'ui.model.rows[1].recipeState="unknown"',
                     'ui.model.rows[1].recipeState="unlocked"', 'ui.model.stockComplete=false',
                     'ui.model.rows[1].runtimeRef=nil'):
            with self.subTest(edit=edit):
                self.setUp()
                self.open_window()
                self.run_lua(edit + ';assert(not invoke("learn_recipe","paint1"));step()')
                self.assertEqual(self.lua.eval('learn_jobs+learn_commits'), 0)

    def test_close_after_synchronous_learning_does_not_replay_it(self):
        self.open_window()
        self.run_lua('''
            assert(invoke("learn_recipe","paint1"));registered.F10();step()
            assert(not ui.open and learn_jobs==1 and learn_commits==1)
            registered.F10();step()
            assert(invoke("learn_recipe","paint1"));step()
            assert(learn_commits==2)
        ''')

    def test_stale_cached_stock_or_context_rejects_without_scan(self):
        for action in ('cachedStockChanged=true', 'ctx="worldB/pawnB"'):
            with self.subTest(action=action):
                self.setUp()
                self.open_window()
                self.run_lua('''
                    local old_scans=scans
                ''' + action + '''
                    local ok,message,terminal=invoke("learn_recipe","paint1")
                    assert(not ok and terminal and scans==old_scans)
                    step()
                ''')
                self.assertEqual(self.lua.eval('learn_commits'), 0)
                self.assertEqual(self.lua.eval('learn_cancelled'), 0)
                reports = json.loads(self.run_lua('return json.encode(json.array(written))'))
                self.assertTrue(any(json.loads(r['text'])['kind'] == 'learn_recipe' for r in reports))

    def test_uncertain_result_reports_error_and_releases_buttons(self):
        self.open_window()
        self.run_lua('learnOutcome="unverified";assert(not invoke("learn_recipe","paint1"));step()')
        self.assertFalse(self.lua.eval('ui.learning'))
        self.assertEqual(self.lua.eval('ui.status.state'), 'error')
        self.assertEqual(self.lua.eval('learn_commits'), 1)

    def test_report_failure_after_commit_does_not_start_another_job(self):
        self.open_window()
        self.run_lua('writeError=true;assert(invoke("learn_recipe","paint1"));step();step()')
        self.assertEqual(self.lua.eval('learn_commits'), 1)
        self.assertFalse(self.lua.eval('ui.learning'))

    def test_unverified_game_build_cannot_queue_a_grant(self):
        self.run_lua('testBuild="different"')
        self.open_window()
        self.run_lua('assert(not invoke("learn_recipe","paint1"));step()')
        self.assertEqual(self.lua.eval('learn_jobs+learn_commits'), 0)

    def test_learning_control_failure_cannot_start_grant(self):
        self.open_window()
        self.run_lua('''
            function ui:set_learning(active) if active then error("control failure") end end
            assert(not invoke("learn_recipe","paint1"));step()
            assert(learn_jobs==0 and learn_commits==0)
        ''')

    def test_queue_log_failure_does_not_reject_an_accepted_learning_request(self):
        self.open_window()
        self.run_lua('''
            failLogOnce=true
            assert(invoke("learn_recipe","paint1"));step()
            assert(learn_jobs==1 and learn_commits==1)
        ''')

    def test_reentrant_pump_and_second_click_cannot_duplicate_single_grant(self):
        self.open_window()
        self.run_lua('''
            reenterLearn=function()
                pump()
                assert(not cb.learn_recipe("paint1"))
            end
            assert(invoke("learn_recipe","paint1"))
            assert(learn_jobs==1 and learn_commits==1 and not ui.learning)
            assert(not invoke("enqueue","learn_recipe"))
        ''')


if __name__ == '__main__':
    unittest.main()
