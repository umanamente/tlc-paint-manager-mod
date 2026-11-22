"""Vault recipe discovery remains separate from radius-limited physical stock."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE


class RecipeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def scan(self):
        return json.loads(self.run_lua('return scan()'))

    def test_vault_only_paint_is_discovered_without_loaded_catalog_or_stock(self):
        self.run_lua('''
            p=paint("DA_Paint_Canada","Last Leaf");paints={}
            vault.members[p.path]=true;recipe_entries({entry(p,1000)})
        ''')
        model = self.scan()
        self.assertEqual(model['physicalCount'], 0)
        self.assertEqual(model['includedSourceCount'], 0)
        self.assertEqual(len(model['rows']), 1)
        self.assertEqual(model['rows'][0]['count'], 0)
        self.assertEqual(model['rows'][0]['displayName'], 'Last Leaf')
        self.assertEqual(model['rows'][0]['recipeState'], 'unlocked')
        self.assertTrue(model['recipeCatalogComplete'])
        self.assertTrue(model['stockComplete'])
        self.assertEqual(model['recipeEntriesVisited'], 1)
        self.assertEqual(self.lua.globals().mutations, 0)

    def test_recipe_discovery_is_independent_of_radius_and_vault_quantity(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A");vault.members[p.path]=true
            recipe_entries({entry(p,0)});box("Far",4500,{entry(p,7)})
        ''')
        near = self.scan()
        self.assertEqual(near['rows'][0]['count'], 0)
        self.assertEqual(near['rows'][0]['recipeState'], 'unlocked')
        self.run_lua('config.ship_radius_cm=5000')
        far = self.scan()
        self.assertEqual(far['rows'][0]['count'], 7)
        self.assertEqual(far['rows'][0]['recipeState'], 'unlocked')

    def test_hasitem_false_or_failure_is_not_overridden_by_vault_entry(self):
        for query, expected in (('return false', 'locked'), ('return nil', 'unknown'),
                                ('error("query failed")', 'unknown')):
            with self.subTest(query=query):
                self.setUp()
                self.run_lua('''
                    p=paint("DA_Paint_A","A");paints={};recipe_entries({entry(p,1)})
                    vault.HasItem=function() ''' + query + ' end')
                model = self.scan()
                self.assertEqual(model['rows'][0]['recipeState'], expected)
                self.assertEqual(model['recipeCatalogComplete'], expected != 'unknown')
                self.assertTrue(model['stockComplete'])

    def test_primary_id_fallback_uses_serialized_item_type_without_loading(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A");paints={};vault.members[p.path]=true
            recipe_entries({entry(nil,1,"DA_Paint_A")})
            library=obj("Default__KismetSystemLibrary","KismetSystemLibrary")
            runtime_objects["/Script/Engine.Default__KismetSystemLibrary"]=library
            primary_queries=0
            function library:GetObjectFromPrimaryAssetId(id)
                assert(id.PrimaryAssetType.Name=="Item")
                assert(id.PrimaryAssetName=="DA_Paint_A")
                primary_queries=primary_queries+1;return p
            end
        ''')
        model = self.scan()
        self.assertEqual(model['rows'][0]['recipeState'], 'unlocked')
        self.assertEqual(model['rows'][0]['count'], 0)
        self.assertTrue(model['recipeCatalogComplete'])
        self.assertEqual(self.lua.globals().primary_queries, 1)

    def test_missing_or_unloaded_primary_asset_is_explicit_unresolved_coverage(self):
        for asset in ('CustomAssetWithoutPaintName', 'DA_Paint_Missing'):
            with self.subTest(asset=asset):
                self.setUp()
                self.run_lua('recipe_entries({entry(nil,1,"' + asset + '")})')
                model = self.scan()
                self.assertFalse(model['recipeCatalogComplete'])
                self.assertFalse(model['complete'])
                self.assertTrue(model['stockComplete'])
                self.assertEqual(model['recipeEntriesUnresolved'], 1)
                self.assertEqual(model['unresolvedRecipes'][0]['assetName'], asset)
                self.assertEqual(model['rows'], [])
                self.assertEqual(model['physicalCount'], 0)

    def test_primary_lookup_unexpected_asset_name_is_rejected(self):
        self.run_lua('''
            p=paint("DA_Paint_Other","Other");paints={}
            recipe_entries({entry(nil,1,"DA_Paint_Wanted")})
            library=obj("Library","KismetSystemLibrary")
            runtime_objects["/Script/Engine.Default__KismetSystemLibrary"]=library
            function library:GetObjectFromPrimaryAssetId() return p end
        ''')
        model = self.scan()
        self.assertEqual(model['rows'], [])
        self.assertFalse(model['recipeCatalogComplete'])
        self.assertIn('unexpected asset name', model['unresolvedRecipes'][0]['reason'])

    def test_resolved_nonpaint_and_test_assets_never_become_recipe_paints(self):
        self.run_lua('''
            normal=obj("VoyageItem /Game/Item.Item","VoyageItem")
            test=paint("DA_Test_Paint_1","Test");paints={}
            recipe_entries({entry(normal,1),entry(test,1)})
        ''')
        model = self.scan()
        self.assertEqual(model['rows'], [])
        self.assertTrue(model['recipeCatalogComplete'])
        self.assertEqual(self.lua.globals().queries, 0)

    def test_vault_cap_marks_recipe_coverage_partial_without_changing_stock(self):
        self.run_lua('''
            config.max_slots=1
            p=paint("DA_Paint_A","A");q=paint("DA_Paint_B","B");paints={}
            vault.members[p.path]=true;vault.members[q.path]=true
            recipe_entries({entry(p,1),entry(q,1)})
        ''')
        model = self.scan()
        self.assertTrue(model['recipeEntriesTruncated'])
        self.assertFalse(model['recipeCatalogComplete'])
        self.assertTrue(model['stockComplete'])
        self.assertEqual(model['recipeEntriesVisited'], 2)
        self.assertEqual(len(model['rows']), 1)

    def test_recipe_paints_have_catalog_priority_and_cap_remains_visible(self):
        self.run_lua('''
            config.max_catalog=1
            locked=paint("DA_Paint_Locked","Locked")
            unlocked=paint("DA_Paint_Open","Open");vault.members[unlocked.path]=true
            recipe_entries({entry(unlocked,1)})
        ''')
        model = self.scan()
        self.assertEqual(model['rows'][0]['assetName'], 'DA_Paint_Open')
        self.assertTrue(model['catalogTruncated'])
        self.assertFalse(model['complete'])
        self.assertTrue(model['stockComplete'])
        self.run_lua('recipe_entries({entry(unlocked,1),entry(locked,1)})')
        capped = self.scan()
        self.assertFalse(capped['recipeCatalogComplete'])
        self.assertTrue(capped['stockComplete'])
        self.assertEqual(capped['recipeEntriesUnresolved'], 1)

    def test_failed_vault_traversal_does_not_make_physical_stock_incomplete(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A");vault.members[p.path]=true
            box("Near",1,{entry(p,4)})
            vault.Items.ForEach=function() error("Vault read failed") end
        ''')
        model = self.scan()
        self.assertEqual(model['physicalCount'], 4)
        self.assertTrue(model['stockComplete'])
        self.assertFalse(model['recipeCatalogComplete'])
        self.assertEqual(model['rows'][0]['recipeState'], 'unlocked')

    def test_duplicate_recipe_entries_do_not_duplicate_rows_or_membership_queries(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A");vault.members[p.path]=true
            recipe_entries({entry(p,1),entry(p,1)})
        ''')
        model = self.scan()
        self.assertEqual(len(model['rows']), 1)
        self.assertEqual(self.lua.globals().queries, 1)

    def test_vault_capture_and_each_recipe_resolve_are_separate_plain_units(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A");paints={};vault.members[p.path]=true
            recipe_entries({entry(p,1)})
            job=collection.begin(config);one_step();result=one_step()
            assert(job.phase=="recipe_library_resolve")
            assert(vault_reads==1 and queries==0 and no_native_handles(job))
            result=one_step()
            assert(queries==1 and no_native_handles(job) and result.model==nil)
            result=drain()
        ''')
        model = json.loads(self.run_lua('return json.encode(result.model)'))
        self.assertTrue(model['recipeCatalogComplete'])
        self.assertEqual(self.lua.eval('result.processed'), self.lua.eval('result.total'))

    def test_recipe_asset_disappearance_and_vault_replacement_are_guarded(self):
        self.run_lua('''
            p=paint("DA_Paint_A","A");paints={};recipe_entries({entry(p,1)})
            job=collection.begin(config);until_phase("recipe_library_resolve")
            p.valid=false;result=drain()
        ''')
        model = json.loads(self.run_lua('return json.encode(result.model)'))
        self.assertFalse(model['recipeCatalogComplete'])
        self.assertTrue(model['stockComplete'])
        self.setUp()
        self.run_lua('''
            job=collection.begin(config);until_phase("catalog_discovery")
            vault.address=vault.address+1;result=one_step()
        ''')
        self.assertEqual(self.lua.eval('result.state'), 'cancelled')
        self.assertIsNone(self.lua.eval('result.model'))

    def test_context_change_inside_vault_capture_cancels_before_publication(self):
        self.run_lua('''
            vault.Items.ForEach=function() pawn.address=pawn.address+1 end
            job=collection.begin(config);result=drain()
        ''')
        self.assertEqual(self.lua.eval('result.state'), 'cancelled')
        self.assertIsNone(self.lua.eval('result.model'))

    def test_vault_native_capture_overrun_stops_before_recipe_resolution(self):
        self.run_lua('''
            fake_time=0;os.clock=function() return fake_time end
            p=paint("DA_Paint_A","A");vault.members[p.path]=true
            vault.Items.ForEach=function(_,callback)
                fake_time=fake_time+0.050
                assert(callback(wrap(1),wrap(entry(p,1)))==nil)
                assert(queries==0)
            end
            job=collection.begin(config);one_step()
            result=job:step(budget_module.new(2,8))
            assert(result.phase=="recipe_library_resolve" and queries==0)
            assert(job.model.capture.overruns==1 and no_native_handles(job))
            result=drain()
        ''')
        model = json.loads(self.run_lua('return json.encode(result.model)'))
        stage = model['capture']['uninterruptibleStages'][0]
        self.assertIn('Vault.Items.ForEach', stage['operation'])
        self.assertGreaterEqual(stage['elapsedMs'], 50)


if __name__ == '__main__':
    unittest.main()
