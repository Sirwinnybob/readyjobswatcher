import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from ready_jobs_watcher.deployment_gate import (
    MODE_BOTH,
    MODE_UNKNOWN,
    DeploymentGateManager,
)


class TestDeploymentGateManager(unittest.TestCase):
    def test_new_job_starts_pending_and_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            job = "998 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            state = gate.ensure_pending_for_new_job(job, detected_mode="BOTH", detection_source="DELIVERY_SHEET")
            self.assertFalse(state["deployed"])
            self.assertFalse(state["parseReady"])
            self.assertEqual(state["modeDetection"]["candidate"], MODE_BOTH)
            self.assertEqual(state["modeDetection"]["source"], "DELIVERY_SHEET")
            self.assertTrue(state["hiddenFromProduction"])
            self.assertIsNotNone(state["timers"]["autoReleaseAt"])
            self.assertIsNotNone(state["timers"]["lastActionAt"])
            self.assertFalse(gate.should_process_job_folder(os.path.join(root, job)))

    def test_deploy_and_parse_ready_enable_processing(self):
        with tempfile.TemporaryDirectory() as root:
            job = "999 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            gate.ensure_pending_for_new_job(job)
            gate.mark_deployed(job, selected_mode="FACE-FRAME")
            gate.mark_parse_ready(job, parse_ready=True)
            state = gate.load_state(job)

            self.assertTrue(state["deployed"])
            self.assertTrue(state["parseReady"])
            self.assertEqual(state["selectedMode"], "FACE-FRAME")
            self.assertTrue(gate.should_process_job_folder(os.path.join(root, job)))

    def test_hidden_from_production_only_affects_non_debug_visibility(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1000 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            gate.mark_deployed(job, selected_mode="UNKNOWN")
            gate.mark_parse_ready(job, parse_ready=True)
            gate.set_hidden_from_production(job, True)

            self.assertFalse(gate.get_visibility(job, is_debug_build=False))
            self.assertTrue(gate.get_visibility(job, is_debug_build=True))

    def test_duplicate_pending_events_do_not_reset_auto_release_timer(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1001 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            first = gate.ensure_pending_for_new_job(job, detected_mode="BOTH", detection_source="FIRST")
            first_auto_release = first["timers"]["autoReleaseAt"]
            second = gate.ensure_pending_for_new_job(job, detected_mode="FRAMELESS", detection_source="SECOND")

            self.assertEqual(second["timers"]["autoReleaseAt"], first_auto_release)
            self.assertEqual(second["modeDetection"]["candidate"], "FRAMELESS")

    def test_operator_action_resets_auto_release_for_pending_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1002 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            state = gate.ensure_pending_for_new_job(job)
            before = datetime.fromisoformat(state["timers"]["autoReleaseAt"])
            updated = gate.mark_operator_action(job)
            after = datetime.fromisoformat(updated["timers"]["autoReleaseAt"])

            self.assertGreater(after, before)
            self.assertIsNotNone(updated["timers"]["lastActionAt"])

    def test_operator_action_helpers_extend_pending_auto_release(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1002B - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            state = gate.ensure_pending_for_new_job(job)
            initial = datetime.fromisoformat(state["timers"]["autoReleaseAt"])

            hidden_update = gate.set_hidden_from_production(job, False)
            hidden_after = datetime.fromisoformat(hidden_update["timers"]["autoReleaseAt"])
            self.assertGreaterEqual(hidden_after, initial)

            mode_update = gate.set_selected_mode(job, "FACE-FRAME")
            mode_after = datetime.fromisoformat(mode_update["timers"]["autoReleaseAt"])
            self.assertGreaterEqual(mode_after, hidden_after)

            retry_update = gate.schedule_retry(job, minutes=1)
            retry_after = datetime.fromisoformat(retry_update["timers"]["autoReleaseAt"])
            self.assertGreaterEqual(retry_after, mode_after)

            remind_update = gate.schedule_reminder(job, minutes=1)
            remind_after = datetime.fromisoformat(remind_update["timers"]["autoReleaseAt"])
            self.assertGreaterEqual(remind_after, retry_after)

    def test_mode_detection_can_skip_operator_action_touch(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1002C - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            state = gate.ensure_pending_for_new_job(job)
            before = state["timers"]["autoReleaseAt"]
            updated = gate.set_mode_detection(
                job,
                candidate="BOTH",
                source="AUTO",
                mark_as_operator_action=False,
            )

            self.assertEqual(updated["timers"]["autoReleaseAt"], before)
            self.assertEqual(updated["modeDetection"]["candidate"], MODE_BOTH)

    def test_clear_timers_clears_auto_release_and_action_clock(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1003 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            gate.ensure_pending_for_new_job(job)
            cleared = gate.clear_timers(job)

            self.assertIsNone(cleared["timers"]["retryAt"])
            self.assertIsNone(cleared["timers"]["remindAt"])
            self.assertIsNone(cleared["timers"]["autoReleaseAt"])
            self.assertIsNone(cleared["timers"]["lastActionAt"])

    def test_mode_normalization_falls_back_to_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1004 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            state = gate.ensure_pending_for_new_job(job, detected_mode="bad-mode", detection_source="manual")
            self.assertEqual(state["modeDetection"]["candidate"], MODE_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
