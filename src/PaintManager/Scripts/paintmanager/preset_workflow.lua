-- Sequenced persistence/current-state jobs. No filesystem work runs at startup.
local presets = require("paintmanager.presets")
local store = require("paintmanager.preset_store")
local json = require("paintmanager.json")
local M = {}
local Job = {}; Job.__index = Job
local function failure(progress, fallback)
    local message = progress.error or fallback
    if #(progress.cleanupErrors or {}) > 0 then
        message = message .. "; recovery details: " .. table.concat(progress.cleanupErrors, "; ")
    end
    return message
end

local function find(rows, id)
    for _, row in ipairs(rows or {}) do if row.id == id then return row end end
end
function M.begin(config, path, action, request, storage)
    storage = storage or store
    local job = setmetatable({ config = config, path = path, action = action, request = request,
        state = "running", processed = 0, storage = storage }, Job)
    if action == "preset_save" then
        job.store_layout = storage.begin_save_layout
    end
    if action == "preset_refresh" and request.initialLibrary then
        -- Same-callback handoff from a completed state reconciliation. This
        -- supplies browsing data only. Every explicit operation owns its proof.
        job.library, job.storage_snapshot = request.initialLibrary, request.storageSnapshot
        job.phase, job.child = "preset_capture", presets.begin_capture(config, nil, request.shipOwnerId)
    elseif job.store_layout then
        -- The guarded store reads and compares the merged library in the same
        -- operation that publishes it. A preliminary reconciliation is redundant.
        job.phase, job.child = "preset_capture", presets.begin_capture(config, request.target and request, request.shipOwnerId)
    elseif action == "preset_apply" and request.view and request.storageSnapshot == request.view.storageSnapshot
        and storage.has_verified_preview and storage.has_verified_preview(request.storageSnapshot) then
        -- This completed preview came from both matching replicas. The proof job
        -- compares their exact bytes and locks the primary before the first transfer.
        job.storage_snapshot = request.storageSnapshot
        job.reused_preview = true
        job.child, job.phase = storage.begin_verify_snapshot(job.storage_snapshot, true), "preset_verify"
    else
        job.phase, job.child = "preset_load", storage.begin_load(path)
    end
    return job
end
function Job:get_receipt() return self.receipt or self.apply and self.apply:get_receipt() end
function Job:get_publication() return self.publication end
function Job:_release_proof()
    local snapshot = self.proof_snapshot
    self.proof_snapshot = nil
    if snapshot and self.storage.release_snapshot then self.storage.release_snapshot(snapshot) end
end
function Job:_finish_view()
    self.view.library = self.library
    self.view.storageSnapshot = self.storage_snapshot
    self.state, self.phase = "done", "done"
end
function Job:_save_record()
    if self.request.connection_guard then
        local connected, reason = self.request.connection_guard()
        assert(connected, reason or "Ship connection lost before saving")
    end
    self.request.newId = self.request.newId or self.storage.new_id()
    return presets.from_target(self.save_target, self.request.name, self.request.newId, self.request.gameBuild)
end
function Job:_begin_apply(preset)
    local guard
    if self.storage.verify_snapshot then
        local snapshot = self.proof_snapshot or self.storage_snapshot
        guard = function() return self.storage.verify_snapshot(snapshot) end
        local fresh, reason = guard(); assert(fresh, reason)
    end
    self.apply = presets.begin_apply(self.config, { preset = preset, target = self.request.target,
        gameBuild = self.request.gameBuild, contextToken = self.request.contextToken,
        persistence_guard = guard, connection_guard = self.request.connection_guard, stockModel = self.request.stockModel })
    self.child, self.phase = self.apply, "preset_apply"
end
function Job:cancel(reason)
    if self.child then
        local progress = self.child:cancel(reason)
        if progress and (progress.localCommitted or progress.publicationPending) then self.publication = progress end
    end
    if self.apply then self.apply:cancel(reason); self.receipt = self.apply:get_receipt() end
    self:_release_proof()
    self.state, self.error, self.child = "cancelled", reason, nil
