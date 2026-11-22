"""Full per-paint box distributions, separate from bounded learning witnesses."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE


class ContainerBreakdownTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)

    def scan(self, code=""):
        self.lua.execute(code)
        return json.loads(self.lua.execute('return scan()'))

    def assert_distribution_totals(self, model):
        for row in model["rows"]:
            self.assertEqual(sum(source["count"] for source in row["stockSources"]), row["count"])
            for source in row["stockSources"]:
                actual = model["sources"][source["sourceIndex"] - 1]
                self.assertEqual(source["sourceId"], actual["id"])
                self.assertEqual(source["distanceCm"], actual["distanceCm"])

    def test_all_sources_are_retained_beyond_the_four_learning_witnesses(self):
        model = self.scan('''
            p=paint("DA_Paint_A","Native name")
            for i=1,6 do
                local _,c=box("Box"..i,i*100,{entry(p,i)})
                c.Name=text("Native box "..i)
            end
        ''')
        row = model["rows"][0]
        self.assertEqual(row["count"], 21)
        self.assertEqual(len(row["stockSources"]), 6)
        self.assertEqual(len(row["stockWitnesses"]), 4)
        self.assertEqual([x["count"] for x in row["stockSources"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual([x["displayName"] for x in row["stockSources"]], [f"Native box {i}" for i in range(1, 7)])
        self.assertTrue(row["stockSourcesComplete"] and model["stockSourcesComplete"])
        self.assertIn("interval", model["stockSourcesScope"])
        self.assertEqual(self.lua.eval("scans"), 2)
        self.assertEqual(self.lua.eval("inventory_reads"), 6)
        self.assertEqual(self.lua.eval("mutations"), 0)
        self.assert_distribution_totals(model)

    def test_duplicate_slots_merge_per_paint_and_source_without_losing_other_paints(self):
        model = self.scan('''
            a=paint("DA_Paint_A","A");b=paint("DA_Paint_B","B")
            box("First",100,{entry(a,2),entry(b,5),entry(a,3)},"BP_Inventory_PlayerBox_Large_C")
            box("Second",200,{entry(a,7)},"BP_Inventory_PlayerBox_Small_C")
        ''')
        a, b = model["rows"]
        self.assertEqual(a["count"], 12)
        self.assertEqual([x["count"] for x in a["stockSources"]], [5, 7])
        self.assertEqual([x["kind"] for x in a["stockSources"]], ["Large", "Small"])
        self.assertEqual([x["count"] for x in b["stockSources"]], [5])
        self.assertEqual([x["paintCount"] for x in model["sources"]], [10, 7])
        self.assert_distribution_totals(model)

    def test_distribution_uses_the_same_unambiguous_weak_item_count_as_physical_stock(self):
        model = self.scan('''
            paint("DA_Paint_A","A")
            box("Weak",100,{entry(nil,6,"DA_Paint_A")})
        ''')
        row = model["rows"][0]
        self.assertEqual(row["count"], 6)
        self.assertEqual([x["count"] for x in row["stockSources"]], [6])
        self.assertEqual(row["stockWitnesses"], [])
        self.assertTrue(row["stockSourcesComplete"])
        self.assert_distribution_totals(model)

    def test_other_inventory_roles_and_box_classes_never_enter_the_distribution(self):
        model = self.scan('''
            p=paint("DA_Paint_A","A")
            box("Counted",100,{entry(p,4)})
            box("Reward",100,{entry(p,90)},"RewardBox_C")
            box("Far",10001,{entry(p,70)})
            pawn.WeightInventory={Items={entry(p,50)}}
            pawn.PaintInventory={Items={entry(p,60)}}
            recipe_entries({entry(p,1000)})
            loose={entry(p,9000)}
        ''')
        row = model["rows"][0]
        self.assertEqual(row["count"], 4)
        self.assertEqual(len(row["stockSources"]), 1)
        self.assertEqual(row["stockSources"][0]["runtimeLabel"], "Counted")
        self.assertEqual(model["physicalCount"], 4)
        self.assert_distribution_totals(model)

    def test_learned_zero_stock_row_has_a_complete_empty_distribution(self):
        model = self.scan('''
            p=paint("DA_Paint_A","A");vault.members[p.path]=true
            recipe_entries({entry(p,1)})
        ''')
        row = model["rows"][0]
        self.assertEqual(row["stockSources"], [])
        self.assertEqual(row["count"], 0)
        self.assertTrue(row["stockSourcesComplete"])
        self.assertTrue(model["stockSourcesComplete"])

    def test_recipe_read_failure_does_not_invalidate_complete_physical_distribution(self):
        model = self.scan('''
            p=paint("DA_Paint_A","A");box("Box",100,{entry(p,6)})
            function vault:HasItem() error("recipe unavailable") end
        ''')
        row = model["rows"][0]
        self.assertFalse(row["complete"])
        self.assertEqual(row["recipeState"], "unknown")
        self.assertTrue(model["stockComplete"])
        self.assertTrue(row["stockSourcesComplete"])
        self.assert_distribution_totals(model)

    def test_container_cap_marks_every_distribution_as_a_lower_bound(self):
        model = self.scan('''
            config.max_sources=2
            p=paint("DA_Paint_A","A")
            box("Third",300,{entry(p,30)})
            box("First",100,{entry(p,10)})
            box("Second",200,{entry(p,20)})
        ''')
        row = model["rows"][0]
        self.assertEqual(model["sourceCount"], 3)
        self.assertEqual(row["count"], 30)
        self.assertEqual([x["runtimeLabel"] for x in row["stockSources"]], ["First", "Second"])
        self.assertFalse(row["stockSourcesComplete"])
        self.assertFalse(model["stockSourcesComplete"])
        self.assert_distribution_totals(model)

    def test_slot_cap_retains_counted_rows_and_marks_distribution_incomplete(self):
        model = self.scan('''
            config.max_slots=2;p=paint("DA_Paint_A","A")
            box("Box",100,{entry(p,1),entry(p,2),entry(p,4)})
        ''')
        self.assertEqual(model["rows"][0]["count"], 3)
        self.assertFalse(model["stockSourcesComplete"])
        self.assertFalse(model["rows"][0]["stockSourcesComplete"])
        self.assert_distribution_totals(model)

    def test_unresolved_later_slot_invalidates_distributions_for_other_rows(self):
        model = self.scan('''
            p=paint("DA_Paint_A","A")
            box("First",100,{entry(p,4)})
            box("Second",200,{entry(p,3),entry(nil,2,"MissingAsset")})
        ''')
        self.assertEqual(model["rows"][0]["count"], 7)
        self.assertFalse(model["rows"][0]["stockSourcesComplete"])
        self.assertFalse(model["sources"][1]["complete"])
        self.assert_distribution_totals(model)

    def test_inline_final_coverage_is_published_incrementally_without_new_native_reads(self):
        self.lua.execute('''
            for i=1,4 do paint("DA_Paint_"..i,"Paint "..i) end
            job=collection.begin(config,{background=false});until_phase("distribution_finalize")
            local reads=scans+queries+inventory_reads+vault_reads
            assert(no_native_handles(job))
            result=one_step()
            assert(result.state=="running" and result.model==nil)
            assert(job.model.rows[1].stockSourcesComplete and not job.model.rows[2].stockSourcesComplete)
            assert(scans+queries+inventory_reads+vault_reads==reads)
            result=drain()
            assert(result.state=="done" and result.processed==result.total)
            assert(scans+queries+inventory_reads+vault_reads==reads)
        ''')

    def test_inline_cancelled_generation_drops_old_distributions_and_a_new_capture_starts_clean(self):
        self.lua.execute('''
            p=paint("DA_Paint_A","A");box("Old",100,{entry(p,4)})
            job=collection.begin(config,{background=false});until_phase("distribution_finalize")
            result=job:cancel("view closed")
            assert(result.state=="cancelled" and result.model==nil and #job.model.rows==0)
            containers={};box("New",200,{entry(p,9)})
            job=collection.begin(config);result=drain()
        ''')
        model = json.loads(self.lua.execute('return json.encode(result.model)'))
        row = model["rows"][0]
        self.assertEqual(row["count"], 9)
        self.assertEqual([x["runtimeLabel"] for x in row["stockSources"]], ["New"])
        self.assert_distribution_totals(model)

    def test_inline_context_change_during_coverage_finalization_cannot_publish_old_sources(self):
        self.lua.execute('''
            p=paint("DA_Paint_A","A");box("Box",100,{entry(p,4)})
            job=collection.begin(config,{background=false});until_phase("distribution_finalize")
            pawn.address=pawn.address+100
            result=one_step()
            assert(result.state=="cancelled" and result.model==nil and #job.model.rows==0)
            assert(job.sourceDistribution==nil)
        ''')

    def test_default_lua_analysis_finishes_full_distributions_without_rereading_inventories(self):
        self.lua.execute('''
            p=paint("DA_Paint_A","A")
            for i=1,6 do box("Box"..i,i*100,{entry(p,i)}) end
            job=collection.begin(config);until_phase("collection_worker")
            local reads=inventory_reads
            assert(reads==6 and job.model.physicalCount==0)
            result=one_step()
            assert(result.state=="running" and result.model==nil)
            result=drain()
            assert(result.state=="done" and result.processed==result.total)
            assert(inventory_reads==reads and result.model.capture.executionBackend=="lua_cooperative")
        ''')
        model = json.loads(self.lua.execute('return json.encode(result.model)'))
        row = model["rows"][0]
        self.assertEqual(row["count"], 21)
        self.assertEqual(len(row["stockSources"]), 6)
        self.assertEqual(len(row["stockWitnesses"]), 4)
        self.assertTrue(row["stockSourcesComplete"])
        self.assert_distribution_totals(model)


if __name__ == "__main__":
    unittest.main()
