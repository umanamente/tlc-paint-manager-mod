"""Bounded optimistic metadata queue uses fresh authoritative store snapshots."""
import unittest

from lupa.lua54 import LuaRuntime

from test_save_store import FIXTURE, SCRIPTS


FRONTEND = r'''
preferences_module=require("paintmanager.ui_preferences")
current_token="A";ready_now=true;published=0;dispatches={};settlements={};errors={}
local initial=sync()
latest_snapshot=initial:get_progress().snapshot
authoritative=initial:get_result()
callbacks={
    ready=function()
        if ready_now then return current_token,latest_snapshot end
    end,
    store=function(token,snapshot)
        assert(snapshot==latest_snapshot,"stale queued snapshot was dispatched")
        local store=factory(token,snapshot)
        local ui,favorite=store.begin_ui_settings,store.begin_favorite
        store.begin_ui_settings=function(changes)
            dispatches[#dispatches+1]={kind="settings",changes=changes,snapshot=snapshot}
            last_job=ui(changes);return last_job
        end
        store.begin_favorite=function(asset_type,asset_name,enabled)
            dispatches[#dispatches+1]={kind="favorite",assetType=asset_type,assetName=asset_name,enabled=enabled,snapshot=snapshot}
            last_job=favorite(asset_type,asset_name,enabled);return last_job
        end
        return store
    end,
    publish=function(settings,favorites)
        published=published+1;visible_settings=settings;visible_favorites=favorites
    end,
    settled=function(progress,value)
        settlements[#settlements+1]={progress=progress,value=value}
        if progress.state=="done" then
            latest_snapshot=progress.snapshot;authoritative=value
        end
    end,
    error=function(code,detail) errors[#errors+1]={code=code,detail=detail} end,
}
prefs=preferences_module.new(callbacks)
assert(prefs:load(authoritative))
function pref_step(allow_start)
    local b=budget(8);prefs:step(b,allow_start);assert(b.units<=1,"metadata lane monopolized shared budget")
    return b.units
end
function pref_drain()
    for _=1,100000 do
        if not prefs:is_busy() then return end
        pref_step(true)
    end
    error("preference lane stalled")
end
function key(name) return codec.favorite_key("Item",name) end
'''


class UIPreferenceQueueTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + FRONTEND)

    def test_language_choice_publishes_immediately_rolls_back_and_survives_reopen(self):
        self.lua.execute('''
        assert(visible_settings.uiLanguage=="auto")
        assert(not prefs:change_settings({uiLanguage="pending-locale"}))
        assert(prefs:change_settings({uiLanguage="ru"}) and visible_settings.uiLanguage=="ru")
        pref_step(true)
        assert(prefs:change_settings({uiLanguage="en"}) and visible_settings.uiLanguage=="en")
        pref_drain()
        assert(authoritative.sections.settings.data.uiLanguage=="en")
        assert(prefs:change_settings({uiLanguage="auto"}) and visible_settings.uiLanguage=="auto")
        fail_write=true;pref_drain();fail_write=false
        assert(#errors==1 and visible_settings.uiLanguage=="en")
        local reopened=preferences_module.new(callbacks)
        assert(reopened:load(sync():get_result()) and visible_settings.uiLanguage=="en")
        reopened:reset();assert(visible_settings.uiLanguage=="auto")
        ''')

    def test_ship_radius_defaults_independently_of_legacy_preset_radius(self):
        self.lua.execute('''
        for _,radius in ipairs({10000,25000}) do
            local frontend=preferences_module.new(callbacks)
            local old=codec.new()
            old.sections.settings.data.radiusCm=radius
            old.sections.settings.data.shipRadiusCm=nil;old.sections.settings.data.updatedAt.shipRadiusCm=nil
            assert(frontend:load(old) and visible_settings.shipRadiusCm==3500)
            assert(visible_settings.radiusCm==nil and not frontend:is_busy())
            assert(old.sections.settings.data.shipRadiusCm==nil)
        end
        ''')

    def test_deposit_preferences_publish_rollback_and_reopen_independently(self):
        self.lua.execute('''
        assert(visible_settings.depositSimilarAllColors==true and visible_settings.showDepositAllPaints==false)
        assert(authoritative.sections.settings.data.depositSimilarAllColors==nil)
        local ok,reason=prefs:change_settings({depositSimilarAllColors="false",showDepositAllPaints=true})
        assert(not ok and reason=="preferences_invalid_setting" and not prefs:is_busy())
        assert(prefs:change_settings({depositSimilarAllColors=false}))
        assert(visible_settings.depositSimilarAllColors==false and visible_settings.showDepositAllPaints==false)
        pref_drain()
        assert(prefs:change_settings({showDepositAllPaints=true}))
        assert(visible_settings.showDepositAllPaints==true and visible_settings.depositSimilarAllColors==false)
        fail_write=true;pref_drain();fail_write=false
        assert(#errors==1 and errors[1].code=="preferences_save_failed")
        assert(visible_settings.depositSimilarAllColors==false and visible_settings.showDepositAllPaints==false)
        assert(prefs:change_settings({showDepositAllPaints=true}));pref_drain()
        local reopened=preferences_module.new(callbacks)
        assert(reopened:load(sync():get_result()))
        assert(visible_settings.depositSimilarAllColors==false and visible_settings.showDepositAllPaints==true)
        reopened:reset()
        assert(visible_settings.depositSimilarAllColors==true and visible_settings.showDepositAllPaints==false)
        ''')

    def test_setting_batch_publishes_and_persists_custom_selected_paint_quantity(self):
        self.lua.execute('''
        assert(visible_settings.shipRadiusCm==3500 and visible_settings.paintTransferQuantity==5)
        assert(prefs:change_settings({shipRadiusCm=5000,sort="count_desc",lastPage="ready",showNames=true,
            tileDensity="compact",showDeveloperTools=true,paintTransferQuantity=9}))
        assert(visible_settings.shipRadiusCm==5000 and authoritative.sections.settings.data.shipRadiusCm==3500)
        for _=1,10 do pref_step(false) end
        assert(#dispatches==0 and visible_settings.shipRadiusCm==5000)
        pref_drain();assert(#dispatches==1 and #errors==0)
        assert(authoritative.sections.settings.data.shipRadiusCm==5000)
        assert(authoritative.sections.settings.data.paintTransferQuantity==9 and visible_settings.paintTransferQuantity==9)
        assert(authoritative.sections.settings.data.radiusCm==nil)
        assert(prefs:change_settings({shipRadiusCm=3500,paintTransferQuantity="all"}));pref_drain()
        assert(authoritative.sections.settings.data.shipRadiusCm==3500 and visible_settings.shipRadiusCm==3500)
        local reopened=preferences_module.new(callbacks)
        assert(reopened:load(authoritative) and visible_settings.paintTransferQuantity=="all")
        ''')

    def test_invalid_ship_radius_edit_is_atomic_and_keeps_visible_setting(self):
        self.lua.execute('''
        for _,value in ipairs({3499,5001,3500.5,"3500",false,math.huge,0/0}) do
            local accepted,code=prefs:change_settings({shipRadiusCm=value,showNames=true})
            assert(not accepted and code=="preferences_invalid_setting")
            assert(visible_settings.shipRadiusCm==3500 and not visible_settings.showNames and not prefs:is_busy())
        end
        assert(prefs:change_settings({shipRadiusCm=3571}));pref_drain()
        assert(authoritative.sections.settings.data.shipRadiusCm==3571)
        ''')

    def test_long_stock_work_defers_metadata_dispatch_but_publishes_optimistic_state(self):
        self.lua.execute('''
        local accepted,code=prefs:change_settings({showNames=true})
        assert(accepted and code=="preferences_queued" and visible_settings.showNames)
        assert(prefs:favorite("Item","DA_A",true))
        assert(visible_favorites[key("DA_A")] and prefs:is_busy())
        local before=files["library.json"]
        for _=1,100 do assert(pref_step(false)==0) end
        assert(#dispatches==0 and files["library.json"]==before)
        pref_drain()
        assert(#dispatches==2 and #settlements==2 and #errors==0)
        assert(authoritative.sections.settings.data.showNames)
        assert(authoritative.sections.favorites.data.records[1].enabled)
        ''')

    def test_settings_and_rapid_star_changes_coalesce_before_dispatch(self):
        self.lua.execute('''
        assert(prefs:change_settings({sort="count_desc"}))
        assert(prefs:change_settings({showNames=true,lastPage="ready"}))
        assert(prefs:change_settings({sort="name_desc"}))
        assert(prefs:favorite("Item","DA_A",true))
        assert(prefs:favorite("Item","DA_A",false))
        assert(not visible_favorites[key("DA_A")])
        pref_drain();assert(#dispatches==2 and #errors==0)
        assert(dispatches[1].kind=="settings" and dispatches[1].changes.sort=="name_desc")
        assert(dispatches[1].changes.showNames and dispatches[1].changes.lastPage=="ready")
        assert(dispatches[2].kind=="favorite" and dispatches[2].enabled==false)
        assert(authoritative.sections.favorites.data.records[1].enabled==false)
        ''')

    def test_dispatch_uses_current_snapshot_and_load_preserves_queued_overlays(self):
        self.lua.execute('''
        assert(prefs:change_settings({showNames=true}))
        local old=latest_snapshot
        local external=factory("A",old).begin_ui_settings({shipRadiusCm=4500});drain(external);assert(external.state=="done",external.error)
        latest_snapshot=external:get_progress().snapshot;authoritative=external:get_result()
        assert(latest_snapshot~=old and prefs:load(authoritative))
        assert(visible_settings.shipRadiusCm==4500 and visible_settings.showNames)
        pref_drain();assert(dispatches[1].snapshot~=old)
        assert(authoritative.sections.settings.data.shipRadiusCm==4500 and authoritative.sections.settings.data.showNames)
        ''')

    def test_active_job_finishes_with_start_disabled_and_later_same_key_edit_survives(self):
        self.lua.execute('''
        assert(prefs:favorite("Item","DA_A",true));pref_step(true)
        assert(last_job and prefs.active)
        assert(prefs:favorite("Item","DA_A",false));assert(visible_favorites[key("DA_A")]==false)
        for _=1,100000 do
            if #settlements>0 then break end
            pref_step(false)
        end
        assert(#settlements==1 and #dispatches==1 and prefs:is_busy())
        assert(visible_favorites[key("DA_A")]==false)
        pref_drain();assert(#dispatches==2 and authoritative.sections.favorites.data.records[1].enabled==false)
        ''')

    def test_failure_rolls_back_only_failed_overlay_and_does_not_retry(self):
        self.lua.execute('''
        assert(prefs:change_settings({showNames=true}));pref_step(true)
        fail_write=true
        for _=1,100000 do if #settlements>0 then break end;pref_step(false) end
        assert(#settlements==1 and #errors==1 and errors[1].code=="preferences_save_failed")
        assert(not visible_settings.showNames and not prefs:is_busy())
        fail_write=false
        for _=1,20 do pref_step(true) end
        assert(#dispatches==1)
        ''')

    def test_later_same_key_edit_is_not_rolled_back_by_older_failed_job(self):
        self.lua.execute('''
        assert(prefs:change_settings({sort="count_desc"}));pref_step(true)
        assert(prefs:change_settings({sort="name_desc"}));fail_write=true
        for _=1,100000 do if #settlements>0 then break end;pref_step(false) end
        assert(#errors==1 and visible_settings.sort=="name_desc" and prefs:is_busy())
        fail_write=false;pref_drain()
        assert(authoritative.sections.settings.data.sort=="name_desc" and #dispatches==2)
        ''')

    def test_successful_local_publication_with_uncertain_mirror_rebases_without_false_retry(self):
        self.lua.execute('''
        assert(prefs:change_settings({showDeveloperTools=true}));writeUncertain=true
        pref_drain();assert(#settlements==1 and #dispatches==1)
        local receipt=settlements[1].progress
        assert(receipt.state=="done" and receipt.localCommitted and receipt.mirrorPending and receipt.outcome=="uncertain")
        assert(visible_settings.showDeveloperTools and #errors==0 and not prefs:is_busy())
        for _=1,20 do pref_step(true) end
        assert(#dispatches==1)
        ''')

    def test_ambiguous_error_reports_uncertainty_without_pretending_document_success(self):
        self.lua.execute('''
        callbacks.store=function()
            return {begin_ui_settings=function()
                return {step=function(_,b) b:consume(1);return {state="error",localCommitted=true,error="readback failed"} end,
                    get_result=function() return nil end,cancel=function() end}
            end}
        end
        assert(prefs:change_settings({showNames=true}));pref_drain()
        assert(#settlements==1 and settlements[1].value==nil and #errors==1)
        assert(errors[1].code=="preferences_save_uncertain" and not visible_settings.showNames)
        ''')

    def test_reset_cancels_active_work_clears_queue_and_restores_safe_defaults(self):
        self.lua.execute('''
        assert(prefs:change_settings({showDeveloperTools=true}));pref_step(true)
        assert(prefs:favorite("Item","DA_A",true));local active=last_job
        prefs:reset()
        assert(active.state=="cancelled" and not prefs:is_busy() and not visible_settings.showDeveloperTools)
        assert(next(visible_favorites)==nil and #settlements==0)
        pref_step(true);assert(#dispatches==1)
        local accepted,code=prefs:change_settings({showNames=true})
        assert(not accepted and code=="preferences_not_ready")
        ''')

    def test_same_token_new_load_generation_revokes_active_and_pending_edits(self):
        self.lua.execute('''
        assert(prefs:change_settings({showNames=true}));pref_step(true)
        assert(prefs:favorite("Item","DA_A",true))
        latest_snapshot.loadGeneration=latest_snapshot.loadGeneration+1
        pref_step(false)
        assert(last_job.state=="cancelled" and not prefs:is_busy() and #settlements==0)
        assert(not visible_settings.showNames and next(visible_favorites)==nil)
        ''')

    def test_queue_is_bounded_and_invalid_multi_setting_edit_is_atomic(self):
        self.lua.execute('''
        for index=1,64 do assert(prefs:favorite("Item","DA_"..index,true)) end
        local accepted,code=prefs:favorite("Item","DA_65",true)
        assert(not accepted and code=="preferences_queue_full" and visible_favorites[key("DA_65")]==nil)
        assert(prefs:favorite("Item","DA_1",false))
        accepted,code=prefs:change_settings({showNames=true,lastPage="debug"})
        assert(not accepted and code=="preferences_invalid_setting" and not visible_settings.showNames)
        accepted,code=prefs:change_settings({showNames=true})
        assert(not accepted and code=="preferences_queue_full" and not visible_settings.showNames)
        accepted,code=prefs:favorite("../Bad","DA_A",true)
        assert(not accepted and code=="preferences_invalid_favorite")
        ''')

    def test_transient_missing_ready_snapshot_does_not_discard_active_work(self):
        self.lua.execute('''
        assert(prefs:change_settings({showNames=true}));pref_step(true)
        ready_now=false
        for _=1,100000 do if not prefs:is_busy() then break end;pref_step(false) end
        assert(not prefs:is_busy() and #settlements==1 and settlements[1].progress.state=="done")
        assert(visible_settings.showNames)
        ''')


if __name__ == "__main__":
    unittest.main()
