"""Native learning proof for carried paint and eligible ship-box contributions."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE
from test_paint_transfer import TRANSFER


class LearningBackpackTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + JOB_FIXTURE + TRANSFER)
        self.lua.execute(r'''
        learning=require("paintmanager.learn_recipe")
        grants=0
        function vault:AddItem(item,data,stacking,notify)
            assert(item==A and data.ItemCount==1 and stacking and notify)
            grants=grants+1;self.members[item.path]=true
            return 0
        end
        function capture(bag_count,box_count)
            if bag_count>0 then bag.slots[0]=can(A,5,bag_count) end
            source=stock("First",id1,1,box_count>0 and {[0]=can(A,7,box_count)} or {})
            model=collection.scan(config)
            for _,candidate in ipairs(model.rows) do if candidate.id==A.path then row=candidate end end
            assert(row and model.backpackCounts[A.path]==(bag_count>0 and bag_count or nil))
            request={itemPath=row.id,runtimeRef=row.runtimeRef,contextToken=model.contextToken,
                radiusCm=model.radiusCm,gameBuild=config.reference_build}
        end
        function learn(emitter)
            result=learning.run_cached(config,request,model,emitter)
            receipt=result.receipt
            assert(no_native_handles(receipt))
        end
        function denied()
            assert(grants==0 and moves==0 and not receipt.attempted and receipt.outcome=="rejected",
                tostring(receipt.reason))
        end
        ''')

    def test_only_backpack_is_needed_and_no_box_hints_are_required(self):
        self.lua.execute('''
        capture(4,0)
        assert(#row.stockLocationHints==0 and #row.stockWitnesses==0)
        function source.Items:ForEach() error("unnecessary box read") end
        learn()
        assert(receipt.outcome=="learned" and grants==1 and moves==0,tostring(receipt.reason))
        assert(receipt.proof.backpack.componentId==bag:GetFullName().."@"..bag:GetAddress())
        assert(receipt.preflight.count==4 and receipt.preflight.physicalCount==4)
        assert(receipt.proof.backpack.count==4 and #receipt.proof.witnesses==0)
        assert(bag.slots[0].Data.ItemCount==4)
        ''')

    def test_two_backpack_slots_and_two_separate_boxes_are_counted_once(self):
        self.lua.execute('''
        local second=stock("Second",id2,2,{[0]=can(A,8,1)})
        capture(2,1)
        bag.slots={[0]=can(A,5,1),[7]=can(A,6,1)}
        local before=box_queries
        learn()
        assert(receipt.outcome=="learned" and grants==1 and moves==0,tostring(receipt.reason))
        assert(receipt.preflight.count==4 and receipt.proof.count==4)
        assert(receipt.proof.backpack.count==2 and receipt.proof.backpack.recordsVisited==2)
        assert(#receipt.proof.witnesses==2)
        assert(receipt.proof.witnesses[1].count==1 and receipt.proof.witnesses[2].count==1)
        assert(box_queries==before and bag.slots[7].Data.ItemCount==1 and second.slots[0].Data.ItemCount==1)
        assert(source.slots[0].Data.ItemCount==1 and bag.slots[0].Data.ItemCount==1)
        ''')

    def test_unreadable_backpack_can_never_contribute_partial_counts(self):
        self.lua.execute('''
        capture(3,1)
        bag.Items.ForEach=function(_,f)
            f(wrap(0),wrap(can(A,1,3)));error("unreadable map")
        end
        learn();denied()
        ''')

    def test_four_independent_box_cans_qualify_if_backpack_cannot_be_read(self):
        self.lua.execute('''
        capture(0,4)
        bag.Items.ForEach=function() error("unreadable map") end
        learn()
        assert(grants==1 and receipt.outcome=="learned" and not receipt.proof.backpack.complete)
        assert(receipt.proof.count==4 and receipt.proof.witnesses[1].count==4)
        ''')

    def test_box_and_backpack_alias_cannot_be_used_twice(self):
        self.lua.execute('''
        capture(2,2)
        model.stockLocationSources[row.stockLocationHints[1]].componentRef.id=bag:GetFullName().."@"..bag:GetAddress()
        learn();denied()
        assert(receipt.reason:find("Duplicate learning witness"))
        ''')

    def test_native_type_identity_and_unique_slots_are_required(self):
        changes = (
            'function controller:IsLocalController() return false end',
            'function controller:GetInventoryComponent() return source end',
            'function bag:GetOwner() return controller end',
            'bag.kind="VoyageBaseInventoryComponent"',
            'bag.slots[0]=can(B,5,4)',
            'bag.slots[0]=entry(nil,4,A:GetFName():ToString())',
            'bag.slots[0].Data.ItemCount=3.5',
            'bag.Items.ForEach=function(_,f) f(wrap(7),wrap(can(A,1,2)));f(wrap(7),wrap(can(A,1,2))) end',
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.lua.execute('capture(4,0);' + change + ';learn();denied()')

    def test_movements_between_reads_and_during_membership_never_authorize_stale_supply(self):
        changes = (
            # A carried stack moves to a box during the first proof.
            'local original=bag.Items.ForEach;bag.Items.ForEach=function(...) original(...);'
            'source.slots[0]=bag.slots[0];bag.slots={} end',
            # The last membership query can call game code while inventory is changing.
            'function vault:HasItem() source.slots={};return false end',
            'function vault:HasItem() bag.slots={};return false end',
            'function vault:HasItem() bag.address=bag.address+1;return false end',
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.lua.execute('capture(2,2);' + change + ';learn();denied()')

    def test_signal_loss_during_final_backpack_reread_blocks_native_call(self):
        self.lua.execute('''
        capture(2,2)
        local original=bag.Items.ForEach
        local reads=0
        bag.Items.ForEach=function(...)
            reads=reads+1;original(...)
            if reads==2 then fixtureShipOrigin={X=50000,Y=0,Z=0} end
        end
        learn();denied()
        assert(reads==2)
        ''')
