"""
Verifies ready_jobs_watcher/__main__.py acquires the single-instance guard
before setup_logging()/Application() run any shared-state setup, and that a
duplicate launch exits cleanly without touching any of it.
"""
import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_duplicate_launch_exits_before_setup_logging_and_application(monkeypatch):
    fake_guard = MagicMock(name="fake_guard")
    fake_guard.acquire.return_value = False
    fake_guard_cls = MagicMock(name="SingleInstanceGuard_cls", return_value=fake_guard)

    setup_logging_mock = MagicMock(name="setup_logging")
    application_cls = MagicMock(name="Application_cls")

    with patch(
        "ready_jobs_watcher.single_instance.SingleInstanceGuard", fake_guard_cls
    ), patch("ready_jobs_watcher.main.setup_logging", setup_logging_mock), patch(
        "ready_jobs_watcher.main.Application", application_cls
    ):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("ready_jobs_watcher.__main__", run_name="__main__")

    assert exc.value.code == 0
    fake_guard.acquire.assert_called_once()
    setup_logging_mock.assert_not_called()
    application_cls.assert_not_called()


def test_successful_acquire_configures_logging_and_threads_the_guard_through():
    fake_guard = MagicMock(name="fake_guard")
    fake_guard.acquire.return_value = True
    fake_guard_cls = MagicMock(name="SingleInstanceGuard_cls", return_value=fake_guard)

    setup_logging_mock = MagicMock(name="setup_logging")

    app_instance = MagicMock(name="app_instance")
    application_cls = MagicMock(name="Application_cls", return_value=app_instance)

    with patch(
        "ready_jobs_watcher.single_instance.SingleInstanceGuard", fake_guard_cls
    ), patch("ready_jobs_watcher.main.setup_logging", setup_logging_mock), patch(
        "ready_jobs_watcher.main.Application", application_cls
    ):
        runpy.run_module("ready_jobs_watcher.__main__", run_name="__main__")

    fake_guard.acquire.assert_called_once()
    setup_logging_mock.assert_called_once()
    application_cls.assert_called_once_with(instance_guard=fake_guard)
    app_instance.start.assert_called_once()
    app_instance.stop.assert_called_once()
    app_instance.release_lock.assert_called_once()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
