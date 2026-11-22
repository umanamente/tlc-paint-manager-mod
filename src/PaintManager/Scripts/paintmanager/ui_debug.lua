-- The Debug shell keeps the processing draft and footer across its three pages.
local t = require("paintmanager.i18n").text
local processing = require("paintmanager.processing")
local sections = { "processing", "diagnostics", "profiles" }
local nav_widths = { processing = 98, diagnostics = 105, profiles = 81 }
local highlight = { R = 0.34, G = 0.72, B = 0.87, A = 1 }

return function(M, valid, colors)
local function button(self, parent, section, label, width, action, color, tooltip)
    local shell = self.debug_hosts
    local widget = self:_button(t(label), function()
        if self.open and self.debug_hosts == shell and self.debug_built and self.active_tab == "debug"
            and not self:is_closing() and self.preferences.showDeveloperTools and self:is_connected()
            and (not section or self.debug_section == section) then action() end
    end, "debug", color, nil, tooltip and t(tooltip))
    local record = self.buttons[#self.buttons]
    record.debug_section = section
    self:_add(parent, self:_sized(widget, width, 32))
    return record
end

local function command_row(self, page, heading)
    self:_add(page, self:_label(t(heading), 16))
    local row = self:_make("HorizontalBox")
    self:_add(page, row)
    return row
end

function M:_debug_show_paused(paused)
    if self.debug_pause and self.debug_pause.paused ~= (paused == true) then
        self.debug_pause.paused = paused == true
        self:_set_text(self.debug_pause.label, t(paused and "ui.debug.resume" or "ui.debug.pause"))
    end
end

function M:_sync_debug_footer()
    if not self.debug_apply or not self.processing_built then return end
    local count = self:_processing_dirty_count()
    local caption = count == 1 and t("ui.debug.one_change")
        or count > 1 and t("ui.debug.changes", { count = count })
        or self.processing_message and self.processing_message ~= "" and self.processing_message or t("ui.debug.clean")
    if self.debug_footer_caption ~= caption then
        self.debug_footer_caption = caption
        self:_set_text(self.debug_footer_status, caption)
    end
    if self.debug_footer_enabled ~= (count > 0) then
        self.debug_footer_enabled = count > 0
        self.debug_apply.widget:SetIsEnabled(self.debug_footer_enabled)
        self.debug_discard.widget:SetIsEnabled(self.debug_footer_enabled)
    end
end

function M:_debug_select_section(section)
    if not self.debug_hosts or not self.debug_hosts[section] or self.active_tab ~= "debug"
        or not self.preferences.showDeveloperTools or self.open and (not self:is_connected() or self:is_closing()) then return false end
    if self.debug_section == section then return true end
    if self.debug_section == "processing" then self:_processing_update_draft() end
    self.debug_section, self.jmap_armed = section, false
    local build = self.debug_pending_sections[section]
    if build then
        build(self, self.debug_hosts[section])
        self.debug_pending_sections[section] = nil
    end
    for index, name in ipairs(sections) do
        local selected = name == section
        self.debug_hosts[name]:SetVisibility(selected and 0 or 1)
        self.debug_nav[name].underline:SetVisibility(selected and 0 or 2)
        self.debug_nav[name].label:SetColorAndOpacity({ SpecifiedColor = selected and colors.text or colors.muted, ColorUseRule = 0 })
        if selected then self.debug_switcher:SetActiveWidgetIndex(index-1) end
    end
    self:_update_jmap()
    if section == "diagnostics" then
        self:_update_diagnostics()
        self:_processing_refresh()
        self:_render_status()
    end
    self:_sync_debug_footer()
    return true
end

local function diagnostics(self, page)
    local section = "diagnostics"
    local reports = command_row(self, page, "ui.debug.reports")
    button(self, reports, section, "ui.button.snapshot", 110, function() self:_request("snapshot") end)
    button(self, reports, section, "ui.debug.discovery", 170, function() self:_request("discovery") end,
        nil, "ui.diagnostics.discovery_notice")
    self.debug_jmap = button(self, reports, section, "ui.button.jmap_prepare", 144, function()
        if self.jmap_armed then
            self.jmap_armed = false; self:_update_jmap(); self:_request("jmap")
        else self.jmap_armed = true; self:_update_jmap() end
    end, nil, "ui.jmap.idle_notice")
    self.jmap_confirm = self.debug_jmap.widget
    self.jmap_notice = self:_text(t("ui.jmap.confirm_notice"), 13, colors.warning)
    self:_add(page, self.jmap_notice)
    self.path_label = self:_text("", 13, colors.muted)
    self:_add(page, self.path_label)
    local measures = command_row(self, page, "ui.debug.measurements")
    button(self, measures, section, "ui.processing.action.refresh_metrics", 207, function() self:_processing_refresh() end)
    local function command(parent, action, width)
        return button(self, parent, section, "ui.processing.action." .. action, width,
            function() self:_processing_action(action) end)
    end
    command(measures, "capture_next", 198)
    command(measures, "reset_metrics", 193)
    local preparation = command_row(self, page, "ui.debug.preparation")
    self.debug_pause = button(self, preparation, section, "ui.debug.pause", 96, function()
        self:_processing_action(self:_processing_snapshot().paused and "resume" or "pause")
    end)
    command(preparation, "step_packet", 120)
    command(preparation, "step_primitive", 157)
    local caches = command_row(self, page, "ui.debug.caches")
    command(caches, "cold_reset", 190)
    command(caches, "rebuild_tab", 154)
    command(caches, "fresh_discovery", 245)
    self.diagnostic_text = self:_text("", 13, colors.muted)
    self.processing_state = self:_text("", 13, colors.accent)
    self.processing_backend_notice = self:_text("", 13, colors.muted)
    self.processing_measurements = self:_text("", 12, colors.muted)
    for _, label in ipairs({ self.diagnostic_text, self.processing_state, self.processing_backend_notice,
        self.processing_measurements }) do self:_add(page, label) end
end

local function profiles(self, page)
    local section = "profiles"
    local stages = command_row(self, page, "ui.debug.stage_profile")
    for _, profile in ipairs(processing.profiles()) do
        button(self, stages, section, profile.label_key, 126, function() self:_processing_stage_profile(profile) end)
    end
    local files = command_row(self, page, "ui.debug.profile_file")
    button(self, files, section, "ui.processing.export", 137, function() self:_processing_action("profile_export", true) end)
    button(self, files, section, "ui.processing.import", 148, function() self:_processing_action("profile_import") end)
    button(self, files, section, "ui.processing.startup_once", 215, function() self:_processing_action("startup_profile", true) end)
end

function M:_build_debug()
    self.debug_built, self.debug_hosts, self.debug_nav = true, {}, {}
    self.debug_pending_sections = { diagnostics = diagnostics, profiles = profiles }
    local outer = self.debug_page
    self:_add(outer, self:_label(t("ui.debug.title"), 18))
    local navigation = self:_make("HorizontalBox")
    self:_add(outer, navigation)
    self.debug_switcher = self:_make("WidgetSwitcher")
    self:_add(outer, self.debug_switcher, true)
    for _, section in ipairs(sections) do
        local tab = self:_make("VerticalBox")
        self:_add(navigation, tab)
        local nav = button(self, tab, nil, "ui.debug.section." .. section, nav_widths[section],
            function() self:_debug_select_section(section) end, colors.panel)
        nav.widget.WidgetStyle.Normal.DrawAs = 0
        local line = self:_make("Border")
        line:SetBrushColor(highlight)
        self:_add(tab, self:_sized(line, nil, 2)):SetPadding({ Left=4, Top=0, Right=4, Bottom=0 })
        self.debug_nav[section], nav.underline = nav, line
        local page = self:_make("VerticalBox")
        self.debug_hosts[section] = page
        if section == "processing" then self.debug_switcher:AddChild(page)
        else
            local scroll = self:_make("ScrollBox")
            self.debug_switcher:AddChild(scroll)
            scroll:AddChild(page)
        end
    end
    local footer = self:_make("HorizontalBox")
    self:_add(outer, self:_border(footer, colors.paint_details, 4))
    self.debug_footer_status = self:_text("", 14, colors.muted)
    self:_add(footer, self.debug_footer_status, true):SetVerticalAlignment(2)
    self.debug_discard = button(self, footer, nil, "ui.processing.discard", 138,
        function() if self.debug_footer_enabled then self:_processing_discard() end end)
    self.debug_apply = button(self, footer, nil, "ui.processing.apply", 92,
        function() if self.debug_footer_enabled then self:_processing_apply() end end, highlight)
    self.debug_apply.label:SetColorAndOpacity({ SpecifiedColor = colors.background, ColorUseRule = 0 })
    self:_debug_select_section("processing")
    self:_build_processing_page(self.debug_hosts.processing)
    self:_sync_debug_footer()
end

function M:_release_debug()
    self:_release_processing_editor()
    self.processing_ui_generation = (self.processing_ui_generation or 0) + 1
    self.processing_state, self.processing_notice, self.processing_measurements, self.processing_backend_notice = nil, nil, nil, nil
    self.debug_built, self.diagnostic_text, self.jmap_confirm, self.jmap_notice, self.path_label = false, nil, nil, nil, nil
    self.debug_section, self.debug_hosts, self.debug_nav, self.debug_switcher = nil, nil, nil, nil
    self.debug_pending_sections = nil
    self.debug_apply, self.debug_discard, self.debug_footer_status = nil, nil, nil
    self.debug_footer_caption, self.debug_footer_enabled, self.debug_jmap, self.debug_pause = nil, nil, nil, nil
    self.processing_message = nil
end
end
