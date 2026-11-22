"""Actual-Lua progress scheduling with deterministic nominal scheduler time."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


class UIProgressTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute('''
        adapter=require("paintmanager.ui_progress").new({ui_progress_delay_ms=200,ui_progress_update_ms=150})
        function running(generation,completed,total,phase)
            return {generation=generation,state="running",kind="stock",phase=phase or "inventory_capture",
                mode=total and "determinate" or "indeterminate",completed=completed,total=total,unit="objects"}
        end
        ''')

    def check(self, source):
        self.lua.execute(source)

    def test_fast_completion_never_flashes(self):
        self.check('''
        assert(adapter:offer(running(1),0)==nil)
        assert(adapter:offer({generation=1,state="done"},100)==nil)
        assert(adapter:pump(300)==nil and adapter:pump(2000)==nil)
        ''')

    def test_delay_flushes_latest_without_another_offer(self):
        self.check('''
        assert(adapter:offer(running(1),0)==nil)
        assert(adapter:offer(running(1,4,12),100)==nil)
        assert(adapter:pump(199)==nil)
        local value=adapter:pump(200)
        assert(value.state=="running" and value.mode=="determinate" and value.completed==4 and value.total==12)
        assert(adapter:pump(201)==nil)
        ''')

    def test_phase_does_not_restart_appearance_delay(self):
        self.check('''
        adapter:offer(running(1,nil,nil,"source_discovery"),0)
        adapter:offer(running(1,2,8),180)
        assert(adapter:pump(200).completed==2)
        ''')

    def test_unknown_to_known_and_outcomes_update_immediately(self):
        self.check('''
        adapter:offer(running(1),0);assert(adapter:pump(200).mode=="indeterminate")
        assert(adapter:offer(running(1,1,15),201).mode=="determinate")
        assert(adapter:offer({generation=1,state="done"},202).state=="done")
        ''')

    def test_count_updates_coalesce_and_flush_at_interval(self):
        self.check('''
        adapter:offer(running(1,0,20),0);adapter:pump(200)
        assert(adapter:offer(running(1,1,20),201)==nil)
        assert(adapter:offer(running(1,4,20),250)==nil)
        assert(adapter:pump(349)==nil)
        assert(adapter:pump(350).completed==4)
        ''')

    def test_unchanged_snapshot_never_rebuilds_and_copies_are_owned(self):
        self.check('''
        local source=running(1,1,20);adapter:offer(source,0)
        source.completed=99
        local shown=adapter:pump(200);assert(shown.completed==1)
        shown.completed=100
        assert(adapter:offer(running(1,1,20),1000)==nil)
        assert(adapter:pump(2000)==nil)
        ''')

    def test_stale_generation_cannot_replace_pending_or_terminal(self):
        self.check('''
        adapter:offer(running(2,3,20),0);adapter:pump(200)
        assert(adapter:offer({generation=1,state="error"},500)==nil)
        assert(adapter:pump(500)==nil and adapter.generation==2)
        ''')

    def test_done_holds_then_clears_without_new_offers(self):
        self.check('''
        adapter:offer(running(1),0);adapter:pump(200)
        assert(adapter:offer({generation=1,state="done"},300).state=="done")
        assert(adapter:offer({generation=1,state="done"},500)==nil)
        assert(adapter:pump(899)==nil)
        local cleared=adapter:pump(900);assert(cleared.state=="idle" and cleared.mode=="idle")
        assert(adapter:pump(1000)==nil)
        ''')

    def test_error_and_partial_persist_without_activity(self):
        self.check('''
        local error=adapter:offer({generation=1,state="error",mode="indeterminate"},0)
        assert(error.state=="error" and error.mode=="idle")
        assert(adapter:pump(10000)==nil)
        adapter:reset()
        local partial=running(2,3,20);partial.coverage="partial"
        local shown=adapter:offer(partial,0)
        assert(shown.state=="error" and shown.coverage=="partial" and shown.mode=="idle")
        assert(adapter:pump(10000)==nil)
        ''')

    def test_new_operation_clears_old_error_then_uses_new_delay(self):
        self.check('''
        adapter:offer({generation=1,state="error"},0)
        assert(adapter:offer(running(2),100).state=="idle")
        assert(adapter:pump(299)==nil and adapter:pump(300).state=="running")
        ''')

    def test_reset_drops_pending_and_allows_new_scope_generation(self):
        self.check('''
        adapter:offer(running(9),0);adapter:reset();assert(adapter:pump(1000)==nil)
        assert(adapter:offer(running(1),1000)==nil)
        assert(adapter:pump(1200).generation==1)
        ''')

    def test_invalid_ratio_never_produces_zero_division_or_guessed_percent(self):
        self.check('''
        adapter:offer(running(1,0,0),0)
        local shown=adapter:pump(200)
        assert(shown.mode=="indeterminate" and shown.completed==nil and shown.total==nil)
        ''')

    def test_nominal_time_rollback_and_invalid_generation_are_bounded(self):
        self.check('''
        assert(adapter:offer(running(-1),0)==nil)
        adapter:offer(running(1,1,8),1000)
        assert(adapter:pump(100)==nil)
        assert(adapter:pump(1200).completed==1)
        assert(adapter:offer(running(1,2,8),1100)==nil)
        assert(adapter:pump(1350).completed==2)
        ''')


if __name__ == "__main__":
    unittest.main()
