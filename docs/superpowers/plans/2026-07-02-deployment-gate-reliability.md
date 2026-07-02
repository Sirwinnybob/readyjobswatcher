# Deployment Gate Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the silent 30-hour auto-deploy timer with an explicit operator-scheduled deploy, add a "Hide from Production" action for already-deployed jobs, and fix the currently-broken Snooze reminder — all without changing the on-disk `deployment_gate.json` schema the Android app reads.

**Architecture:** All changes are write-path behavior in `deployment_gate.py` (the sole owner of gate state), a new mirror-pattern sweep in `scheduler.py` (reminder scheduler, structurally identical to the existing auto-release scheduler), thin `Application` wrapper methods in `main.py`, and additions to the existing state-aware job dialog in `gui.py`. No new files, no schema changes.

**Tech Stack:** Python, PyQt6, existing `unittest`-based test suite (`python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-07-02-deployment-gate-reliability-design.md`

---

### Task 1: Remove the silent auto-deploy timer

**Files:**
- Modify: `ready_jobs_watcher/deployment_gate.py`
- Test: `tests/test_deployment_gate.py`

- [ ] **Step 1: Update the existing tests to expect no default timer**

In `tests/test_deployment_gate.py`, change the assertion in `test_new_job_starts_pending_and_blocked`:

```python
# Before:
            self.assertIsNotNone(state["timers"]["autoReleaseAt"])
# After:
            self.assertIsNone(state["timers"]["autoReleaseAt"])
```

Replace `test_duplicate_pending_events_do_not_reset_auto_release_timer` entirely with:

```python
    def test_duplicate_pending_events_preserve_operator_scheduled_deploy(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1001 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            gate.ensure_pending_for_new_job(job, detected_mode="BOTH", detection_source="FIRST")
            scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
            gate.update_state(job, timers={"autoReleaseAt": scheduled_at})

            second = gate.ensure_pending_for_new_job(job, detected_mode="FRAMELESS", detection_source="SECOND")

            self.assertEqual(second["timers"]["autoReleaseAt"], scheduled_at)
            self.assertEqual(second["modeDetection"]["candidate"], "FRAMELESS")
```

Replace `test_operator_action_resets_auto_release_for_pending_jobs` entirely with:

```python
    def test_operator_action_no_longer_touches_auto_release(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1002 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            state = gate.ensure_pending_for_new_job(job)
            self.assertIsNone(state["timers"]["autoReleaseAt"])
            before_last_action = state["timers"]["lastActionAt"]

            updated = gate.mark_operator_action(job)

            self.assertIsNone(updated["timers"]["autoReleaseAt"])
            self.assertIsNotNone(updated["timers"]["lastActionAt"])
            self.assertGreaterEqual(updated["timers"]["lastActionAt"], before_last_action)
```

Replace `test_operator_action_helpers_extend_pending_auto_release` entirely with:

```python
    def test_operator_action_helpers_do_not_touch_auto_release(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1002B - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            gate.ensure_pending_for_new_job(job)

            mode_update = gate.set_selected_mode(job, "FACE-FRAME")
            self.assertIsNone(mode_update["timers"]["autoReleaseAt"])

            remind_update = gate.schedule_reminder(job, minutes=1)
            self.assertIsNone(remind_update["timers"]["autoReleaseAt"])
```

Leave `test_mode_detection_can_skip_operator_action_touch` and
`test_clear_timers_clears_auto_release_and_action_clock` unchanged — both already
assert behavior that stays correct after this task (the first only asserts
`autoReleaseAt` is unchanged either way, which is now trivially true; the
second exercises `clear_timers`, which this task doesn't touch).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_deployment_gate.py -v`
Expected: `test_new_job_starts_pending_and_blocked`,
`test_duplicate_pending_events_preserve_operator_scheduled_deploy`,
`test_operator_action_no_longer_touches_auto_release`, and
`test_operator_action_helpers_do_not_touch_auto_release` FAIL (current code
still stamps/rearms `autoReleaseAt`).

- [ ] **Step 3: Remove the default-stamping behavior**

In `ready_jobs_watcher/deployment_gate.py`, `ensure_pending_for_new_job`:

```python
# Before:
        if not had_existing_state or not was_pending or not current_timers.get("autoReleaseAt"):
            current_timers["autoReleaseAt"] = self._auto_release_at_from(now_dt)
        if not had_existing_state or not was_pending or not current_timers.get("lastActionAt"):
            current_timers["lastActionAt"] = now_dt.isoformat()
        state["timers"] = current_timers
        return self.save_state(job_folder_name, state)
# After:
        # autoReleaseAt is operator-set only (see schedule_deploy); re-detecting an
        # already-pending job must never touch it, so any explicit schedule survives.
        if not had_existing_state or not was_pending or not current_timers.get("lastActionAt"):
            current_timers["lastActionAt"] = now_dt.isoformat()
        state["timers"] = current_timers
        return self.save_state(job_folder_name, state)
```

In `update_state`, remove the auto-rearm side effect:

```python
# Before:
            if operator_action and not bool(state.get("deployed", True)):
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                current_timers = state.get("timers", {})
                current_timers["lastActionAt"] = now_dt.isoformat()
                current_timers["autoReleaseAt"] = self._auto_release_at_from(now_dt)
                state["timers"] = current_timers
# After:
            if operator_action and not bool(state.get("deployed", True)):
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                current_timers = state.get("timers", {})
                current_timers["lastActionAt"] = now_dt.isoformat()
                state["timers"] = current_timers
```

- [ ] **Step 4: Delete the now-unused default-timer helper and constant**

`_auto_release_at_from` and `PENDING_AUTO_RELEASE_HOURS` have no remaining
callers after Step 3. Delete both:

```python
# Delete this module-level constant (near the top of the file, with the other MODE_* constants):
PENDING_AUTO_RELEASE_HOURS = 30
```

```python
# Delete this staticmethod entirely:
    @staticmethod
    def _auto_release_at_from(action_at: datetime.datetime) -> str:
        return (action_at + datetime.timedelta(hours=PENDING_AUTO_RELEASE_HOURS)).isoformat()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_deployment_gate.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the full suite to confirm nothing else referenced the deleted symbols**

Run: `python -m pytest`
Expected: no `ImportError`/`AttributeError` for `PENDING_AUTO_RELEASE_HOURS` or
`_auto_release_at_from` anywhere in the suite. (Confirmed by earlier grep that
`tests/test_pending_autorelease_scheduler.py` sets `autoReleaseAt` directly via
`update_state(timers=...)` and never calls either deleted symbol, so it's
unaffected.)

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/deployment_gate.py tests/test_deployment_gate.py
git commit -m "$(cat <<'EOF'
Remove silent 30h auto-deploy timer from deployment gate

autoReleaseAt was stamped automatically on every new pending job and
re-armed on any operator action, silently deploying jobs nobody
explicitly released. It becomes purely operator-set in a follow-up
commit (schedule_deploy).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add Schedule Deploy / Cancel Schedule to the gate manager

**Files:**
- Modify: `ready_jobs_watcher/deployment_gate.py`
- Test: `tests/test_deployment_gate.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deployment_gate.py`, inside `TestDeploymentGateManager`:

```python
    def test_schedule_deploy_sets_auto_release_and_mode(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1005 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)

            deploy_at = datetime(2026, 7, 6, 7, 0, 0, tzinfo=timezone.utc)
            state = gate.schedule_deploy(job, deploy_at, selected_mode="FRAMELESS")

            self.assertEqual(state["timers"]["autoReleaseAt"], deploy_at.isoformat())
            self.assertEqual(state["selectedMode"], "FRAMELESS")
            self.assertIsNotNone(state["timers"]["lastActionAt"])

    def test_schedule_deploy_without_mode_leaves_existing_mode(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1006 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.set_selected_mode(job, "BOTH")

            deploy_at = datetime(2026, 7, 6, 7, 0, 0, tzinfo=timezone.utc)
            state = gate.schedule_deploy(job, deploy_at)

            self.assertEqual(state["selectedMode"], "BOTH")

    def test_cancel_scheduled_deploy_clears_only_auto_release(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1007 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.schedule_reminder(job, minutes=10)
            deploy_at = datetime(2026, 7, 6, 7, 0, 0, tzinfo=timezone.utc)
            gate.schedule_deploy(job, deploy_at)

            state = gate.cancel_scheduled_deploy(job)

            self.assertIsNone(state["timers"]["autoReleaseAt"])
            self.assertIsNotNone(state["timers"]["remindAt"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_deployment_gate.py -v -k "schedule_deploy or cancel_scheduled_deploy"`
Expected: FAIL with `AttributeError: 'DeploymentGateManager' object has no attribute 'schedule_deploy'`.

- [ ] **Step 3: Implement `schedule_deploy` and `cancel_scheduled_deploy`**

In `ready_jobs_watcher/deployment_gate.py`, add these methods immediately after
`clear_timers` (right before `mark_operator_action`):

```python
    def schedule_deploy(
        self,
        job_folder_name: str,
        deploy_at: datetime.datetime,
        selected_mode: Optional[str] = None,
    ) -> Dict:
        """
        Set an operator-chosen deploy time. `deploy_at` may be timezone-aware
        or naive-local; it is converted to UTC ISO8601 for storage. This is
        the only writer of timers.autoReleaseAt.
        """
        updates: Dict = {"timers": {"autoReleaseAt": deploy_at.astimezone(datetime.timezone.utc).isoformat()}}
        if selected_mode is not None:
            updates["selectedMode"] = self.normalize_mode(selected_mode)
        return self.update_state(job_folder_name, operator_action=True, **updates)

    def cancel_scheduled_deploy(self, job_folder_name: str) -> Dict:
        return self.update_state(job_folder_name, timers={"autoReleaseAt": None})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_deployment_gate.py -v -k "schedule_deploy or cancel_scheduled_deploy"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/deployment_gate.py tests/test_deployment_gate.py
git commit -m "$(cat <<'EOF'
Add explicit Schedule Deploy / Cancel Schedule to deployment gate

Replaces the removed default timer: autoReleaseAt is now set only via
an operator-chosen timestamp through schedule_deploy.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add Hide from Production / Show in Production

**Files:**
- Modify: `ready_jobs_watcher/deployment_gate.py`
- Test: `tests/test_deployment_gate.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deployment_gate.py`, inside `TestDeploymentGateManager`:

```python
    def test_hide_from_production_keeps_deployed_and_parse_ready(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1008 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.mark_deployed(job, selected_mode="BOTH")
            gate.mark_parse_ready(job, parse_ready=True)

            state = gate.hide_from_production(job)

            self.assertTrue(state["deployed"])
            self.assertTrue(state["parseReady"])
            self.assertTrue(state["hiddenFromProduction"])
            self.assertFalse(gate.get_visibility(job, is_debug_build=False))
            self.assertTrue(gate.get_visibility(job, is_debug_build=True))

    def test_show_in_production_restores_visibility(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1009 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.mark_deployed(job, selected_mode="BOTH")
            gate.mark_parse_ready(job, parse_ready=True)
            gate.hide_from_production(job)

            state = gate.show_in_production(job)

            self.assertFalse(state["hiddenFromProduction"])
            self.assertTrue(gate.get_visibility(job, is_debug_build=False))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_deployment_gate.py -v -k "hide_from_production or show_in_production"`
Expected: FAIL with `AttributeError: 'DeploymentGateManager' object has no attribute 'hide_from_production'`.

- [ ] **Step 3: Implement `hide_from_production` and `show_in_production`**

In `ready_jobs_watcher/deployment_gate.py`, add these methods immediately after
`mark_parse_ready`:

```python
    def hide_from_production(self, job_folder_name: str) -> Dict:
        return self.update_state(job_folder_name, hiddenFromProduction=True)

    def show_in_production(self, job_folder_name: str) -> Dict:
        return self.update_state(job_folder_name, hiddenFromProduction=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_deployment_gate.py -v -k "hide_from_production or show_in_production"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/deployment_gate.py tests/test_deployment_gate.py
git commit -m "$(cat <<'EOF'
Add Hide from Production / Show in Production to deployment gate

Reuses the existing hiddenFromProduction field (already respected by
get_visibility() and the Android app's DeploymentGateRules) to let an
already-deployed job be pulled from shop tablets without touching
deployed/parseReady, so Re-parse and mode edits keep working.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: One-time migration to clear legacy auto-release timers

**Files:**
- Modify: `ready_jobs_watcher/deployment_gate.py`
- Test: `tests/test_deployment_gate.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deployment_gate.py`, inside `TestDeploymentGateManager`:

```python
    def test_migrate_clears_legacy_timer_on_pending_jobs_only(self):
        with tempfile.TemporaryDirectory() as root:
            pending_job = "1010 - PENDING"
            deployed_job = "1011 - DEPLOYED"
            os.makedirs(os.path.join(root, pending_job), exist_ok=True)
            os.makedirs(os.path.join(root, deployed_job), exist_ok=True)
            gate = DeploymentGateManager(root)

            gate.ensure_pending_for_new_job(pending_job)
            stale_at = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
            gate.update_state(pending_job, timers={"autoReleaseAt": stale_at})

            gate.ensure_pending_for_new_job(deployed_job)
            gate.mark_deployed(deployed_job, selected_mode="BOTH")

            cleared_count = gate.migrate_clear_legacy_autorelease_timers()

            self.assertEqual(cleared_count, 1)
            self.assertIsNone(gate.load_state(pending_job)["timers"]["autoReleaseAt"])

    def test_migrate_is_noop_when_nothing_stale(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1012 - CLEAN"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)

            cleared_count = gate.migrate_clear_legacy_autorelease_timers()

            self.assertEqual(cleared_count, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_deployment_gate.py -v -k "migrate"`
Expected: FAIL with `AttributeError: 'DeploymentGateManager' object has no attribute 'migrate_clear_legacy_autorelease_timers'`.

- [ ] **Step 3: Implement the migration sweep**

In `ready_jobs_watcher/deployment_gate.py`, add this method immediately after
`list_job_states`:

```python
    def migrate_clear_legacy_autorelease_timers(self) -> int:
        """
        One-time startup cleanup: clear timers.autoReleaseAt on every currently
        PENDING job. Existing pending jobs may carry a value stamped by the old
        default-30h-timer behavior, indistinguishable on disk from a genuine
        operator schedule - clearing it here ensures nothing deploys as a
        surprise right after that behavior is removed. Safe to call on every
        startup: a no-op once nothing stale remains.
        """
        cleared = 0
        for state in self.list_job_states():
            if bool(state.get("deployed", True)):
                continue
            timers = state.get("timers") if isinstance(state.get("timers"), dict) else {}
            if not timers.get("autoReleaseAt"):
                continue
            job_folder_name = str(state.get("jobFolderName") or "")
            if not job_folder_name:
                continue
            self.update_state(job_folder_name, timers={"autoReleaseAt": None})
            cleared += 1
        if cleared:
            main_logger.info("Cleared legacy auto-release timer on %d pending job(s).", cleared)
        return cleared
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_deployment_gate.py -v -k "migrate"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/deployment_gate.py tests/test_deployment_gate.py
git commit -m "$(cat <<'EOF'
Add one-time migration to clear legacy auto-release timers

Wired into Application startup in a later commit. Clears any
autoReleaseAt left over from the removed default-30h-timer behavior
so no pending job deploys as a surprise right after upgrade.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Add the reminder scheduler (fix Snooze)

**Files:**
- Modify: `ready_jobs_watcher/scheduler.py`
- Test: `tests/test_pending_reminder_scheduler.py` (new, mirrors `tests/test_pending_autorelease_scheduler.py`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pending_reminder_scheduler.py`:

```python
import os
import shutil
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from ready_jobs_watcher.deployment_gate import DeploymentGateManager
from ready_jobs_watcher.scheduler import (
    pending_reminder_scheduler,
    process_pending_reminder_once,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class TestPendingReminderScheduler(unittest.TestCase):
    def test_due_reminder_fires_and_clears(self):
        with tempfile.TemporaryDirectory() as root:
            job = "3000 - DUE"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job, detected_mode="FRAMELESS", detection_source="TEST")
            gate.update_state(
                job,
                timers={"remindAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1))},
            )

            reminded = []

            def _reminder(job_name: str) -> None:
                reminded.append(job_name)

            count = process_pending_reminder_once(gate, _reminder, root)

            self.assertEqual(count, 1)
            self.assertEqual(reminded, [job])
            self.assertIsNone(gate.load_state(job)["timers"]["remindAt"])

    def test_not_due_reminder_is_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            job = "3001 - FUTURE"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job, detected_mode="BOTH", detection_source="TEST")
            gate.update_state(
                job,
                timers={"remindAt": _iso(datetime.now(timezone.utc) + timedelta(hours=1))},
            )

            reminded = []
            count = process_pending_reminder_once(gate, lambda j: reminded.append(j), root)

            self.assertEqual(count, 0)
            self.assertEqual(reminded, [])
            self.assertIsNotNone(gate.load_state(job)["timers"]["remindAt"])

    def test_deployed_job_is_skipped_even_with_remind_at(self):
        with tempfile.TemporaryDirectory() as root:
            job = "3002 - DEPLOYED"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.mark_deployed(job, selected_mode="BOTH")
            gate.update_state(
                job,
                timers={"remindAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1))},
            )

            reminded = []
            count = process_pending_reminder_once(gate, lambda j: reminded.append(j), root)

            self.assertEqual(count, 0)
            self.assertEqual(reminded, [])

    def test_missing_job_folder_is_skipped_without_crashing(self):
        with tempfile.TemporaryDirectory() as root:
            job = "3003 - MISSING"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.update_state(
                job,
                timers={"remindAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1))},
            )
            shutil.rmtree(os.path.join(root, job))

            reminded = []
            count = process_pending_reminder_once(gate, lambda j: reminded.append(j), root)

            self.assertEqual(count, 0)
            self.assertEqual(reminded, [])

    def test_callback_failure_leaves_remind_at_set_for_retry(self):
        with tempfile.TemporaryDirectory() as root:
            job = "3004 - FAILS"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.update_state(
                job,
                timers={"remindAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1))},
            )

            def _failing_reminder(job_name: str) -> None:
                raise RuntimeError("boom")

            count = process_pending_reminder_once(gate, _failing_reminder, root)

            self.assertEqual(count, 0)
            self.assertIsNotNone(gate.load_state(job)["timers"]["remindAt"])

    def test_background_scheduler_runs_sweep(self):
        with tempfile.TemporaryDirectory() as root:
            job = "3005 - LOOP"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.update_state(
                job,
                timers={"remindAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1))},
            )

            reminded = []

            stop_event = mock.Mock()
            wait_calls = {"count": 0}

            def _is_set() -> bool:
                return wait_calls["count"] > 0

            def _wait(_seconds: float) -> bool:
                wait_calls["count"] += 1
                return True

            stop_event.is_set.side_effect = _is_set
            stop_event.wait.side_effect = _wait

            pending_reminder_scheduler(gate, lambda j: reminded.append(j), stop_event, sweep_interval_seconds=1)

            self.assertEqual(reminded, [job])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pending_reminder_scheduler.py -v`
Expected: FAIL with `ImportError: cannot import name 'pending_reminder_scheduler'`.

- [ ] **Step 3: Implement the reminder sweep and scheduler loop**

In `ready_jobs_watcher/scheduler.py`, add after `process_pending_autorelease_once`
(right before `pending_autorelease_scheduler`):

```python
def process_pending_reminder_once(
    deployment_gate: DeploymentGateManager,
    reminder_callback: Callable[[str], None],
    root_dir: Optional[str] = None,
) -> int:
    """
    Sweep pending jobs and re-prompt any whose reminder is due.

    Args:
        deployment_gate: Gate manager used to read current job states.
        reminder_callback: Callback invoked for due jobs as (job_folder_name,).
        root_dir: Optional override for resolving job folder paths.
    """
    if root_dir is None:
        root_dir = deployment_gate.root_dir

    now = datetime.datetime.now(datetime.timezone.utc)
    reminded_count = 0

    for state in deployment_gate.list_job_states():
        if bool(state.get("deployed", True)):
            continue

        timers = state.get("timers") if isinstance(state.get("timers"), dict) else {}
        remind_at = _parse_iso_utc(timers.get("remindAt"))
        if remind_at is None or remind_at > now:
            continue

        job_folder_name = str(state.get("jobFolderName") or "")
        if not job_folder_name:
            continue

        job_folder_path = os.path.join(root_dir, job_folder_name)
        if not os.path.isdir(job_folder_path):
            main_logger.info("Skipping pending reminder for missing folder: %s", job_folder_path)
            continue

        try:
            reminder_callback(job_folder_name)
            deployment_gate.update_state(job_folder_name, timers={"remindAt": None})
            reminded_count += 1
        except Exception as exc:
            main_logger.error("Pending reminder failed for %s: %s", job_folder_name, exc, exc_info=True)

    return reminded_count


def pending_reminder_scheduler(
    deployment_gate: DeploymentGateManager,
    reminder_callback: Callable[[str], None],
    stop_event: threading.Event,
    *,
    sweep_interval_seconds: int = 60,
) -> None:
    """
    Background loop that periodically re-prompts pending jobs whose reminder is due.
    """
    interval = max(1, int(sweep_interval_seconds))
    main_logger.info("Pending reminder scheduler started (interval=%ss)", interval)

    while not stop_event.is_set():
        try:
            reminded = process_pending_reminder_once(deployment_gate, reminder_callback)
            if reminded:
                main_logger.info("Pending reminder sweep re-prompted %s job(s)", reminded)
        except Exception as exc:
            main_logger.error("Error in pending reminder scheduler: %s", exc, exc_info=True)

        if stop_event.wait(interval):
            break

    main_logger.info("Pending reminder scheduler stopped")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pending_reminder_scheduler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/scheduler.py tests/test_pending_reminder_scheduler.py
git commit -m "$(cat <<'EOF'
Add pending reminder scheduler

Snooze has written timers.remindAt since it was added, but nothing
ever read it back - clicking Snooze silently did nothing. This sweep
(mirrors the existing auto-release sweep) fires a callback and clears
remindAt when due. Wired into Application in a later commit.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire everything into Application

**Files:**
- Modify: `ready_jobs_watcher/main.py`

- [ ] **Step 1: Add the `datetime` import and the new scheduler import**

```python
# Before:
import os
import sys
import threading
import logging
import time
import atexit
# After:
import os
import sys
import threading
import logging
import time
import atexit
import datetime
```

```python
# Before:
from .scheduler import (
    backup_scheduler,
    cnc_scan_scheduler,
    stats_logger_scheduler,
    daily_restart_scheduler,
    pending_autorelease_scheduler,
    metadata_end_of_day_scheduler,
)
# After:
from .scheduler import (
    backup_scheduler,
    cnc_scan_scheduler,
    stats_logger_scheduler,
    daily_restart_scheduler,
    pending_autorelease_scheduler,
    pending_reminder_scheduler,
    metadata_end_of_day_scheduler,
)
```

- [ ] **Step 2: Register the new thread attribute in `__init__`**

```python
# Before:
        self.restart_thread = None
        self.pending_autorelease_thread = None
        self.metadata_end_of_day_thread = None
# After:
        self.restart_thread = None
        self.pending_autorelease_thread = None
        self.pending_reminder_thread = None
        self.metadata_end_of_day_thread = None
```

- [ ] **Step 3: Add `_remind_pending_job_due`**

Add immediately after `remind_pending_job`:

```python
    def remind_pending_job(self, job_folder_name: str, minutes: int = 15):
        self.deployment_gate.schedule_reminder(job_folder_name, minutes=minutes)
        self._schedule_pending_job_prompt(job_folder_name, delay_seconds=minutes * 60)

    def _remind_pending_job_due(self, job_folder_name: str) -> None:
        self._queue_pending_job_prompt(job_folder_name)
        logging.info("Pending job reminder due, re-prompting: %s", job_folder_name)
```

(Only the new method is added; `remind_pending_job` itself is shown for
placement context and is unchanged.)

- [ ] **Step 4: Add the Schedule Deploy / Hide-Show wrapper methods**

Add immediately after `auto_release_pending_job` (before `_parse_job_after_deploy`):

```python
    def schedule_pending_job_deploy(self, job_folder_name: str, deploy_at: datetime.datetime, selected_mode: str) -> None:
        state = self.deployment_gate.schedule_deploy(job_folder_name, deploy_at, selected_mode=selected_mode)
        logging.info(
            "Job deploy scheduled: job=%s deployAt=%s selectedMode=%s",
            job_folder_name,
            state.get("timers", {}).get("autoReleaseAt"),
            state.get("selectedMode", "UNKNOWN"),
        )
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def cancel_pending_job_schedule(self, job_folder_name: str) -> None:
        self.deployment_gate.cancel_scheduled_deploy(job_folder_name)
        logging.info("Job deploy schedule cancelled: job=%s", job_folder_name)
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def hide_job_from_production(self, job_folder_name: str) -> None:
        self.deployment_gate.hide_from_production(job_folder_name)
        logging.info("Job hidden from production: job=%s", job_folder_name)
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def show_job_in_production(self, job_folder_name: str) -> None:
        self.deployment_gate.show_in_production(job_folder_name)
        logging.info("Job shown in production: job=%s", job_folder_name)
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()
```

- [ ] **Step 5: Run the migration once at the top of `start_threads`, and start the reminder thread**

```python
# Before:
    def start_threads(self):
        """Starts the background threads for retries and scheduled tasks."""
        self.retry_thread = threading.Thread(target=self.retry_pending, daemon=True)
        self.retry_thread.start()
# After:
    def start_threads(self):
        """Starts the background threads for retries and scheduled tasks."""
        self.deployment_gate.migrate_clear_legacy_autorelease_timers()

        self.retry_thread = threading.Thread(target=self.retry_pending, daemon=True)
        self.retry_thread.start()
```

```python
# Before:
        self.pending_autorelease_thread = threading.Thread(
            target=pending_autorelease_scheduler,
            args=(self.deployment_gate, self.auto_release_pending_job, self.stop_event),
            daemon=True,
            name="PendingAutoReleaseScheduler",
        )
        self.pending_autorelease_thread.start()
        self.metadata_end_of_day_thread = threading.Thread(
# After:
        self.pending_autorelease_thread = threading.Thread(
            target=pending_autorelease_scheduler,
            args=(self.deployment_gate, self.auto_release_pending_job, self.stop_event),
            daemon=True,
            name="PendingAutoReleaseScheduler",
        )
        self.pending_autorelease_thread.start()
        self.pending_reminder_thread = threading.Thread(
            target=pending_reminder_scheduler,
            args=(self.deployment_gate, self._remind_pending_job_due, self.stop_event),
            daemon=True,
            name="PendingReminderScheduler",
        )
        self.pending_reminder_thread.start()
        self.metadata_end_of_day_thread = threading.Thread(
```

- [ ] **Step 6: Join the new thread on shutdown**

In `stop()`, add the new thread to the join list:

```python
# Before:
        threads = [
            ('retry_thread', self.retry_thread),
            ('backup_thread', self.backup_thread),
            ('cnc_scan_thread', self.cnc_scan_thread),
            ('stats_thread', self.stats_thread),
            ('restart_thread', self.restart_thread),
            ('pending_autorelease_thread', self.pending_autorelease_thread),
            ('metadata_end_of_day_thread', self.metadata_end_of_day_thread),
            ('observer_monitor_thread', self.observer_monitor_thread),
            ('tray_thread', self.tray_thread),
        ]
# After:
        threads = [
            ('retry_thread', self.retry_thread),
            ('backup_thread', self.backup_thread),
            ('cnc_scan_thread', self.cnc_scan_thread),
            ('stats_thread', self.stats_thread),
            ('restart_thread', self.restart_thread),
            ('pending_autorelease_thread', self.pending_autorelease_thread),
            ('pending_reminder_thread', self.pending_reminder_thread),
            ('metadata_end_of_day_thread', self.metadata_end_of_day_thread),
            ('observer_monitor_thread', self.observer_monitor_thread),
            ('tray_thread', self.tray_thread),
        ]
```

- [ ] **Step 7: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.main"`
Expected: no output, exit code 0 (per this repo's convention for verifying
`gui.py`/`main.py` changes that can't run through pytest).

- [ ] **Step 8: Run the full test suite**

Run: `python -m pytest`
Expected: all tests pass (aside from the 3 pre-existing `mapbox_earcut`-dependent
failures in `tests/test_dae_converter.py` noted in `CLAUDE.md` as environmental).

- [ ] **Step 9: Commit**

```bash
git add ready_jobs_watcher/main.py
git commit -m "$(cat <<'EOF'
Wire Schedule Deploy, Hide/Show, and reminder scheduler into Application

Runs the legacy-timer migration once at startup before the
auto-release sweep thread starts, and starts the new reminder
scheduler alongside it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Schedule Deploy UI in the pending-job dialog

**Files:**
- Modify: `ready_jobs_watcher/gui.py`

- [ ] **Step 1: Add imports**

```python
# Before:
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QListWidget, QTimeEdit, QSpinBox, QTextEdit, QMessageBox,
    QFormLayout, QGroupBox, QInputDialog, QCheckBox, QComboBox, QDialog,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QScrollArea,
    QSplitter, QGridLayout
)
from PyQt6.QtCore import QTime, QObject, pyqtSignal, Qt
# After:
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QListWidget, QTimeEdit, QSpinBox, QTextEdit, QMessageBox,
    QFormLayout, QGroupBox, QInputDialog, QCheckBox, QComboBox, QDialog,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QScrollArea,
    QSplitter, QGridLayout, QDateTimeEdit
)
from PyQt6.QtCore import QTime, QDateTime, QObject, pyqtSignal, Qt
```

Also add, immediately after the module's `import logging` line:

```python
# Before:
import logging
from typing import Dict, List, Optional
# After:
import logging
import datetime
from typing import Dict, List, Optional
```

- [ ] **Step 2: Extract `timers` in `_show_pending_job_prompt_dialog`'s shared prep block**

```python
# Before:
        state = self._get_job_row_by_name(job_folder_name) or {}
        derived = derive_state(state)
        mode_detection = state.get("modeDetection", {}) if isinstance(state.get("modeDetection"), dict) else {}
# After:
        state = self._get_job_row_by_name(job_folder_name) or {}
        derived = derive_state(state)
        timers = state.get("timers", {}) if isinstance(state.get("timers"), dict) else {}
        mode_detection = state.get("modeDetection", {}) if isinstance(state.get("modeDetection"), dict) else {}
```

- [ ] **Step 3: Add the Schedule Deploy controls to the PENDING branch**

```python
# Before:
        if derived == "PENDING":
            remind_label = QLabel("Remind in")
            remind_spin = QSpinBox(dialog)
            remind_spin.setRange(1, 720)
            remind_spin.setValue(15)
            remind_spin.setSuffix(" min")
            snooze_btn = QPushButton("Snooze")
            cancel_btn = QPushButton("Cancel")
            release_btn = QPushButton("Release")
            release_btn.setObjectName("primaryButton")

            def _snooze_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                if selected != selected_mode:
                    self.app_instance.set_job_selected_mode(job_folder_name, selected)
                self.app_instance.remind_pending_job(job_folder_name, minutes=remind_spin.value())
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _release_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                import threading
                threading.Thread(
                    target=self.app_instance.deploy_pending_job,
                    args=(job_folder_name, selected),
                    daemon=True,
                ).start()
                self.refresh_jobs_dashboard()
                dialog.accept()

            snooze_btn.clicked.connect(_snooze_action)
            cancel_btn.clicked.connect(dialog.reject)
            release_btn.clicked.connect(_release_action)

            action_row.addWidget(remind_label)
            action_row.addWidget(remind_spin)
            action_row.addWidget(snooze_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)
            action_row.addWidget(release_btn)
# After:
        if derived == "PENDING":
            remind_label = QLabel("Remind in")
            remind_spin = QSpinBox(dialog)
            remind_spin.setRange(1, 720)
            remind_spin.setValue(15)
            remind_spin.setSuffix(" min")
            snooze_btn = QPushButton("Snooze")

            scheduled_at = str(timers.get("autoReleaseAt") or "").strip()
            schedule_label = QLabel(
                f"Deploy scheduled for {scheduled_at}" if scheduled_at else "No deploy scheduled"
            )
            schedule_edit = QDateTimeEdit(dialog)
            schedule_edit.setCalendarPopup(True)
            schedule_edit.setDateTime(QDateTime.currentDateTime().addSecs(3600))
            schedule_edit.setMinimumDateTime(QDateTime.currentDateTime())
            schedule_btn = QPushButton("Schedule Deploy")
            cancel_schedule_btn = QPushButton("Cancel Schedule")
            cancel_schedule_btn.setEnabled(bool(scheduled_at))

            cancel_btn = QPushButton("Cancel")
            release_btn = QPushButton("Release")
            release_btn.setObjectName("primaryButton")

            def _snooze_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                if selected != selected_mode:
                    self.app_instance.set_job_selected_mode(job_folder_name, selected)
                self.app_instance.remind_pending_job(job_folder_name, minutes=remind_spin.value())
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _schedule_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                deploy_at = schedule_edit.dateTime().toPyDateTime().astimezone(datetime.timezone.utc)
                self.app_instance.schedule_pending_job_deploy(job_folder_name, deploy_at, selected)
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _cancel_schedule_action():
                self.app_instance.cancel_pending_job_schedule(job_folder_name)
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _release_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                import threading
                threading.Thread(
                    target=self.app_instance.deploy_pending_job,
                    args=(job_folder_name, selected),
                    daemon=True,
                ).start()
                self.refresh_jobs_dashboard()
                dialog.accept()

            snooze_btn.clicked.connect(_snooze_action)
            schedule_btn.clicked.connect(_schedule_action)
            cancel_schedule_btn.clicked.connect(_cancel_schedule_action)
            cancel_btn.clicked.connect(dialog.reject)
            release_btn.clicked.connect(_release_action)

            layout.addWidget(schedule_label)
            schedule_row = QHBoxLayout()
            schedule_row.addWidget(schedule_edit)
            schedule_row.addWidget(schedule_btn)
            schedule_row.addWidget(cancel_schedule_btn)
            layout.addLayout(schedule_row)

            action_row.addWidget(remind_label)
            action_row.addWidget(remind_spin)
            action_row.addWidget(snooze_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)
            action_row.addWidget(release_btn)
```

- [ ] **Step 4: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.gui"`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "$(cat <<'EOF'
Add Schedule Deploy / Cancel Schedule to the pending-job dialog

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Hide from Production UI in the deployed-job dialog

**Files:**
- Modify: `ready_jobs_watcher/gui.py`

- [ ] **Step 1: Add the visibility toggle to the PARSING/ACTIVE branch**

```python
# Before:
        else:
            save_mode_btn = QPushButton("Save Mode")
            reparse_btn = QPushButton("Re-parse")
            cancel_btn = QPushButton("Cancel")

            def _save_mode_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                self.app_instance.set_job_selected_mode(job_folder_name, selected)
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _reparse_action():
                reply = QMessageBox.question(
                    dialog,
                    "Re-parse Job",
                    f"Are you sure you want to fully re-parse job '{job_folder_name}'?\n\n"
                    "This will remove all generated metadata, GLBs, and dark mode PDFs, then re-process them.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    import threading
                    threading.Thread(
                        target=self.app_instance.reparse_job,
                        args=(job_folder_name,),
                        daemon=True,
                    ).start()
                    QMessageBox.information(
                        dialog,
                        "Re-parse Job",
                        f"Re-parsing for job '{job_folder_name}' has been started in the background.",
                    )
                    self.refresh_jobs_dashboard()
                    dialog.accept()

            save_mode_btn.clicked.connect(_save_mode_action)
            reparse_btn.clicked.connect(_reparse_action)
            cancel_btn.clicked.connect(dialog.reject)

            action_row.addWidget(save_mode_btn)
            action_row.addWidget(reparse_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)
# After:
        else:
            save_mode_btn = QPushButton("Save Mode")
            reparse_btn = QPushButton("Re-parse")
            hidden_from_production = bool(state.get("hiddenFromProduction", False))
            visibility_btn = QPushButton("Show in Production" if hidden_from_production else "Hide from Production")
            cancel_btn = QPushButton("Cancel")

            def _save_mode_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                self.app_instance.set_job_selected_mode(job_folder_name, selected)
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _reparse_action():
                reply = QMessageBox.question(
                    dialog,
                    "Re-parse Job",
                    f"Are you sure you want to fully re-parse job '{job_folder_name}'?\n\n"
                    "This will remove all generated metadata, GLBs, and dark mode PDFs, then re-process them.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    import threading
                    threading.Thread(
                        target=self.app_instance.reparse_job,
                        args=(job_folder_name,),
                        daemon=True,
                    ).start()
                    QMessageBox.information(
                        dialog,
                        "Re-parse Job",
                        f"Re-parsing for job '{job_folder_name}' has been started in the background.",
                    )
                    self.refresh_jobs_dashboard()
                    dialog.accept()

            def _visibility_action():
                if hidden_from_production:
                    self.app_instance.show_job_in_production(job_folder_name)
                else:
                    self.app_instance.hide_job_from_production(job_folder_name)
                self.refresh_jobs_dashboard()
                dialog.accept()

            save_mode_btn.clicked.connect(_save_mode_action)
            reparse_btn.clicked.connect(_reparse_action)
            visibility_btn.clicked.connect(_visibility_action)
            cancel_btn.clicked.connect(dialog.reject)

            action_row.addWidget(save_mode_btn)
            action_row.addWidget(reparse_btn)
            action_row.addWidget(visibility_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)
```

- [ ] **Step 2: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.gui"`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "$(cat <<'EOF'
Add Hide from Production / Show in Production to the deployed-job dialog

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Hidden badge on the Jobs dashboard

**Files:**
- Modify: `ready_jobs_watcher/gui.py`

- [ ] **Step 1: Style hidden-but-deployed rows distinctly**

```python
# Before:
    def _populate_jobs_table(self, rows: List[Dict]):
        if self.jobs_table is None:
            return
        from .deployment_gate import derive_state

        state_styles = {
            "PENDING": (QColor("#FEF3C7"), QColor("#92400E")),
            "PARSING": (QColor("#DBEAFE"), QColor("#1E40AF")),
            "ACTIVE":  (QColor("#D1FAE5"), QColor("#065F46")),
        }

        self.jobs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            timers = row.get("timers", {}) if isinstance(row.get("timers"), dict) else {}
            state_name = derive_state(row)
            bg, fg = state_styles.get(state_name, (None, None))

            values = [
                str(row.get("jobFolderName", "")),
                state_name,
                str(row.get("selectedMode", "UNKNOWN")),
                str(mode_detection.get("candidate", "UNKNOWN")),
                str(mode_detection.get("source", "UNKNOWN")),
                str(timers.get("remindAt") or "-"),
                str(row.get("updatedAt") or "-"),
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if bg is not None:
                    item.setBackground(bg)
                    item.setForeground(fg)
                self.jobs_table.setItem(row_index, col_index, item)
# After:
    def _populate_jobs_table(self, rows: List[Dict]):
        if self.jobs_table is None:
            return
        from .deployment_gate import derive_state

        state_styles = {
            "PENDING": (QColor("#FEF3C7"), QColor("#92400E")),
            "PARSING": (QColor("#DBEAFE"), QColor("#1E40AF")),
            "ACTIVE":  (QColor("#D1FAE5"), QColor("#065F46")),
        }
        hidden_style = (QColor("#E2E8F0"), QColor("#334155"))

        self.jobs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            timers = row.get("timers", {}) if isinstance(row.get("timers"), dict) else {}
            state_name = derive_state(row)
            is_hidden_from_production = bool(row.get("hiddenFromProduction", False))
            if is_hidden_from_production:
                bg, fg = hidden_style
                display_state = f"{state_name} (Hidden)"
            else:
                bg, fg = state_styles.get(state_name, (None, None))
                display_state = state_name

            values = [
                str(row.get("jobFolderName", "")),
                display_state,
                str(row.get("selectedMode", "UNKNOWN")),
                str(mode_detection.get("candidate", "UNKNOWN")),
                str(mode_detection.get("source", "UNKNOWN")),
                str(timers.get("remindAt") or "-"),
                str(row.get("updatedAt") or "-"),
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if bg is not None:
                    item.setBackground(bg)
                    item.setForeground(fg)
                self.jobs_table.setItem(row_index, col_index, item)
```

- [ ] **Step 2: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.gui"`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "$(cat <<'EOF'
Show a Hidden badge on the Jobs dashboard for deployed-but-hidden jobs

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Full regression and manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full automated test suite**

Run: `python -m pytest`
Expected: all tests pass except the 3 pre-existing `mapbox_earcut`-dependent
failures in `tests/test_dae_converter.py` (documented in `CLAUDE.md` as
environmental, unrelated to this work).

- [ ] **Step 2: Headless import smoke check on the full app**

Run: `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.main; import ready_jobs_watcher.gui; import ready_jobs_watcher.scheduler; import ready_jobs_watcher.deployment_gate"`
Expected: no output, exit code 0.

- [ ] **Step 3: Manual walkthrough**

Run the app (`python -m ready_jobs_watcher`) against a scratch/test root
directory with a couple of job folders and verify:

- A newly detected job shows "No deploy scheduled" in its popup and has no
  `autoReleaseAt` in `.metadata/deployment_gate.json`.
- Clicking Snooze with "Remind in 1 min" closes the popup, and it reappears
  automatically ~1 minute later (reminder scheduler sweeps every 60s).
- Picking a time a few minutes out and clicking "Schedule Deploy" shows
  "Deploy scheduled for `<time>`" on next open, and the job actually deploys
  at that time without any other action (verify via the Jobs tab state
  changing from PENDING to PARSING/ACTIVE around the scheduled time).
- "Cancel Schedule" clears the schedule; the job does not deploy on its own
  afterward.
- On a deployed (PARSING/ACTIVE) job, "Hide from Production" changes the
  button to "Show in Production" on next open, and the Jobs tab shows
  `<STATE> (Hidden)` with the distinct grey styling.
- Re-parse and Save Mode still work on a hidden-but-deployed job.
- "Show in Production" restores the normal state badge/coloring.
- Restart the app once with a job still PENDING and a manually-set
  `timers.autoReleaseAt` from *before* this change (edit the JSON directly to
  simulate a leftover legacy timer) — confirm it's cleared on the next
  startup and the job does not silently deploy.

- [ ] **Step 4: Update CLAUDE.md if any new operator workflow needs documenting**

Re-read the "Job deployment gate (CRITICAL)" section in `CLAUDE.md` — if the
new Schedule Deploy / Hide-from-Production actions changed anything about
where per-job operator actions live (they didn't; both stay in the same
state-aware dialog), update that section. Otherwise no change needed.
