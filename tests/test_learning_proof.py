"""Live four-can proof uses fresh native witnesses before the grant boundary."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE


PROOF_FIXTURE = r'''
proof_calls=0;proof_events={};last_proof=nil
function set_entries(component,entries,after)
    component.Items={ForEach=function(_,callback)
        inventory_reads=inventory_reads+1
        for i,e in ipairs(entries) do
            local decision=callback(wrap(i),wrap(e))
            assert(decision==nil or decision==true,"unsafe native iterator continuation")
            if decision==true then break end
        end
        if after then after() end
    end}
end
function capture_request()
    job=collection.begin(config);result=drain();model=result.model
    assert(model and no_native_handles(model),"capture must retain only primitive data")
    row=model.rows[1]
    request={itemPath=row.id,contextToken=model.contextToken,radiusCm=model.radiusCm,
        runtimeRef={name=row.runtimeRef.name,id=row.runtimeRef.id,className=row.runtimeRef.className}}
end
function prove()
    proof_ok,proof_result=pcall(function()
        return collection.with_recipe_proof(config,request,model,function(context,item,library,proof)
            assert(context.pawn==pawn and context.controller==controller)
            assert(item==p and library==vault and item:IsValid() and library:IsValid())
            proof_calls=proof_calls+1;last_proof=proof
            assert(no_native_handles(proof),"proof report must be primitive")
            return "callback completed"
        end,function(name,data)
            proof_events[#proof_events+1]={name=name,data=data}
        end)
    end)
    return proof_ok,proof_result
end
function standard_scene(count)
    p=paint("DA_Paint_A","Actual game name")
    actor,component=box("One",100,{entry(p,count or 4)})
    capture_request()
end
'''


class LearningProofTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)
        self.lua.execute(PROOF_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def value(self, expression):
        return self.lua.eval(expression)

    def assert_rejected(self, expected=None):
        self.run_lua('prove()')
        self.assertFalse(self.value('proof_ok'))
        self.assertEqual(self.value('proof_calls'), 0)
        self.assertEqual(self.value('mutations'), 0)
        if expected:
            self.assertIn(expected, self.value('tostring(proof_result)'))

    def test_fresh_native_witnesses_are_plain_and_proof_does_not_mutate(self):
        self.run_lua('standard_scene(8);prove()')
        self.assertTrue(self.value('proof_ok'))
        self.assertEqual(self.value('proof_calls'), 1)
        self.assertEqual(self.value('last_proof.count'), 4)
        self.assertEqual(self.value('last_proof.physicalPolicy'), 'keep')
        self.assertEqual(self.value('mutations'), 0)
        self.assertTrue(self.value('no_native_handles(model)'))
        self.assertTrue(self.value('model.vaultRuntimeRef.id==vault:GetFullName().."@"..vault:GetAddress()'))
        self.assertTrue(self.value('row.runtimeRef.id==request.runtimeRef.id'))
        self.assertEqual(self.value('#row.stockWitnesses'), 1)

    def test_four_distinct_single_can_sources_qualify_in_at_most_four_reads(self):
        self.run_lua('''
            p=paint("DA_Paint_A","Actual game name")
            for i=1,9 do box("Source"..i,i*100,{entry(p,1)}) end
            capture_request();reads_before=inventory_reads;prove()
        ''')
        self.assertTrue(self.value('proof_ok'))
        self.assertEqual(self.value('#row.stockWitnesses'), 4)
        self.assertEqual(self.value('#last_proof.witnesses'), 4)
        self.assertEqual(self.value('inventory_reads-reads_before'), 4)
        self.assertEqual(self.value('last_proof.count'), 4)
        self.assertEqual(self.value('mutations'), 0)

    def test_changed_live_stock_below_four_rejects_old_display_count(self):
        self.run_lua('standard_scene(8);set_entries(component,{entry(p,3)})')
        self.assert_rejected('Fewer than four')
        self.assertEqual(self.value('row.count'), 8)

    def test_ineligible_fresh_row_and_incomplete_stock_are_rejected(self):
        for change in ('row.count=3', 'row.recipeState="unlocked"', 'row.recipeState="unknown"',
                       'row.complete=false', 'model.stockComplete=false', 'model.status="cancelled"'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('standard_scene(4);' + change)
                self.assert_rejected()

    def test_duplicate_component_witness_cannot_supply_the_threshold_twice(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A")
            box("One",100,{entry(p,2)});box("Two",200,{entry(p,2)})
            capture_request();row.stockWitnesses[2]=row.stockWitnesses[1]
        ''')
        self.assert_rejected('Duplicate learning witness')

    def test_weak_name_only_count_does_not_authorize_native_mutation(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A")
            box("One",100,{entry(nil,4,"DA_Paint_A")});capture_request()
        ''')
        self.assertEqual(self.value('row.count'), 4)
        self.assertTrue(self.value('model.stockComplete'))
        self.assertEqual(self.value('#row.stockWitnesses'), 0)
        self.assert_rejected('Fewer than four')

    def test_native_witness_becoming_weak_or_another_paint_is_rejected(self):
        for record in ('entry(nil,100,"DA_Paint_A")', 'entry(paint("DA_Paint_B","B"),100)'):
            with self.subTest(record=record):
                self.setUp()
                self.run_lua('standard_scene(4);set_entries(component,{' + record + '})')
                self.assert_rejected('Fewer than four')

    def test_runtime_source_identity_class_owner_world_and_radius_are_fresh(self):
        changes = (
            'component.address=component.address+1',
            'actor.address=actor.address+1',
            'component.kind="OtherInventoryComponent"',
            'actor.class_path="BlueprintGeneratedClass /Game/Custom/BP_Inventory_PlayerBox.BP_Inventory_PlayerBox_C"',
            'function component:GetOwner() return obj("Actor Replacement","BP_Inventory_PlayerBox_C") end',
            'function actor:GetWorld() return obj("World Other","World") end',
            'function actor:K2_GetActorLocation() return {X=10001,Y=0,Z=0} end',
            'function actor:K2_GetActorLocation() return nil end',
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('standard_scene(4);' + change)
                self.assert_rejected()

    def test_pawn_world_vault_item_and_requested_radius_changes_reject(self):
        changes = (
            'pawn.address=pawn.address+1',
            'world.address=world.address+1',
            'controller.Pawn=nil',
            'vault.address=vault.address+1',
            'pawn.VaultInventory=obj("Vault Other","VoyageBaseInventoryComponent")',
            'p.address=p.address+1',
            'p.kind="VoyageItem"',
            'config.ship_radius_cm=config.ship_radius_cm+1',
            'model.radiusCm=model.radiusCm+1',
            'request.runtimeRef.id=request.runtimeRef.id.."-changed"',
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('standard_scene(4);' + change)
                self.assert_rejected()

    def test_ship_center_stays_fixed_when_character_moves_and_access_is_required(self):
        self.run_lua('''
            standard_scene(4)
            function pawn:K2_GetActorLocation() return {X=50000,Y=0,Z=0} end
        ''')
        self.assert_rejected('Signal lost')
        self.setUp()
        self.run_lua('''
            standard_scene(4)
            function pawn:K2_GetActorLocation() return {X=1000,Y=0,Z=0} end
            prove()
        ''')
        self.assertTrue(self.value('proof_ok'))
        self.assertEqual(self.value('last_proof.origin.X'), 0)
        self.assertEqual(self.value('last_proof.witnesses[1].distanceCm'), 100)
        self.assertEqual(self.value('model.capture.origin.X'), 0)

    def test_context_mutation_inside_last_native_map_rejects_before_callback(self):
        changes = (
            'pawn.address=pawn.address+1',
            'world.address=world.address+1',
            'vault.address=vault.address+1',
            'p.address=p.address+1',
            'config.ship_radius_cm=config.ship_radius_cm+1',
            'function pawn:K2_GetActorLocation() return {X=1,Y=0,Z=0} end',
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('standard_scene(4);set_entries(component,{entry(p,4)},function() ' + change + ' end)')
                self.assert_rejected()

    def test_source_mutation_inside_last_native_map_rejects_before_callback(self):
        changes = (
            'component.address=component.address+1',
            'actor.address=actor.address+1',
            'actor.class_path="BlueprintGeneratedClass /Game/Custom/Box.Box_C"',
            'function actor:GetWorld() return obj("World Other","World") end',
            'function actor:K2_GetActorLocation() return {X=10001,Y=0,Z=0} end',
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('standard_scene(4);set_entries(component,{entry(p,4)},function() ' + change + ' end)')
                self.assert_rejected()

    def test_invalid_target_quantities_cannot_supply_proof(self):
        for quantity in ('-1', '0/0', 'math.huge', '1.5', 'nil', '"4"'):
            with self.subTest(quantity=quantity):
                self.setUp()
                self.run_lua('standard_scene(4);set_entries(component,{entry(p,' + quantity + ')})')
                self.assert_rejected('target count invalid')

    def test_final_native_map_failure_or_record_limit_rejects(self):
        self.run_lua('standard_scene(4);component.Items.ForEach=function() error("native read failed") end')
        self.assert_rejected('native read failed')
        self.setUp()
        self.run_lua('''
            standard_scene(4);config.max_slots=1
            nonpaint=obj("VoyageItem /Game/Ordinary.Ordinary","VoyageItem")
            set_entries(component,{entry(nonpaint,1),entry(p,4)})
        ''')
        self.assert_rejected('record limit')

    def test_threshold_early_stop_and_nil_continue_are_native_safe(self):
        self.run_lua('''
            standard_scene(4);nonpaint=obj("VoyageItem /Game/Ordinary.Ordinary","VoyageItem")
            set_entries(component,{entry(nonpaint,1),entry(p,2),entry(p,2),entry(p,nil)})
            prove()
        ''')
        self.assertTrue(self.value('proof_ok'))
        self.assertEqual(self.value('last_proof.witnesses[1].recordsVisited'), 3)
        self.assertEqual(self.value('last_proof.count'), 4)
        events = json.loads(self.run_lua('return json.encode(json.array(proof_events))'))
        self.assertEqual([event['name'] for event in events], ['witness_rechecked', 'stock_proof_ready'])
        self.assertEqual(self.value('mutations'), 0)


if __name__ == '__main__':
    unittest.main()
