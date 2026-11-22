-- Stable preference values and compact picker metadata. Load message text on selection.
local M = { choices = { "auto", "en" }, game_revision = 0, locales = {} }
local tags = { en = "en" }
for _, locale in ipairs(require("paintmanager.locales.index")) do
    M.choices[#M.choices + 1] = locale.code
    M.locales[locale.code] = locale
    tags[locale.code:lower()] = locale.code
end

-- The game's regional catalogs also serve common platform culture tags.
tags.es, tags.pt, tags.sv = "es-ES", "pt-BR", "sv-FI"
for region in ("ar bo cl co cr cu do ec gt hn mx ni pa pe pr py sv us uy ve"):gmatch("%S+") do
    tags["es-" .. region] = "es-419"
end
tags.zh, tags["zh-cn"], tags["zh-sg"], tags["zh-chs"] = "zh-Hans", "zh-Hans", "zh-Hans", "zh-Hans"
-- Traditional Chinese has no native translation in this build.
tags["zh-hant"], tags["zh-tw"], tags["zh-hk"], tags["zh-mo"], tags["zh-cht"] = "en", "en", "en", "en", "en"

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
    tag = tag:gsub("^es%-latn%-", "es-")
    while tag ~= "" do
        if tags[tag] then return tags[tag] end
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
