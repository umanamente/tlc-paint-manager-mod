"""Single recipe actions synchronously prove only cached native-positive boxes."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE
from test_learn_recipe import LEARN_FIXTURE
from test_learning_proof import PROOF_FIXTURE


class CachedLearningTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + JOB_FIXTURE + LEARN_FIXTURE + PROOF_FIXTURE)
        self.lua.execute(r'''
        function cache_scene()
            cached=collection.scan(config)
            request={itemPath=cached.rows[1].id,runtimeRef=cached.rows[1].runtimeRef,
                contextToken=cached.contextToken,radiusCm=cached.radiusCm,gameBuild=config.reference_build}
        end
        function learn_cached(capture)
            result=learning.run_cached(config,request,capture or cached,
                function(event,data) events[#events+1]={event=event,data=data} end)
            assert(result.state=="done" and no_native_handles(result),"single action did not terminate with plain data")
            receipt=result.receipt
            return result
        end
        ''')

    def run_lua(self, code):
        return self.lua.execute(code)

    def value(self, expression):
        return self.lua.eval(expression)

    def test_click_is_synchronous_and_skips_scan_catalog_worker_and_receipt_log_expansion(self):
        self.run_lua(r'''
        cache_scene()
        local beforeScans,beforeVault,beforeReads,beforeQueries=scans,vault_reads,inventory_reads,queries
        collection.begin=function() error("single click started a global collector") end
        FindAllOf=function() error("single click enumerated global objects") end
        package.loaded["paintmanager.background"]={begin_task=function() error("single click submitted a worker") end}
        learn_cached()
        assert(receipt.outcome=="learned" and nativeCalls==1 and result.model==cached)
        assert(scans==beforeScans and vault_reads==beforeVault and inventory_reads-beforeReads==2)
        assert(queries-beforeQueries==2 and cached.rows[1].recipeState=="unlocked")
        assert(stockRecords[1].Data.ItemCount==4 and receipt.execution=="synchronous_cached_locations")
        for _,event in ipairs(events) do
            if event.event=="preflight_complete" then assert(event.data.capture==nil) end
        end
        ''')

    def test_all_native_positive_locations_are_kept_and_later_cached_box_supplies_four(self):
        self.run_lua(r'''
        stockRecords[1].Data.ItemCount=1
        local boxes={c}
        for i=2,7 do local _,other=box("Box"..i,i,{entry(p,1)});boxes[i]=other end
        cache_scene()
        assert(#cached.rows[1].stockWitnesses==4 and #cached.rows[1].stockLocationHints==7)
        for i=1,6 do set_entries(boxes[i],{}) end
        set_entries(boxes[7],{entry(p,4),entry(p,nil)})
        local before=inventory_reads
        learn_cached()
        assert(receipt.outcome=="learned" and inventory_reads-before==8 and nativeCalls==1)
        assert(receipt.proof.count==4 and #receipt.proof.witnesses==7)
        assert(receipt.proof.witnesses[7].recordsVisited==1)
        ''')

    def test_source_cache_excludes_weak_matches_and_deduplicates_multiple_native_slots(self):
        self.run_lua(r'''
        set_entries(c,{entry(p,1),entry(p,1),entry(nil,2,"DA_Paint_Canada")})
        box("Weak",2,{entry(nil,4,"DA_Paint_Canada")})
        cache_scene()
        assert(cached.rows[1].count==8 and #cached.rows[1].stockLocationHints==1)
        assert(cached.rows[1].stockLocationHints[1]==1 and #cached.stockLocationSources==2)
        assert(cached.rows[1].stockWitnesses[1].capturedCount==2)
        learn_cached()
        assert(receipt.outcome=="rejected" and receipt.reasonCode=="refresh_required" and nativeCalls==0)
        ''')

    def test_threshold_stops_before_later_hints_and_before_later_inventory_records(self):
        self.run_lua(r'''
        local _,later=box("Later",2,{entry(p,4)})
        cache_scene()
        set_entries(c,{entry(p,4),entry(p,nil)})
        later.Items.ForEach=function() error("unneeded later candidate read") end
        local before=inventory_reads
        learn_cached()
        assert(receipt.outcome=="learned" and inventory_reads-before==2 and #receipt.proof.witnesses==1)
        assert(receipt.proof.witnesses[1].recordsVisited==1 and nativeCalls==1)
        ''')

    def test_stock_moved_into_new_or_previously_empty_box_has_no_global_search_fallback(self):
        self.run_lua(r'''
        local _,empty=box("Empty",2,{})
        cache_scene()
        set_entries(c,{entry(p,3)})
        set_entries(empty,{entry(p,4)})
        box("New",3,{entry(p,4)})
        local beforeScans,beforeReads=scans,inventory_reads
        learn_cached()
        assert(receipt.outcome=="rejected" and receipt.reasonCode=="refresh_required")
        assert(scans==beforeScans and inventory_reads-beforeReads==1 and nativeCalls==0)
        assert(cached.rows[1].count==4)
        ''')

    def test_absent_hints_or_capture_reject_without_scans_or_witness_fallback(self):
        for change in ('cached.rows[1].stockLocationHints=nil', 'cached={}', 'cached=nil'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('cache_scene();' + change + r'''
                    local beforeScans,beforeReads=scans,inventory_reads
                    result=learning.run_cached(config,request,cached)
                    assert(result.state=="done" and result.receipt.outcome=="rejected")
                    assert(result.receipt.reasonCode=="refresh_required" and nativeCalls==0)
                    assert(scans==beforeScans and inventory_reads==beforeReads)
                ''')

    def test_current_native_identity_world_radius_library_and_count_are_required(self):
        for change in (
            'world.address=world.address+1', 'pawn.address=pawn.address+1',
            'controller.Pawn=nil', 'config.ship_radius_cm=config.ship_radius_cm+1',
            'p.address=p.address+1', 'vault.address=vault.address+1',
            'pawn.VaultInventory=nil', 'c.address=c.address+1', 'a.address=a.address+1',
            'a.class_path="BlueprintGeneratedClass /Game/Custom/Box.Box_C"',
            'function c:GetOwner() return pawn end',
            'function a:GetWorld() return obj("World Other","World") end',
            'function a:K2_GetActorLocation() return {X=10001,Y=0,Z=0} end',
            'set_entries(c,{entry(p,3)})', 'set_entries(c,{entry(p,-1)})',
            'set_entries(c,{entry(nil,4,"DA_Paint_Canada")})',
            'set_entries(c,{entry(paint("DA_Paint_B","B"),4)})',
            'vault.HasItem=function() return nil end',
            'c.Items.ForEach=function() error("native map failed") end',
        ):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('cache_scene();' + change + ';learn_cached()')
                self.assertEqual(self.value('receipt.outcome'), 'rejected')
                self.assertEqual(self.value('nativeCalls'), 0)
                self.assertFalse(self.value('receipt.attempted'))

    def test_context_or_membership_changes_inside_native_read_prevent_grant(self):
        for change in ('world.address=world.address+1', 'p.address=p.address+1',
                       'vault.address=vault.address+1', 'config.ship_radius_cm=config.ship_radius_cm+1'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('cache_scene();set_entries(c,{entry(p,4)},function() ' + change + ' end);learn_cached()')
                self.assertEqual(self.value('receipt.outcome'), 'rejected')
                self.assertEqual(self.value('nativeCalls'), 0)
        self.setUp()
        self.run_lua(r'''
        cache_scene();vault.HasItem=function() world.address=world.address+1;return false end
        learn_cached();assert(receipt.outcome=="rejected" and nativeCalls==0)
        ''')

    def test_ship_moves_during_membership_and_source_leaves_radius_before_grant(self):
        self.run_lua(r'''
        fixtureShipRadius=3500
        function a:K2_GetActorLocation()return {X=-3000,Y=0,Z=0}end
        cache_scene()
        local reads_before,scans_before=inventory_reads,scans
        function vault:HasItem(item)
            queries=queries+1;fixtureShipOrigin={X=1000,Y=0,Z=0}
            return self.members[item.path]==true
        end
        learn_cached()
        assert(receipt.outcome=="rejected" and not receipt.attempted and nativeCalls==0)
        assert(tostring(receipt.reason):find("radius"))
        assert(inventory_reads-reads_before==1 and scans==scans_before and stockRecords[1].Data.ItemCount==4)
        ''')

    def test_membership_ship_motion_rechecks_only_the_still_eligible_contributor(self):
        self.run_lua(r'''
        fixtureShipRadius=3500
        local source_position={X=-3000,Y=0,Z=0}
        function a:K2_GetActorLocation()return source_position end
        cache_scene()
        local reads_before,scans_before,queries_before=inventory_reads,scans,queries
        function vault:HasItem(item)
            queries=queries+1;fixtureShipOrigin={X=1000,Y=0,Z=0}
            source_position={X=-2000,Y=0,Z=0}
            return self.members[item.path]==true
        end
        learn_cached()
        assert(receipt.outcome=="learned" and receipt.attempted and nativeCalls==1)
        assert(inventory_reads-reads_before==2 and scans==scans_before and queries-queries_before==2)
        assert(stockRecords[1].Data.ItemCount==4)
        ''')

    def test_vehicle_possession_during_membership_rejects_even_with_same_resolved_driver(self):
        self.run_lua(r'''
        cache_scene()
        local vehicle=obj("Vehicle /Game/Map.Map.Vehicle","VoyageVehiclePawn")
        package.loaded["paintmanager.ship_scope"].character=function(controller)
            assert(controller.Pawn==vehicle);return pawn
        end
        function vault:HasItem(item)
            queries=queries+1;controller.Pawn=vehicle
            return self.members[item.path]==true
        end
        learn_cached()
        assert(receipt.outcome=="rejected" and not receipt.attempted and nativeCalls==0)
        assert(tostring(receipt.reason):find("Leave vehicle controls"))
        assert(stockRecords[1].Data.ItemCount==4)
        ''')

    def test_ordinary_batch_witness_descriptors_work_without_cached_location_bank(self):
        self.run_lua(r'''
        fixtureShipRadius=3500
        cache_scene();cached.stockLocationSources=nil
        local reads_before,scans_before=inventory_reads,scans
        local child=learning.begin_from_capture(config,request,cached)
        local result
        for _=1,10 do
            result=child:step(budget_module.new(10000,1))
            if result.state~="running"then break end
        end
        assert(result.state=="done" and result.receipt.outcome=="learned" and nativeCalls==1)
        assert(inventory_reads-reads_before==2 and scans==scans_before)
        ''')

    def test_split_native_records_can_overshoot_the_threshold_without_rejection(self):
        self.run_lua(r'''
        stockRecords={entry(p,3),entry(p,3)}
        set_entries(c,stockRecords);cache_scene()
        local reads_before=inventory_reads
        learn_cached()
        assert(receipt.outcome=="learned" and nativeCalls==1 and receipt.proof.count==6)
        assert(#receipt.proof.witnesses==1 and receipt.proof.witnesses[1].count==6)
        assert(inventory_reads-reads_before==2 and stockRecords[1].Data.ItemCount==3 and stockRecords[2].Data.ItemCount==3)
        ''')

    def test_box_only_cans_removed_by_membership_callback_cannot_authorize_single_learn(self):
        self.run_lua('''
        cache_scene()
        function vault:HasItem(item)
            stockRecords[1].Data.ItemCount=0
            return self.members[item.path]==true
        end
        local before=scans
        learn_cached()
        assert(receipt.outcome=="rejected" and not receipt.attempted and nativeCalls==0 and scans==before)
        assert(receipt.reason:find("Box paint changed",1,true))
        ''')

    def test_recipe_unlocked_since_capture_requires_no_grant(self):
        self.run_lua(r'''
        cache_scene();vault.members[p.path]=true;learn_cached()
        assert(receipt.outcome=="already_known" and nativeCalls==0 and cached.rows[1].recipeState=="unlocked")
        ''')

    def test_unknown_grant_is_never_retried_and_repeat_rejects_before_inventory_read(self):
        self.run_lua(r'''
        cache_scene();noMembershipChange=true;errorDuringAdd=true;learn_cached()
        assert(receipt.outcome=="unverified" and nativeCalls==1 and receipt.afterHasItem==false)
        local reads=inventory_reads
        errorDuringAdd=false;noMembershipChange=false;learn_cached()
        assert(receipt.outcome=="unverified" and receipt.blockedByPreviousAttempt and not receipt.attempted)
        assert(nativeCalls==1 and inventory_reads==reads)
        ''')

    def test_confirmed_context_reset_during_native_call_preserves_the_postcheck_reason(self):
        self.run_lua(r'''
        cache_scene()
        local add=vault.AddItem
        function vault:AddItem(...)
            local returned=add(self,...)
            learning.reset_context();world.address=world.address+1
            return returned
        end
        learn_cached()
        assert(receipt.attempted and nativeCalls==1 and receipt.outcome=="unverified")
        assert(receipt.afterHasItem==nil and receipt.postOutcomeError==nil)
        assert(receipt.reason=="Native call attempted, but unlocked membership was not verified; no automatic retry")
        ''')

    def test_reentrant_native_timing_sample_cannot_bypass_last_grant_admission(self):
        self.run_lua(r'''
        cache_scene()
        local entering=false
        local result=learning.run_cached(config,request,cached,function(event)
            if event=="native_call_begin" then entering=true end
        end,{clock_ms=function()
            if entering then world.address=world.address+1;entering=false end
            return 100
        end})
        assert(result.receipt.outcome=="rejected" and not result.receipt.attempted and nativeCalls==0)
        ''')

    def test_cached_action_enforces_build_without_any_native_inventory_access(self):
        for value in ('nil', '"other"'):
            with self.subTest(value=value):
                self.setUp()
                self.run_lua('cache_scene();request.gameBuild=' + value + r'''
                    local before=scans+inventory_reads+queries
                    learn_cached()
                    assert(receipt.outcome=="rejected" and nativeCalls==0 and scans+inventory_reads+queries==before)
                    assert(receipt.reasonCode==nil)
                ''')

    def test_lua_executor_and_direct_captures_both_retain_all_location_hints(self):
        self.run_lua(r'''
        for i=2,8 do box("Box"..i,i,{entry(p,1)}) end
        job=collection.begin(config,{presentation="paints",background=false});drain()
        local direct=result.model
        assert(#direct.rows[1].stockLocationHints==8 and #direct.rows[1].stockWitnesses==4)
        local background=require("paintmanager.background")
        assert(background.describe().backend=="lua_cooperative" and not background.describe().workerEnabled)
        local submissions=0
        background.set_observer(function(event)
            assert(event.backend=="lua_cooperative");submissions=submissions+1
        end)
        job=collection.begin(config,{presentation="paints"});drain();cached=result.model
        request={itemPath=cached.rows[1].id,runtimeRef=cached.rows[1].runtimeRef,
            contextToken=cached.contextToken,radiusCm=cached.radiusCm,gameBuild=config.reference_build}
        assert(cached.capture.backgroundAnalysis==false and cached.capture.executionBackend=="lua_cooperative")
        assert(#cached.rows[1].stockLocationHints==8 and #cached.stockLocationSources==#direct.stockLocationSources)
        assert(#cached.rows[1].stockWitnesses==4 and no_native_handles(cached))
        assert(cached.rows[1].count==direct.rows[1].count and submissions>0)
        assert(json.encode(cached.rows[1].stockLocationHints)==json.encode(direct.rows[1].stockLocationHints))
        local count=submissions
        learn_cached()
        assert(receipt.outcome=="learned" and submissions==count and nativeCalls==1)
        background.set_observer(nil)
        ''')

    def test_corrupt_unknown_container_kind_cannot_match_nil_native_kind(self):
        self.run_lua(r'''
        cache_scene()
        a.class_path="BlueprintGeneratedClass /Game/Custom/Box.Box_C"
        local hint=cached.stockLocationSources[cached.rows[1].stockLocationHints[1]]
        hint.kind=nil;hint.ownerRef.className=a:GetClass():GetFullName()
        learn_cached();assert(receipt.outcome=="rejected" and nativeCalls==0)
        ''')

    def test_cancelling_legacy_preparation_releases_pending_collector(self):
        self.run_lua(r'''
        new_job()
        local cancelled=false
        job.collector.cancel=function() cancelled=true end
        config.ship_radius_cm=config.ship_radius_cm+1
        learning_step()
        assert(cancelled and job.state=="done" and job.collector==nil and nativeCalls==0)
        ''')


if __name__ == '__main__':
    unittest.main()
