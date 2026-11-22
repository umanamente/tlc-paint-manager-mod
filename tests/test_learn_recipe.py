"""Explicit recipe-grant jobs with real collection/proof and mocked native calls."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE


LEARN_FIXTURE = r'''
learning=require("paintmanager.learn_recipe")
p=paint("DA_Paint_Canada","Last leaf")
stockRecords={entry(p,4)}
a,c=box("First",1,stockRecords)
nativeCalls,nativeResult=0,0
events={}
function vault:AddItem(item,data,stacking,notify)
    nativeCalls=nativeCalls+1
    assert(item==p and data.ItemCount==1 and data.Timestamp==0 and data.Flags==0)
    assert(stacking==true and notify==true)
    lastData={ItemCount=data.ItemCount,Timestamp=data.Timestamp,Flags=data.Flags}
    if mutateBeforeError or not noMembershipChange then self.members[item.path]=true end
    if changeDuringAdd then world.address=world.address+1 end
    if errorDuringAdd then error("native grant threw") end
    return nativeResult
end
function request_for_paint()
    local captured=collection.scan(config)
    local row
    for _,r in ipairs(captured.rows) do if r.id==p.path then row=r end end
    assert(row)
    return {itemPath=row.id,runtimeRef=row.runtimeRef,contextToken=captured.contextToken,
        radiusCm=captured.radiusCm,displayName=row.displayName,gameBuild=config.reference_build}
end
function new_job(emitter)
    request=request_for_paint()
    job=learning.begin(config,request,emitter or function(event,data) events[#events+1]={event=event,data=data} end)
    return job
end
function learning_step()
    result=job:step(budget_module.new(10000,1))
    return result
end
function learning_until(phase)
    for _=1,10000 do
        if job.phase==phase then return end
        assert(job.state=="running","learning ended before phase "..phase)
        learning_step()
    end
    error("learning phase not reached")
end
function learning_drain()
    for _=1,10000 do
        result=learning_step()
        assert(no_native_handles(job),"native handle retained in learning job")
        if result.state~="running" then return result end
    end
    error("learning did not terminate")
end
'''


class LearnRecipeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)
        self.lua.execute(LEARN_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def value(self, expression):
        return self.lua.eval(expression)

    def receipt(self):
        return json.loads(self.run_lua('return json.encode(job:get_receipt())'))

    def test_begin_and_exhausted_budget_do_not_do_native_work(self):
        self.run_lua('''
            request=request_for_paint();local before=scans+queries+inventory_reads+context_reads
            job=learning.begin(config,request)
            assert(scans+queries+inventory_reads+context_reads==before)
            local b=budget_module.new(1,1);b:consume();result=job:step(b)
            assert(scans+queries+inventory_reads+context_reads==before)
        ''')
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertEqual(self.value('result.state'), 'running')

    def test_four_cans_grant_once_preserve_stock_and_verify_slot_zero(self):
        self.run_lua('new_job();result=learning_drain();learning_step();learning_step()')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'learned')
        self.assertTrue(receipt['attempted'])
        self.assertFalse(receipt['readOnly'])
        self.assertEqual(receipt['physicalPolicy'], 'keep')
        self.assertEqual(receipt['callReturn'], 0)
        self.assertFalse(receipt['beforeHasItem'])
        self.assertTrue(receipt['afterHasItem'])
        self.assertEqual(receipt['proof']['count'], 4)
        self.assertEqual(self.value('nativeCalls'), 1)
        self.assertEqual(self.value('stockRecords[1].Data.ItemCount'), 4)
        self.assertEqual(self.value('result.model.rows[1].recipeState'), 'unlocked')
        self.assertFalse(receipt['saveRequestedByMod'])

    def test_negative_native_result_does_not_override_verified_membership(self):
        self.run_lua('nativeResult=-1;new_job();result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'learned')
        self.assertEqual(self.receipt()['callReturn'], -1)
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_threshold_three_is_rejected_and_split_four_is_accepted(self):
        self.run_lua('stockRecords[1].Data.ItemCount=3;new_job();result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertFalse(self.receipt()['attempted'])
        self.assertEqual(self.value('nativeCalls'), 0)
        self.setUp()
        self.run_lua('''
            stockRecords[1].Data.ItemCount=1
            for i=2,4 do box("Other"..i,i,{entry(p,1)}) end
            new_job();result=learning_drain()
        ''')
        self.assertEqual(self.receipt()['outcome'], 'learned')
        self.assertEqual(len(self.receipt()['proof']['witnesses']), 4)
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_native_exception_after_success_is_observed_and_not_retried(self):
        self.run_lua('errorDuringAdd=true;mutateBeforeError=true;new_job();result=learning_drain();learning_step()')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'learned')
        self.assertFalse(receipt['callReturned'])
        self.assertIn('native grant threw', receipt['callException'])
        self.assertTrue(receipt['afterHasItem'])
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_unverified_false_membership_blocks_later_attempts_in_same_context(self):
        self.run_lua('''
            noMembershipChange=true;new_job();result=learning_drain()
            firstReceipt=job:get_receipt();new_job();result=learning_drain()
        ''')
        first = json.loads(self.run_lua('return json.encode(firstReceipt)'))
        self.assertEqual(first['outcome'], 'unverified')
        self.assertFalse(first['afterHasItem'])
        self.assertEqual(self.value('nativeCalls'), 1)
        self.assertEqual(self.receipt()['outcome'], 'unverified')
        self.assertTrue(self.receipt()['blockedByPreviousAttempt'])
        self.assertFalse(self.receipt()['attempted'])

    def test_native_exception_without_change_blocks_retry(self):
        self.run_lua('''
            noMembershipChange=true;errorDuringAdd=true
            new_job();result=learning_drain();firstReceipt=job:get_receipt()
            errorDuringAdd=false;new_job();result=learning_drain()
        ''')
        self.assertEqual(self.value('firstReceipt.outcome'), 'unverified')
        self.assertEqual(self.value('firstReceipt.afterHasItem'), False)
        self.assertEqual(self.value('nativeCalls'), 1)
        self.assertTrue(self.receipt()['blockedByPreviousAttempt'])

    def test_unknown_postquery_blocks_retry_and_preserves_unknown(self):
        self.run_lua('''
            originalHas=vault.HasItem
            function vault:HasItem(item)
                if nativeCalls>0 then return nil end
                return originalHas(self,item)
            end
            new_job();result=learning_drain()
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'unverified')
        self.assertNotIn('afterHasItem', receipt)
        self.assertIn('HasItem did not return bool', receipt['afterError'])
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_logging_exceptions_never_skip_postquery(self):
        self.run_lua('new_job(function() error("log unavailable") end);result=learning_drain()')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'learned')
        self.assertTrue(receipt['afterHasItem'])
        self.assertGreater(len(receipt['loggingErrors']), 0)
        self.assertIn('membership_after', [e['event'] for e in receipt['events']])

    def test_fresh_already_unlocked_skips_native_attempt(self):
        self.run_lua('new_job();vault.members[p.path]=true;result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'already_known')
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertFalse(self.receipt()['attempted'])

    def test_recipe_unlocked_just_before_commit_skips_native_attempt(self):
        self.run_lua('new_job();learning_until("learn_commit");vault.members[p.path]=true;result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'already_known')
        self.assertTrue(self.receipt()['beforeHasItem'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_unknown_precommit_membership_rejects_without_attempt(self):
        self.run_lua('new_job();learning_until("learn_commit");vault.HasItem=function() return nil end;result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertFalse(self.receipt()['attempted'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_stock_falls_below_four_before_commit(self):
        self.run_lua('new_job();learning_until("learn_commit");stockRecords[1].Data.ItemCount=3;result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertIn('Fewer than four', self.receipt()['reason'])

    def test_witness_identity_world_radius_and_unknown_item_reject_before_write(self):
        for change in ('c.address=c.address+1',
                       'function a:GetWorld() return obj("World Other","World") end',
                       'function a:K2_GetActorLocation() return {X=20000,Y=0,Z=0} end',
                       'stockRecords[1].Item=wrap(nil)'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('new_job();learning_until("learn_commit");' + change + ';result=learning_drain()')
                self.assertEqual(self.receipt()['outcome'], 'rejected')
                self.assertFalse(self.receipt()['attempted'])
                self.assertEqual(self.value('nativeCalls'), 0)

    def test_paint_identity_change_after_click_rejects(self):
        self.run_lua('new_job();p.address=p.address+1;result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_incomplete_fresh_stock_rejects_before_native_attempt(self):
        self.run_lua('new_job();c.Items.ForEach=function() error("inventory read failed") end;result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertFalse(self.receipt()['preflight']['stockComplete'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_name_only_stock_estimate_cannot_authorize_native_grant(self):
        self.run_lua('''
            new_job();stockRecords[1].Item=wrap(nil)
            result=learning_drain()
        ''')
        self.assertEqual(self.receipt()['preflight']['count'], 4)
        self.assertTrue(self.receipt()['preflight']['stockComplete'])
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertFalse(self.receipt()['attempted'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_cancel_and_radius_change_before_commit_never_write(self):
        self.run_lua('new_job();learning_until("learn_commit");result=job:cancel("window closed");learning_step()')
        self.assertEqual(self.value('result.state'), 'cancelled')
        self.assertEqual(self.receipt()['outcome'], 'cancelled')
        self.assertEqual(self.value('nativeCalls'), 0)
        self.setUp()
        self.run_lua('new_job();learning_until("learn_commit");config.ship_radius_cm=5000;result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_context_changes_during_membership_query_prevent_native_write(self):
        self.run_lua('''
            new_job();learning_until("learn_commit")
            vault.HasItem=function() world.address=world.address+1;return false end
            result=learning_drain()
        ''')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_context_changes_inside_native_call_become_unverified(self):
        self.run_lua('changeDuringAdd=true;new_job();result=learning_drain()')
        self.assertEqual(self.receipt()['outcome'], 'unverified')
        self.assertTrue(self.receipt()['attempted'])
        self.assertNotIn('afterHasItem', self.receipt())
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_new_context_can_make_a_new_explicit_attempt(self):
        self.run_lua('''
            noMembershipChange=true;new_job();result=learning_drain()
            world.address=world.address+1;noMembershipChange=false
            new_job();result=learning_drain()
        ''')
        self.assertEqual(self.value('nativeCalls'), 2)
        self.assertEqual(self.receipt()['outcome'], 'learned')

    def test_confirmed_context_reset_clears_ledger(self):
        self.run_lua('''
            noMembershipChange=true;new_job();result=learning_drain()
            learning.reset_context();noMembershipChange=false
            new_job();result=learning_drain()
        ''')
        self.assertEqual(self.value('nativeCalls'), 2)
        self.assertEqual(self.receipt()['outcome'], 'learned')

    def test_same_context_asset_recreation_does_not_bypass_attempt_ledger(self):
        self.run_lua('''
            noMembershipChange=true;new_job();result=learning_drain()
            p.address=p.address+1;noMembershipChange=false
            new_job();result=learning_drain()
        ''')
        self.assertEqual(self.value('nativeCalls'), 1)
        self.assertTrue(self.receipt()['blockedByPreviousAttempt'])

    def test_config_caps_are_frozen_and_displayed_count_is_retained(self):
        self.run_lua('''
            request=request_for_paint();request.displayedCount=4
            job=learning.begin(config,request)
            local originalSlots=config.max_slots;local originalCatalog=config.max_catalog
            config.max_slots=999999;config.max_catalog=999999
            assert(job.config.max_slots==originalSlots and job.config.max_catalog==originalCatalog)
            result=learning_drain()
        ''')
        self.assertEqual(self.receipt()['request']['displayedCount'], 4)
        self.assertEqual(self.receipt()['outcome'], 'learned')

    def test_preflight_does_not_double_charge_shared_work_units(self):
        self.run_lua('''
            new_job();local b=budget_module.new(10000,1)
            result=job:step(b);assert(b.units==1)
        ''')
        self.assertEqual(self.value('result.state'), 'running')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_final_commit_overrun_is_disclosed(self):
        self.run_lua('''
            new_job();learning_until("learn_commit")
            fakeClock=0;os.clock=function() return fakeClock end
            local originalAdd=vault.AddItem
            vault.AddItem=function(self,...) fakeClock=fakeClock+0.025;return originalAdd(self,...) end
            result=job:step(budget_module.new(2,8))
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'learned')
        self.assertGreaterEqual(receipt['metrics']['maxSliceMs'], 25)
        self.assertGreaterEqual(receipt['callDurationMs'], 25)
        self.assertTrue(receipt['callClockValid'])
        self.assertGreaterEqual(receipt['metrics']['overruns'], 1)
        self.assertEqual(receipt['metrics']['uninterruptibleStages'][-1]['maxWitnesses'], 4)

    def test_final_commit_exception_remains_explicit_when_clock_unavailable(self):
        self.run_lua('''
            new_job();learning_until("learn_commit")
            os.clock=function() error("clock unavailable") end
            result=learning_drain()
        ''')
        stage = self.receipt()['metrics']['uninterruptibleStages'][-1]
        self.assertFalse(stage['clockValid'])
        self.assertNotIn('elapsedMs', stage)
        self.assertIn('AddItem', stage['operation'])
        self.assertEqual(self.receipt()['outcome'], 'learned')


if __name__ == '__main__':
    unittest.main()
