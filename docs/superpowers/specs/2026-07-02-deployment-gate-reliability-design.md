# Deployment Gate Reliability — Design

**Date:** 2026-07-02
**Status:** Approved (pending final spec review)
**Predecessor:** `2026-06-15-job-visibility-release-simplification-design.md` (state-machine UI, derived PENDING/PARSING/ACTIVE states)

## Problem

Two concrete gaps in the deployment gate today:

1. **Silent auto-deploy.** Every pending job gets a `timers.autoReleaseAt`
   stamped to "now + 30h" the moment it's discovered
   (`ensure_pending_for_new_job`), and — worse — **any** operator action on a
   still-pending job (a mode edit, a snooze) silently re-arms that same 30h
   countdown from `update_state`'s `operator_action` branch. A job the operator
   never explicitly released can go live on its own. There is no way to turn
   this off per-job; the only lever is the global
   `PENDING_AUTO_RELEASE_HOURS` constant.

2. **No way back once deployed.** Once a job reaches PARSING/ACTIVE, the
   state-aware dialog (`_show_pending_job_prompt_dialog`) only offers "Save
   Mode" and "Re-parse." If a job deploys by mistake — via the timer above, or
   a manual Release click — there is no operator action that pulls it back
   out of production view.

A third issue surfaced while tracing the pending-job dialog for this work:
**Snooze doesn't do anything.** It writes `timers.remindAt`
(`schedule_reminder`), but nothing in the codebase reads that field back to
re-show the prompt or send a reminder — confirmed by grepping every reference
to `remindAt`; the only consumer is the dashboard table displaying it as a
column. The predecessor spec's manual test plan assumed "Snooze writes
`remindAt` and re-prompts after N minutes" — that re-prompt step was never
actually wired up. Since this work touches the exact same dialog, it's fixed
alongside the other two.

## Hard Constraint: Android Metadata Unchanged

Same constraint as the predecessor spec. `.metadata/deployment_gate.json`'s
schema does not change — every field stays present with the same name, type,
and meaning:

```
schemaVersion, jobFolderName, deployed, parseReady, hiddenFromProduction,
selectedMode, modeDetection{candidate,source,detectedAt},
timers{retryAt,remindAt,autoReleaseAt,lastActionAt}, createdAt, updatedAt
```

All of this ships through **write-path behavior changes**, reusing existing
fields:

- `timers.autoReleaseAt` — stops being auto-stamped with a default; becomes
  purely operator-set via the new Schedule Deploy action. The
  `pending_autorelease_scheduler` sweep loop that fires on it is unchanged.
- `timers.remindAt` — same field, same `schedule_reminder` writer; gains a
  real consumer (new reminder sweep).
- `hiddenFromProduction` — the predecessor spec retired this from *pre-deploy*
  hiding (redundant with `deployed == False`) but left it fully wired in both
  `get_visibility()` and the Android app's `DeploymentGateRules.evaluate()`
  (confirmed by reading `DeploymentGate.kt` directly). This spec reuses it for
  a different purpose: hiding an *already-deployed* job. `deployed` and
  `parseReady` are untouched, so Re-parse and mode edits keep working, and
  debug/admin tablet builds still see the job (marked hidden) — only
  production tablets stop showing it.

No new fields. No renamed fields.

## Behavioral Changes

### 1. Auto-deploy becomes explicit-only

- `ensure_pending_for_new_job` stops stamping a default `autoReleaseAt`. New
  pending jobs get `modeDetection` and `lastActionAt` as today, nothing else
  timer-wise.
- `update_state`'s `operator_action` branch stops re-arming `autoReleaseAt`.
  It still stamps `timers.lastActionAt` on every operator action (kept as an
  audit trail) — it just no longer has a deploy side effect.
- New `DeploymentGateManager.schedule_deploy(job_folder_name, deploy_at, selected_mode=None)`:
  sets `timers.autoReleaseAt` to the operator-chosen ISO timestamp (and
  `selectedMode` if provided), stamps `lastActionAt`. This is the only
  remaining writer of `autoReleaseAt`.
