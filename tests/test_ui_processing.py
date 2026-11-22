"""Actual-Lua developer editor, demanded rendering and preparation controls."""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_icon_cache import FIXTURE as ICON_FIXTURE
from test_ui_resources import FIXTURE as RESOURCE_FIXTURE


EDITOR_FIXTURE = r'''
processing=require("paintmanager.processing")
applied,processing_actions={},{}
effective=processing.snapshot()
ui.callbacks.processing_snapshot=function()
    return {effective=effective,requested=requested,pending=pending_policy==true,paused=paused_preparation==true,
        generation=7,backend={backend="lua_cooperative"},metrics={callbacks=12}}
end
ui.callbacks.processing_descriptors=function() return processing.descriptors() end
ui.callbacks.processing_apply=function(changes)
    applied[#applied+1]=changes;requested=changes;pending_policy=true;return true,"Staged"
end
ui.callbacks.processing_action=function(action,args)
    processing_actions[#processing_actions+1]={action=action,args=args}
    if action=="profile_import" then
        local imported=processing.defaults();imported.work_units_per_tick=17
        return true,"Imported draft",imported
    end
    if action=="pause" then paused_preparation=true end
    if action=="resume" then paused_preparation=false end
    return true,"Done"
end
debug_now=os.clock()
os.clock=function() return debug_now end
function debug_open()
    assert(ui:toggle());click("Settings");ui.checkboxes.showDeveloperTools.widget:SetIsChecked(true)
    ui:tick(work_budget(8));click("Debug");assert(ui.active_tab=="debug")
end
function editor_tick()
    debug_now=debug_now+0.2
    ui:tick(work_budget(8),true)
end
-- Follow Slate-facing UMG parent slots and switcher indices. Hidden cached
-- groups and sections can retain records with identical labels.
function debug_visible(widget)
    local node=widget
    while node do
        if node.visible==1 or node.visible==2 then return false end
        if node==ui.root then return true end
        local parent=node.Slot and node.Slot.Parent
        if parent and parent.kind=="WidgetSwitcher" and parent.children[(parent.active or 0)+1]~=node then return false end
        node=parent
    end
    return false
end
function debug_button(label)
    for _,button in ipairs(ui.buttons) do
        if button.scope=="debug" and button.label and button.label.text==label and debug_visible(button.widget) then
            return button
        end
    end
end
function press_debug(record)
    assert(record and debug_visible(record.widget),"button is hidden")
    record.widget.down=true;editor_tick()
    record.widget.down=false;editor_tick()
end
function debug_click(label)
    press_debug(assert(debug_button(label),label.." is missing"))
end
function button_sizing(record)
    local box=record.widget.Slot.Parent
    assert(box.kind=="SizeBox" and box.width and box.height,"missing bounded native button box")
    local node=box
    while node and node~=ui.debug_page do
        local slot=node.Slot
        local parent=slot.Parent
        if slot.horizontal_alignment==1 or
            parent.kind=="HorizontalBox" and (not slot.size or slot.size.SizeRule~=1) or
            parent.kind=="SizeBox" and parent.width and parent.width<=245 then return box end
        node=parent
    end
    error("native slot stretches through the available width")
end
function policy(values)
    assert(processing.request(values));assert(processing.commit())
end
'''


class UIProcessingTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(EDITOR_FIXTURE)

    def test_every_debug_setting_explains_its_effect_on_hover_and_click(self):
        self.lua.execute('''
        debug_open()
        local t=require("paintmanager.i18n").text
        for _,d in ipairs(ui.processing_descriptors) do
            if ui.processing_group~=d.group then debug_click(t("ui.processing.group."..d.group)) end
            local help
            for _,record in ipairs(ui.buttons) do
                if record.label and record.label.text=="?" and debug_visible(record.widget) and record.tooltip_label
                    and record.tooltip_label.text:find(t(d.help_key),1,true) then
                    help=record;break
                end
            end
            assert(help and help.widget.tooltip,d.key)
            button_sizing(help)
            local field=ui.processing_fields[d.key]
            if not debug_visible(field.help_label) then press_debug(help) end
            assert(debug_visible(field.help_label) and field.help_label.text:find("Default",1,true),d.key)
            assert(field.help_label.text:find(t(d.help_key),1,true),d.key)
            press_debug(help);assert(not debug_visible(field.help_label),d.key)
        end
        ''')

    def test_editor_is_gated_and_groups_are_built_lazily(self):
        self.lua.execute('''
        assert(ui:toggle());assert(not ui:_select_tab("debug") and not ui.processing_built)
        ui:_change_settings({showDeveloperTools=true});assert(ui:_select_tab("debug"))
        assert(ui.processing_built and ui.debug_section=="processing" and ui.processing_group=="scheduler" and #applied==0)
        assert(ui.processing_fields.work_budget_ms and not ui.processing_fields.job_steps_per_unit)
        assert(ui.processing_group_hosts.scheduler.widget.visible==0)
        local original=ui.processing_fields.work_budget_ms.entry
        debug_click("Pure work batches")
        assert(ui.processing_fields.job_steps_per_unit and not debug_visible(original))
        debug_click("Scheduler limits")
        assert(ui.processing_fields.work_budget_ms.entry==original and debug_visible(original))
        assert(ui.processing_fields.native_task_steps==nil and ui.processing_group_hosts.native==nil)
        assert(#commands==0 and #learned==0)
        ''')

    def test_dropdowns_fit_native_content_and_keep_readable_open_and_closed_styles(self):
        enum, quota = self.lua.execute('''
        debug_open();debug_click("Measurements and logging")
        local enum=ui.processing_fields.metrics_level.choice
        ui:_processing_select_group("quotas")
        return enum,ui.processing_fields.foreground_units.mode
        ''')

        def luminance_of(color, expected_rgb):
            self.assertEqual(color.ColorUseRule, 0)
            rgba = color.SpecifiedColor
            self.assertEqual(rgba.A, 1)
            for channel, expected in zip("RGB", bytes.fromhex(expected_rgb)):
                srgb = expected / 255
                linear = srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4
                self.assertAlmostEqual(rgba[channel], linear, delta=0.0001)
            return 0.2126 * rgba.R + 0.7152 * rgba.G + 0.0722 * rgba.B

        for kind, combo in (("enum", enum), ("quota", quota)):
            with self.subTest(kind=kind):
                box = combo.Slot.Parent
                self.assertEqual((box.kind, box.min_width, box.height), ("SizeBox", 144, 28))
                self.assertIsNone(box.width)
                luminance_of(combo.ForegroundColor, "000000")
                style = combo.ItemStyle
                self.assertEqual(style.SelectorFocusedBrush.DrawAs, 2)
                self.assertEqual(style.SelectorFocusedBrush.TintColor.ColorUseRule, 1)
                text = luminance_of(style.TextColor, "F2F4F6")
                selected_text = luminance_of(style.SelectedTextColor, "FFFFFF")
                for prefix in ("EvenRowBackground", "OddRowBackground", "Active", "Inactive"):
                    selected = prefix in ("Active", "Inactive")
                    for suffix, unselected_rgb in (("Brush", "202428"), ("HoveredBrush", "3A4652")):
                        name = prefix + suffix
                        with self.subTest(brush=name):
                            brush = style[name]
                            self.assertEqual(brush.DrawAs, 3)
                            bg = luminance_of(brush.TintColor, "145A91" if selected else unselected_rgb)
                            fg = selected_text if selected else text
                            self.assertGreaterEqual((fg + 0.05) / (bg + 0.05), 4.5)

    def test_registry_fields_and_profiles_have_stable_english_catalogs(self):
        self.lua.execute('''
        local catalog=require("paintmanager.i18n").english_catalog()
        for _,descriptor in ipairs(processing.descriptors()) do
            assert(catalog[descriptor.label_key],descriptor.label_key)
            assert(catalog["ui.processing.group."..descriptor.group],descriptor.group)
        end
        for _,profile in ipairs(processing.profiles()) do assert(catalog[profile.label_key],profile.label_key) end
        ''')

    def test_typed_numbers_stage_then_apply_without_changing_effective_policy(self):
        self.lua.execute('''
        debug_open();ui:_processing_select_group("scheduler")
        ui.processing_fields.work_budget_ms.entry.text="3.75"
        ui.processing_fields.work_units_per_tick.entry.text="19"
        assert(ui:_processing_apply() and #applied==1)
        assert(applied[1].work_budget_ms==3.75 and applied[1].work_units_per_tick==19)
        assert(processing.get("work_budget_ms")==2 and effective.work_units_per_tick==8)
        ui:processing_changed();assert(pending_policy and not ui.debug_apply.widget.enabled)
        ''')

    def test_typed_edits_survive_boolean_changes_and_group_navigation(self):
        self.lua.execute('''
        debug_open();ui:_processing_select_group("scheduler")
        ui.processing_fields.work_budget_ms.entry.text="3.75"
        local limit=ui.processing_fields.time_limit_enabled.checkbox
        assert(limit.kind=="CheckBox");limit:SetIsChecked(false);editor_tick()
        assert(ui.processing_draft.work_budget_ms==3.75 and ui.processing_fields.work_budget_ms.entry.text=="3.75")
        ui.processing_fields.work_budget_ms.entry.text="4.25"
        debug_click("Native UI and images");debug_click("Scheduler limits")
        assert(ui.processing_draft.work_budget_ms==4.25 and ui.processing_fields.work_budget_ms.entry.text=="4.25")
        debug_click("Apply");assert(applied[1].work_budget_ms==4.25 and applied[1].time_limit_enabled==false)
        ''')

    def test_old_import_updates_closed_limit_and_retains_typed_open_limit(self):
        self.lua.execute('''
        debug_open();ui:_processing_select_group("scheduler")
        ui.processing_fields.open_work_budget_ms.entry.text="11.25"
        ui.callbacks.processing_action=function()
            return true,"Imported",assert(processing.import_profile({work_budget_ms=3.5},2))
        end
        debug_click("Profiles");debug_click("Import as draft");assert(#applied==0);debug_click("Processing")
        assert(ui.processing_fields.work_budget_ms.entry.text=="3.5")
        assert(ui.processing_fields.open_work_budget_ms.entry.text=="11.25")
        assert(ui:_processing_apply() and applied[1].open_work_budget_ms==11.25 and applied[1].work_budget_ms==3.5)
        ''')

    def test_invalid_and_nonfinite_input_never_reaches_controller(self):
        self.lua.execute('''
        debug_open();ui:_processing_select_group("scheduler")
        for _,bad in ipairs({"nan","inf","-1","0","1e500","1.5"}) do
            ui.processing_fields.work_units_per_tick.entry.text=bad
            assert(not ui:_processing_apply() and #applied==0)
        end
        ui.processing_fields.work_units_per_tick.entry.text="12"
        assert(ui:_processing_apply() and #applied==1)
        ''')

    def test_invalid_numeric_draft_survives_navigation_and_group_reset_recovers_it(self):
        self.lua.execute('''
        debug_open()
        ui.processing_fields.work_units_per_tick.entry.text="invalid";editor_tick()
        debug_click("Native UI and images")
        ui.processing_fields.ui_rows_per_tick.entry.text="7";editor_tick()
        debug_click("Diagnostics");debug_click("Apply")
        assert(#applied==0 and ui.status.state=="error")
        debug_click("Processing");debug_click("Scheduler limits")
        assert(ui.processing_fields.work_units_per_tick.entry.text=="invalid")
        debug_click(require("paintmanager.i18n").text("ui.processing.reset_group"))
        assert(ui.processing_fields.work_units_per_tick.entry.text=="8")
        debug_click("Apply");assert(#applied==1 and applied[1].ui_rows_per_tick==7)
        ''')

    def test_quota_inheritance_and_no_local_cap_are_explicit_values(self):
        self.lua.execute('''
        debug_open();ui:_processing_select_group("quotas")
        local foreground=ui.processing_fields.foreground_units
        local stock=ui.processing_fields.stock_open_units
        assert(foreground.mode.kind=="ComboBoxString" and stock.mode.kind=="ComboBoxString")
        assert(foreground.mode.options[2]=="Inherit" and stock.mode.options[3]=="No local cap")
        stock.entry.text="23";ui.processing_fields.paint_urgent_units.entry.text="13";editor_tick()
        foreground.mode:SetSelectedIndex(1)
        stock.mode:SetSelectedIndex(2);editor_tick()
        assert(not stock.entry.enabled and ui.processing_draft.paint_urgent_units==13)
        stock.mode:SetSelectedIndex(0);editor_tick()
        assert(stock.entry.enabled and stock.entry.text=="23" and ui.processing_draft.stock_open_units==23)
        stock.mode:SetSelectedIndex(2);editor_tick();debug_click("Apply")
        assert(applied[1].foreground_units=="inherit" and applied[1].stock_open_units=="unlimited")
        assert(applied[1].paint_urgent_units==13)
        ''')

    def test_synchronous_profile_is_draft_and_import_does_not_apply(self):
        self.lua.execute('''
        debug_open();debug_click("Profiles");debug_click("Synchronous")
        assert(ui.processing_draft.worker_enabled==nil and not ui.processing_draft.batching_enabled)
        assert(not ui.processing_draft.automatic_preparation and #applied==0 and #commands==0)
        debug_click("Import as draft");assert(ui.processing_draft.work_units_per_tick==17 and #applied==0)
        debug_click("Export profile");assert(processing_actions[2].action=="profile_export")
        assert(processing_actions[2].args.values.work_units_per_tick==17)
        debug_click("Use once at next start");assert(processing_actions[3].action=="startup_profile" and #applied==0)
        ''')

    def test_hiding_developer_tools_retires_stale_editor_actions(self):
        self.lua.execute('''
        debug_open();debug_click("Diagnostics")
        local old_apply,old_reset
        for _,button in ipairs(ui.buttons) do
            if button.label and button.label.text=="Apply" then old_apply=button.action end
            if button.label and button.label.text=="Reset measurements" then old_reset=button.action end
        end
        ui:_change_settings({showDeveloperTools=false})
        assert(not ui.processing_built and ui.processing_fields==nil)
        old_apply();old_reset();assert(#applied==0 and #processing_actions==0)
        ui:_change_settings({showDeveloperTools=true});assert(ui:_select_tab("debug"))
        old_apply();old_reset();assert(#applied==0 and #processing_actions==0)
        for _,button in ipairs(ui.buttons) do assert(button.scope~="debug" or button.processing_control or not button.processing_group) end
        ''')

    def test_footer_discard_restores_effective_values_across_sections_and_main_tabs(self):
        self.lua.execute('''
        debug_open()
        local apply,discard=ui.debug_apply,ui.debug_discard
        assert(debug_visible(apply.widget) and debug_visible(discard.widget))
        assert(not apply.widget.enabled and not discard.widget.enabled)
        ui.processing_fields.work_units_per_tick.entry.text="21";editor_tick()
        assert(apply.widget.enabled and discard.widget.enabled and ui.debug_footer_status.text:find("1",1,true))
        debug_click("Profiles");debug_click("Diagnostics");click("Settings");click("Debug")
        assert(ui.debug_apply==apply and ui.debug_discard==discard and #applied==0)
        debug_click("Processing");assert(ui.processing_fields.work_units_per_tick.entry.text=="21")
        debug_click("Diagnostics");debug_click("Discard edits")
        assert(not apply.widget.enabled and not discard.widget.enabled and #applied==0)
        debug_click("Processing");assert(ui.processing_fields.work_units_per_tick.entry.text=="8")
        debug_click("Measurements and logging")
        local choice=ui.processing_fields.metrics_level.choice
        assert(choice.kind=="ComboBoxString" and choice.options[3]=="detailed");choice:SetSelectedIndex(2)
        editor_tick();debug_click("Apply")
        assert(#applied==1 and applied[1].metrics_level=="detailed")
        ''')

    def test_diagnostics_section_rejects_hidden_actions_and_disarms_jmap_on_section_leave(self):
        self.lua.execute('''
        debug_open();assert(not debug_button("Snapshot") and not debug_button("Export profile"))
        debug_click("Diagnostics");local jmap=debug_button("Prepare JMAP")
        local snapshot=debug_button("Snapshot");local reset=debug_button("Reset measurements")
        assert(snapshot and reset and jmap)
        press_debug(jmap);assert(ui.jmap_armed and debug_button("Confirm JMAP")==jmap and #commands==0)
        debug_click("Profiles");assert(not ui.jmap_armed and not debug_visible(jmap.widget))
        local reads=0
        snapshot.widget.IsPressed=function() reads=reads+1;return true end
        ui:tick(work_budget(8),true);assert(reads==0)
        snapshot.action();reset.action();jmap.action()
        assert(#commands==0 and #processing_actions==0 and not ui.jmap_armed)
        snapshot.widget.IsPressed=function(self) return self.down==true end
        debug_click("Diagnostics");debug_click("Snapshot");assert(#commands==1 and commands[1]=="snapshot")
        ui:set_status({command="snapshot",state="done",path="C:/Reports/paint.json"})
        ui:set_status({command="processing",state="done",message="Done"})
        assert(debug_visible(ui.path_label) and ui.path_label.text:find("C:/Reports/paint.json",1,true))
        debug_click("Prepare JMAP");ui:set_connection({connected=false});ui:set_connection({connected=true})
        assert(not ui.jmap_armed);editor_tick() -- observe the released input after reconnect
        debug_click("Prepare JMAP");debug_click("Confirm JMAP")
        assert(#commands==2 and commands[2]=="jmap" and not ui.jmap_armed)
        debug_click("Pause");assert(paused_preparation==true)
        debug_click("Resume");assert(paused_preparation==false)
        ''')

    def test_buttons_remain_compact_at_desktop_and_small_high_dpi_viewports(self):
        self.lua.execute('''
        viewport_width,viewport_height,viewport_dpi=1920,1080,1
        debug_open()
        for _,dims in ipairs({{1920,1080,1},{1024,768,1.5}}) do
            viewport_width,viewport_height,viewport_dpi=table.unpack(dims)
            ui:tick(work_budget(8),true)
            debug_click("Processing")
            local field=ui.processing_fields.open_work_budget_ms
            assert(field.entry.kind=="EditableTextBox" and field.entry.Slot.Parent.width<=135)
            local scheduler=ui.processing_group_hosts.scheduler
            local rail=scheduler.button.widget.Slot.Parent.Slot.Parent
            assert(rail.kind=="ScrollBox" and rail.Slot.Parent.kind=="SizeBox")
            local rail_box=rail.Slot.Parent
            local split=rail_box.Slot.Parent
            assert(split.kind=="HorizontalBox" and rail_box.width<ui.panel_width/3)
            assert(split.children[2].Slot.size.SizeRule==1 and debug_visible(split.children[2]))
            local node=ui.debug_apply.widget
            while node and node~=ui.debug_page do
                assert(node.kind~="ScrollBox","footer moved into the scrolling settings list")
                node=node.Slot.Parent
            end
            assert(node==ui.debug_page)
            for _,label in ipairs({"Processing","Diagnostics","Profiles","Apply","Discard edits"}) do
                local box=button_sizing(assert(debug_button(label),label))
                assert(box.width<=245 and box.width<ui.panel_width/3 and box.height<=42,label)
            end
            debug_click("Diagnostics")
            for _,label in ipairs({"Snapshot","Prepare JMAP","Capture next operation","Reset measurements"}) do
                assert(button_sizing(assert(debug_button(label),label)).width<=245)
            end
            debug_click("Profiles")
            for _,label in ipairs({"Defaults","Demand only","Synchronous","Export profile","Use once at next start"}) do
                assert(button_sizing(assert(debug_button(label),label)).width<=245)
            end
        end
        ''')

    def test_synchronous_render_finishes_visible_page_in_one_callback(self):
        self.lua.execute('''
        policy({batching_enabled=false,automatic_preparation=false})
        assert(ui:toggle());ui:set_model(large_model(40,"Paint"))
        ui:render_tick(work_budget(100000))
        assert(not ui:is_rendering() and #ui.visible_tiles==40 and ui.active_paint_cache.ready)
        assert(not ui.paint_caches.ready.ready and not ui.paint_caches.missing.ready)
        assert(not ui:processing_has_work())
        ''')

    def test_native_construction_limit_can_exceed_old_hidden_two(self):
        self.lua.execute('''
        policy({ui_rows_per_tick=30,work_units_per_tick=1000,automatic_preparation=false})
        assert(ui:toggle());ui:set_model(large_model(30,"Paint"))
        ui:render_tick(work_budget(100000))
        assert(ui.render_job and ui.render_job.completed>2)
        assert(not ui.active_paint_cache.ready)
        drain_render();assert(#ui.visible_tiles==30)
        ''')

    def test_disabling_shared_unit_limit_does_not_keep_hidden_eight_unit_loop(self):
        self.lua.execute('''
        policy({unit_limit_enabled=false,time_limit_enabled=false,ui_rows_per_tick=1000,
            ui_stock_items_per_tick=1000,work_units_per_tick=1,automatic_preparation=false})
        assert(ui:toggle());ui:set_model(large_model(30,"Paint"))
        ui:render_tick(work_budget(100000))
        assert(not ui:is_rendering() and #ui.visible_tiles==30)
        local snapshot=large_model(30,"Paint");for _,row in ipairs(snapshot.rows) do row.count=5 end
        assert(ui:update_stock(snapshot));ui:_pump_stock(work_budget(100000))
        assert(not ui.stock_patch_job and ui.visible_tiles[30].count.text=="5")
        ''')

    def test_preparation_off_cancels_hidden_work_and_selected_tab_still_builds(self):
        self.lua.execute('''
        assert(ui:toggle());render_model(large_model(20,"Paint"))
        policy({automatic_preparation=false});ui:processing_changed()
        assert(not ui.render_job or not ui.render_job.background)
        assert(ui:_select_tab("ready"));drain_render()
        assert(ui.active_paint_cache.ready and ui.active_tab=="ready")
        ui:processing_reset_derived("cold_reset")
        assert(not ui.active_paint_cache.ready and ui.paint_projection==nil)
        drain_render();assert(ui.active_paint_cache.ready)
        ''')

    def test_stock_patch_internal_rows_and_native_items_have_distinct_controls(self):
        self.lua.execute('''
        policy({automatic_preparation=false,ui_stock_rows_per_unit=1,work_units_per_tick=1})
        assert(ui:toggle());render_model(large_model(30,"Paint"))
        local snapshot=large_model(30,"Paint");for _,row in ipairs(snapshot.rows) do row.count=5 end
        assert(ui:update_stock(snapshot))
        ui:_pump_stock(work_budget(1000));assert(ui.stock_patch_job.phase=="source" and ui.stock_patch_job.index==2)
        policy({ui_stock_rows_per_unit=1000,ui_stock_items_per_tick=1000,work_units_per_tick=1000})
        ui:_pump_stock(work_budget(100000))
        assert(not ui.stock_patch_job and ui.visible_tiles[30].count.text=="5")
        ''')


    def test_footer_settles_after_apply_and_only_visible_diagnostics_collect_metrics(self):
        self.lua.execute('''
        local snapshot, codec = ui.callbacks.processing_snapshot, require("paintmanager.json")
        local encode = codec.encode
        local marker = {callbacks = 13}
        local reads, metric_reads, metric_encodes = 0, 0, 0
        ui.callbacks.processing_snapshot = function(include_metrics)
            reads = reads + 1
            if include_metrics then metric_reads = metric_reads + 1 end
            local state = snapshot()
            state.metrics = marker
            return state
        end
        codec.encode = function(value, ...)
            if value == marker then metric_encodes = metric_encodes + 1 end
            return encode(value, ...)
        end
        debug_open()
        local opened_reads = reads
        ui.processing_fields.work_budget_ms.entry:SetText("4.25")
        editor_tick(); editor_tick()
        assert(reads == opened_reads and metric_reads == 0 and metric_encodes == 0)
        debug_click("Apply")
        assert(pending_policy and ui.debug_footer_status.text == "Staged")
        effective, requested, pending_policy = requested, nil, false
        ui:processing_changed()
        assert(ui.debug_footer_status.text == require("paintmanager.i18n").text("main.processing.applied"))
        assert(not ui.debug_apply.widget.enabled and metric_reads == 0 and metric_encodes == 0)
        debug_click("Diagnostics")
        assert(metric_reads == 1 and metric_encodes == 1 and ui.processing_measurements.text:find("13", 1, true))
        click("Settings")
        ui:processing_changed()
        assert(ui.active_tab == "settings" and metric_reads == 1 and metric_encodes == 1)
        click("Debug")
        assert(metric_reads == 2 and metric_encodes == 2)
        debug_click("Profiles")
        local visible_reads, visible_encodes = metric_reads, metric_encodes
        ui:processing_changed()
        assert(metric_reads == visible_reads and metric_encodes == visible_encodes)
        debug_click("Export profile")
        ui:processing_changed()
        assert(ui.debug_footer_status.text == "Done")
        ''')


class IconProcessingTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute(ICON_FIXTURE)

    def test_demand_only_never_queues_unrequested_icons(self):
        self.lua.execute('''
        local p=require("paintmanager.processing")
        assert(p.request({automatic_preparation=false,batching_enabled=false}));assert(p.commit())
        assert(not icons:preload_all() and #icons.order==0 and imports==0)
        local widget,target=icons:image("refresh",18)
        assert(widget and target and #icons.order==1 and icons:has_demand())
        icons:pump(budget(100000))
        assert(imports==1 and not icons:has_demand() and not icons:has_work())
        assert(icons.entries.star==nil and icons.entries.signal_lost==nil)
        ''')

    def test_queued_speculative_decode_stops_when_preparation_disabled(self):
        self.lua.execute('''
        assert(icons:preload_all());icons:pump(budget(1))
        local p=require("paintmanager.processing")
        assert(p.request({automatic_preparation=false}));assert(p.commit())
        assert(not icons:has_work() and icons:pump(budget(10000))==0 and imports==0)
        icons:image("refresh",18)
        assert(icons:has_demand() and icons:has_work())
        for _=1,100 do icons:pump(budget(8)) end
        assert(imports==1)
        ''')


class ResourceProcessingTests(unittest.TestCase):
    def test_no_startup_host_when_preloading_is_disabled(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts_path = SCRIPTS.as_posix()
        lua.execute(RESOURCE_FIXTURE)
        lua.execute('''
        local p=require("paintmanager.processing")
        assert(p.request({automatic_preparation=false}));assert(p.commit())
        resources=require("paintmanager.ui_resources").new({pump_ms=16})
        resources:tick(nil,false);assert(created==0 and imports==0 and finds==0)
        resources:tick(nil,true);assert(created==1 and resources:get()~=nil)
        assert(not resources.icons.preloaded)
        ''')


if __name__ == "__main__":
    unittest.main()
