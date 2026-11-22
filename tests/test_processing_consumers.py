"""Actual consumers: configurable chunk boundaries preserve outcomes and demand gates."""
import unittest
from lupa.lua54 import LuaRuntime
from test_local_library import SCRIPTS, FIXTURE as FILES
from test_paint_cache import FIXTURE as PAINTS
from test_stock_cache import FIXTURE as STOCK
from test_collection import FIXTURE as COLLECTION


class ProcessingConsumerTests(unittest.TestCase):
    def runtime(self, fixture=""):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts_path = SCRIPTS.as_posix()
        lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        lua.execute('''
            processing=require("paintmanager.processing")
            function policy(value)
                assert(processing.request(value));processing.commit({})
            end
        ''')
        lua.execute(fixture)
        return lua

    def test_time_and_unit_limits_are_independent_and_sync_keeps_validated_numbers(self):
        self.runtime().execute('''
            local budgets=require("paintmanager.work_budget")
            local now=0
            policy({time_limit_enabled=false})
            local count=budgets.new(2,2,function()return now end)
            now=100;count:consume();assert(not count:expired());count:consume();assert(count:expired())
            policy({time_limit_enabled=true,unit_limit_enabled=false})
            now=0;local timed=budgets.new(2,1,function()return now end)
            timed:consume(20);assert(not timed:expired());now=.003;assert(timed:expired())
            policy({batching_enabled=false})
            local sync=budgets.new(2,1,function()return now end)
            sync:consume(500);now=200;assert(not sync:expired())
            assert(sync:metrics().units==500 and sync:metrics().batchingEnabled==false)
            assert(not pcall(budgets.new,0,0))
        ''')

    def test_codec_chunks_change_work_count_without_changing_canonical_bytes(self):
        self.runtime().execute('''
            local json=require("paintmanager.json");local reader=require("paintmanager.json_reader")
            local value={description=string.rep(utf8.char(0x0416)..string.char(34,92,10),1800),arr=json.array({1,true,json.null})}
            local expected=json.encode(value)
            local function encode(bytes)
                policy({json_string_bytes=bytes,json_key_bytes=bytes})
                local job=json.begin(value);local pieces={};local calls=0
                repeat
                    local p=job:step_one();calls=calls+1;assert(p.state~="error",p.error)
                    if p.chunk then pieces[#pieces+1]=p.chunk end
                until job.state=="done"
                assert(table.concat(pieces)==expected);return calls
            end
            assert(encode(1)>encode(8192))
            local function parse(bytes)
                policy({json_read_bytes=bytes})
                local job=reader.begin(expected,{max_string=32768});local calls=0
                repeat local p=job:step_one();calls=calls+1;assert(p.state~="error",p.error) until job.state=="done"
                assert(json.encode(job:get_result())==expected);return calls
            end
            assert(parse(1)>parse(8192))
            policy({compare_bytes=1});local order,cursor=json.compare_bytes("long-A","long-B")
            assert(order==nil and cursor==2)
            policy({compare_bytes=32768});assert(json.compare_bytes("long-A","long-B")==-1)
        ''')

    def test_pure_work_group_override_and_primitive_step_have_real_effect(self):
        self.runtime().execute('''
            local pure=require("paintmanager.plain_work");local budgets=require("paintmanager.work_budget")
            policy({pure_primitives=500,schema_primitives=3,time_limit_enabled=false})
            local b=budgets.new(2,8,function()return 0 end)
            local n=0;local function advance()n=n+1;return {state="running"}end
            local _,count=pure.run(advance,b);assert(count==500 and n==500)
            _,count=pure.run(advance,b,{group="schema_primitives"});assert(count==3)
            processing.set_primitive_step(true)
            _,count=pure.run(advance,b);assert(count==1)
        ''')

    def test_catalog_demand_is_independent_of_automatic_preparation_and_has_no_disk_tasks(self):
        self.runtime(PAINTS).execute('''
            policy({automatic_preparation=false})
            discovered={stable(1)}
            cache:admit(event());assert(#requests==0 and #collectors==0)
            for _=1,10 do cache:step(budget_module.new(100,8),false,false)end
            assert(#requests==0 and #collectors==0)
            cache:begin_open()
            for _=1,200 do
                cache:step(budget_module.new(100,8),true,true)
                if cache:catalog() and cache.phase=="idle" and not cache:is_busy() then break end
            end
            assert(cache:catalog() and #requests==0 and #collectors==1)
            assert(cache:quiesce(budget_module.new(100,8)))
            assert(cache:reset_derived());assert(cache:catalog()==nil and #requests==0)
        ''')

    def test_box_disk_bypass_preserves_one_opening_refresh(self):
        self.runtime(STOCK).execute('''
            policy({automatic_preparation=false,box_cache_read=false,box_cache_write=false})
            cache:admit(event());cache:step(budgets.new(100,8),true,nil,10)
            assert(#requests==0 and #collectors==0)
            cache:refresh()
            for n=1,10 do cache:step(budgets.new(100,8),true,nil,n*10)end
            assert(cache:capture() and #collectors==1 and #requests==0)
            for n=11,30 do cache:step(budgets.new(100,8),true,nil,n*10)end
            assert(#collectors==1 and cache:capture().stockComplete)
            assert(cache:reset_derived());assert(cache:capture()==nil)
        ''')


    def test_demanded_stock_cache_output_finishes_with_preparation_disabled(self):
        self.runtime(STOCK).execute('''
            policy({automatic_preparation=false,box_cache_read=false})
            cache:admit(event());cache:refresh()
            for n=1,3 do cache:step(budgets.new(100,8),true,nil,n*10)end
            local reads=scope_reads
            for n=4,60 do cache:settle_output(budgets.new(100,8),n*10) end
            assert(scope_reads==reads)
            assert(cache:capture() and #collectors==1)
            assert(count("container_cache_read")==0 and count("container_cache_write")==1)
            assert(cache.diskJob==nil and cache.pendingSnapshot==nil)
        ''')

    def test_catalog_quiesce_cancels_readonly_preparation_before_reset(self):
        self.runtime(PAINTS).execute('''
            for n=1,20 do discovered[n]=stable(n)end
            cache:admit(event());cache:begin_open();cache:step(budget_module.new(100,3),true,true)
            assert(cache:learned_capture() and cache.phase=="seed" and cache:is_busy())
            local active=cache.job
            assert(not cache:reset_derived())
            assert(cache:quiesce(budget_module.new(100,1)))
            assert(active.state=="cancelled" and not cache:is_busy())
            assert(cache:reset_derived() and #requests==0)
        ''')


    def test_aggregation_batch_changes_real_work_count_without_changing_stock(self):
        self.runtime(COLLECTION).execute('''
            local budgets=require("paintmanager.work_budget")
            local blue=paint("DA_Paint_Blue","Blue");local records={}
            for _=1,80 do records[#records+1]=entry(blue,1)end
            box("Stock",100,records)
            local function capture(size)
                policy({aggregate_rows_per_unit=size})
                local job=collection.begin(config,{background=false})
                while job.state=="running" do job:step(budgets.new(100000,8,function()return 0 end))end
                assert(job.state=="done",job.error)
                local model=job:get_progress().model
                assert(model.physicalCount==80 and model.stockComplete and mutations==0)
                return job.processed,json.encode(model.rows)
            end
            local small,small_rows=capture(1);local large,large_rows=capture(128)
            assert(small>large and small_rows==large_rows)
        ''')


if __name__ == "__main__":
    unittest.main()
