"""Pure Lua task algorithms and owned-data capture/view/action integration."""
import json
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE
from test_learn_all import BULK_FIXTURE
from test_presets import PRESET_FIXTURE
from test_diagnostics import FIXTURE as DIAGNOSTIC_FIXTURE
from test_snapshot_jobs import JOB_FIXTURE as SNAPSHOT_FIXTURE
from test_ui import FIXTURE as UI_FIXTURE
from test_ui_presets import PRESET_FIXTURE as UI_PRESET_FIXTURE


MOCK = r'''
local worker=require("paintmanager.worker_analysis")
analysis_enabled=true;analysis_jobs={};analysis_calls={};analysis_block=false
local function clone(value,active)
    if value==json.null then return value end
    local kind=type(value)
    if kind~="table" then
        assert(kind=="nil" or kind=="boolean" or kind=="string"
            or kind=="number" and value==value and math.abs(value)<math.huge,
            "Non-data value submitted to pure Lua task")
        return value
    end
    active=active or {}
    assert(getmetatable(value)==nil and not active[value],"Native wrapper/metatable/cycle submitted to task")
    active[value]=true
    local result=json.is_array(value) and json.array() or {}
    for key,item in next,value do
        assert(type(key)=="string" or type(key)=="number" and key%1==0,"Invalid task key")
        result[key]=clone(item,active)
    end
    active[value]=nil
    return result
end
package.loaded["paintmanager.background"]={
    supports=function(kind) return analysis_enabled and worker.supports(kind) end,
    begin_task=function(kind,payload,options)
        analysis_calls[kind]=(analysis_calls[kind] or 0)+1
        -- A shared VM still needs a frozen primitive snapshot. This strict
        -- fixture rejects engine wrappers without recreating retired transport.
        local frozen=clone(payload)
        local native=worker.begin(kind,frozen)
        local child={payload=frozen,kind=kind,steps=0}
        function child:step_one()
            self.steps=self.steps+1
            if analysis_block then return {state="running"} end
            local oldFirst,oldAll,oldStatic=FindFirstOf,FindAllOf,StaticFindObject
            local function forbidden() error("Engine access on analysis worker") end
            FindFirstOf,FindAllOf,StaticFindObject=forbidden,forbidden,forbidden
            local ok,result=pcall(native.step_one,native)
            FindFirstOf,FindAllOf,StaticFindObject=oldFirst,oldAll,oldStatic
            assert(ok,result)
            return result
        end
        function child:get_result() return native:get_result() end
        function child:cancel(reason) self.cancelled=true;return native:cancel(reason) end
        analysis_jobs[#analysis_jobs+1]=child
        return child
    end,
}
function normalized_model(model)
    local function plain(value)
        if type(value)~="table" or value==json.null then return value end
        local result=json.is_array(value) and json.array() or {}
        for key,item in pairs(value) do
            if key~="capture" and key~="capturedUtc" and key~="recipeCapturedUtc" and key~="checkedAt" then result[key]=plain(item) end
        end
        return result
    end
    return json.encode(plain(model))
end
'''


class WorkerCollectionTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(JOB_FIXTURE)
        self.lua.execute(MOCK)

    def test_progressive_stock_prefix_is_immutable_and_native_capture_pauses_for_worker(self):
        self.lua.execute('''
        local p=paint("DA_Paint_A","A")
        for i=1,20 do box("Box"..i,i,{entry(p,1)}) end
        job=collection.begin(config,{presentation="paints",progressiveStock=true})
        until_phase("stock_preview_worker")
        assert(inventory_reads==8 and job:get_stock_preview()==nil)
        analysis_block=true
        for i=1,12 do one_step() end
        assert(inventory_reads==8)
        analysis_block=false
        local prefix
        for i=1,10000 do
            one_step();prefix=job:get_stock_preview()
            if prefix then break end
        end
        assert(prefix and prefix.rows[1].count==8 and prefix.stockComplete==false)
        assert(prefix.status=="preparing" and inventory_reads==8)
        drain()
        assert(result.model.rows[1].count==20 and result.model.stockComplete)
        assert(prefix.rows[1].count==8 and prefix.stockComplete==false)
        ''')

    def test_copied_collection_matches_cooperative_counts_distributions_and_witnesses(self):
        self.lua.execute('''
        local a=paint("DA_Paint_A","A");local b=paint("DA_Paint_B","B")
        for i=1,7 do box("Box"..i,i*100,{entry(a,i),entry(a,1),entry(b,2)}) end
        analysis_enabled=false;job=collection.begin(config);drain();expected=normalized_model(result.model)
        local before=inventory_reads
        analysis_enabled=true;job=collection.begin(config);drain();actual=normalized_model(result.model)
        assert(result.state=="done",result.error)
        assert(inventory_reads-before==7 and analysis_calls.analysis_collection==2)
        assert(result.model.capture.backgroundAnalysis==false
            and result.model.capture.executionBackend=="lua_cooperative" and mutations==0)
        assert(#result.model.rows[1].stockWitnesses==4 and #result.model.rows[1].stockSources==7)
        ''')
        self.assertEqual(json.loads(self.lua.globals().actual), json.loads(self.lua.globals().expected))

    def test_lean_paint_capture_worker_matches_cooperative_without_source_payload_results(self):
        self.lua.execute('''
        local p=paint("DA_Paint_A","A")
        for i=1,4 do box("Hidden box "..i,i,{entry(p,i)}) end
        analysis_enabled=false;job=collection.begin(config,{presentation="paints"});drain()
        expected=normalized_model(result.model)
        analysis_enabled=true;job=collection.begin(config,{presentation="paints"});drain()
        actual=normalized_model(result.model)
        assert(result.state=="done",result.error)
        assert(#result.model.sources==0 and #result.model.rows[1].stockSources==0)
        assert(result.model.rows[1].count==10 and #result.model.rows[1].stockWitnesses==4)
        assert(result.model.stockComplete and result.model.stockSourcesOmitted and mutations==0)
        for _,child in ipairs(analysis_jobs) do
            if child.payload.model then
                assert(#child.payload.model.sources==0 and #child.payload.sources==4)
                assert(child.payload.sources[1].displayName==nil and child.payload.sources[1].label==nil)
            end
        end
        ''')
        self.assertEqual(json.loads(self.lua.globals().actual), json.loads(self.lua.globals().expected))

    def test_full_catalog_favorites_categories_and_checking_projection_match_worker(self):
        self.lua.execute(r'''
        local view=require("paintmanager.paint_view")
        local rows=json.array()
        for i=1,256 do rows[i]={id="row"..i,assetType="Item",assetName="DA_Paint_"..i,
            displayName=string.format("Paint %03d",i),count=0,recipeState="locked"} end
        rows[2].recipeState="unknown"
        rows[3].recipeState="unlocked"
        rows[4].count=2
        local model={status="captured",stockComplete=true,rows=rows,backpackCounts={row4=2,row5=4}}
        local options={tab="missing",favorites={["Item\0DA_Paint_256"]=true},max_rows=8}
        local function finish(job)
            for _=1,20000 do local p=job:step_one();assert(p.state~="error",p.error)
                if p.state=="done" then return p.result end end
            error("projection did not complete")
        end
        analysis_enabled=false;local cooperative=finish(view.begin(model,options))
        analysis_enabled=true;local native=finish(view.begin(model,options))
        assert(native.rows[1]==rows[256] and native.checking_rows[1]==rows[2])
        assert(native.matched_count==252 and native.category_counts.missing==252)
        assert(native.category_counts.learned==1 and native.category_counts.ready==2 and native.has_more)
        for key,value in pairs(cooperative) do
            if key=="groups" then
                for name,list in pairs(value) do
                    assert(#list==#native.groups[name])
                    for i,r in ipairs(list) do assert(r==native.groups[name][i]) end
                end
            elseif key=="rows" or key=="checking_rows" then
                assert(#value==#native[key])
                for i,r in ipairs(value) do assert(r==native[key][i]) end
            elseif key=="category_counts" then
                for name,count in pairs(value) do assert(native[key][name]==count) end
            else assert(native[key]==value,key) end
        end
        local copied=analysis_jobs[#analysis_jobs].payload.model.rows[256]
        assert(copied.assetType=="Item" and copied.assetName=="DA_Paint_256")
        ''')

    def test_candidate_task_uses_the_same_combined_offer_and_displayed_count(self):
        self.lua.execute('''
        local worker=require("paintmanager.worker_analysis")
        local function row(id,boxes)
            return {id=id,count=boxes,recipeState="locked",runtimeRef={name=id,id=id,className="Item"}}
        end
        local data={rows={row("bag",0),row("box",5),row("split",1),row("short",2),row("split",1)},
            backpackCounts={bag=4,split=3,short=1},contextToken="world"}
        local function run()
            local job=worker.begin("analysis_learn_candidates",data)
            for _=1,100 do local p=job:step_one();assert(p.state~="error",p.error)
                if p.state=="done" then return job:get_result() end end
            error("Candidate preparation stalled")
        end
        local candidates=run();assert(#candidates==3)
        assert(candidates[1].itemPath=="bag" and candidates[1].displayedCount==4)
        assert(candidates[2].itemPath=="box" and candidates[2].displayedCount==5)
        assert(candidates[3].itemPath=="split" and candidates[3].displayedCount==4)
        data.backpackCounts=false;assert(#run()==1)
        data.stockComplete=false;assert(#run()==0)
        ''')

    def test_ui_projection_omits_duplicate_group_transport_and_native_reconstruction(self):
        self.lua.execute('''
        local view=require("paintmanager.paint_view")
        local rows=json.array()
        for i=1,256 do rows[i]={id="row"..i,assetType="Item",assetName="DA_Paint_"..i,
            displayName=string.format("Paint %03d",i),count=0,recipeState="locked"} end
        for i=1,8 do rows[i].recipeState="unknown" end
        local model={status="captured",stockComplete=true,rows=rows}
        local function finish(job)
            for _=1,20000 do local p=job:step_one();assert(p.state~="error",p.error)
                if p.state=="done" then return p.result,p.completed end end
            error("projection did not complete")
        end
        local full,full_steps=finish(view.begin(model,{tab="missing"}))
        local lean_job=view.begin(model,{tab="missing",include_groups=false})
        while lean_job.phase~="view_worker" do lean_job:step_one() end
        local child=analysis_jobs[#analysis_jobs]
        assert(child.payload.options.include_groups==false)
        local original=child.get_result
        function child:get_result()
            local projection=original(self)
            assert(projection.groups==nil,"Unused duplicate groups crossed the worker boundary")
            projection.groups=setmetatable({},{__index=function() error("Native UI traversed groups") end,
                __pairs=function() error("Native UI iterated groups") end})
            return projection
        end
        local lean,lean_steps=finish(lean_job)
        assert(lean.groups==nil and full_steps-lean_steps>=256)
        for key,value in pairs(full) do
            if key=="rows" or key=="checking_rows" then
                assert(#value==#lean[key]);for i,r in ipairs(value) do assert(r==lean[key][i]) end
            elseif key=="category_counts" then
                for name,count in pairs(value) do assert(lean[key][name]==count) end
            elseif key~="groups" then assert(lean[key]==value,key) end
        end
        assert(lean.rows[1]==rows[9] and lean.checking_rows[1]==rows[1])
        ''')

    def test_full_catalog_projection_preserves_all_512_rows_through_worker_options(self):
        self.lua.execute('''
        local view=require("paintmanager.paint_view")
        local rows=json.array()
        for index=1,512 do
            rows[index]={id="row"..index,displayName=string.format("Paint %03d",513-index),
                assetType="Item",assetName="DA_Paint_"..index,complete=true,
                recipeState=index<=129 and "unlocked" or index<=385 and "locked" or "unknown",
                count=index<=257 and 4 or 0}
        end
        local model={status="captured",stockComplete=true,rows=rows}
        local function finish(options)
            local job=view.begin(model,options)
            for _=1,30000 do
                local progress=job:step_one()
                assert(progress.state~="error",progress.error)
                if progress.state=="done" then return progress.result end
            end
            error("full catalog projection stalled")
        end
        analysis_enabled=false
        local expected=finish({filter="all",full_catalog=true,max_catalog=512,include_groups=false})
        analysis_enabled=true
        local full=finish({filter="all",full_catalog=true,max_catalog=512,include_groups=false})
        local paged=finish({filter="all",max_catalog=512,include_groups=false})
        assert(#full.rows==512 and #paged.rows==128 and #expected.rows==512)
        assert(full.groups==nil and paged.groups==nil and full.matched_count==512)
        assert(not full.has_more and paged.has_more and full.scanned_count==512)
        assert(full.category_counts.learned==129 and full.category_counts.ready==128)
        assert(full.category_counts.missing==128 and full.category_counts.checking==127)
        for index,row in ipairs(full.rows) do
            assert(row==expected.rows[index] and row==rows[513-index])
        end
        assert(analysis_calls.analysis_view==2)
        local full_payload=analysis_jobs[#analysis_jobs-1].payload
        assert(full_payload.options.full_catalog==true and full_payload.options.include_groups==false)
        assert(full_payload.options.max_rows==512 and #full_payload.model.rows==512)
        ''')

    def test_weak_unknown_items_remain_partial_and_do_not_gain_witnesses(self):
        self.lua.execute('''
        local a=paint("DA_Paint_A","A")
        box("Box",10,{entry(a,1),entry(nil,2,"DA_Paint_Missing")})
        analysis_enabled=false;job=collection.begin(config);drain();expected=normalized_model(result.model)
        analysis_enabled=true;job=collection.begin(config);drain();actual=normalized_model(result.model)
        assert(mutations==0 and result.model.stockComplete==false and #result.model.unresolvedItems==1)
        ''')
        self.assertEqual(json.loads(self.lua.globals().actual), json.loads(self.lua.globals().expected))

    def test_prepared_catalog_refresh_matches_native_worker_aggregation(self):
        self.lua.execute('''
        local p=paint("DA_Paint_Cached","Cached");p.Type={Name="Paint"}
        box("CachedBox",10,{entry(p,4)})
        job=collection.begin_catalog(config,{discover=true});drain()
        local catalog=job:get_catalog();assert(catalog and #catalog.rows==1)
        analysis_enabled=false;job=collection.begin(config,{catalog=catalog});drain()
        expected=normalized_model(result.model)
        analysis_enabled=true;job=collection.begin(config,{catalog=catalog});drain()
        actual=normalized_model(result.model)
        assert(result.model.preparedCatalog and result.model.recipeEntriesVisited==0)
        assert(result.model.rows[1].count==4 and result.model.stockComplete)
        assert(analysis_calls.analysis_collection and mutations==0)
        ''')
        self.assertEqual(json.loads(self.lua.globals().actual), json.loads(self.lua.globals().expected))

    def test_context_change_cancels_pending_analysis_and_publishes_no_partial_model(self):
        self.lua.execute('''
        local a=paint("DA_Paint_A","A");box("Box",10,{entry(a,4)})
        job=collection.begin(config);until_phase("collection_worker");analysis_block=true
        one_step();assert(result.state=="running" and result.model==nil)
        pawn.address=pawn.address+1;one_step()
        assert(result.state=="cancelled" and result.model==nil and analysis_jobs[#analysis_jobs].cancelled)
        assert(#job.model.rows==0 and mutations==0)
        ''')

    def test_large_source_selection_is_batched_and_keeps_exact_nearest_scope(self):
        self.lua.execute('''
        local p=paint("DA_Paint_A","A");config.max_sources=8
        for i=1,300 do box("Box"..i,301-i,{entry(p,1)}) end
        analysis_enabled=false;job=collection.begin(config);drain();expected=normalized_model(result.model)
        analysis_enabled=true;job=collection.begin(config);drain();actual=normalized_model(result.model)
        assert(result.model.sourceCount==300 and result.model.includedSourceCount==8)
        assert(result.model.sourcesTruncated and result.model.stockComplete==false)
        local selections=0
        for _,child in ipairs(analysis_jobs) do
            if child.payload.mode=="source_candidates" then
                selections=selections+1
                assert(#child.payload.candidates<=128 and #child.payload.selected<=8)
            end
        end
        assert(selections==3)
        ''')
        self.assertEqual(self.lua.eval("actual"), self.lua.eval("expected"))

    def test_late_catalog_ambiguity_preserves_each_sources_original_lookup_boundary(self):
        self.lua.execute('''
        local a=paint("DA_Paint_Shared","A","VoyagePaintItem /Game/First/DA_Paint_Shared.DA_Paint_Shared")
        local b=paint("DA_Paint_Shared","B","VoyagePaintItem /Game/Second/DA_Paint_Shared.DA_Paint_Shared")
        paints={a}
        box("First",10,{entry(nil,2,"DA_Paint_Shared")})
        box("Second",20,{entry(b,3),entry(nil,5,"DA_Paint_Shared")})
        analysis_enabled=false;job=collection.begin(config);drain();expected=normalized_model(result.model)
        assert(result.model.rows[1].count==2 and result.model.rows[2].count==3)
        assert(#result.model.rows[1].stockWitnesses==0 and #result.model.unresolvedItems==1)
        analysis_enabled=true;job=collection.begin(config);drain();actual=normalized_model(result.model)
        assert(result.model.physicalCount==5 and result.model.stockComplete==false and mutations==0)
        ''')
        self.assertEqual(self.lua.eval("actual"), self.lua.eval("expected"))

    def test_view_worker_returns_indexes_and_keeps_render_rows_in_current_model(self):
        self.lua.execute('''
        local view=require("paintmanager.paint_view")
        local rows={{id="b",displayName="Beta",count=4,recipeState="locked"},
            {id="a",displayName="Alpha",count=0,recipeState="unlocked"},
            {id="c",displayName="Unknown",count=9,recipeState="unknown"}}
        local model={status="captured",stockComplete=true,rows=rows}
        local job=view.begin(model,{sort="count_desc",generation=11})
        local result
        for _=1,1000 do local p=job:step_one();assert(p.state~="error",p.error)
            if p.state=="done" then result=p.result;break end end
        assert(result and result.ready_count==1 and #result.groups.unknown==1)
        assert(result.groups.locked[1]==rows[1] and result.groups.unlocked[1]==rows[2])
        assert(analysis_calls.analysis_view==1)
        local copied=analysis_jobs[1].payload.model.rows[1]
        assert(copied.__viewIndex==1 and copied.stockSources==nil and copied.runtimeRef==nil)
        assert(rows[1].__viewIndex==nil)
        ''')

    def test_cache_preview_keeps_locked_and_pending_rows_without_stock_verdicts(self):
        self.lua.execute('''
        local view=require("paintmanager.paint_view")
        local rows=json.array({
            {id="locked",displayName="Locked",count=0,recipeState="locked",complete=true},
            {id="pending",displayName="Pending",count=0,recipeState="unknown",complete=false}})
        local model={status="preparing",stockComplete=false,cachePreview=true,rows=rows}
        local job=view.begin(model,{})
        local result
        for _=1,5000 do local p=job:step_one();assert(p.state~="error",p.error)
            if p.state=="done" then result=p.result;break end end
        assert(result.visible_count==2 and result.ready_count==0)
        assert(#result.groups.locked==1 and #result.groups.unknown==1)
        assert(not view.learn_eligible(model,rows[1]))
        local ready=view.begin(model,{filter="ready"})
        for _=1,5000 do local p=ready:step_one();assert(p.state~="error",p.error)
            if p.state=="done" then assert(p.result.visible_count==0);return end end
        error("ready filter stalled")
        ''')

    def test_replaced_query_cancels_worker_without_reusing_old_projection(self):
        self.lua.execute('''
        local view=require("paintmanager.paint_view")
        local model={status="captured",stockComplete=true,rows={{id="a",displayName="Alpha",count=4,recipeState="locked"}}}
        local old=view.begin(model,{query="alpha"})
        for _=1,4 do old:step_one() end
        old:cancel("query changed")
        assert(old:step_one().state=="cancelled" and analysis_jobs[1].cancelled)
        local current=view.begin(model,{query="missing"})
        for _=1,1000 do local p=current:step_one();assert(p.state~="error",p.error)
            if p.state=="done" then assert(p.result.visible_count==0);return end end
        error("new query did not finish")
        ''')


