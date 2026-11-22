"""Bulk learning shares a capture and budgets each guarded native recipe job."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE


BULK_FIXTURE = r'''
bulk=require("paintmanager.learn_all")
learning=require("paintmanager.learn_recipe")
nativeCalls=0;granted={};records={};scenePaints={};emitted={}
function scene(count)
    for i=1,count do
        local id=string.format("DA_Paint_%03d",i)
        local p=paint(id,"Paint "..i)
        scenePaints[i]=p;records[i]=entry(p,4)
    end
    actor,component=box("Stock",100,records)
end
function vault:AddItem(item,data,stack,notify)
    nativeCalls=nativeCalls+1;granted[#granted+1]=item.path
    assert(data.ItemCount==1 and data.Timestamp==0 and data.Flags==0 and stack and notify)
    if grantClockAdvance then fakeTime=fakeTime+grantClockAdvance end
    if not noMembershipChange then self.members[item.path]=true end
    if changeContextOnGrant then pawn.address=pawn.address+1 end
    if throwOnGrant then error("grant exception") end
    return 0
end
function new_bulk(options)
    request={contextToken=collection.context(),radiusCm=config.ship_radius_cm,gameBuild=config.reference_build}
    job=bulk.begin(config,request,function(event,data) emitted[#emitted+1]={event=event,data=data} end,options)
end
function bulk_step(units)
    local before=nativeCalls
    local b=budget_module.new(10000,units or 1)
    result=job:step(b)
    assert(nativeCalls-before<=1,"more than one grant in one batch step")
    assert(b.units<=(units or 1),"budget double charged")
    assert(no_native_handles(job),"native handles retained")
    if result.state=="running" then assert(result.model==nil,"partial model published") end
    return result
end
function bulk_until(phase)
    for _=1,10000 do
        if job.phase==phase then return end
        assert(job.state=="running","ended before "..phase)
        bulk_step()
    end
    error("phase not reached")
end
function bulk_drain()
    for _=1,10000 do
        result=bulk_step()
        if result.state~="running" then return result end
    end
    error("bulk never finished")
end
'''


class LearnAllTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)
        self.lua.execute(BULK_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def value(self, expression):
        return self.lua.eval(expression)

    def receipt(self):
        return json.loads(self.run_lua('return json.encode(job:get_receipt())'))

    def test_begin_and_expired_budget_do_not_scan_or_grant(self):
        self.run_lua('''
            scene(3);request={contextToken=collection.context(),radiusCm=config.ship_radius_cm,gameBuild=config.reference_build}
            local before=scans+inventory_reads+queries+context_reads
            job=bulk.begin(config,request)
            assert(scans+inventory_reads+queries+context_reads==before)
            local b=budget_module.new(1,1);b:consume();result=job:step(b)
            assert(scans+inventory_reads+queries+context_reads==before and nativeCalls==0)
        ''')
        self.assertEqual(self.value('result.state'), 'running')

    def test_box_only_cans_removed_by_membership_callback_cannot_authorize_bulk_grant(self):
        self.run_lua('''
            scene(1);new_bulk();bulk_until("bulk_start_recipe")
            local before=scans
            function vault:HasItem(item)
                records[1].Data.ItemCount=0
                return self.members[item.path]==true
            end
            bulk_drain()
            local receipt=job:get_receipt()
            assert(receipt.counts.learned==0 and not receipt.attempted and nativeCalls==0)
            assert(scans==before and not vault.members[scenePaints[1].path])
        ''')

    def test_many_paints_share_one_capture_and_have_one_grant_per_step(self):
        self.run_lua('scene(12);new_bulk();result=bulk_drain()')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'completed')
        self.assertEqual(receipt['counts']['learned'], 12)
        self.assertEqual(receipt['counts']['processed'], 12)
        self.assertEqual(self.value('scans'), 2)
        self.assertEqual(self.value('vault_reads'), 1)
        self.assertEqual(self.value('nativeCalls'), 12)
        self.assertTrue(self.value('no_native_handles(result.model)'))
        self.assertEqual(self.value('result.processed'), self.value('result.total'))
        self.assertEqual(self.value('result.model.physicalCount'), 48)
        self.assertTrue(self.value('records[1].Data.ItemCount==4 and records[12].Data.ItemCount==4'))
        self.assertTrue(receipt['attempted'])
        self.assertEqual(receipt['physicalPolicy'], 'keep')
        self.assertFalse(receipt['readOnly'])

    def test_candidate_preparation_is_incremental_and_does_not_grant(self):
        self.run_lua('scene(6);new_bulk();bulk_until("bulk_prepare");bulk_step()')
        self.assertEqual(self.value('#job.candidates'), 1)
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertEqual(self.value('job.rowIndex'), 2)

    def test_only_locked_complete_threshold_rows_are_selected(self):
        self.run_lua('''
            scene(5);records[1].Data.ItemCount=3;vault.members[scenePaints[2].path]=true
            new_bulk();bulk_until("bulk_prepare")
            job.model.rows[3].recipeState="unknown";job.model.rows[4].complete=false
            result=bulk_drain()
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['counts']['eligible'], 1)
        self.assertEqual(receipt['counts']['learned'], 1)
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_no_available_paints_finishes_without_native_attempts(self):
        self.run_lua('scene(2);records[1].Data.ItemCount=3;records[2].Data.ItemCount=0;new_bulk();result=bulk_drain()')
        self.assertEqual(self.receipt()['outcome'], 'none_available')
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertFalse(self.receipt()['attempted'])

    def test_partial_stock_rejects_whole_batch_before_any_grant(self):
        self.run_lua('''
            scene(2);new_bulk();bulk_until("bulk_prepare")
            job.model.stockComplete=false
            result=bulk_drain()
        ''')
        # A real capture with partial stock is rejected at publication; changes
        # after publication must still be refused by the individual live job.
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertTrue(all(r['outcome'] == 'rejected' for r in self.receipt()['results']))

    def test_real_partial_capture_never_prepares_native_grants(self):
        self.run_lua('scene(2);config.max_slots=1;new_bulk();result=bulk_drain()')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertEqual(self.receipt()['counts']['processed'], 0)

    def test_duplicate_rows_do_not_duplicate_recipe_jobs(self):
        self.run_lua('''
            scene(2);new_bulk();bulk_until("bulk_prepare")
            job.model.rows[#job.model.rows+1]=job.model.rows[1]
            result=bulk_drain()
        ''')
        self.assertEqual(self.receipt()['counts']['eligible'], 2)
        self.assertEqual(self.value('nativeCalls'), 2)

    def test_stock_changes_after_selection_reject_only_the_affected_paint(self):
        self.run_lua('''
            scene(3);new_bulk();bulk_until("bulk_start_recipe")
            records[1].Data.ItemCount=3;result=bulk_drain()
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['counts']['rejected'], 1)
        self.assertEqual(receipt['counts']['learned'], 2)
        self.assertEqual(self.value('nativeCalls'), 2)
        self.assertEqual(receipt['outcome'], 'completed')

    def test_recipe_learned_elsewhere_is_skipped_without_a_second_write(self):
        self.run_lua('''
            scene(2);new_bulk();bulk_until("bulk_start_recipe")
            vault.members[scenePaints[1].path]=true;result=bulk_drain()
        ''')
        self.assertEqual(self.receipt()['counts']['already_known'], 1)
        self.assertEqual(self.receipt()['counts']['learned'], 1)
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_uncertain_native_result_stops_later_recipes_and_does_not_retry(self):
        self.run_lua('scene(4);noMembershipChange=true;new_bulk();result=bulk_drain();bulk_step()')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'stopped_unverified')
        self.assertEqual(receipt['counts']['unverified'], 1)
        self.assertEqual(receipt['remaining'], 3)
        self.assertEqual(self.value('nativeCalls'), 1)
        self.assertTrue(receipt['attempted'])
        self.assertTrue(receipt['partial'])

    def test_native_exception_after_success_is_verified_and_batch_continues(self):
        self.run_lua('scene(2);throwOnGrant=true;new_bulk();result=bulk_drain()')
        self.assertEqual(self.receipt()['counts']['learned'], 2)
        self.assertEqual(self.value('nativeCalls'), 2)

    def test_cancel_before_first_step_does_not_touch_native_state(self):
        self.run_lua('scene(3);new_bulk();local before=scans+inventory_reads+queries;result=job:cancel("close");assert(scans+inventory_reads+queries==before)')
        self.assertEqual(self.value('result.state'), 'cancelled')
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertIsNone(self.value('result.model'))

    def test_cancel_mid_batch_keeps_completed_grants_and_receipts(self):
        self.run_lua('''
            scene(4);new_bulk()
            for _=1,10000 do
                bulk_step();if nativeCalls==1 then break end
            end
            result=job:cancel("window closed");bulk_step()
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'cancelled')
        self.assertEqual(receipt['counts']['learned'], 1)
        self.assertEqual(receipt['remaining'], 3)
        self.assertTrue(receipt['attempted'])
        self.assertEqual(self.value('nativeCalls'), 1)
        self.assertIsNone(self.value('result.model'))

    def test_cancel_active_child_records_it_once_and_never_calls_additem(self):
        self.run_lua('scene(2);new_bulk();bulk_until("bulk_recipe");result=job:cancel("close");bulk_step()')
        receipt = self.receipt()
        self.assertEqual(receipt['counts']['cancelled'], 1)
        self.assertEqual(len(receipt['results']), 1)
        self.assertEqual(self.value('nativeCalls'), 0)
        self.assertFalse(receipt['attempted'])

    def test_context_radius_and_unverified_build_cannot_grant(self):
        for change in ('pawn.address=pawn.address+1', 'config.ship_radius_cm=5000', 'job.request.gameBuild="unknown"'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('scene(2);new_bulk();bulk_until("bulk_start_recipe");' + change + ';result=bulk_drain()')
                self.assertEqual(self.value('nativeCalls'), 0)
                self.assertNotEqual(self.receipt()['outcome'], 'completed')

    def test_log_failure_does_not_interrupt_native_membership_postcheck(self):
        self.run_lua('''
            scene(2);new_bulk()
            job.emit=function() error("logger failed") end
            result=bulk_drain()
        ''')
        self.assertEqual(self.receipt()['counts']['learned'], 2)
        self.assertTrue(self.receipt()['loggingErrors'])

    def test_child_receipts_reference_shared_capture_without_repeating_it(self):
        self.run_lua('scene(2);new_bulk();result=bulk_drain()')
        receipt = self.receipt()
        self.assertIn('capture', receipt['preflight'])
        for result in receipt['results']:
            child = result['receipt']
            self.assertNotIn('capture', child['preflight'])
            self.assertEqual(child['preflight']['captureReference'], 'batch.preflight.capture')
            for event in child['events']:
                if event['event'] == 'preflight_complete':
                    self.assertNotIn('capture', event['data'])

    def test_scheduler_callbacks_are_distinct_from_budget_work_steps(self):
        self.run_lua('''
            scene(1);new_bulk()
            job:note_scheduler_callback(1,100,{mode="native_time_budget"})
            bulk_step();bulk_step()
            job:note_scheduler_callback(1,100,{mode="native_time_budget"})
            job:note_scheduler_callback(2,101,{mode="one_recipe_per_frame"})
            bulk_step()
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['metrics']['schedulerCallbacks'], 2)
        self.assertEqual(receipt['metrics']['workSteps'], 3)
        self.assertEqual(receipt['scheduler']['frameCount'], 101)

    def test_custom_native_clock_is_forwarded_to_child_and_records_overrun(self):
        self.run_lua('''
            scene(1);fakeTime=0
            new_bulk({clock_ms=function() return fakeTime end,clock_source="test native clock"})
            bulk_until("bulk_recipe");bulk_step()
            assert(job.child.phase=="learn_commit")
            grantClockAdvance=5
            result=job:step(budget_module.new(2,8,function() return fakeTime/1000 end))
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['outcome'], 'completed')
        self.assertEqual(receipt['clockSource'], 'test native clock')
        self.assertGreaterEqual(receipt['metrics']['maxSliceMs'], 5)
        self.assertGreaterEqual(receipt['metrics']['overruns'], 1)
        self.assertEqual(receipt['results'][0]['receipt']['callDurationMs'], 5)

    def test_invalid_clock_does_not_invent_elapsed_metrics_or_stop_count_progress(self):
        self.run_lua('''
            scene(2);new_bulk({clock_ms=function() return nil end,clock_source="unavailable native clock"})
            result=bulk_drain()
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['counts']['learned'], 2)
        self.assertFalse(receipt['metrics']['clockValid'])
        self.assertNotIn('workMs', receipt['metrics'])
        self.assertNotIn('durationMs', receipt)

    def test_child_limits_are_frozen_for_the_whole_batch_but_radius_guard_is_live(self):
        self.run_lua('''
            scene(2);new_bulk()
            local slots,catalog=config.max_slots,config.max_catalog
            config.max_slots=999999;config.max_catalog=999999
            bulk_until("bulk_recipe")
            assert(job.child.config.max_slots==slots and job.child.config.max_catalog==catalog)
            assert(job.child.runtimeConfig==config)
            result=bulk_drain()
        ''')
        self.assertEqual(self.receipt()['counts']['learned'], 2)

    def test_receipt_retains_displayed_selection_count_separately_from_fresh_queue(self):
        self.run_lua('''
            scene(2)
            request={contextToken=collection.context(),radiusCm=config.ship_radius_cm,
                gameBuild=config.reference_build,displayedEligibleCount=7}
            job=bulk.begin(config,request)
            result=bulk_drain()
        ''')
        receipt = self.receipt()
        self.assertEqual(receipt['request']['displayedEligibleCount'], 7)
        self.assertEqual(receipt['counts']['eligible'], 2)


if __name__ == '__main__':
    unittest.main()
