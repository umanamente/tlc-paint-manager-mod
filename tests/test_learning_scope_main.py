"""Derived-cache invalidation cannot erase an uncertain native-grant guard."""
import unittest

import test_learning_end_to_end as end_to_end


class LearningScopeMainTests(unittest.TestCase):
    def setUp(self):
        end_to_end.LearningEndToEndTests.setUp(self)

    def test_storage_scope_failure_then_same_player_refresh_keeps_uncertain_attempt_blocked(self):
        self.lua.execute('''
        local storage=require("paintmanager.save_store")
        local original_new=storage.new
        storage.new=function(...)
            local selected=original_new(...)
            local original_load=selected.begin_state_load
            selected.begin_state_load=function()
                local job=original_load()
                local original_step=job.step
                function job:step(budget)
                    local progress=original_step(self,budget)
                    if progress.snapshot then
                        progress.snapshot.accountId="76561198000000001"
                        progress.snapshot.path="native-library.json"
                        progress.snapshot.loadGeneration=1
                    end
                    return progress
                end
                return job
            end
            return selected
        end
        local bridge=require("paintmanager.save_bridge")
        storage_ready=true
        bridge.storage_context=function(token)
            assert(inGameThread)
            return {status=storage_ready and "ready" or "not_ready",scopeId=token,
                accountId="76561198000000001",libraryPath="native-library.json",loadGeneration=1,
                error="Temporary storage-scope failure"}
        end
        local lifecycle=require("paintmanager.save_lifecycle")
        lifecycle.consume=function()
            local event=scope_event;scope_event=nil;return event
        end
        -- Admit a completed same-player restoration notice to establish a
        -- preview whose account/scope the actual main pump monitors.
        scope_event={contextToken=ui.model.contextToken,loadGeneration=1}
        finish_work();assert(not ui.open)
        registered.F10();finish_work();assert(ui.open and ui.model)
        local native_token=ui.model.contextToken
        function vault:AddItem(item)
            assert(inGameThread and item==p)
            native_grants=native_grants+1
            error("native outcome unknown")
        end
        local id=ui.model.rows[1].id
        assert(not invoke("learn_recipe",id));finish_work()
        assert(native_grants==1 and ui.model.rows[1].recipeState=="locked")
        storage_ready=false;finish_work()
        assert(not ui.open and ui.model==nil)
        local actual_token=collection.context()
        assert(actual_token==native_token,"fixture changed the native player context")
        storage_ready=true
        registered.F10();finish_work()
        assert(ui.open and ui.model and ui.model.contextToken==native_token)
        assert(ui.model.rows[1].recipeState=="locked")
        local before_reads=inventory_reads
        assert(not invoke("learn_recipe",id))
        assert(native_grants==1 and inventory_reads==before_reads)
        finish_work();assert(native_grants==1)
        ''')


if __name__ == "__main__":
    unittest.main()
