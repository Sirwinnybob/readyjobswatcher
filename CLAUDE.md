# CLAUDE.md

Guidance for Ready Jobs Watcher repo.

## Running

- Run app: `python -m ready_jobs_watcher`
- Run tests: `python -m pytest`
- 3 `tests/test_dae_converter.py` tests fail unless optional `mapbox_earcut` installed. Environmental, unrelated to most changes. Not regressions.
- PyQt6 desktop app. GUI code in `ready_jobs_watcher/gui.py` not unit-tested. Verify GUI changes with headless import smoke check (`QT_QPA_PLATFORM=offscreen`) plus manual walkthrough.

## Job deployment gate (CRITICAL)

- Each job stores state at `<job>/.metadata/deployment_gate.json`, managed by `ready_jobs_watcher/deployment_gate.py`.
- Android app reads this JSON. Do NOT change schema — keep every field name and type stable: `deployed`, `parseReady`, `hiddenFromProduction`, `selectedMode`, `modeDetection`, `timers.{retryAt,remindAt,autoReleaseAt,lastActionAt}`, etc. Add behavior via write-paths, not serialized shape.
- Presentation state is derived, not stored. Use `derive_state(state)` in `deployment_gate.py`: PENDING (not deployed) / PARSING (deployed, not parseReady) / ACTIVE (deployed and parseReady).
- `hiddenFromProduction` retired from UI. Stays in JSON (default `False`); UI never sets it `True`. New pending jobs hidden via `deployed=False`, not the flag. `retryAt` also stays a JSON field, no longer written.
- Per-job operator actions live in the state-aware dialog from double-clicking a Jobs-tab row: PENDING jobs get Release/Snooze, released jobs get Re-parse.

## Conventions

- Atomic JSON writes go through `DeploymentGateManager._atomic_write_json` (temp file + rename).
- Background work (deploy, re-parse) runs in daemon threads spawned from the GUI; refresh dashboard after dispatch.
