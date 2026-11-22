-- English messages owned by the command queue and scan coordinator.
return {
    -- TRANSLATORS: Verified outcome inserted in the status after learning one color's recipe.
    ["main.outcome.learned"] = "learned",
    -- TRANSLATORS: Recipe learning found that the character already knew the selected recipe.
    ["main.outcome.known"] = "already learned",
    -- TRANSLATORS: Status result when processing the requested batch of recipes completed.
    ["main.outcome.completed"] = "completed",
    -- TRANSLATORS: Batch outcome when there were no remaining recipes that could be learned.
    ["main.outcome.none"] = "no eligible recipes",
    -- TRANSLATORS: Preset application finished and the mod verified the target's resulting colors and empty zones.
    ["main.outcome.applied"] = "applied and verified",
    -- TRANSLATORS: Preset application result when the current paint layout already matches the saved one.
    ["main.outcome.matches"] = "already matches",
    -- TRANSLATORS: Outcome of a recipe or paint action for which the mod cannot confirm the result.
    ["main.outcome.uncertain"] = "the result could not be verified",
    -- TRANSLATORS: Result when a requested paint or recipe action was rejected before it could complete.
    ["main.outcome.rejected"] = "request rejected",
    -- TRANSLATORS: Receipt outcome when the requested action was cancelled.
    ["main.outcome.cancelled"] = "cancelled",
    -- TRANSLATORS: Outcome after some requested paint or recipe changes succeeded before the action stopped.
    ["main.outcome.partial"] = "partially completed",
    -- TRANSLATORS: Batch recipe learning stopped on an error before finishing the remaining recipes.
    ["main.outcome.stopped"] = "stopped after an error",
    -- TRANSLATORS: Batch recipe learning stopped after an uncertain result; no further recipes were attempted.
    ["main.outcome.stopped_unverified"] = "stopped because a result could not be verified",
    -- TRANSLATORS: Player-facing fallback when an internal operation fails without a more specific user-actionable reason.
    ["main.reason.failed"] = "The operation could not be completed. See the log for details.",
    -- TRANSLATORS: Transfer could not fit a paint can in its destination, which can be a storage box or the backpack.
    ["main.reason.destination_full"] = "The destination inventory has no room for this paint.",
    -- TRANSLATORS: Applying a saved layout would remove paint and needs an empty backpack slot with enough capacity.
    ["main.reason.backpack_full"] = "Make room in your backpack for the removed paint.",
    -- TRANSLATORS: The player cannot complete the requested paint layout or transfer with the available physical cans.
    ["main.reason.shortage"] = "There are not enough cans of the required colors in your backpack and accessible ship boxes.",
    -- TRANSLATORS: Returning paint to its original box requires identifying and accessing that same box again.
    ["main.reason.origin"] = "The original box could not be verified. Check that it is loaded and accessible.",
    -- TRANSLATORS: Transfer controls are unavailable because the player's backpack could not be read.
    ["main.reason.backpack"] = "Could not check your backpack. Reopen the window to try again.",
    -- TRANSLATORS: Transfer controls could not load or reconcile the record of which boxes supplied the player's cans.
    ["main.reason.origins"] = "Could not verify the recorded origins of your paint cans.",
    -- TRANSLATORS: The source library or its snapshot changed before the proposed edit could be saved.
    ["main.reason.library_changed"] = "Library preview changed. Refresh the saved configurations and try again.",
    -- TRANSLATORS: A saved preset library failed validation. The player should check the cause before another edit.
    ["main.reason.library_invalid"] = "Invalid preset library. Check the log before editing it.",
    -- TRANSLATORS: A persistence or preset library error has no more specific verified player-facing cause.
    ["main.reason.storage"] = "Saved data could not be verified. Check the log for details.",
    -- TRANSLATORS: Transfer controls cannot start until the current character's backpack and eligible paint storage are available.
    ["main.transfer.not_ready"] = "Waiting for the current backpack and paint storage.",
    -- TRANSLATORS: Shown when a gather or return action is disabled because the installed game build has not been verified.
    ["main.transfer.build_unverified"] = "Paint transfers require the verified game build.",
    -- TRANSLATORS: Gather/return refusal when none of the selected paint can be moved under the current stock or return rules.
    ["main.transfer.unavailable"] = "No eligible paint is available for this transfer.",
    -- TRANSLATORS: Gather selected paint button. {count} is the configured target for this color in the backpack; the current stock already meets it.
    -- PLURAL count: "Your backpack already holds the target of {count} can or more."
    ["main.transfer.target_reached"] = "Your backpack already holds the target of {count} cans or more.",
    -- TRANSLATORS: Transfer receipt shown after gathering or returning paint. {moved} counts confirmed moves; {skipped} counts candidates skipped.
    ["main.transfer.result"] = "Confirmed moves: {moved}. Skipped: {skipped}.",
    -- TRANSLATORS: Appended to the transfer receipt after an uncertain move; further transfers are blocked until the save is reloaded.
    ["main.transfer.uncertain"] = "The last move could not be verified. Further transfers require reloading the save.",
    -- TRANSLATORS: Appended to the move receipt if some storage could not be read. Confirmed moves still count.
    ["main.transfer.incomplete"] = "Some storage could not be checked.",
    -- TRANSLATORS: Live status during a gather or return operation. {moved} is the number of confirmed moves so far.
    ["main.transfer.running"] = "Moving paint cans. Moved: {moved}.",
    -- BEGIN ENGLISH ONLY
    ["main.processing.pending"] = "Processing settings are waiting for current work and publications to settle.",
    ["main.processing.applied"] = "Processing settings applied for this session.",
    ["main.processing.invalid"] = "Processing settings failed: {error}",
    ["main.processing.imported"] = "Profile loaded into the draft. Apply to use it.",
    ["main.processing.imported_legacy"] = "Old profile migrated into the draft. Retired settings are listed in the log. Apply to use it.",
    ["main.processing.exported"] = "Developer profile saved: {path}",
    ["main.processing.action_queued"] = "Developer processing action accepted.",
    ["main.phase.paint_cache"] = "Preparing known paints; stock has not been captured",
    ["main.phase.storage_read"] = "Reading the account library",
    ["main.phase.storage_check"] = "Checking saved configurations",
    ["main.phase.storage_merge"] = "Reconciling saved configurations",
    ["main.phase.storage_write"] = "Saving the account library",
    ["main.preset.deleted"] = "Configuration deleted locally. Its deletion marker is retained.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Persistence notice on the presets page. The account library was saved, but the in-game copy is still pending. {error} is optional storage detail.
    ["main.storage.local_pending"] = "Saved locally; game mirror pending. Reopen the window or refresh configurations to reconcile. {error}",
    -- TRANSLATORS: Presets storage warning after publishing the local file. {error} gives the verification or cleanup failure, if available.
    ["main.storage.local_verification_uncertain"] = "Local JSON was published, but verification or cleanup failed; mirror pending. Refresh before editing. {error}",
    -- TRANSLATORS: Presets storage warning after an uncertain write to the in-game mirror. {error} is optional detail about that attempt.
    ["main.storage.local_uncertain"] = "Saved locally; game mirror uncertain. Further native attempts are blocked in this context. {error}",
    -- TRANSLATORS: Presets storage notice after updating the account library and staging its mirror in the current save slot. The ordinary game save is still needed.
    ["main.storage.local_staged"] = "Local library saved; mirror staged in the loaded game. Save the game normally to persist it in this slot.",
    -- TRANSLATORS: Presets storage notice while confirmation of a local write is outstanding.
    ["main.storage.publication_pending"] = "Local publication is awaiting confirmation. Refresh configurations to check the library and game mirror.",
    -- BEGIN ENGLISH ONLY
    ["main.storage.waiting_restore"] = "Storage is waiting for verified player-progress restoration. {error}",
    ["main.storage.loaded"] = "Mod data loaded from this save's player progress. Cloud transfer is managed by the game platform.",
    ["main.storage.empty"] = "This save has no mod data yet. Changes will be included in the next ordinary game save.",
    ["main.storage.staged"] = "Mod data staged in player progress. Save the game normally to retain it; Cloud upload is not verified.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Persistence warning on the presets page. {error} is the detail returned by the storage operation.
    ["main.storage.unavailable"] = "Mod storage unavailable: {error}",
    -- TRANSLATORS: Import refusal when the loaded game's mod data is not yet available.
    ["main.storage.not_ready"] = "Wait for mod data to load. If storage failed, inspect the error before changing this save.",
    -- TRANSLATORS: Presets import was requested while another command or preference write was in progress.
    ["main.storage.busy"] = "Wait for the current command to finish before importing configurations.",
    -- TRANSLATORS: Shown on the presets page or when importing on a game build for which persistence has not been verified.
    ["main.storage.build"] = "Saving PaintManager settings and presets requires the verified game build.",
    -- BEGIN ENGLISH ONLY
    ["main.phase.state_read"] = "Reading this save's mod data",
    ["main.phase.state_decode"] = "Checking mod data versions",
    ["main.phase.state_change"] = "Preparing mod data",
    ["main.phase.state_encode"] = "Preparing mod data for saving",
    ["main.phase.state_stage"] = "Staging mod data for the next game save",
    ["main.phase.state_import"] = "Reading local configurations for import",
    ["preset.target.character"] = "Character · {name}",
    ["preset.target.boat"] = "Boat · {name} · {owner}",
    ["preset.zone.empty"] = "Empty",
    ["preset.zone.unknown"] = "Unknown paint",
    ["preset.paint.unavailable"] = "Game display name unavailable · {id}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Result of applying a saved paint layout. {outcome} is the operation outcome, including failures and partial changes.
    ["main.preset.result"] = "Preset result: {outcome}",
    -- TRANSLATORS: Presets action requires a freshly read paint state and a current target: either character or ship.
    ["main.preset.refresh_required"] = "Refresh paint state and select the current target first.",
    -- TRANSLATORS: Saving/applying a preset failed because the character or ship paint zones could not all be read. May also appear in the game's paint editor.
    ["main.preset.target_incomplete"] = "The selected paint target is incomplete; inspect diagnostics and refresh.",
    -- TRANSLATORS: Validation of a new preset name in PaintManager or the game's paint editor. The limit is in bytes rather than characters.
    ["main.preset.name_invalid"] = "Enter a configuration name (1–128 UTF-8 bytes, no control characters).",
    -- BEGIN ENGLISH ONLY
    ["main.preset.saved"] = "Configuration saved to the local library. Check the mirror status above.",
    ["main.preset.already_saved"] = "The current paint layout already matches a saved configuration.",
    ["main.preset.refreshed"] = "Current paint zones and saved configurations refreshed.",
    ["main.phase.preset_load"] = "Reading saved configurations",
    ["main.phase.preset_verify"] = "Checking the saved preset against current storage",
    ["main.phase.preset_store"] = "Saving configuration",
    ["main.phase.preset_capture"] = "Reading current paint zones",
    ["main.phase.preset_apply"] = "Checking paint and applying the saved layout",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Fallback refusal when a requested action, including a paint transfer, does not match a known command.
    ["main.queue.unknown"] = "Unknown command",
    -- TRANSLATORS: Command refusal while an indivisible operation is in progress.
    ["main.queue.atomic_busy"] = "Another atomic operation is running",
    -- BEGIN ENGLISH ONLY
    ["main.queue.already_queued"] = "Already queued",
    ["main.queue.refresh_running"] = "Collection refresh is already running",
    -- END ENGLISH ONLY
    -- TRANSLATORS: A player action could not be queued because the pending command limit was reached.
    ["main.queue.full"] = "Command queue is full",
    -- BEGIN ENGLISH ONLY
    ["main.queue.queued"] = "Queued",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Fallback shown when an action cannot identify the character or game world currently in use.
    ["main.error.no_character"] = "No valid current character or world",
    -- TRANSLATORS: A scan or transfer stopped when the character/world no longer matched the one for which stock was read.
    ["main.error.context_changed"] = "Collection context changed; reopen the window to refresh nearby containers",
    -- TRANSLATORS: Shared fallback when a stock scan, preset inspection or save operation failed and supplied no specific detail; it can appear in the game's paint editor.
    ["main.error.job_failed"] = "Collection job failed",
    -- TRANSLATORS: Error fallback after a completed stock scan returned no results model.
    ["main.error.missing_model"] = "Completed collection job returned no model",
    -- TRANSLATORS: Action refused or interrupted because the player is outside the ship connection or the ship cannot be confirmed.
    ["main.connection.lost"] = "Signal lost. Move closer to the ship.",
    -- TRANSLATORS: Fallback error when opening PaintManager requires a loaded save and a player-controlled character.
    ["main.error.load_character"] = "Load a save and leave vehicle controls",
    -- TRANSLATORS: Fallback for a command interrupted by an error while drawing the PaintManager window.
    ["main.error.render_failed"] = "UI rendering failed",
    -- BEGIN ENGLISH ONLY
    ["main.phase.context"] = "Checking the world",
    ["main.phase.bulk_prepare"] = "Preparing available recipes",
    ["main.phase.bulk_learn"] = "Learning available recipes",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Learn all refusal. No unlearned recipe has at least four cans of its color across eligible stock.
    ["main.bulk.none"] = "No locked recipe has four matching cans in your backpack and ship boxes combined.",
    -- BEGIN ENGLISH ONLY
    ["main.bulk.queued"] = "Checking available recipes; learning will run in bounded portions and keep all resources.",
    ["main.bulk.progress"] = "{phase} · recipes: {completed}/{total}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Learn all receipt. {outcome} is the batch result; {learned}/{total} counts learned versus initially eligible recipes; {known}, {rejected} and {unverified} are other result counts.
    ["main.bulk.result"] = "Batch {outcome}: learned {learned}/{total}; already known {known}; rejected {rejected}; unverified {unverified}.",
    -- TRANSLATORS: Learn all was interrupted after a native attempt. {outcome} identifies its result; completed recipe grants remain in effect.
    ["main.bulk.after_attempt"] = "Batch stopped: {outcome}. Recipes already granted remain learned; see the report.",
    -- BEGIN ENGLISH ONLY
    ["main.phase.learn_prepare"] = "Checking recipe learning eligibility",
    ["main.phase.learn_commit"] = "Rechecking live cans and learning recipe",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Recipe learning refusal after the current player or world changed.
    ["main.learn.context_changed"] = "The player context changed. Reopen the window before learning.",
    -- TRANSLATORS: An action was requested while the PaintManager window was closing. The existing English is also reused for some preset and transfer actions.
    ["main.learn.window_closed"] = "The window closed before recipe learning started.",
    -- TRANSLATORS: Recipe learning refusal on an unsupported build. The existing English is also reused by the preset-apply guard.
    ["main.learn.build_unverified"] = "Recipe learning is unavailable because the installed game build is not the verified reference build.",
    -- TRANSLATORS: A shared busy guard refuses recipe learning; the existing English is also reused for preset and paint transfer actions.
    ["main.learn.busy"] = "Wait for the current command to finish before learning a recipe.",
    -- TRANSLATORS: Learn refusal when the current paint count is still missing or belongs to a different scan range.
    ["main.learn.refresh_required"] = "Reopen the window and wait for the ship stock count before learning.",
    -- TRANSLATORS: A fresh stock check contradicted the earlier count for one recipe. Four matching physical cans are required.
    ["main.learn.cached_stock_changed"] = "Could not confirm four matching cans in your backpack and the scanned ship boxes. Reopen the window to refresh.",
    -- TRANSLATORS: Eligibility failure for one recipe: it must be locked and stock must contain four matching cans in total.
    ["main.learn.not_eligible"] = "Learning requires a locked recipe and four matching cans in your backpack and ship boxes combined.",
    -- BEGIN ENGLISH ONLY
    ["main.learn.queued"] = "Checking current cans and recipe state before learning. Physical cans are kept.",
    ["main.learn.verified"] = "Recipe membership verified: {outcome}. Physical cans are kept.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Single-recipe error after a failed or unverified operation. {outcome} is the result or detailed error text.
    ["main.learn.failed"] = "Recipe learning result: {outcome}. Check the report and log before retrying.",
    -- TRANSLATORS: Shown after interruption if a recipe-learning call already happened. {outcome} is that attempt's result.
    ["main.learn.after_attempt"] = "The recipe call was already attempted: {outcome}. Closing or changing radius does not undo it.",
    -- BEGIN ENGLISH ONLY
    ["main.phase.recipe_library"] = "Reading the recipe library",
    ["main.phase.recipe_resolve"] = "Resolving recipe entries",
    ["main.phase.catalog_discovery"] = "Finding paints",
    ["main.phase.paint_metadata"] = "Reading paint names and recipes",
    ["main.phase.source_discovery"] = "Finding containers",
    ["main.phase.source_filter"] = "Checking containers",
    ["main.phase.container_read"] = "Reading containers",
    ["main.phase.aggregate"] = "Counting paint",
    ["main.phase.stock_processing"] = "Processing captured stock",
    ["main.phase.render"] = "Building the list",
    ["main.phase.report_encode"] = "Preparing the report",
    ["main.phase.report_write"] = "Writing the report",
    ["main.phase.snapshot"] = "Reading diagnostic snapshot",
    ["main.phase.refresh"] = "Refreshing the list",
    ["main.progress.count"] = "{phase} · {completed}",
    ["main.progress.total"] = "{phase} · {completed}/{total}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Status when changing the ship radius cancels the ongoing paint count. Reopening starts a count for the new range.
    ["main.cancel.radius_changed"] = "Range changed. Reopen the window to count paint in the new range.",
    -- TRANSLATORS: Fallback cancellation reason for a stopped refresh when no more specific player-facing explanation is available.
    ["main.cancel.default"] = "Refresh cancelled",
    -- TRANSLATORS: Cancellation reason when closing PaintManager interrupts an operation. A transfer receipt can include this message.
    ["main.cancel.window_closed"] = "Window closed; unfinished refresh cancelled",
    -- TRANSLATORS: Cancellation reason when the player closes the window while an action is still in the queue.
    ["main.cancel.before_start"] = "Window closed; command cancelled before starting",
    -- BEGIN ENGLISH ONLY
    ["main.cancel.toggle"] = "Opening or closing the window",
    ["main.cancel.toggle_staged"] = "Command cancelled before starting while opening or closing the window",
    ["main.status.legacy_snapshot"] = "Reading diagnostic snapshot in portions",
    ["main.status.refreshed"] = "Collection refreshed",
    ["main.status.partial"] = "Some paint data is unavailable. Reopen the window to retry.",
    ["main.settings.queued"] = "Saving preferences...",
    ["main.settings.unavailable"] = "Preferences are not ready: {reason}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Shown if the player changes a preference or favorite before preferences have loaded.
    ["main.settings.not_ready"] = "Wait for preferences to finish loading.",
    -- TRANSLATORS: Saving a setting or favorite was rejected because too many changes are already queued.
    ["main.settings.queue_full"] = "Too many pending changes. Try again shortly.",
    -- TRANSLATORS: An invalid preference was rejected; also used for an invalid backpack target when gathering paint.
    ["main.settings.invalid"] = "This setting is unavailable.",
    -- TRANSLATORS: Favorite action rejected because PaintManager cannot identify the selected paint reliably yet.
    ["main.settings.invalid_paint"] = "This paint cannot be added to favorites yet.",
    -- TRANSLATORS: Preference write failed or could not be verified. {error} is the returned reason.
    ["main.settings.failed"] = "Preferences could not be confirmed: {error}",
    -- BEGIN ENGLISH ONLY
    ["main.settings.developer_disabled"] = "Enable developer tools in Settings first.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Cancellation notice after the player hides developer tools using the checkbox in ordinary Settings.
    ["main.settings.developer_hidden"] = "Developer tools were hidden.",
    -- BEGIN ENGLISH ONLY
    ["main.status.opened"] = "Window opened",
    ["main.status.closed"] = "Window closed",
    ["main.status.jmap_returned"] = "DumpJMAP returned; see the receipt and UE4SS.log for the actual JMAP file",
    -- END ENGLISH ONLY
}
