"""Durable UI preferences share the existing bounded account reconciliation."""
import unittest

from lupa.lua54 import LuaRuntime

from test_state_document import FIXTURE as DOCUMENT_FIXTURE, SCRIPTS
from test_state_merge import MERGE_FIXTURE
from test_save_store import FIXTURE as STORE_FIXTURE
from test_worker_save_store import BACKGROUND


class PreferenceDocumentTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path;json=require("paintmanager.json");codec=require("paintmanager.state_document")')
        self.lua.execute(DOCUMENT_FIXTURE + MERGE_FIXTURE)

    def test_language_defaults_to_follow_game_and_merges_with_concurrent_edits(self):
        self.lua.execute('''
        local old=codec.new()
        local original=raw(old)
        assert(codec.optional_setting_defaults.uiLanguage=="auto")
        assert(raw(update(old,{uiSettings={uiLanguage="auto"}},10))==original)
        local ru=update(old,{uiSettings={uiLanguage="ru"}},10)
        local separate=update(old,{uiSettings={showNames=true}},20)
        local both=result(codec.begin_decode(raw(merge(ru,separate))))
        local data=both.sections.settings.data
        assert(data.uiLanguage=="ru" and data.updatedAt.uiLanguage==10 and data.showNames)
        local reset=update(both,{uiSettings={uiLanguage="auto"}},30)
        assert(merge(ru,reset).sections.settings.data.uiLanguage=="auto")
        local invalid=codec.begin_update(both,{uiSettings={uiLanguage="not-shipped"}})
        drain(invalid);assert(invalid.state=="error" and raw(old)==original)
        ''')

    def test_transfer_target_uses_a_sparse_preference_and_roundtrips_all_modes(self):
        self.lua.execute('''
        local old=codec.new();local original=raw(old)
        assert(raw(result(codec.begin_decode(original)))==original)
        assert(raw(update(old,{uiSettings={paintTransferQuantity=5}},10))==original)
        local previous=old
        for i,value in ipairs({1,7,"all",2147483647,5}) do
            local next_doc=update(previous,{uiSettings={paintTransferQuantity=value}},100+i)
            local saved=result(codec.begin_decode(raw(next_doc))).sections.settings.data
            assert(saved.paintTransferQuantity==value and saved.updatedAt.paintTransferQuantity==100+i)
            assert(raw(merge(next_doc,old))==raw(merge(old,next_doc)))
            assert(merge(old,next_doc).sections.settings.data.paintTransferQuantity==value)
            previous=next_doc
        end
        local number=update(old,{uiSettings={paintTransferQuantity=7}},20)
        local all=update(old,{uiSettings={paintTransferQuantity="all"}},20)
        assert(raw(merge(all,number))==raw(merge(number,all)))
        for _,bad in ipairs({0,1.5,-1,"",false,"4",2147483648}) do
            local job=codec.begin_update(old,{uiSettings={paintTransferQuantity=bad}})
            drain(job);assert(job.state=="error" and raw(old)==original)
        end
        for _,missing in ipairs({"timestamp","value"}) do
            local broken=copy(number)
            local data=broken.sections.settings.data
            if missing=="value" then data.paintTransferQuantity=nil
            else data.updatedAt.paintTransferQuantity=nil end
            local job=codec.begin_validate(broken);drain(job);assert(job.state=="error")
        end
        ''')

    def test_deposit_defaults_leave_a_full_legacy_document_byte_identical(self):
        self.lua.execute('''
        local old=codec.new()
        local padding=json.array()
        old.sections.future={schemaVersion=1,data=padding}
        for index=1,8 do padding[index]=string.rep("q",4000) end
        padding[9]=""
        padding[9]=string.rep("q",codec.MAX_BYTES-#raw(old))
        local before=raw(old)
        assert(#before==codec.MAX_BYTES)
        local decoded=result(codec.begin_decode(before))
        assert(raw(decoded)==before)
        assert(raw(update(decoded,{uiSettings={depositSimilarAllColors=true,showDepositAllPaints=false}}))==before)
        local failed=codec.begin_update(decoded,{uiSettings={showDepositAllPaints=true}})
        drain(failed)
        assert(failed.state=="error" and raw(decoded)==before)
        ''')

    def test_deposit_modes_merge_by_independent_clocks_and_require_stamped_booleans(self):
        self.lua.execute('''
        local old=codec.new()
        local stock=update(old,{uiSettings={depositSimilarAllColors=false}},100)
        local button=update(old,{uiSettings={showDepositAllPaints=true}},200)
        local combined=merge(stock,button)
        assert(raw(combined)==raw(merge(button,stock)))
        local data=combined.sections.settings.data
        assert(data.depositSimilarAllColors==false and data.updatedAt.depositSimilarAllColors==100)
        assert(data.showDepositAllPaints==true and data.updatedAt.showDepositAllPaints==200)
        local reset=update(combined,{uiSettings={depositSimilarAllColors=true,showDepositAllPaints=false}},99)
        local restored=merge(combined,reset).sections.settings.data
        assert(restored.depositSimilarAllColors==true and restored.updatedAt.depositSimilarAllColors==101)
        assert(restored.showDepositAllPaints==false and restored.updatedAt.showDepositAllPaints==201)
        assert(raw(merge(old,reset))==raw(reset))
        local tie=update(old,{uiSettings={depositSimilarAllColors=false}},101)
        assert(raw(merge(tie,reset))==raw(merge(reset,tie)))
        for _,key in ipairs({"depositSimilarAllColors","showDepositAllPaints"}) do
            for _,bad in ipairs({1,"false",json.null}) do
                local job=codec.begin_update(combined,{uiSettings={[key]=bad}})
                drain(job);assert(job.state=="error")
            end
            for _,missing in ipairs({"value","timestamp"}) do
                local broken=copy(combined)
                local partial=broken.sections.settings.data
                if missing=="value" then partial[key]=nil else partial.updatedAt[key]=nil end
                local job=codec.begin_decode(raw(broken));drain(job);assert(job.state=="error")
            end
        end
        ''')

    def test_optional_ship_radius_extension_defaults_old_v3_without_edit_stamps(self):
        self.lua.execute('''
        for _,radius in ipairs({10000,25000}) do
            local old=replica("saved","Saved",19)
            old.sections.settings.schemaVersion=3
            old.sections.settings.data.radiusCm,old.sections.settings.data.updatedAt.radiusCm=radius,42
            old.revision=7
            old.sections.settings.data.shipRadiusCm=nil;old.sections.settings.data.updatedAt.shipRadiusCm=nil
            local before=raw(old);local migrated=result(codec.begin_decode(before))
            local settings=migrated.sections.settings.data
            assert(migrated.schemaVersion==3 and migrated.sections.settings.schemaVersion==4 and migrated.revision==7)
            assert(settings.shipRadiusCm==3500 and settings.updatedAt.shipRadiusCm==0)
            assert(settings.radiusCm==nil and settings.updatedAt.radiusCm==nil)
            assert(migrated.sections.presets.data.presets[1].updatedAt==19 and raw(old)==before)
            assert(raw(result(codec.begin_decode(raw(migrated))))==raw(migrated))
        end
        local original=legacy_document();local migrated=result(codec.begin_validate(original))
        assert(migrated.sections.settings.data.shipRadiusCm==3500 and migrated.sections.settings.data.updatedAt.shipRadiusCm==0)
        ''')

    def test_ship_radius_uses_its_own_newest_wins_clock_and_legacy_radius_is_independent(self):
        self.lua.execute('''
        local base=codec.new()
        local ship=update(base,{uiSettings={shipRadiusCm=5000}},100)
        local preset=copy(base)
        preset.sections.settings.schemaVersion=3
        preset.sections.settings.data.radiusCm,preset.sections.settings.data.updatedAt.radiusCm=10000,999
        local combined=merge(ship,preset);local settings=combined.sections.settings.data
        assert(settings.shipRadiusCm==5000 and settings.updatedAt.shipRadiusCm==100)
        assert(settings.radiusCm==nil and settings.updatedAt.radiusCm==nil)
        assert(raw(combined)==raw(merge(preset,ship)))
        assert(raw(update(combined,{uiSettings={shipRadiusCm=5000}},9999))==raw(combined))
        local smaller=update(combined,{uiSettings={shipRadiusCm=3500}},1)
        assert(smaller.sections.settings.data.updatedAt.shipRadiusCm==101)
        assert(smaller.sections.settings.data.updatedAt.radiusCm==nil)
        assert(merge(smaller,combined).sections.settings.data.shipRadiusCm==3500)
        local old=preset;old.sections.settings.data.shipRadiusCm=nil;old.sections.settings.data.updatedAt.shipRadiusCm=nil
        assert(merge(smaller,old).sections.settings.data.shipRadiusCm==3500)
        assert(merge(ship,old).sections.settings.data.shipRadiusCm==5000)
        ''')

    def test_ship_radius_rejects_bad_values_and_half_present_stamp_pairs(self):
        self.lua.execute('''
        local base=codec.new();local before=raw(base)
        for _,value in ipairs({3499,5001,3500.5,"5000",false,math.huge,0/0}) do
            local job=codec.begin_update(base,{uiSettings={shipRadiusCm=value}},{now=100});drain(job)
            assert(job.state=="error" and raw(base)==before)
        end
        local missing_stamp=copy(base);missing_stamp.sections.settings.data.updatedAt.shipRadiusCm=nil
        local job=codec.begin_validate(missing_stamp);drain(job);assert(job.state=="error")
        local missing_value=copy(base);missing_value.sections.settings.data.shipRadiusCm=nil
        job=codec.begin_validate(missing_value);drain(job);assert(job.state=="error")
        ''')

    def test_v2_migrates_without_renewing_existing_records_or_touching_unknown_sections(self):
        self.lua.execute('''
        local old=replica("saved","Saved",19);old.schemaVersion=2;old.sections.favorites=nil
        old.sections.settings={schemaVersion=2,data={radiusCm=7500,updatedAt={radiusCm=42}}}
        old.sections.future={schemaVersion=91,data={enabled=false,empty=json.array()},extra="retained"}
        local before=raw(old);local job=codec.begin_decode(before);drain(job);assert(job.state=="done",job.error)
        local d=job:get_result();assert(d.schemaVersion==3 and d.sections.settings.schemaVersion==4)
        local s=d.sections.settings.data
        assert(s.radiusCm==nil and s.updatedAt.radiusCm==nil and s.sort=="name_asc")
        assert(s.lastPage=="learned" and s.showNames==false and s.showDeveloperTools==false)
        assert(s.tileDensity=="comfortable" and s.updatedAt.sort==0 and s.updatedAt.showDeveloperTools==0)
        assert(#d.sections.favorites.data.records==0 and d.sections.presets.data.presets[1].updatedAt==19)
        assert(d.sections.future.extra=="retained" and raw(old)==before)
        assert(raw(result(codec.begin_decode(job:get_encoded())))==raw(d))
        ''')

    def test_settings_merge_independently_and_real_edits_are_monotonic(self):
        self.lua.execute('''
        local original=replica("saved","Saved",7)
        local a=update(original,{uiSettings={sort="count_desc",showNames=true}},100)
        local b=update(original,{uiSettings={lastPage="ready",showDeveloperTools=true,tileDensity="compact"}},101)
        local d=merge(a,b);local s=d.sections.settings.data
        assert(s.sort=="count_desc" and s.showNames and s.lastPage=="ready" and s.showDeveloperTools)
        assert(s.tileDensity=="compact" and s.updatedAt.sort==100 and s.updatedAt.lastPage==101)
        assert(s.updatedAt.shipRadiusCm==0 and d.sections.presets.data.presets[1].updatedAt==7)
        assert(raw(d)==raw(merge(b,a)))
        local same=update(d,{uiSettings={sort="count_desc",showNames=true}},9999)
        assert(raw(same)==raw(d))
        local changed=update(d,{uiSettings={showNames=false}},1)
        assert(changed.sections.settings.data.updatedAt.showNames==101)
        assert(changed.sections.settings.data.updatedAt.sort==100 and changed.revision==d.revision+1)
        ''')

    def test_favorite_removal_survives_old_save_and_duplicate_edits_do_not_stamp(self):
        self.lua.execute('''
        local base=replica("saved","Saved",7)
        local old=update(base,{favorite={assetType="Item",assetName="DA_A",enabled=true}},100)
        local removed=update(old,{favorite={assetType="Item",assetName="DA_A",enabled=false}},99)
        local row=removed.sections.favorites.data.records[1]
        assert(not row.enabled and row.updatedAt==101 and #removed.sections.favorites.data.records==1)
        assert(raw(update(removed,{favorite={assetType="Item",assetName="DA_A",enabled=false}},9999))==raw(removed))
        local merged=merge(removed,old)
        assert(not merged.sections.favorites.data.records[1].enabled and raw(merged)==raw(merge(old,removed)))
        assert(merged.sections.presets.data.presets[1].updatedAt==7)
        local unseen=update(base,{favorite={assetType="Item",assetName="DA_Unknown",enabled=false}},100)
        assert(#unseen.sections.favorites.data.records==1 and not unseen.sections.favorites.data.records[1].enabled)
        ''')

    def test_favorite_union_canonical_ties_and_merge_laws(self):
        self.lua.execute('''
        local base=codec.new()
        local a=update(base,{favorite={assetType="Item",assetName="DA_B",enabled=true}},100)
        local b=update(base,{favorite={assetType="Item",assetName="DA_A",enabled=false}},100)
        local c=update(base,{favorite={assetType="Item",assetName="DA_B",enabled=false}},100)
        local d=merge(a,b);local rows=d.sections.favorites.data.records
        assert(#rows==2 and rows[1].assetName=="DA_A" and rows[2].assetName=="DA_B")
        assert(raw(merge(a,b))==raw(merge(b,a)))
        assert(raw(merge(merge(a,b),c))==raw(merge(a,merge(b,c))))
        local tied=merge(a,c);local previous=a.sections.favorites.data.records[1]
        local candidate=c.sections.favorites.data.records[1]
        local expected=json.byte_less(raw(previous),raw(candidate)) and candidate or previous
        assert(raw(tied.sections.favorites.data.records[1])==raw(expected))
        assert(raw(merge(tied,tied))==raw(tied))
        assert(codec.favorite_key("AB","C")~=codec.favorite_key("A","BC"))
        ''')

    def test_invalid_preference_records_and_future_required_schemas_are_rejected(self):
        edits = [
            'd.sections.favorites=nil',
            'd.sections.favorites.schemaVersion=2',
            'd.sections.favorites.data.records={}',
            'd.sections.favorites.data.records=json.array({{assetType="Item",assetName="DA_A",enabled=true,updatedAt=-1}})',
            'd.sections.favorites.data.records=json.array({{assetType="Item",assetName="DA_A",enabled="true",updatedAt=1}})',
            'd.sections.favorites.data.records=json.array({{assetType="../Item",assetName="DA_A",enabled=true,updatedAt=1}})',
            'd.sections.favorites.data.records=json.array({{assetType="Item",assetName="DA_A",enabled=true,updatedAt=1,runtimeRef="bad"}})',
            'local r={assetType="Item",assetName="DA_A",enabled=true,updatedAt=1};d.sections.favorites.data.records=json.array({r,r})',
            'd.sections.settings.data.lastPage="debug"',
            'd.sections.settings.data.sort="arbitrary"',
            'd.sections.settings.data.showNames=1',
            'd.sections.settings.data.showDeveloperTools="false"',
            'd.sections.settings.data.tileDensity="unbounded"',
            'd.sections.settings.data.updatedAt.sort=nil',
            'd.sections.settings.data.updatedAt.showNames=0.5',
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                self.lua.execute('local d=codec.new();' + edit + ';job=codec.begin_validate(d);drain(job);assert(job.state=="error" and job:get_result()==nil)')
        self.lua.execute('local d=codec.new();codec.VERSION=2;job=codec.begin_validate(d);drain(job);assert(job.state=="error");codec.VERSION=3')

    def test_invalid_edits_do_not_mutate_original_and_document_cap_is_shared(self):
        edits = [
            '{uiSettings={lastPage="debug"}}', '{uiSettings={radiusCm=7500}}',
            '{uiSettings={updatedAt={sort=99}}}', '{uiSettings={query="private"}}',
            '{uiSettings=false}', '{uiSettings=json.array()}',
            '{favorite={assetType="Item",assetName="DA_A",enabled=1}}',
            '{favorite={assetType="Item",assetName="DA_A",enabled=true,updatedAt=99}}',
        ]
        for change in edits:
            with self.subTest(change=change):
                self.lua.execute('local d=codec.new();local before=raw(d);job=codec.begin_update(d,' + change + ',{now=100});drain(job);assert(job.state=="error" and raw(d)==before)')
        self.lua.execute('''
        local d=codec.new()
        for index=1,100 do
            d.sections.favorites.data.records[index]={assetType="Item",assetName="DA_"..string.rep("x",240)..index,enabled=true,updatedAt=1}
        end
        assert(#raw(d)<codec.MAX_BYTES)
        d.sections.future={schemaVersion=1,data=""}
        local padding=codec.MAX_BYTES-#raw(d)-32;assert(padding>0 and padding<=4096)
        d.sections.future.data=string.rep("q",padding)
        local before=raw(d);assert(#before<codec.MAX_BYTES)
        job=codec.begin_update(d,{favorite={assetType="Item",assetName="DA_New",enabled=true}},{now=100});drain(job)
        assert(job.state=="error" and raw(d)==before and job:get_result()==nil)
        ''')

    def test_record_limits_and_preference_job_cancellation_remain_bounded(self):
        self.lua.execute('''
        local d=codec.new()
        for index=1,codec.MAX_FAVORITES do
            d.sections.favorites.data.records[index]={assetType="Item",assetName="P"..index,enabled=false,updatedAt=1}
        end
        local before=raw(d);assert(#before<codec.MAX_BYTES)
        job=codec.begin_update(d,{favorite={assetType="Item",assetName="New",enabled=true}},{now=100})
        drain(job);assert(job.state=="error" and raw(d)==before)
        local fresh=codec.new();local change={favorite={assetType="Item",assetName="New",enabled=true}}
        job=codec.begin_update(fresh,change,{now=100});local phases={}
        while job.state=="running" do phases[job.phase]=true;local before=job.completed;job:step_one();assert(job.completed==before+1) end
        assert(job.state=="done",job.error)
        for phase in pairs(phases) do
            local other=codec.begin_update(fresh,change,{now=100})
            while other.state=="running" and other.phase~=phase do other:step_one() end
            assert(other.state=="running");other:cancel("Scope changed")
            assert(other.state=="cancelled" and other:get_result()==nil)
        end
        assert(#fresh.sections.favorites.data.records==0)
        ''')


class PreferenceStoreTests(unittest.TestCase):
    def _runtime(self, worker=False):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts_path = SCRIPTS.as_posix()
        lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        lua.execute(STORE_FIXTURE)
        if worker:
            lua.execute(BACKGROUND)
            lua.execute('store=worker_store()')
        return lua

    def test_deposit_modes_reload_from_either_library_or_hud_mirror(self):
        for worker in (False, True):
            with self.subTest(worker=worker):
                lua = self._runtime(worker)
                lua.execute('''
                local function check(job,stock,button)
                    drain(job);assert(job.state=="done",job.error)
                    local data=job:get_result().sections.settings.data
                    assert(data.depositSimilarAllColors==stock and data.showDepositAllPaints==button)
                    assert(files["library.json"]==raw_by_context.A)
                end
                check(store.begin_ui_settings({depositSimilarAllColors=false,showDepositAllPaints=true}),false,true)
                local previous=raw_by_context.A
                local options=store.backend=="native_worker" and {document={now=100}} or settings
                local function reopen() return factory("A",nil,options) end
                raw_by_context.A=nil
                check(reopen().begin_state_load(),false,true)
                check(reopen().begin_ui_settings({depositSimilarAllColors=true,showDepositAllPaints=false}),true,false)
                files["library.json"]=previous
                check(reopen().begin_state_load(),true,false)
                local saved=files["library.json"]
                local invalid=reopen().begin_ui_settings({depositSimilarAllColors=false,showDepositAllPaints="true"})
                drain(invalid);assert(invalid.state=="error")
                assert(files["library.json"]==saved and raw_by_context.A==saved)
                ''')

    def test_ship_radius_35_50_roundtrip_and_invalid_edit_preserves_both_backends(self):
        for worker in (False, True):
            with self.subTest(worker=worker):
                lua = self._runtime(worker)
                lua.execute('''
                job=store.begin_state_load();drain(job);assert(job.state=="done",job.error)
                assert(job:get_result().sections.settings.data.shipRadiusCm==3500)
                job=store.begin_ui_settings({shipRadiusCm=5000});drain(job);assert(job.state=="done",job.error)
                local settings=job:get_result().sections.settings.data
                assert(settings.shipRadiusCm==5000 and settings.radiusCm==nil and settings.updatedAt.shipRadiusCm==100)
                local options=store.backend=="native_worker" and {document={now=100}} or _G.settings
                local reloaded=factory("A",nil,options).begin_state_load();drain(reloaded);assert(reloaded.state=="done",reloaded.error)
                assert(reloaded:get_result().sections.settings.data.shipRadiusCm==5000)
                job=store.begin_ui_settings({shipRadiusCm=3500});drain(job);assert(job.state=="done",job.error)
                assert(job:get_result().sections.settings.data.shipRadiusCm==3500)
                local before=files["library.json"];local mirror=raw_by_context.A
                job=store.begin_ui_settings({shipRadiusCm=5001});drain(job)
                assert(job.state=="error" and files["library.json"]==before and raw_by_context.A==mirror)
                ''')

    def test_both_backends_publish_ui_and_false_membership_through_existing_library(self):
        for worker in (False, True):
            with self.subTest(worker=worker):
                lua = self._runtime(worker)
                lua.execute('''
                job=store.begin_ui_settings({sort="count_desc",lastPage="ready",showDeveloperTools=true});drain(job)
                assert(job.state=="done" and job:get_progress().staged,job.error)
                local d=job:get_result();assert(d.sections.settings.data.sort=="count_desc")
                assert(d.sections.settings.data.lastPage=="ready" and d.sections.settings.data.showDeveloperTools)
                job=store.begin_favorite("Item","DA_A",true);drain(job);assert(job.state=="done",job.error)
                job=store.begin_favorite("Item","DA_A",false);drain(job);assert(job.state=="done",job.error)
                local row=job:get_result().sections.favorites.data.records[1]
                assert(row.enabled==false and row.updatedAt==101)
                assert(raw_by_context.A==files["library.json"] and files["library.json.lock"]==nil)
                for path in pairs(files) do assert(path=="library.json" or path=="library.json.backup",path) end
                ''')

    def test_invalid_ui_update_preserves_published_bytes_in_both_backends(self):
        for worker in (False, True):
            with self.subTest(worker=worker):
                lua = self._runtime(worker)
                lua.execute('''
                job=store.begin_ui_settings({showNames=true});drain(job);assert(job.state=="done",job.error)
                local before=files["library.json"];local mirror=raw_by_context.A
                job=store.begin_ui_settings({showDeveloperTools="true"});drain(job)
                assert(job.state=="error" and files["library.json"]==before and raw_by_context.A==mirror)
                assert(files["library.json.lock"]==nil)
                ''')

    def test_preferences_are_isolated_by_native_account_and_retained_across_same_account_saves(self):
        for worker in (False, True):
            with self.subTest(worker=worker):
                lua = self._runtime(worker)
                lua.execute('''
                job=store.begin_ui_settings({showNames=true,showDeveloperTools=true});drain(job);assert(job.state=="done",job.error)
                job=store.begin_favorite("Item","DA_A",true);drain(job);assert(job.state=="done",job.error)
                local options=store.backend=="native_worker" and {document={now=100}} or settings
                job=factory("B",nil,options).begin_state_load();drain(job);assert(job.state=="done",job.error)
                assert(job:get_result().sections.settings.data.showNames and #job:get_result().sections.favorites.data.records==1)
                job=factory("C",nil,options).begin_state_load();drain(job);assert(job.state=="done",job.error)
                assert(not job:get_result().sections.settings.data.showNames and not job:get_result().sections.settings.data.showDeveloperTools)
                assert(#job:get_result().sections.favorites.data.records==0)
                assert(files["other-library.json"]~=files["library.json"])
                ''')


if __name__ == "__main__":
    unittest.main()