end
function Job:step(budget)
    if self.state ~= "running" or budget and budget:expired() then
        return { state = self.state, phase = self.phase, error = self.error }
    end
    local ok, err = pcall(function()
        if self.phase == "preset_load" then
            local progress = self.child:step(budget)
            if progress.state == "running" then return end
            assert(progress.state == "done", failure(progress, "Preset library could not be loaded"))
            self.library = assert(self.child:get_result(), "Preset library result missing")
            self.storage_snapshot = progress.snapshot
            if self.action ~= "preset_save" then self.publication = progress end
            if self.action == "preset_delete" then
                assert(find(self.library.presets, self.request.presetId), "Saved preset no longer exists")
                self.view = assert(self.request.view, "Refresh the configuration view before deleting")
                self.child, self.phase = self.storage.begin_delete(self.request.presetId), "preset_store"
            elseif self.action == "preset_apply" then
                local preset = assert(find(self.library.presets, self.request.presetId), "Saved preset no longer exists")
                if self.storage.begin_verify_snapshot then
                    self.selected_preset = preset
                    self.child, self.phase = self.storage.begin_verify_snapshot(self.storage_snapshot), "preset_verify"
                else
                    self:_begin_apply(preset)
                end
            else
                local selected = self.action == "preset_save" and self.request.target and self.request or nil
                self.child, self.phase = presets.begin_capture(self.config, selected, self.request.shipOwnerId), "preset_capture"
            end
        elseif self.phase == "preset_verify" then
            local progress = self.child:step(budget)
            if progress.state == "running" then return end
            assert(progress.state == "done", failure(progress, "Configuration source proof failed"))
            self.proof_snapshot = assert(progress.snapshot, "Background source proof returned no lease")
            if not self.selected_preset then
                local verified = assert(self.child:get_result(), "Verified configuration library unavailable")
                self.library = verified.sections.presets.data
                self.selected_preset = assert(find(self.library.presets, self.request.presetId), "Saved preset no longer exists")
            end
            self:_begin_apply(self.selected_preset)
            self.selected_preset = nil
        elseif self.phase == "preset_apply" then
            local progress = self.apply:step(budget)
            if progress.state == "running" then return end
            assert(progress.state == "done", progress.error or "Preset application failed")
            self.receipt = self.apply:get_receipt()
            self.receipt.storageVerification = self.reused_preview and "verified_preview" or "library_reload"
            self:_release_proof()
            self.child, self.phase = presets.begin_capture(self.config, nil, self.request.shipOwnerId), "preset_recapture"
        elseif self.phase == "preset_capture" or self.phase == "preset_recapture" then
            local progress = self.child:step(budget)
            if progress.state == "running" then return end
            assert(progress.state == "done", progress.error or "Current paint state could not be read")
            self.view = progress.view
            if self.action == "preset_save" and self.phase == "preset_capture" then
                local target = assert(find(self.view.targets, self.request.targetId), "Selected target changed; refresh paint state")
                assert(self.view.contextToken == self.request.contextToken, "Preset save context changed")
                assert(target.complete and target.compatibility and target.logicalZones, "Selected paint target is incomplete")
                self.save_target = target
                self.publication = nil
                -- The untouched targets remain display-only. Every action reads
                -- its own selected target again before using its configuration.
                if self.request.view then
                    local targets = json.array()
                    for index, other in ipairs(self.request.view.targets) do
                        targets[index] = other.id == target.id and target or other
                    end
                    self.view.targets = targets
                end
                if self.store_layout then
                    self.child, self.phase = self.store_layout(self:_save_record()), "preset_store"
                else
                    self.save_index, self.save_presets = 1, json.array()
                    self.child, self.phase = nil, "preset_compare"
                end
            else
                self:_finish_view()
            end
        elseif self.phase == "preset_compare" then
            -- The target and library were read again after the Save click.
            local existing = self.library.presets[self.save_index]
            if not existing then
                local preset = self:_save_record()
                self.save_presets[#self.save_presets + 1] = preset
                self.child = self.storage.begin_add and self.storage.begin_add(preset) or
                    self.storage.begin_save(self.path, { schemaVersion = 1, presets = self.save_presets })
                self.phase = "preset_store"
            elseif presets.compare_layout(self.save_target, existing) == "match" then
                self.saved_id, self.save_skipped = existing.id, true
                self:_finish_view()
            else
                self.save_presets[self.save_index] = existing
                self.save_index = self.save_index + 1
            end
        elseif self.phase == "preset_store" then
            local progress = self.child:step(budget)
            self.publication = progress
            if progress.state == "running" then return end
            assert(progress.state == "done", failure(progress, "Preset library could not be saved"))
            self.library = self.child:get_result()
            self.storage_snapshot = progress.snapshot
            self.publication = progress
            if self.save_target then
                self.saved_id, self.save_skipped = progress.matchedPresetId or self.request.newId, progress.matchedPresetId ~= nil
                assert(find(self.library.presets, self.saved_id), "Saved layout missing from storage result")
            end
            self:_finish_view()
        end
    end)
    self.processed = self.processed + 1
    if not ok then
        if self.child then pcall(function() self.child:cancel(tostring(err)) end) end
        pcall(function() self:_release_proof() end)
        self.receipt = self:get_receipt()
        self.state, self.error = "error", tostring(err)
    end
    return { state = self.state, phase = self.phase, processed = self.processed, total = self.processed + 1,
        view = self.state == "done" and self.view or nil, receipt = self.receipt, error = self.error,
        publication = self.publication, saveSkipped = self.save_skipped, savedId = self.saved_id }
end
return M
