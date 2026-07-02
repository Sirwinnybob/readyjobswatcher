import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from ready_jobs_watcher.deployment_gate import (
    MODE_BOTH,
    MODE_UNKNOWN,
    DeploymentGateManager,
    derive_state,
    ensure_hidden_gates_for_all_folders,
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
            self.assertFalse(state["hiddenFromProduction"])
            self.assertIsNone(state["timers"]["autoReleaseAt"])
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

    def test_schedule_deploy_converts_naive_datetime_to_utc(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1008 - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)

            deploy_at = datetime(2026, 7, 6, 7, 0, 0)
            state = gate.schedule_deploy(job, deploy_at)

            self.assertEqual(
                state["timers"]["autoReleaseAt"],
                deploy_at.astimezone(timezone.utc).isoformat(),
            )

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

    def test_re_detecting_a_hidden_deployed_job_resets_hidden_from_production(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1008B - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job(job)
            gate.mark_deployed(job, selected_mode="BOTH")
            gate.mark_parse_ready(job, parse_ready=True)
            gate.hide_from_production(job)

            state = gate.ensure_pending_for_new_job(job)

            self.assertFalse(state["hiddenFromProduction"])

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

    def test_migrate_returns_zero_for_empty_root(self):
        with tempfile.TemporaryDirectory() as root:
            gate = DeploymentGateManager(root)

            cleared_count = gate.migrate_clear_legacy_autorelease_timers()

            self.assertEqual(cleared_count, 0)


class TestEnsureHiddenGatesForAllFolders(unittest.TestCase):
    def test_returns_names_of_newly_gated_folders_only(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "100 - NEW JOB"), exist_ok=True)
            os.makedirs(os.path.join(root, "200 - ALREADY GATED"), exist_ok=True)
            gate = DeploymentGateManager(root)
            gate.ensure_pending_for_new_job("200 - ALREADY GATED")

            created = ensure_hidden_gates_for_all_folders(root)

            self.assertEqual(created, ["100 - NEW JOB"])


class TestDeriveState(unittest.TestCase):
    def test_pending_when_not_deployed(self):
        self.assertEqual(derive_state({"deployed": False, "parseReady": False}), "PENDING")
        self.assertEqual(derive_state({"deployed": False, "parseReady": True}), "PENDING")

    def test_parsing_when_deployed_but_not_parse_ready(self):
        self.assertEqual(derive_state({"deployed": True, "parseReady": False}), "PARSING")

    def test_active_when_deployed_and_parse_ready(self):
        self.assertEqual(derive_state({"deployed": True, "parseReady": True}), "ACTIVE")

    def test_missing_keys_default_to_active(self):
        self.assertEqual(derive_state({}), "ACTIVE")


if __name__ == "__main__":
    unittest.main()
