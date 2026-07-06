import os
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from ready_jobs_watcher.deployment_gate import DeploymentGateManager
from ready_jobs_watcher.main import Application


class _FakeObserver:
    def __init__(self, fail_on_start: bool = False):
        self.fail_on_start = fail_on_start
        self._alive = False
        self.scheduled = []

    def schedule(self, handler, path, recursive=True):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        if self.fail_on_start:
            raise OSError("network drive offline")
        self._alive = True

    def stop(self):
        self._alive = False

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return self._alive


def _build_minimal_app() -> Application:
    app = Application.__new__(Application)
    app.config = types.SimpleNamespace(
        ROOT_DIR=r"Y:\Ready Jobs",
        filesystem_monitor_mode="hybrid",
        ready_jobs_file_poll_seconds=60,
        ready_jobs_root_poll_seconds=10,
        ready_jobs_stable_poll_count=2,
    )
    app.job_processor = object()
    app.pending_queue = object()
    app.executor = object()
    app.deployment_gate = object()
    app.tracker_monitor = object()
    app.alert_coordinator = object()
    app._observer_lock = threading.RLock()
    app._pending_operations_restored = False
    app._root_unavailable_logged = False
    app.stop_event = threading.Event()
    app.observer = _FakeObserver()
    app.pdf_observer = _FakeObserver()
    app.poller = None
    app.poller_thread = None
    app._polling_started = False
    app.restart_calls = []
    app.restart = lambda: app.restart_calls.append("restart")  # type: ignore[method-assign]
    app.restore_calls = []
    app.restore_pending_operations = lambda rename_handler, pdf_handler: app.restore_calls.append(
        (rename_handler, pdf_handler)
    )
    return app


