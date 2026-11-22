"""Real UMG-module preview and split input/render scheduling regressions."""
import unittest

from lupa.lua54 import LuaRuntime
from test_ui import FIXTURE, SCRIPTS


class CacheUiTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def test_game_names_follow_actual_language_in_bounded_reads_without_changing_stock(self):
        self.lua.execute('''
        local languages=require("paintmanager.languages")
        local i18n=require("paintmanager.i18n")
        assert(languages.observe_game("en-US"))
        assert(not languages.observe_game("en_US"))
        assert(ui:toggle())
        local source=large_model(4,"English")
        source.paintNameRevision=languages.game_revision
        local old_name=source.rows[1].displayName
        ui:set_model(source)
        local reads=0
        require("paintmanager.collection").loaded_paint_icon=function()
            reads=reads+1
            return nil,reads==1 and "Native translated name" or nil
        end
        assert(languages.observe_game("ru"))
        assert(i18n.language=="en")
        ui:render_tick(work_budget(1))
        assert(reads==1 and ui.model==source)
        for _=1,10 do ui:render_tick(work_budget(1)) end
        assert(reads==4 and ui.model~=source and ui.capture_source==source)
        assert(ui.model.rows[1].displayName=="Native translated name" and ui.model.rows[2].displayName==nil)
        assert(source.rows[1].displayName==old_name and ui.model.rows[1].count==source.rows[1].count)
        assert(not languages.observe_game(nil))
        ''')

    def test_input_poll_does_not_consume_render_work_and_close_preempts(self):
        self.lua.execute('''
        assert(ui:toggle());ui:set_model(large_model(20,"Warm"))
        local job=ui.render_job;local budget=work_budget(8)
        ui:tick(budget,true)
        assert(ui.render_job==job and job.phase=="clear" and budget.units==0)
        ui:render_tick(budget)
        assert(job.phase~="clear" and budget.units>0 and budget.units<=8)
        for _,button in ipairs(ui.buttons) do
            if button.kind=="close" then button.widget.down=true end
        end
        -- Native input loss must also close before queued render work.
        pc.bShowMouseCursor=false;ui:tick(work_budget(8),true)
        assert(not ui:is_open() and ui.render_job==nil)
        ui:render_tick(work_budget(8));assert(not ui:is_open())
        ''')

    def test_preview_shows_unknown_stock_and_never_offers_learning(self):
        self.lua.execute('''
        assert(ui:toggle())
        render_model({status="preparing",cachePreview=true,stockComplete=false,complete=false,
            readiness={verifiedRows=1,pendingRows=1},sources={},rows={
                {id="learned",assetType="Item",assetName="DA_Learned",displayName="Learned early",count=0,
                    recipeState="unlocked",complete=false},
                {id="locked",assetName="DA_A",displayName="Checked locked paint",count=0,
                    recipeState="locked",complete=true},
                {id="pending",assetName="DA_B",count=0,recipeState="unknown",complete=false}}})
        assert(#ui.visible_tiles==1 and ui.visible_tiles[1].row.id=="learned")
        assert(ui.visible_tiles[1].count.text=="?" and not ui.visible_tiles[1].absent)
        ui.checking_expanded=true;ui:_select_tab("missing");drain_render()
        assert(#ui.visible_tiles==2 and ui.checking_button.visible==0)
        for _,tile in ipairs(ui.visible_tiles) do
            assert(tile.count.text=="?" and not tile.absent)
        end
        assert(#learn_buttons()==0 and bulk_button().widget.enabled==false)
        ''')


if __name__ == "__main__":
    unittest.main()
