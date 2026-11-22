"""Fast stock updates use verified whole-box contents and keep live Learn evidence accurate."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_paint_transfer import TRANSFER


class TransferStockTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path; realShip=require("paintmanager.ship_scope")')
        self.lua.execute(FIXTURE)
        self.lua.execute(TRANSFER)
        self.lua.execute('''
            ship.same_scope,ship.contains=realShip.same_scope,realShip.contains
            reconcile=require("paintmanager.transfer_stock").reconcile
            for i=1,6 do
                stock("Box"..i,string.format("%016x",i),100*i,
                    i<=5 and {[0]=can(A,i,i),[1]=can(A,i+20)} or {})
            end
            function capture(background)
                local job=collection.begin(config,{presentation="paints", background=background})
                local budget={expired=function() return false end,consume=function() end,
                    metrics=function() return {clockValid=true} end}
                for i=1,30000 do
                    local progress=job:step(budget)
                    if progress.state~="running" then
                        assert(progress.state=="done",progress.error)
                        model=job.model; return
                    end
                end
                error("capture did not complete")
            end
            function observation(i,counts)
                local source=model.stockLocationSources[i]
                return {persistentId=source.persistentId,componentRef=source.componentRef,
                    ownerRef=source.ownerRef,kind=source.kind,counts=counts,location={X=100*i,Y=0,Z=0}}
            end
            function apply(changes)
                return reconcile(model,changes,ship.current(config),token)
            end
        ''')

    def test_take_and_return_rebuild_counts_and_all_learning_hints_without_native_queries(self):
        for background in (False, True):
            with self.subTest(background=background):
                self.lua.globals().background_run = background
                self.lua.execute('''
                    capture(background_run)
                    local original=json.encode(model)
                    local reads, finds, vault_checks=inventory_reads,scans,queries
                    local result=assert(apply({observation(1,{}),observation(6,{[B.path]=9})}))
                    local a,b=result.rows[1],result.rows[2]
                    assert(a.itemPath==A.path and a.count==18 and result.physicalCount==27)
                    assert(#a.stockLocationHints==4 and #a.stockWitnesses==4)
                    for i=1,4 do
                        assert(a.stockLocationHints[i]==i+1 and a.stockWitnesses[i].capturedCount==i+2)
                        assert(a.stockWitnesses[i].ownerRef==result.stockLocationSources[i+1].ownerRef)
                    end
                    assert(b.count==9 and #b.stockWitnesses==1 and b.stockLocationHints[1]==6)
                    if background_run then
                        assert(#result.boxCache==6 and #result.boxCache[1].paints==0)
                        assert(result.boxCache[6].paints[1].assetName=="DA_Paint_B")
                    end
                    assert(json.encode(model)==original)
                    assert(inventory_reads==reads and scans==finds and vault_checks==queries)
                    model=result
                    local returned=assert(apply({observation(1,{[A.path]=2}),observation(6,{})}))
                    assert(returned.physicalCount==20 and returned.rows[2].count==0)
                    assert(#returned.rows[2].stockWitnesses==0 and #returned.rows[1].stockLocationHints==5)
                ''')

    def test_stale_scope_weak_or_missing_source_and_unknown_native_results_require_fresh_interval(self):
        self.lua.execute('''
            capture(true)
            local observations={observation(1,{})}
            assert(not apply(nil) and not apply({}))
            assert(not reconcile(model,observations,ship.current(config),"different-world"))
            assert(not reconcile(model,observations,{token="another-ship"},token))
            local observation=observations[1]
            observation.location.X=3501;assert(not apply(observations));observation.location.X=100
            observation.counts={["Unknown paint"]=1};assert(not apply(observations))
            observation.counts={}
            observation.componentRef={id="replaced"};assert(not apply(observations))
            observation.componentRef=model.stockLocationSources[1].componentRef
            model.stockComplete=false;assert(not apply(observations));model.stockComplete=true
            model.rows[1].count=model.rows[1].count+1;assert(not apply(observations))
            model.rows[1].count=model.rows[1].count-1;assert(apply(observations))
            model.stockLocationSources[2].persistentId=observation.persistentId;assert(not apply(observations))
            model.stockLocationSources[2].persistentId="0000000000000002"
            model.stockLocationSources[6].paintCounts=nil;assert(not apply(observations))
        ''')

    def test_confirmed_transfer_receipts_match_a_fresh_scan_after_take_and_return(self):
        self.lua.execute('''
            capture(true)
            for _,dir in ipairs({"take","return"}) do
                local job=new_job(dir,dir=="take" and A or nil,5)
                local receipt=finish(job)
                assert(receipt.moved==5,receipt.reason or receipt.error)
                local patched=assert(apply(job:get_stock_changes()))
                capture(true)
                assert(patched.physicalCount==model.physicalCount)
                for index,row in ipairs(model.rows) do
                    assert(json.encode(patched.rows[index].stockWitnesses)==json.encode(row.stockWitnesses))
                    assert(json.encode(patched.rows[index].stockLocationHints)==json.encode(row.stockLocationHints))
                    assert(patched.rows[index].count==row.count)
                end
            end
        ''')

    def test_return_to_identified_box_outside_interval_keeps_nearby_counts(self):
        self.lua.execute('''
            capture(true)
            local remote=observation(1,{[A.path]=7})
            remote.persistentId="0000000000000123"
            remote.componentRef={id="remote component"};remote.ownerRef={id="remote owner"}
            remote.location.X=3501
            local patched=assert(apply({remote}))
            assert(json.encode(patched.rows)==json.encode(model.rows))
            remote.location.X=100;assert(not apply({remote}))
        ''')


if __name__ == "__main__":
    unittest.main()
