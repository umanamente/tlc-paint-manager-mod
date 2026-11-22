-- Backpack and provenance observations for the mod's paint actions.
local transfer = require("paintmanager.paint_transfer")
local origins = require("paintmanager.transfer_ledger")
local transfer_quantity = require("paintmanager.transfer_quantity")
local paint_view = require("paintmanager.paint_view")
local t = require("paintmanager.i18n").text
local player_messages = require("paintmanager.player_messages")
local M = {}
M.__index = M

local commands = { gather_paint = "take", gather_all = "take", return_paint = "return", return_all = "return" }

function M.is_command(command) return commands[command] ~= nil end
function M.is_return(command) return commands[command] == "return" end

function M.new(config)
    return setmetatable({ config = config }, M)
end

function M:reset()
    self.token, self.ledger, self.state, self.next_poll, self.last_model = nil, nil, nil, nil, nil
    self.last_bag_counts = nil
end

function M:invalidate()
    self.next_poll, self.state = nil, nil
end

function M:observe_load(generation)
    if type(generation) == "number" and generation ~= self.load_generation then
        self.load_generation = generation
        transfer.reset_context()
    end
end

local function same_counts(left, right)
    for k, v in pairs(left) do if right[k] ~= v then return false end end
    for k in pairs(right) do if left[k] == nil then return false end end
    return true
end

local function needs_stock_capture(model, before, counts)
    if not model or model.status ~= "captured" or not counts then return false end
    if before and same_counts(before, counts) then return false end
    -- A deposit could use a previously empty slot in any of these boxes.
    if before and (model.sourceCount or model.includedSourceCount or 0) > 0 then
        for id, n in pairs(before) do if n > (counts[id] or 0) then return true end end
    end
    local stock = {}
    for _, row in ipairs(model.rows or {}) do stock[row.id] = row.count or 0 end
    for id, n in pairs(counts) do
        if n > (before and before[id] or 0) then
            -- Refresh a newly acquired color missing from the catalog, or a
            -- possible pickup from an already counted box. A verified mod
            -- transfer has already published its matching before/after totals.
            if stock[id] == nil and not model.catalogTruncated or before and (stock[id] or 0) > 0 then return true end
        end
    end
    return false
end

function M:refresh(window, token, model, now, budget)
    if self.token ~= token then self:reset(); self.token = token end
    if self.next_poll and self.last_model == model and now < self.next_poll then return end
    if not token or budget:expired() then return end
    if self.last_model ~= model then
        self.last_bag_counts = model and model.backpackCounts or nil
    end
    self.next_poll, self.last_model = now + 5000, model
    -- One bounded backpack read. Source-box discovery belongs to the visible
    -- stock interval or an explicit transfer request.
    budget:consume(1)
    local bag, err = transfer.backpack(self.config, token)
    if model and model.contextToken == token and needs_stock_capture(model, self.last_bag_counts, bag and bag.counts) then
        self.needs_capture = true
    end
    if bag then self.last_bag_counts = bag.counts end
    if window.update_backpack_counts then window:update_backpack_counts(token, bag and bag.counts) end
    -- Reconciliation can give the UI a separate copy of the captured rows.
    -- Keep the controller's admission counts current even in that case.
    if model and model.contextToken == token and model ~= window.model then
        paint_view.set_backpack_counts(model, bag and bag.counts)
    end
    local ledger = self.ledger or origins.new(token)
    local loaded, journal_error = ledger:load()
    local state
    if bag and loaded then
        self.ledger = ledger
        local ok
        ok, err = ledger:reconcile(bag.slots)
        if ok then
            local counts = transfer.returnable(bag, ledger:entries())
            state = { contextToken = token, backpack = bag.counts, returnable = counts,
                canGatherAll = model ~= nil and not model.cachePreview and not model.learnedOnly
                    and (model.physicalCount or 0) > 0,
                canReturnAll = next(counts) ~= nil }
        end
    end
    if not state then
        local detail = err or journal_error
        local key = player_messages.reason_key(detail, bag and "main.reason.origins" or "main.reason.backpack")
        state = { contextToken = token, unavailable = t(key), unavailableKey = key, diagnostic = detail,
            backpack = bag and bag.counts, canGatherAll = false, canReturnAll = false }
        if detail and (not self.state or detail ~= self.state.diagnostic) and window.callbacks and window.callbacks.log then
            pcall(window.callbacks.log, "Transfer controls unavailable: " .. tostring(detail))
        end
    end
    local old = self.state
    self.state = state
    if old and old.contextToken == token and old.canGatherAll == state.canGatherAll
        and old.canReturnAll == state.canReturnAll and old.unavailable == state.unavailable
        and same_counts(old.backpack or {}, state.backpack or {}) and same_counts(old.returnable or {}, state.returnable or {}) then return end
    window:set_transfer_state(state)
end

function M:begin(command, item_id, token, model, build, guard, quantity)
    local state = self.state
    if not commands[command] or not state or state.unavailable or state.contextToken ~= token or not self.ledger then
        return nil, state and state.unavailable or t("main.transfer.not_ready")
    end
    if command == "return_all" or command == "gather_all" then
        if item_id ~= nil then return nil, t("main.transfer.unavailable") end
    elseif type(item_id) ~= "string" or item_id == "" then return nil, t("main.transfer.unavailable") end
    if M.is_return(command) then
        if not state.canReturnAll or item_id and (state.returnable[item_id] or 0) < 1 then
            return nil, t("main.transfer.unavailable")
        end
    else
        if not model or model.contextToken ~= token or not state.canGatherAll then return nil, t("main.transfer.not_ready") end
        if item_id then
            if quantity == nil then quantity = self.config.selected_backpack_target end
            if not transfer_quantity.valid(quantity) then return nil, t("main.settings.invalid") end
            if quantity ~= "all" and (state.backpack[item_id] or 0) >= quantity then
                return nil, t("main.transfer.target_reached", { count = quantity })
            end
            local found = false
            for _, row in ipairs(model.rows or {}) do
                if row.id == item_id and paint_view.stock_current(model, row) and (row.count or 0) > 0 then
                    found = true
                    break
                end
            end
            if not found then return nil, t("main.transfer.unavailable") end
        end
    end
    return transfer.begin(self.config, { direction = commands[command], itemPath = item_id, model = model,
        quantity = command == "gather_paint" and quantity or nil,
        contextToken = token, gameBuild = build, ledger = self.ledger, guard = guard })
end

function M.result(progress)
    local reason = progress.error or progress.reason
    local message = t("main.transfer.result", { moved = progress.moved or 0, skipped = progress.skipped or 0 })
    if progress.outcome == "uncertain" then message = message .. " " .. t("main.transfer.uncertain") end
    if progress.incomplete then message = message .. " " .. t("main.transfer.incomplete") end
    if (progress.reasonKey or reason and reason ~= "") and progress.outcome ~= "uncertain" then
        message = message .. " " .. t(progress.reasonKey or player_messages.reason_key(reason))
    end
    return message
end

return M
