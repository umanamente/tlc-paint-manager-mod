"""Real hybrid storage jobs through the game-thread controller pump."""
import unittest
from lupa.lua54 import LuaRuntime
from test_main_ui import FIXTURE, SCRIPTS
from test_local_library import FIXTURE as LOCAL_FIXTURE

HYBRID_MAIN = r'''
fixtureDeveloperTools=false
package.loaded["paintmanager.save_store"]=nil
debug.getinfo=function() return {source="@D:/Steam/steamapps/common/Voyage/Voyage/Binaries/Win64/ue4ss/Mods/PaintManager/Scripts/main.lua"} end
io.open=function(path,mode)
    assert(mode=="r")
    return {read=function() return '"buildid" "25982825"' end,close=function() return true end}
end
carriers,reads,writes,attempts={},0,0,0
account="76561198000000001";load_generation=1;storage_ready=true
package.loaded["paintmanager.save_lifecycle"]={
    install=function() return true end,
    consume=function() local event=queued_event;queued_event=nil;return event end,
}
package.loaded["paintmanager.save_bridge"]={
    storage_context=function(token)
        assert(inGameThread)
        if bound_generation==load_generation and bound_account~=account then
            return {status="not_ready",error="Native account changed within the restored scope"}
        end
        bound_generation,bound_account=load_generation,account
        return {status=storage_ready and "ready" or "not_ready",accountId=account,
            libraryPath="library-"..account..".json",scopeId=token.."/hud/state",loadGeneration=load_generation,error="Restoration not ready"}
    end,
    read=function(token)
        assert(inGameThread and token==ctx);reads=reads+1
        return {status=carriers[token] and "present" or "missing",raw=carriers[token],scopeId=token.."/hud/state"}
    end,
    write=function(token,expected,raw,scope)
        assert(inGameThread and token==ctx and scope==token.."/hud/state")
        assert(files["library-"..account..".json"]==raw,"native stage preceded local publication")
        writes=writes+1
        if writeConflict then return {status="conflict",error="External change"} end
        assert(carriers[token]==expected);attempts=attempts+1;carriers[token]=raw
        return {status="staged"}
    end,
    new_id=function() return "12345678-1234-4234-8234-123456789abc" end,
}
local actual=require("paintmanager.save_store")
local create=actual.new
actual.new=function(c,token,build,snapshot)
    return create(c,token,build,snapshot,{repository={fs=fs},document={now=100}})
end
local old_ui=package.loaded["paintmanager.ui"].new
package.loaded["paintmanager.ui"].new=function(c,callbacks)
    local result=old_ui(c,callbacks)
    function result:set_persistence(message) assert(inGameThread);self.persistence=message end
    function result:set_learning(active) assert(inGameThread);self.learning=active end
    function result:set_presets(view) assert(inGameThread);self.presets=view end
    return result
end
function pump_once()
    inGameThread=true;local ok,err=pcall(pump);inGameThread=false;assert(ok,err)
end
function drain_main() for _=1,4500 do pump_once() end end
function ready_load(token)
    ctx=token or ctx;load_generation=load_generation+1
    queued_event={status="ready",contextToken=ctx,loadGeneration=load_generation}
end
function current_local() return files["library-"..account..".json"] end
function decode(raw)
    local job=codec.begin_decode(raw);drain(job);assert(job.state=="done",job.error);return job:get_result()
end
function saved(radius) return canonical(with_ship_radius(radius)) end
function observe_editor_library()
    local editor = require("paintmanager.native_editor")
    local create = editor.new
    editor.new = function(config, callbacks)
        function editor_library(retry)
            inGameThread = true
            local library, snapshot, reason, index = callbacks.library(ctx, retry)
            inGameThread = false
            return library, snapshot, reason, index
        end
        editor_instance = create(config, callbacks)
        return editor_instance
    end
end
function observe_initial_presets()
    state_starts,library_starts,target_starts=0,0,0
    local store_module=package.loaded["paintmanager.save_store"]
    local old_store=store_module.new
    store_module.new=function(...)
        local store=old_store(...)
        local state,library=store.begin_state_load,store.begin_load
        store.begin_state_load=function()
            state_starts=state_starts+1
            local job=state();local step=job.step
            function job:step(budget)
                local p=step(self,budget)
                if p.state=="done" then completed_state=p;completed_data=self:get_result() end
                return p
            end
            return job
        end
        store.begin_load=function(...)
            library_starts=library_starts+1
            return library(...)
        end
        return store
    end
    local ui_module=package.loaded["paintmanager.ui"]
    local old_ui=ui_module.new
    ui_module.new=function(...)
        local shell=old_ui(...)
        shell.active_tab="learned"
        function shell:needs_preset_refresh()
            return self.active_tab=="presets" and not self.presets and not self.requested
        end
        function shell:mark_preset_refresh_requested() self.requested=true end
        return shell
    end
    local presets=require("paintmanager.presets")
    presets.begin_capture=function()
        target_starts=target_starts+1
        return {step=function(_,budget) budget:consume()
            return {state="done",view={contextToken=ctx,targets={}}} end,
            cancel=function() end}
    end
end
'''


class SaveMainTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path=SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(LOCAL_FIXTURE)
        self.lua.execute(HYBRID_MAIN)

    def run_lua(self,source): self.lua.execute(source)

    def test_closed_window_warms_the_editor_library_once_per_restored_scope(self):
        self.run_lua('''
        observe_initial_presets();observe_editor_library()
        start();ready_load(ctx);pump_once()
        local library,snapshot,reason=editor_library(true)
        assert(not library and not snapshot and not reason)
        drain_main()
        local operations_before=#operations
        local library,snapshot,reason,index=editor_library(true)
        assert(library==completed_data.sections.presets.data and snapshot==completed_state.snapshot and not reason)
        assert(index.nextName=="Preset 1" and #operations==operations_before)
        assert(state_starts==1 and library_starts==0 and target_starts==0 and not ui)
        local first=snapshot
        ready_load(ctx);pump_once()
        library,snapshot,reason=editor_library(true)
        assert(not library and not snapshot and not reason)
        drain_main();library,snapshot,reason,index=editor_library(true)
        assert(snapshot~=first and snapshot.loadGeneration~=first.loadGeneration and index.nextName=="Preset 1")
        assert(state_starts==2 and library_starts==0)
        ''')

    def test_cold_editor_requests_share_one_load_and_require_restoration(self):
        self.run_lua('''
        observe_initial_presets();observe_editor_library();storage_ready=false;start()
        local library,snapshot,reason=editor_library(true)
        assert(not library and not snapshot and reason=="Restoration not ready")
        for _=1,10 do pump_once() end
        assert(state_starts==0 and reads==0)
        storage_ready=true
        for _=1,3 do
            library,snapshot,reason=editor_library(true)
            assert(not library and not snapshot and not reason)
            pump_once()
        end
        drain_main()
        local library,snapshot,reason,index=editor_library(true)
        assert(library and snapshot and not reason and index.nextName=="Preset 1")
        assert(state_starts==1 and library_starts==0 and target_starts==0)
        ''')

    def test_popup_inspection_progresses_while_the_shared_reconciliation_is_running(self):
        self.run_lua('''
        observe_initial_presets();observe_editor_library()
        start();ready_load(ctx);pump_once()
        assert(state_starts==1 and not completed_state)
        local waiting,finished=0,false
        editor_instance.popup={phase="inspect"}
        editor_instance.tick=function() end
        function editor_instance:work(budget)
            local library,snapshot,reason,index=self.callbacks.library(ctx,waiting==0)
            assert(not reason)
            if not library then
                if not completed_state then waiting=waiting+1 end
                return false
            end
            assert(snapshot==completed_state.snapshot and index.nextName=="Preset 1")
            self.popup=nil;finished=true
            return true
        end
        drain_main()
        assert(waiting>0 and finished and state_starts==1 and library_starts==0)
        ''')

    def test_cold_preset_tab_uses_the_just_validated_document_and_explicit_refresh_rereads(self):
        self.run_lua('''
        observe_initial_presets()
        files["library-"..account..".json"]=saved(4000);carriers[ctx]=current_local()
        start();registered.F10()
        for i=1,100 do pump_once();if state_starts==1 then break end end
        assert(state_starts==1 and not completed_state);ui.active_tab="presets"
        for i=1,1000 do pump_once();if completed_state then break end end
        assert(completed_state and completed_state.staged and not completed_state.localCommitted and writes==0)
        assert(library_starts==0 and scans==0 and target_starts==1)
        drain_main()
        assert(ui.presets.library==completed_data.sections.presets.data)
        assert(ui.presets.storageSnapshot==completed_state.snapshot and scans==0 and not ui.learning)
        assert(invoke("preset_refresh"));drain_main()
        assert(library_starts==1 and target_starts==2 and state_starts==1 and scans==0)
        ui.active_tab="learned";drain_main();assert(scans==1)
        ''')

    def test_initial_preset_handoff_requires_verified_sources_and_current_tab(self):
        for leave, conflict in ((True, False), (False, True)):
            with self.subTest(leave=leave, conflict=conflict):
                self.setUp()
                self.lua.globals().leave_tab=leave
                self.lua.globals().writeConflict=conflict
                self.run_lua('''
                observe_initial_presets();start();registered.F10()
                for i=1,100 do pump_once();if state_starts==1 then break end end
                assert(state_starts==1 and not completed_state)
                ui.active_tab="presets";pump_once()
                if leave_tab then ui.active_tab="learned" end
                for i=1,1000 do pump_once();if completed_state then break end end
                assert(completed_state and target_starts==0)
                if leave_tab then drain_main();ui.active_tab="presets"
                else assert(completed_state.mirrorPending) end
                drain_main();assert(library_starts==1 and target_starts==1 and ui.presets)
                ''')

    def test_presets_can_take_the_active_lifecycle_sync_after_character_attachment(self):
        self.run_lua('''
        observe_initial_presets()
        package.loaded["paintmanager.collection"].character_possessed=function() return attached==true end
        files["library-"..account..".json"]=saved(4000);carriers[ctx]=current_local()
        start();ready_load(ctx);pump_once()
        registered.F10();drain_main()
        assert(ui.open and state_starts==0);attached=true
        for i=1,100 do pump_once();if state_starts==1 then break end end
        assert(state_starts==1 and not completed_state);ui.active_tab="presets"
        local before=scans
        for i=1,1000 do pump_once();if completed_state then break end end
        assert(completed_state and completed_state.staged and target_starts==1 and library_starts==0)
        drain_main();assert(ui.presets.library==completed_data.sections.presets.data and scans==before)
        ''')

    def test_presets_before_the_first_vault_step_promotes_one_cold_library_read(self):
        self.run_lua('''
        observe_initial_presets();files["library-"..account..".json"]=saved(4000);carriers[ctx]=current_local()
        local collection=package.loaded["paintmanager.collection"]
        local create=collection.begin_catalog
        local catalog_steps=0
        collection.begin_catalog=function(...)
            local job=create(...);local step=job.step
            function job:step(...) catalog_steps=catalog_steps+1;return step(self,...) end
            return job
        end
        start();registered.F10()
        for i=1,20 do pump_once();if ui and ui.open then break end end
        assert(ui.active_tab=="learned" and catalog_steps==0 and state_starts==0)
        ui.active_tab="presets";pump_once()
        assert(state_starts==1 and library_starts==0 and catalog_steps==0)
        drain_main();assert(ui.presets and target_starts==1 and library_starts==0 and catalog_steps==0 and scans==0)
        ui.active_tab="learned";drain_main()
        assert(state_starts==1 and library_starts==0 and catalog_steps==1 and scans==1)
        ''')

    def test_lifecycle_reconciles_with_window_closed_without_collection(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();assert(reads==0 and writes==0 and scans==0)
        ready_load();drain_main()
        assert(ui==nil and scans==0 and snapshots==0 and discoveries==0 and attempts==0)
        assert(current_local()==carriers[ctx] and config.ship_radius_cm==4000)
        local before=reads;drain_main();assert(reads==before)
        ''')

    def test_storage_status_does_not_present_microsteps_as_record_totals(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        local seen=false
        for _,entry in ipairs(statuses) do
            if entry.command=="toggle" and entry.state=="running" and entry.message then
                assert(not entry.message:match("%d+/%d+"),entry.message)
                seen=true
            end
        end
        assert(seen and ui.open and ui.model)
        ''')

    def test_not_ready_load_never_writes_and_explicit_window_still_reads_collection(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();storage_ready=false;ready_load();drain_main()
        assert(current_local()==nil and writes==0 and scans==0)
        registered.F10();drain_main()
        assert(ui.open and ui.model and config.ship_radius_cm==3500 and writes==0)
        assert(ui.persistence:find("unavailable"))
        ''')

    def test_ship_radius_local_first_and_normal_save_disclosure(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        assert(ui.open and config.ship_radius_cm==4000 and writes==0)
        assert(invoke("change_settings",{shipRadiusCm=4500}));assert(writes==0 and config.ship_radius_cm==4500)
        drain_main();assert(writes==1 and config.ship_radius_cm==4500 and ui.model==nil and scans==1)
        local state=decode(current_local())
        assert(state.sections.settings.data.updatedAt.shipRadiusCm==100)
        assert(state.sections.settings.data.shipRadiusCm==4500 and current_local()==carriers[ctx])
        assert(ui.persistence:find("normally") and ui.persistence:find("Local library"))
        ''')

    def test_older_slot_uses_account_local_aggregate_without_opening_window(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        assert(invoke("change_settings",{shipRadiusCm=4500}));drain_main()
        ready_load("worldB/pawnB");drain_main()
        assert(not ui.open and config.ship_radius_cm==4500 and decode(carriers[ctx]).sections.settings.data.shipRadiusCm==4500)
        assert(current_local()==carriers[ctx] and scans==1)
        ''')

    def test_accounts_are_partitioned_and_missing_values_use_defaults(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();ready_load();drain_main()
        local first=current_local();account="76561198000000002";ready_load("worldB/pawnB");drain_main()
        assert(config.ship_radius_cm==3500 and current_local()~=first)
        assert(files["library-76561198000000001.json"]==first and scans==0)
        ''')

    def test_mirror_failure_keeps_local_ship_radius_and_reports_pending_without_retry(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        writeConflict=true;assert(invoke("change_settings",{shipRadiusCm=4500}));drain_main()
        assert(writes==1 and config.ship_radius_cm==4500 and decode(current_local()).sections.settings.data.shipRadiusCm==4500)
        assert(decode(carriers[ctx]).sections.settings.data.shipRadiusCm==4000)
        assert(ui.persistence:find("Saved locally") and ui.persistence:find("pending"))
        drain_main();assert(writes==1)
        writeConflict=false;registered.F10();drain_main();registered.F10();drain_main()
        assert(writes==1 and current_local()~=carriers[ctx])
        ready_load();drain_main()
        assert(writes==2 and current_local()==carriers[ctx])
        assert(decode(current_local()).sections.settings.data.updatedAt.shipRadiusCm==100)
        ''')

    def test_corrupt_future_data_preserved_and_collection_remains_available(self):
        for destination in ("local","native"):
            for raw in ("{broken",'{"format":"PaintManager.State","schemaVersion":99}'):
                with self.subTest(destination=destination,raw=raw):
                    self.setUp();self.lua.globals().bad_raw=raw
                    self.run_lua('carriers[ctx]=saved(4000)')
                    self.run_lua('files["library-"..account..".json"]=bad_raw' if destination=="local" else 'carriers[ctx]=bad_raw')
                    self.run_lua('''
                    start();registered.F10();drain_main()
                    assert(ui.open and ui.model and ui.persistence:find("unavailable") and writes==0)
                    ''')
                    self.run_lua('assert(current_local()==bad_raw)' if destination=="local" else 'assert(carriers[ctx]==bad_raw)')

    def test_map_invalidation_cancels_partial_work_before_publication(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();ready_load();pump_once();pump_once()
        queued_event={invalidated=true};pump_once();storage_ready=false;drain_main()
        assert(current_local()==nil and writes==0 and scans==0)
        ''')

    def test_toggle_cancellation_reschedules_automatic_reconciliation(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();ready_load();pump_once();pump_once()
        registered.F10();pump_once();pump_once()
        registered.F10();drain_main()
        assert(not ui.open and current_local()==carriers[ctx] and config.ship_radius_cm==4000)
        ''')

    def test_same_token_account_switch_invalidates_until_fresh_load(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        local previous=current_local();account="76561198000000002"
        drain_main();assert(not ui.open and current_local()==nil)
        assert(files["library-76561198000000001.json"]==previous and scans==1)
        carriers[ctx]=saved(3500);ready_load();drain_main();assert(current_local()==carriers[ctx])
        ''')

    def test_ready_event_retires_same_token_previews_without_separate_pre_event(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        assert(ui.open);ready_load(ctx);drain_main()
        assert(not ui.open and ui.presets==nil and current_local()==carriers[ctx] and scans==1)
        ''')

    def test_favorites_and_settings_persist_during_long_stock_capture(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        assert(not ui.preferences.showDeveloperTools and not ui.preferences.showNames)
        jobSlices=12000;reopen_window();pump_once()
        assert(invoke("change_settings",{sort="count_desc",showNames=true,lastPage="ready"}))
        assert(invoke("favorite","Item","DA_A",true))
        assert(ui.preferences.showNames and ui.favorites[codec.favorite_key("Item","DA_A")])
        drain_main()
        local state=decode(current_local())
        assert(scans==2 and ui.open and state.sections.settings.data.sort=="count_desc")
        assert(state.sections.settings.data.showNames and state.sections.settings.data.lastPage=="ready")
        assert(state.sections.favorites.data.records[1].enabled and current_local()==carriers[ctx])
        assert(state.sections.favorites.data.records[1].updatedAt==100)
        jobSlices=2;drain_main()
        assert(ui.model.radiusCm==4000 and decode(current_local()).sections.favorites.data.records[1].enabled)
        ''')

    def test_queued_metadata_survives_close_and_reopen_reuses_current_storage_snapshot(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        assert(invoke("change_settings",{showNames=true}))
        assert(invoke("favorite","Item","DA_A",true))
        registered.F10();drain_main()
        assert(not ui.open and current_local()==carriers[ctx])
        local state=decode(current_local())
        assert(state.sections.settings.data.showNames and state.sections.favorites.data.records[1].enabled)
        local before=writes;registered.F10();drain_main()
        assert(ui.open and ui.preferences.showNames and ui.favorites[codec.favorite_key("Item","DA_A")])
        assert(writes==before)
        ''')

    def test_rapid_star_clicks_coalesce_to_one_durable_false_membership(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        local before=writes
        assert(invoke("favorite","Item","DA_A",true))
        assert(invoke("favorite","Item","DA_A",false))
        assert(ui.favorites[codec.favorite_key("Item","DA_A")]==false)
        drain_main();local records=decode(current_local()).sections.favorites.data.records
        assert(writes==before+1 and #records==1 and records[1].enabled==false and records[1].updatedAt==100)
        assert(ui.favorites[codec.favorite_key("Item","DA_A")]==false)
        ''')

    def test_new_load_generation_retires_pending_metadata_and_resets_developer_mode(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        assert(invoke("change_settings",{showNames=true,showDeveloperTools=true}))
        assert(invoke("favorite","Item","DA_A",true))
        assert(ui.preferences.showDeveloperTools)
        ready_load(ctx);drain_main()
        local state=decode(current_local())
        assert(not ui.open and not ui.preferences.showDeveloperTools and not ui.preferences.showNames)
        assert(not state.sections.settings.data.showDeveloperTools and #state.sections.favorites.data.records==0)
        assert(next(ui.favorites)==nil and writes==0)
        ''')

    def test_real_developer_setting_controls_staged_work_and_persists_false(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        local accepted=invoke("enqueue","jmap");assert(not accepted)
        assert(invoke("change_settings",{showDeveloperTools=true}));drain_main()
        assert(decode(current_local()).sections.settings.data.showDeveloperTools)
        registered.J();pump_once()
        assert(invoke("change_settings",{showDeveloperTools=false}));drain_main()
        local state=decode(current_local())
        assert(dumps==0 and not state.sections.settings.data.showDeveloperTools)
        assert(state.sections.settings.data.updatedAt.showDeveloperTools==101)
        assert(not ui.preferences.showDeveloperTools)
        accepted=invoke("enqueue","discovery");assert(not accepted)
        ''')

    def test_metadata_mirror_conflict_keeps_local_success_without_automatic_retry(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        writeConflict=true;assert(invoke("change_settings",{showNames=true}));drain_main()
        assert(ui.preferences.showNames and decode(current_local()).sections.settings.data.showNames)
        assert(not decode(carriers[ctx]).sections.settings.data.showNames)
        assert(writes==1 and ui.persistence:find("Saved locally") and ui.persistence:find("pending"))
        drain_main();assert(writes==1)
        ''')

    def test_failed_metadata_publication_rolls_back_without_false_saved_status(self):
        self.run_lua('''
        carriers[ctx]=saved(4000);start();registered.F10();drain_main()
        local before=current_local();fail_write=true
        assert(invoke("change_settings",{showNames=true}));drain_main()
        assert(current_local()==before and carriers[ctx]==before and not ui.preferences.showNames)
        assert(ui.status.state=="error" and ui.status.command=="preferences")
        assert(ui.persistence:find("unavailable"),ui.persistence)
        local seen=writes;fail_write=false;drain_main();assert(writes==seen)
        ''')

    def test_save_uuid_delete_and_old_slot_cannot_resurrect(self):
        self.run_lua(r'''
        local function complete(view)
            return {cancel=function() end,step=function(self,budget)
                assert(inGameThread);budget:consume();return {state="done",view=view} end}
        end
        local actual_presets=require("paintmanager.presets")
        package.loaded["paintmanager.presets"]={
            compare_layout=actual_presets.compare_layout,
            from_target=actual_presets.from_target,
            begin_capture=function() return complete({contextToken=ctx,
                targets=json.array({{id="target1",kind="character",complete=true,zones=json.array(),
                    compatibility={kind="character",className="/Script/Voyage.VoyageCharacter",paintSetName="Character"},
                    logicalZones=json.array({{materialSlotName="Body",partName="Body",paint=json.null}})}})}) end,
        }
        observe_editor_library()
        start();registered.F10();drain_main();assert(invoke("preset_refresh"));drain_main()
        assert(invoke("preset_save","target1","My configuration"));drain_main()
        assert(#ui.presets.library.presets==1,ui.status.message)
        local id=ui.presets.library.presets[1].id;local old=carriers[ctx]
        assert(id=="12345678-1234-4234-8234-123456789abc")
        local preview=ui.presets
        local cached,snapshot,reason,index=editor_library(true)
        assert(cached.presets[1].id==id and snapshot==preview.storageSnapshot and not reason and index)
        for _,page in ipairs({"learned","presets"}) do
            local previous=preview.storageSnapshot
            assert(invoke("change_settings",{lastPage=page,showNames=true}));drain_main()
            assert(ui.presets==preview and preview.storageSnapshot~=previous)
            assert(preview.storageSnapshot.raw==carriers[ctx])
            cached,snapshot,reason,index=editor_library(true)
            assert(cached.presets[1].id==id and snapshot==preview.storageSnapshot and not reason and index)
        end
        -- An external change still invalidates that exact preview.
        local owned=carriers[ctx];carriers[ctx]=old
        assert(invoke("preset_delete",id));drain_main()
        assert(ui.status.state=="error" and #decode(current_local()).sections.presets.data.presets==1)
        carriers[ctx]=owned
        assert(invoke("change_settings",{tileDensity="compact"}));pump_once()
        assert(invoke("change_settings",{showDeveloperTools=true}))
        assert(invoke("preset_delete",id));drain_main()
        assert(#ui.presets.library.presets==0 and #ui.presets.library.tombstones==1)
        cached,snapshot,reason,index=editor_library(true)
        assert(#cached.presets==0 and snapshot==ui.presets.storageSnapshot and not reason and index)
        local settings=decode(current_local()).sections.settings.data
        assert(settings.showNames and settings.tileDensity=="compact" and settings.showDeveloperTools)
        assert(ui.presets.library.tombstones[1].id==id and ui.status.message:find("deleted"))
        carriers["worldB/pawnB"]=old;ready_load("worldB/pawnB");drain_main()
        local state=decode(carriers[ctx]);assert(#state.sections.presets.data.presets==0)
        assert(#state.sections.presets.data.tombstones==1 and current_local()==carriers[ctx])
        ''')


if __name__=="__main__": unittest.main()
