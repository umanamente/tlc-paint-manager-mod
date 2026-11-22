"""The shared Escape binding queues a close for the current window only."""
import unittest

from lupa.lua54 import LuaRuntime

from test_main_ui import FIXTURE, SCRIPTS


ESCAPE_FIXTURE = r'''
Key.ESCAPE="ESCAPE"
local register, check = RegisterKeyBind, IsKeyBindRegistered
human_builder_presses = 0
local escape_handlers = {function() human_builder_presses=human_builder_presses+1 end}
function IsKeyBindRegistered(key)
    if key==Key.ESCAPE then return true end
    return check(key)
end
function RegisterKeyBind(key, modifiers, callback)
    if key~=Key.ESCAPE then return register(key, modifiers, callback) end
    assert(#modifiers==0)
    escape_handlers[#escape_handlers+1]=callback
end
function press_escape()
    assert(not inGameThread)
    for _,handler in ipairs(escape_handlers) do handler() end
end
local original=package.loaded["paintmanager.ui"].new
package.loaded["paintmanager.ui"].new=function(c,callbacks)
    local view=original(c,callbacks)
    function view:request_escape()
        if not self.open then return false end
        self.escape_pending=true;return true
    end
    function view:is_closing() return self.escape_pending==true end
    local tick=view.tick
    function view:tick(...)
        if self.open and self.escape_pending then
            self.escape_pending=nil
            self:close("user")
            return
        end
        tick(self,...)
    end
    return view
end
'''


class MainEscapeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + ESCAPE_FIXTURE)

    def test_escape_closes_open_window_on_game_thread_with_other_binding_present(self):
        self.lua.execute('''
        start();assert(IsKeyBindRegistered(Key.ESCAPE))
        press_escape();step();assert(ui==nil and human_builder_presses==1)
        registered.F10();step()
        press_escape();assert(ui.open and ui:is_closing() and #closed==0)
        step();assert(not ui.open and #closed==1 and closed[1]=="user")
        press_escape();step();assert(not ui.open and #closed==1 and human_builder_presses==3)
        registered.F10();step();assert(ui.open and not ui:is_closing())
        ''')

    def test_escape_press_cancels_current_collection_and_discards_queued_commands(self):
        self.lua.execute('''
        start();registered.F10();step()
        jobSlices=100
        reopen_window()
        assert(scans==2)
        assert(invoke("enqueue","snapshot"))
        press_escape()
        assert(not invoke("enqueue","snapshot"))
        step()
        assert(not ui.open and cancellations==1 and snapshots==0)
        for _=1,30 do step() end
        assert(scans==2 and snapshots==0)
        ''')

    def test_escape_takes_priority_over_a_queued_toggle(self):
        self.lua.execute('''
        start();registered.F10();step()
        registered.F10();press_escape();step()
        assert(not ui.open and #closed==1)
        step();assert(not ui.open and #closed==1)
        ''')
