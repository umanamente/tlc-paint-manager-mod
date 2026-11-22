"""Configured responsive UMG contracts; these fixtures do not render Slate."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE


HELPERS = r'''
function descendants(node, kind, found, seen)
    found,seen=found or {},seen or {}
    if not node or seen[node] then return found end
    seen[node]=true
    if node.kind==kind then found[#found+1]=node end
    descendants(node.child,kind,found,seen)
    for _,child in ipairs(node.children or {}) do descendants(child,kind,found,seen) end
    return found
end
function assert_grid(columns)
    for index,slot in ipairs(ui.paint_list.slots) do
        assert(slot.row==math.floor((index-1)/columns))
        assert(slot.column==(index-1)%columns)
        assert(slot.horizontal_alignment==1)
        assert(slot.Content.width==slot.Content.height)
    end
end
'''


class UIResponsiveTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PLAYER_FIXTURE)
        self.lua.execute(HELPERS)

    def test_frame_fills_viewport_with_fixed_logical_inset_across_dpi(self):
        cases = ((2560, 1600, 1), (2560, 1600, 1.5), (3440, 1440, 1),
                 (1280, 720, 1.25), (800, 600, 2), (900, 1400, 1))
        for width, height, dpi in cases:
            with self.subTest(width=width, height=height, dpi=dpi):
                self.setUp()
                self.lua.globals().viewport_width = width
                self.lua.globals().viewport_height = height
                self.lua.globals().viewport_dpi = dpi
                self.lua.execute("assert(ui:toggle())")
                fit = self.lua.eval('require("paintmanager.ui").fit_geometry')(width, height, dpi)
                configured = self.lua.eval("configured_frame_rect()")
                available_width, available_height = width / dpi - 24, height / dpi - 24
                scale = min(1, available_width / 900, available_height / 700)
                self.assertAlmostEqual(fit["scale"], scale)
                self.assertAlmostEqual(fit["layoutWidth"], available_width / scale)
                self.assertAlmostEqual(fit["layoutHeight"], available_height / scale)
                self.assertAlmostEqual(self.lua.eval("ui.frame_slot.size.X"), fit["layoutWidth"])
                self.assertAlmostEqual(self.lua.eval("ui.frame_slot.size.Y"), fit["layoutHeight"])
                self.assertAlmostEqual(configured["left"], 12)
                self.assertAlmostEqual(configured["top"], 12)
                self.assertAlmostEqual(configured["width"], available_width)
                self.assertAlmostEqual(configured["height"], available_height)
                self.assertTrue(self.lua.eval("ui.root==ui.host.default_root and ui.root.scale==nil"))
                self.assertEqual(self.lua.eval("event_count('host_position')+event_count('host_size')+event_count('host_anchors')+event_count('host_alignment')"), 0)

    def test_grid_reflows_for_width_and_density_without_stretching_tiles(self):
        self.lua.execute('''
        assert(ui:toggle());render_model(large_model(70,"P"))
        local fit=require("paintmanager.ui").fit_geometry(1920,1080,1)
        assert_grid(math.max(1,math.floor((fit.layoutWidth-96)/116)))
        viewport_width=1280;viewport_height=720;ui:tick(work_budget(0));drain_render()
        fit=require("paintmanager.ui").fit_geometry(1280,720,1)
        assert_grid(math.max(1,math.floor((fit.layoutWidth-96)/116)))
        ui:_change_settings({tileDensity="compact"});drain_render()
        assert_grid(math.max(1,math.floor((fit.layoutWidth-96)/104)))
        ''')

    def test_resize_preserves_browsing_state_and_retires_old_tile_actions(self):
        self.lua.execute('''
        favorite_changes={};assert(ui:toggle());ui.config.max_catalog=512
        render_model(large_model(260,"P"));ui:_page(1);drain_render()
        local selected=ui.visible_tiles[1].row.id
        ui:_select_paint(selected,ui.render_generation);drain_render()
        local stale
        for _,button in ipairs(ui.buttons) do
            if button.kind=="favorite" then stale=button.action;break end
        end
        local generation=ui.render_generation
        viewport_width=1280;viewport_height=720;ui:tick(work_budget(0))
        assert(ui.render_generation>generation and ui:is_rendering())
        assert(ui.page_offset==128 and ui.selected_paint_id==selected and ui.view.query=="")
        stale();assert(#favorite_changes==0)
        drain_render();assert(ui.page_offset==128 and ui.selected_paint_id==selected)
        ui:set_view({query="Paint"});drain_render()
        viewport_width=1600;ui:tick(work_budget(0));drain_render()
        assert(ui.view.query=="Paint" and ui.search_box.text=="Paint")
        assert(#commands==0 and #learned==0 and #favorite_changes==0)
        ''')

    def test_sparse_grid_keeps_occupied_columns_compact_and_left_aligned(self):
        self.lua.execute('''
        open_player()
        assert(#ui.paint_list.slots==2 and ui.paint_grid_box.width==2*116)
        assert(ui.paint_grid_box.slot.horizontal_alignment==1)
        page("missing")
        assert(#ui.paint_list.slots==2 and ui.paint_grid_box.width==2*116)
        ui:_change_settings({tileDensity="compact"});drain_render()
        assert(ui.paint_grid_box.width==2*104)
        ''')

    def test_resize_after_density_change_uses_current_column_count(self):
        self.lua.execute('''
        assert(ui:toggle());render_model(large_model(70,"P"))
        ui:_change_settings({tileDensity="compact"});drain_render()
        local generation=ui.render_generation
        viewport_width=1680;ui:tick(work_budget(0))
        assert(ui.render_generation>generation and ui:is_rendering())
        drain_render()
        local fit=require("paintmanager.ui").fit_geometry(1680,1080,1)
        assert_grid(math.max(1,math.floor((fit.layoutWidth-96)/104)))
        ''')

    def test_same_column_resize_updates_frame_without_restarting_render(self):
        self.lua.execute('''
        assert(ui:toggle());render_model(large_model(40,"P"))
        local generation=ui.render_generation
        local width=ui.frame_slot.size.X
        viewport_width=1919;ui:tick(work_budget(0))
        assert(ui.frame_slot.size.X~=width)
        assert(ui.render_generation==generation and not ui:is_rendering())
        ''')

    def test_narrow_settings_keep_open_help_and_quantity_draft_during_language_switch(self):
        self.lua.execute('''
        local messages=require("paintmanager.i18n")
        ui.callbacks.transfer_paint=function() error("Settings moved paint") end
        viewport_width,viewport_height,viewport_dpi=1280,1600,1.5
        open_player();page("settings")
        local original=ui.callbacks.change_settings
        ui.callbacks.change_settings=function(changes)
            if changes.uiLanguage then messages.select(changes.uiLanguage,"en-US") end
            return original(changes)
        end
        local function press(widget)
            widget.down=true;ui:tick();widget.down=false;ui:tick()
        end
        local function help_for(key)
            for _,record in ipairs(ui.buttons) do
                if record.scope=="settings" and record.label and record.label.text=="?" then
                    for _,label in ipairs(descendants(record.widget.tooltip,"TextBlock")) do
                        if label.text==messages.text(key) then return record.widget end
                    end
                end
            end
            error("Missing Settings help: "..key)
        end
        local quantity_help="ui.settings.help.quantity"
        press(help_for(quantity_help))
        assert(ui.settings_help_text.visible==0 and ui.settings_help_text.text==messages.text(quantity_help))
        ui.quantity_entry:SetText("27")
        click(messages.language_name("ru"))
        assert(messages.language=="ru" and ui.active_tab=="settings")
        assert(ui.quantity_entry:GetText()=="27" and ui.settings_help_text.text==messages.text(quantity_help))
        local box_help="ui.settings.help.depositSimilarAllColors"
        local previous=ui.settings_help_text
        press(help_for(box_help))
        assert(previous.visible~=0 and ui.settings_help_text.visible==0 and ui.settings_help_text.text==messages.text(box_help))
        press(ui.quantity_apply.widget)
        assert(ui.preferences.paintTransferQuantity==27 and ui.quantity_entry:GetText()=="27")
        local check=ui.checkboxes.showDepositAllPaints.widget
        local wanted=not check:IsChecked()
        check:SetIsChecked(wanted);ui:tick()
        assert(ui.preferences.showDepositAllPaints==wanted and setting_changes[#setting_changes].showDepositAllPaints==wanted)
        click(messages.text("ui.settings.language_auto"))
        assert(messages.language=="en" and ui.settings_help_text.text==messages.text(box_help))
        assert(ui.quantity_entry:GetText()=="27" and #commands==0 and #learned==0)
        ''')

    def test_translated_checkbox_labels_wrap_without_overlapping_the_next_setting(self):
        self.lua.execute('''
        local messages=require("paintmanager.i18n")
        messages.select("ru");ui.callbacks.transfer_paint=function() end
        viewport_width,viewport_height,viewport_dpi=1280,1600,1.5
        open_player();page("settings")
        assert(math.abs(ui.panel_width-900)<0.01)
        local bounded_captions={}
        for name,key in pairs({
            showNames="ui.settings.names", showDeveloperTools="ui.settings.developer",
            depositSimilarAllColors="ui.settings.deposit_similar_all_colors",
            showDepositAllPaints="ui.settings.show_deposit_all_paints",
        }) do
            local checkbox=ui.checkboxes[name].widget
            local label=descendants(checkbox,"TextBlock")[1]
            assert(label.text==messages.text(key) and label.wrap, key.." must wrap inside the clickable checkbox")
            local node=label
            local fill_row,cap
            while node and node~=ui.settings_page do
                assert(not node.height, key.." is constrained to a fixed-height row")
                if node.max_width then cap=node end
                local slot=node.slot
                if slot and slot.Parent.kind=="HorizontalBox" and slot.size and slot.size.SizeRule==1 then
                    fill_row=slot.Parent
                end
                node=slot and slot.Parent
            end
            assert(node==ui.settings_page and fill_row, key.." must receive the remaining row width")
            assert(cap and not cap.width and cap.max_width>0 and cap.max_width<ui.panel_width,
                key.." must bound the checkbox content as well as its outer slot")
            bounded_captions[name]={widget=checkbox,cap=cap,narrow=cap.max_width}
            local help=descendants(fill_row,"Button")
            assert(#help==1 and descendants(help[1],"TextBlock")[1].text=="?", key.." must reserve space for its help")
        end
        viewport_width,viewport_height,viewport_dpi=1920,1080,1
        ui:tick(work_budget(0));drain_render()
        for name,c in pairs(bounded_captions) do
            assert(ui.checkboxes[name].widget==c.widget and c.cap.max_width>c.narrow and c.cap.max_width<ui.panel_width,
                name.." did not adapt its native caption bound on resize")
        end
        viewport_width,viewport_height,viewport_dpi=1280,1600,1.5
        ui:tick(work_budget(0));drain_render()
        for name,c in pairs(bounded_captions) do assert(c.cap.max_width==c.narrow, name.." did not narrow again") end
        ''')

    def test_narrow_settings_options_can_flow_instead_of_clipping_russian_labels(self):
        self.lua.execute('''
        local messages=require("paintmanager.i18n")
        messages.select("ru");ui.callbacks.transfer_paint=function() end
        viewport_width,viewport_height,viewport_dpi=900,1400,1
        open_player();page("settings")
        local function options_group(widgets)
            local group
            for _,widget in ipairs(widgets) do
                local node=widget
                while node and node~=ui.settings_page and node.kind~="WrapBox" do
                    assert(not node.width, "fixed control width prevents translated options from growing")
                    node=node.slot and node.slot.Parent
                end
                assert(node and node.kind=="WrapBox", "options must flow in the available Settings width")
                assert(not group or group==node, "the options for one setting must stay in one group")
                group=node
            end
        end
        for _,key in ipairs({"ui.settings.language","ui.settings.ship_radius","ui.settings.backpack_target"}) do
            local found
            for _,label in ipairs(descendants(ui.settings_page,"TextBlock")) do
                if label.text==messages.text(key) then found=label;break end
            end
            assert(found and found.wrap, key.." must allow a second line")
        end
        options_group({ui.language_choices.auto,ui.language_choices.en,ui.language_choices.ru})
        local radii={}
        for _,distance in ipairs({3500,4000,4500,5000}) do radii[#radii+1]=ui.ship_radius_controls[distance].widget end
        options_group(radii)
        options_group({ui.quantity_controls[1].widget,ui.quantity_controls[5].widget,ui.quantity_controls.all.widget,
            ui.quantity_entry,ui.quantity_apply.widget})
        local entry=ui.quantity_entry
        assert(entry.hint==messages.text("ui.settings.custom_cans"))
        while entry and entry.kind~="WrapBox" and not entry.min_width do entry=entry.slot and entry.slot.Parent end
        assert(entry and entry.min_width and entry.min_width>112, "custom quantity needs room for its translated hint")
        ''')

    def test_button_text_uses_smaller_font_and_downward_only_fit(self):
        self.lua.execute('''
        assert(ui:toggle());render_model(player_model())
        for _,record in ipairs(ui.buttons) do
            if record.kind~="paint_select" and record.kind~="favorite" then
                assert(record.label.Font.Size==14)
                local fits=descendants(record.widget,"ScaleBox")
                assert(#fits>=1)
                local found=false
                for _,box in ipairs(fits) do
                    if box.stretch==2 and box.stretch_direction==1 then found=true end
                end
                assert(found)
                local padding=record.widget.child.Slot.padding
                assert(padding.Left==8 and padding.Right==8 and padding.Top==2 and padding.Bottom==2)
                for _,slot in ipairs(record.widget.child.slots) do
                    assert(slot.padding.Top==0 and slot.padding.Bottom==0)
                end
            end
        end
        assert(ui.search_box.WidgetStyle.TextStyle.Font.Size==14)
        ''')

    def test_favorite_has_no_button_brush_and_occupies_upper_right(self):
        self.lua.execute('''
        open_player()
        for _,entry in ipairs(ui.visible_tiles) do
            local star=entry.star
            local style=star.WidgetStyle
            assert(style.Normal.DrawAs==0 and style.Hovered.DrawAs==0 and style.Pressed.DrawAs==0 and style.Disabled.DrawAs==0)
            for _,padding in ipairs({style.NormalPadding,style.PressedPadding,star.child.Slot.padding}) do
                assert(padding.Left==0 and padding.Right==0 and padding.Top==0 and padding.Bottom==0)
            end
            assert(#descendants(star,"HorizontalBox")==0)
            local slot=star.slot
            assert(slot.size.X==32 and slot.size.Y==32 and slot.position.Y<=5)
            assert(slot.position.X>slot.Parent.children[1].slot.size.X/2)
            local canvas=slot.Parent
            for _,other in ipairs(canvas.slots) do
                if other~=slot and other.position and other.position.Y<=5 and other.size.X==18 then
                    assert(other.position.X+other.size.X<=slot.position.X)
                end
            end
        end
        ''')


if __name__ == "__main__":
    unittest.main()
