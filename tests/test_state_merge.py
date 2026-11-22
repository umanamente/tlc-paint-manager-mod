"""Real cooperative Lua reconciliation jobs and permanent deletion semantics."""
import unittest

import test_state_document as schema_tests


MERGE_FIXTURE = r'''
function result(job)
    drain(job);assert(job.state=="done",job.error);return job:get_result()
end
function copy(value) return result(codec.begin_decode(json.encode(value))) end
function replica(id,name,stamp,radius,radius_stamp)
    local d=codec.new()
    d.sections.settings.data.shipRadiusCm=radius or 3500
    if id then
        local p=preset();p.id=id;p.name=name or id;p.updatedAt=stamp or 0
        d.sections.presets.data.presets[1]=p
    end
    d.sections.settings.data.updatedAt.shipRadiusCm=radius_stamp or 0
    return d
end
function merge(a,b) return result(codec.begin_merge(a,b)) end
function update(a,change,now) return result(codec.begin_update(a,change,{now=now or 100})) end
function raw(value) return json.encode(value) end
function legacy_document()
    local p=preset();p.updatedAt=nil
    return {format=codec.FORMAT,schemaVersion=1,revision=7,sections={
        settings={schemaVersion=1,data={radiusCm=7500}},
        presets={schemaVersion=1,data={schemaVersion=1,presets=json.array({p})}},
        future={schemaVersion=99,data={unchanged=json.array({false,json.null,"future"})}}}}
end
'''


