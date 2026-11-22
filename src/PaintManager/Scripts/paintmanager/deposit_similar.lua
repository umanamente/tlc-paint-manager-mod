-- The separate paint button and the stock Deposit Similar action share a
-- verified open-box transfer. Each game-thread step moves at most one stack.
local collection = require("paintmanager.collection")
local transfer = require("paintmanager.paint_transfer")
local M = {}
M.__index = M

local function notify(callback, ...)
    if callback then pcall(callback, ...) end
end

function M:_allowed(token)
    return not self.closed and (self.enabled.stock or self.enabled.custom) and type(self.config.reference_build) == "string"
        and self.options.game_build() == self.config.reference_build
        and self.options.guard(token) == true and transfer.can_transfer(token)
end

local function endpoint_id(component)
    return assert(collection.runtime_ref(component), "Could not identify the open inventory").id
end

function M:_can_offer(token, endpoints)
    if self.busy or not self:_allowed(token) then return false, false end
    local bag, box = endpoints()
    if not transfer.inventory_has_paint(box, self.config) or not transfer.inventory_has_paint(bag, self.config) then
        return false, false
    end
    local slot = box:GetNextEmptySlot()
    return true, not self.job and math.type(slot) == "integer" and slot >= 0 and slot <= 2147483647
end

function M:_prepare(token, endpoints, feature)
    if not self.enabled[feature] or self.busy or not self:_allowed(token) then
        return nil, "Paint transfers are unavailable in the current scope"
    end
    local generation = self.generation
    local revision = self.revisions[feature]
    local bag, box = endpoints()
    local bag_id, box_id = endpoint_id(bag), endpoint_id(box)
    if bag_id == box_id then return nil, "The open inventory is the player's backpack" end
    if not transfer.inventory_has_paint(box, self.config) then return nil, "The open box contains no paint" end
    local rows = transfer.inventory_paints(bag, self.config)
    if #rows == 0 then return nil, "The backpack contains no paint" end
    local current_bag, current_box = endpoints()
    assert(generation == self.generation and revision == self.revisions[feature]
        and endpoint_id(current_bag) == bag_id and endpoint_id(current_box) == box_id,
        "Open inventories changed during paint deposit preparation")
    return { generation = generation, feature = feature, revision = revision, token = token, bagId = bag_id, boxId = box_id, rows = rows }
end

function M:_finish(reason)
    local job = self.job
    self.job = nil
    if not job then return end
    local detail = reason or job.first_skip
    local summary
    if job.explicit then
        summary = string.format("Deposit all paints: %d of %d cans verified in %d transfers",
            job.verified, job.requested, job.moves)
    else
        -- The native action has already handled its own matches. Only cans
        -- moved and verified by the extension contribute to this number.
        summary = string.format("Deposit Similar: %d additional paint cans verified by PaintManager in %d transfers",
            job.verified, job.moves)
        if not detail and job.verified == 0 and job.source_changed then
            detail = "The captured paint stacks were no longer available after the native action"
        end
    end
    notify(self.options.log, summary .. "." .. (detail and (" " .. detail) or ""))
    notify(self.options.changed, job.token)
end

function M:_submit(snapshot, endpoints, explicit)
    local feature = explicit and "custom" or "stock"
    local function current_selection()
        return self.enabled[feature] and snapshot and snapshot.feature == feature
            and snapshot.generation == self.generation and snapshot.revision == self.revisions[feature]
    end
    if not current_selection() then return false, "The original paint selection expired" end
    if self.busy or not self:_allowed(snapshot.token) then return false, "Paint transfers are unavailable in the current scope" end
    if self.activating and not explicit or self.job and (explicit or self.job.explicit) then
        return false, "Another paint deposit is already running"
    end
    self:_finish("Replaced by a newer deposit action")
    local function current()
        assert(current_selection() and self:_allowed(snapshot.token), "Paint deposit scope changed")
        local bag, box = endpoints()
        assert(current_selection() and endpoint_id(bag) == snapshot.bagId and endpoint_id(box) == snapshot.boxId,
            "The open deposit box or backpack changed")
        return bag, box
    end
    current()
    local requested = 0
    for _, row in ipairs(snapshot.rows) do requested = requested + row.itemCount end
    self.job = { feature = feature, token = snapshot.token, rows = snapshot.rows, index = 1, moves = 0, endpoints = current,
        explicit = explicit, requested = requested, verified = 0 }
    return true
end

