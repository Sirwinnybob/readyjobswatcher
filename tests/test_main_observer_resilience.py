import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import pytest

from ready_jobs_watcher.deployment_gate import DeploymentGateManager
from ready_jobs_watcher.main import (
    Application,
    _restart_exec_args,
    _win32_quote_arg,
    _win32_quote_argv,
)


class FakeGuard:
    """Minimal stand-in for SingleInstanceGuard, matching the acquire()/release()
    interface Application.acquire_lock()/release_lock() actually call."""

    def __init__(self, acquired: bool = True):
        self._acquired = acquired
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        return self._acquired

    def release(self) -> None:
        self.release_calls += 1


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

        with patch(
            "ready_jobs_watcher.main.os.execv", side_effect=OSError("cannot exec")
        ) as execv, patch("ready_jobs_watcher.main.send_critical_alert") as alert, patch(
            "ready_jobs_watcher.main.os._exit", side_effect=SystemExit
        ) as exit_process:
            with self.assertRaises(SystemExit):
                app.restart()

        execv.assert_called_once_with(sys.executable, _restart_exec_args())
        alert.assert_called_once()
        exit_process.assert_called_once_with(1)
        self.assertTrue(app.stop_event.is_set())
        self.assertEqual(calls, ["release_lock"])


def test_duplicate_application_refuses_to_start_before_workers(monkeypatch):
    app = _build_minimal_app()
    app._single_instance_guard = FakeGuard(acquired=False)
    clear_logs = Mock()
    start_threads = Mock()
    monkeypatch.setattr("ready_jobs_watcher.main.clear_old_logs", clear_logs)
    monkeypatch.setattr(app, "start_threads", start_threads)
    with pytest.raises(SystemExit) as exc:
        app.start()
    assert exc.value.code == 0
    clear_logs.assert_not_called()
    start_threads.assert_not_called()


def test_restart_executes_in_place_after_stopping(monkeypatch):
    app = _build_minimal_app()
    # _build_minimal_app() stubs .restart() with a recording lambda for the
    # root-offline-restart tests above; here we exercise the real
    # Application.restart implementation instead.
    del app.restart
    app.release_lock = Mock()
    execv = Mock()
    monkeypatch.setattr("ready_jobs_watcher.main.os.execv", execv)
    app.restart()
    app.release_lock.assert_called_once()
    execv.assert_called_once_with(sys.executable, _restart_exec_args())


def test_win32_quote_arg_leaves_plain_args_alone():
    assert _win32_quote_arg("plain") == "plain"
    assert _win32_quote_arg("-m") == "-m"


def test_win32_quote_arg_quotes_paths_containing_spaces():
    path = r"C:\Program Files\Python313\python.exe"
    quoted = _win32_quote_arg(path)
    # subprocess.list2cmdline's MS C runtime quoting rules for one argument.
    assert quoted == subprocess.list2cmdline([path])
    assert quoted == f'"{path}"'


def test_win32_quote_argv_only_quotes_elements_that_need_it():
    args = ["plain", r"C:\Scripts\Ready Jobs Watcher\.venv\Scripts\python.exe", "-m"]
    quoted = _win32_quote_argv(args)
    assert quoted[0] == "plain"
    assert quoted[1] == f'"{args[1]}"'
    assert quoted[2] == "-m"


def test_restart_exec_args_frozen_build_omits_full_argv(monkeypatch):
    # PyInstaller sets sys.frozen; a frozen build's sys.argv is already just
    # [exe_path], so restart() must not also append it (that would duplicate
    # the executable path as a bogus CLI argument -- the "Important" fix).
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    args = _restart_exec_args()

    assert args == _win32_quote_argv([sys.executable])


def test_restart_exec_args_script_mode_includes_full_argv():
    assert not getattr(sys, "frozen", False)

    args = _restart_exec_args()

    assert args == _win32_quote_argv([sys.executable, *sys.argv])


def test_execv_with_quoted_argv_actually_relaunches_on_windows(tmp_path):
    """Real (non-mocked) proof that the quoting fix works on this machine.

    The reviewer's repro: os.execv's Windows emulation joins argv elements
    with a bare, unquoted space when building the replacement process's
    command line. Any element containing whitespace -- guaranteed here,
    since sys.executable lives under a path containing spaces in this repo's
    own dev environment -- gets split apart by the OS, the relaunch silently
    fails to start, and the failure is NOT a catchable Python exception in
    the caller (execv replaces/relaunches before Python can raise on the
    child's failure to even start).

    This spawns a real child process that calls the actual os.execv (not a
    Mock) using ready_jobs_watcher.main._win32_quote_argv's quoted argv list,
    and confirms the relaunched process actually started, ran, and wrote its
    marker file -- proving the fix works end to end, not just that execv was
    *called with* the right-looking arguments.
    """
    if os.name != "nt":
        pytest.skip("Windows-specific os.execv command-line quoting behavior")

    assert " " in sys.executable, (
        "expected the dev interpreter path to contain a space so this test "
        "exercises the real-world failure mode the reviewer reproduced; "
        "got %r" % (sys.executable,)
    )

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    probe_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_execv_relaunch_probe.py"
    )
    marker_path = tmp_path / "relaunch-marker.txt"

    result = subprocess.run(
        [sys.executable, probe_script, "launch", str(marker_path), repo_root],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"probe process failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert marker_path.exists(), (
        "relaunched process never ran -- os.execv's command line was likely "
        f"split on whitespace in sys.executable ({sys.executable!r}); "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert marker_path.read_text(encoding="utf-8") == "relaunched-ok"


if __name__ == "__main__":
    unittest.main()
