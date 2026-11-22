-- Built from one reconciled library. Looking up an edited target only visits its zones.
local layout = require("paintmanager.preset_layout")
local i18n = require("paintmanager.i18n")
local display_text = require("paintmanager.display_text")
local M = {}
local Index = {}; Index.__index = Index
local Build = {}; Build.__index = Build

function Index:match(target)
    local key = target and target.complete == true and layout.key(target.compatibility, target.logicalZones)
    if not key then return nil, "Selected paint target is incomplete" end
    return self.byLayout[key]
end

function Index:suggest_name()
    if self.nameLanguage ~= i18n.language then
        local number, candidate = 0, nil
        repeat
            number = number + 1
            candidate = i18n.text("ui.preset.suggest_name", { number = number })
        until not self.usedNames[display_text.fold(candidate)]
        self.nextName, self.nameLanguage = candidate, i18n.language
    end
    return self.nextName
end

function M.begin(library)
    return setmetatable({ records = library.presets, cursor = 1, usedNames = {}, state = "running",
        index = setmetatable({ byLayout = {} }, Index) }, Build)
end

function Build:_advance()
    local record = self.records[self.cursor]
    if record then
        local key = layout.key(record.target, record.zones)
        if key and not self.index.byLayout[key] then self.index.byLayout[key] = record end
        self.usedNames[display_text.fold(record.name)] = true
        self.cursor = self.cursor + 1
        return
    end
    self.index.usedNames = self.usedNames
    self.index:suggest_name()
    self.records, self.usedNames, self.state = nil, nil, "done"
end

function Build:step(budget)
    while self.state == "running" and (not budget or not budget:expired()) do
        -- The storage schema caps each unit at one preset with 128 zones.
        local ok, reason = pcall(self._advance, self)
        if not ok then self.state, self.error, self.index = "error", tostring(reason), nil end
        if not budget then break end
        budget:consume(1)
    end
    return { state = self.state, error = self.error }
end

function Build:get_result() return self.state == "done" and self.index or nil end

return M
