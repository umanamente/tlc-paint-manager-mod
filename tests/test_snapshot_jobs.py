"""Resumable diagnostic reads: no native wrappers survive between work units."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_diagnostics import FIXTURE, SCRIPTS
from test_paint_state import FIXTURE as PAINT_FIXTURE


JOB_FIXTURE = r'''
function budget(limit)
    local b={units=0,limit=limit or 1}
    function b:expired() return self.units>=self.limit end
    function b:consume() self.units=self.units+1 end
    return b
end
function drain(job)
    local ticks,progress=0
    repeat progress=job:step(budget(1));ticks=ticks+1;assert(ticks<10000) until progress.state~="running"
    assert(progress.state=="done",progress.error or progress.reason)
    return progress.data,ticks
end
function assert_plain_state(value,seen)
    if type(value)~="table" then assert(type(value)~="userdata");return end
    seen=seen or {};if seen[value] then return end;seen[value]=true
    assert(value.IsValid==nil and value.GetOwner==nil and value.GetAddress==nil,"retained native object")
    for _,child in pairs(value) do assert_plain_state(child,seen) end
end
'''


class SnapshotJobTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)

    def test_begin_and_exhausted_budget_do_no_native_work(self):
        self.lua.execute('''
        job=adapter.begin(config)
        assert(reads==0 and scans==0 and job.phase=="context")
        local p=job:step(budget(0))
        assert(p.state=="running" and p.processed==0 and p.data==nil and reads==0 and scans==0)
        ''')

    def test_inventory_sources_and_global_queries_are_spread_over_units(self):
        self.lua.execute('''
        inventory_reads=0
        for i=1,24 do
            local box=actor("Box World."..i,"BP_Inventory_PlayerBox_Small_C",i)
            local c=inventory("Inventory World."..i,box,{slot("DA_Paint_Red",i,nil)})
            local iterate=c.Items.ForEach
            c.Items.ForEach=function(...) inventory_reads=inventory_reads+1;return iterate(...) end
            inventories[#inventories+1]=c
        end
        job=adapter.begin(config)
        local ticks,progress=0
        repeat
            local before_reads,before_scans=inventory_reads,scans
            local b=budget(1);progress=job:step(b);ticks=ticks+1
            assert(b.units<=1 and inventory_reads-before_reads<=1 and scans-before_scans<=1)
            assert(progress.data==nil or progress.state=="done")
            assert_plain_state(job)
        until progress.state~="running"
        assert(progress.state=="done",progress.error or progress.reason)
        assert(ticks>50 and inventory_reads==24 and progress.data.paintCandidates.nearbyContainers.DA_Paint_Red==300)
        assert(progress.data.capture.atomic==false and progress.data.capture.slices==ticks)
        assert(progress.data.capture.workUnits==progress.processed and #progress.data.capture.uninterruptibleStages>24)
        assert(mutations==0)
        ''')

    def test_inline_inventory_aggregation_has_its_own_count_bound(self):
        self.lua.execute('''
        config.aggregate_rows_per_unit=7
        local entries={};for i=1,200 do entries[i]=slot("DA_Paint_Red",1,nil) end
        pawn.WeightInventory=inventory("Inventory World.Pack",pawn,entries)
        job=adapter.begin(config,{background=false})
        while job.phase~="aggregate" do assert(job:step().state=="running") end
        assert(job.data.paintCandidates.backpack.DA_Paint_Red==nil)
        local p=job:step(budget(1))
        assert(p.state=="running" and job.phase=="aggregate")
        assert(job.data.paintCandidates.backpack.DA_Paint_Red==7)
        local data=drain(job);assert(data.paintCandidates.backpack.DA_Paint_Red==200)
        ''')

    def test_cancel_and_radius_changes_never_publish_partial_reports(self):
        self.lua.execute('''
        job=adapter.begin(config);job:step();job:cancel("user closed")
        local p=job:step(budget(8));assert(p.state=="cancelled" and p.data==nil and job.data==nil)
        job=adapter.begin(config);job:step();config.ship_radius_cm=config.ship_radius_cm+1
        p=job:step();assert(p.state=="cancelled" and p.data==nil and p.reason:find("radius",1,true))
        ''')

    def test_controller_pawn_or_world_change_cancels_between_units(self):
        for change in ('controller.address=controller.address+1', 'pawn.address=pawn.address+1',
                       'world.address=world.address+1', 'controller.Pawn=nil'):
            with self.subTest(change=change):
                self.setUp()
                self.lua.execute('job=adapter.begin(config);job:step()')
                self.lua.execute(change)
                self.lua.execute('local p=job:step();assert(p.state=="cancelled" and p.data==nil)')

    def test_source_identity_is_resolved_again_before_reading(self):
        self.lua.execute('''
        local box=actor("Box World.One","BP_Inventory_PlayerBox_C",20)
        changing=inventory("Inventory World.One",box,{slot("DA_Paint_Red",4,nil)});inventories={changing}
        job=adapter.begin(config)
        while job.phase~="sources" do assert(job:step().state=="running") end
        changing.address=changing.address+1
        local data=drain(job)
        assert(data.complete==false and data.paintCandidates.nearbyContainers.DA_Paint_Red==nil)
        assert(#data.inventories==1 and #data.warnings>0)
        ''')

    def test_source_radius_is_rechecked_before_reading(self):
        self.lua.execute('''
        moving=actor("Box World.One","BP_Inventory_PlayerBox_C",20)
        inventories={inventory("Inventory World.One",moving,{slot("DA_Paint_Red",4,nil)})}
        job=adapter.begin(config)
        while job.phase~="sources" do assert(job:step().state=="running") end
        moving.K2_GetActorLocation=function() return {X=config.ship_radius_cm+1,Y=0,Z=0} end
        local data=drain(job)
        assert(not data.complete and #data.inventories==1 and data.paintCandidates.nearbyContainers.DA_Paint_Red==nil)
        ''')

    def test_source_discovery_descriptor_cap_is_explicit(self):
        self.lua.execute('''
        config.max_discovery_objects=1
        for i=1,2 do local a=actor("Box World."..i,"BP_Inventory_PlayerBox_C",i)
            inventories[i]=inventory("Inventory World."..i,a,{}) end
        local data=drain(adapter.begin(config))
        assert(not data.complete and data.sourceDiscoveryTruncated and #data.inventories==2)
        ''')

    def test_exact_class_path_excludes_a_same_named_custom_storage_class(self):
        self.lua.execute('''
        local a=actor("Custom Box","BP_Inventory_PlayerBox_C",1)
        a.class.path="BlueprintGeneratedClass /Game/Custom/BP_Inventory_PlayerBox.BP_Inventory_PlayerBox_C"
        inventories={inventory("Custom Inventory",a,{slot("DA_Paint_Red",5,nil)})}
        local data=drain(adapter.begin(config))
        assert(data.nearbyStockContainerCandidates==0 and #data.inventories==1)
        ''')

    def test_completed_snapshot_is_plain_json_with_separate_installed_scope(self):
        encoded = self.lua.execute('''
        local data=drain(adapter.begin(config));assert_plain_state(data)
        return json.encode(data)
        ''')
        data = json.loads(encoded)
        self.assertFalse(data['capture']['atomic'])
        self.assertFalse(data['paintState']['capture']['atomic'])
        self.assertTrue(data['paintState']['character']['complete'])
        self.assertEqual(data['paintCandidates'], {'backpack': {}, 'nearbyContainers': {}})

    def test_paint_child_step_failure_preserves_physical_diagnostic_data(self):
        self.lua.execute('''
        require("paintmanager.paint_state").begin=function()
            return {step=function() error("child step failed") end,cancel=function() end}
        end
        pawn.WeightInventory=inventory("Inventory World.Pack",pawn,{slot("DA_Paint_Red",3,nil)})
        local data=drain(adapter.begin(config))
        assert(not data.complete and data.paintCandidates.backpack.DA_Paint_Red==3)
        assert(data.paintState.status=="error" and data.paintState.errors[1]:find("child step failed",1,true))
        ''')

    def test_invalid_global_query_results_do_not_claim_empty_complete_coverage(self):
        self.lua.execute('''
        local find=FindAllOf
        FindAllOf=function(class) if class=="VoyageItem" then return false end;return find(class) end
        local data=drain(adapter.begin(config))
        assert(not data.complete and not data.catalogComplete and #data.catalog==0 and #data.warnings>0)
        ''')


class PaintDiscoveryJobTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(PAINT_FIXTURE)
        self.lua.execute(JOB_FIXTURE)
        self.lua.execute('''
        function begin_paint_job(ship_owner_id)
            local ref={name=pawn:GetFullName(),id=pawn:GetFullName().."@"..pawn:GetAddress(),className=pawn:GetClass():GetFullName()}
            return paint_state.begin(ref,config,read_inventory,
                {worldId=mainWorld:GetFullName().."@"..mainWorld:GetAddress(),shipOwnerId=ship_owner_id})
        end
        ''')

    def test_native_flags_skip_only_proven_default_and_archetype_components(self):
        self.lua.execute('''
        EObjectFlags={RF_ClassDefaultObject=0x10,RF_ArchetypeObject=0x20}
        local default=component("Paint Default",nil,4,true)
        function default:HasAnyFlags(flag) return flag==0x10 end
        local archetype=component("Paint Archetype",nil,4,true)
        function archetype:HasAnyFlags(flag) return flag==0x20 end
        candidates={default,archetype}
        local data=drain(begin_paint_job())
        assert(data.complete and #data.boats==0 and #data.errors==0)
        assert(data.excludedNonInstanceCount==2 and #data.excludedNonInstances==2)
        assert(data.excludedNonInstances[1].confirmedFlag=="RF_ClassDefaultObject")
        assert(data.excludedNonInstances[2].classification=="archetype_object")
        assert(mutationCalls==0)
        ''')

    def test_name_prefix_without_native_flag_proof_is_not_excluded(self):
        self.lua.execute('''
        EObjectFlags={RF_ClassDefaultObject=0x10,RF_ArchetypeObject=0x20}
        local c=component("Paint Default__Unowned",nil,4,true)
        c.outer=object("Outer Candidate","/Script/CoreUObject.Object")
        function c:HasAnyFlags() return false end
        candidates={c}
        local data=drain(begin_paint_job())
        assert(not data.complete and data.excludedNonInstanceCount==nil and #data.discoveryErrors==1)
        local detail=data.discoveryErrors[1]
        assert(detail.component=="Paint Default__Unowned" and detail.componentClass:find("VoyagePaintInventoryComponent",1,true))
        assert(detail.outer=="Outer Candidate" and detail.address~=json.null)
        assert(detail.lifecycle.classification=="unclassified" and detail.lifecycle.ownerAvailable==false)
        assert(data.errors[1]:find("component=Paint Default__Unowned",1,true))
        ''')

    def test_missing_flags_or_failed_flag_reads_preserve_unknown_owner_errors(self):
        for flags, method in (('nil', 'return true'),
                              ('{RF_ClassDefaultObject=0x10,RF_ArchetypeObject=0x20}', 'error("flags unavailable")')):
            with self.subTest(flags=flags):
                self.setUp()
                self.lua.execute(f'''
                EObjectFlags={flags}
                local c=component("Paint OwnerMissing",nil,4,true)
                function c:HasAnyFlags() {method} end
                candidates={{c}}
                local data=drain(begin_paint_job())
                assert(not data.complete and #data.discoveryErrors==1 and data.excludedNonInstanceCount==nil)
                ''')

    def test_live_ownerless_component_is_identified_without_masking_failure(self):
        self.lua.execute('''
        candidates={component("Paint LiveMissingOwner",nil,4,true)}
        local data=drain(begin_paint_job())
        assert(not data.complete and data.character.complete and #data.boats==0)
        assert(data.discoveryErrors[1].lifecycle.validAtRead and not data.discoveryErrors[1].lifecycle.ownerAvailable)
        assert(data.discoveryErrors[1].error:find("owner unavailable",1,true))
        ''')

    def test_each_boat_target_is_read_in_a_separate_unit(self):
        self.lua.execute('''
        for i=1,12 do local a=actor("Boat "..i,true,i)
            candidates[i]=component("Paint Boat "..i,a,4,true) end
        config.max_paint_boats=8
        local job=begin_paint_job()
        local p
        repeat local before=#readCalls;p=job:step(budget(1));assert(#readCalls-before<=1);assert_plain_state(job)
        until p.state~="running"
        assert(p.state=="done" and not p.data.complete and p.data.boatsTruncated)
        assert(#p.data.boats==8 and p.data.boatCandidatesInScope==12)
        ''')

    def test_boat_world_is_rechecked_before_capture(self):
        self.lua.execute('''
        boat=actor("Boat Moving",true,1)
        candidates={component("Paint Moving",boat,4,true)}
        local job=begin_paint_job()
        while job.phase~="targets" do assert(job:step().state=="running") end
        boat.world=world("World Other")
        local data=drain(job)
        assert(not data.complete and #data.boats==0 and #readCalls==1)
        assert(data.discoveryErrors[1].error:find("owner or world changed",1,true))
        ''')

    def test_selected_ship_is_kept_before_applying_the_boat_cap(self):
        self.lua.execute('''
        local first,selected=actor("Boat A",true,1),actor("Boat Z",true,1000000)
        config.max_paint_boats=1
        candidates={component("Paint A",first,4,true),component("Paint Z",selected,4,true)}
        local job=begin_paint_job(selected:GetFullName().."@"..selected:GetAddress())
        local data=drain(job)
        assert(data.complete and #data.boats==1 and data.boats[1].owner=="Boat Z")
        assert(data.boatCandidatesInScope==1 and #readCalls==2)
        ''')

    def test_discovery_failure_preserves_completed_character_read(self):
        self.lua.execute('''
        discoveryError=true
        local data=drain(begin_paint_job())
        assert(not data.complete and data.character.complete and #data.boats==0 and #data.errors>0)
        ''')

    def test_cancellation_drops_partial_installed_results(self):
        self.lua.execute('''
        local job=begin_paint_job();job:step();job:cancel("closed")
        local p=job:step(budget(8))
        assert(p.state=="cancelled" and p.data==nil and job.data==nil and #readCalls==1)
        ''')


if __name__ == '__main__':
    unittest.main()