class StateMergeTests(unittest.TestCase):
    def setUp(self):
        schema_tests.StateDocumentTests.setUp(self)
        self.lua.execute(MERGE_FIXTURE)

    def test_change_jobs_reuse_field_validation_and_encode_the_final_document_once(self):
        self.lua.execute('''
        local reader=require("paintmanager.json_reader")
        local parse,encode=reader.begin,json.begin
        local parses,documents=0,0
        reader.begin=function(...) parses=parses+1;return parse(...) end
        json.begin=function(value,...)
            if type(value)=="table" and value.format==codec.FORMAT then documents=documents+1 end
            return encode(value,...)
        end
        local a,b=replica("a","A",1),replica("b","B",2)
        for _,job in ipairs({codec.begin_merge(a,b),codec.begin_update(a,{uiSettings={shipRadiusCm=4750}},{now=100}),
            codec.begin_import(legacy_document().sections.presets.data)}) do
            parses,documents=0,0;drain(job);assert(job.state=="done",job.error)
            assert(parses==0 and documents==1)
            assert(job:get_document()==job:get_result() and job:get_encoded()==raw(job:get_result()))
        end
        ''')

    def test_medium_update_has_a_regression_bound_on_charged_work(self):
        self.lua.execute('''
        local a=codec.new()
        for index=1,10 do
            local p=preset();p.id="id-"..index;p.name="Preset "..index;p.zones=json.array()
            for zone=1,8 do
                p.zones[zone]={materialSlotName="Slot"..zone,partName="Body",paint={assetType="Item",assetName="DA_Paint_Canada"}}
            end
            a.sections.presets.data.presets[index]=p
        end
        local job=codec.begin_update(a,{uiSettings={shipRadiusCm=4750}},{now=100});drain(job)
        assert(job.state=="done" and job.completed<11000,job.error or tostring(job.completed))
        assert(#job:get_encoded()<32768 and job:get_result().sections.settings.data.shipRadiusCm==4750)
        ''')

    def test_distinct_ids_same_names_and_equal_counts_form_union(self):
        self.lua.execute('''
        a=replica("a","Same name",12);b=replica("b","Same name",3)
        x=merge(a,b);assert(#x.sections.presets.data.presets==2)
        assert(x.sections.presets.data.presets[1].id=="a" and x.sections.presets.data.presets[2].id=="b")
        assert(raw(merge(a,b))==raw(merge(b,a)))
        assert(raw(merge(x,a))==raw(x) and raw(merge(x,b))==raw(x))
        ''')

    def test_whole_preset_newer_timestamp_wins_without_field_merge(self):
        self.lua.execute('''
        a=replica("same","Older",10);b=replica("same","Newer",11)
        b.sections.presets.data.presets[1].zones[1].paint=json.null
        x=merge(a,b);p=x.sections.presets.data.presets[1]
        assert(p.name=="Newer" and p.updatedAt==11 and p.zones[1].paint==json.null)
        assert(raw(x)==raw(merge(b,a)))
        ''')

    def test_equal_timestamps_use_complete_canonical_record_order(self):
        self.lua.execute('''
        a=replica("same","Alpha",10);b=replica("same","Zulu",10)
        local expected=raw(a.sections.presets.data.presets[1])>raw(b.sections.presets.data.presets[1])
            and a.sections.presets.data.presets[1] or b.sections.presets.data.presets[1]
        x=merge(a,b);assert(raw(x.sections.presets.data.presets[1])==raw(expected))
        assert(raw(x)==raw(merge(b,a)) and raw(merge(x,b))==raw(x))
        ''')

    def test_equal_time_unicode_winner_is_byte_ordered_across_available_locales(self):
        self.lua.execute('''
        local original=os.setlocale(nil,"collate")
        a=replica("same","Zebra",10);b=replica("same","äbc",10)
        local expected
        for _,locale in ipairs({"C","German_Germany.1252","German","de_DE.UTF-8","en_US.UTF-8",""}) do
            if os.setlocale(locale,"collate") then
                local x=merge(a,b)
                assert(x.sections.presets.data.presets[1].name=="äbc")
                assert(raw(x)==raw(merge(b,a)))
                if expected then assert(raw(x)==expected) else expected=raw(x) end
            end
        end
        assert(os.setlocale(original,"collate"))
        ''')

    def test_canonical_ties_with_long_common_prefixes_compare_in_bounded_units(self):
        self.lua.execute('''
        local a,b=codec.new(),codec.new()
        a.sections.future={schemaVersion=1,data={a=string.rep("a",4096),b=string.rep("b",4096),last="Alpha"}}
        b.sections.future={schemaVersion=1,data={a=string.rep("a",4096),b=string.rep("b",4096),last="Zulu"}}
        local compare=json.compare_bytes
        local calls,previous_index=0,0
        json.compare_bytes=function(left,right,index,maximum)
            if #left>8000 and #right>8000 then
                assert(maximum==1024)
                calls=calls+1;assert(index>previous_index);previous_index=index
            end
            return compare(left,right,index,maximum)
        end
        local job=codec.begin_merge(a,b)
        local comparison_units=0
        while job.state=="running" do
            if job.phase=="tie_compare" then comparison_units=comparison_units+1 end
            local before=job.completed;job:step(budget(1));assert(job.completed==before+1)
        end
        json.compare_bytes=compare
        assert(job.state=="done",job.error)
        assert(calls>=9 and comparison_units>=9)
        assert(job:get_result().sections.future.data.last=="Zulu")
        ''')

    def test_merge_laws_with_live_versions_deletions_settings_and_optional_sections(self):
        self.lua.execute('''
        a=replica("a","A",1,4000,4);b=replica("b","B",2,4500,5);c=replica("a","C",9,4750,5)
        a.sections.future={schemaVersion=1,data={value="alpha"}}
        b.sections.future={schemaVersion=2,data={value="beta"}}
        c.sections.presets.data.tombstones[1]={id="gone",deletedAt=1}
        expected=raw(merge(merge(a,b),c));values={a,b,c}
        for i=1,3 do for j=1,3 do for k=1,3 do
            if i~=j and i~=k and j~=k then
                assert(raw(merge(merge(values[i],values[j]),values[k]))==expected)
                assert(raw(merge(values[i],merge(values[j],values[k])))==expected)
            end
        end end end
        for _,v in ipairs(values) do local normalized=merge(v,v);assert(raw(merge(normalized,normalized))==raw(normalized)) end
        ''')

    def test_old_or_future_dated_live_save_cannot_resurrect_deletion(self):
        self.lua.execute('''
        a=replica("same","Saved",1);deleted=update(a,{deleteId="same"},100)
        old=replica("same","Wrong computer clock",9000000000000)
        for _,x in ipairs({merge(deleted,a),merge(a,deleted),merge(old,deleted),merge(deleted,old)}) do
            assert(#x.sections.presets.data.presets==0 and #x.sections.presets.data.tombstones==1)
            assert(x.sections.presets.data.tombstones[1].deletedAt==100)
        end
        assert(raw(update(deleted,{deleteId="same"},999))==raw(deleted))
        job=codec.begin_update(deleted,{preset=preset()},{now=100})
        job.change.preset.id="same";drain(job);assert(job.state=="error" and job.error:find("cannot be reused",1,true))
        ''')

    def test_deletion_recreation_uses_new_identity_and_keeps_marker(self):
        self.lua.execute('''
        a=replica("same","Saved",1);deleted=update(a,{deleteId="same"},10)
        p=preset();p.id="new";p.name="Saved"
        x=update(deleted,{preset=p},11)
        assert(#x.sections.presets.data.presets==1 and x.sections.presets.data.presets[1].id=="new")
        assert(x.sections.presets.data.tombstones[1].id=="same")
        ''')

    def test_tombstone_union_never_expires_and_uses_greater_provenance_time(self):
        self.lua.execute('''
        a=codec.new();b=codec.new()
        a.sections.presets.data.tombstones=json.array({{id="a",deletedAt=0},{id="b",deletedAt=2}})
        b.sections.presets.data.tombstones=json.array({{id="a",deletedAt=9},{id="c",deletedAt=0}})
        x=merge(a,b);assert(#x.sections.presets.data.tombstones==3)
        assert(x.sections.presets.data.tombstones[1].id=="a" and x.sections.presets.data.tombstones[1].deletedAt==9)
        assert(raw(x)==raw(merge(b,a)) and raw(x)==raw(merge(x,a)))
        ''')

    def test_setting_merge_is_independent_of_preset_version(self):
        self.lua.execute('''
        a=replica("a","A",999,4000,1);b=replica("a","B",2,4500,4)
        x=merge(a,b);assert(x.sections.presets.data.presets[1].name=="A")
        assert(x.sections.settings.data.shipRadiusCm==4500 and x.sections.settings.data.updatedAt.shipRadiusCm==4)
        assert(raw(x)==raw(merge(b,a)))
        ''')

    def test_equal_setting_time_uses_canonical_value_not_direction(self):
        self.lua.execute('''
        a=replica(nil,nil,nil,4000,3);b=replica(nil,nil,nil,4500,3)
        x=merge(a,b);assert(x.sections.settings.data.shipRadiusCm==4500)
        assert(raw(x)==raw(merge(b,a)))
        ''')

    def test_real_setting_edits_are_monotonic_with_same_second_and_clock_rollback(self):
        self.lua.execute('''
        a=codec.new();x=update(a,{uiSettings={shipRadiusCm=4000}},100)
        assert(x.sections.settings.data.updatedAt.shipRadiusCm==100)
        y=update(x,{uiSettings={shipRadiusCm=4500}},100);assert(y.sections.settings.data.updatedAt.shipRadiusCm==101)
        z=update(y,{uiSettings={shipRadiusCm=3500}},5);assert(z.sections.settings.data.updatedAt.shipRadiusCm==102)
        assert(z.revision==3 and raw(update(z,{uiSettings={shipRadiusCm=3500}},99999))==raw(z))
        assert(raw(merge(z,a))==raw(z))
        ''')

    def test_real_preset_edits_stamp_once_noop_and_repeated_publication_keep_time(self):
        self.lua.execute('''
        a=replica("preset-1","My character",9);p=preset()
        x=update(a,{preset=p},100);assert(raw(x)==raw(a))
        p.name="Changed";x=update(a,{preset=p},100)
        assert(x.sections.presets.data.presets[1].updatedAt==100 and x.revision==1)
        assert(raw(update(x,{preset=p},9999))==raw(x))
        p.name="Fast edit";y=update(x,{preset=p},100)
        assert(y.sections.presets.data.presets[1].updatedAt==101)
        p.name="Clock rollback";z=update(y,{preset=p},0)
        assert(z.sections.presets.data.presets[1].updatedAt==102)
        assert(raw(merge(z,z))==raw(z))
        ''')

    def test_new_record_ignores_supplied_clock_metadata_and_stamps_actual_creation(self):
        self.lua.execute('''
        a=codec.new();p=preset();p.updatedAt=999999
        x=update(a,{preset=p},123);assert(x.sections.presets.data.presets[1].updatedAt==123)
        assert(p.updatedAt==999999)
        ''')

    def test_native_v1_and_legacy_library_migration_use_stable_zero(self):
        self.lua.execute('''
        old=legacy_document();before=raw(old)
        x=result(codec.begin_decode(before));assert(x.schemaVersion==codec.VERSION and x.revision==7)
        assert(x.sections.settings.data.updatedAt.shipRadiusCm==0)
        assert(x.sections.presets.data.presets[1].updatedAt==0 and x.sections.presets.data.presets[1].id=="preset-1")
        assert(x.sections.future.schemaVersion==99 and raw(old)==before)
        y=result(codec.begin_import(old.sections.presets.data))
        assert(y.sections.presets.data.presets[1].updatedAt==0)
        assert(raw(merge(x,x))==raw(x) and raw(result(codec.begin_decode(before)))==raw(x))
        ''')

    def test_missing_record_does_not_mean_deletion(self):
        self.lua.execute('''
        a=replica("a","A",1);b=codec.new()
        x=merge(a,b);assert(#x.sections.presets.data.presets==1 and #x.sections.presets.data.tombstones==0)
        ''')

    def test_required_v2_metadata_cannot_be_silently_edited_by_v1_reader(self):
        self.lua.execute('''
        local data=replica("a","A",9);local bytes=raw(data)
        codec.VERSION=1
        job=codec.begin_decode(bytes);drain(job)
        assert(job.state=="error" and job.error:find("Unsupported future root",1,true))
        codec.VERSION=3
        assert(raw(data)==bytes)
        ''')

    def test_invalid_timestamps_tombstones_and_future_schemas_fail(self):
        edits = [
            'x.sections.presets.data.presets[1].updatedAt=nil',
            'x.sections.presets.data.presets[1].updatedAt=-1',
            'x.sections.presets.data.presets[1].updatedAt=0.5',
            'x.sections.presets.data.presets[1].updatedAt=0/0',
            'x.sections.presets.data.presets[1].updatedAt=9007199254740992',
            'x.sections.settings.data.updatedAt.shipRadiusCm=nil',
            'x.sections.settings.data.updatedAt.shipRadiusCm="1"',
            'x.sections.settings.data.updatedAt.shipRadiusCm=-1',
            'x.sections.presets.data.tombstones=nil',
            'x.sections.presets.data.tombstones=json.array({{id="a",deletedAt=-1}})',
            'x.sections.presets.data.tombstones=json.array({{id="a",deletedAt=1},{id="a",deletedAt=2}})',
            'x.sections.presets.data.tombstones=json.array({{id="preset-1",deletedAt=1}})',
            'x.sections.presets.data.schemaVersion=1',
            'x.schemaVersion=codec.VERSION+1',
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                self.lua.execute('x=replica("preset-1","Saved",1);' + edit + ';job=codec.begin_merge(x,codec.new());drain(job)')
                self.assertEqual(self.lua.eval('job.state'), 'error')
                self.assertIsNone(self.lua.eval('job:get_result()'))

    def test_integer_limits_and_bad_clock_never_publish_partial_update(self):
        for now in ['-1', '0.5', '0/0', '9007199254740992', '"100"']:
            self.lua.execute('a=codec.new();job=codec.begin_update(a,{uiSettings={shipRadiusCm=4000}},{now=' + now + '});drain(job)')
            self.assertEqual(self.lua.eval('job.state'), 'error')
            self.assertEqual(self.lua.eval('a.sections.settings.data.shipRadiusCm'), 3500)
        self.lua.execute('''
        a=replica("a","A",9007199254740991)
        job=codec.begin_update(a,{deleteId="a"},{now=1});drain(job);assert(job.state=="error")
        assert(a.sections.presets.data.presets[1].id=="a")
        ''')

    def test_accepted_clock_skew_can_choose_wrong_live_version_but_not_undo_delete(self):
        self.lua.execute('''
        wrong=replica("a","Clock set far ahead",9999999999)
        actual=replica("a","Real later edit",200)
        x=merge(wrong,actual);assert(x.sections.presets.data.presets[1].name=="Clock set far ahead")
        deleted=update(actual,{deleteId="a"},201)
        assert(#merge(wrong,deleted).sections.presets.data.presets==0)
        ''')

    def test_combined_overflow_and_metadata_limit_fail_without_truncation(self):
        self.lua.execute('''
        a=codec.new();b=codec.new()
        for i=1,33 do
            local p=preset();p.id="a"..i;a.sections.presets.data.presets[i]=p
            local q=preset();q.id="b"..i;b.sections.presets.data.presets[i]=q
        end
        job=codec.begin_merge(a,b);drain(job);assert(job.state=="error" and job:get_result()==nil)
        a=codec.new();b=codec.new()
        for i=1,129 do
            a.sections.presets.data.tombstones[i]={id="a"..i,deletedAt=1}
            b.sections.presets.data.tombstones[i]={id="b"..i,deletedAt=1}
        end
        job=codec.begin_merge(a,b);drain(job);assert(job.state=="error" and job:get_result()==nil)
        a=codec.new();b=codec.new()
        a.sections.future={schemaVersion=1,data={values=json.array({string.rep("a",4000),string.rep("a",4000),string.rep("a",4000),string.rep("a",4000)})}}
        b.sections.other={schemaVersion=1,data={values=json.array({string.rep("b",4000),string.rep("b",4000),string.rep("b",4000),string.rep("b",4000),string.rep("b",4000)})}}
        job=codec.begin_merge(a,b);drain(job);assert(job.state=="error" and job:get_result()==nil)
        ''')

    def test_all_merge_and_update_phases_are_cancellable_and_charge_one_unit(self):
        self.lua.execute('''
        local a=replica("a","A",1);local b=replica("a","B",1)
        b.sections.future={schemaVersion=1,data="future"};a.sections.future={schemaVersion=1,data="other"}
        for _,factory in ipairs({function() return codec.begin_merge(a,b) end,
            function() local p=preset();p.id="a";p.name="Edited";return codec.begin_update(a,{preset=p},{now=8}) end,
            function() return codec.begin_update(a,{deleteId="a"},{now=8}) end}) do
            local phases={};local job=factory()
            while job.state=="running" do
                phases[job.phase]=true;local before=job.completed;local p=job:step(budget(1))
                assert(job.completed==before+1)
            end
            assert(job.state=="done",job.error)
            for phase in pairs(phases) do
                local other=factory()
                while other.state=="running" and other.phase~=phase do other:step_one() end
                assert(other.state=="running");other:cancel("Context changed")
                assert(other.state=="cancelled" and other:get_result()==nil)
                local before=other.completed;other:step(budget(8));assert(other.completed==before)
            end
        end
        ''')


if __name__ == '__main__':
    unittest.main()
