"""
Documentation test for the Ready Jobs Watcher launcher policy.

INSTALLATION.md is the operator-facing source of truth for how the watcher
is deployed on the Windows host. This test asserts the document actually
states the single-launcher policy that Task Scheduler enforces today
(``ReadyJobsWatcher`` with ``MultipleInstances = IgnoreNew``), that the
legacy ``Ready Jobs Watcher`` task stays disabled, and that the named
mutex from ``ready_jobs_watcher.single_instance`` is documented as the
in-process singleton authority — so the doc can't silently drift from the
code again.
"""
from pathlib import Path

from ready_jobs_watcher.single_instance import DEFAULT_MUTEX_NAME

INSTALLATION_MD = Path(__file__).resolve().parent.parent / "INSTALLATION.md"


def _read_installation_md() -> str:
    return INSTALLATION_MD.read_text(encoding="utf-8")


def test_installation_md_exists():
    assert INSTALLATION_MD.is_file()


def test_documents_the_supported_scheduled_task_name():
    text = _read_installation_md()
    assert "ReadyJobsWatcher" in text


def test_documents_the_ignore_new_multiple_instances_policy():
    text = _read_installation_md()
    assert "MultipleInstancesPolicy=IgnoreNew" in text


def test_documents_the_legacy_task_restriction():
    text = _read_installation_md()
    # The legacy Task Scheduler task keeps the space in its name and must be
    # documented as disabled/unsupported, not just absent from the doc.
    assert "Ready Jobs Watcher" in text
    assert "disabled" in text.lower()
    assert "legacy" in text.lower()


def test_documents_the_named_mutex_by_exact_value():
    text = _read_installation_md()
    # Must reference the real DEFAULT_MUTEX_NAME constant, not a paraphrase.
    assert DEFAULT_MUTEX_NAME in text
