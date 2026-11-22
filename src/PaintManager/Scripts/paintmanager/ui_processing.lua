-- Developer-only scheduling editor. Drafts are separate from applied policy;
-- all controller operations are checked again at the main.lua boundary.
local t = require("paintmanager.i18n").text
local processing = require("paintmanager.processing")
local json = require("paintmanager.json")
local safe = require("paintmanager.safe")

local function copy(value)
    local result = {}
    for key, item in pairs(type(value) == "table" and value or {}) do result[key] = item end
    return result
end

local function display(value)
    if type(value) == "boolean" then return t(value and "ui.processing.on" or "ui.processing.off") end
    if type(value) == "number" then return string.format("%.9g", value) end
    return tostring(value or "")
end

return function(M, valid, colors)
function M:_processing_allowed(generation, group)
    return self.open and self:is_connected() and self.active_tab == "debug"
        and self.preferences.showDeveloperTools and self.debug_built
        and (generation == nil or generation == self.processing_ui_generation)
        and (group == nil or group == self.processing_group and self.debug_section == "processing")
end

function M:_processing_metrics_visible()
    return self.debug_section == "diagnostics" and self:_processing_allowed() and not self:is_closing()
        and valid(self.processing_measurements)
end

function M:_processing_snapshot()
    local callback = self.callbacks.processing_snapshot
    if type(callback) == "function" then
        local ok, result = pcall(callback, self:_processing_metrics_visible())
        if ok and type(result) == "table" then return result end
    end
    return { effective = processing.snapshot(), pending = false }
end

function M:_processing_notice(message, failed)
    self.processing_apply_pending = nil
    self.processing_message = tostring(message or "")
    if valid(self.processing_notice) then self:_set_text(self.processing_notice, self.processing_message) end
    if failed then self:set_status({ state = "error", message = self.processing_message }) end
    if self._sync_debug_footer then self:_sync_debug_footer() end
end

