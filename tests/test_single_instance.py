import sys

import pytest

from ready_jobs_watcher.single_instance import SingleInstanceGuard

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="SingleInstanceGuard relies on the real Win32 CreateMutexW API.",
)


def test_second_guard_cannot_acquire_same_named_mutex():
    first = SingleInstanceGuard(name="Local\\KKC_test_singleton")
    second = SingleInstanceGuard(name="Local\\KKC_test_singleton")
    assert first.acquire() is True
    assert second.acquire() is False
    second.release()
    first.release()


def test_release_does_not_delete_another_process_diagnostic_pid(tmp_path):
    diagnostic = tmp_path / "ready_jobs_watcher.lock"
    guard = SingleInstanceGuard(
        name="Local\\KKC_test_release_owner",
        diagnostic_path=diagnostic,
    )
    assert guard.acquire() is True
    diagnostic.write_text("999999", encoding="ascii")
    guard.release()
    assert diagnostic.read_text(encoding="ascii") == "999999"


def test_acquire_writes_own_pid_to_diagnostic_path(tmp_path):
    import os

    diagnostic = tmp_path / "ready_jobs_watcher.lock"
    guard = SingleInstanceGuard(
        name="Local\\KKC_test_diagnostic_write",
        diagnostic_path=diagnostic,
    )
    assert guard.acquire() is True
    assert diagnostic.read_text(encoding="ascii") == str(os.getpid())
    guard.release()


def test_release_removes_own_diagnostic_pid_when_unmodified(tmp_path):
    diagnostic = tmp_path / "ready_jobs_watcher.lock"
    guard = SingleInstanceGuard(
        name="Local\\KKC_test_release_cleanup",
        diagnostic_path=diagnostic,
    )
    assert guard.acquire() is True
    guard.release()
    assert not diagnostic.exists()


def test_different_named_mutexes_can_both_be_acquired():
    first = SingleInstanceGuard(name="Local\\KKC_test_name_a")
    second = SingleInstanceGuard(name="Local\\KKC_test_name_b")
    assert first.acquire() is True
    assert second.acquire() is True
    first.release()
    second.release()
