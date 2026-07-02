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
