import os
import shutil
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from ready_jobs_watcher.deployment_gate import DeploymentGateManager
from ready_jobs_watcher.scheduler import (
    pending_autorelease_scheduler,
    process_pending_autorelease_once,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class TestPendingAutoReleaseScheduler(unittest.TestCase):
    def test_due_job_is_released(self):
        with tempfile.TemporaryDirectory() as root:
            job = "2000 - DUE"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job, detected_mode="FRAMELESS", detection_source="TEST")
            gate.update_state(
                job,
                timers={
                    "autoReleaseAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1)),
                },
            )

            releases = []

            def _release(job_name: str, selected_mode: str) -> bool:
                releases.append((job_name, selected_mode))
                return True

            count = process_pending_autorelease_once(gate, _release, root)

            self.assertEqual(count, 1)
            self.assertEqual(releases, [(job, "FRAMELESS")])

    def test_not_due_job_is_not_released(self):
        with tempfile.TemporaryDirectory() as root:
            job = "2001 - FUTURE"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job, detected_mode="BOTH", detection_source="TEST")
            gate.update_state(
                job,
                timers={
                    "autoReleaseAt": _iso(datetime.now(timezone.utc) + timedelta(hours=5)),
                },
            )

            releases = []

            def _release(job_name: str, selected_mode: str) -> bool:
                releases.append((job_name, selected_mode))
                return True

            count = process_pending_autorelease_once(gate, _release, root)
            self.assertEqual(count, 0)
            self.assertEqual(releases, [])

    def test_missing_job_folder_is_skipped_without_crashing(self):
        with tempfile.TemporaryDirectory() as root:
            job = "2002 - MISSING"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job, detected_mode="UNKNOWN", detection_source="TEST")
            gate.update_state(
                job,
                timers={
                    "autoReleaseAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1)),
                },
            )
            # Remove the folder before sweep.
            shutil.rmtree(os.path.join(root, job))

            releases = []

            def _release(job_name: str, selected_mode: str) -> bool:
                releases.append((job_name, selected_mode))
                return True

            count = process_pending_autorelease_once(gate, _release, root)
            self.assertEqual(count, 0)
            self.assertEqual(releases, [])

    def test_selected_mode_falls_back_to_detected_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            job = "2003 - FALLBACK"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job, detected_mode="BOTH", detection_source="TEST")
            gate.update_state(
                job,
                selectedMode="UNKNOWN",
                timers={
                    "autoReleaseAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1)),
                },
            )

            releases = []

            def _release(job_name: str, selected_mode: str) -> bool:
                releases.append((job_name, selected_mode))
                return True

            count = process_pending_autorelease_once(gate, _release, root)
            self.assertEqual(count, 1)
            self.assertEqual(releases, [(job, "BOTH")])

    def test_background_scheduler_runs_sweep(self):
        with tempfile.TemporaryDirectory() as root:
            job = "2004 - LOOP"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job, detected_mode="FRAMELESS", detection_source="TEST")
            gate.update_state(
                job,
                timers={
                    "autoReleaseAt": _iso(datetime.now(timezone.utc) - timedelta(minutes=1)),
                },
            )

            releases = []

            def _release(job_name: str, selected_mode: str) -> bool:
                releases.append((job_name, selected_mode))
                return True

            stop_event = mock.Mock()
            wait_calls = {"count": 0}

            def _is_set() -> bool:
                return wait_calls["count"] > 0

            def _wait(_seconds: float) -> bool:
                wait_calls["count"] += 1
                return True

            stop_event.is_set.side_effect = _is_set
            stop_event.wait.side_effect = _wait

            pending_autorelease_scheduler(gate, _release, stop_event, sweep_interval_seconds=1)

            self.assertEqual(releases, [(job, "FRAMELESS")])


if __name__ == "__main__":
    unittest.main()
