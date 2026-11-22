"""Queue/lifetime integration tests. Native UMG needs the separate game test."""

import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
fixtureConnected=true
fixtureShipToken="ship-fixture"
package.loaded["paintmanager.ship_scope"]={
    player_access=function(c)
        if fixtureConnected==false then return nil,"Signal lost",{connected=false,distanceCm=99999,radiusCm=10000} end
        return {token=fixtureShipToken,center={X=0,Y=0,Z=0},radiusCm=10000},nil,{connected=true,distanceCm=0,radiusCm=10000}
    end,
    prepare=function() return {token="ship-fixture",radiusCm=10000} end,
    invalidate=function() end,
}
config=require("paintmanager.config")
json=require("paintmanager.json")
-- Historical diagnostic tests explicitly opt in through the real preference
-- coordinator. New player/default tests disable this fixture-only seed.
local actual_preferences=require("paintmanager.ui_preferences")
local preferences_new=actual_preferences.new
function seed_fixture_developer_tools(enabled)
    local seed=require("paintmanager.state_document").new()
    seed.sections.settings.data.showDeveloperTools=enabled
    assert(fixture_preferences:load(seed))
end
actual_preferences.new=function(callbacks)
    local controller=preferences_new(callbacks)
    fixture_preferences=controller
    if fixtureDeveloperTools~=false then seed_fixture_developer_tools(true) end
    return controller
end
config.pump_ms=100; config.context_poll_ms=100
inGameThread=false
ctx="worldA/pawnA"
scans,snapshots,discoveries,dumps,uiTicks,jobs,cancellations=0,0,0,0,0,0,0
registered,logs,written,statuses,models,closed={},{},{},{},{},{}
local function native()
    assert(inGameThread,"native operation outside game thread")
