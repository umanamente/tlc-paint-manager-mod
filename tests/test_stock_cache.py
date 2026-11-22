"""Actual stock controller: freshness revocation, scope-bound disk retry/order."""
import unittest

from lupa.lua54 import LuaRuntime

from test_local_library import SCRIPTS


FIXTURE = r'''
json=require("paintmanager.json");budgets=require("paintmanager.work_budget")
live_scope={token="ship-A",shipId="8000000000000001",radiusCm=5000,sourcePolicyVersion=1}
ship_available,proof_ok,worker_available=true,true,true
clock,proof_reads,scope_reads,cancellations=0,0,0,0
workers,requests,collectors={},{},{}
worker_delay,fail_reads,fail_writes=2,0,0
capture_stalled=false
read_result={status="hit",writeAllowed=true,paintSeeds=json.array({{assetType="VoyageItem",assetName="DA_Cached"}}),
    boxes=json.array({{count=999999}}),advisoryOnly=true,restoredCounts=false}
write_result={status="written",writeAllowed=true}
function event(account)
    account=account or "76561198000000001"
    return {baseScopeId="world/controller/state/hud",contextToken="character-A",scopeId="scope-A",
        accountId=account,libraryPath="C:/Save/"..account.."/PaintManager.library.json",loadGeneration=7}
end
function model(scope,count,id)
    scope=scope or live_scope
    return {stockScope=scope,contextToken="character-A",radiusCm=10000,stockComplete=true,complete=true,
        rows=json.array({{id="paint-blue",itemPath="/Game/Items/DA_Blue.DA_Blue",assetType="VoyageItem",
            assetName="DA_Blue",displayName="Blue",recipeState="unlocked",count=count or 4,complete=true,
            runtimeRef={id="runtime-paint"}}}),physicalCount=count or 4,
        boxCache=json.array({{persistentId=id or "ffffffffffffffff",boxKind="large",lastCheckedAt=100,
            paints=json.array({{assetType="VoyageItem",assetName="DA_Blue",itemPath="/Game/Items/DA_Blue.DA_Blue"}})}})}
end
package.loaded["paintmanager.ship_scope"]={current=function(_,expected)
    scope_reads=scope_reads+1
    if not ship_available then return nil,"ship unavailable","ship_unavailable" end
    if expected and expected.token~=live_scope.token then return nil,"scope changed","ship_scope_changed" end
    return live_scope
end}
guid_calls=0;guid_failure=false
package.loaded["paintmanager.save_bridge"]={new_id=function(token)
    assert(token=="character-A");guid_calls=guid_calls+1
    if guid_failure then return nil,"UUID unavailable" end
    return "fixture-guid-"..guid_calls
end,stock_context=function(bound)
    proof_reads=proof_reads+1
    return {status=proof_ok and "ready" or "unavailable",error=proof_ok and nil or "load revoked"}
end}
package.loaded["paintmanager.background"]={supports=function()return worker_available,"worker unavailable"end,
    begin_task=function(kind,request,options)
        requests[#requests+1]={kind=kind,request=request,options=options}
        local job={kind=kind,request=request,state="running",steps=0}
        workers[#workers+1]=job
        function job:step(budget)
            self.steps=self.steps+1;budget:consume()
            if self.steps>=worker_delay then
                if kind=="container_cache_read" and fail_reads>0 then
                    fail_reads=fail_reads-1;self.state="error"
                elseif kind=="container_cache_write" and fail_writes>0 then
                    fail_writes=fail_writes-1;self.state="error"
                else self.state="done";self.result=kind=="container_cache_read" and read_result or write_result end
            end
            return {state=self.state,error=self.state=="error" and "temporary disk failure" or nil}
        end
        function job:get_result() return self.result end
        function job:cancel() self.state="cancelled";cancellations=cancellations+1 end
        return job
    end}
package.loaded["paintmanager.collection"]={context=function()return "character-A"end,
    begin=function(_,options)
        local job={options=options,state="running",phase="container_read",steps=0}
        collectors[#collectors+1]=job
        function job:step(budget)
            self.steps=self.steps+1;budget:consume()
            if not capture_stalled and self.steps>=3 then self.state="done" end
            return {state=self.state,phase=self.phase,model=self.state=="done" and model(options.shipScope) or nil}
        end
        function job:get_registry()return {"current-native-descriptor"}end
        function job:get_stock_preview()return self.partial,self.partialRevision end
        function job:cancel()self.state="cancelled";cancellations=cancellations+1 end
        return job
    end}
module=require("paintmanager.stock_cache")
config={context_poll_ms=0,stock_idle_callbacks=4,stock_idle_inventory_ms=250,
    stock_refresh_ms=100000,stock_discovery_ms=30000,stock_write_delay_ms=20,stock_disk_retry_ms=100}
cache=module.new(config,"25982825")
function step(foreground,units,advance)
    clock=clock+(advance or 10);local budget=budgets.new(100000,units or 8)
    cache:step(budget,foreground~=false,nil,clock)
    cache:settle_output(budget,clock)
    assert(budget.units<=(units or 8),"controller exceeded caller operation cap")
end
function open_cache()
    assert(cache:admit(event()));return cache:refresh()
end
function until_true(predicate)
    for _=1,3000 do if predicate() then return end;step(true) end
    error("stock fixture stalled")
end
function count(kind)
    local n=0;for _,value in ipairs(requests)do if value.kind==kind then n=n+1 end end;return n
end
function last_request(kind)
    for i=#requests,1,-1 do if requests[i].kind==kind then return requests[i].request end end
end
'''


class StockCacheTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def test_partial_interval_is_terminal_until_another_opening(self):
        self.lua.execute('''
        open_cache();until_true(function()return cache:capture()~=nil end)
        local partial=model();partial.stockComplete=false
        cache:observe(partial)
        local revision=cache.revision
        assert(cache:get_progress().state=="partial")
        assert(cache:get_progress().presentation.coverage=="partial")
        for i=1,100 do step(i%2==0,8,100000) end
        assert(#collectors==1 and cache.revision==revision and cache:capture()==partial)
        ''')

    def test_private_guid_is_frozen_once_per_write_admission_only(self):
        self.lua.execute('''
        open_cache();until_true(function()return count("container_cache_write")==1 end)
        assert(guid_calls==1)
        for _,entry in ipairs(requests) do
            if entry.kind=="container_cache_read" then assert(entry.options.temp_token==nil)
            else assert(entry.options.temp_token=="fixture-guid-1") end
        end
        local admitted=workers[#workers];step(true)
        assert(guid_calls==1 and admitted==workers[#workers])
        ''')

    def test_unavailable_guid_preserves_snapshot_and_backs_off_without_write(self):
        self.lua.execute('''
        guid_failure=true;open_cache();until_true(function()return guid_calls==1 end)
        assert(count("container_cache_write")==0 and cache.pendingSnapshot)
        assert(cache.diskStatus:find("cache_token_unavailable",1,true))
        for _=1,4 do step(true,8,1) end
        assert(guid_calls==1 and count("container_cache_write")==0)
        guid_failure=false;until_true(function()return count("container_cache_write")==1 end)
        assert(guid_calls==2 and not cache.pendingSnapshot)
        ''')

    def test_no_unadmitted_work_and_disk_counts_never_become_capture(self):
        self.lua.execute('''
        step(true);assert(proof_reads==0 and scope_reads==0 and #requests==0)
        assert(not cache:admit({}) and not cache:binding())
        assert(cache:admit(event()));capture_stalled=true
        for i=1,40 do step(i%2==0,1,60000) end
        assert(proof_reads==0 and scope_reads==0 and #requests==0 and #collectors==0)
        cache:refresh()
        until_true(function()return cache.diskRead end)
        assert(cache:capture()==nil and cache:take_paint_seeds()[1].assetName=="DA_Cached")
        capture_stalled=false;until_true(function()return cache:capture()~=nil end)
        local capture=cache:capture();assert(capture.rows[1].count==4 and capture.physicalCount==4)
        ''')

    def test_positive_owned_snapshot_filters_empty_rows_and_detaches_model_data(self):
        self.lua.execute('''
        open_cache();until_true(function()return cache:capture()~=nil end)
        local source=model();source.boxCache[2]={persistentId="0000000000000001",paints=json.array()}
        cache:observe(source)
        until_true(function()return count("container_cache_write")>=1 end)
        local request=last_request("container_cache_write")
        assert(#request.boxes==1 and request.boxes[1].paints[1].count==nil)
        source.boxCache[1].persistentId="0000000000000002"
        source.boxCache[1].paints[1].assetName="MUTATED"
        assert(request.boxes[1].persistentId=="ffffffffffffffff" and request.boxes[1].paints[1].assetName=="DA_Blue")
        assert(request.shipId=="8000000000000001" and request.sourcePolicyVersion==1)
        ''')

    def test_read_failure_retries_after_backoff_and_does_not_mark_read_done(self):
        self.lua.execute('''
        fail_reads=1;capture_stalled=true;open_cache()
        until_true(function()return cache.diskStatus=="temporary disk failure" end)
        assert(not cache.diskRead and count("container_cache_read")==1)
        step(true,8,10);assert(count("container_cache_read")==1)
        until_true(function()return cache.diskRead end)
        assert(count("container_cache_read")==2 and cache:take_paint_seeds()[1].assetName=="DA_Cached")
        ''')

    def test_failed_write_retries_same_frozen_capture(self):
        self.lua.execute('''
        fail_writes=1;open_cache()
        until_true(function()return cache.diskStatus=="temporary disk failure" end)
        local first=last_request("container_cache_write")
        assert(first and cache.pendingSnapshot and count("container_cache_write")==1)
        until_true(function()return count("container_cache_write")==2 end)
        assert(last_request("container_cache_write").boxes==first.boxes)
        until_true(function()return cache.diskStatus=="written" end)
        assert(not cache.pendingSnapshot and not cache.diskJob)
        ''')

    def test_newer_capture_wins_over_failed_older_inflight_write(self):
        self.lua.execute('''
        open_cache();until_true(function()return count("container_cache_write")==1 end)
        local first=last_request("container_cache_write");fail_writes=1
        cache:observe(model(nil,8,"0000000000000002"))
        until_true(function()return count("container_cache_write")==2 end)
        local second=last_request("container_cache_write")
        assert(second.boxes[1].persistentId=="0000000000000002")
        assert(first.boxes[1].persistentId=="ffffffffffffffff")
        ''')

    def test_unavailable_worker_and_publication_block_do_not_spin_or_write(self):
        self.lua.execute('''
        capture_stalled=true;worker_available=false;open_cache();step(true)
        assert(cache.diskStatus=="worker unavailable" and #requests==0)
        worker_available=true;step(true,8,10);assert(#requests==0)
        read_result={status="stale_schema",writeAllowed=false,paintSeeds=json.array()}
        until_true(function()return not cache.writeAllowed end)
        capture_stalled=false;until_true(function()return cache:capture()~=nil end)
        for _=1,30 do step(true) end
        assert(count("container_cache_read")==1 and count("container_cache_write")==0)
        ''')

    def test_account_load_invalidation_discards_unconsumed_seeds_and_retry_data(self):
        self.lua.execute('''
        open_cache();until_true(function()return cache.diskRead end)
        assert(cache.paintSeeds and cache:admit(event("76561198000000002")))
        assert(cache:take_paint_seeds()==nil and not cache.pendingSnapshot and not cache.completed)
        assert(cache.path=="C:/Save/76561198000000002/PaintManager.container-cache.json")
        proof_ok=false;cache:refresh();step(true);assert(not cache:binding() and not cache.scope and cache:capture()==nil)
        ''')

    def test_ship_change_recovers_without_old_expected_scope_and_cancels_disk(self):
        self.lua.execute('''
        open_cache();until_true(function()return count("container_cache_write")==1 end)
        local old_worker=workers[#workers];local old_request=old_worker.request
        local old_generation,old_revision=cache.generation,cache.revision
        live_scope={token="ship-B",shipId="8000000000000002",radiusCm=6000,sourcePolicyVersion=1}
        capture_stalled=true;cache:refresh();step(true)
        assert(old_worker.state=="cancelled" and cache.generation>old_generation)
        assert(cache.revision==old_revision and cache:capture()==nil)
        assert(cache.scope.token=="ship-B" and not cache.pendingSnapshot and not cache.paintSeeds)
        assert(old_request.shipId=="8000000000000001")
        assert(last_request("container_cache_read").shipId=="8000000000000002")
        ''')

    def test_ship_unload_during_refresh_revokes_capture_and_stops_until_next_open(self):
        self.lua.execute('''
        open_cache();until_true(function()return cache:capture()~=nil end)
        local revision=cache.revision
        cache:refresh();step(true);local active=cache.job
        ship_available=false;step(true)
        assert(active.state=="cancelled" and cache:capture()==nil and cache.revision==revision)
        assert(cache:get_progress().state=="error" and not cache.pendingSnapshot and not cache.snapshotWork)
        ship_available=true;for i=1,30 do step(true,8,60000) end
        assert(#collectors==2 and cache:capture()==nil)
        cache:refresh();until_true(function()return cache:capture()~=nil end)
        assert(#collectors==3 and cache.revision==revision+1)
        ''')

    def test_reopen_retains_previous_interval_until_fresh_full_box_discovery_completes(self):
        self.lua.execute('''
        local target=open_cache();until_true(function()return cache.revision==target end)
        local previous=cache:capture();target=cache:refresh();capture_stalled=true
        step(true);local job=cache.job
        assert(job and job.options.sourceRefs==nil and not job.options.progressiveStock)
        assert(cache:capture()==previous and previous.stockComplete and previous.rows[1].count==4)
        local steps=job.steps
        for i=1,10 do step(false,8,60000) end
        assert(job.steps==steps and cache.revision==target-1)
        capture_stalled=false;until_true(function()return cache.revision==target end)
        local next_capture=cache:capture()
        assert(next_capture~=previous and next_capture.stockComplete and #collectors==2)
        ''')

    def test_all_empty_successful_observations_publish_empty_positive_cache(self):
        self.lua.execute('''
        open_cache();until_true(function()return cache:capture()~=nil end)
        local capture=model();capture.boxCache=json.array({{persistentId="ffffffffffffffff",paints=json.array()}})
        cache:observe(capture);until_true(function()return count("container_cache_write")>=1 end)
        assert(#last_request("container_cache_write").boxes==0)
        ''')

    def test_cancelled_read_and_capture_cannot_publish_or_restart_in_background(self):
        self.lua.execute('''
        capture_stalled=true;open_cache();step(true)
        local job=cache.job;assert(not job.options.progressiveStock and cache.diskJob)
        local disk=cache.diskJob
        job.get_stock_preview=function()error("No preview should be requested")end
        step(true);cache:cancel_refresh("Window closed")
        local reads=scope_reads
        for i=1,50 do step(i%2==0,8,60000) end
        assert(disk.state=="cancelled" and job.state=="cancelled" and scope_reads==reads)
        assert(#collectors==1 and not cache:capture() and cache.revision==0 and not cache:is_busy())
        assert(cache:get_progress().state=="error")
        ''')

    def test_failed_interval_keeps_same_scope_display_and_reports_one_terminal_error(self):
        self.lua.execute('''
        open_cache();until_true(function()return cache:capture()~=nil end)
        local previous,revision=cache:capture()
        cache:refresh();step(true)
        function cache.job:step()return {state="error",error="Inventory unavailable"}end
        step(true)
        assert(cache:get_progress().state=="error" and cache:get_progress().error=="Inventory unavailable")
        for i=1,30 do step(true,1,100000) end
        assert(cache:capture()==previous and cache.revision==revision and #collectors==2)
        ''')

    def test_scope_change_during_capture_does_not_redirect_the_opening(self):
        self.lua.execute('''
        open_cache();step(true);local job=cache.job
        live_scope={token="B",shipId="8000000000000002",radiusCm=5000,sourcePolicyVersion=1}
        step(true);assert(job.state=="cancelled" and cache:get_progress().state=="error")
        for _=1,30 do step(true) end
        assert(#collectors==1 and cache:capture()==nil and cache.revision==0)
        ''')


if __name__ == "__main__":
    unittest.main()
