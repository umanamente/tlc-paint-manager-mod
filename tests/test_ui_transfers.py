"""Backpack transfer controls through the normal paint window input loop."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE


class UITransferTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PLAYER_FIXTURE)
        self.lua.execute('''
        transfers={}
        ui.callbacks.transfer_paint=function(command,id)
            transfers[#transfers+1]={command,id}
            return true
        end
        assert(ui:toggle());render_model(player_model({contextToken="world-A"}))
        function inventories(backpack,returnable)
            ui:set_transfer_state({contextToken="world-A", backpack=backpack, returnable=returnable,
                canGatherAll=true, canReturnAll=next(returnable)~=nil})
        end
        function control(command)
            for _,button in ipairs(ui.transfer_controls) do
                if button.transfer==command and not button.signal then return button end
            end
        end
        function press(button)
            button.widget.down=true;ui:tick(work_budget(0),true)
            button.widget.down=false;ui:tick(work_budget(0),true)
        end
        ''')

    def test_status_renders_translated_reasons_and_keeps_diagnostics_out_of_widgets(self):
        self.lua.execute('''
        local messages = require("paintmanager.i18n")
        local player = require("paintmanager.player_messages")
        local control = require("paintmanager.transfer_controls")
        local diagnostic = "private/path/paint_transfer.lua:123: Destination has no empty slot"
        local key = player.reason_key(diagnostic)
        assert(key == "main.reason.destination_full")
        assert(player.reason_key("Your backpack needs an empty slot for the removed paint") == "main.reason.backpack_full")
        local status = {command="unlisted_internal_command", state="error", message=diagnostic, reasonKey=key}
        for _, code in ipairs({"en", "ru"}) do
            messages.select(code)
            ui:set_status(status)
            local line = ui.status_label.text
            assert(line:find(messages.text("ui.command.task"), 1, true))
            assert(line:find(messages.text(key), 1, true))
            assert(not line:find("unlisted_internal_command", 1, true) and not line:find("private/path", 1, true))
            assert(player.outcome("applied_verified") == messages.text("main.outcome.applied"))
            local result = control.result({moved=1, skipped=2, reasonKey=key})
            assert(result:find(messages.text(key), 1, true))
            assert(ui.status.message == diagnostic)
        end
        ''')

    def test_selection_routes_gather_and_return_without_learning_or_repeating(self):
        self.lua.execute('''
        inventories({},{});page("ready");ui:_select_paint("blue",ui.render_generation)
        local gather,back=control("gather_paint"),control("return_paint")
        assert(gather.enabled and gather.shown and gather.box.visible==0)
        assert(gather.label.text=="Top up to 5" and gather.tooltip_label.text:find("have 5",1,true))
        assert(ui.detail_learn_control.widget.enabled and ui.detail_learn_box.visible==0)
        assert(not back.shown and ui.detail_backpack.text=="Backpack: 0")
        gather.widget.down=true;ui:tick(work_budget(0),true);ui:tick(work_budget(0),true)
        assert(#transfers==1 and transfers[1][1]=="gather_paint" and transfers[1][2]=="blue")
        assert(ui.learning and not gather.enabled and not ui.detail_learn_control.widget.enabled and #learned==0)
        gather.widget.down=false;ui:tick(work_budget(0),true)
        inventories({blue=2},{blue=1});ui:set_learning(false)
        assert(gather.enabled and gather.shown and back.enabled and back.shown and ui.detail_backpack.text=="Backpack: 2")
        press(back);assert(#transfers==2 and transfers[2][1]=="return_paint" and transfers[2][2]=="blue")
        ui:set_learning(false);inventories({blue=4},{})
        assert(not back.shown and gather.enabled and gather.shown and ui.detail_backpack.text=="Backpack: 4")
        press(gather);assert(#transfers==3 and transfers[3][1]=="gather_paint" and transfers[3][2]=="blue")
        inventories({blue=5},{blue=3});ui:set_learning(false)
        assert(not gather.shown and not gather.enabled and back.enabled)
        press(gather);assert(#transfers==3)
        ''')

    def test_click_preserves_the_status_of_a_synchronously_finished_transfer(self):
        self.lua.execute('''
        inventories({},{});page("ready");ui:_select_paint("blue",ui.render_generation)
        ui.callbacks.transfer_paint=function(command)
            ui:set_learning(false)
            ui:set_status({command=command,state="done",message="5 cans moved"})
            return true,nil,true
        end
        press(control("gather_paint"))
        assert(ui.status.state=="done" and ui.status.message=="5 cans moved" and not ui.learning)
        ''')

    def test_settings_change_target_and_preserve_custom_draft_on_tab_switch(self):
        self.lua.execute('''
        inventories({blue=5},{blue=1});page("ready");ui:_select_paint("blue",ui.render_generation)
        local gather,back=control("gather_paint"),control("return_paint")
        local stale=gather.action
        page("settings");press(ui.quantity_controls.all)
        assert(ui.preferences.paintTransferQuantity=="all" and #transfers==0)
        page("ready");assert(gather.shown and gather.enabled and back.enabled)
        assert(gather.label.text=="Take all of this color" and gather.tooltip_label.text:find("this color",1,true))
        stale();assert(#transfers==0)
        press(gather);assert(#transfers==1 and transfers[1][1]=="gather_paint" and transfers[1][2]=="blue")
        ui:set_learning(false);page("settings");press(ui.quantity_controls[1])
        assert(ui.preferences.paintTransferQuantity==1)
        ui.quantity_entry.text="12"
        page("learned");page("settings");assert(ui.quantity_entry.text=="12" and ui.preferences.paintTransferQuantity==1)
        press(ui.quantity_apply);assert(ui.preferences.paintTransferQuantity==12 and ui.quantity_entry.text=="12")
        assert(setting_changes[#setting_changes].paintTransferQuantity==12 and #transfers==1)
        page("ready");assert(gather.enabled and gather.label.text=="Top up to 12" and back.enabled)
        local before=#setting_changes
        ui.quantity_controls[1].action();assert(#setting_changes==before and ui.preferences.paintTransferQuantity==12)
        local old_apply=ui.quantity_apply.action
        ui:close("user");assert(ui:toggle());page("settings");assert(ui.quantity_entry.text=="12")
        ui.quantity_entry.text="6";old_apply();assert(ui.preferences.paintTransferQuantity==12)
        press(ui.quantity_controls[5]);assert(ui.preferences.paintTransferQuantity==5 and ui.quantity_entry.text=="")
        ''')

    def test_box_settings_checkbox_defaults_and_independent_edits(self):
        self.lua.execute('''
        page("settings")
        local stock=ui.checkboxes.depositSimilarAllColors.widget
        local separate=ui.checkboxes.showDepositAllPaints.widget
        assert(stock:IsChecked() and not separate:IsChecked())
        stock:SetIsChecked(false);ui:tick(work_budget(8))
        assert(setting_changes[#setting_changes].depositSimilarAllColors==false and not separate:IsChecked())
        separate:SetIsChecked(true);ui:tick(work_budget(8))
        assert(setting_changes[#setting_changes].showDepositAllPaints and not stock:IsChecked())
        ui:close("user");assert(ui:toggle());page("settings")
        assert(not ui.checkboxes.depositSimilarAllColors.widget:IsChecked())
        assert(ui.checkboxes.showDepositAllPaints.widget:IsChecked() and #transfers==0)
        ''')

    def test_invalid_custom_text_or_failed_save_cannot_change_target(self):
        self.lua.execute('''
        page("settings")
        local before=#setting_changes
        for _,input in ipairs({"", "0", "-4", "3.5", "1e2", "all", "2147483648"}) do
            ui.quantity_entry.text=input;press(ui.quantity_apply)
            assert(ui.status.state=="error" and ui.status.message:find("2147483647",1,true))
            assert(ui.preferences.paintTransferQuantity==5 and #setting_changes==before)
        end
        ui.quantity_entry.text="  2147483647 ";press(ui.quantity_apply)
        assert(ui.preferences.paintTransferQuantity==2147483647)
        ui.callbacks.change_settings=function() return false,"Save unavailable" end
        press(ui.quantity_controls.all)
        assert(ui.status.message=="Save unavailable" and ui.preferences.paintTransferQuantity==2147483647)
        ''')

    def test_three_actions_share_card_at_small_viewport(self):
        self.lua.execute('''
        viewport_width,viewport_height,viewport_dpi=1280,720,1.5
        ui:tick(work_budget(8));drain_render()
        inventories({blue=2},{blue=1});page("ready");ui:_select_paint("blue",ui.render_generation)
        local gather,back=control("gather_paint"),control("return_paint")
        local row=gather.box.Slot.Parent
        assert(gather.enabled and back.enabled and ui.detail_learn_control.widget.enabled)
        assert(row.kind=="HorizontalBox" and row==back.box.Slot.Parent)
        assert(gather.box.Slot.size.Value==1 and back.box.Slot.size.Value==1)
        assert(ui.detail_learn_box.Slot.Parent==row.Slot.Parent and ui.detail_learn_box.Slot.size.SizeRule==1)
        assert(ui.detail_preview.height-math.max(gather.box.height,back.box.height)>=56)
        assert(ui.detail_actions_box.width==ui.detail_learn_box.width)
        ''')

    def test_all_paint_tabs_offer_selected_action_and_obsolete_bindings_do_nothing(self):
        self.lua.execute('''
        inventories({},{});local single=control("gather_paint")
        ui:_select_paint("amber",ui.render_generation)
        assert(single.item_id=="amber" and single.enabled and ui.detail_learn_box.visible==1)
        local old=single.action
        ui:_select_paint("zero",ui.render_generation)
        assert(single.item_id=="zero" and single.shown and not single.enabled)
        ui:_select_paint("amber",ui.render_generation);old();assert(#transfers==0)
        page("missing");ui:_select_paint("delta",ui.render_generation)
        assert(single.item_id=="delta" and single.enabled)
        local old_delta=single.action;page("settings");old_delta();assert(#transfers==0 and not single.shown)
        page("missing")
        ui:set_transfer_state({contextToken="world-A",unavailable="Backpack is unavailable"})
        assert(not single.shown and not control("gather_all").enabled and not control("return_all").enabled)
        assert(control("gather_all").tooltip_label.text=="Backpack is unavailable")
        assert(ui.detail_backpack.text=="Backpack is unavailable");old_delta();assert(#transfers==0)
        inventories({},{});assert(control("gather_all").tooltip_label.text:find("including duplicate colors",1,true))
        ui:set_model(nil);single.action();assert(#transfers==0)
        ''')

    def test_bulk_commands_ignore_filter_and_return_without_ship_contact(self):
        self.lua.execute('''
        inventories({blue=2},{blue=1});page("ready")
        ui:set_view({query="no matches"});drain_render()
        local take,back=control("gather_all"),control("return_all")
        assert(paint_names()=="" and take.enabled and back.enabled)
        press(take);assert(#transfers==1 and transfers[1][1]=="gather_all" and transfers[1][2]==nil)
        press(back);assert(#transfers==1)
        ui:set_learning(false)
        ui:set_connection({connected=false});ui:set_model(nil)
        ui:tick(work_budget(0),true)
        assert(not take.shown and not back.shown and ui.signal_return.shown and ui.signal_return.enabled)
        press(ui.signal_return);assert(#transfers==2 and transfers[2][1]=="return_all" and transfers[2][2]==nil)
        ui:set_learning(false);inventories({},{})
        ui:set_status({command="return_all",state="done",message="Returned 4 cans"})
        ui:set_status({command="refresh",state="running",message="Updating stock"})
        assert(not ui.signal_return.shown and ui.signal_transfer_status.visible==0)
        assert(ui.signal_transfer_status.text:find("Returned 4 cans",1,true))
        ui:set_connection({connected=true})
        assert(ui.transfer_result_label.visible==0 and ui.transfer_result_label.text:find("Returned 4 cans",1,true))
        ui:set_transfer_state(nil);assert(ui.transfer_result_label.visible==1)
        ''')

    def test_busy_and_rejected_transfer_preserve_inventory_display(self):
        self.lua.execute('''
        page("learned");inventories({},{});ui:_select_paint("amber",ui.render_generation)
        local single=control("gather_paint")
        ui.transfer_state.busy=true;ui:set_transfer_state(ui.transfer_state)
        assert(single.shown and not single.enabled and not control("gather_all").enabled)
        press(single);assert(#transfers==0)
        inventories({},{})
        ui.callbacks.transfer_paint=function() return false,"Container no longer loaded" end
        press(single)
        assert(not ui.learning and single.enabled and ui.detail_backpack.text=="Backpack: 0")
        assert(ui.status.state=="error" and ui.status_label.text:find("Container no longer loaded",1,true))
        ''')
