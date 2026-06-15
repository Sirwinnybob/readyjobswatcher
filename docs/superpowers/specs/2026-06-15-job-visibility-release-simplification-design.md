# Job Visibility & Release Simplification — Design

**Date:** 2026-06-15
**Status:** Approved (pending final spec review)
**Approach:** C — State-machine UI

## Problem

The job visibility and release system has accumulated redundant, overlapping
ways to do the same thing:

- **Two ways to hide a job from production:** `deployed=False` (pending) and
  `deployed=True, hiddenFromProduction=True` (deployed-but-suppressed). The
  second exists only to gate visibility for non-debug builds — a distinction the
  operator no longer needs.
- **Two snooze actions:** "Retry 3 min" and "Remind 15 min" do the same thing
  (re-show the pending prompt later) with hardcoded times and different timer
  fields (`retryAt` vs `remindAt`).
- **Duplicated controls:** Deploy / Hide / Unhide / Retry / Remind buttons appear
  on both the Jobs dashboard toolbar and the pending-job popup dialog.
- **Boolean-soup table:** The dashboard shows four separate boolean columns
  (Deployed, Parse Ready, Hidden From Prod, Visible) that the operator has to
  mentally combine into "what state is this job in?"

The result is clunky and annoying to use day to day.

## Hard Constraint: Android Metadata Unchanged

The Android app reads each job's `.metadata/deployment_gate.json`. **The on-disk
JSON schema must not change.** Every field stays present with the same name,
type, and meaning:

```
schemaVersion, jobFolderName, deployed, parseReady, hiddenFromProduction,
selectedMode, modeDetection{candidate,source,detectedAt},
timers{retryAt,remindAt,autoReleaseAt,lastActionAt}, createdAt, updatedAt
```

All simplification happens in the **desktop UI and write-paths** — never in the
serialized shape.

## Core Idea: Derive State, Keep Raw Metadata

The desktop UI presents a single derived **State** instead of four booleans. The
state is computed from existing fields — nothing new is stored:

| Derived State | Condition |
|---------------|-----------|
| `PENDING`     | `deployed == False` |
| `PARSING`     | `deployed == True and parseReady == False` |
| `ACTIVE`      | `deployed == True and parseReady == True` |

`hiddenFromProduction` is no longer a user-facing concept. Because pending jobs
are already invisible via `deployed == False`, the separate hide flag is
redundant. The field remains in the JSON (always `False` going forward) so the
Android app and `get_visibility()` keep working unchanged.

## Behavioral Changes (No Schema Change)

1. **Retire `hiddenFromProduction` writes.**
   - `ensure_pending_for_new_job` stops stamping `hiddenFromProduction = True`.
     New jobs are already hidden because `deployed = False`.
   - The field still serializes (default `False`); `get_visibility()` keeps its
     `and not hiddenFromProduction` clause for backward compatibility — it simply
     never trips from the UI anymore.

2. **Collapse retry into a single remind.**
   - "Retry 3 min" is removed entirely.
   - One "Remind in [N] min" control writes `remindAt` exactly as today via
     `schedule_reminder(minutes=N)`, where `N` is operator-entered.
   - `retryAt` stays in the JSON, simply never written again.

3. **Release = deploy + immediately visible.**
   - The "Deploy" action (relabeled "Release") sets `deployed = True`. Once
     parsing completes (`parseReady = True`) the job is visible in Android. No
     separate visibility step.

## Code Removal (Approved: remove unused)

After the UI no longer references them, delete:

- `DeploymentGateManager.schedule_retry()` (`deployment_gate.py`)
- `DeploymentGateManager.set_hidden_from_production()` (`deployment_gate.py`)
- `ReadyJobsApp.retry_pending_job()` (`main.py`)
- `ReadyJobsApp.set_job_hidden_from_production()` (`main.py`)
- The auto-release dialog's "Undo (Re-Hide)" button (`gui.py`) — the dialog
  remains as an informational notice with a single "Dismiss" action.

`remind_pending_job()` and `schedule_reminder()` are kept (still used).
`clear_timers()`, `auto_release_pending_job()`, and the auto-release scheduler
are unchanged. `auto_release_pending_job()` still calls
`update_state(..., hiddenFromProduction=False, ...)`, which is harmless and keeps
the field consistent.

