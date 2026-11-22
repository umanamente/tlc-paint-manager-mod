"""Worker stock aggregation produces advisory logical positive-box records."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import SCRIPTS


FIXTURE = r'''
json=require("paintmanager.json")
worker=require("paintmanager.worker_analysis")
function row(name)
    return {id="VoyagePaintItem /Game/Paints/"..name,itemPath="VoyagePaintItem /Game/Paints/"..name,
        assetType="Item",assetName=name,displayName=name,count=0,
        stockSources=json.array(),stockWitnesses=json.array(),stockLocationHints=json.array()}
end
function source(id,kind)
    return {id=id,persistentId=id,kind=kind or "Large",checkedAt=1795000000,
        complete=true,paintCount=0,errors=json.array()}
end
function record(r,count,native)
    return {nativePaint=native~=false,itemPath=r.itemPath,assetName=r.assetName,itemCount=count}
end
function run(sources,captures,rows)
    local model={rows=json.array(rows),sources=json.array(),errors=json.array(),unresolvedItems=json.array(),
        complete=true,stockComplete=true,physicalCount=0,stockSourcesOmitted=true}
    local input=json.array()
    for i,records in ipairs(captures) do input[i]={sourceIndex=i,records=json.array(records),
        candidate={componentRef={id="component"..i},ownerRef={id="owner"..i},kind=sources[i].kind}} end
    local job=worker.begin("analysis_collection",{model=model,sources=json.array(sources),captures=input})
    for _=1,10000 do
        local progress=job:step_one();assert(progress.state~="error",progress.error)
        if progress.state=="done" then return job:get_result() end
    end
    error("Worker analysis did not finish")
end
'''


class ContainerCacheAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def test_distinct_primary_ids_sorted_without_counts_or_native_descriptors(self):
        self.lua.execute('''
        local a,b=row("A"),row("B")
        local result=run({source("fedcba9876543210")},{{record(b,2),record(a,3),record(a,4)}},{a,b})
        assert(result.physicalCount==9 and #result.boxCache==1)
        local box=result.boxCache[1]
        assert(box.persistentId=="fedcba9876543210" and box.boxKind=="large" and box.lastCheckedAt==1795000000)
        assert(#box.paints==2 and box.paints[1].assetName=="A" and box.paints[2].assetName=="B")
        assert(box.paints[1].itemPath=="/Game/Paints/A" and box.paints[1].count==nil and box.componentRef==nil)
        assert(result.rows[1].count==7 and result.rows[2].count==2)
        ''')

    def test_empty_complete_sources_publish_explicit_negative_and_unresolved_reads_do_not(self):
        self.lua.execute('''
        local a=row("A");local good=source("0000000000000001","Small")
        local bad=source("0000000000000002","Medium")
        local result=run({good,bad},{ {}, {{assetName="Unloaded",itemCount=1}} },{a})
        assert(#result.boxCache==1 and result.boxCache[1].persistentId==good.persistentId)
        assert(#result.boxCache[1].paints==0 and not result.stockComplete)
        assert(json.encode(result.boxCache[1]):find('"paints":[]',1,true))
        ''')

    def test_missing_duplicate_zero_or_malformed_identity_is_not_persisted(self):
        self.lua.execute('''
        local a=row("A");local s={source("0000000000000001"),source("0000000000000001"),
            source("0000000000000000"),source("BAD"),source("0000000000000005")}
        s[5].persistentId=nil
        local result=run(s,{{record(a,1)},{record(a,1)},{record(a,1)},{record(a,1)},{record(a,1)}},{a})
        assert(#result.boxCache==0 and result.physicalCount==5)
        for _,box in ipairs(s) do assert(box._cachePaints==nil) end
        ''')

    def test_only_native_positive_paint_observations_enter_persistent_sample_set(self):
        self.lua.execute('''
        local a,b=row("A"),row("B")
        local result=run({source("0000000000000001")},{{record(a,0),record(b,3,false)}},{a,b})
        assert(result.physicalCount==3 and #result.boxCache==1 and #result.boxCache[1].paints==0)
        ''')

    def test_unqualified_time_kind_or_incomplete_box_is_excluded(self):
        self.lua.execute('''
        local a=row("A");local s={source("0000000000000001"),source("0000000000000002","Other"),source("0000000000000003")}
        s[1].checkedAt=nil;s[3].complete=false
        local result=run(s,{{record(a,1)},{record(a,1)},{record(a,1)}},{a})
        assert(#result.boxCache==0)
        ''')


if __name__ == "__main__":
    unittest.main()
