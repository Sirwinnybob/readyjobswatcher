# Possible Issues Fix Plan

## Objective

Fix the eight Ready Jobs Watcher risks captured in `docs/possible-issues.md` without expanding Android/tablet metadata schemas or changing unrelated workflows.

## Scope

- Harden Ready Jobs polling against transient share failures and root changes.
- Retarget pending in-memory and persisted work after job folder renames.
- Make tracker-driven metadata refresh and mixed tracker migration behavior consistent.
- Avoid misleading operator state after restart spawn failure.
- Make settings root changes explicitly restart-required.

## Implementation Steps

1. Add failing regression tests for each documented issue.
2. Fix polling so failed scans are non-authoritative and mismatched-root snapshots are discarded.
3. Fix pending queue rename, active PDF conversion timer retargeting, and pending locked-file retry rename behavior.
4. Fix tracker refresh/consolidation and mixed NDJSON plus legacy JSON action loading.
5. Fix restart spawn failure behavior and settings root-change messaging.
6. Update `docs/possible-issues.md` after verification.

## Verification

- `python -m pytest tests/test_polling.py tests/test_pending_queue_resume.py tests/test_main_rename.py tests/test_watchers_metadata_refresh.py tests/test_tracker_action_stream.py tests/test_metadata_refresh_scheduler.py tests/test_main_observer_resilience.py tests/test_gui_rename.py -q`
- `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.main; import ready_jobs_watcher.gui"`
- Preferred: `python -m pytest -q`

## Notes

- The full suite may fail in `tests/test_dae_converter.py` if optional `mapbox_earcut` is unavailable.
- Root-directory changes are handled by an explicit restart requirement, not live service rewiring.
