"""Current-context catalog: demand, bounded preparation and zero paint-file I/O."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
json = require("paintmanager.json")
budget_module = require("paintmanager.work_budget")
engine_calls, worker_ops, starts, cancellations = 0, 0, {}, 0
admitted, native_generation, scope, token = true, 7, "scope-A", "pawn-A"
live_account,live_path,proof_reads="76561198012345678","C:/Save/76561198012345678/PaintManager.library.json",0
discovered, collectors, requests = {}, {}, {}
fail_catalog = false
package.loaded["paintmanager.save_lifecycle"] = {
    is_ready = function(t,s) return admitted and t==token and s==scope,native_generation end }
package.loaded["paintmanager.save_bridge"] = {storage_context=function(expected)
    proof_reads=proof_reads+1
    return {status="ready",contextToken=expected,scopeId=scope,loadGeneration=native_generation,
        accountId=live_account,libraryPath=live_path}
end}
package.loaded["paintmanager.background"] = setmetatable({}, {__index=function()
    error("Memory catalog touched task/file backend") end})
package.loaded["paintmanager.collection"] = {
    context=function() return token end,
    cache_entry=function(row)
        if not row.assetType or not row.assetName or not row.itemPath then return nil end
        return {assetType=row.assetType,assetName=row.assetName,itemPath=row.itemPath,
            name=row.displayName or row.name,iconPath=row.iconPath}
    end,
    begin_catalog=function(config, options)
        local job={state="running",phase="context",steps=0,options=options,seen={},
            model={status="catalog_prepared",contextToken=token,radiusCm=10000,rows=json.array(),
                stockComplete=false,readiness={verifiedRows=0,pendingRows=0,learnedRows=0}}}
        collectors[#collectors+1]=job
        function job:step(budget)
            if options.awaitingKnown then
                budget:consume(1);engine_calls=engine_calls+1
                if fail_catalog then self.state="error";return {state="error",error="native fixture failure"} end
                self.phase="catalog_wait"
                self.learned={status="learned_capture",contextToken=token,rows=json.array(),stockComplete=false}
                return {state="running",phase=self.phase,defer=true}
            end
            while self.state=="running" and not budget:expired() do
                budget:consume(1);engine_calls=engine_calls+1;self.steps=self.steps+1
                if fail_catalog then self.state="error";return {state="error",error="native fixture failure"} end
                local source=self.steps<=#options.entries and options.entries[self.steps]
                    or options.discover and discovered[self.steps-#options.entries]
                if source and #self.model.rows<(config.max_catalog or 128) then
                    if not self.seen[source.itemPath] then
                        self.seen[source.itemPath]=true
                        local row={id=source.itemPath,itemPath=source.itemPath,assetType=source.assetType,
                            assetName=source.assetName,displayName=source.name or "Native paint",
                            recipeState=source.pending and "unknown" or source.locked and "locked" or "unlocked",
                            cachePending=source.pending,count=0,stockSources=json.array(),
                            runtimeRef={name=source.itemPath,id="runtime-only"}}
                        self.model.rows[#self.model.rows+1]=row
                        if row.recipeState=="unlocked" then self.model.readiness.learnedRows=self.model.readiness.learnedRows+1 end
                    end
                elseif not source then self.state,self.phase="done","done" end
            end
            return {state=self.state,phase=self.phase}
        end
        function job:get_catalog() return self.state=="done" and self.model or nil end
        function job:get_learned_capture() return self.learned end
        function job:provide_known(entries,discover)
            assert(self.phase=="catalog_wait")
            options.entries,options.discover,options.awaitingKnown=entries,discover,false
            self.phase="discover"
        end
        function job:get_preview() return self.model end
        function job:get_progress() return {state=self.state,phase=self.phase} end
        function job:cancel() cancellations=cancellations+1;self.state="cancelled" end
        return job
    end }
cache_module=require("paintmanager.paint_cache")
config={pump_ms=16,max_catalog=128}
cache=cache_module.new(config,"25982825")
function event()
    return {contextToken=token,scopeId=scope,accountId=live_account,
        libraryPath=live_path,loadGeneration=native_generation}
end
function stable(i)
    return {assetType="Paint",assetName="DA_Paint_"..i,itemPath="/Game/Paints/DA_Paint_"..i..".DA_Paint_"..i,
        name="Native "..i,iconPath="/Game/Icons/T_Paint.T_Paint"}
end
function step(foreground,units,demanded)
    local budget=budget_module.new(100000,units or 1)
    cache:step(budget,foreground,demanded)
    assert(budget.units<=(units or 1),"controller exceeded supplied scheduler cap")
end
function until_ready()
    if not cache:is_busy() and not cache:catalog() then cache:begin_open() end
    for _=1,10000 do
        if cache:catalog() then return end
        step(true,4)
    end
    error("catalog did not become usable")
end
function until_idle()
    for _=1,10000 do
        if cache.phase=="idle" and not cache:is_busy() and not cache.observed then return end
        step(true,4)
    end
    error("catalog did not finish bounded work")
end
'''

class PaintCacheControllerTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def test_admission_and_idle_do_not_start_catalog_or_touch_disk(self):
        self.lua.execute('''
        assert(engine_calls==0 and #requests==0)
        admitted=false;assert(not cache:admit(event()) and cache:binding()==nil)
        admitted=true;assert(cache:admit(event()))
        for _=1,100 do step(false,8,false);step(true,8,false) end
        assert(engine_calls==0 and #collectors==0 and #requests==0)
        assert(cache:describe().state=="waiting_demand" and cache:describe().persistence=="session_only")
        ''')

    def test_demand_builds_catalog_once_without_any_file_or_task_backend(self):
        self.lua.execute('''
        local old_open,old_rename,old_remove=io.open,os.rename,os.remove
        io.open=function() error("paint file opened")end
        os.rename=function() error("paint file renamed")end
        os.remove=function() error("paint file removed")end
        PaintManagerWorker=setmetatable({}, {__index=function()error("DLL touched")end})
        discovered={stable(1),stable(2)};cache:admit(event());until_ready();until_idle()
        assert(#cache:catalog().rows==2 and #collectors==1)
        assert(cache:describe().backend=="lua_cooperative" and #requests==0)
        local calls=engine_calls
        for _=1,500 do step(true,8);step(false,8)end
        assert(engine_calls==calls and #collectors==1 and cache.path==nil)
        io.open,os.rename,os.remove=old_open,old_rename,old_remove
        ''')

    def test_close_cancels_catalog_and_idle_does_no_native_work(self):
        self.lua.execute('''
        for i=1,32 do discovered[i]=stable(i) end
        cache:admit(event());cache:begin_open();step(true,3,true)
        local job=collectors[1];assert(job and engine_calls==1)
        cache:quiesce();for _=1,300 do step(false,1,false) end
        assert(not cache:catalog() and job.state=="cancelled" and engine_calls==1 and #collectors==1)
        ''')

    def test_scope_generation_cancels_inflight_job_and_published_preview(self):
        self.lua.execute('''
        for i=1,20 do discovered[i]=stable(i) end
        cache:admit(event());cache:begin_open();step(true,3,true)
        native_generation=native_generation+1;step(true,8,true)
        assert(cache:binding()==nil and cache:catalog()==nil and cancellations==1)
        token,scope="pawn-B","scope-B";cache:admit(event());until_ready()
        assert(cache:catalog().contextToken==token and #collectors==2)
        ''')

    def test_account_drift_is_rejected_before_more_native_preparation(self):
        self.lua.execute('''
        for i=1,10 do discovered[i]=stable(i)end
        cache:admit(event());cache:begin_open();step(true,3,true);local before=engine_calls
        live_account="76561198087654321";step(true,8,true)
        assert(cache:binding()==nil and cache:catalog()==nil and engine_calls==before)
        assert(cache:describe().state=="error")
        ''')

    def test_new_open_rediscovers_after_the_first_completed_catalog(self):
        self.lua.execute('''
        discovered={stable(1)};cache:admit(event());until_ready()
        discovered[2]=stable(2)
        for _=1,100 do step(true,1) end
        assert(#cache:catalog().rows==1 and #collectors==1)
        cache:begin_open();assert(not cache:catalog())
        until_ready()
        assert(#cache:catalog().rows==2 and #collectors==2 and collectors[2].options.discover)
        ''')

    def test_completed_observation_adopts_memory_model_without_discovery(self):
        self.lua.execute('''
        cache:admit(event())
        local model={contextToken=token,status="captured",rows=json.array({stable(1)}),stockComplete=true}
        assert(cache:observe(model));step(false,1,false)
        assert(cache:catalog()==model and #collectors==0 and engine_calls==0)
        assert(not cache:observe({contextToken="old",status="captured",rows={}}))
        assert(not cache:observe({contextToken=token,status="preparing",rows={}}))
        ''')

    def test_box_advisory_seeds_wait_for_demand_and_are_sanitized_before_native_check(self):
        self.lua.execute('''
        cache:admit(event());local row=stable(4);row.count=999;row.recipeState="unlocked";row.runtimeRef={id="stale"}
        cache:seed_advisory({row})
        for _=1,20 do step(false,8,false)end
        assert(#collectors==0 and engine_calls==0)
        until_ready();until_idle()
        assert(#collectors==1 and #cache:catalog().rows==1)
        local entry=collectors[1].options.entries[1]
        assert(entry.count==nil and entry.recipeState==nil and entry.runtimeRef==nil)
        ''')

    def test_no_periodic_discovery_even_if_old_timer_fields_are_set(self):
        self.lua.execute('''
        discovered={stable(1)};cache:admit(event());until_ready();until_idle()
        discovered={stable(2)};cache.until_discovery=0
        for _=1,200 do step(false,8);step(true,8)end
        assert(#collectors==1 and #cache:catalog().rows==1)
        ''')

    def test_native_failure_releases_initial_binding_for_explicit_read_fallback(self):
        self.lua.execute('''
        fail_catalog=true;cache:admit(event());cache:begin_open();step(true,8,true)
        assert(cache:binding()==nil and cache:catalog()==nil and cache:describe().state=="error")
        assert(cache:describe().error=="native fixture failure")
        ''')

    def test_quiesce_cancels_readonly_work_without_waiting_for_an_imaginary_writer(self):
        self.lua.execute('''
        for i=1,20 do discovered[i]=stable(i)end
        cache:admit(event());cache:begin_open();step(true,3,true);local active=cache.job
        assert(not cache:reset_derived());assert(cache:quiesce())
        assert(active.state=="cancelled" and not cache:is_busy() and cache:reset_derived())
        assert(cache:catalog()==nil and cache.phase=="idle")
        ''')


class PaintCacheActualCollectionTests(unittest.TestCase):
    """The memory controller and actual collection agree on their native seam."""
    def setUp(self):
        from test_collection import FIXTURE as native_fixture
        from test_collection_jobs import JOB_FIXTURE
        from test_cache_collection import CACHE_FIXTURE
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path=SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(native_fixture+JOB_FIXTURE+CACHE_FIXTURE)
        self.lua.execute(r'''
        native_generation=7;scope="native-scope";admitted=true;native_token=collection.context()
        package.loaded["paintmanager.save_lifecycle"]={is_ready=function(t,s)
            return admitted and t==native_token and s==scope,native_generation end}
        package.loaded["paintmanager.save_bridge"]={storage_context=function(expected)
            return {status="ready",contextToken=expected,scopeId=scope,loadGeneration=native_generation,
                accountId="76561198012345678",libraryPath="C:/Save/76561198012345678/PaintManager.library.json"}
        end}
        package.loaded["paintmanager.background"]={supports=function()return false end}
        cache=require("paintmanager.paint_cache").new(config,"25982825")
        function admit()
            local ok=cache:admit({contextToken=native_token,scopeId=scope,loadGeneration=native_generation,
                accountId="76561198012345678",libraryPath="C:/Save/76561198012345678/PaintManager.library.json"})
            if ok then cache:begin_open() end
            return ok
        end
        function cache_step(units) cache:step(budget_module.new(100000,units or 1),true)end
        function cache_ready()
            for _=1,10000 do if cache:catalog()then return end;cache_step()end
            error(cache:describe().error or "native catalog did not finish")
        end
        ''')

    def test_native_catalog_then_refresh_rechecks_membership_without_repeating_vault_scan(self):
        self.lua.execute('''
        p=paint("DA_Paint_A","A");q=paint("DA_Paint_B","B")
        vault.members[p.path]=true;recipe_entries({entry(p,1)})
        box("Near",1,{entry(p,4)})
        assert(admit());cache_ready()
        assert(vault_reads==1 and #cache:catalog().rows==2 and inventory_reads==0)
        vault.members[p.path]=false
        job=collection.begin(config,{catalog=cache:catalog(),background=false})
        local result=drain();assert(result.state=="done",result.error)
        assert(vault_reads==1 and inventory_reads==1 and result.model.physicalCount==4)
        assert(result.model.rows[1].recipeState=="locked" and mutations==0)
        ''')

    def test_second_open_includes_newly_learned_and_loaded_paints_without_reading_boxes(self):
        self.lua.execute('''
        p=paint("DA_Paint_Learned","Learned");vault.members[p.path]=true
        recipe_entries({entry(p,1)});assert(admit());cache_ready()
        assert(#cache:catalog().rows==1 and vault_reads==1)
        q=paint("DA_Paint_New","New");vault.members[q.path]=true
        recipe_entries({entry(p,1),entry(q,1)})
        assert(admit());cache_ready()
        assert(#cache:catalog().rows==2 and vault_reads==2 and inventory_reads==0 and mutations==0)
        ''')

    def test_completed_vault_stage_precedes_even_the_seed_work_for_a_reopen(self):
        self.lua.execute('''
        local learned=paint("DA_Paint_Learned","Learned")
        local other=paint("DA_Paint_Other","Other")
        vault.members[learned.path]=true
        recipe_entries({entry(learned,1),entry(learned,1),entry(other,1)})
        assert(admit());cache_ready()
        assert(admit() and cache:learned_capture()==nil)
        local before=scans
        cache:step(budget_module.new(100000,100000))
        local first=assert(cache:learned_capture())
        assert(vault_reads==2 and scans==before and inventory_reads==0 and not cache:catalog())
        assert(cache:describe().defer and cache.seedWork.index==1 and #first.rows==1 and first.learnedOnly)
        local row=first.rows[1]
        assert(row.displayName=="Learned" and row.recipeState=="unlocked" and row.count==nil and not row.runtimeRef)
        assert(first.recipeCatalogComplete and not first.catalogComplete and not first.stockComplete)
        local bytes=json.encode(first)
        cache_ready()
        assert(#cache:catalog().rows==2 and scans==before+1 and vault_reads==2)
        assert(cache:learned_capture()==first and json.encode(first)==bytes)
        cache:quiesce();assert(not cache:learned_capture())
        ''')

    def test_pawn_change_during_catalog_releases_binding_without_old_rows(self):
        self.lua.execute('''
        p=paint("DA_Paint_A","A");assert(admit());cache_step()
        pawn.address=pawn.address+1;cache_step()
        assert(cache:binding()==nil and cache:catalog()==nil)
        assert(cache:describe().state=="error" and mutations==0)
        ''')

if __name__=="__main__":
    unittest.main()
