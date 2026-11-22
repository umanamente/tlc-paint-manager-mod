"""Actual bounded Lua executor; accidental companion access is trapped."""
from pathlib import Path
import unittest
from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
PaintManagerWorker=setmetatable({}, {__index=function() error("Removed companion accessed") end})
processing=require("paintmanager.processing");json=require("paintmanager.json")
background=require("paintmanager.background")
function budget(limit)
    local b={units=0,limit=limit or 8}
    function b:expired() return self.units>=self.limit end
    function b:consume(n) self.units=self.units+(n or 1) end
    return b
end
function finish(job)
    for _=1,200000 do local p=job:step(budget());if p.state~="running" then return p end end
    error("Lua executor stalled")
end
function request(rows)
    return background.begin_task("analysis_collection",{mode="source_candidates",selected=json.array(),
        candidates=json.array(rows or {{id="b",distance=2},{id="a",distance=1}}),maximum=2})
end
'''

class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path=SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def test_lua_backend_preserves_source_ownership_and_json_null(self):
        self.lua.execute('''
        assert(background.describe().backend=="lua_cooperative")
        assert(not background.supports("paint_cache_read") and not background.supports("analysis_learn_candidates"))
        local rows={{id="b",distance=2},{id="a",distance=1,payload=json.null}}
        local j=request(rows);assert(j.phase=="admit" and j.driver==nil)
        assert(finish(j).state=="done",j.error)
        local result=j:get_result()
        assert(result[1].id=="a" and result[1].payload==json.null and result[2].id=="b")
        assert(result[1]~=rows[2] and rows[1].id=="b")
        result[1].id="changed";assert(rows[2].id=="a" and not background.is_busy())
        ''')

    def test_snapshot_progress_and_frozen_policy(self):
        self.lua.execute('''
        local policy=processing.snapshot();policy.job_steps_per_unit=1;processing.install_snapshot(policy)
        local rows={};for i=1,100 do rows[i]={id=tostring(i),distance=i} end
        local j=request(rows);j:step(budget());assert(j.phase=="snapshot")
        policy.job_steps_per_unit=128;processing.install_snapshot(policy)
        local before=j.completed;j:step(budget())
        assert(j.phase=="snapshot" and j.completed==before+1)
        assert(processing.get("job_steps_per_unit")==128)
        assert(finish(j).state=="done",j.error)
        ''')

    def test_callbacks_cycles_and_metatables_are_rejected(self):
        self.lua.execute('''
        local cycle={};cycle.self=cycle
        for _,value in ipairs({cycle,setmetatable({},{__index={}}),{fn=function() end}}) do
            local j=background.begin_task("analysis_collection",value)
            assert(finish(j).state=="error")
        end
        assert(not background.is_busy())
        ''')

    def test_admission_backpressure_defers_without_waiting(self):
        self.lua.execute('''
        local p=processing.snapshot();p.job_admission_target=1;processing.install_snapshot(p)
        local first,second=request(),request();first:step(budget());assert(first.reserved)
        local result=second:step(budget());assert(result.state=="running" and result.defer and second.driver==nil)
        first:cancel("replaced");assert(finish(second).state=="done",second.error)
        assert(not background.is_busy())
        ''')

    def test_scope_replacement_retires_snapshot(self):
        self.lua.execute('''
        local j=request();j:step(budget());assert(background.is_busy())
        background.invalidate_scope()
        assert(j:get_progress().state=="cancelled" and j:get_result()==nil and not background.is_busy())
        ''')

    def test_task_failure_restores_processing_policy(self):
        self.lua.execute('''
        local before=processing.snapshot()
        local j=background.begin_task("analysis_view",{model={rows={{id="a"}}},options={sort="bad"}})
        finish(j)
        for key,value in pairs(before) do assert(processing.get(key)==value,key) end
        ''')

    def test_caller_step_one_batches_snapshot_without_charging_caller_budget(self):
        self.lua.execute('''
        local rows={};for i=1,7 do rows[i]={id=tostring(i),distance=i,label="Paint "..i} end
        local j=request(rows);local calls=0
        while j.state=="running" do
            local b=budget();j:step_one(b);assert(b.units==0,"Caller-owned unit was charged twice")
            calls=calls+1;assert(calls<20,"Small snapshot regressed to token-per-frame execution")
        end
        assert(j.state=="done",j.error)
        ''')

if __name__=="__main__":unittest.main()
