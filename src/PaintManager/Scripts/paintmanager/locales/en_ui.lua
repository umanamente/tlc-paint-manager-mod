-- Authored PaintManager UI text. Native game display names are never translated here.
-- Keep keys stable and preserve named placeholders when adding a future locale.
return {
    -- TRANSLATORS: Status-line action name for opening, closing or refreshing the mod window.
    ["ui.command.window"] = "PaintManager window",
    -- TRANSLATORS: Status-line action name while saving or applying the player's preferences.
    ["ui.command.preferences"] = "Settings",
    -- TRANSLATORS: General status-line action name when the underlying command has no player-visible name.
    ["ui.command.task"] = "PaintManager action",
    -- TRANSLATORS: Label for the language selector in the mod's Settings. It controls messages written by PaintManager.
    ["ui.settings.language"] = "PaintManager language",
    -- TRANSLATORS: Default option in the language selector. Use the language of the game's interface when available and otherwise show English.
    ["ui.settings.language_auto"] = "Follow game language",
    -- TRANSLATORS: Name of this translation in its own language. The Settings selector shows this name even when the rest of the interface is in a different language.
    ["ui.settings.language_name"] = "English",
    -- TRANSLATORS: Settings tooltip for the language selector. Mention the automatic option using its label and explain when English is shown.
    ["ui.settings.help.language"] = "Choose the language for PaintManager. Follow game language uses the game's interface language when a translation is available, and English otherwise.",
    -- BEGIN ENGLISH ONLY
    ["ui.cache.stock"] = "Stock has not been captured. Recipe checks refer to the current character.",
    ["ui.cache.summary"] = "Known paints checked: {verified} · pending/unloaded: {pending}\nPreparing the known catalog; discovery is not exhaustive. Stock has not been captured.",
    ["ui.paint.quantity_unknown"] = "? pcs.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Button in the selected preset details. Deletes the saved paint layout from the library.
    ["ui.preset.delete"] = "Delete",
    -- TRANSLATORS: Help for the preset Delete button. Deletion affects the saved layout only.
    ["ui.preset.delete_help"] = "Permanently delete the selected saved configuration. Paints and current character or boat appearance are unchanged.",
    -- BEGIN ENGLISH ONLY
    ["ui.storage.waiting"] = "Configurations share this account's local library. The game mirror still needs an ordinary game save.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Button on the presets page for importing saved paint configurations from local storage.
    ["ui.storage.import"] = "Import",
    -- TRANSLATORS: Main window title. This is the mod name, styled in all capitals.
    ["ui.title"] = "PAINT MANAGER",
    -- BEGIN ENGLISH ONLY
    ["ui.subtitle"] = "Paint recipes and nearby stock",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Dismisses the main window or the native paint editor's finished save dialog.
    ["ui.button.close"] = "Close",
    -- TRANSLATORS: Button added to the game's paint editor. Opens the mod's main window.
    ["ui.button.open_paintmanager"] = "Open PaintManager",
    -- TRANSLATORS: Main toolbar button for rereading the current paint configuration and saved presets. Also names a refresh in the status line.
    ["ui.button.refresh"] = "Refresh",
    -- TRANSLATORS: Button on the selected paint card. Learns that color's recipe when four matching cans are available and keeps the cans.
    ["ui.button.learn_recipe"] = "Learn",
    -- TRANSLATORS: Button above the paint list. Learns all eligible recipes, including those hidden by search or pagination.
    ["ui.button.learn_all"] = "Learn all",
    -- TRANSLATORS: Status-line name for transferring the selected paint color from ship boxes to the player's backpack.
    ["ui.transfer.gather_paint"] = "Move selected paint to backpack",
    -- TRANSLATORS: Selected paint button. {quantity} is the desired total number of cans of this color in the backpack after transfer.
    ["ui.transfer.gather_target"] = "Top up to {quantity}",
    -- TRANSLATORS: Selected paint button when the backpack target is All. Takes only this color, up to available space.
    ["ui.transfer.gather_color"] = "Take all of this color",
    -- TRANSLATORS: Selected paint button. Returns one can previously brought by the mod to the box it came from.
    ["ui.transfer.return_paint"] = "Return to original box",
    -- TRANSLATORS: Bulk transfer button. Moves all colors from boxes in ship range to the player's backpack, up to capacity.
    ["ui.transfer.gather_all"] = "Move all paints to backpack",
    -- TRANSLATORS: Bulk return action, also available on the signal-lost page. Returns cans previously moved by the mod to their original boxes.
    ["ui.transfer.return_all"] = "Return all paints to boxes",
    -- TRANSLATORS: Button added to the game's open storage box. Deposits cans of every color from the backpack into this box.
    ["ui.transfer.deposit_all_paints"] = "Deposit all paints",
    -- TRANSLATORS: Help for the selected paint transfer. {quantity} is the target backpack total for this color, including cans already carried.
    ["ui.transfer.gather_target_help"] = "Take this color from ship storage until you have {quantity} in your backpack, or as many as fit.",
    -- TRANSLATORS: Help for taking all cans of one selected color when the target quantity is All.
    ["ui.transfer.gather_color_help"] = "Take as many cans of this color from ship storage as your backpack can hold.",
    -- TRANSLATORS: Help for returning one can of the selected color. The mod remembers which box supplied the can.
    ["ui.transfer.return_paint_help"] = "Return one can brought by PaintManager to its original box.",
    -- TRANSLATORS: Help for the bulk gather action. Extra cans of the same color are included.
    ["ui.transfer.gather_all_help"] = "Move as many cans as fit from containers in ship range, including duplicate colors.",
    -- TRANSLATORS: Help for the bulk return action. The original box can be out of range but must still be loaded in the game.
    ["ui.transfer.return_all_help"] = "Return cans brought by PaintManager to their original boxes, including loaded boxes outside ship range.",
    -- TRANSLATORS: Selected paint card. {count} is the number of cans of this color already in the backpack.
    ["ui.transfer.backpack_count"] = "Backpack: {count}",
    -- TRANSLATORS: Temporary label on the selected paint card while the mod reads the backpack contents.
    ["ui.transfer.backpack_loading"] = "Checking backpack...",
    -- BEGIN ENGLISH ONLY
    ["ui.button.back"] = "Back",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Paint list tab containing colors whose recipes the character has already learned.
    ["ui.tab.learned"] = "Learned",
    -- TRANSLATORS: Paint list tab containing unlearned recipes with enough matching cans to learn now.
    ["ui.tab.ready"] = "Can Learn",
    -- TRANSLATORS: Paint list tab for unlearned recipes for which the player still needs matching cans.
    ["ui.tab.missing"] = "Need Cans",
    -- TRANSLATORS: Navigation tab for ordinary player preferences, including transfers, appearance and scan radius.
    ["ui.tab.settings"] = "Settings",
    -- BEGIN ENGLISH ONLY
    ["ui.tab.debug"] = "Debug",
    ["ui.debug.title"] = "PaintManager  /  Debug",
    ["ui.debug.section.processing"] = "Processing",
    ["ui.debug.section.diagnostics"] = "Diagnostics",
    ["ui.debug.section.profiles"] = "Profiles",
    ["ui.debug.reports"] = "Reports",
    ["ui.debug.discovery"] = "Metadata discovery",
    ["ui.debug.measurements"] = "Measurements",
    ["ui.debug.preparation"] = "Preparation",
    ["ui.debug.caches"] = "Caches",
    ["ui.debug.pause"] = "Pause",
    ["ui.debug.resume"] = "Resume",
    ["ui.debug.stage_profile"] = "Stage a processing profile",
    ["ui.debug.profile_file"] = "Profile file",
    ["ui.debug.clean"] = "No pending changes",
    ["ui.debug.one_change"] = "1 unsaved change",
    ["ui.debug.changes"] = "{count} unsaved changes",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Paint category tab caption. {name} is the localized tab name and {count} is the number of colors in that category.
    ["ui.tab.count"] = "{name} ({count})",
    -- TRANSLATORS: Settings checkbox for displaying each paint name below its tile in the grid.
    ["ui.settings.names"] = "Show paint names",
    -- TRANSLATORS: Checkbox in ordinary Settings that reveals or hides the English-language Debug tab.
    ["ui.settings.developer"] = "Show developer tools",
    -- TRANSLATORS: Settings control for switching the size of paint tiles in the grid.
    ["ui.settings.density"] = "Tile size",
    -- TRANSLATORS: Current tile size on the Settings button. {value} is the translated Compact or Comfortable size option.
    ["ui.settings.density_value"] = "Tiles: {value}",
    -- TRANSLATORS: Smaller tile size value in Settings. Used in the caption for the plural noun meaning paint tiles.
    ["ui.settings.compact"] = "Compact",
    -- TRANSLATORS: Larger tile size value in Settings. Used in the caption for the plural noun meaning paint tiles.
    ["ui.settings.comfortable"] = "Comfortable",
    -- TRANSLATORS: Settings control for cycling through sort orders for paints.
    ["ui.settings.sort"] = "Sort order",
    -- TRANSLATORS: Settings label above distance options. The radius is measured from the ship for both storage scans and connection.
    ["ui.settings.ship_radius"] = "Ship scan radius",
    -- TRANSLATORS: Settings heading for the single-color transfer target. Counts cans already in the backpack toward the target.
    ["ui.settings.backpack_target"] = "Backpack target for selected paint",
    -- TRANSLATORS: Settings section for moving paint cans between the backpack and storage boxes.
    ["ui.settings.paint_transfers"] = "Paint transfers",
    -- TRANSLATORS: Settings checkbox that extends the game's Deposit similar action to match paint of every color if the destination already has paint.
    ["ui.settings.deposit_similar_all_colors"] = "Group all colors in Deposit similar",
    -- TRANSLATORS: Settings checkbox controlling the extra paint-only button on a game's open storage box. Use the same button name as ui.transfer.deposit_all_paints.
    ["ui.settings.show_deposit_all_paints"] = "Show Deposit all paints button",
    -- TRANSLATORS: Help for the player option that changes the game's existing Deposit similar action. This applies to different paint colors.
    ["ui.settings.help.depositSimilarAllColors"] = "When a box already holds paint, Deposit similar also moves backpack paints of other colors. Turn this off to use the game's usual matching rules.",
    -- TRANSLATORS: Help for showing the mod's paint-only button in the game's open storage menu. Both inventories must contain paint.
    ["ui.settings.help.showDepositAllPaints"] = "Show a separate paint-only button in the open box when both the box and your backpack contain paint. It works independently of Deposit similar.",
    -- TRANSLATORS: Quantity option in transfer settings. Takes as many cans of the selected color as will fit in the backpack.
    ["ui.settings.all_cans"] = "All",
    -- TRANSLATORS: Placeholder for entering a custom target number of cans in transfer settings.
    ["ui.settings.custom_cans"] = "Custom amount",
    -- TRANSLATORS: Button that saves the custom backpack target quantity entered in the field beside it.
    ["ui.settings.apply_quantity"] = "Apply",
    -- TRANSLATORS: Validation error for the custom backpack target. {maximum} is the largest permitted number of cans of one color.
    ["ui.settings.invalid_backpack_target"] = "Enter a whole number from 1 to {maximum}.",
    -- TRANSLATORS: Help for the paint-name checkbox in ordinary Settings. The detail card keeps showing names when grid labels are hidden.
    ["ui.settings.help.showNames"] = "Show each paint's name below its thumbnail. Turn this off for a picture-only grid. Select a paint to read its name in the detail card.",
    -- TRANSLATORS: Help beside the developer-tools checkbox in ordinary Settings. Debug is the name of the English-only developer tab.
    ["ui.settings.help.showDeveloperTools"] = "Show the Debug tab for reports, timing measurements and processing controls. Hiding Debug restores the default processing settings after pending work settles.",
    -- TRANSLATORS: Help explaining the two paint grid sizes on the Settings page.
    ["ui.settings.help.density"] = "Switch between comfortable and compact paint tiles. Compact tiles fit more colors in each row.",
    -- TRANSLATORS: Help for cycling through paint sort modes. Favorites stay above other colors in each list.
    ["ui.settings.help.sort"] = "Cycle through name A–Z, name Z–A, most cans first and fewest cans first. Favorites appear first within each paint list.",
    -- TRANSLATORS: Help for the single-color backpack target. Keep the example arithmetic and distinguish this control from the all-colors bulk action.
    ["ui.settings.help.quantity"] = "Choose how many cans of the selected color to keep in your backpack. If you choose 5 and carry 2, the selected-paint action takes up to 3 more from ship boxes. All takes as many as fit. The bulk action collects every color that fits.",
    -- TRANSLATORS: Help for scan radius in Settings. The ship is the center both for finding boxes and for the player's connection.
    ["ui.settings.help.ship_radius"] = "Distance from the ship's position used to find loaded storage boxes. You must also be inside this range to connect to PaintManager. The default is 35 m.",
    -- TRANSLATORS: Small fallback glyph shown on the favorite toggle when a paint is not a favorite and the star image is unavailable. Keep it to one glyph.
    ["ui.favorite.empty"] = "+",
    -- TRANSLATORS: Small fallback glyph shown on the favorite toggle when a paint is already a favorite and the star image is unavailable. Keep it to one glyph.
    ["ui.favorite.filled"] = "*",
    -- TRANSLATORS: Tooltip on the star button of a paint tile when this color is not in favorites.
    ["ui.favorite.add"] = "Add to favorites",
    -- TRANSLATORS: Tooltip on the star button of a paint tile when this color is already a favorite.
    ["ui.favorite.remove"] = "Remove from favorites",
    -- BEGIN ENGLISH ONLY
    ["ui.tile.mark.learned"] = "L",
    ["ui.tile.mark.ready"] = "4/4",
    ["ui.tile.mark.missing"] = "?",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Paint tile tooltip and details for a confirmed zero stock of the selected color.
    ["ui.tile.absent"] = "No cans in your backpack or ship boxes",
    -- TRANSLATORS: Stock line for one paint color. {count} can be an exact quantity, a lower bound or a question mark.
    ["ui.tile.stock"] = "Backpack and ship boxes: {count}",
    -- TRANSLATORS: Total number of cans across all colors in the backpack and scanned boxes. {count} may be exact, a lower bound, or unknown.
    ["ui.stock.total"] = "Total cans: {count}",
    -- TRANSLATORS: Compact inventory quantity when some stock could not be read. At least {count} physical cans were confirmed.
    ["ui.tile.lower_bound"] = ">={count}",
    -- TRANSLATORS: Three-line hover tooltip on a paint tile. {name} is the game display name, {recipe} the translated recipe status and {stock} the translated stock description.
    ["ui.tile.tooltip"] = "{name}\n{recipe}\n{stock}",
    -- TRANSLATORS: Prompt in the paint details card before a color is selected.
    ["ui.paint.select"] = "Select a paint to view its details.",
    -- TRANSLATORS: Two lines in the selected paint card. {recipe} is the translated recipe status and {stock} the translated inventory line.
    ["ui.paint.details"] = "{recipe}\n{stock}",
    -- TRANSLATORS: Tooltip for learning one recipe on the selected paint card. The four matching cans are checked but never consumed or moved.
    ["ui.learn.help"] = "Learn with four matching cans in your backpack and ship boxes combined. The cans stay where they are.",
    -- TRANSLATORS: Expandable group in the paint grid for colors whose recipe or stock status is still being checked. {count} is the number of colors in that group.
    ["ui.checking.caption"] = "Checking ({count})",
    -- TRANSLATORS: Paint list pagination button. Opens the previous page of paint colors.
    ["ui.page.previous"] = "Previous",
    -- TRANSLATORS: Paint list pagination button. Opens the next page of paint colors.
    ["ui.page.next"] = "Next",
    -- TRANSLATORS: Paint list footer. {shown} is the count displayed on this page, {total} the number matching the category and search, and {page} the current one-based page number.
    -- PLURAL total: "{shown} of {total} paint · page {page}"
    ["ui.page.summary"] = "{shown} of {total} paints · page {page}",
    -- TRANSLATORS: Progress label during discovery, while the mod is finding storage containers to scan.
    ["ui.progress.finding"] = "Finding containers...",
    -- TRANSLATORS: Scan progress when the box count is known. {completed} is the number checked and {total} the number to check.
    ["ui.progress.containers"] = "Containers checked: {completed} / {total}",
    -- TRANSLATORS: Scan progress before the full number of boxes is known. {completed} is the number checked so far.
    ["ui.progress.containers_total_unknown"] = "Containers checked: {completed} · total unknown",
    -- TRANSLATORS: Short progress phase label for a stock scan, including checking objects and counting cans.
    ["ui.progress.scan"] = "Scan",
    -- TRANSLATORS: Short progress phase label while the mod checks paint recipes for the character.
    ["ui.progress.recipes"] = "Recipes",
    -- TRANSLATORS: Short progress phase label while updating paint tiles in the grid.
    ["ui.progress.tiles"] = "Tiles",
    -- TRANSLATORS: Progress label while building or updating the player-visible paint list.
    ["ui.progress.updating"] = "Updating paints...",
    -- TRANSLATORS: Short progress phase label while learning paint recipes.
    ["ui.progress.learning"] = "Learning",
    -- TRANSLATORS: Scan progress warning that some paint stock could not be read and totals may be incomplete.
    ["ui.progress.partial"] = "Some stock is unavailable",
    -- TRANSLATORS: Temporary completion label when the paint list update succeeds.
    ["ui.progress.updated"] = "Updated",
    -- TRANSLATORS: Single-line progress. {phase} is a translated activity name; {completed}, {total} and {remaining} count work units.
    ["ui.progress.count"] = "{phase}: {completed}/{total} · {remaining} left",
    -- TRANSLATORS: Detailed tooltip for the progress indicator. {unit} is a localized unit phrase, {completed} the number finished, {total} the target and {remaining} the number still to process.
    ["ui.progress.detail"] = "{completed} of {total} {unit}; {remaining} left.",
    -- TRANSLATORS: Unit phrase inserted into the progress tooltip when scanning game objects. Use a construction compatible with ui.progress.detail.
    ["ui.progress.unit.objects"] = "scan objects processed",
    -- TRANSLATORS: Unit phrase inserted into the progress tooltip during storage-box checks. Use a construction compatible with ui.progress.detail.
    ["ui.progress.unit.containers"] = "containers checked",
    -- TRANSLATORS: Unit phrase inserted into the progress tooltip while checking recipes. Use a construction compatible with ui.progress.detail.
    ["ui.progress.unit.recipes"] = "recipe checks processed",
    -- TRANSLATORS: Unit phrase inserted into the progress tooltip while updating paint tiles. Use a construction compatible with ui.progress.detail.
    ["ui.progress.unit.tiles"] = "paint tiles updated",
    -- BEGIN ENGLISH ONLY
    ["ui.debug.summary"] = "State: {status}\nKnown paints: {paints}\nStock coverage: {coverage}\nRecipe coverage: {recipes}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Status message if the selected paint's Learn action is unavailable.
    ["ui.learn.unavailable"] = "Recipe learning is unavailable.",
    -- TRANSLATORS: Status when a request to learn one recipe has been accepted and is being checked.
    ["ui.learn.queued"] = "Checking current stock and recipe before learning.",
    -- TRANSLATORS: Status on the paint page when a recipe learning request fails. {error} is the accompanying failure detail.
    ["ui.learn.failed"] = "Recipe learning request failed: {error}",
    -- TRANSLATORS: Status when the Learn all action for eligible paint recipes is unavailable.
    ["ui.learn_all.unavailable"] = "Batch recipe learning is unavailable.",
    -- TRANSLATORS: Status after the player requests Learn all. The mod checks eligible recipes and keeps the physical cans.
    ["ui.learn_all.queued"] = "Checking ready recipes before batch learning. Physical cans are kept.",
    -- TRANSLATORS: Distance option on a scan-radius button in Settings. {meters} is the distance from the ship, in meters.
    ["ui.button.radius"] = "{meters} m",
    -- BEGIN ENGLISH ONLY
    ["ui.button.snapshot"] = "Snapshot",
    ["ui.button.discovery"] = "Metadata",
    ["ui.button.jmap_prepare"] = "Prepare JMAP",
    ["ui.button.jmap_confirm"] = "Confirm JMAP",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Navigation tab for saving and applying character or ship paint configurations.
    ["ui.tab.presets"] = "Presets",
    -- TRANSLATORS: Target selector on the presets page. Opens saved configurations for the connected ship.
    ["ui.preset.type.ship"] = "Ship",
    -- TRANSLATORS: Button added to the stock paint editor and heading of the dialog it opens. Saves the colors of the target being edited.
    ["ui.editor.save_preset"] = "Save Preset",
    -- TRANSLATORS: Button in the stock paint editor's save-preset dialog. Dismisses it without saving the proposed new preset.
    ["ui.editor.cancel"] = "Cancel",
    -- TRANSLATORS: Wait message in the native editor's Save Preset dialog while the saved library loads.
    ["ui.editor.loading"] = "Loading saved presets…",
    -- TRANSLATORS: Wait message in the native editor's Save Preset dialog while checking the target's actual colors and zones.
    ["ui.editor.checking"] = "Checking current paint configuration…",
    -- TRANSLATORS: Native editor dialog shown when colors and empty zones already match an existing preset. {name} is its saved player-visible name.
    ["ui.editor.duplicate"] = "This configuration is already saved as “{name}”.",
    -- TRANSLATORS: Acknowledgment button in the native editor's dialog when the current layout is already saved.
    ["ui.button.ok"] = "OK",
    -- TRANSLATORS: Success notice in the stock editor popup. {name} is the saved preset name entered or accepted by the player.
    ["ui.editor.saved"] = "Saved “{name}”.",
    -- TRANSLATORS: Native paint editor popup when the edited target changes before validation or saving. Names the editor button to reopen the dialog.
    ["ui.editor.source_changed"] = "The editor’s paint target changed. Reopen Save Preset.",
    -- TRANSLATORS: Target selector on the presets page. Opens paint configurations for the player character.
    ["ui.preset.type.character"] = "Character",
    -- TRANSLATORS: Badge on a saved set that matches the arrangement currently applied to this target, including its empty zones.
    ["ui.preset.matches_current"] = "Current",
    -- TRANSLATORS: Heading above the name field and Save button on the presets page. Captures the target's current colors and empty zones.
    ["ui.preset.save_current"] = "Save current configuration",
    -- TRANSLATORS: Empty-state notice when no saved paint configurations exist for the selected character or ship.
    ["ui.preset.none_saved"] = "No saved configurations for this target.",
    -- TRANSLATORS: Pending-save notice in the presets list or native editor. {name} is the new preset's player-entered or accepted name.
    ["ui.preset.saving"] = "Saving {name}…",
    -- TRANSLATORS: Presets page status when current paint zones are missing or could not be read completely.
    ["ui.preset.current_unknown"] = "Current paint state is unavailable. Refresh to check it.",
    -- TRANSLATORS: Selected preset's detail note when the target already has exactly these colors and empty zones.
    ["ui.preset.already_applied"] = "Already applied.",
    -- TRANSLATORS: Selected preset's detail note when applying it only needs to paint currently empty zones.
    ["ui.preset.can_fill"] = "Fills empty zones from your backpack or accessible ship boxes.",
    -- TRANSLATORS: Tooltip for the Apply preset button. Applying may install, replace or remove paint cans to match the saved layout.
    ["ui.preset.apply_help"] = "Apply the saved colors and empty zones. Paint comes from your backpack or accessible ship boxes. Removed cans return to your backpack.",
    -- TRANSLATORS: Selected preset's detail note when applying it would replace or remove existing paint. The removed cans go back into backpack slots.
    ["ui.preset.can_replace"] = "Removed cans return to an empty slot in your backpack.",
    -- TRANSLATORS: Selected preset's detail note when the saved paint zones are incompatible with the selected object.
    ["ui.preset.incompatible"] = "This configuration is for a different paint layout.",
    -- TRANSLATORS: Placeholder glyph for a thumbnail of a deliberately empty paint zone. Keep it short and distinguish it from an unknown zone.
    ["ui.preset.empty_thumbnail"] = "—",
    -- TRANSLATORS: Tiny badge after a preset row's paint previews. {count} is how many additional zones are omitted from the thumbnails.
    ["ui.preset.more_zones"] = "+{count}",
    -- BEGIN ENGLISH ONLY
    ["ui.preset.help"] = "Save the current colors of your character or ship. Select a preset and choose Apply to restore its colors and empty zones. Paint comes from your backpack first, then accessible ship boxes. Each removed can needs room in your backpack.",
    ["ui.preset.target"] = "Target: {target} · click to choose",
    ["ui.preset.choose_target"] = "Choose a character or boat target",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Command name in the status line when rereading paint targets and saved presets.
    ["ui.preset.refresh"] = "Refresh",
    -- TRANSLATORS: Label or placeholder in the name field for saving the current paint configuration in the main window or the native editor.
    ["ui.preset.name_hint"] = "Configuration name",
    -- TRANSLATORS: Suggested editable name in the game's Save Preset dialog. {number} is the first available positive number. Saved names are kept exactly as entered.
    ["ui.preset.suggest_name"] = "Preset {number}",
    -- TRANSLATORS: Button in the presets page and in the native editor's save dialog. Stores the current layout under the name in the field.
    ["ui.preset.save"] = "Save",
    -- TRANSLATORS: Selected preset button. Changes the character or ship's paint to match the saved colors and empty zones.
    ["ui.preset.apply"] = "Apply",
    -- TRANSLATORS: Presets page prompt before paint targets and saved configurations have been read.
    ["ui.preset.initial"] = "Refresh paint state to view available targets and saved configurations.",
    -- TRANSLATORS: Status on the presets page when the requested preset operation is unavailable.
    ["ui.preset.unavailable"] = "Configuration action is unavailable.",
    -- TRANSLATORS: Status on the presets page when an operation is queued for a live check of the target.
    ["ui.preset.queued"] = "Checking current paint state before this action.",
    -- TRANSLATORS: Status when a preset request fails. {error} is the accompanying failure detail.
    ["ui.preset.failed"] = "Configuration request failed: {error}",
    -- TRANSLATORS: Error during a preset save request if the mod cannot read the name entered in the text field.
    ["ui.preset.name_read_error"] = "PaintManager could not read the configuration name field",
    -- TRANSLATORS: Presets page notice when the library cannot be loaded. {error} is the accompanying failure detail.
    ["ui.preset.library_error"] = "Saved configurations could not be loaded: {error}",
    -- BEGIN ENGLISH ONLY
    ["ui.preset.current"] = "Current zones: {target}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Zone tooltip or row in the selected preset. {zone} is the paintable part name and {paint} is the paint name, Empty, or an unknown-state label.
    ["ui.preset.zone"] = "{zone} · {paint}",
    -- TRANSLATORS: Label in a saved paint configuration for a zone deliberately left without paint.
    ["ui.preset.empty_zone"] = "Empty",
    -- TRANSLATORS: Label for a zone whose saved paint data is unavailable. Distinguish an unknown state from a known empty zone.
    ["ui.preset.unknown_zone"] = "Paint state unknown",
    -- TRANSLATORS: Fallback label for a saved zone lacking a game-provided display name. {material} and {part} are unchanged game identifiers.
    ["ui.preset.logical_zone"] = "Material {material} / part {part}",
    -- TRANSLATORS: Heading above saved presets for the selected target. {count} is the number of matching saved sets.
    ["ui.preset.saved"] = "Saved configurations ({count})",
    -- BEGIN ENGLISH ONLY
    ["ui.preset.select"] = "{selected}Select: {name}",
    ["ui.preset.preview"] = "Selected configuration: {name} · {target}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Prompt in the preset details area before a saved configuration is selected.
    ["ui.preset.choose_saved"] = "Select a saved configuration to preview its zones",
    -- BEGIN ENGLISH ONLY
    ["ui.preset.summary"] = "Targets: {targets} · saved configurations: {presets}",
    ["ui.search.caption"] = "Search",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Placeholder in the paint grid search field. Search operates on the names supplied by the game.
    ["ui.search.hint"] = "Search paint names…",
    -- TRANSLATORS: Button next to the paint search field. Clears the query and redisplays matching paints.
    ["ui.search.clear"] = "Clear",
    -- BEGIN ENGLISH ONLY
    ["ui.filter.all"] = "All",
    ["ui.filter.unlocked"] = "Learned",
    ["ui.filter.locked"] = "Unlearned",
    ["ui.filter.ready"] = "Ready to learn",
    ["ui.filter.insufficient"] = "Not enough cans",
    ["ui.filter.caption"] = "{selected}{name}",
    ["ui.filter.selected"] = "✓ ",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Sort option for paint names, forward alphabetical order.
    ["ui.sort.name_asc"] = "Name A–Z",
    -- TRANSLATORS: Sort option for paint names, reverse alphabetical order.
    ["ui.sort.name_desc"] = "Name Z–A",
    -- TRANSLATORS: Sort option ordering colors by the total number of cans, largest first.
    ["ui.sort.count_desc"] = "Cans: high to low",
    -- TRANSLATORS: Sort option ordering colors by the total number of cans, smallest first.
    ["ui.sort.count_asc"] = "Cans: low to high",
    -- TRANSLATORS: Caption on the paint grid and Settings sort controls. {name} is the translated currently selected sort option.
    ["ui.sort.caption"] = "Sort: {name}",
    -- TRANSLATORS: Initial paint list status before the first view has been prepared.
    ["ui.view.initial"] = "Preparing paint view…",
    -- BEGIN ENGLISH ONLY
    ["ui.view.counts"] = "Showing {shown} of {total} paints",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Tooltip for Learn all on the paint list. Applies to every eligible recipe in the category, independent of the current search and page.
    ["ui.view.scope"] = "Learn all includes ready paints hidden by search or another page. All cans are kept.",
    -- BEGIN ENGLISH ONLY
    ["ui.view.limited"] = " · display limit reached",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Heading on the disconnected PaintManager screen when its link to the ship is unavailable.
    ["ui.connection.lost"] = "Signal lost",
    -- TRANSLATORS: Main toolbar label for the ship-centered search radius. {meters} is the configured distance in metres.
    ["ui.stock.radius.scope"] = "Ship scan radius: {meters} m",
    -- TRANSLATORS: Disconnected screen instruction when the measured player-to-ship distance exceeds the current range.
    ["ui.connection.description"] = "Move closer to the ship to connect.",
    -- TRANSLATORS: Disconnected screen distance line. {distance} is the measured distance to the ship; {radius} is the maximum connection distance, both in metres.
    ["ui.connection.distance"] = "Distance to ship: {distance} m · range {radius} m",
    -- TRANSLATORS: Disconnected screen when the distance cannot be measured. {radius} is the configured connection range in metres.
    ["ui.connection.distance_unavailable"] = "Distance to ship: unavailable · range {radius} m",
    -- TRANSLATORS: Disconnected screen while the game is still identifying the player's ship.
    ["ui.connection.locating"] = "Locating ship",
    -- TRANSLATORS: Disconnected screen when PaintManager cannot locate the current ship.
    ["ui.connection.ship_unavailable"] = "Ship not located",
    -- TRANSLATORS: Disconnected screen when more than one ship matches and PaintManager cannot select one safely.
    ["ui.connection.ship_ambiguous"] = "Several ships found",
    -- TRANSLATORS: Disconnected screen when the player's position is unavailable, so distance to the ship cannot be measured.
    ["ui.connection.player_unavailable"] = "Player position unavailable",
    -- TRANSLATORS: Generic disconnected-screen explanation when PaintManager lacks a more specific reason for the failed ship connection.
    ["ui.connection.unavailable"] = "Ship connection unavailable",
    -- BEGIN ENGLISH ONLY
    ["ui.summary.initial"] = "Open the window within ship range to count paint.",
    ["ui.summary.complete"] = "complete within the stated limits",
    ["ui.summary.incomplete"] = "incomplete data / list not refreshed yet",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Empty paint detail panel before the first stock snapshot has been taken.
    ["ui.empty.not_refreshed"] = "No paint snapshot yet. Open the window within ship range.",
    -- BEGIN ENGLISH ONLY
    ["ui.empty.incomplete"] = "Paint availability is incomplete. Reopen the window to check again.",
    ["ui.empty.no_paint"] = "No paint found in range.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Empty paint details after applying the current tab and search filter.
    ["ui.empty.no_matches"] = "No paints match this page and search.",
    -- TRANSLATORS: Fallback in paint tiles, tooltips, preset thumbnails and details when the game supplies no paint display name.
    ["ui.paint.name_unavailable"] = "Game display name unavailable",
    -- BEGIN ENGLISH ONLY
    ["ui.paint.icon_unavailable"] = "Icon unavailable. ",
    ["ui.paint.incomplete"] = "Incomplete read. ",
    ["ui.paint.identity"] = "{notes}ID: {id}",
    ["ui.paint.stock_scope"] = "Physical cans in your backpack and ship boxes within the scan radius.",
    ["ui.paint.stock_partial"] = "Physical stock read incomplete; shown quantity is a lower bound.",
    ["ui.paint.quantity"] = "{count} pcs.",
    ["ui.paint.group"] = "{title} ({count})",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Paint tile tooltip and details when the current character has already unlocked this paint recipe.
    ["ui.recipe.unlocked"] = "Recipe unlocked",
    -- TRANSLATORS: Paint tile tooltip and details when the current character is confirmed not to know this paint recipe.
    ["ui.recipe.locked"] = "Recipe locked",
    -- TRANSLATORS: Paint tile tooltip and details when membership in the character's recipe library could not be verified.
    ["ui.recipe.unknown"] = "Recipe status unknown",
    -- BEGIN ENGLISH ONLY
    ["ui.recipes.covered"] = "Unlocked recipes: Vault entries covered within limits",
    ["ui.recipes.partial"] = "Unlocked recipes: incomplete coverage",
    ["ui.recipes.unknown"] = "Unlocked recipes: coverage unknown",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Temporary label while the list of paints or saved presets is being prepared for display.
    ["ui.render.building"] = "Building list…",
    -- BEGIN ENGLISH ONLY
    ["ui.render.progress"] = "Building list… {completed} / {total} · display not complete yet",
    ["ui.status.ready"] = "Ready.",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Status prefix in the main footer for a command accepted but not started.
    ["ui.status.queued"] = "Queued",
    -- TRANSLATORS: Status prefix in the main footer while a command is in progress.
    ["ui.status.running"] = "Running",
    -- TRANSLATORS: Status prefix in the main footer after a command finished.
    ["ui.status.done"] = "Done",
    -- TRANSLATORS: Status prefix in the main footer for a failed command; the message follows it.
    ["ui.status.error"] = "Error",
    -- TRANSLATORS: Status prefix after a cancelled command; also replaces progress text after an interruption.
    ["ui.status.cancelled"] = "Cancelled",
    -- TRANSLATORS: Middle part of the footer status line. {command} is the display name of the action; keep the introductory separator and spacing.
    ["ui.status.command"] = " · {command}",
    -- TRANSLATORS: Main footer. {state} is the status label; {command} is optional and already includes its introductory separator. {message} contains an error or transfer detail and may be empty.
    ["ui.status.line"] = "{state}{command} · {message}",
    -- BEGIN ENGLISH ONLY
    ["ui.status.report"] = "Report: {path}",
    ["ui.command.jmap"] = "JMAP dump",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Fallback error after a command was rejected without a specific reason; shared by paint transfers, learning and presets.
    ["ui.queue.rejected"] = "Command was not accepted.",
    -- TRANSLATORS: Fallback status after a command was queued without a specific message; visible for paint transfers.
    ["ui.queue.waiting"] = "Waiting to run.",
    -- BEGIN ENGLISH ONLY
    ["ui.jmap.confirm_notice"] = "Choose Confirm JMAP to create the dump. This can pause the game for a long time and replace the previous dump for this game build.",
    ["ui.jmap.idle_notice"] = "Heavy JMAP requires separate confirmation. Opening this window does not run it.",
    ["ui.diagnostics.discovery_notice"] = "Discovery walks loaded objects and may pause the game. These commands do not unlock recipes or move items.",
    ["ui.diagnostics.recipes"] = "Recipe catalog: current Vault entries within limits; unresolved or unloaded entries leave coverage incomplete. Membership is checked separately through Vault.HasItem; unavailable reads and errors remain unknown.",
    ["ui.diagnostics.recipe_entries"] = "Vault entries visited: {visited}; unresolved entries: {unresolved}.",
    ["ui.diagnostics.radius"] = "The ship scan radius covers loaded boxes and sets the distance at which you can connect to PaintManager. It defaults to 35 m and can be increased to 50 m.",
    ["ui.diagnostics.shortcuts"] = "Shortcut checks cover the UE4SS registry only; native game bindings require a manual check.",
    ["ui.diagnostics.status"] = "Status: {status}",
    ["ui.diagnostics.not_refreshed"] = "not refreshed",
    ["ui.diagnostics.warning"] = "Warning: {warning}",
    ["ui.diagnostics.error"] = "Error: {error}",
    -- END ENGLISH ONLY
    -- TRANSLATORS: Compact placeholder for a value that has not been checked, especially a stock quantity. It must remain distinct from zero.
    ["ui.value.unknown"] = "?",
    -- TRANSLATORS: Opening PaintManager requires a live player character who is not controlling a vehicle.
    ["ui.error.player_unavailable"] = "Load a save and leave vehicle controls.",
    -- TRANSLATORS: Error while locating the class used to create the normal window; the game's asset registry cannot help load it.
    ["ui.error.registry_unavailable"] = "PaintManager UI host is not loaded and AssetRegistryHelpers is unavailable",
    -- TRANSLATORS: Normal window creation failed to resolve its host class. {class} is the engine's class path.
    ["ui.error.host_class_unavailable"] = "PaintManager UI host class unavailable: {class}",
    -- TRANSLATORS: Window creation refused because the resolved game object has an unexpected class.
    ["ui.error.host_class_unexpected"] = "PaintManager UI host resolved to an unexpected class",
    -- TRANSLATORS: Window-opening error when the intended host cannot be verified as an Unreal UserWidget subclass.
    ["ui.error.host_not_userwidget"] = "PaintManager UI host is not a verified UserWidget subclass",
    -- TRANSLATORS: Window layout error when the viewport dimensions or DPI scale are invalid.
    ["ui.error.viewport_invalid"] = "Invalid viewport dimensions or DPI",
    -- TRANSLATORS: Window layout error when the available screen area cannot accommodate the panel.
    ["ui.error.viewport_small"] = "Viewport is too small for PaintManager",
    -- TRANSLATORS: A widget class required to build the normal interface is missing. {class} is its Unreal class name.
    ["ui.error.umg_class_unavailable"] = "UMG class unavailable: {class}",
    -- TRANSLATORS: Error while constructing a widget in the normal window: its Unreal WidgetTree is missing.
    ["ui.error.tree_construction"] = "PaintManager WidgetTree unavailable during construction",
    -- TRANSLATORS: Error when creating a normal UI control. {class} identifies the Unreal widget class.
    ["ui.error.widget_construction"] = "Cannot construct UMG {class}",
    -- TRANSLATORS: A UI action could not be dispatched because the window is missing its command queue callback.
    ["ui.error.queue_unavailable"] = "PaintManager queue unavailable",
    -- TRANSLATORS: Cleanup error while closing the main window; the host still appears in the game viewport.
    ["ui.error.host_not_removed"] = "PaintManager host remains in viewport after removal",
    -- TRANSLATORS: Error returned by closing the normal window. {errors} lists one or more individual cleanup failures.
    ["ui.error.close"] = "Closing: {errors}",
    -- TRANSLATORS: Window-opening error when the game does not supply the current Unreal world.
    ["ui.error.world_unavailable"] = "PaintManager current world unavailable",
    -- TRANSLATORS: Window-opening refusal because another menu or mod window already controls input.
    ["ui.error.menu_open"] = "Close the game menu or the other mod's window first.",
    -- TRANSLATORS: Window-opening refusal because the pause menu is active.
    ["ui.error.pause_open"] = "Close the pause menu first.",
    -- TRANSLATORS: Window-opening error when the game cannot provide its layout or input services.
    ["ui.error.services_unavailable"] = "UMG services unavailable",
    -- TRANSLATORS: Window-opening error when Unreal cannot instantiate the UserWidget hosting PaintManager.
    ["ui.error.host_creation"] = "Cannot create PaintManager UserWidget host",
    -- TRANSLATORS: Window-opening error when the newly created host has no usable WidgetTree.
    ["ui.error.tree_unavailable"] = "PaintManager UserWidget has no valid WidgetTree",
    -- TRANSLATORS: Window-opening error when the widget tree belongs to another object.
    ["ui.error.tree_ownership"] = "PaintManager host WidgetTree is not owned by its new instance",
    -- TRANSLATORS: Window-opening error when the expected native root panel is missing.
    ["ui.error.root_unavailable"] = "PaintManager host has no native CanvasPanel root",
    -- TRANSLATORS: Window-opening error when the root canvas has a different owner than expected.
    ["ui.error.root_ownership"] = "PaintManager native canvas is not owned by its host WidgetTree",
    -- TRANSLATORS: Error while the window is open: its player controller has become invalid.
    ["ui.error.controller_invalid"] = "PaintManager UI controller is no longer valid",
    -- TRANSLATORS: Error while the window is open: the Unreal UserWidget hosting it became invalid.
    ["ui.error.host_invalid"] = "PaintManager UI UserWidget host is no longer valid",
    -- TRANSLATORS: Error while the window is open: the Unreal WidgetTree became invalid.
    ["ui.error.tree_invalid"] = "PaintManager UI WidgetTree is no longer valid",
    -- TRANSLATORS: Error while the window is open: the root CanvasPanel became invalid.
    ["ui.error.root_invalid"] = "PaintManager UI CanvasPanel root is no longer valid",
    -- TRANSLATORS: Error while the window is open: the main panel frame became invalid.
    ["ui.error.frame_invalid"] = "PaintManager UI panel frame is no longer valid",
    -- TRANSLATORS: Error while the window is open: the WidgetTree's root widget is no longer the expected one.
    ["ui.error.root_changed"] = "PaintManager UI WidgetTree.RootWidget changed",
    -- TRANSLATORS: Paint search failed because the input field's current text could not be read.
    ["ui.error.search_text"] = "PaintManager could not read the search field text",
    -- BEGIN ENGLISH ONLY
    ["ui.log.view"] = "Paint view: generation={generation}; query={query}; filter={filter}; sort={sort}; shown={shown}/{total}; allReady={ready}",
    ["ui.log.error"] = "UI error: {error}",
    ["ui.log.layout"] = "UI layout: nativeRoot={root}; rootClass={class}; viewportPx={width}x{height}; dpi={dpi}; fitScale={scale}; expectedLogicalRect=({left},{top},{panelWidth},{panelHeight})",
    ["ui.processing.title"] = "Processing",
    ["ui.processing.help"] = "Session-only developer settings. Edit a draft, then Apply. Synchronous processing can pause the game until the callback returns. Hiding developer tools restores defaults after pending work settles.",
    ["ui.processing.on"] = "On",
    ["ui.processing.off"] = "Off",
    ["ui.processing.pending"] = "Pending · backend {backend} · policy {generation} · {detail}",
    ["ui.processing.effective"] = "Applied · backend {backend} · policy {generation} · {detail}",
    ["ui.processing.backend_status"] = "Backend: {backend} · {reason} · preparation {pause}",
    ["ui.processing.paused"] = "paused",
    ["ui.processing.running"] = "running",
    ["ui.processing.value"] = "Applied: {value} {unit}",
    ["ui.processing.field_help"] = "Range {minimum}–{maximum} {unit}. Default {default}.",
    ["ui.processing.choice_help"] = "Default {default}.",
    ["ui.processing.help.batching_enabled"] = "Off drains finite demanded preparation synchronously. External waits and gameplay mutation pacing remain in force.",
    ["ui.processing.help.automatic_preparation"] = "Off disables optional interface preparation. Opening the window still reads paints and stock.",
    ["ui.processing.help.texture_cache_enabled"] = "Off prepares a fresh texture bank after the window closes. Textures already used by an open window retain their native ownership until normal cleanup.",
    ["ui.processing.help.time_limit_enabled"] = "Stop starting new work when the callback reaches its time target. A game operation already running can finish after that target.",
    ["ui.processing.help.unit_limit_enabled"] = "Stop a callback when it has used its allowed number of work units. A unit can be a native read, a UI update or a batch of Lua operations.",
    ["ui.processing.help.open_work_budget_ms"] = "Time per update while PaintManager, a supported paint editor, a preset dialog, or a supported container is visible. Raising it can finish work sooner, but can make frames take longer.",
    ["ui.processing.help.work_budget_ms"] = "Time per background update when all interfaces handled by PaintManager are closed. Lower values spread pending work over more updates.",
    ["ui.processing.help.work_units_per_tick"] = "Maximum units of mod work started in one callback when the work-unit limit is on. The time target can end the callback earlier.",
    ["ui.processing.help.minimum_pure_quantum_ms"] = "Minimum time given to the first Lua-only work slice. This lets a slice make progress even when the callback has little time left.",
    ["ui.processing.help.pump_ms"] = "Requested delay between callbacks while the window is closed. This startup setting takes effect when the scheduler starts again.",
    ["ui.processing.help.job_admission_target"] = "How many cooperative Lua jobs can be admitted at once. Extra requests wait for a free place.",
    ["ui.processing.help.max_pending"] = "How many commands or action receipts the queue can hold. A full queue can decline a new request.",
    ["ui.processing.help.foreground_units"] = "Work units available to the active foreground job in one callback, such as a requested report or preset operation.",
    ["ui.processing.help.paint_urgent_units"] = "Work units available to prepare paint catalog data that the visible interface currently needs.",
    ["ui.processing.help.stock_open_units"] = "Work units available to the open window's stock scan. Raising this can check boxes sooner.",
    ["ui.processing.help.resource_signal_units"] = "Work units available to prepare the ship-disconnected illustration while that screen is needed.",
    ["ui.processing.help.resource_idle_units"] = "Work units available to prepare UI resources in the background for later use.",
    ["ui.processing.help.resource_open_units"] = "Work units available to prepare resources requested by the open window.",
    ["ui.processing.help.preference_units"] = "Work units available per callback to finish pending changes to favorites and interface preferences.",
    ["ui.processing.help.pure_primitives"] = "Maximum simple data operations in a general Lua-only work slice. Larger batches reduce scheduling overhead.",
    ["ui.processing.help.schema_primitives"] = "Amount of library or settings data validation done in one Lua work slice.",
    ["ui.processing.help.merge_primitives"] = "Number of steps taken to merge or copy saved data in one Lua work slice.",
    ["ui.processing.help.projection_primitives"] = "Number of Lua steps used per slice to prepare filtered and sorted paint views.",
    ["ui.processing.help.aggregate_rows_per_unit"] = "Number of captured inventory or catalog rows combined into paint results per work unit.",
    ["ui.processing.help.report_steps_per_unit"] = "Number of encoding steps used to prepare diagnostic report output per work unit.",
    ["ui.processing.help.ui_stock_rows_per_unit"] = "Number of paint rows prepared per unit before new stock quantities are sent to visible controls.",
    ["ui.processing.help.source_batch_records"] = "Maximum source records grouped for an inventory source-selection batch. Larger batches process more candidates together.",
    ["ui.processing.help.job_steps_per_unit"] = "Number of internal steps a cooperative Lua job may take per unit before yielding to the scheduler.",
    ["ui.processing.help.json_read_bytes"] = "Size of each text slice processed while parsing JSON input.",
    ["ui.processing.help.json_string_bytes"] = "Size of each slice used to escape and encode a JSON string value.",
    ["ui.processing.help.json_key_bytes"] = "Size of each slice used to escape and encode a JSON property name.",
    ["ui.processing.help.compare_bytes"] = "Amount of serialized data compared per step when checking if two documents match.",
    ["ui.processing.help.library_read_bytes"] = "Size of each read from the player's local PaintManager library.",
    ["ui.processing.help.library_write_bytes"] = "Amount of local library data written in one file step.",
    ["ui.processing.help.legacy_read_bytes"] = "Amount read in one step when importing an older preset file.",
    ["ui.processing.help.report_chunk_bytes"] = "Amount of diagnostic report data written to disk in one file step.",
    ["ui.processing.help.ui_rows_per_tick"] = "Limits native control construction while preparing a paint page. Larger values can reveal the completed page sooner.",
    ["ui.processing.help.ui_stock_items_per_tick"] = "Limits how many existing native UI items receive refreshed stock information per update.",
    ["ui.processing.help.ui_icon_units"] = "Limits preparation work for icons requested by the visible interface.",
    ["ui.processing.help.icon_pump_units"] = "Amount of preparation work for the reusable set of control icons in one pass.",
    ["ui.processing.help.icon_decode_bytes"] = "Amount of embedded control-image data decoded per slice while preparing an icon.",
    ["ui.processing.help.illustration_decode_bytes"] = "Amount of embedded image data decoded per slice for the Signal lost illustration.",
    ["ui.processing.help.preload_icons"] = "Prepare control icons before you need them, when automatic preparation is enabled. Disabling this leaves more work for the first visible request.",
    ["ui.processing.help.prewarm_tabs"] = "Prepare unselected paint pages with spare work time. A completed page can then appear as soon as you select its tab.",
    ["ui.processing.help.box_cache_read"] = "Load saved hints about previously observed boxes to assist discovery. Current counts and actions require a live read.",
    ["ui.processing.help.box_cache_write"] = "Save discovered box hints locally so they can be read in another session.",
    ["ui.processing.help.paint_hints_enabled"] = "Reuse identified paint assets from the current session when preparing the catalog.",
    ["ui.processing.help.box_hints_enabled"] = "Reuse previously observed box locations to help find stock. The mod verifies each box before moving paint.",
    ["ui.processing.help.view_cache_enabled"] = "Reuse the prepared filter and sort results when the underlying paint view still matches.",
    ["ui.processing.help.tab_cache_enabled"] = "Keep completed native paint pages for quick tab switching. Turning this off makes the interface rebuild pages when needed.",
    ["ui.processing.help.stock_write_delay_ms"] = "Wait after a box-hint change before writing the cache so nearby updates can share a write.",
    ["ui.processing.help.stock_disk_retry_ms"] = "Delay before another attempt after a box-cache disk operation fails.",
    ["ui.processing.help.resource_retry_ms"] = "Delay before trying again to prepare the native UI resource host when it is unavailable.",
    ["ui.processing.help.context_poll_ms"] = "Interval between scheduled checks of the current player or world context.",
    ["ui.processing.help.ui_progress_delay_ms"] = "Delay before showing progress for a new operation so brief jobs can finish without flashing an indicator.",
    ["ui.processing.help.ui_progress_update_ms"] = "Minimum interval between routine progress updates. Increase it for fewer UI changes during longer jobs.",
    ["ui.processing.help.metrics_level"] = "Choose how much timing information to collect. Summary records overall activity, while Detailed adds sampled diagnostic logging.",
    ["ui.processing.help.slow_unit_ms"] = "Work taking at least this long can be recorded as slow during timing measurements.",
    ["ui.processing.help.log_sample_callbacks"] = "Number of callbacks between detailed timing log samples when Detailed measurements are enabled.",
    ["ui.processing.measurements"] = "Measurements: {metrics}\nRead-only capabilities: {capabilities}",
    ["ui.processing.invalid"] = "Enter a valid value for {name}.",
    ["ui.processing.unavailable"] = "Processing settings are unavailable.",
    ["ui.processing.minus"] = "−",
    ["ui.processing.plus"] = "+",
    ["ui.processing.inherit"] = "Inherit",
    ["ui.processing.inherit_effective"] = "Inherited ({value})",
    ["ui.processing.unlimited"] = "No local cap",
    ["ui.processing.reset_group"] = "Reset group",
    ["ui.processing.custom"] = "Custom",
    ["ui.processing.reset_all"] = "Reset all drafts",
    ["ui.processing.apply"] = "Apply",
    ["ui.processing.discard"] = "Discard edits",
    ["ui.processing.export"] = "Export profile",
    ["ui.processing.import"] = "Import as draft",
    ["ui.processing.startup_once"] = "Use once at next start",
    ["ui.processing.profile_draft"] = "Profile staged. Apply to change processing; gameplay actions never start automatically.",
    ["ui.processing.profile.defaults"] = "Defaults",
    ["ui.processing.profile.demand"] = "Demand only",
    ["ui.processing.profile.synchronous"] = "Synchronous",
    ["ui.processing.profile.small"] = "Small batches",
    ["ui.processing.group.execution"] = "Execution modes",
    ["ui.processing.group.scheduler"] = "Scheduler limits",
    ["ui.processing.group.quotas"] = "Subsystem quotas",
    ["ui.processing.group.primitives"] = "Pure work batches",
    ["ui.processing.group.bytes"] = "Codec and file slices",
    ["ui.processing.group.ui"] = "Native UI and images",
    ["ui.processing.group.preparation"] = "Automatic preparation",
    ["ui.processing.group.caches"] = "Cache reuse",
    ["ui.processing.group.cadence"] = "Maintenance cadence",
    ["ui.processing.group.progress"] = "Progress publications",
    ["ui.processing.group.diagnostics"] = "Measurements and logging",
    ["ui.processing.action.reset_metrics"] = "Reset measurements",
    ["ui.processing.action.capture_next"] = "Capture next operation",
    ["ui.processing.action.refresh_metrics"] = "Refresh measurements",
    ["ui.processing.action.cold_reset"] = "Reset derived memory",
    ["ui.processing.action.rebuild_tab"] = "Rebuild paint tab",
    ["ui.processing.action.fresh_discovery"] = "Reset box hints for next open",
    ["ui.processing.action.pause"] = "Pause preparation",
    ["ui.processing.action.step_packet"] = "One packet",
    ["ui.processing.action.step_primitive"] = "One Lua primitive",
    ["ui.processing.action.resume"] = "Resume preparation",
    ["ui.processing.field.batching_enabled"] = "Cooperative batching",
    ["ui.processing.field.job_admission_target"] = "Admitted Lua jobs",
    ["ui.processing.field.job_steps_per_unit"] = "Lua task steps per unit",
    ["ui.processing.field.automatic_preparation"] = "Automatic preparation",
    ["ui.processing.field.time_limit_enabled"] = "Limit elapsed work time",
    ["ui.processing.field.unit_limit_enabled"] = "Limit work units",
    ["ui.processing.field.open_work_budget_ms"] = "Work time target (interface visible)",
    ["ui.processing.field.work_budget_ms"] = "Work time target (background)",
    ["ui.processing.field.work_units_per_tick"] = "Work units per callback",
    ["ui.processing.field.minimum_pure_quantum_ms"] = "Minimum pure-work quantum",
    ["ui.processing.field.pump_ms"] = "Closed-window callback delay (next start)",
    ["ui.processing.field.foreground_units"] = "Foreground job",
    ["ui.processing.field.paint_urgent_units"] = "Demanded paint preparation",
    ["ui.processing.field.paint_idle_units"] = "Idle paint preparation",
    ["ui.processing.field.stock_open_units"] = "Open-window stock preparation",
    ["ui.processing.field.stock_closed_units"] = "Closed-window stock preparation",
    ["ui.processing.field.resource_signal_units"] = "Signal illustration resources",
    ["ui.processing.field.resource_idle_units"] = "Idle resources",
    ["ui.processing.field.resource_open_units"] = "Visible resources",
    ["ui.processing.field.preference_units"] = "Preference settlement",
    ["ui.processing.field.pure_primitives"] = "Pure data",
    ["ui.processing.field.schema_primitives"] = "Schema validation",
    ["ui.processing.field.merge_primitives"] = "Merge and clone",
    ["ui.processing.field.projection_primitives"] = "View projection",
    ["ui.processing.field.aggregate_rows_per_unit"] = "Collection aggregation",
    ["ui.processing.field.report_steps_per_unit"] = "Report encoding",
    ["ui.processing.field.ui_stock_rows_per_unit"] = "Stock patch row preparation",
    ["ui.processing.field.source_batch_records"] = "Source-selection submission",
    ["ui.processing.field.json_read_bytes"] = "JSON parser text",
    ["ui.processing.field.json_string_bytes"] = "JSON string encoding",
    ["ui.processing.field.json_key_bytes"] = "JSON key encoding",
    ["ui.processing.field.compare_bytes"] = "Canonical byte comparison",
    ["ui.processing.field.library_read_bytes"] = "Library file reads",
    ["ui.processing.field.library_write_bytes"] = "Library file writes",
    ["ui.processing.field.legacy_read_bytes"] = "Legacy import reads",
    ["ui.processing.field.report_chunk_bytes"] = "Report file writes",
    ["ui.processing.field.ui_rows_per_tick"] = "Native page construction",
    ["ui.processing.field.ui_stock_items_per_tick"] = "Native stock patches",
    ["ui.processing.field.ui_icon_units"] = "Visible icon preparation",
    ["ui.processing.field.icon_pump_units"] = "Icon bank preparation",
    ["ui.processing.field.icon_decode_bytes"] = "Control image decode",
    ["ui.processing.field.illustration_decode_bytes"] = "Signal illustration decode",
    ["ui.processing.field.max_pending"] = "Pending commands and receipts",
    ["ui.processing.field.preload_stock_open"] = "Maintain stock while open",
    ["ui.processing.field.preload_stock_closed"] = "Maintain stock while closed",
    ["ui.processing.field.preload_icons"] = "Preload control images",
    ["ui.processing.field.prewarm_tabs"] = "Prewarm hidden tabs",
    ["ui.processing.field.stock_discovery_enabled"] = "Periodic box discovery",
    ["ui.processing.field.box_cache_read"] = "Read box disk cache",
    ["ui.processing.field.box_cache_write"] = "Write box disk cache",
    ["ui.processing.field.paint_hints_enabled"] = "Reuse session paint catalog",
    ["ui.processing.field.box_hints_enabled"] = "Reuse box hints",
    ["ui.processing.field.view_cache_enabled"] = "Reuse view projection",
    ["ui.processing.field.tab_cache_enabled"] = "Reuse tab widgets",
    ["ui.processing.field.texture_cache_enabled"] = "Reuse prepared textures",
    ["ui.processing.field.stock_idle_callbacks"] = "Stock idle callback spacing",
    ["ui.processing.field.stock_idle_inventory_ms"] = "Idle inventory spacing",
    ["ui.processing.field.stock_discovery_ms"] = "Box discovery interval",
    ["ui.processing.field.stock_refresh_ms"] = "Stock refresh interval",
    ["ui.processing.field.stock_write_delay_ms"] = "Box cache write debounce",
    ["ui.processing.field.stock_disk_retry_ms"] = "Box cache disk retry",
    ["ui.processing.field.stock_incomplete_retry_ms"] = "Incomplete stock retry",
    ["ui.processing.field.resource_retry_ms"] = "Resource host retry",
    ["ui.processing.field.context_poll_ms"] = "Context polling",
    ["ui.processing.field.ui_progress_delay_ms"] = "Progress display delay",
    ["ui.processing.field.ui_progress_update_ms"] = "Progress update interval",
    ["ui.processing.field.stock_prefix_enabled"] = "Publish progressive stock observations",
    ["ui.processing.field.stock_prefix_first"] = "First progressive stock checkpoint",
    ["ui.processing.field.stock_prefix_growth"] = "Progressive checkpoint multiplier",
    ["ui.processing.field.metrics_level"] = "Measurements",
    ["ui.processing.field.slow_unit_ms"] = "Slow work threshold",
    ["ui.processing.field.log_sample_callbacks"] = "Detailed log sample interval",
    -- END ENGLISH ONLY
}
