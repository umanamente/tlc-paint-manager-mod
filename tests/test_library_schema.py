"""Direct logical validation, exact capacity accounting and independent copies."""
import unittest

import test_state_document as state_tests


class LibrarySchemaTests(unittest.TestCase):
    def setUp(self):
        state_tests.StateDocumentTests.setUp(self)
        self.lua.execute('schema=require("paintmanager.library_schema")')

    def test_exact_count_matches_full_canonical_output_without_encoding_the_library(self):
        self.lua.execute('''
        local p=preset();p.updatedAt=10.0;p.name='Quoted " name'..string.char(92).." Ä"
        p.target.displayName="";p.zones[1].displayName='Zone "'..string.char(92)
        local value={schemaVersion=2,presets=json.array({p}),tombstones=json.array({{id="deleted",deletedAt=9007199254740991}})}
        local expected=json.encode(value)
        local encode=json.begin
        json.begin=function() error("Library validation must not create another streaming encoder") end
        local job=schema.begin_validate(value,{max_bytes=#expected});drain(job)
        assert(job.state=="done",job.error)
        assert(job:get_progress().bytes==#expected and json.encode(job:get_result())==expected)
        value.presets[1].zones[1].paint.assetName="Changed externally"
        assert(job:get_result().presets[1].zones[1].paint.assetName=="DA_Paint_Canada")
        job=schema.begin_validate(job:get_result(),{max_bytes=#expected-1});drain(job)
        assert(job.state=="error" and job:get_result()==nil)
        json.begin=encode
        ''')

    def test_legacy_validation_still_rejects_duplicate_names_and_preserves_zero_time(self):
        self.lua.execute('''
        local a,b=preset(),preset();a.updatedAt=nil;b.updatedAt=nil;b.id="different-id";b.name=a.name:upper()
        local source={schemaVersion=1,presets=json.array({a,b})}
        local job=schema.begin_validate(source);drain(job)
        assert(job.state=="error" and job.error:find("Duplicate legacy preset name",1,true))
        source.presets[2]=nil;job=schema.begin_validate(source);drain(job)
        assert(job.state=="done" and job:get_result().presets[1].updatedAt==0 and source.presets[1].updatedAt==nil)
        assert(job.bytes==#json.encode(job:get_result()))
        ''')

    def test_direct_record_and_zone_steps_are_cancellable(self):
        self.lua.execute('''
        local value={schemaVersion=2,presets=json.array({preset()}),tombstones=json.array({{id="deleted",deletedAt=0}})}
        local job=schema.begin_validate(value);local phases={}
        while job.state=="running" do
            phases[job.phase]=true;local before=job.completed;job:step(budget(1));assert(job.completed==before+1)
        end
        assert(job.state=="done",job.error)
        for phase in pairs(phases) do
            local other=schema.begin_validate(value)
            while other.state=="running" and other.phase~=phase do other:step_one() end
            assert(other.state=="running");other:cancel("Context changed")
            assert(other.state=="cancelled" and other:get_result()==nil)
        end
        ''')


if __name__ == "__main__":
    unittest.main()
