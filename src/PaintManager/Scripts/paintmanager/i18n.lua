-- Pure catalog lookup. Only a game-thread caller chooses the presentation language.
local languages = require("paintmanager.languages")
local plurals = require("paintmanager.locales.en_plurals")
local english = {}
for _, catalog in ipairs({ (require("paintmanager.locales.en_ui")), (require("paintmanager.locales.en_main")) }) do
    for key, value in pairs(catalog) do
        assert(type(key) == "string" and key ~= "" and type(value) == "string", "Invalid English message entry")
        assert(english[key] == nil, "Duplicate English message key: " .. key)
        english[key] = value
    end
end

local M = { language = "en", revision = 0 }

local function parameters(template)
    local result = {}
    for name in template:gmatch("{([%a_][%w_]*)}") do result[name] = true end
    return result
end

local function check_override(key, value)
    assert(english[key] ~= nil, "Unknown translation key: " .. tostring(key))
    assert(type(value) == "string", "Translation must be a string: " .. key)
    local expected, actual = parameters(english[key]), parameters(value)
    for name in pairs(expected) do assert(actual[name], "Missing translation placeholder: " .. key .. "." .. name) end
    for name in pairs(actual) do assert(expected[name], "Unexpected translation placeholder: " .. key .. "." .. name) end
end

local function plural_form(language, count)
    count = tonumber(count)
    if language ~= "ru" then return count == 1 and "one" or "other" end
    if not count or count % 1 ~= 0 then return "many" end
    local last, two = math.abs(count) % 10, math.abs(count) % 100
    if last == 1 and two ~= 11 then return "one" end
    if last >= 2 and last <= 4 and (two < 12 or two > 14) then return "few" end
    return "many"
end

-- A partial translation falls back to English per key, including English plural rules.
function M.new(overrides, language)
    local selected = {}
    for key, value in pairs(overrides or {}) do
        if type(value) == "table" then
            assert(plurals[key], "Unexpected plural translation: " .. key)
            local copy = {}
            for _, form in ipairs(language == "ru" and {"one", "few", "many"} or {"one", "other"}) do
                check_override(key, value[form])
                copy[form] = value[form]
            end
            selected[key] = copy
        else
            check_override(key, value)
            selected[key] = value
        end
    end
    return {
        text = function(key, values)
            local fallback = assert(english[key], "Unknown English message key: " .. tostring(key))
            values = values or {}
            assert(type(values) == "table", "Message parameters must be a table: " .. key)
            local template = selected[key]
            local plural = plurals[key]
            if type(template) == "table" then
                template = template[plural_form(language, values[plural.count])]
            elseif not template and plural and plural_form("en", values[plural.count]) == "one" then
                template = plural.one
            end
            local rendered = (template or fallback):gsub("{([%a_][%w_]*)}", function(name)
                local value = values[name]
                local kind = type(value)
                assert(kind == "string" or kind == "number" or kind == "boolean",
                    "Missing or invalid message parameter: " .. key .. "." .. name)
                return tostring(value)
            end)
            return rendered
        end,
    }
end

local catalogs = { en = M.new() }
local function catalog(code)
    if not catalogs[code] then catalogs[code] = M.new(require("paintmanager.locales." .. code), code) end
    return catalogs[code]
end

function M.select(preference, game_tag)
    local code = languages.resolve(preference, game_tag)
    if M.language == code then return false end
    catalog(code)
    M.language, M.revision = code, M.revision + 1
    return true
end

function M.language_name(code) return catalog(code).text("ui.settings.language_name") end
function M.text(key, values) return catalogs[M.language].text(key, values) end

-- Return a copy so validation tooling cannot mutate the runtime fallback.
function M.english_catalog()
    local result = {}
    for key, value in pairs(english) do result[key] = value end
    return result
end

return M
