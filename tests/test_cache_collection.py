"""Actual game-thread catalog preparation, cache reuse and native boundaries."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE
from test_worker_analysis import MOCK


CACHE_FIXTURE = r'''
local original_paint=paint
function paint(id,label,path)
    local value=original_paint(id,label,path)
    value.Type={Name=text("Item")}
    return value
end
function stable(item)
    return {assetType="Item",assetName=item:GetFName():ToString(),
        itemPath=item.path:match("^[^ ]+ (/.+)$"),name="Cached label"}
end
library=obj("Default__KismetSystemLibrary","KismetSystemLibrary")
runtime_objects["/Script/Engine.Default__KismetSystemLibrary"]=library
primary_queries=0
function library:GetObjectFromPrimaryAssetId(id)
    primary_queries=primary_queries+1
    for _,item in ipairs(paints) do
        if item.valid and item.Type.Name:ToString()==id.PrimaryAssetType.Name
            and item:GetFName():ToString()==id.PrimaryAssetName then return item end
    end
end
function finish_catalog(options)
    job=collection.begin_catalog(config,options)
    result=drain()
    assert(result.state=="done",result.error)
    return job:get_catalog()
end
'''


class CacheCollectionTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + JOB_FIXTURE + CACHE_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def test_factory_does_no_engine_work_and_catalog_never_scans_boxes(self):
        self.run_lua('''
        p=paint("DA_Paint_A","Native A");box("Near",1,{entry(p,4)})
        job=collection.begin_catalog(config)
        assert(scans+queries+inventory_reads+context_reads+vault_reads==0)
        result=drain();assert(result.state=="done",result.error)
        assert(scans==1 and vault_reads==1 and inventory_reads==0 and mutations==0)
        local model=job:get_catalog()
        assert(#model.rows==1 and model.rows[1].count==0 and not model.stockComplete)
        assert(model.status=="catalog_prepared" and no_native_handles(job))
        ''')

    def test_learned_vault_entries_are_prepared_before_cached_locked_paints(self):
        self.run_lua('''
        p=paint("DA_Paint_A","Native A");vault.members[p.path]=true
        q=paint("DA_Paint_Locked","Locked")
        recipe_entries({entry(p,1)})
        job=collection.begin_catalog(config,{entries={stable(q)},discover=false})
        one_step();assert(job.phase=="recipe_library_read" and queries==0)
        one_step();assert(job.phase=="recipe_library_resolve" and vault_reads==1)
        one_step();local preview=job:get_preview()
        assert(preview.rows[1].displayName=="Native A" and preview.rows[1].recipeState=="unlocked")
        assert(scans==0 and inventory_reads==0 and queries==1)
        assert(preview.readiness.learnedRows==1 and not preview.readiness.complete)
        assert(preview.readiness.exhaustive==false and not preview.stockComplete)
        result=drain();assert(result.state=="done" and vault_reads==1 and queries==2)
        ''')

    def test_explicit_readonly_vault_stage_without_account_binding_yields_even_in_sync_mode(self):
        self.run_lua('''
        p=paint("DA_Paint_A","Native A");vault.members[p.path]=true
        paint("DA_Paint_Locked","Other")
        recipe_entries({entry(p,1),{AssetType={Name=text("Item")},AssetName=text("Missing")}})
        job=collection.begin_catalog(config,{learnedFirst=true,contextToken=collection.context()})
        local sync=budget_module.new(100000,1,nil,{batching_enabled=false})
        local first=job:step(sync)
        local display=assert(job:get_learned_capture())
        assert(first.state=="running" and first.defer and job:get_catalog()==nil)
        assert(scans==0 and inventory_reads==0 and vault_reads==1 and queries==1)
        assert(#display.rows==1 and display.learnedOnly and not display.stockComplete)
        assert(not display.recipeCatalogComplete and no_native_handles(display))
        local snapshot=json.encode(display)
        assert(job:step(sync).state=="done" and #job:get_catalog().rows==2 and scans==1)
        assert(snapshot==json.encode(display) and mutations==0)
        ''')

    def test_locked_paint_survives_false_membership_and_missing_assets_stay_unknown(self):
        self.run_lua('''
        p=paint("DA_Paint_Locked","Native locked")
        missing={assetType="Item",assetName="DA_Paint_Missing",itemPath="/Game/Paints/DA_Paint_Missing.DA_Paint_Missing",name="Cached missing"}
        model=finish_catalog({entries={stable(p),missing},discover=false})
        assert(#model.rows==2 and model.rows[1].recipeState=="locked")
        assert(model.rows[2].recipeState=="unknown" and model.rows[2].cachePending)
        assert(not model.rows[2].runtimeRef and not model.recipeCatalogComplete)
        assert(queries==1 and vault_reads==1 and scans==0 and mutations==0)
        local values=job:get_cache_entries()
        assert(#values==2 and values[2].assetName=="DA_Paint_Missing")
        ''')

    def test_unloaded_paint_does_not_restore_a_name_from_another_game_language(self):
        self.run_lua('''
        local languages=require("paintmanager.languages")
        assert(languages.observe_game("ru"))
        local hint={assetType="Item",assetName="DA_Paint_Missing",itemPath="/Game/Paints/DA_Paint_Missing.DA_Paint_Missing",
            name="English name",paintNameLanguage="en"}
        local model=finish_catalog({entries={hint},discover=false})
        assert(model.paintNameRevision==languages.game_revision and model.rows[1].displayName==nil)
        assert(model.rows[1].cachePending and model.rows[1].recipeState=="unknown")
        ''')

    def test_pending_row_resolves_later_without_mutating_published_row(self):
        self.run_lua('''
        p=paint("DA_Paint_A","Native A");local record=stable(p);p.valid=false
        job=collection.begin_catalog(config,{entries={record}})
        until_phase("known_paint_verify");one_step();local old=job:get_preview().rows[1]
        assert(old.cachePending and old.recipeState=="unknown")
        p.valid=true;result=drain();assert(result.state=="done",result.error)
        local row=job:get_catalog().rows[1]
        assert(#job:get_catalog().rows==1 and row~=old and row.recipeState=="locked")
        assert(old.cachePending and old.recipeState=="unknown" and old.runtimeRef==nil)
        assert(job:get_preview().readiness.pendingRows==0)
        assert(job:get_preview().readiness.verifiedRows==1)
        ''')

    def test_native_primary_identity_path_and_type_are_checked(self):
        for change in ('record.assetType="Other"',
                       'record.itemPath="/Game/Wrong/DA_Paint_A.DA_Paint_A"',
                       'record.assetName="DA_Paint_Other"'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('''
                p=paint("DA_Paint_A","Native A");record=stable(p)
                ''' + change + '''
                model=finish_catalog({entries={record},discover=false})
                assert(#model.rows==1 and model.rows[1].recipeState=="unknown")
                assert(model.rows[1].cachePending and queries==0 and mutations==0)
                ''')

    def test_nonpaint_or_test_object_cannot_be_accepted_as_cached_paint(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");local a=stable(p);p.kind="VoyageItem"
        q=paint("DA_Test_Paint_1","Test");local b=stable(q)
        model=finish_catalog({entries={a,b},discover=false})
        assert(#model.rows==2 and model.rows[1].cachePending and model.rows[2].cachePending)
        assert(queries==0 and mutations==0)
        ''')

    def test_cold_discovery_filters_nonpaint_vault_entries_and_deduplicates(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A")
        normal=obj("VoyageItem /Game/Item.Item","VoyageItem")
        local records={entry(normal,1),entry(p,1),entry(p,2)}
        for i=1,253 do records[#records+1]=entry(normal,1) end
        recipe_entries(records)
        model=finish_catalog()
        assert(model.recipeEntriesVisited==256 and #model.rows==1 and queries==1)
        assert(model.rows[1].recipeState=="locked" and not model.stockComplete)
        assert(#job:get_cache_entries()==1 and mutations==0 and inventory_reads==0)
        ''')

    def test_cache_projection_never_contains_runtime_stock_or_unlock_authority(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");vault.members[p.path]=true
        local model=finish_catalog()
        model.rows[1].count=999;model.rows[1].unsafe="ignored"
        local entry=collection.cache_entry(model.rows[1])
        assert(entry.assetType=="Item" and entry.assetName=="DA_Paint_A")
        assert(entry.itemPath==p.path:match("^[^ ]+ (/.+)$"))
        assert(entry.name=="A" and entry.iconPath:sub(1,6)=="/Game/")
        for key in pairs(entry) do
            assert(key=="assetType" or key=="assetName" or key=="itemPath" or key=="name" or key=="iconPath")
        end
        assert(collection.cache_entry({assetName="No type",itemPath="/Game/X.X"})==nil)
        ''')

    def test_recipe_query_error_remains_unknown(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");vault.HasItem=function() error("unavailable") end
        model=finish_catalog({entries={stable(p)},discover=false})
        assert(model.rows[1].recipeState=="unknown" and not model.recipeCatalogComplete)
        assert(model.rows[1].runtimeRef and not model.rows[1].cachePending)
        ''')

    def test_world_pawn_and_vault_switch_cancel_each_native_warmup(self):
        for change in ('world.address=world.address+1', 'pawn.address=pawn.address+1',
                       'vault.address=vault.address+1'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('''
                p=paint("DA_Paint_A","A")
                job=collection.begin_catalog(config,{entries={stable(p)},discover=false});one_step()
                ''' + change + '''
                one_step();assert(result.state=="cancelled" and job:get_catalog()==nil)
                assert(queries==0 and inventory_reads==0 and no_native_handles(job))
                ''')

    def test_expected_context_rejects_new_pawn_before_the_first_unit(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");local token=collection.context()
        job=collection.begin_catalog(config,{entries={stable(p)},contextToken=token})
        pawn.address=pawn.address+1
        one_step();assert(result.state=="cancelled" and job:get_catalog()==nil)
        assert(queries==0 and scans==0 and vault_reads==0 and inventory_reads==0)
        assert(job.knownRecords==nil and mutations==0 and no_native_handles(job))
        ''')

    def test_cancelled_warmup_releases_candidates_without_engine_mutation(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");job=collection.begin_catalog(config,{entries={stable(p)}})
        one_step();job:cancel("load generation retired");one_step()
        assert(result.state=="cancelled" and job:get_catalog()==nil and mutations==0 and queries==0)
        ''')

    def test_catalog_warmup_ignores_radius_changes_and_keeps_its_cursor(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");q=paint("DA_Paint_B","B")
        job=collection.begin_catalog(config,{entries={stable(p),stable(q)},discover=false})
        until_phase("known_paint_verify");one_step();assert(queries==1 and job.knownIndex==2)
        config.ship_radius_cm=4500
        result=drain();assert(result.state=="done",result.error)
        assert(#job:get_catalog().rows==2 and queries==2 and inventory_reads==0)
        assert(scans==0 and vault_reads==1 and mutations==0)
        ''')

    def test_refresh_reuses_catalog_without_reenumerating_vault_and_rechecks_membership(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");recipe_entries({entry(p,1)})
        prepared=finish_catalog();local before_reads,before_scans=vault_reads,scans
        assert(prepared.rows[1].recipeState=="locked")
        vault.members[p.path]=true;box("Near",1,{entry(p,4)})
        job=collection.begin(config,{catalog=prepared,background=false});result=drain()
        assert(result.state=="done",result.error)
        assert(result.model.rows[1].recipeState=="unlocked" and result.model.physicalCount==4)
        assert(vault_reads==before_reads and scans==before_scans+1)
        assert(prepared.rows[1].recipeState=="locked" and prepared.rows[1].count==0)
        local refreshed=result.model
        job=collection.begin(config,{catalog=refreshed,background=false});result=drain()
        assert(result.state=="done" and vault_reads==before_reads and scans==before_scans+2)
        ''')

    def test_refresh_rejects_catalog_from_another_character(self):
        self.run_lua('''
        paint("DA_Paint_A","A");prepared=finish_catalog()
        pawn.address=pawn.address+1
        job=collection.begin(config,{catalog=prepared,background=false});one_step()
        assert(result.state=="error" and result.model==nil and inventory_reads==0)
        ''')

    def test_late_loaded_paint_is_discovered_by_a_later_bounded_catalog_pass(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");local first=finish_catalog()
        q=paint("DA_Paint_B","B");vault.members[q.path]=true
        local second=finish_catalog({entries=collection.cache_entries(first)})
        assert(#first.rows==1 and #second.rows==2 and second.rows[2].recipeState=="unlocked")
        assert(inventory_reads==0 and mutations==0)
        ''')

    def test_prepared_refresh_discovers_new_container_paints(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");local prepared=finish_catalog()
        q=paint("DA_Paint_B","B");paints={p};box("Near",1,{entry(q,4)})
        job=collection.begin(config,{catalog=prepared,background=false});result=drain()
        assert(result.state=="done" and #result.model.rows==2 and result.model.physicalCount==4)
        assert(#collection.cache_entries(result.model)==2 and mutations==0)
        ''')

    def test_current_open_catalog_is_consumed_once_with_old_hints_off_and_preserves_partial_coverage(self):
        self.run_lua('''
        local processing=require("paintmanager.processing")
        assert(processing.request({paint_hints_enabled=false}));assert(processing.commit())
        local p=paint("DA_Paint_A","Learned");vault.members[p.path]=true
        recipe_entries({entry(p,1),{AssetType={Name=text("Item")},AssetName=text("Missing")}})
        local catalog=finish_catalog({contextToken=collection.context(),learnedFirst=true})
        assert(not catalog.recipeCatalogComplete and vault_reads==1 and scans==1)
        box("Near",1,{entry(p,2)})
        job=collection.begin(config,{catalog=catalog,openCatalog=true,background=false});local result=drain()
        assert(result.state=="done" and result.model.preparedCatalog and not result.model.complete)
        assert(result.model.stockComplete and not result.model.recipeCatalogComplete and result.model.physicalCount==2)
        assert(vault_reads==1 and scans==2 and inventory_reads==1)
        job=collection.begin(config,{catalog=catalog,background=false});result=drain()
        assert(result.state=="done" and not result.model.preparedCatalog and vault_reads==2)
        ''')

    def test_transport_checks_native_context_once_per_slice_and_on_result_acceptance(self):
        self.run_lua(MOCK)
        self.run_lua('''
        paint("DA_Paint_A","A");job=collection.begin(config);until_phase("collection_worker")
        analysis_block=true;local before=context_reads
        job:step(budget_module.new(10000,8))
        assert(context_reads-before==1 and job.state=="running")
        pawn.address=pawn.address+1;one_step()
        assert(result.state=="cancelled" and analysis_jobs[#analysis_jobs].cancelled)
        ''')

    def test_worker_cache_read_can_supply_known_entries_without_second_vault_capture(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");vault.members[p.path]=true
        q=paint("DA_Paint_B","B");recipe_entries({entry(p,1)})
        job=collection.begin_catalog(config,{awaitingKnown=true,discover=false})
        until_phase("catalog_wait")
        assert(vault_reads==1 and #job:get_preview().rows==1 and inventory_reads==0)
        job:provide_known({stable(p),stable(q)},true)
        result=drain();assert(result.state=="done",result.error)
        assert(vault_reads==1 and scans==1 and queries==2 and #job:get_catalog().rows==2)
        local entries=job:get_cache_entries()
        job=collection.begin_catalog(config,{entries=entries,discover=true,skipRecipes=true})
        result=drain();assert(result.state=="done" and vault_reads==1 and queries==4)
        ''')

    def test_lean_ui_capture_omits_source_presentation_but_preserves_stock_witnesses(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");box("Secret label",1,{entry(p,2)});box("Another",2,{entry(p,3)})
        job=collection.begin(config,{presentation="paints",background=false});result=drain()
        assert(result.state=="done",result.error)
        local model=result.model;local row=model.rows[1]
        assert(model.physicalCount==5 and model.stockComplete and row.count==5)
        assert(#model.sources==0 and #row.stockSources==0 and model.stockSourcesOmitted)
        assert(not model.stockSourcesComplete and #row.stockWitnesses==2)
        assert(row.stockWitnesses[1].componentRef and row.stockWitnesses[1].ownerRef)
        assert(no_native_handles(job) and mutations==0)
        ''')

    def test_progress_uses_frozen_phase_denominators_and_unknown_discovery(self):
        self.run_lua('''
        p=paint("DA_Paint_A","A");box("One",1,{entry(p,2)});box("Two",2,{entry(p,3)})
        job=collection.begin(config,{presentation="paints",background=false})
        until_phase("source_discovery")
        local progress=job:get_progress().presentation
        assert(progress.mode=="indeterminate" and progress.total==nil)
        until_phase("container_read")
        progress=job:get_progress().presentation
        assert(progress.mode=="determinate" and progress.total==2 and progress.completed==0 and progress.unit=="containers")
        one_step();local total=job:get_progress().presentation.total
        assert(total==2)
        result=drain();assert(result.state=="done")
        assert(result.presentation.mode=="idle" and result.presentation.coverage=="complete")
        ''')


if __name__ == "__main__":
    unittest.main()
