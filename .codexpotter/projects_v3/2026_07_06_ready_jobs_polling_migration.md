# Objective

## Original User Request

PLEASE IMPLEMENT THIS PLAN:
# Loop Execution Plan For Ready Jobs Polling Migration

## Summary
- Use after Plan Mode ends to implement the Ready Jobs polling migration.
- Round limit: default `10`.
- Handoff file: `.codexpotter/projects_v3/2026_07_06_ready_jobs_polling_migration.md`.
- Subagents own implementation, tests, fixes, and commits. Parent agent only coordinates loop.

## Handoff Content
- Objective: migrate Ready Jobs Watcher to hybrid polling where timed polling is authoritative and watchdog is optional fast hint.
- Scope: Ready Jobs Watcher only.
- Defaults: `filesystem_monitor_mode="hybrid"`, `ready_jobs_file_poll_seconds=60`, `ready_jobs_root_poll_seconds=10`, `ready_jobs_stable_poll_count=2`.
- Required behaviors:
  - Poller detects created, modified, deleted, and likely top-level renamed job folders.
  - Existing watcher side effects remain intact through reusable handler methods.
  - GUI rename calls `Application.rename_job` directly after disk rename.
  - `watchdog` remains enabled only in hybrid mode.
  - Polling-only mode skips observers.
  - No Android/tablet metadata schema changes.

## Loop Flow
- Create handoff file with prior plan plus constraints.
- Send exact Initial Prompt to subagent.
- Continue same subagent until it emits `::potter(ready)`.
- Start fresh verifier subagent on next round.
- Stop only when fresh verifier emits `::potter(exit)`.
- If 10 rounds reached, send Reach Limit Prompt and report state as `round limit reached`.

## Verification Gates
- Required tests:
  - `python -m pytest tests/test_config.py tests/test_polling.py tests/test_main_observer_resilience.py tests/test_watchers_deployment_gate.py tests/test_watchers_metadata_refresh.py tests/test_pending_queue_resume.py -q`
  - `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.main; import ready_jobs_watcher.gui"`
- Preferred full check:
  - `python -m pytest`
  - Known caveat: `tests/test_dae_converter.py` may fail without optional `mapbox_earcut`.

## Assumptions
- User wants loop used for implementation, not more design.
- Loop starts only after Plan Mode ends or user explicitly switches to execution-capable mode.
- Subagent may commit implementation changes, as Initial Prompt requires.

## Important Context, Constraints, and User Preferences

- Current date is 2026-07-06.
- Repository root is `C:\Scripts\Ready Jobs Watcher`.
- The user wants the Ready Jobs Watcher migrated because the Ready Jobs folder is a server share and watchdog events can be unreliable on SMB/network shares.
- Preferred strategy selected during planning: hybrid first, where watchdog remains as optional low-latency hints but polling reconciliation is authoritative.
- Preferred latency selected during planning: 60 seconds for file changes.
- Scope selected during planning: Ready Jobs Watcher only, not all KKC systems.
- Existing local working tree may contain unrelated uncommitted changes. Do not revert user changes. Work with current state.
- Commit all implementation changes before final response, per loop protocol.

## Critical Data, Examples, and References

- Earlier plan established these implementation details:
  - Add config fields in `Config` and `config.json`: `filesystem_monitor_mode`, `ready_jobs_file_poll_seconds`, `ready_jobs_root_poll_seconds`, `ready_jobs_stable_poll_count`.
  - Add local cache file `polling_snapshot.json` beside `pending_queue.json`.
  - Add module `ready_jobs_watcher/polling.py`.
  - Refactor current watchdog event handlers in `ready_jobs_watcher/watchers.py` into reusable path handlers so both watchdog and polling dispatch the same side effects.
  - Add `Application.rename_job(old_name, new_name)` and call it from GUI rename immediately after `os.rename`.
  - In hybrid mode, keep watchdog observers running; in polling mode, skip watchdog observer startup.
  - Keep Android/tablet-facing metadata schema unchanged.
- Important existing behaviors to preserve:
  - New top-level job folders get gated pending and prompt operator flow.
  - Delayed folder processing and PDF conversion still use `pending_queue.json`.
  - PDF create/modify/delete still schedules metadata refresh, index refresh, dark-mode conversion, CNC tracker scans, and dark-mode cleanup as current handlers do.
  - CNC tracker JSON and event stream changes still trigger tracker reconcile/remake refresh.
  - `3d.dae` changes still trigger delayed GLB conversion.
  - Sync-conflict files still route through existing conflict resolver.