class WorkerLearningTests(unittest.TestCase):
    def setUp(self):
        WorkerCollectionTests.setUp(self)
        self.lua.execute(BULK_FIXTURE)

    def test_batch_selects_incrementally_without_selection_worker_and_grants_with_live_proof(self):
        self.lua.execute('''
        scene(3);new_bulk();bulk_drain()
        assert(result.state=="done" and nativeCalls==3 and result.receipt.counts.learned==3)
        assert(analysis_calls.analysis_learn_candidates==nil and analysis_calls.analysis_collection==2)
        for _,record in ipairs(records) do assert(record.Data.ItemCount==4) end
        ''')

    def test_batch_candidate_selection_is_local_bounded_and_cancellable(self):
        self.lua.execute('''
        scene(2);new_bulk();bulk_until("bulk_prepare")
        local shared=budget_module.new(10000,1)
        local p=job:step(shared)
        assert(p.state=="running" and p.phase=="bulk_prepare" and shared.units==1)
        assert(#job.candidates==1 and nativeCalls==0 and analysis_calls.analysis_learn_candidates==nil)
        job:cancel("UI closed")
        assert(job.state=="cancelled" and nativeCalls==0)
        ''')


class WorkerPresetTests(unittest.TestCase):
    def setUp(self):
        WorkerCollectionTests.setUp(self)
        self.lua.execute(PRESET_FIXTURE)

    def test_preset_worker_plans_supply_then_current_native_checks_transfer_one_can(self):
        self.lua.execute('''
        start();until_phase(job,"preset_commit");assert(nativeCalls==0)
        assert(analysis_calls.analysis_preset_plan==2)
        job:step();assert(nativeCalls==1);drain(job)
        assert(job:get_receipt().outcome=="applied_verified" and nativeCalls==2)
        assert(#backpack.rows==0 and #pawn.PaintInventory.rows==2)
        ''')

    def test_source_change_after_worker_plan_rejects_before_native_attempt(self):
        self.lua.execute('''
        start();until_phase(job,"preset_commit");backpack.rows={}
        drain(job)
        assert(job:get_receipt().outcome=="rejected" and nativeCalls==0)
        assert(job:get_receipt().attempted==false)
        ''')

    def test_worker_plans_occupied_replacement_and_saved_empty_zone(self):
        self.lua.execute('''
        start("boat")
        request.preset.zones[2].paint=json.null
        boatPaint.rows={{20,entry(paintB,1)},{30,entry(paintB,1)}}
        drain(job);local receipt=job:get_receipt()
        assert(analysis_calls.analysis_preset_plan==2 and receipt.outcome=="applied_verified",receipt.error)
        assert(receipt.completed==2 and nativeCalls==3 and #boatPaint.rows==1 and #backpack.rows==3)
        assert(boatPaint.rows[1][2].Item:get()==paintA)
        ''')

    def test_capture_context_error_cancels_nested_installed_paint_worker_before_dropping_it(self):
        self.lua.execute('''
        analysis_block=true
        local capture=presets.begin_capture(config)
        local nested
        for _=1,1000 do
            if capture.child and capture.child.analysisJob then nested=capture.child.analysisJob;break end
            local p=capture:step(budget_module.new(10000,1))
            assert(p.state=="running",p.error)
        end
        assert(nested and not nested.cancelled,"Pending installed-paint worker missing")
        pawn.address=pawn.address+1
        local p=capture:step(budget_module.new(10000,1))
        assert(p.state=="error" and nested.cancelled and capture.child==nil)
        assert(nativeCalls==0,"Capture error attempted a gameplay mutation")
        ''')

    def test_captured_logical_presets_and_installed_slot_joins_match_reference(self):
        self.lua.execute('''
        analysis_enabled=false;refresh();expected=normalized_model(view)
        analysis_enabled=true;refresh();actual=normalized_model(view)
        assert(analysis_calls.analysis_collection==2 and analysis_calls.analysis_preset_plan==1)
        assert(nativeCalls==0)
        ''')
        self.assertEqual(self.lua.eval("actual"), self.lua.eval("expected"))

    def test_partial_installed_rows_keep_reference_unknowns_and_error_order(self):
        self.lua.execute('''
        pawn.PaintInventory.rows={{2,entry(paintA,1)},{2,entry(paintB,1)},{99,entry(paintA,1)}}
        analysis_enabled=false;refresh();expected=normalized_model(view)
        analysis_enabled=true;refresh();actual=normalized_model(view)
        assert(not view.paintState.character.complete and #view.paintState.character.unconfiguredItems==1)
        assert(nativeCalls==0)
        ''')
        actual, expected = json.loads(self.lua.eval("actual")), json.loads(self.lua.eval("expected"))
        # Error location moves from the game-side module to the worker-side
        # module. The diagnostic meaning and captured unknowns remain exact.
        for value in (actual, expected):
            value["targets"][0]["error"] = value["targets"][0]["error"].split(": ", 1)[-1]
        self.assertEqual(actual, expected)


class WorkerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path;json=require("paintmanager.json")')
        self.lua.execute(DIAGNOSTIC_FIXTURE)
        self.lua.execute(SNAPSHOT_FIXTURE)
        self.lua.execute(MOCK)

    def test_copied_diagnostic_classification_sorting_and_totals_match_reference(self):
        self.lua.execute('''
        analysis_enabled=false;local before=drain(adapter.begin(config));expected=normalized_model(before)
        analysis_enabled=true;local after=drain(adapter.begin(config));actual=normalized_model(after)
        assert(analysis_calls.analysis_collection>=1 and mutations==0)
        ''')
        self.assertEqual(self.lua.eval("actual"), self.lua.eval("expected"))

    def test_synchronous_snapshot_keeps_nested_paint_capture_cooperative_when_worker_is_available(self):
        self.lua.execute('''
        analysis_enabled=false
        expected=normalized_model(adapter.snapshot(config))
        analysis_enabled=true
        local attempts=0
        package.loaded["paintmanager.background"].begin_task=function()
            attempts=attempts+1;error("Synchronous compatibility snapshot dispatched a worker")
        end
        actual=normalized_model(adapter.snapshot(config))
        assert(attempts==0 and mutations==0)
        ''')
        self.assertEqual(self.lua.eval("actual"), self.lua.eval("expected"))


class WorkerPresentationTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path;json=require("paintmanager.json")')
        self.lua.execute(UI_FIXTURE)
        self.lua.execute(UI_PRESET_FIXTURE)
        self.lua.execute(MOCK)

    def test_preset_labels_and_native_thumbnails_render_with_analysis_blocked(self):
        self.lua.execute('''
        analysis_block=true
        local view=presets_view()
        view.library.presets[1].name="Same";view.library.presets[2].name="same"
        open_presets(view);select_preset("Same [Preset1]")
        assert(preset_has(ui.preset_list,"Same [Preset1]") and preset_has(ui.preset_list,"same [Preset2]"))
        assert_zone_order(ui.preset_detail_zones,"Deck","Tower","Hull","Flag")
        assert(preset_has(ui.preset_detail_zones,"North home") and preset_has(ui.preset_detail_zones,"Empty"))
        assert(#nodes(ui.preset_icon_box,function(node) return node.texture==paint_textures.north end)==1)
        assert(analysis_calls.analysis_preset_plan==nil and #preset_requests==0)
        ''')

    def test_replaced_or_closed_preset_view_retires_partial_native_presentation(self):
        self.lua.execute('''
        analysis_block=true
        ui:set_connection({connected=true,shipOwnerId="ShipOwner"})
        assert(ui:toggle());ui:set_presets(presets_view(48));ui:_select_tab("presets")
        for _=1,128 do
            local budget=work_budget(1)
            ui:_pump_render(budget);assert(budget.units<=1)
            if #preset_buttons()>0 then break end
        end
        local previous=assert(ui.preset_render_job)
        local completed,stale=previous.completed,preset_button("Saved 1")
        assert(#preset_buttons()==1 and not shown(ui.preset_list) and not stale.widget:GetIsEnabled())

        local replacement=presets_view(1)
        replacement.library.presets[1].name="Replacement"
        ui:set_presets(replacement);drain_render()
        assert(previous.completed==completed and #preset_buttons()==1 and shown(ui.preset_list))
        assert(preset_has(ui.preset_list,"Replacement") and not preset_has(ui.preset_list,"Saved 1"))
        stale.action();assert(ui.selected_preset_id==nil)
        select_preset("Replacement");assert(ui.selected_preset_id=="Preset1")

        ui:set_presets(presets_view(48));ui:_pump_render(work_budget(1))
        assert(ui.preset_render_job)
        ui:close("closed")
        assert(ui.preset_render_job==nil and not ui:is_rendering() and #preset_buttons()==0)
        assert(analysis_calls.analysis_preset_plan==nil and #preset_requests==0)
        ''')


if __name__ == "__main__":
    unittest.main()
