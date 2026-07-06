# Possible Issues Parking Lot

Use this file for unrelated bugs, risks, or cleanup ideas noticed while working on another task.

Keep entries concise and evidence-based. Do not fix these as part of unrelated work unless the user asks.

## Open

None.

## Fixed / Closed

### 2026-07-06 - Offline polling can look like mass deletion
- Area: `ready_jobs_watcher/polling.py`, `ready_jobs_watcher/main.py`
- Evidence: `_scan_root_entries()` / `_scan_file_entries()` log `OSError` and return `{}`; `poll_once()` then compares that empty scan against the prior snapshot and can dispatch deletes after `ready_jobs_stable_poll_count`.
- Risk: A temporary SMB outage could trigger false PDF delete cleanup, metadata refreshes, and snapshot removal, then miss changes made while offline.
- Resolution: Failed scans now return a non-authoritative result. Polling skips reconciliation/snapshot mutation when all requested scans fail, or when an individual path cannot be scanned during an otherwise successful pass.
- Verification: `tests/test_polling.py::test_failed_file_scan_does_not_dispatch_deletes_or_mutate_snapshot` and `tests/test_polling.py::test_per_path_file_scan_failure_does_not_dispatch_delete_or_mutate_snapshot`.

### 2026-07-06 - Polling snapshot is not invalidated when root changes
- Area: `ready_jobs_watcher/polling.py`, `ready_jobs_watcher/gui.py`
- Evidence: The snapshot stores `root_dir`, but `_load_snapshot()` only checks that `entries` exists; settings can update `ROOT_DIR` without clearing `polling_snapshot.json`.
- Risk: Reusing relative entries from an old root against a new root could cause false deletes/creates or a bad first baseline after a root-directory change.
- Resolution: Polling discards snapshots whose saved root does not match the current configured root.
- Verification: `tests/test_polling.py::test_snapshot_for_different_root_is_discarded_without_delete_dispatch`.

### 2026-07-06 - Failed restart leaves app visually alive but monitoring may stay stopped
- Area: `ready_jobs_watcher/main.py`
- Evidence: `restart()` sets `stop_event`, sleeps, then on spawn failure clears it and restores the tray only; it does not restart `observer_monitor_thread`, `poller_thread`, schedulers, or observers.
- Risk: If process respawn fails, the tray can return while polling/observer lifecycle threads have already exited.
- Resolution: Restart spawn failure now sends a critical alert and exits with status `1` instead of restoring a misleading tray state.
- Verification: `tests/test_main_observer_resilience.py::TestMainObserverResilience::test_restart_spawn_failure_alerts_and_exits_instead_of_restoring_false_alive`.

### 2026-07-06 - Runtime root/config save does not rewire long-lived services
- Area: `ready_jobs_watcher/gui.py`, `ready_jobs_watcher/main.py`
- Evidence: `save_settings()` updates `config.ROOT_DIR` and saves, but `DeploymentGateManager`, `MetadataRefreshService`, existing handlers, observers, and poller state are created earlier.
- Risk: After changing the root in settings, polling may read one root while deployment gate, metadata refresh, or observers still point at old state until restart.
- Resolution: Saving a different root directory now tells the operator to restart Ready Jobs Watcher for the root change to take effect.
- Verification: `tests/test_gui_rename.py::test_save_settings_warns_restart_required_when_root_changes`.

### 2026-07-06 - Pending PDF timers are not retargeted after job rename
- Area: `ready_jobs_watcher/main.py`, `ready_jobs_watcher/pending_queue.py`, `ready_jobs_watcher/watchers.py`
- Evidence: `Application.rename_job()` rewrites persisted pending queue paths, but existing conversion timers still close over the old `pdf_path`.
- Risk: If a job is renamed while a PDF conversion is cooling down, the old timer may skip the missing old path while the rewritten queue entry for the new path remains unscheduled until restart.
- Resolution: Pending queue rename now returns old-to-new PDF path mappings; active PDF conversion timers are cancelled and rescheduled under the renamed path.
- Verification: `tests/test_watchers_metadata_refresh.py::test_pdf_change_handler_retargets_pending_conversion_timer` and `tests/test_main_rename.py::test_rename_job_retargets_pending_renames_and_pdf_conversions`.

### 2026-07-06 - Locked-file retry can double-prefix renamed files
- Area: `ready_jobs_watcher/file_handler.py`, `ready_jobs_watcher/main.py`
- Evidence: Immediate `process_file()` strips an existing wrong prefix before renaming, but locked-file retry stores `original_name` and later builds `job_num + ' - ' + original_name`.
- Risk: A locked file like `123 - Part.pdf` inside a renamed/new job can retry as `999 - 123 - Part.pdf`; entries can also be dropped if the job folder is renamed before retry.
- Resolution: Locked-file retry now uses shared target-name normalization, and job rename retargets pending rename paths/directories in memory.
- Verification: `tests/test_main_rename.py::test_retry_pending_strips_wrong_existing_prefix_before_adding_new_prefix`.

### 2026-07-06 - Tracker-triggered metadata refresh skips tracker consolidation
- Area: `ready_jobs_watcher/watchers.py`, `ready_jobs_watcher/metadata_refresh.py`
- Evidence: Watcher/poller events can schedule metadata refresh for tracker JSON/NDJSON changes, but `MetadataRefreshService.refresh_job_now()` calls `refresh_single_job(..., consolidate_trackers=False)`.
- Risk: Bad-parts alerting may see new tracker actions immediately, while `consolidated.json` and metadata snapshots remain stale until the scheduled sweep.
- Resolution: Watcher tracker JSON/NDJSON events now schedule tracker-specific metadata refresh reasons, tracker refreshes call `refresh_single_job(..., consolidate_trackers=True)`, and NDJSON tracker event files are rebuild triggers.
- Verification: `tests/test_metadata_refresh_scheduler.py::test_tracker_reason_refresh_consolidates_trackers`, `tests/test_watchers_metadata_refresh.py::test_tracker_json_change_schedules_tracker_metadata_refresh`, `tests/test_watchers_metadata_refresh.py::test_tracker_ndjson_change_schedules_tracker_metadata_refresh`, and `tests/test_metadata_inventory.py::test_tracker_ndjson_event_file_is_external_source_trigger`.

### 2026-07-06 - Mixed tracker migration mode can ignore legacy tablet files
- Area: `ready_jobs_watcher/tracker_action_stream.py`, `tests/test_tracker_action_stream.py`
- Evidence: `load_cnc_tracker_actions()` uses NDJSON streams whenever any `events/**/*.ndjson` exists, otherwise falls back to legacy JSON. Existing tests cover NDJSON-only formats but not mixed NDJSON plus legacy files.
- Risk: During partial migration or recovery, a tablet still writing `.tracker/*.json` could have actions ignored for alerts/remake candidates.
- Resolution: Tracker action loading now merges migrated NDJSON and legacy JSON actions and sorts the combined stream deterministically.
- Verification: `tests/test_tracker_action_stream.py::test_load_cnc_tracker_actions_merges_migrated_and_legacy_sources`.