function M:_processing_button(parent, label, action, width, group, text_style)
    local generation = self.processing_ui_generation
    local widget = self:_button(t(label), function()
        if self.debug_section == "processing" and self:_processing_allowed(generation, group) then action() end
    end, "debug", nil, nil, nil, nil, text_style)
    local record = self.buttons[#self.buttons]
    record.processing_group, record.processing_control, record.debug_section = group, true, "processing"
    self:_add(parent, self:_sized(widget, width, 28))
    return record
end

local function quota_caption(mode)
    if mode == "inherit" then return t("ui.processing.inherit") end
    if mode == "unlimited" then return t("ui.processing.unlimited") end
    return t("ui.processing.custom")
end

local function option_index(options, value)
    for i, option in ipairs(options) do if option == value then return i - 1 end end
    return 0
end

local function read_field(field)
    if field.checkbox then return field.checkbox:IsChecked() == true end
    if field.choice then
        local choice = field.descriptor.choices[field.choice:GetSelectedIndex() + 1]
        return choice, not choice and "" or nil
    end
    if field.mode then
        local mode = field.modes[field.mode:GetSelectedIndex() + 1]
        if not mode then return nil, "" end
        if mode == "inherit" or mode == "unlimited" then return mode end
    end
    local raw = safe.text(field.entry:GetText()) or ""
    local value = #raw <= 64 and tonumber(raw)
    if not value or not processing.validate({ [field.descriptor.key] = value }) then return nil, raw end
    return value
end

local function enable_amount(field, enabled)
    if field.amount_enabled == enabled then return end
    field.amount_enabled = enabled
    field.entry:SetIsEnabled(enabled)
    field.minus.widget:SetIsEnabled(enabled)
    field.plus.widget:SetIsEnabled(enabled)
end

function M:_processing_update_draft()
    local host = self.processing_group_hosts and self.processing_group_hosts[self.processing_group]
    if self.active_tab ~= "debug" or self.debug_section ~= "processing" or not host then return false end
    local changed = false
    for _, field in ipairs(host.fields) do
        local key = field.descriptor.key
        local value, invalid = read_field(field)
        if invalid ~= self.processing_invalid[key] or value ~= nil and value ~= self.processing_draft[key] then
            changed = true
            self.processing_invalid[key] = invalid
            if value ~= nil then self.processing_draft[key] = value end
            if field.mode then enable_amount(field, value ~= "inherit" and value ~= "unlimited") end
        end
    end
    return changed
end

function M:_processing_dirty_count()
    local count = 0
    for key, value in pairs(self.processing_draft or {}) do
        local invalid = self.processing_invalid[key]
        local field = self.processing_fields[key]
        if field and self.active_tab == "debug" and self.debug_section == "processing" and field.descriptor.group == self.processing_group then
            value, invalid = read_field(field)
        end
        if invalid ~= nil or value ~= self.processing_baseline[key] then count = count + 1 end
    end
    return count
end

function M:_poll_processing_editor()
    local now = os.clock()
    if self.processing_polled and now >= self.processing_polled and now - self.processing_polled < 0.125 then return end
    self.processing_polled = now
    if self:_processing_update_draft() and self._sync_debug_footer then self:_sync_debug_footer() end
end

function M:_processing_sync_fields(group, imported)
    for key, field in pairs(self.processing_fields or {}) do
        if (group == nil or field.descriptor.group == group) and (not imported or imported[key] ~= nil) then
            local value = self.processing_draft[key]
            if field.checkbox then field.checkbox:SetIsChecked(value)
            elseif field.choice then field.choice:SetSelectedIndex(option_index(field.descriptor.choices, value))
            else
                if field.mode then
                    field.mode:SetSelectedIndex(option_index(field.modes, value))
                    enable_amount(field, type(value) == "number")
                end
                local number = type(value) == "number" and value or field.descriptor.default
                field.entry:SetText(FText(self.processing_invalid[key] or display(number)))
            end
        end
    end
end

function M:_processing_refresh()
    if not self.processing_built then return end
    local snapshot = self:_processing_snapshot()
    local effective = snapshot.effective or {}
    self:_processing_update_draft()
    local baseline = snapshot.requested or snapshot.configured or effective
    local updated = {}
    for _, descriptor in ipairs(self.processing_descriptors or {}) do
        local key = descriptor.key
        if not descriptor.restart_required and not self.processing_invalid[key]
            and self.processing_draft[key] == self.processing_baseline[key] and baseline[key] ~= nil
            and self.processing_draft[key] ~= baseline[key] then
            self.processing_draft[key], updated[key] = baseline[key], true
        end
    end
    self.processing_baseline = copy(baseline)
    self.processing_effective = effective
    self:_processing_sync_fields(nil, updated)
    if self.processing_apply_pending and not snapshot.pending then
        self.processing_apply_pending = nil
        self.processing_message = t("main.processing.applied")
        self:_set_text(self.processing_notice, self.processing_message)
    end
    local backend_info = snapshot.backend
    local backend = type(backend_info) == "table" and backend_info.backend or backend_info
    self:_set_text(self.processing_state, t(snapshot.pending and "ui.processing.pending" or "ui.processing.effective", {
        generation = snapshot.generation or 0, backend = backend or "", detail = snapshot.status or "" }))
    self:_set_text(self.processing_backend_notice, t("ui.processing.backend_status", {
        backend = backend or "", reason = type(backend_info) == "table" and backend_info.reason or "",
        pause = t(snapshot.paused and "ui.processing.paused" or "ui.processing.running") }))
    if self.processing_help then self:_processing_show_help(self.processing_help) end
    if self:_processing_metrics_visible() then
        local ok, metrics = pcall(json.encode, snapshot.metrics or {})
        local ok_caps, caps = pcall(json.encode, snapshot.capabilities or snapshot.caps or {})
        self:_set_text(self.processing_measurements, t("ui.processing.measurements", {
            metrics = ok and metrics or "{}", capabilities = ok_caps and caps or "{}" }))
    end
    if self._debug_show_paused then self:_debug_show_paused(snapshot.paused) end
    if self._sync_debug_footer then self:_sync_debug_footer() end
end

function M:processing_changed()
    if self.processing_built and self.preferences.showDeveloperTools then self:_processing_refresh() end
    -- A newly disabled speculative policy does not retire valid completed tabs.
    if not processing.get("automatic_preparation", true) or not processing.get("prewarm_tabs", true) then
        local job = self.render_job
        if job and job.background then
            if job.cache then job.cache.job = nil end
            self.render_job = nil
        end
    end
end

function M:processing_has_work()
    if not self.open then return false end
    if self.active_tab == "presets" and self:is_connected() then return self.preset_render_job ~= nil end
    if self.stock_patch_job or self:is_rendering() then return true end
    return self.icons and type(self.icons.has_demand) == "function" and self.icons:has_demand() or false
end

function M:processing_reset_derived(action)
    local all = action == "cold_reset"
    if all and self.projection_job then self.projection_job:cancel(); self.projection_job = nil end
    if all then self.paint_projection = nil end
    for _, cache in pairs(self.paint_caches or {}) do
        if all or cache == self.active_paint_cache then
            self:_conceal_paint_cache(cache)
            cache.job, cache.result = nil, nil
        end
    end
    self.render_job = nil
    self.render_generation = (self.render_generation or 0) + 1
    if self.open and (self.active_tab == "learned" or self.active_tab == "ready" or self.active_tab == "missing") then
        self:_queue_render(self.model, true, not all)
    end
end

function M:_processing_collect()
    self:_processing_update_draft()
    for _, descriptor in ipairs(self.processing_descriptors) do
        if self.processing_invalid[descriptor.key] ~= nil then
            return nil, t("ui.processing.invalid", { name = t(descriptor.label_key) })
        end
    end
    local changes = copy(self.processing_draft)
    local ok, reason = processing.validate(changes)
    if not ok then return nil, reason end
    return changes
end

function M:_processing_apply()
    if not self:_processing_allowed() then return false end
    local changes, failure = self:_processing_collect()
    if not changes then self:_processing_notice(failure, true); return false end
    local callback = self.callbacks.processing_apply
    if type(callback) ~= "function" then self:_processing_notice(t("ui.processing.unavailable"), true); return false end
    local ok, accepted, message = pcall(callback, changes)
    self:_processing_notice(ok and message or tostring(accepted), not ok or accepted == false)
    self.processing_apply_pending = ok and accepted ~= false
    self:_processing_refresh()
    return ok and accepted ~= false
end

function M:_processing_action(action, include_draft)
    if not self:_processing_allowed() then return false end
    local values
    if include_draft then
        local failure
        values, failure = self:_processing_collect()
        if not values then self:_processing_notice(failure, true); return false end
    end
    local callback = self.callbacks.processing_action
    if type(callback) ~= "function" then self:_processing_notice(t("ui.processing.unavailable"), true); return false end
    local ok, accepted, message, imported = pcall(callback, action, { values = values })
    if ok and accepted ~= false and type(imported) == "table" then
        local valid_profile, reason = processing.validate(imported)
        if not valid_profile then self:_processing_notice(reason, true); return false end
        -- Older profiles omit newer controls. Keep their current values,
        -- including text the user has entered into an open numeric field.
        for key, value in pairs(imported) do self.processing_draft[key], self.processing_invalid[key] = value, nil end
        self:_processing_sync_fields(nil, imported)
    end
    self:_processing_notice(ok and message or tostring(accepted), not ok or accepted == false)
    self:_processing_refresh()
    return ok and accepted ~= false
end

function M:_processing_discard()
    if not self:_processing_allowed() then return false end
    self.processing_invalid, self.processing_message = {}, ""
    self.processing_draft = copy(self.processing_baseline)
    self:_processing_sync_fields()
    self:_processing_notice("")
    return true
end

function M:_processing_stage_profile(profile)
    if not self:_processing_allowed() then return false end
    local values = profile and profile.values
    local ok, reason = processing.validate(values)
    if not ok then self:_processing_notice(reason, true); return false end
    self.processing_invalid, self.processing_draft = {}, copy(values)
    self:_processing_sync_fields()
    self:_processing_notice(t("ui.processing.profile_draft"))
    return true
end

function M:_processing_reset_group()
    local group = self.processing_group
    if not self:_processing_allowed(nil, group) then return end
    for _, field in ipairs(self.processing_group_hosts[group].fields) do
        local key = field.descriptor.key
        self.processing_invalid[key], self.processing_draft[key] = nil, field.descriptor.default
    end
    self:_processing_sync_fields(group)
    self:_processing_notice("")
end

function M:_processing_adjust(key, direction)
    self:_processing_update_draft()
    local field = self.processing_fields[key]
    local descriptor = field.descriptor
    local previous = self.processing_draft[key]
    if type(previous) ~= "number" then previous = descriptor.default end
    local value = previous + direction * (descriptor.step or 1)
    self.processing_draft[key] = math.max(descriptor.min, math.min(descriptor.max, value))
    self.processing_invalid[key] = nil
    self:_processing_sync_fields(descriptor.group, { [key] = true })
    self:_processing_notice("")
end

function M:_processing_show_help(field)
    local previous = self.processing_help
    if previous and previous ~= field then previous.help_label:SetVisibility(1) end
    self.processing_help = field
    if not field then return end
    local d = field.descriptor
    local current = self.processing_effective[d.key]
    local caption = current == "unlimited" and t("ui.processing.unlimited") or display(current)
    self:_set_text(field.help_label, field.help .. "\n" .. t("ui.processing.value", {
        value = caption, unit = d.unit or "" }))
    field.help_label:SetVisibility(0)
end

function M:_processing_build_group(group)
    local host = self.processing_group_hosts[group]
    if host.built then return end
    host.built = true
    local heading = self:_make("HorizontalBox")
    self:_add(heading, self:_label(host.caption, 16, colors.accent), true):SetVerticalAlignment(2)
    self:_processing_button(heading, "ui.processing.reset_group", function() self:_processing_reset_group() end, 108, group)
    self:_add(host.widget, heading)
    local scroll = self:_make("ScrollBox")
    self:_add(host.widget, scroll, true)
    local rows = self:_make("VerticalBox")
    scroll:AddChild(rows)
    for _, d in ipairs(host.descriptors) do
        local key = d.key
        local field = { descriptor = d }
        self.processing_fields[key] = field
        host.fields[#host.fields + 1] = field
        local row = self:_make("HorizontalBox")
        self:_add(row, self:_text(t(d.label_key), 14), true):SetVerticalAlignment(2)
        if d.kind == "boolean" then
            field.checkbox = self:_make("CheckBox")
            self:_add(row, self:_sized(field.checkbox, 30, 26))
        elseif d.kind == "enum" then
            field.choice = self:_combo(row, d.choices)
        else
            field.entry = self:_entry()
            field.minus = self:_processing_button(row, "ui.processing.minus",
                function() self:_processing_adjust(key, -1) end, 26, group, { padding_x = 4 })
            self:_add(row, self:_sized(field.entry, 72, 28))
            field.plus = self:_processing_button(row, "ui.processing.plus",
                function() self:_processing_adjust(key, 1) end, 26, group, { padding_x = 4 })
            self:_add(row, self:_label(d.unit or "", 12, colors.muted)):SetVerticalAlignment(2)
            if d.allow_inherit or d.allow_unlimited then
                field.modes = { "custom" }
                if d.allow_inherit then field.modes[#field.modes+1] = "inherit" end
                if d.allow_unlimited then field.modes[#field.modes+1] = "unlimited" end
                local options = {}
                for i, mode in ipairs(field.modes) do options[i] = quota_caption(mode) end
                field.mode = self:_combo(row, options)
            end
        end
        local range = t(d.kind == "number" and "ui.processing.field_help" or "ui.processing.choice_help", {
            unit = d.unit or "", minimum = d.min or "", maximum = d.max or "", default = display(d.default) })
        field.help = t(d.help_key) .. "\n" .. range
        local generation = self.processing_ui_generation
        field.help_button = self:_help_button(row, field.help, "debug", function()
            if not self:_processing_allowed(generation, group) then return end
            if self.processing_help == field then self:_processing_show_help(nil) else self:_processing_show_help(field) end
        end)
        field.help_button.processing_group, field.help_button.processing_control = group, true
        field.help_button.debug_section = "processing"
        self:_add(rows, row)
        field.help_label = self:_text("", 13, colors.accent)
        field.help_label:SetVisibility(1)
        self:_add(rows, field.help_label)
    end
    self:_processing_sync_fields(group)
end

function M:_processing_activate_group(group)
    local host = self.processing_group_hosts[group]
    self:_processing_show_help(nil)
    self.processing_group = group
    self:_processing_build_group(group)
    for name, candidate in pairs(self.processing_group_hosts) do
        candidate.widget:SetVisibility(name == group and 0 or 1)
        candidate.button.widget:SetBackgroundColor(name == group and colors.selected or colors.button)
    end
    self.processing_polled = nil
end

function M:_processing_select_group(group)
    if not self:_processing_allowed() or self.debug_section ~= "processing" or not self.processing_group_hosts[group] then return end
    if self.processing_group == group then return end
    self:_processing_update_draft()
    self:_processing_activate_group(group)
    if self._sync_debug_footer then self:_sync_debug_footer() end
end

function M:_release_processing_editor()
    self.processing_fields, self.processing_group_hosts, self.processing_help = nil, nil, nil
    self.processing_baseline, self.processing_effective, self.processing_invalid = nil, nil, nil
    self.processing_built, self.processing_draft, self.processing_descriptors = false, nil, nil
    self.processing_group, self.processing_polled, self.processing_apply_pending = nil, nil, nil
end

function M:_build_processing_page(parent)
    self.processing_ui_generation = (self.processing_ui_generation or 0) + 1
    local snapshot = self:_processing_snapshot()
    self.processing_baseline = copy(snapshot.requested or snapshot.configured or snapshot.effective or processing.defaults())
    self.processing_invalid, self.processing_draft, self.processing_effective = {}, copy(self.processing_baseline), snapshot.effective or {}
    local callback = self.callbacks.processing_descriptors
    self.processing_descriptors = type(callback) == "function" and callback() or processing.descriptors()
    self.processing_fields, self.processing_group_hosts = {}, {}
    local split, sidebar, content = self:_make("HorizontalBox"), self:_make("ScrollBox"), self:_make("VerticalBox")
    self:_add(split, self:_sized(sidebar, 246))
    self:_add(split, content, true)
    self:_add(parent, split, true)
    local ordered = { "scheduler", "execution", "quotas", "ui", "preparation", "caches", "primitives", "bytes", "cadence", "diagnostics" }
    local groups = {}
    for _, d in ipairs(self.processing_descriptors) do
        if not groups[d.group] then groups[d.group] = {} end
        groups[d.group][#groups[d.group] + 1] = d
    end
    local rank = { open_work_budget_ms = 1, work_budget_ms = 2, work_units_per_tick = 3,
        time_limit_enabled = 4, unit_limit_enabled = 5 }
    local scheduler = groups.scheduler
    if scheduler then
        for index, d in ipairs(scheduler) do rank[d.key] = rank[d.key] or 5 + index end
        table.sort(scheduler, function(a, b) return rank[a.key] < rank[b.key] end)
    end
    for _, group in ipairs(ordered) do
        if groups[group] then
            local key = "ui.processing.group." .. group
            local host = { widget = self:_make("VerticalBox"), caption = t(key), descriptors = groups[group], fields = {} }
            self.processing_group_hosts[group] = host
            host.button = self:_processing_button(sidebar, key, function() self:_processing_select_group(group) end, 224, nil,
                { left = true, size = 13, padding_x = 6 })
            host.widget:SetVisibility(1)
            self:_add(content, host.widget, true)
        end
    end
    self.processing_built = true
    self:_processing_activate_group("scheduler")
    self:_processing_show_help(self.processing_fields.open_work_budget_ms)
    self:_processing_refresh()
end
end
