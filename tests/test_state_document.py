"""Native-save document schemas, roundtrips, migrations and bounded jobs."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"

FIXTURE = r'''
function budget(n)
    local result={units=0}
    function result:expired() return self.units>=n end
    function result:consume(n) self.units=self.units+(n or 1) end
    return result
end
function drain(job)
    for _=1,200000 do
        local b=budget(8);local p=job:step(b);assert(b.units<=8)
        if p.state~="running" then return p end
    end
    error("Document job stalled")
end
function preset()
    return {id="preset-1",updatedAt=0,name="My character",createdUtc="2026-11-17T12:00:00Z",gameBuild="25982825",
        target={kind="character",className="/Script/Voyage.VoyageCharacter",paintSetName="Character"},
        zones=json.array({{materialSlotName="Body",partName="Body",paint={assetType="Item",assetName="DA_Paint_Canada"}},
            {materialSlotName="Head",partName="Head",paint=json.null}})}
end
function document()
    local value=codec.new()
    value.sections.presets.data.presets[1]=preset()
    value.sections.future={schemaVersion=91,data={enabled=false,values=json.array({json.null,"paint",3})},extra="retained"}
    return value
end
'''


class StateDocumentTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path;json=require("paintmanager.json");codec=require("paintmanager.state_document")')
        self.lua.execute(FIXTURE)

    def test_factory_has_only_logical_save_state(self):
        raw = self.lua.execute('return json.encode(codec.new())')
        value = json.loads(raw)
        self.assertEqual(value, {"format": "PaintManager.State", "schemaVersion": 3, "revision": 0,
                                "sections": {"settings": {"schemaVersion": 4, "data": {
                                    "shipRadiusCm": 3500, "sort": "name_asc", "lastPage": "learned", "showNames": False,
                                    "tileDensity": "comfortable", "showDeveloperTools": False,
                                    "updatedAt": {key: 0 for key in ("shipRadiusCm", "sort", "lastPage", "showNames", "tileDensity", "showDeveloperTools")}}},
                                             "favorites": {"schemaVersion": 1, "data": {"records": []}},
                                             "presets": {"schemaVersion": 2, "data": {"schemaVersion": 2, "presets": [], "tombstones": []}}}})

    def test_encode_decode_roundtrip_preserves_unknown_sections_and_logical_presets(self):
        self.lua.execute('''
        original=document();job=codec.begin_encode(original)
        assert(job.completed==0 and job:get_result()==nil);drain(job)
        assert(job.state=="done");raw=job:get_result();assert(raw==json.encode(original))
        job=codec.begin_decode(raw);drain(job);assert(job.state=="done")
        decoded=job:get_result();assert(json.encode(decoded)==raw and decoded~=original)
        assert(decoded.sections.future.extra=="retained" and decoded.sections.future.schemaVersion==91)
        decoded.sections.settings.data.shipRadiusCm=4500
        job=codec.begin_encode(decoded);drain(job);assert(job.state=="done")
        assert(original.sections.settings.data.shipRadiusCm==3500)
        ''')

    def test_validate_makes_a_complete_independent_copy(self):
        self.lua.execute('''
        original=document();job=codec.begin_validate(original);drain(job)
        assert(job.state=="done");copy=job:get_result()
        original.sections.future.data.values[2]="changed"
        original.sections.presets.data.presets[1].zones[1].paint.assetName="Changed"
        assert(copy.sections.future.data.values[2]=="paint")
        assert(copy.sections.presets.data.presets[1].zones[1].paint.assetName=="DA_Paint_Canada")
        ''')

    def test_direct_copy_encodes_once_without_reparsing_plain_data(self):
        self.lua.execute('''
        local reader=require("paintmanager.json_reader")
        local parse,encode=reader.begin,json.begin
        local parses,encodes=0,0
        reader.begin=function(...) parses=parses+1;return parse(...) end
        json.begin=function(...) encodes=encodes+1;return encode(...) end
        for _,factory in ipairs({codec.begin_validate,codec.begin_encode}) do
            parses,encodes=0,0
            local source=document();local job=factory(source)
            assert(job:get_document()==nil and job:get_encoded()==nil)
            drain(job);assert(job.state=="done",job.error)
            assert(parses==0 and encodes==1)
            assert(job:get_document()~=source and job:get_encoded()==json.encode(job:get_document()))
            source.sections.future.data.values[2]="External edit"
            assert(job:get_document().sections.future.data.values[2]=="paint")
        end
        parses,encodes=0,0
        local job=codec.begin_decode(json.encode(document()));drain(job)
        assert(job.state=="done" and parses==1 and encodes==1)
        assert(job:get_encoded()==json.encode(job:get_document()))
        ''')

    def test_direct_clone_preserves_empty_markers_shared_values_and_strict_array_shapes(self):
        self.lua.execute('''
        local source=document()
        local shared={number=0.1,text=string.char(0,8,9,10,12,13,31,34,92).."Ä"}
        source.sections.future.data={a=shared,b=shared,object={},array=json.array()}
        local job=codec.begin_validate(source);drain(job);assert(job.state=="done",job.error)
        local copy=job:get_result()
        assert(copy.sections.future.data.a~=shared and copy.sections.future.data.a~=copy.sections.future.data.b)
        assert(json.is_array(copy.sections.future.data.array) and not json.is_array(copy.sections.future.data.object))
        assert(job:get_encoded()==json.encode(source))
        for _,bad in ipairs({ { [1]="unmarked" },json.array({[2]="hole"}),json.array({named="field"}) }) do
            source.sections.future.data=bad;job=codec.begin_validate(source);drain(job)
            assert(job.state=="error" and job:get_document()==nil and job:get_encoded()==nil)
        end
        ''')

    def test_clone_exact_canonical_byte_accounting_includes_escaped_text_and_float_numbers(self):
        self.lua.execute('''
        local source=document()
        source.sections.future.data={number=0.1,other=-0.0,text=string.char(0,8,9,10,12,13,31,34,92).."Ä"}
        local expected=json.encode(source)
        local job=codec.begin_encode(source,{max_bytes=#expected});drain(job)
        assert(job.state=="done" and job:get_encoded()==expected)
        job=codec.begin_encode(source,{max_bytes=#expected-1});drain(job)
        assert(job.state=="error" and job:get_result()==nil)
        ''')

    def test_malformed_future_and_unrecognized_known_data_fail_without_result(self):
        edits = [
            'x.format="AnotherMod"', 'x.schemaVersion=4', 'x.schemaVersion=-1', 'x.schemaVersion=0',
            'x.revision=-1', 'x.revision=0.5', 'x.revision=9007199254740992',
            'x.sections.settings.schemaVersion=5', 'x.sections.presets.schemaVersion=3',
            'x.sections.settings.data.shipRadiusCm=3499', 'x.sections.settings.data.shipRadiusCm=5001',
            'x.sections.settings.data.shipRadiusCm=0/0', 'x.sections.settings.data.shipRadiusCm="4000"',
            'x.sections.settings.data.transientPath="/Game/Runtime"',
            'x.sections.presets.data.presets[1].zones[1].slotIndex=3',
            'x.sections.presets.data.schemaVersion=3', 'x.sections.presets=nil',
            'x.sections.presets.data.presets={}', 'x.sections.settings.data={}',
            'x.sections.future.schemaVersion=0', 'x.sections.future.data=nil',
            'x.sections["../bad"]={schemaVersion=1,data={}}', 'x.unknown="ignored?"',
            'x.sections.settings.extra=true', 'x.sections.settings=json.array()',
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                self.lua.execute('x=document();' + edit + ';job=codec.begin_validate(x);drain(job)')
                self.assertEqual(self.lua.eval('job.state'), 'error')
                self.assertIsNone(self.lua.eval('job:get_result()'))

    def test_decode_strict_json_does_not_turn_invalid_data_into_defaults(self):
        cases = [
            'null', '[]', '{}', 'return {}', '{"format":"PaintManager.State","format":"PaintManager.State"}',
            '{"format":"PaintManager.State","schemaVersion":1e999}',
            '{"format":"PaintManager.State","schemaVersion":1,}',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.lua.globals().raw = raw
                self.lua.execute('job=codec.begin_decode(raw);drain(job)')
                self.assertEqual(self.lua.eval('job.state'), 'error')
                self.assertIsNone(self.lua.eval('job:get_result()'))

    def test_decode_preserves_empty_unknown_object_and_array_distinction(self):
        self.lua.execute('''
        x=codec.new();x.sections.future={schemaVersion=2,data={object={},array=json.array()}}
        job=codec.begin_decode(json.encode(x));drain(job);assert(job.state=="done")
        job=codec.begin_encode(job:get_result());drain(job);assert(job.state=="done");raw=job:get_result()
        ''')
        data = json.loads(self.lua.eval('raw'))['sections']['future']['data']
        self.assertEqual(data, {'array': [], 'object': {}})

    def test_explicit_root_and_known_section_migrations_apply_in_order(self):
        self.lua.execute('''
        x=document();x.schemaVersion=0;x.sections.settings.schemaVersion=0
        x.sections.settings.data={radiusMeters=75};x.sections.presets.schemaVersion=0
        x.sections.presets.data.schemaVersion=1;x.sections.presets.data.tombstones=nil
        x.sections.presets.data.presets[1].updatedAt=nil
        calls={}
        options={migrations={
            root={[0]=function(old) calls[#calls+1]="root";old.schemaVersion=1;old.revision=4;return old end},
            settings={[0]=function(old)
                calls[#calls+1]="settings";return {schemaVersion=1,data={radiusCm=old.data.radiusMeters*100}}
            end},
            presets={[0]=function(old) calls[#calls+1]="presets";old.schemaVersion=1;return old end},
        }}
        job=codec.begin_decode(json.encode(x),options);drain(job)
        assert(job.state=="done" and job:get_progress().migrated)
        result=job:get_result();assert(result.schemaVersion==codec.VERSION and result.revision==4)
        assert(result.sections.settings.data.radiusCm==nil)
        assert(table.concat(calls,",")=="root,settings,presets")
        assert(result.sections.future.schemaVersion==91 and result.sections.future.extra=="retained")
        job=codec.begin_encode(result,options);drain(job)
        assert(job.state=="done" and not job:get_progress().migrated and #calls==3)
        ''')

    def test_migration_errors_missing_routes_skips_and_invalid_output_fail(self):
        bodies = [
            'return old',
            'error("Cannot migrate")',
            'old.schemaVersion=2;return old',
            'return nil',
            'old.schemaVersion=1;old.revision=-1;return old',
            'old.schemaVersion=1;old.sections.future.data=string.rep("x",5000);return old',
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.lua.execute('''x=document();x.schemaVersion=0
                    job=codec.begin_validate(x,{migrations={root={[0]=function(old) ''' + body + ''' end}}});drain(job)''')
                self.assertEqual(self.lua.eval('job.state'), 'error')
                self.assertIsNone(self.lua.eval('job:get_result()'))
        self.lua.execute('x=document();x.sections.settings.schemaVersion=0;job=codec.begin_validate(x);drain(job)')
        self.assertIn('Missing settings migration', self.lua.eval('job.error'))

    def test_future_versions_never_call_migrations(self):
        self.lua.execute('''
        called=0;x=document();x.schemaVersion=codec.VERSION+1
        job=codec.begin_validate(x,{migrations={root={[2]=function(old) called=called+1;return old end}}})
        drain(job);assert(job.state=="error" and called==0)
        ''')

    def test_default_module_registry_is_used_without_caller_options(self):
        self.lua.execute('''
        codec.migrations.settings[0]=function(old)
            return {schemaVersion=1,data={radiusCm=old.data.radiusMeters*100}}
        end
        x=document();x.sections.settings={schemaVersion=0,data={radiusMeters=75}}
        job=codec.begin_decode(json.encode(x));drain(job)
        assert(job.state=="done" and job:get_result().sections.settings.data.radiusCm==nil)
        codec.migrations.settings[0]=nil
        ''')

    def test_sequential_migrations_support_multiple_steps_and_reject_a_gap(self):
        self.lua.execute('''
        codec.VERSION=3;local calls={}
        for version=0,2 do
            codec.migrations.root[version]=function(old)
                calls[#calls+1]=old.schemaVersion;old.schemaVersion=old.schemaVersion+1;return old
            end
        end
        local x=document();x.schemaVersion=0
        job=codec.begin_validate(x);drain(job)
        assert(job.state=="done" and job:get_result().schemaVersion==3)
        assert(table.concat(calls,",")=="0,1,2")
        codec.migrations.root[1]=nil
        job=codec.begin_validate(x);drain(job)
        assert(job.state=="error" and job.error:find("Missing root migration from version 1",1,true))
        ''')

    def test_output_size_utf8_depth_nodes_sections_and_plain_values_are_bounded(self):
        edits = [
            'x.sections.future.data=string.rep("x",4097)',
            'x.sections.future.data=string.char(0xff)',
            'x.sections.future.data=function() end',
            'x.sections.future.data=setmetatable({},{__index=function() return "hidden" end})',
            'x.sections.future.data=x',
            'x.sections.future.data={};local d=x.sections.future.data;for i=1,17 do d.a={};d=d.a end',
            'for i=1,31 do x.sections["future"..i]={schemaVersion=1,data=false} end',
            'x.sections.future.data=json.array();for i=1,12000 do x.sections.future.data[i]=false end',
            'x.sections.future.data=json.array();for i=1,9 do x.sections.future.data[i]=string.rep("x",4096) end',
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                self.lua.execute('x=document();' + edit + ';job=codec.begin_encode(x);drain(job)')
                self.assertEqual(self.lua.eval('job.state'), 'error')
        self.lua.execute('job=codec.begin_decode(string.rep(" ",32769));drain(job);assert(job.state=="error")')

    def test_smaller_caller_byte_limit_is_respected_but_never_raised(self):
        self.lua.execute('''
        job=codec.begin_encode(document(),{max_bytes=100});drain(job);assert(job.state=="error")
        x=document();x.sections.future.data=json.array();for i=1,9 do x.sections.future.data[i]=string.rep("x",4096) end
        job=codec.begin_encode(x,{max_bytes=10000000});drain(job);assert(job.state=="error")
        ''')

    def test_jobs_make_bounded_progress_and_can_be_cancelled_in_all_phases(self):
        self.lua.execute('''
        job=codec.begin_encode(document());local phases={}
        while job.state=="running" do
            phases[job.phase]=true;local before=job.completed;job:step_one();assert(job.completed==before+1)
        end
        assert(job.state=="done" and job.completed>50)
        for phase in pairs(phases) do
            local other=codec.begin_encode(document())
            while other.state=="running" and other.phase~=phase do other:step_one() end
            assert(other.state=="running");other:cancel("Context changed")
            assert(other.state=="cancelled" and other:get_result()==nil)
            local before=other.completed;other:step(budget(8));assert(other.completed==before)
        end
        ''')


if __name__ == "__main__":
    unittest.main()
