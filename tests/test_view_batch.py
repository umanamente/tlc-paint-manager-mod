"""Native view preparation batches owned primitives without batching native calls."""
import unittest

from lupa.lua54 import LuaRuntime

from test_paint_view import SCRIPTS


FIXTURE = r'''
json=require("paintmanager.json")
view=require("paintmanager.paint_view")
work=require("paintmanager.work_budget")
worker=require("paintmanager.worker_analysis")
model={status="captured",stockComplete=true,rows=json.array()}
for i=1,512 do
    model.rows[i]={id="row"..i,displayName=string.format("Paint %03d",513-i),count=4,
        recipeState="locked",assetType="Item",assetName="DA_Paint_"..i,complete=true}
end
begin_calls,poll_calls,cancel_calls=0,0,0
package.loaded["paintmanager.background"]={supports=function() return true end,
    begin_task=function(kind,payload)
        begin_calls=begin_calls+1
        assert(kind=="analysis_view")
        last_payload=payload
        local child=worker.begin(kind,payload)
        while child.state=="running" do
            local p=child:step_one();assert(p.state~="error",p.error)
        end
        native_result=child:get_result()
        return {
            step_one=function(_,budget)
                poll_calls=poll_calls+1
                if defer_worker then return {state="running",defer=true} end
                return {state="done"}
            end,
            get_result=function() return native_result end,
            cancel=function() cancel_calls=cancel_calls+1 end,
        }
    end,
}
function new_view()
    job=view.begin(model,{tab="ready",include_groups=false,full_catalog=true,max_catalog=512})
    return job
end
function count_budget() return work.new(10000,1,function() return 0 end) end
function advance()
    local budget=count_budget()
    local p=job:step_one(budget)
    budget:consume()
    assert(budget.units==1)
    return p
end
function until_phase(phase)
    for _=1,100 do if job.phase==phase then return end;advance() end
    error("view phase not reached: "..phase)
end
'''


class PaintViewBatchTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def test_copy_batches_128_plain_rows_per_charge_and_separates_worker_admission(self):
        self.lua.execute(r'''
        new_view()
        for i=1,4 do
            advance()
            assert(job.index==i*128+1 and #job.plain_rows==i*128 and begin_calls==0)
            assert(job.completed==i*128 and job.phase=="view_copy")
        end
        advance()
        assert(begin_calls==1 and poll_calls==0 and job.phase=="view_worker")
        assert(#last_payload.model.rows==512 and model.rows[1].__viewIndex==nil)
        ''')

    def test_full_catalog_apply_batches_and_preserves_original_row_identity_and_order(self):
        self.lua.execute(r'''
        new_view();until_phase("view_apply")
        local before=job.completed
        advance()
        assert(#job.result.rows==128 and job.completed-before==128 and poll_calls==1)
        assert(job.result.rows[1]==model.rows[512])
        local steps=1
        while job.state=="running" do advance();steps=steps+1 end
        assert(steps<=6 and #job.result.rows==512 and job.result.matched_count==512)
        for i,r in ipairs(job.result.rows) do assert(r==model.rows[513-i]) end
        assert(model.rows[1].id=="row1" and poll_calls==1 and begin_calls==1)
        ''')

    def test_expired_budget_does_no_plain_copy(self):
        self.lua.execute(r'''
        new_view();local budget=count_budget();budget:consume()
        local p=job:step_one(budget)
        assert(p.state=="running" and job.index==1 and job.completed==0 and begin_calls==0)
        ''')

    def test_shared_deadline_stops_batch_between_plain_rows(self):
        self.lua.execute(r'''
        new_view()
        local now=0
        local budget=work.new(2,8,function() return now end)
        local unit=job._unit
        function job:_unit(b) unit(self,b);now=now+.0005 end
        job:step_one(budget)
        assert(job.index>1 and job.index<129 and job.completed==job.index-1)
        assert(now>=.002 and begin_calls==0 and poll_calls==0 and budget.units==0)
        ''')

    def test_no_budget_call_retains_single_primitive_contract(self):
        self.lua.execute(r'''
        new_view();job:step_one()
        assert(job.index==2 and #job.plain_rows==1 and job.completed==1 and begin_calls==0)
        ''')

    def test_pending_worker_is_polled_once_and_defers_without_plain_batch_spin(self):
        self.lua.execute(r'''
        new_view();until_phase("view_worker");defer_worker=true
        local p=job:step_one(count_budget())
        assert(p.defer and poll_calls==1 and job.phase=="view_worker")
        job:cancel("new model")
        assert(cancel_calls==1 and job.state=="cancelled" and job.input==nil)
        ''')

    def test_invalid_worker_index_stops_apply_without_publishing_partial_result(self):
        self.lua.execute(r'''
        new_view();until_phase("view_apply")
        job.projection.rows[129]=513
        advance();local p=advance()
        assert(p.state=="error" and p.result==nil and p.error:find("Invalid native paint projection index",1,true))
        assert(job.input==nil and job.model==nil)
        ''')


if __name__ == '__main__':
    unittest.main()
