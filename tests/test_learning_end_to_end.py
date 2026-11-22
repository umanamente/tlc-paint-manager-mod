"""Actual controller/collector/proof/grant modules with a native inventory fixture."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE as COLLECTION_FIXTURE, SCRIPTS
from test_main_ui import FIXTURE as MAIN_FIXTURE


class LearningEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(MAIN_FIXTURE)
        self.lua.execute('package.loaded["paintmanager.collection"]=nil')
        self.lua.execute(COLLECTION_FIXTURE)
        self.lua.execute(r'''
            debug.getinfo=function() return {source="@D:/Steam/steamapps/common/Voyage/Voyage/Binaries/Win64/ue4ss/Mods/PaintManager/Scripts/main.lua"} end
            local original_open=io.open
            io.open=function(path,mode)
                if mode=="r" then return {read=function() return '"buildid" "25982825"' end,close=function() return true end} end
                return original_open(path,mode)
            end
            p=paint("DA_Paint_Canada","Last leaf")
            box("Small",1,{entry(p,2)},"BP_Inventory_PlayerBox_Small_C")
            box("Large",2,{entry(p,2)},"BP_Inventory_PlayerBox_Large_C")
            native_grants=0
            function vault:AddItem(item,data,stack,notify)
                assert(inGameThread and item==p)
                assert(data.ItemCount==1 and data.Timestamp==0 and data.Flags==0)
                assert(stack==true and notify==true)
                native_grants=native_grants+1;self.members[item.path]=true
                return 0
            end
            function finish_work()
                for _=1,100 do step() end
            end
            start();registered.F10();finish_work()
            assert(ui.model.physicalCount==4 and ui.model.rows[1].recipeState=="locked")
        ''')

    def capture_backpack_scene(self, backpack_count, box_count):
        self.lua.globals().bag_quantity = backpack_count
        self.lua.globals().box_quantity = box_count
        self.lua.execute('''
            local adapter = assert(loadfile(scripts_path.."/paintmanager/game_adapter.lua"))()
            require("paintmanager.game_adapter").read_slot = adapter.read_slot
            containers = {}
            if box_quantity > 0 then box("MixedStock", 4, {entry(p, box_quantity)}) end
            bag = obj("Inventory /Game/Map.Map.Pawn.WeightInventory", "VoyageInventoryWeightLimitedComponent")
            function bag:GetOwner() return pawn end
            bag.Items = {ForEach=function(_, visit)
                if bag_quantity > 0 then visit(wrap(1), wrap(entry(p, bag_quantity))) end
            end}
            pawn.WeightInventory = bag
            function controller:IsLocalController() return true end
            function controller:GetInventoryComponent(index) assert(index == 1); return bag end
            reopen_window(); finish_work()
            assert(ui.model.backpackCounts[p.path] == bag_quantity and ui.model.physicalCount == box_quantity)
        ''')

    def test_four_backpack_cans_enable_single_learning_without_box_hints(self):
        self.capture_backpack_scene(4, 0)
        self.lua.execute('''
            local row = ui.model.rows[1]
            assert(#row.stockLocationHints == 0 and require("paintmanager.paint_view").classify(ui.model, row) == "ready")
            local before = scans
            assert(invoke("learn_recipe", row.id))
            assert(scans == before and native_grants == 1 and bag_quantity == 4 and mutations == 0)
        ''')

    def test_batch_learns_from_backpack_and_box_together(self):
        self.capture_backpack_scene(2, 2)
        self.lua.execute('''
            assert(invoke("learn_all")); finish_work()
            assert(native_grants == 1 and bag_quantity == 2 and ui.model.physicalCount == 2 and mutations == 0)
            assert(ui.model.rows[1].recipeState == "unlocked")
        ''')

    def test_can_removed_from_backpack_after_listing_prevents_grant(self):
        self.capture_backpack_scene(4, 0)
        self.lua.execute('''
            local row = ui.model.rows[1]
            assert(require("paintmanager.paint_view").classify(ui.model, row) == "ready")
            bag_quantity = 3
            assert(not invoke("learn_recipe", row.id))
            assert(native_grants == 0 and mutations == 0)
        ''')

    def test_signal_loss_during_native_membership_read_rejects_before_grant(self):
        self.lua.execute('''
        function vault:HasItem(item)
            function pawn:K2_GetActorLocation() return {X=50000,Y=0,Z=0} end
            return false
        end
        assert(not invoke("learn_recipe",ui.model.rows[1].id))
        assert(native_grants==0 and not ui.learning)
        ''')

    def test_real_modules_grant_once_keep_stock_and_write_verified_receipt(self):
        self.lua.execute('''
            local beforeScans,beforeVault,beforeReads=scans,vault_reads,inventory_reads
            assert(invoke("learn_recipe",ui.model.rows[1].id))
            assert(native_grants==1 and ui.model.rows[1].recipeState=="unlocked")
            assert(scans==beforeScans and vault_reads==beforeVault and inventory_reads-beforeReads==4)
            assert(ui.status.state=="done" and not ui.learning and #written==0)
            finish_work()
            assert(ui.model.physicalCount==4 and ui.status.state=="done")
            assert(not invoke("learn_recipe",ui.model.rows[1].id));finish_work()
            assert(native_grants==1)
        ''')
        reports = json.loads(self.lua.execute('return json.encode(json.array(written))'))
        learning = [json.loads(r['text']) for r in reports if json.loads(r['text'])['kind'] == 'learn_recipe']
        self.assertEqual(len(learning), 1)
        self.assertFalse(learning[0]['readOnly'])
        self.assertEqual(learning[0]['data']['outcome'], 'learned')
        self.assertTrue(learning[0]['data']['attempted'])
        self.assertEqual(learning[0]['data']['physicalPolicy'], 'keep')

    def test_current_cached_boxes_below_four_reject_without_finding_new_stock(self):
        self.lua.execute('''
            local function contents(component,count)
                component.Items={ForEach=function(_,callback)
                    inventory_reads=inventory_reads+1
                    callback(wrap(1),wrap(entry(p,count)))
                end}
            end
            contents(containers[1],1);contents(containers[2],2)
            box("NewStock",3,{entry(p,100)})
            local beforeScans,beforeReads=scans,inventory_reads
            assert(not invoke("learn_recipe",ui.model.rows[1].id))
            assert(native_grants==0 and scans==beforeScans and inventory_reads-beforeReads==2)
            assert(ui.model.rows[1].recipeState=="locked" and ui.model.physicalCount==4)
            assert(ui.status.state=="error" and not ui.learning)
            assert(ui.status.message:find("Reopen",1,true))
            finish_work();assert(native_grants==0)
        ''')

    def test_runtime_context_change_rejects_before_any_inventory_read(self):
        self.lua.execute('''
            world.address=world.address+1
            local beforeScans,beforeReads=scans,inventory_reads
            local ok,message=invoke("learn_recipe",ui.model.rows[1].id)
            assert(not ok and message:find("Reopen",1,true))
            assert(native_grants==0 and scans==beforeScans and inventory_reads==beforeReads)
            assert(ui.model==nil and not ui.learning)
        ''')

    def test_uncertain_grant_and_repeated_click_do_not_repeat_native_or_inventory_work(self):
        self.lua.execute('''
            function vault:AddItem(item,data,stack,notify)
                assert(inGameThread and item==p)
                native_grants=native_grants+1
                error("native call outcome unavailable")
            end
            local id=ui.model.rows[1].id
            assert(not invoke("learn_recipe",id))
            assert(native_grants==1 and ui.status.state=="error" and not ui.learning)
            local beforeScans,beforeReads=scans,inventory_reads
            assert(not invoke("learn_recipe",id))
            assert(native_grants==1 and scans==beforeScans and inventory_reads==beforeReads)
            finish_work();assert(native_grants==1 and ui.model.rows[1].recipeState=="locked")
        ''')

    def test_success_returns_before_receipt_processing_and_recipe_closes_safely(self):
        self.lua.execute('''
            assert(invoke("learn_recipe",ui.model.rows[1].id))
            assert(native_grants==1 and ui.status.state=="done" and not ui.learning)
            registered.F10()
            world.address=world.address+1
            finish_work()
            assert(native_grants==1 and not ui.open)
        ''')
        reports = json.loads(self.lua.execute('return json.encode(json.array(written))'))
        learning = [json.loads(row['text']) for row in reports if json.loads(row['text'])['kind'] == 'learn_recipe']
        self.assertEqual(len(learning), 1)
        self.assertEqual(learning[0]['data']['outcome'], 'learned')
        self.assertTrue(learning[0]['data']['afterHasItem'])

    def test_real_bulk_pipeline_uses_native_time_and_keeps_both_paints(self):
        self.lua.execute('''
            q=paint("DA_Paint_Suomi","North home")
            box("Extra",3,{entry(q,4)})
            native_time=100000;native_frame=1
            local gameplay=obj("ClockLibrary","GameplayStatics")
            function gameplay:IsA(path) return path=="/Script/Engine.GameplayStatics" end
            function gameplay:GetAccurateRealTime(a,b)
                assert(a==b)
                native_time=native_time+0.05
                a.Seconds=math.floor(native_time/1000)
                a.PartialSeconds=(native_time%1000)/1000
            end
            local system=obj("FrameLibrary","KismetSystemLibrary")
            function system:IsA(path) return path=="/Script/Engine.KismetSystemLibrary" end
            function system:GetFrameCount() return native_frame end
            runtime_objects["/Script/Engine.Default__GameplayStatics"]=gameplay
            runtime_objects["/Script/Engine.Default__KismetSystemLibrary"]=system
            function vault:AddItem(item,data,stack,notify)
                assert(inGameThread and (item==p or item==q))
                assert(data.ItemCount==1 and stack and notify)
                native_grants=native_grants+1;self.members[item.path]=true
                native_time=native_time+1
                return 1
            end
            reopen_window();finish_work()
            local open_frame=pump
            pump=function() native_frame=native_frame+1;open_frame() end
            assert(ui.model.physicalCount==8)
            assert(invoke("learn_all"));assert(native_grants==0)
            assert(not invoke("learn_all"));finish_work()
            assert(native_grants==2 and ui.model.physicalCount==8)
            assert(ui.model.rows[1].recipeState=="unlocked" and ui.model.rows[2].recipeState=="unlocked")
            assert(ui.status.state=="done")
        ''')
        reports = json.loads(self.lua.execute('return json.encode(json.array(written))'))
        batches = [json.loads(r['text']) for r in reports if json.loads(r['text'])['kind'] == 'learn_all']
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertFalse(batch['readOnly'])
        self.assertEqual(batch['data']['counts']['learned'], 2)
        self.assertEqual(batch['data']['outcome'], 'completed')
        self.assertIn('GetAccurateRealTime', batch['data']['clockSource'])
        self.assertTrue(batch['data']['scheduler']['timing']['nativeClock'])
        self.assertGreater(batch['data']['metrics']['schedulerCallbacks'], 1)


if __name__ == '__main__':
    unittest.main()
