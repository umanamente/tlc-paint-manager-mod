"""Paint-list filtering and stable sorting stay pure and resumable."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


FIXTURE = r'''
view=require("paintmanager.paint_view")
budget_module=require("paintmanager.work_budget")
model={status="captured",stockComplete=true,rows={}}
function row(id,name,count,state,complete)
    return {id=id,itemPath="Asset/"..id,displayName=name,count=count,
        recipeState=state,complete=complete}
end
function run(options,input)
    job=view.begin(input or model,options)
    for _=1,100000 do
        result=job:step_one()
        if result.state~="running" then return result.result end
    end
    error("projection did not finish")
end
function ids(rows)
    local result={}
    for _,r in ipairs(rows) do result[#result+1]=r.id end
    return table.concat(result,",")
end
function options(filter,sort,query)
    return {filter=filter,sort=sort,query=query}
end
'''


class PaintViewTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def value(self, expression):
        return self.lua.eval(expression)

    def test_four_filters_preserve_unknown_as_a_separate_state(self):
        self.run_lua('''
            model.rows={row("learned","A",0,"unlocked"),row("ready","B",4,"locked"),
                row("short","C",3,"locked"),row("unknown","D",8,"unknown"),
                row("nilstate","E",5,nil),row("hidden","F",0,"locked")}
            assert(ids(run(options("all")).rows)=="learned,ready,short,unknown,nilstate,hidden")
            assert(ids(run(options("unlocked")).rows)=="learned")
            assert(ids(run(options("locked")).rows)=="ready,short,hidden")
            assert(ids(run(options("ready")).rows)=="ready")
            assert(ids(run(options("insufficient")).rows)=="short,hidden")
        ''')

    def test_unknown_incomplete_or_untrusted_stock_cannot_be_ready_or_insufficient(self):
        self.run_lua('''
            model.rows={row("ready","Ready",4,"locked"),row("short","Short",2,"locked"),
                row("bad","Bad",4,"locked",false),row("badshort","Bad short",2,"locked",false),
                row("unknown","Unknown",4,"unknown")}
            assert(ids(run(options("ready")).rows)=="ready")
            assert(ids(run(options("insufficient")).rows)=="short")
            model.stockComplete=false
            assert(#run(options("ready")).rows==0 and #run(options("insufficient")).rows==0)
            model.stockComplete=true;model.status="partial"
            assert(#run(options("ready")).rows==0 and #run(options("insufficient")).rows==0)
        ''')

    def test_combined_learning_filters_sort_and_uncertain_backpack(self):
        self.run_lua('''
            model.rows={row("bag","C",0,"locked"),row("split","B",2,"locked"),
                row("box","A",4,"locked"),row("short","D",2,"locked"),row("empty","E",0,"locked")}
            model.backpackCounts={bag=5,split=2,short=1,elsewhere=99}
            local ready=run(options("ready","count_desc"))
            assert(ids(ready.rows)=="bag,box,split" and ready.ready_count==3 and ready.category_counts.missing==2)
            assert(ids(run(options("missing","count_asc")).rows)=="empty,short")
            assert(ids(run(options("insufficient")).rows)=="short,empty")
            model.backpackCounts=false
            assert(ids(run(options("ready")).rows)=="box" and #run(options("missing")).rows==0)
            assert(ids(run(options("checking")).rows)=="split,bag,short,empty")
            model.backpackCounts={split=2}
            model.cachePreview=true
            assert(#run(options("ready")).rows==0 and #run(options("missing")).rows==0)
        ''')

    def test_ready_count_matches_shared_policy_and_requires_a_real_id(self):
        self.run_lua('''
            model.rows={row("valid","One",4,"locked"),row("","Empty",4,"locked"),
                row("negative","Negative",-1,"locked"),row("fraction","Fraction",3.5,"locked"),
                row("infinite","Infinite",math.huge,"locked"),row("nan","Nan",0/0,"locked"),
                row("string","String","4","locked")}
            assert(ids(run(options("ready")).rows)=="valid")
            assert(run(options("all")).ready_count==1)
            assert(#run(options("insufficient")).rows==0)
            assert(not view.learn_eligible(nil,model.rows[1]))
            assert(not view.learn_eligible(model,nil))
        ''')

    def test_search_is_literal_case_insensitive_and_display_name_only(self):
        self.run_lua('''
            model.rows={row("internal-needle","Last leaf",9,"unlocked"),
                row("pattern","A.b [blue] 100%",4,"locked"),row("other","AxB blue",6,"locked")}
            assert(ids(run(options("all",nil,"  LAST LeAf  ")).rows)=="internal-needle")
            assert(#run(options("all",nil,"internal-needle")).rows==0)
            assert(ids(run(options("all",nil,"a.b")).rows)=="pattern")
            assert(ids(run(options("all",nil,"[blue]")).rows)=="pattern")
            assert(ids(run(options("all",nil,"100%")).rows)=="pattern")
            assert(#run(options("all",nil,".*")).rows==0)
        ''')

    def test_search_handles_accented_case_and_native_display_names(self):
        self.lua.globals().native_name = "\u041a\u0440\u0430\u0441\u043a\u0430"
        self.run_lua('''
            model.rows={row("accent","Café sea",4,"locked"),row("cyrillic",native_name,4,"locked")}
            assert(ids(run(options("all",nil,"Café")).rows)=="accent")
            assert(ids(run(options("all",nil,native_name)).rows)=="cyrillic")
            assert(ids(run(options("all",nil,"CAFÉ")).rows)=="accent")
        ''')

    def test_four_sort_orders_and_deterministic_name_id_ties(self):
        self.run_lua('''
            model.rows={row("z","Zulu",1,"unlocked"),row("b","alpha",5,"locked"),
                row("a","ALPHA",5,"locked"),row("m","Middle",2,"unknown")}
            assert(ids(run(options("all","name_asc")).rows)=="a,b,m,z")
            assert(ids(run(options("all","name_desc")).rows)=="z,m,a,b")
            assert(ids(run(options("all","count_desc")).rows)=="a,b,m,z")
            assert(ids(run(options("all","count_asc")).rows)=="z,m,a,b")
        ''')

    def test_identical_keys_preserve_original_order_and_input_rows(self):
        self.run_lua('''
            local first=row("same","Same",4,"locked")
            local second=row("same","Same",4,"locked")
            model.rows={first,second,row("last","Alpha",2,"unlocked")}
            local original=model.rows
            local r=run(options("all","name_desc"))
            assert(r.rows[1]==first and r.rows[2]==second)
            assert(model.rows==original and model.rows[1]==first and model.rows[2]==second)
            assert(first.displayName=="Same" and first.count==4 and first.recipeState=="locked")
            assert(first.index==nil and first.name==nil and first.state==nil)
        ''')

    def test_filtered_view_does_not_change_whole_model_totals_or_bulk_scope(self):
        self.run_lua('''
            model.rows={row("known","Known",0,"unlocked"),row("first","First",4,"locked"),
                row("second","Second",7,"locked"),row("third","Third",2,"locked")}
            local r=run(options("unlocked","count_desc","Known"))
            assert(ids(r.rows)=="known" and r.visible_count==1)
            assert(r.ready_count==2 and r.base_visible_count==4 and r.stock==13)
            assert(ids(r.groups.unlocked)=="known" and #r.groups.locked==0)
            assert(model.rows[2].recipeState=="locked" and model.rows[2].count==4)
        ''')

    def test_sorted_group_partition_preserves_order_within_each_recipe_group(self):
        self.run_lua('''
            model.rows={row("u1","Z",0,"unlocked"),row("l1","C",4,"locked"),
                row("u2","B",0,"unlocked"),row("l2","A",4,"locked"),row("x","D",1,"unknown")}
            local r=run(options("all","name_asc"))
            assert(ids(r.rows)=="l2,u2,l1,x,u1")
            assert(ids(r.groups.unlocked)=="u2,u1" and ids(r.groups.locked)=="l2,l1")
            assert(ids(r.groups.unknown)=="x")
        ''')

    def test_optional_ui_projection_omits_groups_without_changing_rows_or_totals(self):
        self.run_lua('''
            model.rows={row("known","Known",0,"unlocked"),row("ready","Ready",4,"locked"),
                row("zero","Zero",0,"locked"),row("short","Short",2,"locked"),
                row("unknown","Unknown",8,"unknown"),row("partial","Partial",2,"locked",false)}
            local full=run({tab="missing",sort="name_desc",max_rows=1,offset=1})
            local opts={tab="missing",sort="name_desc",max_rows=1,offset=1,include_groups=false}
            local job=view.begin(model,opts,{background=false})
            assert(job.groups==nil);opts.include_groups=true
            while job.state=="running" do job:step_one() end
            local lean=job.result
            assert(lean.groups==nil and full.groups.missing[1]~=nil)
            for key,value in pairs(full) do
                if key=="rows" or key=="checking_rows" then
                    assert(#value==#lean[key]);for i,r in ipairs(value) do assert(r==lean[key][i]) end
                elseif key=="category_counts" then
                    for name,count in pairs(value) do assert(lean[key][name]==count) end
                elseif key~="groups" then assert(lean[key]==value,key) end
            end
        ''')

    def test_inline_projection_advances_one_filter_merge_or_group_unit_at_a_time(self):
        self.run_lua('''
            for i=1,8 do model.rows[i]=row(tostring(i),string.format("Name %02d",9-i),i,"locked") end
            job=view.begin(model,nil,{background=false})
            assert(job.index==1 and #job.entries==0 and job.completed==0)
            job:step_one();assert(job.index==2 and #job.entries==1 and job.completed==1)
            for _=2,8 do job:step_one() end
            assert(job.phase=="filter" and #job.entries==8)
            job:step_one();assert(job.phase=="sort" and #job.target==0)
            job:step_one();assert(#job.target==1 and job.completed==10)
            assert(model.rows[1].id=="1")
            while job.state=="running" do result=job:step_one() end
            assert(result.completed==result.total and result.completed==42)
            assert(ids(result.result.rows)=="8,7,6,5,4,3,2,1")
        ''')

    def test_inline_budget_deadline_and_unit_limit_stop_before_next_output(self):
        self.run_lua('''
            for i=1,8 do model.rows[i]=row(tostring(i),"Name "..i,i,"locked") end
            job=view.begin(model,nil,{background=false})
            local b=budget_module.new(2,2,function() return 0 end)
            result=job:step(b)
            assert(b.units==2 and job.index==3 and job.completed==2 and result.state=="running")
            job:step(b);assert(job.completed==2)
            local now=0
            local timed=budget_module.new(2,8,function() return now end)
            local consume=timed.consume
            function timed:consume(units) consume(self,units);now=now+0.003 end
            job:step(timed);assert(timed.units==1 and job.completed==3)
        ''')

    def test_default_lua_projection_obeys_primitive_count_and_deadline_bounds(self):
        self.run_lua('''
            local processing=require("paintmanager.processing")
            assert(processing.request({projection_primitives=3,job_steps_per_unit=2}))
            processing.commit({})
            for i=1,11 do model.rows[i]=row(tostring(i),string.format("Name %02d",i),i,"locked") end
            job=view.begin(model)
            assert(job.phase=="view_copy" and job.completed==0)
            local count=budget_module.new(2,2,function() return 0 end)
            result=job:step(count)
            assert(count.units==2 and job.index==7 and job.completed==6)
            assert(result.state=="running" and result.result==nil)
            job:step(count);assert(job.completed==6)

            local now=0
            local timed=budget_module.new(2,8,function() now=now+.0005;return now end)
            result=job:step(timed)
            assert(timed.units==1 and job.index==8 and job.completed==7)
            assert(result.state=="running" and result.result==nil)

            local previous=job.completed
            for _=1,10000 do
                local b=budget_module.new(2,1,function() return 0 end)
                result=job:step(b)
                assert(b.units<=1 and result.completed>=previous and result.completed<=result.total)
                previous=result.completed
                if result.state~="running" then break end
                assert(result.result==nil)
            end
            assert(result.state=="done" and result.backend=="lua_cooperative",result.error)
            assert(result.completed==result.total and #result.result.rows==11)
            assert(ids(result.result.rows)=="1,2,3,4,5,6,7,8,9,10,11")
        ''')

    def test_cancellation_never_publishes_a_partial_result(self):
        self.run_lua('''
            for i=1,8 do model.rows[i]=row(tostring(i),"Name "..i,i,"locked") end
            job=view.begin(model);job:step_one();job:cancel("new query")
            local before=job.completed
            result=job:step_one()
            assert(result.state=="cancelled" and result.reason=="new query" and result.result==nil)
            assert(job.completed==before and job.input==nil and job.rows==nil and job.model==nil)
            local replacement=view.begin(model,{query="Name 8"})
            while replacement.state=="running" do result=replacement:step_one() end
            assert(ids(result.result.rows)=="8")
        ''')

    def test_options_are_frozen_and_empty_result_finishes_without_invalid_progress(self):
        self.run_lua('''
            model.rows={row("a","Alpha",4,"locked"),row("b","Beta",4,"locked")}
            local opts=options("all","name_desc"," Alpha ")
            job=view.begin(model,opts)
            opts.query="Beta";opts.filter="unlocked";opts.sort="name_asc"
            while job.state=="running" do result=job:step_one() end
            assert(ids(result.result.rows)=="a" and result.result.query=="alpha")
            local r=run({query="No match"})
            assert(#r.rows==0 and r.visible_count==0 and r.ready_count==2)
            assert(result.completed==result.total)
            local empty=run(nil,{status="captured",stockComplete=true,rows={}})
            assert(empty.scanned_count==0 and #empty.rows==0)
        ''')

    def test_page_cap_follows_full_catalog_sort_and_separate_capture_cap(self):
        self.run_lua('''
            for i=1,140 do model.rows[i]=row(tostring(i),string.format("Name %03d",141-i),4,"locked") end
            local capped=run({max_rows=999})
            assert(capped.scanned_count==140 and capped.visible_count==128 and not capped.truncated)
            assert(capped.ready_count==140 and capped.matched_count==140 and capped.has_more)
            assert(capped.rows[1].id=="140")
            local smaller=run({max_rows=3,offset=3})
            assert(smaller.scanned_count==140 and smaller.visible_count==3 and not smaller.truncated)
            assert(smaller.rows[1].id=="137" and smaller.offset==3 and smaller.has_more)
            local admitted=run({max_catalog=3,max_rows=1})
            assert(admitted.scanned_count==3 and admitted.visible_count==1 and admitted.truncated)
            local fallback=run({filter="invalid",sort="invalid",max_rows=-1})
            assert(fallback.filter=="all" and fallback.sort=="name_asc" and fallback.scanned_count==140)
        ''')

    def test_category_counts_checking_and_favorites_use_stable_identity(self):
        self.run_lua('''
            model.rows={row("known","Known",0,"unlocked"),row("ready","Ready",4,"locked"),
                row("zero","Zero",0,"locked"),row("short","Short",2,"locked"),
                row("unknown","Unknown",0,"unknown"),row("partial","Partial",2,"locked",false)}
            for _,r in ipairs(model.rows) do r.assetType="Item";r.assetName=r.id end
            local favorite={["Item\0zero"]=true}
            local result=run({tab="missing",favorites=favorite,sort="name_asc"})
            assert(ids(result.rows)=="zero,short" and ids(result.checking_rows)=="partial,unknown")
            assert(result.category_counts.learned==1 and result.category_counts.ready==1)
            assert(result.category_counts.missing==2 and result.category_counts.checking==2)
            local searched=run({tab="missing",query="Zero",favorites=favorite})
            assert(searched.matched_count==1 and searched.category_counts.missing==2)
            assert(#searched.checking_rows==0)
            model.stockComplete=false
            assert(view.classify(model,model.rows[1])=="learned")
            assert(view.classify(model,model.rows[2])=="checking")
            local preview=run({tab="missing"})
            assert(#preview.rows==0 and #preview.checking_rows==5)
        ''')

    def test_favorite_after_first_render_page_is_sorted_before_slicing(self):
        self.run_lua('''
            for i=1,256 do
                local r=row(tostring(i),string.format("Name %03d",i),0,"unlocked")
                r.assetType="Item";r.assetName="DA_Paint_"..i;model.rows[i]=r
            end
            local favorites={["Item\0DA_Paint_256"]=true}
            local options={tab="learned",favorites=favorites,max_rows=8}
            job=view.begin(model,options)
            favorites["Item\0DA_Paint_256"]=false
            while job.state=="running" do result=job:step_one() end
            assert(result.result.rows[1].id=="256" and result.result.scanned_count==256)
            assert(result.result.matched_count==256 and #result.result.rows==8)
            local second=run({tab="learned",max_rows=8,offset=8,favorites={["Item\0DA_Paint_256"]=true}})
            assert(second.rows[1].id=="8" and second.page==2)
        ''')

    def test_missing_name_or_invalid_count_still_handles_learned_rows_safely(self):
        self.run_lua('''
            model.rows={row("missing",nil,nil,"unlocked"),row("valid","Named",0,"unlocked"),
                row("fraction","Fractional",1.5,"locked")}
            assert(#run(options("all", "count_desc")).rows==3)
            assert(#run(options("insufficient")).rows==0)
            assert(ids(run(options("unlocked", "count_desc")).rows)=="valid,missing")
            assert(#run(options("all",nil,"Asset")).rows==0)
        ''')


if __name__ == "__main__":
    unittest.main()
