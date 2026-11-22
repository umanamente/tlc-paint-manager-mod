-- UI, diagnostics and explicitly requested learning/preset workflow defaults.
return {
    version = "0.3.0",
    selected_backpack_target = require("paintmanager.transfer_quantity").default,
    reference_build = "25982825",
    -- Stock discovery and the UI connection share the ship actor position.
    ship_radius_cm = 3500,
    ship_min_radius_cm = 3500,
    ship_max_radius_cm = 5000,
    ship_max_candidates = 32,
    -- Only for showing a recent completed interval while the next open is reading.
    reopen_snapshot_seconds = 60,
    stock_write_delay_ms = 2000,
    context_poll_ms = 500,
    -- Background callback delay. Visible interfaces use every engine tick.
    pump_ms = 16,
    -- Cooperative work per scheduler callback, not a hard frame deadline.
    -- A single engine API call/one native inventory traversal cannot be preempted.
    work_budget_ms = 2,
    open_work_budget_ms = 30,
    -- These bound consecutive transfer work on the game thread. Neither is a wait.
    transfer_slice_ms = 50,
    transfer_slice_units = 256,
    work_units_per_tick = 8,
    ui_rows_per_tick = 2,
    aggregate_rows_per_unit = 32,
    report_steps_per_unit = 32,
    report_chunk_bytes = 8192,
    max_pending = 4,
    max_sources = 128,
    max_slots = 1024,
    max_paint_boats = 8,
    max_paint_slots = 128,
    max_catalog = 512,
    ui_page_size = 128,
    -- Opacity of a paint tile with confirmed zero stock.
    ui_absent_opacity = 0.4,
    ui_progress_delay_ms = 200,
    ui_progress_update_ms = 150,
    max_classes = 32,
    max_functions = 96,
    max_properties = 128,
    max_super_depth = 12,
    max_discovery_objects = 100000,
    max_discovery_seconds = 0.25,
    -- All shortcuts use Ctrl+Alt. Paint warmup waits for a proven native load.
    -- UE4SS polls shortcuts; it does not consume the underlying game key.
    -- Leave the game menu binding for P available.
    snapshot_key = "F6",
    -- Avoid clashes with HumanBuilder F7/F8/F9/F11. The UE4SS registry
    -- only reports bindings registered with the loader.
    ui_key = "F10",
    discovery_key = "O",
    -- JMAP is a large, explicit diagnostic dump; includes game metadata/bytecode.
    jmap_key = "J",
}