end
package.loaded["paintmanager.collection"]={
    context=function() native(); return ctx,ctx==nil and "no character" or nil end,
    begin_catalog=function(c,options)
        local job={model={contextToken=ctx,rows={},catalogComplete=true,recipeCatalogComplete=true}}
        function job:step(budget) native();budget:consume();return {state="done"} end
        function job:get_catalog() return self.model end
        function job:get_learned_capture() end
        function job:cancel() end
        return job
    end,
    scan=function() error("synchronous scan must never be used by main") end,
    begin=function(c)
        jobs=jobs+1
        local job={steps=0,radius=c.ship_radius_cm,ship=fixtureShipToken}
        function job:cancel(reason) self.cancelled=reason;cancellations=cancellations+1 end
        function job:step(budget)
            native()
            if self.cancelled then return {state="cancelled",error=self.cancelled} end
            if budget:expired() then return {state="running",phase="inventory",processed=self.steps,total=jobSlices or 2} end
            budget:consume();self.steps=self.steps+1
            if self.steps==1 then
                scans=scans+1;self.token=ctx
                if scanError then error("collection failed") end
            end
            if self.steps<(jobSlices or 2) then
                return {state="running",phase="inventory",processed=self.steps,total=jobSlices or 2}
            end
            local count=ctx=="worldA/pawnA" and 4 or 8
            return {state="done",model={contextToken=self.token,status=changedDuringScan and "context_changed" or "captured",
                schemaVersion=2,complete=not partial,stockComplete=not partial,physicalCount=count,stockScope={token=self.ship},
                includedSourceCount=1,sourceCount=1,sourceInclusion="automatic_all_nearby_stock_boxes",
                sources=json.array({{id="source1",included=true}}),
                rows=json.array({{id="paint1",count=count}}),radiusCm=self.radius}}
        end
        return job
    end,
}
package.loaded["paintmanager.game_adapter"]={
    snapshot=function() error("main must use the resumable snapshot job") end,
    begin=function()
        local job={}
        function job:cancel(reason) self.cancelled=reason end
        function job:step(budget)
            native()
            if self.cancelled then return {state="cancelled",error=self.cancelled} end
            if budget:expired() then return {state="running",phase="diagnostic_inventory"} end
            budget:consume();snapshots=snapshots+1
            if changeDuringSnapshot then ctx="worldB/pawnB" end
            return {state="done",data={complete=true,paintCandidates={backpack={paint1=99}}}}
        end
        return job
    end,
}
-- Controller fixtures use a one-unit writer; streaming/real I/O have separate
-- integration tests. Creation performs no encoding or file operations.
package.loaded["paintmanager.report_writer"]={begin=function(path,report)
    local job={}
    function job:cancel(reason) self.cancelled=reason end
    function job:step(budget)
        if self.cancelled then return {state="cancelled",error=self.cancelled} end
        if budget:expired() then return {state="running",phase="encode"} end
        budget:consume()
        local file,err=io.open(path..".tmp","wb")
        if not file then return {state="error",error=err} end
        file:write(json.encode(report));file:close();os.rename(path..".tmp",path)
        return {state="done",path=path}
    end
    return job
end}
package.loaded["paintmanager.reflection"]={discover=function()
    native(); discoveries=discoveries+1; return {complete=true}
end}
-- UI/learning tests isolate storage behind a one-unit fixture. The actual
-- cooperative save repository is exercised by test_save_main and test_save_store.
package.loaded["paintmanager.save_store"]={new=function(c,token)
    local function job()
        return {cancel=function() end,get_result=function() return {sections={settings={data={}}}} end,
            step=function(self,budget)
                native();budget:consume()
                return {state="done",snapshot={scopeId=token},missing=true}
            end}
    end
    return {begin_state_load=job}
end}
package.loaded["paintmanager.ui"]={new=function(c,callbacks)
    native(); cb=callbacks
    ui={open=false}
    function ui:is_open() return self.open end
    function ui:toggle()
        native()
        if openError then return false,"cannot build UI" end
        self.open=not self.open; return self.open
    end
    function ui:close(reason)
        native(); self.open=false; closed[#closed+1]=reason
        if recurseClosePump then pump() end
    end
    function ui:tick()
        native(); uiTicks=uiTicks+1
        if baselineClock then simulatedClock=simulatedClock+0.010 end
        if closeFromTick and self.open then closeFromTick=false;self:close("user") end
        if failDeferredRender and self.model then
            self:close("deferred render error");renderPending=false;failDeferredRender=false
            self.status={state="error",message="deferred render failed"}
        end
        if recursePump then pump() end
        if tickError then error("UI tick failed") end
    end
    function ui:set_model(model)
        native(); self.model=model; models[#models+1]=model or false
        if failDeferredRender and model then renderPending=true end
    end
    function ui:set_status(status) native(); self.status=status;statuses[#statuses+1]=status end
    function ui:set_progress(progress)
        native();self.progress=progress;self.progressUpdates=(self.progressUpdates or 0)+1
    end
    function ui:set_preferences(settings,favorites)
        native();self.preferences=settings;self.favorites=favorites
    end
    function ui:is_rendering() return renderPending==true end
    function ui:render_progress() return {phase="render",completed=1,total=2} end
    return ui
end}
print=function(message)
    if failLogOnce then failLogOnce=false; error("unexpected logging failure") end
    logs[#logs+1]=message
end
Key={F6="F6",F10="F10",O="O",J="J"}
ModifierKey={CONTROL="ctrl",ALT="alt"}
function IsKeyBindRegistered(key) return conflict==key end
function RegisterKeyBind(key,modifiers,callback) registered[key]=callback end
loop_handles={}
fixture_wall_ms=0
os.clock=function() return fixture_wall_ms/1000 end
local function register_loop(mode,interval,callback)
    local handle=#loop_handles+1
    local timed=function()
        fixture_wall_ms=fixture_wall_ms+(fixture_elapsed_ms or config.pump_ms)
        return callback()
    end
    loop_handles[handle]={mode=mode,interval=interval,callback=timed}
    pump=timed
    return handle
end
function LoopInGameThreadWithDelay(ms,callback) return register_loop("delay",ms,callback) end
function LoopInGameThreadAfterFrames(frames,callback) return register_loop("frame",frames,callback) end
function CancelDelayedAction(handle)
    local loop=assert(loop_handles[handle],"unknown scheduler handle")
    loop.cancelled=true
    if pump==loop.callback then pump=nil end
    return true
end
function DumpJMAP(a,b) native(); assert(a==true and b==true); dumps=dumps+1 end
io.open=function(path,mode)
    assert(mode=="wb")
    if writeError then return nil,"disk unavailable" end
    local f={}
    function f:write(value) written[#written+1]={path=path,text=value}; return self end
    function f:close() return true end
    return f
end
os.rename=function() return true end
function step()
    inGameThread=true
    -- The default tiny collector fixture deliberately takes multiple pumps.
    local ok,err=pcall(function() for i=1,8 do pump() end end)
    inGameThread=false
    assert(ok,err)
end
function reopen_window()
    registered.F10();step();assert(not ui.open)
    registered.F10();step();assert(ui.open)
end
function load_ship_radius(cm)
    local document=require("paintmanager.state_document").new()
    document.sections.settings.data.shipRadiusCm=cm
    inGameThread=true
    assert(fixture_preferences:load(document))
    inGameThread=false
end
function invoke(name,...)
    inGameThread=true
    local results={cb[name](...)}
    inGameThread=false
    return table.unpack(results)
end
function start() assert(loadfile(scripts_path.."/main.lua"))() end
'''


class MainUiTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, source):
        return self.lua.execute(source)

    def open_window(self):
        self.run_lua("start(); registered.F10(); step()")

    def test_culture_getter_runs_in_game_thread_and_manual_language_survives_native_changes(self):
        self.run_lua('''
            fixtureDeveloperTools=false
            local messages=require("paintmanager.i18n")
            local language,calls,bad_thread="ru-RU",0,false
            local service={IsValid=function() return true end,GetCurrentLanguage=function()
                calls=calls+1
                if not inGameThread then bad_thread=true end
                if language then return {ToString=function() return language end} end
            end}
            local find=StaticFindObject
            StaticFindObject=function(path)
                if path=="/Script/Engine.Default__KismetInternationalizationLibrary" then return service end
                if find then return find(path) end
            end
            local function restore(preference)
                local document=require("paintmanager.state_document").new()
                document.sections.settings.data.uiLanguage=preference
                inGameThread=true;assert(fixture_preferences:load(document));inGameThread=false
            end
            start();registered.F10();assert(calls==0)
            step();assert(ui.open and messages.language=="ru" and calls>0 and not bad_thread)
            restore("en");language="ru-RU";step();assert(messages.language=="en")
            restore("auto");assert(messages.language=="ru")
            language=nil;step();step();assert(messages.language=="ru")
            language="de-DE";step();step();assert(messages.language=="de")
            language="ko-KR";step();step();assert(messages.language=="en")
            language="en-US";restore("ru");step();assert(messages.language=="ru" and not bad_thread)
        ''')

    def test_open_housekeeping_uses_frame_elapsed_and_rebases_invalid_clock(self):
        self.run_lua('''
            local progress=require("paintmanager.ui_progress");local new=progress.new
            progress.new=function(...)
                local tracker=new(...);local advance=tracker.pump
                function tracker:pump(now) last_now=now;return advance(self,now) end
                return tracker
            end
            start();registered.F10();step()
            assert(ui:is_open() and last_now)
            local function check(delta,expected)
                local before=last_now;fixture_elapsed_ms=delta
                inGameThread=true;pump();inGameThread=false
                assert(math.abs(last_now-before-expected)<0.001,tostring(last_now-before))
            end
            check(6,6);check(0,0);check(33,33);check(-8,0);check(12,0);check(17,17)
            local clock=os.clock;os.clock=function() return 0/0 end
            check(10,config.pump_ms)
            os.clock=clock;check(18,0);check(7,7)
        ''')

    def start_scheduler(self, setup=""):
        self.run_lua('''
            -- Use the shipped interval here. Other controller tests speed up
            -- context polling by setting their nominal callback interval.
            config.pump_ms=assert(loadfile(scripts_path.."/paintmanager/config.lua"))().pump_ms
            ModRef={}
            function only_loop(mode,interval)
                local live
                for _,loop in ipairs(loop_handles) do
                    if not loop.cancelled then
                        assert(not live,"two live scheduler handles")
                        live=loop
                    end
                end
                assert(live and live.mode==mode and live.interval==interval,"wrong scheduler mode or interval")
                assert(pump==live.callback)
                return live
            end
            function fire(loop)
                inGameThread=true
                local ok,err=pcall(loop.callback)
                inGameThread=false
                assert(ok,err)
            end
            function toggle_on(loop,opened)
                registered.F10();registered.F10()
                for _=1,20 do
                    fire(loop)
                    if ui and ui.open==opened then return end
                end
                error("toggle did not finish on the current loop")
            end
        ''' + setup + '\nstart()')

    def test_open_and_close_switch_scheduler_at_the_actual_visibility_transition(self):
        self.start_scheduler()
        self.run_lua('''
            local idle=only_loop("delay",16)
            assert(not ui and #loop_handles==1)
            toggle_on(idle,true)
            local open=only_loop("frame",1)
            assert(idle.cancelled and #loop_handles==2)
            local before=uiTicks;fire(idle);assert(uiTicks==before and ui.open)
            for _=1,20 do fire(open) end
            assert(uiTicks==before+20 and #loop_handles==2 and scans==1)
            toggle_on(open,false)
            local closed=only_loop("delay",16)
            assert(open.cancelled and #loop_handles==3)
            toggle_on(closed,true)
            local reopened=only_loop("frame",1)
            -- Old frame callbacks can arrive after the mode returns to frame.
            before=uiTicks;fire(open);assert(uiTicks==before)
            closeFromTick=true;fire(reopened)
            assert(not ui.open and reopened.cancelled)
            only_loop("delay",16);assert(#loop_handles==5)
        ''')

    def test_native_interfaces_share_foreground_budget_and_frame_loop(self):
        from test_learning_main import LEARNING_FIXTURE

        self.run_lua(LEARNING_FIXTURE)
        self.start_scheduler('''
            config.open_work_budget_ms,config.work_budget_ms=18,3
            local editor,container={},{}
            function editor:tick(elapsed) self.visible=show_editor;last_elapsed=elapsed end
            function editor:is_ui_visible() return self.visible end
            function editor:reset() self.visible=false end
            function container:active() return container_job or show_container end
            function container:is_ui_visible() return show_container end
            function container:set_settings() end
            function container:step(budget,elapsed) container_ms=budget.target_ms end
            package.loaded["paintmanager.native_editor"]={new=function() return editor end}
            package.loaded["paintmanager.deposit_similar"]={install=function() return container end}
            local background=require("paintmanager.background")
            local pump=background.pump
            background.pump=function(budget) last_target=budget.target_ms;return pump(budget) end
        ''')
        self.run_lua('''
            local idle=only_loop("delay",16)
            fire(idle);assert(last_target==3 and not ui)
            show_editor=true;fire(idle)
            local frame=only_loop("frame",1)
            assert(last_target==18 and #loop_handles==2)
            fire(frame);fixture_elapsed_ms=6;fire(frame);assert(math.abs(last_elapsed-6)<0.001)
            fixture_elapsed_ms=45;fire(frame);assert(math.abs(last_elapsed-45)<0.001)
            show_container=true;fire(frame);assert(container_ms==18)
            show_editor=false;fire(frame);assert(last_target==18 and only_loop("frame",1)==frame)
            show_container=false;container_job=true;fire(frame)
            assert(container_ms==3 and last_target==3 and frame.cancelled)
            only_loop("delay",16);assert(#loop_handles==3 and not ui)
        ''')

    def test_unload_invalidates_frame_and_delayed_callbacks_before_ui_cleanup(self):
        for opened in (True, False):
            with self.subTest(opened=opened):
                self.setUp()
                self.start_scheduler()
                self.lua.globals().leave_open = opened
                self.run_lua('''
                    toggle_on(only_loop("delay",16),true)
                    local frame=only_loop("frame",1)
                    if not leave_open then toggle_on(frame,false) end
                    local active=only_loop(leave_open and "frame" or "delay",leave_open and 1 or 16)
                    function ui:shutdown()
                        -- Engine shutdown can re-enter an already queued callback.
                        active.callback();self:close("shutdown")
                    end
                    local before,count=uiTicks,#loop_handles
                    inGameThread=true;ModRef.OnUnload();inGameThread=false
                    for _,loop in ipairs(loop_handles) do assert(loop.cancelled);fire(loop) end
                    assert(uiTicks==before and #loop_handles==count and not ui.open and not pump)
                ''')

    def test_failed_scheduler_registration_keeps_working_loop_until_replacement(self):
        for failure in ("throw", "nil"):
            with self.subTest(failure=failure):
                self.setUp()
                self.lua.globals().failure = failure
                self.start_scheduler('''
                    local real_frame=LoopInGameThreadAfterFrames
                    function LoopInGameThreadAfterFrames(frames,callback)
                        if failure then
                            failed_attempt=true
                            if failure=="throw" then error("frame hook unavailable") end
                            return nil
                        end
                        return real_frame(frames,callback)
                    end
                ''')
                self.run_lua('''
                    local idle=only_loop("delay",16)
                    toggle_on(idle,true);assert(failed_attempt and only_loop("delay",16)==idle)
                    local before=uiTicks
                    for _=1,8 do fire(idle) end
                    assert(uiTicks==before+8 and not idle.cancelled)
                    failure=nil
                    for _=1,100 do if idle.cancelled then break end;fire(idle) end
                    assert(idle.cancelled and only_loop("frame",1))
                    before=uiTicks;fire(idle);assert(uiTicks==before)
                ''')

    def test_failed_close_timer_limits_closed_work_by_elapsed_time_then_recovers(self):
        self.start_scheduler('''
            local register_timer=LoopInGameThreadWithDelay
            function LoopInGameThreadWithDelay(ms,callback)
                if reject_timer then
                    timer_failures=(timer_failures or 0)+1
                    error("closed timer unavailable")
                end
                return register_timer(ms,callback)
            end
        ''')
        self.run_lua('''
            toggle_on(only_loop("delay",16),true)
            local frame=only_loop("frame",1)
            reject_timer=true;closeFromTick=true;fire(frame)
            assert(not ui.open and timer_failures==1 and only_loop("frame",1)==frame)
            local before=uiTicks
            fixture_elapsed_ms=5;fire(frame);assert(uiTicks==before,"closed pump after 5 ms")
            fire(frame);assert(uiTicks==before,"closed pump after 10 ms")
            fixture_elapsed_ms=6;fire(frame);assert(uiTicks==before+1,"closed pump missing at 16 ms")
            reject_timer=false;fixture_elapsed_ms=16
            for _=1,100 do if frame.cancelled then break end;fire(frame) end
            local idle=only_loop("delay",16)
            assert(frame.cancelled and #loop_handles==3)
            before=uiTicks;fire(frame);assert(uiTicks==before)
            fire(idle);assert(uiTicks==before+1 and not ui.open)
        ''')

    def test_failed_native_cancellation_leaves_the_old_handle_inert(self):
        self.start_scheduler('''
            local cancel=CancelDelayedAction
            function CancelDelayedAction(handle)
                if handle==1 and not cancel_allowed then error("timer cancellation unavailable") end
                return cancel(handle)
            end
        ''')

        self.run_lua('''
            local idle=only_loop("delay",16);toggle_on(idle,true)
            local open=loop_handles[2]
            assert(open.mode=="frame" and pump==open.callback and not idle.cancelled)
            local before=uiTicks
            fire(idle);fire(idle);assert(uiTicks==before)
            fire(open);assert(uiTicks==before+1)
            cancel_allowed=true;fire(idle)
            assert(uiTicks==before+1 and #loop_handles==2 and pump==open.callback)
        ''')

    def test_missing_frame_scheduler_keeps_input_and_collection_on_the_delayed_loop(self):
        self.start_scheduler('LoopInGameThreadAfterFrames=nil')
        self.run_lua('''
            local idle=only_loop("delay",16)
            toggle_on(idle,true)
            for _=1,40 do fire(idle) end
            assert(ui.open and ui.model and scans==1 and only_loop("delay",16)==idle)
            closeFromTick=true;fire(idle)
            assert(not ui.open and only_loop("delay",16)==idle and #loop_handles==1)
        ''')

    def test_startup_and_key_callbacks_never_touch_native_objects(self):
        self.run_lua("start(); registered.F10(); registered.F10()")
        self.assertEqual(self.lua.globals().scans, 0)
        self.assertIsNone(self.lua.globals().ui)
        self.run_lua("step()")
        self.assertTrue(self.lua.globals().ui.open)
        self.assertEqual(self.lua.globals().scans, 1)
        self.assertEqual(self.lua.globals().dumps, 0)
        self.assertEqual(self.lua.globals().discoveries, 0)

    def test_idle_open_window_does_not_rescan_or_start_heavy_work(self):
        self.open_window()
        self.run_lua("for i=1,40 do step() end")
        self.assertEqual(self.lua.globals().scans, 1)
        self.assertGreater(self.lua.globals().uiTicks, 0)
        self.assertEqual(self.lua.globals().dumps, 0)
        self.assertEqual(self.lua.globals().discoveries, 0)

    def test_pump_switches_deadline_on_open_hotkey_close_and_input_close(self):
        for opened_ms, closed_ms in ((30, 2), (12.5, 4)):
            with self.subTest(opened_ms=opened_ms, closed_ms=closed_ms):
                self.setUp()
                if opened_ms != 30:
                    self.run_lua('config.open_work_budget_ms=12.5;config.work_budget_ms=4')
                self.lua.globals().opened_ms = opened_ms
                self.lua.globals().closed_ms = closed_ms
                self.run_lua('''
                    local function expected() return ui and ui.open and opened_ms or closed_ms end
                    local background=require("paintmanager.background")
                    local original_pump=background.pump
                    background.pump=function(budget)
                        assert(budget.target_ms==expected(),"foreground/background budget after input")
                        background_ticks=(background_ticks or 0)+1
                        return original_pump(budget)
                    end
                    local clock=0
                    os.clock=function() return clock end
                    local original_new=require("paintmanager.ui").new
                    require("paintmanager.ui").new=function(...)
                        local w=original_new(...)
                        local toggle,tick=w.toggle,w.tick
                        function w:toggle()
                            clock=clock+.006
                            self.check_toggle_cost=true
                            return toggle(self)
                        end
                        function w:tick(budget,...)
                            assert(budget.target_ms==expected(),"input budget at callback start")
                            return tick(self,budget,...)
                        end
                        function w:render_tick(budget)
                            assert(budget.target_ms==expected(),"render after a transition")
                            if self.check_toggle_cost then
                                assert(budget.units>=1 and budget:elapsed_ms()>5,"toggle was refunded")
                                self.check_toggle_cost=false
                                renders_after_toggle=(renders_after_toggle or 0)+1
                            end
                        end
                        return w
                    end
                    start();step();assert(background_ticks>0 and not ui)
                    registered.F10();step();assert(ui.open)
                    registered.F10();step();assert(not ui.open)
                    registered.F10();step();assert(ui.open and renders_after_toggle==3)
                    closeFromTick=true;step();assert(not ui.open)
                ''')

    def test_heavy_command_yields_to_engine_before_work_and_reentrant_tick_is_ignored(self):
        self.open_window()
        self.run_lua('''
            recursePump=true; registered.J()
            inGameThread=true; pump(); inGameThread=false
        ''')
        self.assertEqual(self.lua.globals().dumps, 0)
        status = self.lua.globals().statuses[len(self.lua.globals().statuses)]
        self.assertEqual(status.state, "running")
        self.run_lua("step()")
        self.assertEqual(self.lua.globals().dumps, 1)

    def test_long_collection_spans_pumps_and_publishes_only_when_finished(self):
        self.run_lua('jobSlices=12; start();registered.F10();step()')
        self.assertTrue(self.lua.globals().ui.open)
        self.assertIsNone(self.lua.globals().ui.model)
        self.run_lua('step()')
        self.assertEqual(self.lua.globals().ui.model.physicalCount, 4)

    def test_close_hotkey_cancels_inflight_collection_without_publishing(self):
        self.run_lua('jobSlices=30;start();registered.F10();step();registered.F10();step()')
        self.assertFalse(self.lua.globals().ui.open)
        self.assertIsNone(self.lua.globals().ui.model)
        self.assertGreater(self.lua.globals().cancellations, 0)

    def test_radius_change_cancels_old_capture_and_uses_latest_radius(self):
        self.run_lua('jobSlices=30;start();registered.F10();step();load_ship_radius(4500);jobSlices=2;step();step()')
        self.assertIsNone(self.lua.globals().ui.model)
        self.run_lua('reopen_window()')
        self.assertEqual(self.lua.globals().ui.model.radiusCm, 4500)
        self.assertGreater(self.lua.globals().cancellations, 0)

    def test_close_preempts_heavy_command_that_has_only_been_staged(self):
        self.open_window()
        self.run_lua('registered.J();inGameThread=true;pump();inGameThread=false;registered.F10();step()')
        self.assertEqual(self.lua.globals().dumps, 0)
        self.assertFalse(self.lua.globals().ui.open)

    def test_close_remains_available_when_normal_queue_is_full(self):
        self.open_window()
        self.run_lua('''
            invoke("enqueue","refresh");invoke("enqueue","snapshot")
            invoke("enqueue","discovery");invoke("enqueue","jmap")
            registered.F10();step()
        ''')
        self.assertFalse(self.lua.globals().ui.open)
        self.assertEqual(self.lua.globals().dumps, 0)
        self.assertEqual(self.lua.globals().discoveries, 0)
        self.assertEqual(self.lua.globals().snapshots, 0)

    def test_native_close_button_discards_staged_and_queued_heavy_work(self):
        self.open_window()
        self.run_lua('''
            registered.J();invoke("enqueue","discovery")
            inGameThread=true;pump();inGameThread=false -- JMAP staged, not executed
            closeFromTick=true;step()
        ''')
        self.assertFalse(self.lua.globals().ui.open)
        self.assertEqual(self.lua.globals().dumps, 0)
        self.assertEqual(self.lua.globals().discoveries, 0)

    def test_deferred_render_failure_is_never_reported_as_success(self):
        self.run_lua('failDeferredRender=true;start();registered.F10();step()')
        self.assertFalse(self.lua.globals().ui.open)
        status = self.lua.globals().statuses[len(self.lua.globals().statuses)]
        self.assertEqual(status.state, "error")
        self.assertIn("deferred render failed", status.message)

    def test_baseline_housekeeping_overrun_does_not_starve_work(self):
        self.run_lua('''
            simulatedClock=0;baselineClock=true
            os.clock=function() return simulatedClock end
            start();registered.F10();step()
        ''')
        self.assertEqual(self.lua.globals().ui.model.physicalCount, 4)

    def test_open_counts_all_sources_without_any_selection_callback(self):
        self.open_window()
        self.assertIsNone(self.lua.globals().cb.toggle_source)
        self.assertEqual(self.lua.globals().ui.model.rows[1].count, 4)
        self.run_lua('assert(not invoke("enqueue","refresh")); reopen_window()')
        self.assertEqual(self.lua.globals().ui.model.rows[1].count, 4)

    def test_context_change_clears_view_and_old_dump_then_reopen_counts_new_world(self):
        self.open_window()
        self.run_lua('registered.J(); ctx="worldB/pawnB"; step()')
        self.assertFalse(self.lua.globals().ui.open)
        self.assertIsNone(self.lua.globals().ui.model)
        self.assertEqual(self.lua.globals().dumps, 0)
        self.run_lua("registered.F10(); step()")
        self.assertEqual(self.lua.globals().ui.model.rows[1].count, 8)

    def test_context_change_detected_inside_scan_is_immediately_rejected(self):
        self.open_window()
        self.run_lua('registered.F10();step();changedDuringScan=true;registered.F10();step()')
        self.assertFalse(self.lua.globals().ui.open)
        self.assertIsNone(self.lua.globals().ui.model)
        self.assertEqual(self.lua.globals().statuses[len(self.lua.globals().statuses)].state, "error")

    def test_pawn_loss_closes_and_does_not_substitute_a_character(self):
        self.open_window()
        self.run_lua("ctx=nil; step(); registered.F10(); step()")
        self.assertFalse(self.lua.globals().ui.open)
        self.assertEqual(self.lua.globals().scans, 1)
        self.assertEqual(self.lua.globals().statuses[len(self.lua.globals().statuses)].state, "error")

    def test_snapshot_includes_automatic_stock_scope_and_actual_report_path(self):
        self.open_window()
        self.run_lua('local old=ui.model;partial=true;registered.F6();step();assert(ui.model==old and old.stockComplete)')
        self.assertEqual(self.lua.globals().snapshots, 1)
        report = json.loads(self.lua.globals().written[1].text)
        self.assertTrue(report["readOnly"])
        self.assertFalse(report["data"]["uiCollection"]["complete"])
        self.assertEqual(report["data"]["uiCollection"]["rows"][0]["count"], 4)
        self.assertEqual(report["data"]["uiCollection"]["sourceInclusion"], "automatic_all_nearby_stock_boxes")
        self.assertEqual(report["data"]["uiCollection"]["includedSourceCount"], 1)
        # Legacy backpack diagnostics are separately labelled, never the UI count.
        self.assertEqual(report["data"]["paintCandidates"]["backpack"]["paint1"], 99)
        status = self.lua.globals().statuses[len(self.lua.globals().statuses)]
        self.assertEqual(status.state, "done")
        self.assertTrue(status.path.endswith(".json"))

    def test_context_change_during_legacy_snapshot_discards_mixed_report(self):
        self.open_window()
        self.run_lua('changeDuringSnapshot=true;registered.F6();step()')
        self.assertEqual(self.lua.globals().snapshots, 1)
        self.assertEqual(len(self.lua.globals().written), 0)
        self.assertFalse(self.lua.globals().ui.open)

    def test_context_is_checked_in_post_collection_phases_before_write(self):
        self.open_window()
        self.run_lua('''
            config.context_poll_ms=100000 -- do not let the periodic guard mask a missing phase guard
            registered.F6()
            inGameThread=true
            for i=1,4 do pump() end -- staged, begin, two collection units
            ctx="worldB/pawnB"
            pump();inGameThread=false
            step()
        ''')
        self.assertEqual(self.lua.globals().snapshots, 0)
        self.assertEqual(len(self.lua.globals().written), 0)

    def test_ui_error_releases_window_and_queue_remains_usable(self):
        self.open_window()
        self.run_lua('tickError=true; step(); tickError=false; registered.F6(); step()')
        self.assertFalse(self.lua.globals().ui.open)
        self.assertEqual(self.lua.globals().snapshots, 1)
        self.assertIn("UI error", list(self.lua.globals().closed.values()))

    def test_outer_pump_error_cleanup_stays_guarded_against_native_reentry(self):
        self.open_window()
        self.run_lua('''
            invoke("enqueue","refresh")
            local before=uiTicks
            local backend=require("paintmanager.background")
            local original=backend.pump
            backend.pump=function()
                backend.pump=original;error("outer pump failure")
            end
            failLogOnce=true; recurseClosePump=true
            inGameThread=true; pump(); inGameThread=false
            assert(not ui.open)
            assert(uiTicks==before+1, "error cleanup must not reenter the UI pump")
            recurseClosePump=false; registered.F10(); step()
            assert(ui.open)
        ''')

    def test_collection_failure_releases_window_and_busy_flag_recovers(self):
        self.open_window()
        self.run_lua('registered.F10();step();scanError=true;registered.F10();step();scanError=false;registered.F10();step()')
        self.assertTrue(self.lua.globals().ui.open)
        self.assertEqual(self.lua.globals().scans, 3)

    def test_queue_deduplicates_and_reports_capacity_without_executing_work(self):
        self.open_window()
        self.run_lua('''
            config.max_pending=2
            assert(invoke("enqueue","discovery")); assert(invoke("enqueue","discovery")); assert(invoke("enqueue","jmap"))
        ''')
        accepted, message = self.run_lua('return invoke("enqueue","snapshot")')
        self.assertFalse(accepted)
        self.assertIn("full", message)
        self.assertEqual(self.lua.globals().dumps, 0)
        self.run_lua("for i=1,4 do step() end")
        self.assertEqual(self.lua.globals().dumps, 1)
        self.assertEqual(self.lua.globals().discoveries, 1)

    def test_only_ship_radius_can_be_changed_in_settings(self):
        self.open_window()
        self.run_lua('assert(cb.set_radius==nil and config.radius_cm==nil);load_ship_radius(5000);reopen_window()')
        self.assertEqual(self.lua.globals().ui.model.radiusCm, 5000)

    def test_occupied_ue4ss_ui_shortcut_does_not_disable_other_commands(self):
        self.run_lua('conflict="F10"; start()')
        self.assertIsNone(self.lua.globals().registered.F10)
        self.assertIsNotNone(self.lua.globals().registered.F6)
        self.assertIsNone(self.lua.globals().registered.P)

    def test_jmap_receipt_does_not_claim_to_be_the_dump_path(self):
        self.run_lua("start(); registered.J(); step()")
        self.assertEqual(self.lua.globals().dumps, 1)
        report = json.loads(self.lua.globals().written[1].text)
        self.assertEqual(report["kind"], "jmap-request")
        self.assertIn("not the JMAP path", report["data"]["note"])

    def test_player_default_blocks_developer_commands_and_registered_shortcuts(self):
        self.run_lua('''
        fixtureDeveloperTools=false;start();registered.F10();step()
        assert(ui.open and ui.preferences.showDeveloperTools==false)
        for _,command in ipairs({"snapshot","discovery","jmap"}) do
            local accepted,message=invoke("enqueue",command)
            assert(not accepted and message:find("developer"),message)
        end
        registered.F6();registered.O();registered.J();step()
        assert(snapshots==0 and discoveries==0 and dumps==0 and scans==1 and #written==0)
        ''')

    def test_disabling_developer_mode_retires_staged_and_queued_developer_work(self):
        self.open_window()
        self.run_lua('''
        registered.J();assert(invoke("enqueue","discovery"))
        inGameThread=true;pump();seed_fixture_developer_tools(false);inGameThread=false
        step()
        assert(ui.open and not ui.preferences.showDeveloperTools)
        assert(dumps==0 and discoveries==0 and #written==0)
        local accepted=invoke("enqueue","snapshot");assert(not accepted)
        ''')

    def test_removed_sources_command_is_rejected_even_with_developer_tools(self):
        self.open_window()
        self.run_lua('''
        assert(ui.preferences.showDeveloperTools)
        local accepted,message=invoke("enqueue","sources")
        assert(not accepted and message:find("Unknown"))
        step();assert(scans==1 and snapshots==0 and #written==0)
        ''')


if __name__ == "__main__":
    unittest.main()
