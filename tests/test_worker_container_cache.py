"""Advisory container-cache jobs, native-ID isolation and publication faults."""
import unittest

from lupa.lua54 import LuaRuntime

from test_local_library import FIXTURE, SCRIPTS


CACHE_FIXTURE = r'''
cache=require("paintmanager.worker_container_cache");ids=require("paintmanager.persistent_id")
current=true;reserve_allowed=true;reservations=0
control={fs=fs,temp_token="fixture-private-guid",allow_test_paths=true,is_current=function() return current end,
    reserve_commit=function() reservations=reservations+1;return reserve_allowed end}
path="PaintManager.container-cache.json"
function paint(id)
    return {assetType="VoyageItem",assetName=id or "DA_Paint_Blue",
        itemPath="VoyagePaintItem /Game/Items/Paints/DA_Paint_Blue.DA_Paint_Blue"}
end
function box(id,paints)
    return {persistentId=id or "ffffffffffffffff",boxKind="medium",lastCheckedAt=100,
        paints=paints or json.array({paint()})}
end
function request(boxes,ship,lineage)
    return {path=path,accountId="76561198000000001",gameBuild="25982825",sourcePolicyVersion=1,
        shipId=ship or "8000000000000001",lineageHint=lineage,boxes=boxes}
end
function run(kind,req)
    local job=cache.begin(kind,req or request(),control)
    for _=1,300000 do local p=job:step_one();if p.state~="running" then return job end end
    error("Container cache stalled")
end
function save(boxes,ship,lineage)
    local job=run("container_cache_write",request(boxes or json.array({box()}),ship,lineage))
    assert(job.state=="done",job.error);return job
end
function read(ship,lineage)
    local job=run("container_cache_read",request(nil,ship,lineage));assert(job.state=="done",job.error)
    return job:get_result()
end
function finish(job)
    for _=1,300000 do if job.state~="running" then return job end;job:step_one() end
    error("Container cache stalled")
end
'''


class WorkerContainerCacheTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + CACHE_FIXTURE)

    def test_private_token_is_required_before_any_write_and_determines_only_temp_path(self):
        self.lua.execute('''
        control.temp_token=nil
        local ok,err=pcall(cache.begin,"container_cache_write",request(json.array({box()})),control)
        assert(not ok and tostring(err):find("frozen private publication token",1,true))
        assert(#operations==0 and count_files()==0)
        control.temp_token="frozen-guid"
        local job=cache.begin("container_cache_write",request(json.array({box()})),control)
        assert(job.pending==path..".pending-frozen-guid")
        assert(#operations==0)
        finish(job);assert(job.state=="done",job.error)
        assert(files[path] and not files[path..".pending-frozen-guid"])
        ''')

    def test_cold_warm_counts_absent_and_native_ids_remain_strings(self):
        self.lua.execute('''
        local cold=read();assert(cold.status=="missing" and #cold.boxes==0 and reservations==0)
        assert(not files[path] and #operations==2)
        save(json.array({box(),box("0000000000000001",json.array({paint("DA_Paint_Red")}))}))
        local result=read();assert(result.status=="hit" and result.namespaceMatched)
        assert(result.advisoryOnly and not result.boxMappingUsable and not result.restoredCounts)
        assert(not result.lineageVerified and result.writeAllowed and #result.paintSeeds==2)
        assert(#result.boxes==2 and result.boxes[1].persistentId=="0000000000000001")
        assert(result.boxes[2].persistentId=="ffffffffffffffff" and result.boxes[2].paints[1].count==nil)
        assert(not files[path]:find('"count"',1,true) and not files["library.json"])
        for _,n in ipairs(read_sizes) do assert(n<=8192) end
        for _,n in ipairs(write_sizes) do assert(n<=8192) end
        ''')

    def test_unqualified_namespace_mismatch_only_returns_paint_seeds(self):
        self.lua.execute('''
        save(nil,nil,"unverified-world-A")
        local same=read(nil,"unverified-world-A");assert(same.namespaceMatched and not same.lineageVerified)
        for _,entry in ipairs({{ship="8000000000000002",lineage="unverified-world-A"},
            {ship="8000000000000001",lineage="unverified-world-B"},{ship="8000000000000001"}}) do
            local result=read(entry.ship,entry.lineage)
            assert(result.status=="paint_seeds_only" and not result.namespaceMatched)
            assert(#result.boxes==0 and #result.paintSeeds==1 and not result.restoredCounts)
        end
        ''')

    def test_build_policy_and_account_mismatch_never_seed_other_scope(self):
        self.lua.execute('''
        save();local raw=files[path]
        for _,entry in ipairs({{"25982825","11111111","stale_build"},
            {'"sourcePolicyVersion":1','"sourcePolicyVersion":2',"stale_policy"},
            {"76561198000000001","76561198000000002","stale_account"}}) do
            files[path]=raw:gsub(entry[1],entry[2]);local result=read()
            assert(result.status==entry[3] and #result.boxes==0 and #result.paintSeeds==0)
        end
        ''')

    def test_future_unknown_fields_preserved_before_backup_or_publication(self):
        self.lua.execute('''
        save();local raw=files[path]
        local future=raw:gsub('"schemaVersion":1','"schemaVersion":9,"futureField":true')
        files[path]=future;local result=read()
        assert(result.status=="stale_schema" and not result.writeAllowed and #result.paintSeeds==0)
        local job=save();assert(job:get_result().status=="preserved_incompatible")
        assert(not job:get_result().writeAllowed and not job.local_committed and files[path]==future)
        files[path]=raw;files[path..".backup"]=future
        job=save();assert(job:get_result().status=="preserved_incompatible")
        assert(files[path]==raw and files[path..".backup"]==future and reservations==1)
        ''')

    def test_corrupt_primary_recovers_without_read_side_effects(self):
        self.lua.execute('''
        save();local raw=files[path];files[path..".backup"]=raw;files[path]="broken"
        local result=read();assert(result.status=="recovered" and #result.boxes==1)
        assert(files[path]=="broken" and files[path..".backup"]==raw)
        files[path]=nil;result=read();assert(result.status=="recovered" and not files[path])
        files[path..".backup"]=nil
        for _,bad in ipairs({'broken','{"format":"PaintManager.ContainerCache","format":"duplicate"}',
            '[]','{"format":"PaintManager.ContainerCache","schemaVersion":1,"boxes":{}}'}) do
            files[path]=bad;result=read();assert(#result.boxes==0 and #result.paintSeeds==0)
            assert(files[path]==bad)
        end
        ''')

    def test_rejects_stock_permissions_runtime_fields_invalid_ids_and_paths_before_io(self):
        self.lua.execute('''
        for _,field in ipairs({"count","runtimeRef","inventoryPath","address","recipeKnown","worldKey","unlocked"}) do
            local row=box();row[field]=true;local before=#operations
            local job=run("container_cache_write",request(json.array({row})))
            assert(job.state=="error" and #operations==before and reservations==0)
            local entry=paint();entry[field]=true
            job=run("container_cache_write",request(json.array({box(nil,json.array({entry}))})))
            assert(job.state=="error" and #operations==before)
        end
        for _,id in ipairs({"0000000000000000","FFFFFFFFFFFFFFFF","18446744073709551615","Box_12",false,9007199254740993}) do
            local row=box();row.persistentId=id
            assert(run("container_cache_write",request(json.array({row}))).state=="error")
        end
        local entry=paint();entry.itemPath="/Game/Maps/Map.Map:PersistentLevel.Box"
        assert(run("container_cache_write",request(json.array({box(nil,json.array({entry}))}))).state=="error")
        assert(#operations==0 and not files[path])
        ''')

    def test_positive_cache_limits_duplicates_and_conflicting_asset_paths(self):
        self.lua.execute('''
        for _,rows in ipairs({json.array({box(),box()}),json.array({box(nil,json.array())}),
            json.array({box(nil,json.array({paint(),paint()}))})}) do
            assert(run("container_cache_write",request(rows)).state=="error")
        end
        local rows=json.array();for i=1,129 do rows[i]=box(ids.encode(i)) end
        assert(run("container_cache_write",request(rows)).state=="error")
        local changed=paint();changed.itemPath="/Game/Items/Paints/Other.Other"
        assert(run("container_cache_write",request(json.array({box(),box("0000000000000001",json.array({changed}))}))).state=="error")
        assert(#operations==0)
        save(json.array());assert(#read().boxes==0 and #read().paintSeeds==0)
        ''')

    def test_path_is_frozen_to_account_and_only_owned_derived_filename(self):
        self.lua.execute('''
        control.allow_test_paths=false
        for _,bad in ipairs({"C:/s/76561198000000001/PaintManager.library.json",
            "C:/s/76561198000000001/Save.sav","C:/s/76561198000000002/PaintManager.container-cache.json",
            "C:/s/../76561198000000001/PaintManager.container-cache.json"}) do
            local req=request();req.path=bad;assert(not pcall(cache.begin,"container_cache_read",req,control))
        end
        assert(#operations==0)
        ''')

    def test_scope_loss_before_commit_cleans_pending_and_retains_previous(self):
        self.lua.execute('''
        save();local raw=files[path];reservations=0
        local job=cache.begin("container_cache_write",request(json.array({box("0000000000000001")})),control)
        reach(job,"write_reserve");current=false;job:step_one()
        assert(job.state=="error" and reservations==0 and files[path]==raw and not files[job.pending])
        ''')

    def test_reserved_frozen_publication_finishes_after_cancellation(self):
        self.lua.execute('''
        save();local raw=files[path]
        local job=cache.begin("container_cache_write",request(json.array({box("0000000000000001")})),control)
        reach(job,"write_rotate");assert(job.commit_reserved)
        current=false;job:cancel("load changed");finish(job)
        assert(job.state=="done" and job.local_committed and files[path..".backup"]==raw)
        current=true;assert(read().boxes[1].persistentId=="0000000000000001")
        ''')

    def test_revoked_write_flush_and_publish_failure_recover_prior_copy(self):
        self.lua.execute('''
        save();local raw=files[path];reserve_allowed=false
        local job=run("container_cache_write",request(json.array({box("0000000000000001")})))
        assert(job.state=="error" and files[path]==raw and not files[job.pending])
        reserve_allowed=true;fail_flush=true
        job=run("container_cache_write",request(json.array({box("0000000000000001")})))
        assert(job.state=="error" and files[path]==raw and not files[job.pending])
        fail_flush=false;local rename=fs.rename
        fs.rename=function(source,target)
            if source:find(".pending-",1,true) and target==path then return nil,"disk failure" end
            return rename(source,target)
        end
        job=run("container_cache_write",request(json.array({box("0000000000000001")})))
        assert(job.state=="error" and not job.local_uncertain and files[path]==raw and not files[job.pending])
        ''')

    def test_uncertain_publication_keeps_recoverable_backup(self):
        self.lua.execute('''
        save();local raw=files[path];local rename=fs.rename
        fs.rename=function(source,target)
            if target==path then return nil,"disk failure" end
            return rename(source,target)
        end
        local job=run("container_cache_write",request(json.array({box("0000000000000001")})))
        assert(job.state=="error" and job.local_uncertain and not files[path] and files[path..".backup"]==raw)
        assert(not files[job.pending]);fs.rename=rename;assert(read().status=="recovered")
        ''')

    def test_worker_module_has_no_engine_or_arbitrary_task_execution(self):
        source = (SCRIPTS / "paintmanager/worker_container_cache.lua").read_text(encoding="utf-8")
        for forbidden in ("FindAllOf", "StaticFindObject", "FindFirstOf", ":IsA(", "HasItem", "load(", "loadfile", "dofile"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
