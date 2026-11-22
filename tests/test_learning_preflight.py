"""Lean learning preparation keeps fresh native proof and scheduling boundaries."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE
from test_learn_recipe import LEARN_FIXTURE


PREPARATION_FIXTURE = r'''
bulk=require("paintmanager.learn_all")
function cached_request()
    cached=collection.scan(config)
    local row=assert(cached.rows[1])
    request={itemPath=row.id,runtimeRef=row.runtimeRef,contextToken=cached.contextToken,
        radiusCm=cached.radiusCm,displayName=row.displayName,gameBuild=config.reference_build}
    return request
end
function new_cached_job(kind)
    if kind=="single" then job=learning.begin(config,request,nil,{catalog=cached})
    else job=bulk.begin(config,request,nil,{catalog=cached}) end
    return job
end
function finish_job()
    for _=1,10000 do
        result=job:step(budget_module.new(10000,1))
        if result.state~="running" then return result end
    end
    error("Learning did not finish")
end
'''


class LearningPreflightTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + JOB_FIXTURE + LEARN_FIXTURE + PREPARATION_FIXTURE)

    def test_both_factories_pass_lean_catalog_hints_without_native_work(self):
        self.lua.execute('''
        cached_request()
        local begin=collection.begin
        local calls=0
        function collection.begin(c,execution)
            calls=calls+1
            assert(c==config and execution.catalog==cached and execution.presentation=="paints")
            return begin(c,execution)
        end
        local before=scans+queries+inventory_reads+context_reads
        new_cached_job("single");assert(job:is_preparing())
        new_cached_job("bulk");assert(job:is_preparing())
        assert(calls==2 and scans+queries+inventory_reads+context_reads==before and nativeCalls==0)
        ''')

    def test_preparation_queries_are_pure_and_stop_before_native_recipe_stages(self):
        self.lua.execute('''
        cached_request();new_cached_job("single")
        local before=scans+queries+inventory_reads+context_reads
        for _,phase in ipairs({"preflight","learn_prepare"}) do
            job.phase=phase;assert(job:is_preparing())
        end
        for _,phase in ipairs({"learn_commit","done","cancelled"}) do
            job.phase=phase;assert(not job:is_preparing())
        end
        new_cached_job("bulk")
        for _,phase in ipairs({"preflight","bulk_prepare"}) do
            job.phase=phase;assert(job:is_preparing())
        end
        for _,phase in ipairs({"bulk_start_recipe","bulk_recipe","done","cancelled"}) do
            job.phase=phase;assert(not job:is_preparing())
        end
        job.phase="preflight";job.state="cancelled";assert(not job:is_preparing())
        assert(scans+queries+inventory_reads+context_reads==before and nativeCalls==0)
        ''')

    def test_both_jobs_forward_deferred_preflight_presentation(self):
        for kind in ("single", "bulk"):
            with self.subTest(kind=kind):
                self.setUp()
                self.lua.globals().kind = kind
                self.lua.execute('''
                cached_request();new_cached_job(kind)
                local waiting=true
                local presentation={kind="stock",phase="container_read",mode="determinate",
                    completed=3,total=8,unit="objects"}
                function job.collector:step(budget)
                    budget:consume()
                    return {state="running",phase="container_read",processed=50,total=200,
                        presentation=presentation,defer=waiting}
                end
                local progress=job:step(budget_module.new(10000,1))
                assert(progress.defer and progress.presentation==presentation)
                assert(progress.phase=="container_read" and progress.presentation.completed==3)
                waiting=false;progress=job:step(budget_module.new(10000,1))
                assert(not progress.defer and progress.presentation==presentation and nativeCalls==0)
                ''')

    def test_lean_catalog_does_not_reuse_cached_stock(self):
        self.lua.execute('''
        cached_request();assert(cached.rows[1].count==4)
        stockRecords[1].Data.ItemCount=3
        local reads=inventory_reads
        new_cached_job("single");result=finish_job()
        assert(job:get_receipt().outcome=="rejected" and not job:get_receipt().attempted)
        assert(job.model.rows[1].count==3 and inventory_reads>reads and nativeCalls==0)
        assert(job.model.presentation=="paints" and job.model.stockSourcesOmitted and #job.model.sources==0)
        assert(#job.model.rows[1].stockWitnesses>0)
        assert(cached.rows[1].count==4)
        ''')

    def test_lean_catalog_rechecks_current_recipe_membership(self):
        self.lua.execute('''
        cached_request();assert(cached.rows[1].recipeState=="locked")
        vault.members[p.path]=true
        local reads=queries
        new_cached_job("single");result=finish_job()
        assert(job:get_receipt().outcome=="already_known" and not job:get_receipt().attempted)
        assert(queries>reads and nativeCalls==0 and cached.rows[1].recipeState=="locked")
        ''')

    def test_final_witness_read_is_fresh_after_lean_preparation(self):
        self.lua.execute('''
        cached_request();new_cached_job("single")
        for _=1,10000 do
            if not job:is_preparing() then break end
            assert(job.state=="running");job:step(budget_module.new(10000,1))
        end
        assert(job.phase=="learn_commit" and job.model.rows[1].count==4 and nativeCalls==0)
        local reads=inventory_reads
        stockRecords[1].Data.ItemCount=3
        result=finish_job()
        assert(job:get_receipt().outcome=="rejected" and not job:get_receipt().attempted)
        assert(inventory_reads>reads and nativeCalls==0)
        ''')

    def test_bulk_preparation_boundary_does_not_start_a_recipe(self):
        self.lua.execute('''
        cached_request();new_cached_job("bulk")
        for _=1,10000 do
            if not job:is_preparing() then break end
            assert(job.state=="running");job:step(budget_module.new(10000,1))
        end
        assert(job.phase=="bulk_start_recipe" and job.child==nil and nativeCalls==0)
        assert(job.model.presentation=="paints" and #job.model.rows[1].stockWitnesses>0)
        stockRecords[1].Data.ItemCount=3
        result=finish_job()
        assert(job:get_receipt().counts.rejected==1 and not job:get_receipt().attempted)
        assert(nativeCalls==0)
        ''')

    def test_bulk_selection_uses_fresh_stock_instead_of_cached_threshold(self):
        self.lua.execute('''
        cached_request();stockRecords[1].Data.ItemCount=3
        local reads=inventory_reads
        new_cached_job("bulk");result=finish_job()
        assert(job:get_receipt().outcome=="none_available" and job:get_receipt().counts.eligible==0)
        assert(inventory_reads>reads and nativeCalls==0 and job.model.rows[1].count==3)
        assert(job.model.presentation=="paints" and not job:is_preparing())
        ''')


if __name__ == "__main__":
    unittest.main()
