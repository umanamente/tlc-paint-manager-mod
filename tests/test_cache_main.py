"""Lifecycle, priority and UI wiring through the real main.lua scheduler."""
import unittest

from lupa.lua54 import LuaRuntime
from test_main_ui import FIXTURE, SCRIPTS


CACHE_MAIN = r'''
cache_steps,cache_admissions,cache_resets,cache_observed=0,0,0,0
cache_ready_at=30
account="76561198000000001";generation=1
function scope()
    return {contextToken=ctx,scopeId=ctx.."/hud/state",accountId=account,
        loadGeneration=generation,libraryPath="C:/Saves/"..account.."/PaintManager.library.json"}
end
package.loaded["paintmanager.save_lifecycle"]={
    install=function() return true end,
    consume=function() local event=cache_event;cache_event=nil;return event end,
}
package.loaded["paintmanager.save_bridge"]={storage_context=function()
    assert(inGameThread);local value=scope();value.status="ready";return value
end}
package.loaded["paintmanager.paint_cache"]={new=function()
    cache={}
    function cache:begin_open() self.work,self.finished,self.started=0,nil,true end
    function cache:quiesce() self.started=false;return true end
    function cache:binding() return self.bound end
    function cache:admit(event)
        assert(inGameThread);assert(event.accountId and event.libraryPath and event.scopeId)
        cache_admissions=cache_admissions+1;self.bound=event;self.work=0;self.finished=nil;self.started=false
        return true
    end
    function cache:invalidate()
        cache_resets=cache_resets+1;self.bound=nil;self.finished=nil;self.work=0;self.started=false
    end
    function cache:step(budget)
        assert(inGameThread and self.bound)
        if not self.started then return end
        while not budget:expired() and not self.finished do
            cache_steps=cache_steps+1;self.work=self.work+1;budget:consume()
            if self.work>=cache_ready_at then
                self.finished={contextToken=self.bound.contextToken,status="catalog_ready",rows={}}
            end
        end
        self.promoted=true
        return {state=self.finished and "ready" or "warming"}
    end
    function cache:learned_capture()
        if self.work>=2 then return self.learned end
    end
    function cache:catalog() return self.finished end
    function cache:preview()
        if self.bound and self.work>=2 then
            return {contextToken=self.bound.contextToken,status="preparing",stockComplete=false,rows={}},self.work
        end
    end
    function cache:observe(model) cache_observed=cache_observed+1;assert(model.status=="captured") end
    return cache
end}
local original_begin=package.loaded["paintmanager.collection"].begin
package.loaded["paintmanager.collection"].begin=function(c,execution)

    last_catalog=execution and execution.catalog
    return original_begin(c)
end
local original_ui=package.loaded["paintmanager.ui"].new
package.loaded["paintmanager.ui"].new=function(c,callbacks)
    local result=original_ui(c,callbacks)
    function result:render_tick(budget)
        assert(inGameThread);render_calls=(render_calls or 0)+1
        assert(budget.units<=config.work_units_per_tick)
    end
    return result
end
function once() inGameThread=true;pump();inGameThread=false end
function load_ready()
    cache_event=scope();cache_event.status="ready";once()
    if fixtureDeveloperTools~=false then
        inGameThread=true;seed_fixture_developer_tools(true);inGameThread=false
    end
end
function drain_cache_main() for _=1,100 do once() end end
'''


class CacheMainTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + CACHE_MAIN)

    def test_proven_load_admits_catalog_but_closed_idle_does_no_preparation(self):
        self.lua.execute('''
        start();drain_cache_main();assert(cache_steps==0 and scans==0 and ui==nil)
        load_ready();drain_cache_main()
        assert(cache_admissions==1 and cache_steps==0 and cache:catalog()==nil and cache.bound)
        assert(scans==0 and ui==nil and snapshots==0 and discoveries==0 and dumps==0)
        ''')

    def test_each_open_refreshes_catalog_once_and_uses_it_for_stock(self):
        self.lua.execute('''
        start();load_ready();for _=1,4 do once() end
        local partial=cache.work;assert(partial==0 and cache.finished==nil)
        registered.F10();for _=1,8 do once() end
        assert(cache.promoted and cache_admissions==1 and cache.work>partial)
        drain_cache_main();assert(ui.open and scans==1 and last_catalog==cache.finished)
        assert(cache_observed==1 and render_calls>0)
        assert(not invoke("enqueue","refresh"));reopen_window();drain_cache_main()
        assert(scans==2 and cache_admissions==1 and cache_steps==60 and cache_observed==2)
        ''')

    def test_visible_learned_page_borrows_preference_writer_unit_and_resumes_it(self):
        self.lua.execute('''
        start();load_ready();drain_cache_main();registered.F10()
        for _=1,80 do if cache.started then break end;once() end
        assert(cache.started)
        config.work_units_per_tick=1
        ui.active_tab="learned"
        cache.learned={contextToken=ctx,learnedOnly=true,stockComplete=false,rows={}}
        local writes,yields,drawn=0,0,0
        function ui:show_learned(prefix)
            self.model=prefix;self.remaining=2;return true
        end
        function ui:is_rendering() return (self.remaining or 0)>0 end
        function ui:render_tick(budget)
            if self:is_rendering() and not budget:expired() then
                assert(writes==0);budget:consume();self.remaining=self.remaining-1;drawn=drawn+1
            end
        end
        fixture_preferences.active={job={yield_execution=function() yields=yields+1 end}}
        function fixture_preferences:step(budget)
            if self.active then
                writes=writes+1;budget:consume();self.active=nil
            end
        end
        for _=1,100 do once() end
        assert(yields>0 and drawn==2 and writes==1 and scans==1)
        ''')

    def test_close_stops_all_catalog_and_stock_work(self):
        self.lua.execute('''
        cache_ready_at=200;start();load_ready();registered.F10()
        for _=1,5 do once() end
        assert(ui.open and cache.work<200)
        registered.F10();for _=1,3 do once() end
        assert(not ui.open and cache.bound and cache_admissions==1 and scans==0)
        local prior=cache.work;for _=1,250 do once() end
        assert(cache.work==prior and not cache:catalog() and scans==0)
        ''')

    def test_closed_window_diagnostic_uses_its_own_capture(self):
        self.lua.execute('''
        start();load_ready();registered.F6();drain_cache_main()
        assert(ui==nil and not cache.promoted and not cache:catalog() and cache_steps==0)
        assert(scans==1 and snapshots==1 and #written==1)
        ''')

    def test_same_world_account_drift_revokes_unfinished_catalog(self):
        self.lua.execute('''
        start();load_ready();registered.F10();once();local old=cache.bound;account="76561198000000002";once()
        assert(not cache.bound and not cache:catalog() and scans==0)
        generation=generation+1;load_ready();registered.F6();drain_cache_main()
        assert(cache_admissions==2 and cache.bound.accountId==account)
        assert(cache.finished==nil and old.accountId~=cache.bound.accountId and scans==1)
        ''')

    def test_idle_window_preserves_foreground_partial_warning(self):
        self.lua.execute('''
        config.ui_progress_delay_ms=0
        partial=true;start();load_ready();registered.F10();drain_cache_main()
        assert(ui.open and ui.progress.state=="error" and ui.progress.coverage=="partial")
        local previous=ui.progress
        cache.finished=nil;cache.work=0;cache_ready_at=1000
        function cache:describe()
            return {presentation={kind="recipes",phase="catalog",mode="indeterminate"}}
        end
        for _=1,100 do once() end
        assert(cache.work==0 and not cache:catalog())
        assert(ui.progress==previous,"background maintenance replaced a persistent foreground warning")
        cache_ready_at=cache.work+1;drain_cache_main()
        assert(not cache:catalog() and ui.progress==previous)
        ''')

    def test_map_pre_invalidation_retires_warmup_and_preview(self):
        self.lua.execute('''
        start();load_ready();registered.F10();for _=1,5 do once() end
        cache_event={invalidated=true,loadGeneration=2};once();drain_cache_main()
        assert(not cache.bound and not cache:catalog() and not ui.open)
        assert(ui.model==nil and scans==0)
        ''')


if __name__ == "__main__":
    unittest.main()
