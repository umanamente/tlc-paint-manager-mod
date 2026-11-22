-- Stable preference values. Order here is also the order of the language picker.
local M = { choices = { "auto", "en", "ru" }, game_revision = 0 }

-- Native FText follows the game even when the mod's own controls use an override.
function M.observe_game(tag)
    if type(tag) ~= "string" or tag == "" then return false end
    tag = tag:lower():gsub("_", "-")
    if tag == M.game_tag then return false end
    M.game_tag, M.game_revision = tag, M.game_revision + 1
    return true
end

function M.valid(code)
    for _, choice in ipairs(M.choices) do if choice == code then return true end end
    return false
end

function M.resolve(preference, game_tag)
    if preference ~= "auto" and M.valid(preference) then return preference end
    local tag = type(game_tag) == "string" and game_tag:lower():gsub("_", "-") or ""
    while tag ~= "" do
        if tag ~= "auto" and M.valid(tag) then return tag end
        tag = tag:match("^(.*)%-[^-]+$") or ""
    end
    return "en"
end

-- Game-thread caller only. The same-build reflected getter has one FString return.
-- Keep only its plain text, and retry if the native service is not ready yet.
function M.game_language()
    local safe = require("paintmanager.safe")
    local ok, tag = pcall(function()
        local service = StaticFindObject("/Script/Engine.Default__KismetInternationalizationLibrary")
        if safe.valid(service) then return safe.text(service:GetCurrentLanguage()) end
    end)
    return ok and type(tag) == "string" and tag ~= "" and tag or nil
end

return M