class TestMainObserverResilience(unittest.TestCase):
    def test_start_observers_returns_false_when_root_unavailable(self):
        app = _build_minimal_app()
        app._is_root_available = lambda: False  # type: ignore[method-assign]

        ok = app.start_observers()

        self.assertFalse(ok)
        self.assertTrue(app._root_unavailable_logged)
        self.assertEqual(app.restore_calls, [])

    def test_start_observers_starts_once_and_restores_pending_once(self):
        app = _build_minimal_app()
        app._is_root_available = lambda: True  # type: ignore[method-assign]

        def _observer_factory():
            return _FakeObserver()

        with patch("ready_jobs_watcher.main.Observer", side_effect=_observer_factory), patch(
            "ready_jobs_watcher.main.RenameHandler", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "ready_jobs_watcher.main.PdfChangeHandler", side_effect=lambda *args, **kwargs: object()
        ):
            first_ok = app.start_observers()
            second_ok = app.start_observers()

        self.assertTrue(first_ok)
        self.assertTrue(second_ok)
        self.assertEqual(len(app.restore_calls), 1)
        self.assertFalse(app._root_unavailable_logged)

    def test_start_observers_hybrid_starts_watchdog_and_poller(self):
        app = _build_minimal_app()
        app._is_root_available = lambda: True  # type: ignore[method-assign]
        poller_starts = []
        app._start_polling_if_needed = lambda rename_handler, pdf_handler: poller_starts.append(  # type: ignore[method-assign]
            (rename_handler, pdf_handler)
        )

        with patch("ready_jobs_watcher.main.Observer", side_effect=lambda: _FakeObserver()), patch(
            "ready_jobs_watcher.main.RenameHandler", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "ready_jobs_watcher.main.PdfChangeHandler", side_effect=lambda *args, **kwargs: object()
        ):
            ok = app.start_observers()

        self.assertTrue(ok)
        self.assertEqual(len(app.observer.scheduled), 1)
        self.assertEqual(len(app.pdf_observer.scheduled), 1)
        self.assertEqual(len(poller_starts), 1)

    def test_start_observers_polling_mode_skips_watchdog_observers(self):
        app = _build_minimal_app()
        app.config.filesystem_monitor_mode = "polling"
        app._is_root_available = lambda: True  # type: ignore[method-assign]
        poller_starts = []
        app._start_polling_if_needed = lambda rename_handler, pdf_handler: poller_starts.append(  # type: ignore[method-assign]
            (rename_handler, pdf_handler)
        )

        with patch("ready_jobs_watcher.main.Observer", side_effect=lambda: _FakeObserver()), patch(
            "ready_jobs_watcher.main.RenameHandler", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "ready_jobs_watcher.main.PdfChangeHandler", side_effect=lambda *args, **kwargs: object()
        ):
            ok = app.start_observers()

        self.assertTrue(ok)
        self.assertEqual(app.observer.scheduled, [])
        self.assertEqual(app.pdf_observer.scheduled, [])
        self.assertEqual(len(poller_starts), 1)
        self.assertEqual(len(app.restore_calls), 1)

    def test_start_observers_handles_observer_start_failure(self):
        app = _build_minimal_app()
        app.config.filesystem_monitor_mode = "watchdog"
        app._is_root_available = lambda: True  # type: ignore[method-assign]
        created = []

        def _observer_factory():
            fail = len(created) == 0
            obs = _FakeObserver(fail_on_start=fail)
            created.append(obs)
            return obs

        with patch("ready_jobs_watcher.main.Observer", side_effect=_observer_factory), patch(
            "ready_jobs_watcher.main.RenameHandler", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "ready_jobs_watcher.main.PdfChangeHandler", side_effect=lambda *args, **kwargs: object()
        ):
            ok = app.start_observers()

        self.assertFalse(ok)
        self.assertEqual(app.restore_calls, [])

    def test_root_catchup_runs_all_startup_scans_after_reconnect(self):
        app = _build_minimal_app()
        calls = []
        app.initial_scan = lambda: calls.append("initial")  # type: ignore[method-assign]
        app._run_startup_glb_check = lambda: calls.append("glb")  # type: ignore[method-assign]
        app._run_cabinet_index_startup_check = lambda: calls.append("index")  # type: ignore[method-assign]

        app._run_root_catchup_scans("after reconnect")

        self.assertEqual(calls, ["initial", "glb", "index"])

    def test_startup_glb_check_defers_when_root_unavailable(self):
        app = _build_minimal_app()
        app._is_root_available = lambda: False  # type: ignore[method-assign]

        with patch("ready_jobs_watcher.main.scan_root_for_missing_glbs") as scan:
            ok = app._run_startup_glb_check()

        self.assertFalse(ok)
        scan.assert_not_called()

    def test_initial_scan_defers_when_root_unavailable(self):
        app = _build_minimal_app()
        app.PAUSE_PROCESSING = False
        app._is_root_available = lambda: False  # type: ignore[method-assign]

        with patch("ready_jobs_watcher.main.os.scandir") as scandir:
            ok = app.initial_scan()

        self.assertFalse(ok)
        scandir.assert_not_called()

    def test_cabinet_index_check_defers_when_root_unavailable(self):
        app = _build_minimal_app()
        app._is_root_available = lambda: False  # type: ignore[method-assign]

        ok = app._run_cabinet_index_startup_check()

        self.assertFalse(ok)

    def test_bootstrap_new_job_folders_alerts_only_for_real_new_job_folders(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "100 - NEW JOB"), exist_ok=True)
            os.makedirs(os.path.join(root, "Face Frame"), exist_ok=True)
            os.makedirs(os.path.join(root, "200 - ALREADY KNOWN"), exist_ok=True)

            app = _build_minimal_app()
            app.config = types.SimpleNamespace(ROOT_DIR=root)
            app.deployment_gate = DeploymentGateManager(root)
            app.deployment_gate.ensure_pending_for_new_job("200 - ALREADY KNOWN")
            detected = []
            app.on_new_job_folder_detected = lambda path: detected.append(os.path.basename(path))  # type: ignore[method-assign]

            app._bootstrap_new_job_folders()

            self.assertEqual(detected, ["100 - NEW JOB"])

    def test_root_offline_restart_disabled_when_threshold_zero(self):
        app = _build_minimal_app()
        app.config.root_offline_restart_minutes = 0
        app._root_offline_since = 100.0

        app._maybe_restart_after_root_offline(1000.0)

        self.assertEqual(app.restart_calls, [])

    def test_root_offline_restart_waits_until_threshold(self):
        app = _build_minimal_app()
        app.config.root_offline_restart_minutes = 15
        app._root_offline_since = 100.0

        app._maybe_restart_after_root_offline(999.0)

        self.assertEqual(app.restart_calls, [])

    def test_root_offline_restart_triggers_after_threshold(self):
        app = _build_minimal_app()
        app.config.root_offline_restart_minutes = 15
        app._root_offline_since = 100.0

        with patch("ready_jobs_watcher.main.send_critical_alert") as alert:
            app._maybe_restart_after_root_offline(1000.0)

        alert.assert_called_once()
        self.assertEqual(app.restart_calls, ["restart"])

    def test_restart_spawn_failure_alerts_and_exits_instead_of_restoring_false_alive(self):
        app = Application.__new__(Application)
        app.stop_event = threading.Event()
        app.icon = None
        app.settings_window = None
        app.config = types.SimpleNamespace()
        calls = []
        app.release_lock = lambda: calls.append("release_lock")  # type: ignore[method-assign]
        app.acquire_lock = lambda: calls.append("acquire_lock")  # type: ignore[method-assign]

        with patch("ready_jobs_watcher.main.time.sleep"), patch(
            "subprocess.Popen", side_effect=OSError("cannot spawn")
        ), patch("ready_jobs_watcher.main.send_critical_alert") as alert, patch(
            "ready_jobs_watcher.main.os._exit", side_effect=SystemExit
        ) as exit_process:
            with self.assertRaises(SystemExit):
                app.restart()

        alert.assert_called_once()
        exit_process.assert_called_once_with(1)
        self.assertTrue(app.stop_event.is_set())
        self.assertEqual(calls, ["release_lock"])


if __name__ == "__main__":
    unittest.main()
