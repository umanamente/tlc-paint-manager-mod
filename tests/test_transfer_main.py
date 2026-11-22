"""Exercise paint transfer requests through the window, scheduler and stock refresh."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_learning_main import LEARNING_FIXTURE
from test_main_ui import FIXTURE, SCRIPTS


TRANSFER_FIXTURE = r'''
bagCount, borrowed, transferJobs, transferSteps, transferCancels, bagReads = 0,0,0,0,0,0
package.loaded["paintmanager.transfer_ledger"] = {new=function(token)
    return {
        load=function() assert(inGameThread); return true end,
        entries=function() return {} end,
        reconcile=function(self, slots) assert(slots); return true end,
    }
end}
package.loaded["paintmanager.paint_transfer"] = {
    reset_context=function() end,
    backpack=function(c, token)
        assert(inGameThread and ctx==token)
        bagReads=bagReads+1
        return {slots={}, counts=bagCount>0 and {paint1=bagCount} or {}}
    end,
    returnable=function() return borrowed>0 and {paint1=borrowed} or {} end,
    begin=function(c, request)
        transferJobs=transferJobs+1
        lastQuantity=request.quantity
        local job={receipt={state="running", moved=0, skipped=0}}
        function job:get_receipt() return self.receipt end
        function job:get_stock_changes() return observedBoxes end
        function job:cancel()
            transferCancels=transferCancels+1
            self.receipt.state="cancelled"
        end
        function job:step(budget)
            assert(inGameThread and not budget:expired())
            local ok,err=request.guard()
            assert(ok,err)
            budget:consume(1)
            transferSteps=transferSteps+1
            fixture_wall_ms=fixture_wall_ms+(costPerTransferStep or 0)
            if onTransferStep then onTransferStep() end
            if simulatedWork and transferSteps < simulatedWork then return self.receipt end
            if holdTransfer then return self.receipt end
            local amount=request.itemPath and 1 or (request.direction=="take" and 4 or borrowed)
            if request.direction=="take" and request.itemPath then
                amount=request.quantity=="all" and 4 or math.min(4,math.max(0,request.quantity-bagCount))
            end
            if request.direction=="take" then
                bagCount,borrowed=bagCount+amount,borrowed+amount
            else
                bagCount,borrowed=bagCount-amount,borrowed-amount
            end
            self.receipt={state="done",outcome="completed",moved=amount,skipped=0,attempted=true}
            return self.receipt
        end
        return job
    end,
}
local baseUI=package.loaded["paintmanager.ui"].new
package.loaded["paintmanager.ui"].new=function(c, callbacks)
    local u=baseUI(c,callbacks)
    function u:set_transfer_state(state) assert(inGameThread); self.transfer_state=state end
    local toggle=u.toggle
    function u:toggle(...)
        local open,err=toggle(self,...)
        if not open then self.transfer_state=nil end
        return open,err
    end
    return u
end
function open_transfers()
    start();registered.F10();step();step()
    assert(ui.open)
end
'''


class TransferMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + LEARNING_FIXTURE + TRANSFER_FIXTURE)

    def test_independent_ui_and_capture_counts_follow_external_moves_and_unreadable_bag(self):
        self.lua.execute('''
        inGameThread=true
        local controls=require("paintmanager.transfer_controls").new(config)
        local active={status="captured",contextToken=ctx,sourceCount=1,physicalCount=2,
            backpackCounts={},rows={{id="paint1",count=2}}}
        local window={model={},set_transfer_state=function() end,
            update_backpack_counts=function(self,token,counts) self.model.backpackCounts=counts or false end}
        local budget={expired=function() return false end,consume=function() end}
        controls:refresh(window,ctx,active,0,budget);assert(not controls.needs_capture)
        bagCount=2;controls:refresh(window,ctx,active,5000,budget)
        assert(controls.needs_capture and active.backpackCounts.paint1==2 and window.model.backpackCounts.paint1==2)
        controls.needs_capture=nil;controls:refresh(window,ctx,active,10000,budget);assert(not controls.needs_capture)
        local transfer=package.loaded["paintmanager.paint_transfer"]
        local read=transfer.backpack;transfer.backpack=function() return nil,"unreadable" end
        controls:refresh(window,ctx,active,15000,budget)
        assert(active.backpackCounts==false and window.model.backpackCounts==false and not controls.needs_capture)
        transfer.backpack=read;controls:refresh(window,ctx,active,20000,budget);assert(not controls.needs_capture)
        bagCount=0;controls:refresh(window,ctx,active,25000,budget)
        assert(controls.needs_capture and active.backpackPhysicalCount==0 and window.model.backpackCounts.paint1==nil)
        controls.needs_capture=nil;active.sourceCount=0;active.rows={};bagCount=4
        controls:refresh(window,ctx,active,30000,budget);assert(controls.needs_capture)
        controls.needs_capture=nil;controls:refresh(window,ctx,active,35000,budget);assert(not controls.needs_capture)
        ''')

    def test_single_and_bulk_use_separate_admission_and_refresh_stock(self):
        self.lua.execute('''
            bagCount=2
            open_transfers()
            assert(ui.transfer_state.canGatherAll and not ui.transfer_state.canReturnAll)
            local before=scans
            assert(not invoke("transfer_paint","gather_paint","unknown"))
            assert(invoke("transfer_paint","gather_paint","paint1"))
            assert(not invoke("transfer_paint","gather_paint","paint1"))
            step();step()
            assert(lastQuantity==5 and bagCount==5 and borrowed==3 and scans==before+1 and transferJobs==1)
            assert(ui.transfer_state.returnable.paint1==3 and ui.status.command=="gather_paint")
            assert(not invoke("transfer_paint","gather_paint","paint1"))
            assert(invoke("transfer_paint","gather_all"))
            step();step()
            assert(borrowed==7 and ui.transfer_state.backpack.paint1==9 and lastQuantity==nil)
            assert(invoke("transfer_paint","return_paint","paint1"))
            step();step()
            assert(borrowed==6 and transferJobs==3 and not ui.learning and lastQuantity==nil)
        ''')
        last = json.loads(self.lua.eval("written[#written].text"))
        self.assertEqual(last["kind"], "return_paint")
        self.assertFalse(last["readOnly"])
        self.assertEqual(last["data"]["moved"], 1)

    def test_return_from_signal_lost_survives_reopen_and_does_not_scan_ship(self):
        self.lua.execute('''
            fixtureConnected=false;bagCount=3;borrowed=2;open_transfers()
            assert(ui.transfer_state.canReturnAll and not ui.transfer_state.canGatherAll)
            reopen_window();step()
            assert(ui.transfer_state and ui.transfer_state.canReturnAll)
            assert(not invoke("transfer_paint","gather_all"))
            assert(invoke("transfer_paint","return_all"))
            step();step()
            assert(scans==0 and bagCount==1 and borrowed==0 and transferJobs==1)
            assert(not ui.transfer_state.canReturnAll and not invoke("transfer_paint","return_all"))
        ''')

    def test_proven_stock_updates_the_open_list_and_allows_an_immediate_followup(self):
        self.lua.execute('''
            package.loaded["paintmanager.transfer_stock"]={reconcile=function(model,changes,scope,token)
                assert(changes==observedBoxes and changes and token==model.contextToken)
                local updated={}
                for k,v in pairs(model) do updated[k]=v end
                updated.physicalCount=4-borrowed
                updated.rows={{id="paint1",count=4-borrowed}}
                return updated
            end}
            bagCount=2;observedBoxes={};open_transfers()
            local before=scans
            assert(invoke("transfer_paint","gather_paint","paint1"))
            assert(ui.status.state=="done" and ui.model.rows[1].count==1)
            step()
            assert(scans==before and ui.transfer_state.canReturnAll)
            assert(invoke("transfer_paint","return_paint","paint1"))
            assert(ui.model.rows[1].count==2 and ui.status.state=="done")
            step()
            assert(scans==before and borrowed==2)
        ''')
        receipt = json.loads(self.lua.eval("written[#written].text"))
        self.assertEqual(receipt["data"]["timing"]["stockRefresh"], "changed_boxes")

    def test_close_cancels_and_current_build_gates_requests(self):
        self.lua.execute('''
            open_transfers()
            holdTransfer=true
            assert(invoke("transfer_paint","gather_all"))
            step()
            registered.F10();step()
            assert(not ui.open and transferCancels==1 and bagCount==0)
        ''')
        self.setUp()
        self.lua.execute('''
            testBuild="unsupported";open_transfers()
            assert(not invoke("transfer_paint","gather_all") and transferJobs==0 and bagReads==0)
        ''')

    def test_open_window_observes_external_backpack_changes(self):
        self.lua.execute('''
            open_transfers()
            bagCount=5
            fixture_elapsed_ms=800
            for i=1,12 do step() end
            assert(ui.transfer_state.backpack.paint1==5 and not ui.transfer_state.canReturnAll)
            assert(not invoke("transfer_paint","gather_paint","paint1"))
        ''')

    def test_selected_take_uses_current_saved_choice_with_the_existing_stock_view(self):
        self.lua.execute('''
            bagCount=2
            open_transfers()
            local doc=require("paintmanager.state_document").new()
            local function select_quantity(value)
                doc.sections.settings.data.paintTransferQuantity=value
                inGameThread=true
                assert(fixture_preferences:load(doc))
                inGameThread=false
            end
            select_quantity(1)
            assert(not invoke("transfer_paint","gather_paint","paint1"))
            select_quantity(3)
            assert(invoke("transfer_paint","gather_paint","paint1"))
            step();step()
            assert(bagCount==3 and borrowed==1 and lastQuantity==3)
            select_quantity("all")
            assert(invoke("transfer_paint","gather_paint","paint1"))
            step();step()
            assert(bagCount==7 and lastQuantity=="all")
        ''')

    def test_quarantine_is_released_once_per_real_load_generation(self):
        self.lua.execute('''
            local resets=0
            package.loaded["paintmanager.paint_transfer"].reset_context=function() resets=resets+1 end
            local controls=require("paintmanager.transfer_controls").new(config)
            controls:observe_load(4)
            controls:reset()
            controls:observe_load(4)
            controls:observe_load(nil)
            assert(resets==1)
            controls:observe_load(5)
            assert(resets==2)
        ''')

    def test_small_transfer_finishes_in_button_handler_without_querying_frame_or_waiting(self):
        self.lua.execute('''
            require("paintmanager.clock").frame_count=function() error("Transfer waited for a frame") end
            open_transfers()
            simulatedWork=16
            local ok,msg,handled=invoke("transfer_paint","gather_all")
            assert(ok and handled and transferSteps==16 and borrowed==4)
            assert(ui.status.state=="done" and not ui.learning)
            step();step();assert(transferSteps==16)
        ''')

    def test_time_bound_returns_to_input_and_immediately_continues_on_next_callback(self):
        self.lua.execute('''
            require("paintmanager.clock").frame_count=function() error("Transfer waited for a new frame") end
            open_transfers()
            costPerTransferStep=30;simulatedWork=5
            assert(invoke("transfer_paint","gather_all"))
            assert(transferSteps==2 and ui.learning and ui.status.state=="running")
            inGameThread=true;pump();assert(transferSteps==4 and ui.learning)
            pump();inGameThread=false
            assert(transferSteps==5 and borrowed==4 and not ui.learning)
        ''')

    def test_action_burst_blocks_reentry_and_observes_close_between_native_steps(self):
        self.lua.execute('''
            open_transfers();simulatedWork=100
            function onTransferStep()
                assert(not cb.transfer_paint("gather_all"))
                if transferSteps==3 then registered.F10() end
            end
            assert(invoke("transfer_paint","gather_all"))
            assert(transferSteps==3 and transferJobs==1 and borrowed==0)
            step();assert(not ui.open and transferJobs==1 and borrowed==0)
        ''')
