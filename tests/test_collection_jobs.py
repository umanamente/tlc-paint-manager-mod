"""Cooperative collection jobs: native reads, lifetimes, cancellation and budgets."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
JOB_FIXTURE = r'''
budget_module=require("paintmanager.work_budget")
context_reads=0
local original_find_first=FindFirstOf
FindFirstOf=function(kind) context_reads=context_reads+1;return original_find_first(kind) end
function one_step()
    result=job:step(budget_module.new(10000,1))
    return result
end
function until_phase(phase)
    for _=1,10000 do
        if job.phase==phase then return end
        assert(job.state=="running","job ended before requested phase: "..phase)
        one_step()
    end
    error("phase not reached: "..phase)
end
function drain()
    for _=1,10000 do
        result=one_step()
        if result.state~="running" then return result end
        assert(result.model==nil,"partial model was exposed")
    end
    error("job failed to terminate")
end
function no_native_handles(value,seen)
    if type(value)~="table" then return type(value)~="userdata" end
    if rawget(value,"IsValid") or rawget(value,"GetAddress") then return false end
    seen=seen or {};if seen[value] then return true end;seen[value]=true
    for k,v in pairs(value) do
        if not no_native_handles(k,seen) or not no_native_handles(v,seen) then return false end
    end
    return true
end
'''


class CollectionJobTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def value(self, expression):
        return self.lua.eval(expression)

    def model(self):
        return json.loads(self.run_lua('return json.encode(result.model)'))

    def test_begin_does_no_native_work_and_one_context_unit_does_not_scan(self):
        self.run_lua('job=collection.begin(config)')
        self.assertEqual(self.value('scans+queries+inventory_reads+context_reads'), 0)
        self.run_lua('result=one_step()')
        self.assertEqual(self.value('result.state'), 'running')
        self.assertEqual(self.value('result.phase'), 'recipe_library_read')
        self.assertEqual(self.value('scans'), 0)
        self.assertIsNone(self.value('result.model'))

    def test_expired_shared_budget_does_no_native_work(self):
        self.run_lua('job=collection.begin(config);local b=budget_module.new(1,1);b:consume();result=job:step(b)')
        self.assertEqual(self.value('context_reads+scans+queries+inventory_reads'), 0)
        self.assertEqual(self.value('result.processed'), 0)
        self.assertEqual(self.value('result.state'), 'running')

    def test_container_progress_follows_inventory_reads_including_empty_boxes(self):
        self.run_lua('''
            local p=paint("DA_Paint_A","A")
            box("Empty",1,{})
            box("Full",2,{entry(p,4),entry(p,1)})
            box("Distant",config.ship_radius_cm+1,{})
            job=collection.begin(config,{presentation="paints"})
            until_phase("source_filter")
            for _=1,1000 do
                if job.phase=="container_read" then break end
                local p=job:get_progress().presentation
                assert(p.phase=="source_discovery" and p.total==nil and inventory_reads==0)
                one_step()
            end
            assert(job.phase=="container_read")
            for _=1,1000 do
                if job.state~="running" then break end
                local p=job:get_progress().presentation
                assert(p.unit=="containers" and p.total==2 and p.completed==inventory_reads)
                one_step()
            end
            assert(job.state=="done" and inventory_reads==2 and result.model.physicalCount==5)
        ''')

    def test_empty_discovery_and_unknown_discovery_have_distinct_totals(self):
        for discovery, expected_total in (('', 0), ('config.max_discovery_objects=1;box("One",1,{});box("Two",2,{})', None)):
            with self.subTest(expected_total=expected_total):
                self.setUp()
                self.run_lua(discovery)
                self.run_lua('''
                    job=collection.begin(config,{presentation="paints"})
                    until_phase("finalize")
                    progress=job:get_progress().presentation
                    assert(progress.unit=="containers" and progress.completed==inventory_reads)
                ''')
                self.assertEqual(self.value('progress.total'), expected_total)

    def test_capped_scan_counts_all_eligible_boxes_and_only_successful_reads(self):
        self.run_lua('''
            config.max_sources=2
            local _,lost=box("Lost",1,{})
            box("Empty",2,{})
            box("BeyondReadLimit",3,{})
            job=collection.begin(config,{presentation="paints"})
            until_phase("container_read")
            lost.address=lost.address+1
            until_phase("finalize")
            local p=job:get_progress().presentation
            assert(p.unit=="containers" and p.completed==1 and p.total==3)
            result=drain()
            assert(result.state=="done" and not result.model.stockComplete and inventory_reads==1)
        ''')

    def test_catalog_discovery_and_per_paint_queries_are_separate_units(self):
        self.run_lua('paint("DA_Paint_A","A");paint("DA_Paint_B","B");job=collection.begin(config);until_phase("catalog_metadata")')
        self.assertEqual(self.value('scans'), 1)
        self.assertEqual(self.value('queries'), 0)
        self.assertEqual(self.value('job.phase'), 'catalog_metadata')
        self.run_lua('one_step()')
        self.assertEqual(self.value('queries'), 1)
        self.assertEqual(self.value('scans'), 1)
        self.run_lua('one_step()')
        self.assertEqual(self.value('queries'), 2)
        self.assertEqual(self.value('job.phase'), 'source_discovery')

    def test_work_unit_cap_bounds_each_step_and_only_done_exposes_model(self):
        self.run_lua('''
            local p=paint("DA_Paint_A","A")
            for i=1,20 do box("Box"..i,i,{entry(p,1)}) end
            job=collection.begin(config)
            for _=1,1000 do
                local b=budget_module.new(10000,2)
                result=job:step(b)
                assert(b.units<=2)
                if result.state=="done" then break end
                assert(result.state=="running" and result.model==nil)
            end
        ''')
        model = self.model()
        self.assertEqual(model['physicalCount'], 20)
        self.assertEqual(model['includedSourceCount'], 20)
        self.assertEqual(self.value('result.processed'), self.value('result.total'))
        self.assertGreater(model['capture']['slices'], 1)

    def test_all_retained_job_state_is_plain_after_every_phase(self):
        self.run_lua('''
            local p=paint("DA_Paint_A","A")
            box("One",1,{entry(p,4),entry(nil,2,"DA_Paint_Unknown")})
            job=collection.begin(config)
            for _=1,1000 do
                assert(no_native_handles(job),"native handle retained before step")
                result=one_step()
                assert(no_native_handles(job),"native handle retained after step")
                if result.state~="running" then break end
            end
        ''')
        self.assertEqual(self.value('result.state'), 'done')
        self.assertEqual(self.value('mutations'), 0)

    def test_cancel_before_first_step_never_scans_or_returns_partial_model(self):
        self.run_lua('job=collection.begin(config);result=job:cancel("closed");result=one_step()')
        self.assertEqual(self.value('result.state'), 'cancelled')
        self.assertIsNone(self.value('result.model'))
        self.assertEqual(self.value('context_reads+scans+queries+inventory_reads'), 0)

    def test_radius_change_cancels_before_next_global_scan(self):
        self.run_lua('job=collection.begin(config);one_step();config.ship_radius_cm=config.ship_radius_cm+100;result=one_step()')
        self.assertEqual(self.value('result.state'), 'cancelled')
        self.assertIn('Radius changed', self.value('result.error'))
        self.assertEqual(self.value('scans'), 0)
        self.assertIsNone(self.value('result.model'))

    def test_world_or_pawn_change_cancels_without_a_mixed_result(self):
        for change in ('world.address=world.address+100', 'pawn.address=pawn.address+100', 'controller.Pawn=nil'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('job=collection.begin(config);one_step();' + change + ';result=one_step()')
                self.assertEqual(self.value('result.state'), 'cancelled')
                self.assertEqual(self.value('scans'), 0)
                self.assertIsNone(self.value('result.model'))
                self.assertEqual(self.value('job.model.physicalCount'), 0)

    def test_source_address_is_revalidated_before_inventory_read(self):
        self.run_lua('''
            a,c=box("One",1,{entry(paint("DA_Paint_A","A"),4)})
            job=collection.begin(config);until_phase("container_read")
            c.address=c.address+100;result=drain()
        ''')
        model = self.model()
        self.assertEqual(self.value('inventory_reads'), 0)
        self.assertEqual(model['physicalCount'], 0)
        self.assertFalse(model['complete'])
        self.assertIn('identity/class changed', model['sources'][0]['errors'][0])
        self.assertEqual(len(model['capture']['uninterruptibleStages']), 3)

    def test_component_class_changes_are_rejected_after_discovery(self):
        self.run_lua('''
            a,c=box("One",1,{entry(paint("DA_Paint_A","A"),4)})
            job=collection.begin(config);until_phase("source_filter")
            c.kind="UnexpectedComponent";result=drain()
        ''')
        model = self.model()
        self.assertEqual(model['sourceCount'], 0)
        self.assertEqual(model['physicalCount'], 0)
        self.assertFalse(model['complete'])
        self.assertEqual(self.value('inventory_reads'), 0)

    def test_descriptor_with_unreadable_class_is_excluded_explicitly(self):
        self.run_lua('''
            a,c=box("One",1,{entry(paint("DA_Paint_A","A"),4)})
            function c:GetClass() return nil end
            job=collection.begin(config);result=drain()
        ''')
        model = self.model()
        self.assertEqual(model['sourceCount'], 0)
        self.assertFalse(model['complete'])
        self.assertEqual(self.value('inventory_reads'), 0)

    def test_source_owner_class_world_and_radius_are_revalidated(self):
        for change in (
            'a.class_path="BlueprintGeneratedClass /Game/Custom/Box.Box_C"',
            'function a:GetWorld() return obj("World Other","World") end',
            'function a:K2_GetActorLocation() return {X=999999,Y=0,Z=0} end',
        ):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('a,c=box("One",1,{entry(paint("DA_Paint_A","A"),4)});job=collection.begin(config);until_phase("container_read");' + change + ';result=drain()')
                model = self.model()
                self.assertEqual(self.value('inventory_reads'), 0)
                self.assertEqual(model['physicalCount'], 0)
                self.assertFalse(model['complete'])

    def test_paint_discovered_in_a_box_gets_a_later_metadata_query_unit(self):
        self.run_lua('''
            local p=paint("DA_Paint_A","Actual name")
            box("One",1,{entry(p,4)});paints={}
            job=collection.begin(config);until_phase("container_read");one_step()
        ''')
        self.assertEqual(self.value('inventory_reads'), 1)
        self.assertEqual(self.value('queries'), 0)
        self.assertEqual(self.value('job.phase'), 'container_paint_metadata')
        self.assertEqual(self.value('job.model.physicalCount'), 0)
        self.run_lua('one_step()')
        self.assertEqual(self.value('queries'), 1)
        self.run_lua('result=drain()')
        self.assertEqual(self.model()['physicalCount'], 4)
        self.assertEqual(self.model()['rows'][0]['displayName'], 'Actual name')

    def test_metadata_disappearance_keeps_observed_native_stock_with_unknown_recipe(self):
        self.run_lua('''
            p=paint("DA_Paint_A","Actual name")
            box("One",1,{entry(p,4)});paints={}
            job=collection.begin(config);until_phase("container_paint_metadata")
            p.valid=false;result=drain()
        ''')
        model = self.model()
        self.assertEqual(model['physicalCount'], 4)
        self.assertEqual(model['rows'][0]['recipeState'], 'unknown')
        self.assertFalse(model['complete'])
        self.assertNotIn('displayName', model['rows'][0])

    def test_inline_aggregation_batches_32_records_without_double_counting(self):
        self.run_lua('''
            local p=paint("DA_Paint_A","A");local records={}
            for i=1,70 do records[i]=entry(p,1) end
            box("One",1,records);job=collection.begin(config,{background=false});until_phase("aggregate")
            result=one_step()
        ''')
        self.assertEqual(self.value('job.model.physicalCount'), 32)
        self.assertIsNone(self.value('result.model'))
        self.run_lua('one_step()')
        self.assertEqual(self.value('job.model.physicalCount'), 64)
        self.run_lua('result=drain();local count=inventory_reads;one_step();one_step();assert(inventory_reads==count)')
        self.assertEqual(self.model()['physicalCount'], 70)
        self.assertEqual(self.value('inventory_reads'), 1)
        self.assertEqual(self.value('result.processed'), self.value('result.total'))

    def test_global_scan_overrun_is_reported_and_prevents_more_work_that_tick(self):
        self.run_lua('''
            fake_time=0;os.clock=function() return fake_time end
            local original=FindAllOf
            FindAllOf=function(kind) fake_time=fake_time+0.050;return original(kind) end
            paint("DA_Paint_A","A");job=collection.begin(config)
            result=job:step(budget_module.new(2,100))
        ''')
        self.assertEqual(self.value('result.state'), 'running')
        self.assertEqual(self.value('result.phase'), 'catalog_metadata')
        self.assertEqual(self.value('scans'), 1)
        self.assertEqual(self.value('queries'), 0)
        self.assertEqual(self.value('job.model.capture.overruns'), 1)
        self.assertGreaterEqual(self.value('job.model.capture.maxSliceMs'), 50)
        self.assertIn('FindAllOf', self.value('job.model.capture.uninterruptibleStages[2].operation'))

    def test_atomic_map_read_stops_at_cap_with_nil_continue_and_reports_overrun(self):
        self.run_lua('''
            fake_time=0;os.clock=function() return fake_time end
            config.max_slots=2
            local p=paint("DA_Paint_A","A")
            local a,c=box("One",1,{})
            callbacks=0
            c.Items.ForEach=function(_,callback)
                inventory_reads=inventory_reads+1
                for i=1,10 do
                    local before=queries
                    fake_time=fake_time+0.010
                    local decision=callback(wrap(i),wrap(entry(p,1)))
                    assert(queries==before,"recipe query inside native map callback")
                    assert(decision==nil or decision==true,"false is not a safe pinned continuation")
                    callbacks=callbacks+1
                    if decision==true then break end
                end
            end
            job=collection.begin(config);until_phase("container_read")
            result=job:step(budget_module.new(2,100));assert(result.state=="running")
            assert(job.model.capture.overruns==1)
            result=drain()
        ''')
        model = self.model()
        self.assertEqual(self.value('callbacks'), 3)
        self.assertEqual(model['physicalCount'], 2)
        self.assertTrue(model['sources'][0]['slotsTruncated'])
        self.assertFalse(model['complete'])
        self.assertGreaterEqual(model['capture']['maxSliceMs'], 29)

    def test_capture_explicitly_records_nonatomic_interval_and_indivisible_stages(self):
        self.run_lua('box("One",1,{entry(paint("DA_Paint_A","A"),4)});job=collection.begin(config);result=drain()')
        model = self.model()
        capture = model['capture']
        self.assertFalse(capture['atomic'])
        self.assertIsInstance(capture['startedUtc'], str)
        self.assertIsInstance(capture['finishedUtc'], str)
        self.assertGreater(capture['slices'], 1)
        self.assertEqual(capture['budgetMs'], 10000)
        self.assertEqual(len(capture['uninterruptibleStages']), 4)
        self.assertIn('capturedUtc', model['sources'][0])

    def test_context_change_during_map_capture_cancels_before_publishing_aggregates(self):
        self.run_lua('''
            local p=paint("DA_Paint_A","A");local a,c=box("One",1,{})
            c.Items.ForEach=function(_,callback)
                callback(wrap(1),wrap(entry(p,4)))
                pawn.address=pawn.address+100
            end
            job=collection.begin(config);until_phase("container_read");one_step();result=one_step()
        ''')
        self.assertEqual(self.value('result.state'), 'cancelled')
        self.assertIsNone(self.value('result.model'))
        self.assertEqual(self.value('job.model.physicalCount'), 0)

    def test_descriptor_cap_is_bounded_and_explicitly_partial(self):
        self.run_lua('''
            config.max_discovery_objects=1
            local p=paint("DA_Paint_A","A");paint("DA_Paint_B","B")
            box("One",1,{entry(p,2)});box("Two",2,{entry(p,9)})
            job=collection.begin(config);result=drain()
        ''')
        model = self.model()
        self.assertTrue(model['catalogTruncated'])
        self.assertTrue(model['sourceDiscoveryTruncated'])
        self.assertTrue(model['sourceCountIsLowerBound'])
        self.assertFalse(model['complete'])
        self.assertEqual(model['physicalCount'], 2)

    def test_invalid_clock_preserves_count_limited_progress_and_serializable_reports(self):
        for clock in ('error("clock failed")', 'return 0/0', 'return math.huge', 'return 1e308', 'return -1'):
            with self.subTest(clock=clock):
                self.setUp()
                self.run_lua('box("One",1,{entry(paint("DA_Paint_A","A"),4)});os.clock=function() ' + clock + ' end;job=collection.begin(config);result=drain()')
                model = self.model()
                self.assertEqual(model['physicalCount'], 4)
                self.assertFalse(model['capture']['clockValid'])
                self.assertNotIn('durationMs', model['capture'])
                self.assertGreater(model['capture']['slices'], 1)

    def test_clock_wrapping_midjob_invalidates_times_and_keeps_unit_progress(self):
        self.run_lua('''
            fake_time=100;os.clock=function() return fake_time end
            box("One",1,{entry(paint("DA_Paint_A","A"),4)})
            job=collection.begin(config);one_step();fake_time=1;result=drain()
        ''')
        model = self.model()
        self.assertEqual(model['physicalCount'], 4)
        self.assertFalse(model['capture']['clockValid'])
        self.assertIn('backwards', model['capture']['clockError'])
        self.assertNotIn('maxSliceMs', model['capture'])


if __name__ == '__main__':
    unittest.main()