## UI Design

Visual reference: approved mockup (`approach_c_mockup`).

**Design system** (from ui-ux-pro-max, "Data-Dense Dashboard"):
- Primary / industrial grey `#64748B`, CTA / safety orange `#F97316`
- Background `#F8FAFC`, text `#334155`
- State badge colors: Pending amber, Parsing blue, Active green

### Jobs Tab (`setup_jobs_tab`)

- Tab renamed `"Jobs & Visibility"` → `"Jobs"`.
- Table columns become: **Job · State · Selected Mode · Detected Mode · Mode
  Source · Remind At · Updated At**. The four boolean columns (Deployed, Parse
  Ready, Hidden From Prod, Visible) are replaced by the single **State** badge.
- The State cell renders a colored pill (amber `PENDING` / blue `PARSING` /
  green `ACTIVE`) and the row is tinted to match.
- Toolbar trimmed to a single **Refresh** button. Removed: Deploy Selected,
  Re-parse Selected, Retry, Remind, Hide Selected, Unhide Selected, both
  Selected/Detected mode comboboxes and their Set buttons.
- **Double-clicking a row** opens the job action dialog (the same dialog used for
  the auto-popup). All per-job actions live there.

### Job Action Dialog (`_show_pending_job_prompt_dialog`, reused for double-click)

- Header: orange eyebrow ("New job pending" / context label) + bold job folder
  name.
- Info grid: Detected mode, Detection source.
- Deploy-mode selector (FACE-FRAME / FRAMELESS / BOTH / UNKNOWN), defaulting to
  selected-or-detected mode as today.
- Action row, left to right: **Remind in [N] min** spinbox + **Snooze** button |
  spacer | **Cancel** | **Release** (orange primary).
- Removed from the dialog: Retry, Hide, Visible buttons.
- "DEPLOY" relabeled "Release".

### Styling (QSS)

- Orange `#F97316` primary button (Release), grey secondary buttons.
- Status-pill styling for the State badge; subtle row hover highlight.
- Respect existing dialog modality / stay-on-top behavior.

## Components & Boundaries

- **`deployment_gate.py`** — owns metadata read/write. Add a pure
  `derive_state(state: dict) -> str` helper (returns `"PENDING"`/`"PARSING"`/
  `"ACTIVE"`). Remove the two retired methods. No serialization change.
- **`gui.py`** — owns presentation. Rebuild `setup_jobs_tab` and the dialog;
  add double-click wiring; apply QSS. One dialog method serves both popup and
  double-click.
- **`main.py`** — app actions. Remove `retry_pending_job` and
  `set_job_hidden_from_production`. Everything else unchanged.
- **`scheduler.py`** — unchanged; auto-release still reads `autoReleaseAt`.

## Testing

**Unit:**
- `derive_state()` truth table: all `(deployed, parseReady)` combinations map to
  the correct state.
- `ensure_pending_for_new_job` no longer sets `hiddenFromProduction = True`, and
  the produced dict still contains every schema key with correct types.
- `get_visibility()` behavior unchanged across the existing cases.

**Existing tests to update** (`tests/test_deployment_gate.py`):
- Line ~25: assertion that a new job has `hiddenFromProduction == True` — invert
  to expect `False`.
- Lines ~54, ~95: `set_hidden_from_production` calls — remove (method deleted).
- Line ~103: `schedule_retry` call — remove (method deleted).

**Regression:**
- Auto-release scheduler still fires on `autoReleaseAt` and deploys the job.
- A round-trip load/save of `deployment_gate.json` preserves all keys.

**Manual:**
- Double-click each state row → dialog opens with correct context.
- Release deploys and (after parse) the job appears in Android.
- Snooze writes `remindAt` and re-prompts after N minutes.
- Cancel closes without state change.
- Badges and row tints render the correct color per state.

## Out of Scope

- No change to the Android app.
- No change to parsing, PDF conversion, backup, or tracker subsystems.
- No change to the auto-release timing (still 30h via `PENDING_AUTO_RELEASE_HOURS`).
