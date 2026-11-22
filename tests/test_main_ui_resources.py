"""Resource warmup wiring through the actual game-thread coordinator."""
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS
from test_cache_main import CACHE_MAIN


RESOURCE_MAIN = r'''
resource_ticks,resource_work,resource_invalidations,resource_resumes=0,0,0,0
resource_icons={identity="original-control-images"}
package.loaded["paintmanager.ui_resources"]={new=function()
    resources={}
    function resources:tick(budget)
        assert(inGameThread,"resource work left the game thread")
        resource_ticks=resource_ticks+1
        if self.suspended or budget:expired() then return end
        local used=0
        while used<2 and not budget:expired() do
            budget:consume();used=used+1;resource_work=resource_work+1
        end
        assert(used<=2)
    end
    function resources:get()
        assert(inGameThread)
        return not self.suspended and resource_icons or nil
    end
    function resources:invalidate(suspend)
        assert(inGameThread);resource_invalidations=resource_invalidations+1
        self.suspended=suspend==true
    end
    function resources:resume()
        assert(inGameThread);resource_resumes=resource_resumes+1;self.suspended=false
    end
    return resources
end}
'''


class MainUIResourcesTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + CACHE_MAIN + RESOURCE_MAIN)

    def test_startup_schedules_resources_without_opening_or_scanning(self):
        self.lua.execute('''
        start();assert(resource_ticks==0 and resource_work==0 and ui==nil)
        once();assert(resource_ticks==1 and resource_work>0)
        assert(ui==nil and scans==0 and jobs==0)
        ''')

    def test_window_callback_reuses_resources_and_close_keeps_warmup(self):
        self.lua.execute('''
        start();once();registered.F10();drain_cache_main()
        assert(ui and ui:is_open() and invoke("icons")==resource_icons)
        local work=resource_work
        registered.F10();drain_cache_main()
        assert(not ui:is_open() and resource_work>work and resource_invalidations==0)
        registered.F10();drain_cache_main()
        assert(ui:is_open() and invoke("icons")==resource_icons)
        ''')

    def test_native_load_suspends_and_resumes_but_pawn_reset_keeps_cache(self):
        self.lua.execute('''
        start();once()
        cache_event={invalidated=true,error="Native map load"};once()
        assert(resource_invalidations==1 and resources.suspended)
        local work=resource_work;once();assert(resource_work==work)
        load_ready();assert(resource_resumes==1 and not resources.suspended)
        drain_cache_main();assert(resource_work>work)
        local invalidations=resource_invalidations
        ctx="worldA/pawnB";once()
        assert(resource_invalidations==invalidations)
        ''')


if __name__ == "__main__":
    unittest.main()