- New `DeploymentGateManager.cancel_scheduled_deploy(job_folder_name)`: clears
  `timers.autoReleaseAt` only, leaves `retryAt`/`remindAt`/`lastActionAt`
  untouched (narrower than the existing blanket `clear_timers`).
- `pending_autorelease_scheduler` / `process_pending_autorelease_once`
  (`scheduler.py`) are functionally unchanged — they already just fire
  whatever's due in `autoReleaseAt`. They become a "scheduled deploy" sweep
  purely because nothing else populates that field by default anymore.

**One-time migration.** Existing PENDING jobs already carry a stale
`autoReleaseAt` from the old default-stamping behavior, and it's
indistinguishable on disk from a genuine operator schedule. New
`DeploymentGateManager.migrate_clear_legacy_autorelease_timers()` sweeps all
currently-PENDING jobs once at startup and clears `autoReleaseAt` on every one
of them. Called once from `Application.start()` (or `__init__`, before the
autorelease scheduler thread starts), guarded so it only needs to run once per
process (idempotent by nature — clearing an already-empty field is a no-op —
so no persistent "have I migrated" flag is needed). This guarantees nothing
deploys as a surprise the moment this ships; operators re-schedule anything
they want timed, going forward, with intent.

### 2. Schedule Deploy (new operator action)

Lives in the existing state-aware dialog (`_show_pending_job_prompt_dialog`),
PENDING branch, alongside Snooze/Release/Cancel — this single dialog already
serves both the auto-popup-on-new-job path and double-clicking a Jobs-tab row
(per the predecessor spec), so no second UI surface is needed to satisfy
"available from the prompt or the Jobs tab."

- New "Schedule Deploy…" button opens a date/time picker (`QDateTimeEdit`,
  minimum = now) inline in the dialog. Confirming calls
  `Application.schedule_pending_job_deploy(job_folder_name, deploy_at, selected_mode)`
  → `deployment_gate.schedule_deploy(...)`.
- If the job already has `timers.autoReleaseAt` set, the dialog shows "Deploy
  scheduled for `<time>`" and offers "Cancel Schedule" in place of (or next
  to) the Schedule Deploy button.
- Picking a new time while a schedule already exists overwrites it (no need
  for a separate reschedule action — Schedule Deploy is idempotent).

### 3. Hide from Production (new operator action)

Lives in the same dialog's PARSING/ACTIVE branch, alongside Save Mode/Re-parse.

- New `DeploymentGateManager.hide_from_production(job_folder_name)` /
  `show_in_production(job_folder_name)` — thin wrappers over
  `update_state(job, hiddenFromProduction=True/False, operator_action=True)`
  (already an allowed key in `update_state`'s update set; just needs a
  dedicated method name, matching the existing `mark_deployed` /
  `mark_parse_ready` naming pattern).
- `Application.hide_job_from_production` / `show_job_in_production` call
  through, refresh the dashboard, log the action.
- Dialog button label/action toggles based on current `hiddenFromProduction`:
  "Hide from Production" when visible, "Show in Production" when hidden.
- Jobs-tab dashboard gets a visual indicator (e.g. a "Hidden" badge appended
  to the state pill, styled distinctly — not a new column, to avoid
  reintroducing the boolean-soup table the predecessor spec collapsed) so a
  hidden-but-deployed job is visible at a glance, not a silent trap.

### 4. Snooze actually re-prompts

- New `pending_reminder_scheduler` in `scheduler.py`, structurally mirroring
  `pending_autorelease_scheduler` (same 60s sweep interval, same
  list-states-then-filter-then-per-item-try/except shape): finds PENDING jobs
  whose `timers.remindAt` is due, invokes a callback to re-surface the popup,
  then clears `remindAt` so it doesn't refire.
- New `Application._remind_pending_job_due(job_folder_name)` reuses the
  existing pending-job-prompt-flush path (`_pending_job_prompts` list /
  `emit_pending_job_prompt`) that already handles showing the popup for newly
  detected jobs — no new dialog-triggering mechanism needed, just a second
  caller into the existing one.
- Started in `Application.start_threads()` alongside
  `pending_autorelease_thread`, same daemon-thread construction pattern.

## Components & Boundaries

