"""Frozen processing policy and admission on the single Lua job backend."""
from pathlib import Path
import unittest
from lupa.lua54 import LuaRuntime

SCRIPTS=Path(__file__).resolve().parents[1]/"src/PaintManager/Scripts"


class ProcessingBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path=SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute('''
        PaintManagerWorker=setmetatable({},{__index=function()error("retired native bridge")end})
        processing=require("paintmanager.processing");background=require("paintmanager.background")
        json=require("paintmanager.json")
        function task()
            return assert(background.begin_task("analysis_collection",{mode="source_candidates",
                selected=json.array(),candidates=json.array(),maximum=8}))
        end
        function drain(job)
            for _=1,1000 do local p=job:step_one();assert(p.state~="error",p.error)
                if p.state=="done" then return job:get_result() end end
            error("job did not finish")
        end
        ''')

    def test_only_lua_backend_and_supported_tasks_exist(self):
        self.lua.execute('''
        assert(background.describe().backend=="lua_cooperative")
        assert(background.supports("analysis_collection") and background.supports("storage_verify"))
        assert(background.supports("container_cache_read"))
        assert(not background.supports("paint_cache_read") and not background.supports("paint_cache_write"))
        assert(not processing.request({worker_enabled=true}))
        assert(drain(task()) and not background.is_busy())
        ''')

    def test_frozen_policy_survives_later_policy_transition(self):
        self.lua.execute('''
        assert(processing.request({job_steps_per_unit=2}));processing.commit()
        local job=task();assert(job.policy.job_steps_per_unit==2)
        assert(processing.request({job_steps_per_unit=7}));processing.commit()
        assert(job.policy.job_steps_per_unit==2);assert(drain(job))
        assert(processing.get("job_steps_per_unit")==7)
        ''')

    def test_admission_defers_and_releases_without_a_wait_loop(self):
        self.lua.execute('''
        assert(processing.request({job_admission_target=1}));processing.commit()
        local first,second=task(),task();first:step_one()
        local p=second:step_one();assert(p.defer and p.phase=="admit")
        assert(drain(first));assert(second:step_one().defer==false);assert(drain(second))
        assert(not background.is_busy())
        ''')

    def test_tab_switch_yields_single_permit_and_resumes_the_same_cursor(self):
        self.lua.execute('''
        assert(processing.request({job_admission_target=1}));processing.commit()
        local paint,visible=task(),task()
        paint:step_one();paint:step_one()
        local cursor=paint.copier
        assert(visible:step_one().defer)
        background.yield_paint_slots()
        assert(not visible:step_one().defer and paint:step_one().defer)
        assert(paint.copier==cursor)
        drain(visible);drain(paint);assert(not background.is_busy())
        ''')

    def test_monotonic_measurements_use_existing_unreal_clock_only(self):
        self.lua.execute('''
        assert(background.monotonic_ms()==nil)
        function StaticFindObject(path)
            return {IsValid=function()return true end,IsA=function()return true end,
                GetAccurateRealTime=function(_,seconds,partial)
                    assert(seconds==partial);seconds.Seconds=12;partial.PartialSeconds=.25
                end}
        end
        assert(background.monotonic_ms()==12250)
        ''')


if __name__=="__main__":unittest.main()
