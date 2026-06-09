import threading
import types
import unittest
from unittest.mock import patch

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
    app.config = types.SimpleNamespace(ROOT_DIR=r"Y:\Ready Jobs")
    app.job_processor = object()
    app.pending_queue = object()
    app.executor = object()
    app.deployment_gate = object()
    app.tracker_monitor = object()
    app.alert_coordinator = object()
    app._observer_lock = threading.RLock()
    app._pending_operations_restored = False
    app._root_unavailable_logged = False
    app.observer = _FakeObserver()
    app.pdf_observer = _FakeObserver()
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

    def test_start_observers_handles_observer_start_failure(self):
        app = _build_minimal_app()
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


if __name__ == "__main__":
    unittest.main()
