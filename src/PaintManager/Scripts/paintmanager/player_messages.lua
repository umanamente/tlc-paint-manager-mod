-- Native and storage diagnostics stay on receipts and in the log. Only known
-- player-facing reasons cross the boundary into a widget or translated template.
local t = require("paintmanager.i18n").text
local M = {}

local outcomes = {
    learned = "main.outcome.learned", already_known = "main.outcome.known",
    completed = "main.outcome.completed", none_available = "main.outcome.none",
    applied_verified = "main.outcome.applied", already_matches = "main.outcome.matches",
    rejected = "main.outcome.rejected", cancelled = "main.outcome.cancelled",
    partial = "main.outcome.partial", partial_stopped = "main.outcome.partial",
    partial_cancelled = "main.outcome.partial", stopped_error = "main.outcome.stopped",
    stopped_unverified = "main.outcome.stopped_unverified",
}

function M.outcome(code)
    return t(outcomes[code] or "main.outcome.uncertain")
end

local reasons = {
    ["Destination has no empty slot"] = "main.reason.destination_full",
    ["Destination lacks room or weight allowance for this paint"] = "main.reason.destination_full",
    ["Your backpack needs an empty slot for the removed paint"] = "main.reason.backpack_full",
    ["Your backpack cannot accept the removed paint"] = "main.reason.backpack_full",
    ["Not enough matching physical cans in the backpack and accessible ship boxes for the entire preset"] = "main.reason.shortage",
    ["Not enough of the selected paint is available in ship storage"] = "main.reason.shortage",
    ["The selected paint is no longer available"] = "main.transfer.unavailable",
    ["Original container is unavailable or ambiguous"] = "main.reason.origin",
    ["Original container identity changed"] = "main.reason.origin",
    ["Current backpack can has no matching box origin"] = "main.reason.origin",
    ["A previous transfer has an uncertain outcome"] = "main.transfer.uncertain",
    ["A previous paint transfer is uncertain; restart/reload before retrying"] = "main.transfer.uncertain",
    ["Library preview changed"] = "main.reason.library_changed",
    ["Library preview changed; refresh before continuing"] = "main.reason.library_changed",
    ["Local library changed since the preview; refresh before continuing"] = "main.reason.library_changed",
    ["Preset library changed while saving"] = "main.reason.library_changed",
    ["Selected paint target is incomplete"] = "main.preset.target_incomplete",
}

-- Some call sites already raise a localized, actionable message through Lua
-- assert/pcall. Recognize these before substituting a generic failure; never
-- display an arbitrary native exception, stack path or internal identifier.
local user_reasons = {
    "ui.editor.source_changed", "main.preset.target_incomplete", "main.connection.lost",
    "main.error.context_changed", "main.error.no_character", "main.error.load_character",
    "main.cancel.window_closed", "main.cancel.radius_changed", "main.cancel.default",
    "main.cancel.before_start", "main.learn.window_closed", "main.settings.developer_hidden",
    "ui.error.player_unavailable", "ui.error.menu_open", "ui.error.pause_open",
    "ui.error.viewport_small", "main.storage.build", "main.learn.build_unverified",
    "main.learn.not_eligible", "main.learn.busy", "main.preset.refresh_required",
}

function M.reason_key(detail, fallback)
    local message = tostring(detail or ""):gsub("^.*%.lua:%d+:%s*", "")
    local key = reasons[message]
    if key then return key end
    if message:match("^Invalid preset library") then return "main.reason.library_invalid" end
    if message ~= "" then
        for _, candidate in ipairs(user_reasons) do
            if message == t(candidate) then return candidate end
        end
    end
    return fallback or "main.reason.failed"
end

function M.reason(detail, fallback)
    return t(M.reason_key(detail, fallback))
end

return M