- Verification commands from user plan must be run before declaring completion:
  - `python -m pytest tests/test_config.py tests/test_polling.py tests/test_main_observer_resilience.py tests/test_watchers_deployment_gate.py tests/test_watchers_metadata_refresh.py tests/test_pending_queue_resume.py -q`
  - `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.main; import ready_jobs_watcher.gui"`
  - Prefer also `python -m pytest`, noting optional `mapbox_earcut` caveat if relevant.

# Done

- Completed Ready Jobs Watcher hybrid polling migration. Added `ReadyJobsPoller` with `polling_snapshot.json` local state, stable-count reconciliation, created/modified/deleted file detection, and likely top-level job-folder rename detection. Added handler path entry points so polling and watchdog reuse `RenameHandler`/`PdfChangeHandler` side effects. Added config defaults/validation for `filesystem_monitor_mode="hybrid"`, `ready_jobs_file_poll_seconds=60`, `ready_jobs_root_poll_seconds=10`, and `ready_jobs_stable_poll_count=2`. Wired startup so hybrid starts polling plus watchdog, polling mode skips observers, and watchdog mode remains available. Added `Application.rename_job` and made GUI rename call it immediately after disk rename. Kept Android/tablet metadata schema unchanged.
  - Key decisions + rationale: polling is authoritative in hybrid mode, so watchdog observer startup failure no longer prevents monitoring when polling is running; `polling_snapshot.json` is ignored as runtime state; existing watcher handlers remain the single side-effect surface for metadata refresh, dark-mode conversion, tracker scans, DAE conversion, sync conflicts, and job-folder processing.
  - Files changed: `.gitignore`, `ready_jobs_watcher/polling.py`, `ready_jobs_watcher/config.py`, `ready_jobs_watcher/main.py`, `ready_jobs_watcher/watchers.py`, `ready_jobs_watcher/gui.py`, `tests/test_polling.py`, `tests/test_gui_rename.py`, `tests/test_config.py`, `tests/test_main_observer_resilience.py`, `tests/test_watchers_metadata_refresh.py`. Existing inherited SMB resilience changes remain in `ready_jobs_watcher/scheduler.py` and `ready_jobs_watcher/utils.py`.
  - Verification: `python -m pytest tests/test_config.py tests/test_polling.py tests/test_main_observer_resilience.py tests/test_watchers_deployment_gate.py tests/test_watchers_metadata_refresh.py tests/test_pending_queue_resume.py tests/test_gui_rename.py -q` passed (51 passed). `$env:QT_QPA_PLATFORM='offscreen'; python -c "import ready_jobs_watcher.main; import ready_jobs_watcher.gui"` exited 0. `python -m pytest -q` ran with 305 passed and only the documented optional `mapbox_earcut` failures in `tests/test_dae_converter.py`.
- Completion audit on 2026-07-06 confirmed the current worktree satisfies the Ready Jobs polling migration objective. Current code evidence shows config defaults and validation in `ready_jobs_watcher/config.py`, polling reconciliation in `ready_jobs_watcher/polling.py`, shared watcher handler path methods in `ready_jobs_watcher/watchers.py`, hybrid/polling/watchdog startup selection in `ready_jobs_watcher/main.py`, and direct GUI rename propagation through `Application.rename_job` in `ready_jobs_watcher/gui.py`.
  - Key decisions + rationale: no additional implementation patch was needed in this audit because current-state code and focused tests already prove the requested end state; the full-suite failures remain outside this migration and match the documented missing optional `mapbox_earcut` caveat.
  - Files changed: `.codexpotter/projects_v3/2026_07_06_ready_jobs_polling_migration.md`.
  - Verification: `python -m pytest tests/test_config.py tests/test_polling.py tests/test_main_observer_resilience.py tests/test_watchers_deployment_gate.py tests/test_watchers_metadata_refresh.py tests/test_pending_queue_resume.py -q` passed (50 passed). `$env:QT_QPA_PLATFORM='offscreen'; python -c "import ready_jobs_watcher.main; import ready_jobs_watcher.gui"` exited 0. `python -m pytest tests/test_gui_rename.py -q` passed (1 passed). `python -m pytest -q` produced 305 passed and 3 failures, all in `tests/test_dae_converter.py` due to missing optional `mapbox_earcut`.
