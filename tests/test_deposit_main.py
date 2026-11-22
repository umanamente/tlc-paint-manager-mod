"""Controller lifetime and stock invalidation for the native box action."""
import unittest

from lupa.lua54 import LuaRuntime

from test_learning_main import LEARNING_FIXTURE
from test_main_ui import FIXTURE, SCRIPTS


class DepositMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + LEARNING_FIXTURE)
        self.lua.execute('''
            RegisterHook=function() end
            ModRef={}
            service={steps=0,resets=0,publications=0}
            function service:set_settings(settings)
                self.settings=settings
                self.publications=self.publications+1
            end
            function service:active() return self.work==true end
            function service:is_ui_visible() return false end
            function service:step(budget)
                assert(inGameThread and callbacks.guard(ctx))
                budget:consume();self.steps=self.steps+1;self.work=false
                callbacks.changed(ctx)
            end
            function service:reset() self.resets=self.resets+1;self.work=false end
            function service:close() self.closed=true end
            package.loaded["paintmanager.deposit_similar"]={install=function(c, options)
                assert(c.reference_build==options.game_build())
                callbacks=options
                return service
            end}
        ''')

    def test_stock_action_can_run_with_mod_window_closed_and_refreshes_next_view(self):
        self.lua.execute('''
            start();registered.F10();step();assert(ui.model and callbacks)
            registered.F10();step();assert(not ui.open)
            local previous=scans
            service.work=true;step()
            assert(service.steps==1 and scans==previous)
            registered.F10();step();assert(ui.open and ui.model and scans>previous)
            inGameThread=true;ModRef.OnUnload();inGameThread=false
            assert(service.closed)
        ''')

    def test_unverified_build_keeps_original_button(self):
        self.lua.execute('testBuild="other";start();assert(callbacks==nil)')

    def test_dedicated_button_is_wired_without_stock_hooks(self):
        self.lua.execute('''
            RegisterHook=nil
            local ui_module=require("paintmanager.ui")
            ui_module.make_deposit_button=function(tree,serial,key,stock,resources)
                assert(tree=="tree" and key=="label" and stock=="stock" and resources)
                return "bucket",serial+1
            end
            start()
            local button,serial=callbacks.make_button("tree",11,"label","stock")
            assert(button=="bucket" and serial==12)
        ''')

    def test_stock_hook_waits_for_current_players_preferences_without_opening_window(self):
        self.lua.execute('''
            fixtureDeveloperTools=false
            start()
            assert(not service.settings and service.publications==0)
            local document=require("paintmanager.state_document").new()
            local data=document.sections.settings.data
            data.depositSimilarAllColors=false
            data.showDepositAllPaints=true
            assert(fixture_preferences:load(document))
            assert(service.publications==1 and service.settings.depositSimilarAllColors==false)
            assert(service.settings.showDepositAllPaints==true)
            fixture_preferences:reset()
            assert(not service.settings)
            data.depositSimilarAllColors=nil
            data.showDepositAllPaints=nil
            assert(fixture_preferences:load(document))
            assert(service.settings.depositSimilarAllColors==true and service.settings.showDepositAllPaints==false)
        ''')