function M:_activate(token, endpoints)
    if self.activating then return false, "Deposit input is already being processed" end
    self.activating = true
    local ok, started, why = pcall(function()
        if self.busy or self.job then return false, "Another paint deposit is already running" end
        local snapshot, reason = self:_prepare(token, endpoints, "custom")
        if not snapshot then return false, reason end
        if not self:_submit(snapshot, endpoints, true) then return false, "The open box or player scope changed" end
        return true
    end)
    self.activating = false
    if not ok or not started then
        local reason = tostring(ok and why or started)
        notify(self.options.log, "Deposit all paints could not start: " .. reason)
        return false, reason
    end
    notify(self.options.log, "Deposit all paints queued " .. self.job.requested .. " backpack cans.")
    return true
end

function M:active()
    return not self.closed and (self.job ~= nil or self.widget and self.widget:active()) or false
end

function M:is_ui_visible()
    return not self.closed and self.widget and self.widget.visible == true or false
end

function M:probe(controller)
    if self.closed or not (self.enabled.stock or self.enabled.custom) or not self.widget or not self.widget.probe then return end
    local ok, reason = pcall(self.widget.probe, self.widget, controller)
    if not ok and not self.probe_error_reported then
        self.probe_error_reported = true
        notify(self.options.log, "Could not inspect the open deposit menu: " .. tostring(reason))
    end
end

function M:step(budget, elapsed_ms)
    if self.closed or self.busy or budget and budget:expired() then return end
    local job = self.job
    if not job then
        if self.widget and self.widget:active() then self.widget:step(budget, elapsed_ms) end
        return
    end
    self.busy = true
    local work = job.rows[job.index]
    local ok, count, remaining, skip_kind = pcall(transfer.deposit_open_slot, self.config, job.token, work, job.endpoints,
        function() return self.job == job and not self.closed and not job.stop_reason end)
    self.busy = false
    if budget then budget:consume(1) end
    if job ~= self.job then return end
    if not ok or count == nil then
        local reason = tostring(ok and remaining or count)
        self:_finish(reason)
        return
    end
    if count > 0 then
        job.moves, job.verified = job.moves + 1, job.verified + count
    elseif skip_kind == "changed_source" and not job.explicit then
        job.source_changed = true
    elseif not job.first_skip then
        job.first_skip = tostring(remaining)
    end
    if count > 0 and remaining > 0 then work.itemCount = remaining
    else job.index = job.index + 1 end
    if job.stop_reason then self:_finish(job.stop_reason)
    elseif job.index > #job.rows then self:_finish()
    elseif job.moves >= self.config.max_slots then self:_finish("Reached the per-inventory slot limit") end
end

-- A nil profile means that the current player's saved settings are pending.
function M:set_settings(settings)
    if self.closed then return end
    local stock = settings ~= nil and settings.depositSimilarAllColors == true
    local custom = settings ~= nil and settings.showDepositAllPaints == true
    if self.enabled.stock == stock and self.enabled.custom == custom then return end
    if self.enabled.stock ~= stock then self.revisions.stock = self.revisions.stock + 1 end
    if self.enabled.custom ~= custom then self.revisions.custom = self.revisions.custom + 1 end
    self.enabled.stock, self.enabled.custom = stock, custom
    local job = self.job
    if job and not self.enabled[job.feature] then
        job.stop_reason = "This paint deposit option was disabled"
        -- A native call already in progress must reconcile its result first.
        if not self.busy then self:_finish(job.stop_reason) end
    end
    if self.widget and self.enabled.stock == stock and self.enabled.custom == custom then self.widget:set_settings(settings) end
end

function M:reset()
    self.generation = self.generation + 1
    self:_finish("The container or player context changed")
    if self.widget then self.widget:reset() end
end

function M:close()
    self.closed = true
    self.generation = self.generation + 1
    self:_finish("PaintManager closed")
    if self.widget then self.widget:close() end
end

function M.install(config, options)
    local self = setmetatable({ config = config, options = options, generation = 0,
        revisions = { stock = 0, custom = 0 }, enabled = { stock = false, custom = false } }, M)
    local bindings = {
        game_build = options.game_build, guard = function(token) return self:_allowed(token) end, log = options.log,
        make_button = options.make_button,
        can_offer = function(token, endpoints) return self:_can_offer(token, endpoints) end,
        activate = function(token, endpoints) return self:_activate(token, endpoints) end,
        prepare = function(token, endpoints) return self:_prepare(token, endpoints, "stock") end,
        submit = function(snapshot, endpoints)
            if not snapshot then return false end
            local ok, started, reason = pcall(self._submit, self, snapshot, endpoints)
            if not ok or not started then
                notify(options.log, "Deposit Similar paint extension could not start: " .. tostring(ok and reason or started))
                return false
            end
            return true
        end,
        cancel = function() self:_finish("The open container changed") end,
    }
    local ok, widget = pcall(function()
        return require("paintmanager.native_deposit_widget").install(config, bindings)
    end)
    if ok then self.widget = widget
    else notify(options.log, "Paint deposit integration unavailable: " .. tostring(widget)) end
    return self
end

return M