- **`deployment_gate.py`** — owns metadata read/write. Adds
  `schedule_deploy`, `cancel_scheduled_deploy`, `hide_from_production`,
  `show_in_production`, `migrate_clear_legacy_autorelease_timers`. Removes the
  default-stamping behavior from `ensure_pending_for_new_job` and the
  auto-rearm side effect from `update_state`. No serialization change.
- **`scheduler.py`** — `pending_autorelease_scheduler` unchanged in shape.
  Adds `pending_reminder_scheduler` (and a `process_pending_reminder_once`
  pure-sweep function, mirroring `process_pending_autorelease_once` for
  testability).
- **`main.py`** — adds `schedule_pending_job_deploy`,
  `cancel_pending_job_schedule`, `hide_job_from_production`,
  `show_job_in_production`, `_remind_pending_job_due`. Calls the migration
  sweep once at startup. Starts the new reminder-scheduler thread.
- **`gui.py`** — extends `_show_pending_job_prompt_dialog`'s PENDING and
  PARSING/ACTIVE branches; adds the Hidden badge to
  `_populate_jobs_table`/the state-pill rendering. No new dialog, no new tab.

## Testing

**Unit (`tests/test_deployment_gate.py`):**
- `ensure_pending_for_new_job` no longer sets `timers.autoReleaseAt`.
- `update_state(..., operator_action=True)` on a pending job updates
  `lastActionAt` but leaves `autoReleaseAt` untouched.
- `schedule_deploy` sets `autoReleaseAt` to the exact provided timestamp and
  `selectedMode` when given.
- `cancel_scheduled_deploy` clears only `autoReleaseAt`; `retryAt`/
  `remindAt`/`lastActionAt` are untouched.
- `hide_from_production` / `show_in_production` toggle `hiddenFromProduction`
  without touching `deployed`/`parseReady`.
- `migrate_clear_legacy_autorelease_timers` clears `autoReleaseAt` for every
  PENDING job that has one, leaves deployed jobs and `remindAt` alone, and is
  a no-op on a gate with no stale timers.

**Unit (new, mirroring `tests/test_pending_autorelease_scheduler.py`):**
- `process_pending_reminder_once` fires the callback exactly for PENDING jobs
  with a due `remindAt`, clears `remindAt` after firing, skips jobs with a
  future or absent `remindAt`, and skips missing job folders (same shape as
  the existing autorelease test file).

**Regression:**
- `process_pending_autorelease_once` still fires exactly on jobs with an
  explicit `autoReleaseAt` and does nothing for jobs without one (this is the
  main behavioral proof that removing the default-stamping worked).
- Round-trip load/save of `deployment_gate.json` preserves all schema keys.

**Manual (headless import smoke check + walkthrough, per this repo's
convention — `gui.py` isn't unit-tested):**
- New pending job shows no scheduled-deploy time; Snooze re-shows the popup
  after the chosen interval; Schedule Deploy fires at the chosen time and
  not before; Cancel Schedule stops it from firing.
- Hide from Production on a deployed job removes it from a simulated
  production-build visibility check (`get_visibility(..., is_debug_build=False)`)
  while a debug check still returns it; Show in Production restores it.
- Re-parse and Save Mode continue to work on a hidden-but-deployed job.
- Dashboard renders the Hidden badge correctly and clears it after Show in
  Production.

## Out of Scope

- No change to the Android app (verified existing `DeploymentGateRules`
  already supports the reused `hiddenFromProduction` semantics — nothing to
  change there).
- `schedule_deploy` takes the operator-chosen timestamp directly; it does not
  call `_auto_release_at_from` (the "now + `PENDING_AUTO_RELEASE_HOURS`"
  helper). Once the two default-stamping call sites are removed, both
  `_auto_release_at_from` and `PENDING_AUTO_RELEASE_HOURS` have no remaining
  callers — delete them as part of implementation cleanup rather than leaving
  unused code behind.
- No change to parsing, PDF conversion, backup, or tracker subsystems.
- No per-job override of *whether* auto-deploy exists — after this change
  there simply is no default auto-deploy to opt out of; every deploy is
  either manual Release or an explicit Schedule Deploy.
