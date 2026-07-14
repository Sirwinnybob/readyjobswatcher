import datetime

from ready_jobs_watcher.rename_history import (
    RENAME_HISTORY_RETENTION_DAYS,
    find_recent_rename_source,
    record_rename,
)


def test_record_and_find_recent_rename_source(tmp_path):
    history_file = tmp_path / "rename_history.json"

    record_rename(
        "502 - HARTFORD McCASLIN REFACE",
        "649 - HARTFORD McCASLIN REFACE",
        history_file=history_file,
    )

    entry = find_recent_rename_source("502 - HARTFORD McCASLIN REFACE", history_file=history_file)
    assert entry is not None
    assert entry["newName"] == "649 - HARTFORD McCASLIN REFACE"


def test_find_recent_rename_source_returns_none_when_no_match(tmp_path):
    history_file = tmp_path / "rename_history.json"

    record_rename("111 - SOMEONE", "222 - SOMEONE", history_file=history_file)

    assert find_recent_rename_source("999 - NOBODY", history_file=history_file) is None


def test_find_recent_rename_source_ignores_stale_entries(tmp_path):
    history_file = tmp_path / "rename_history.json"
    stale_at = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=RENAME_HISTORY_RETENTION_DAYS + 1)
    ).isoformat()
    history_file.write_text(
        '[{"oldName": "502 - OLD", "newName": "649 - OLD", "renamedAt": "%s"}]' % stale_at,
        encoding="utf-8",
    )

    assert find_recent_rename_source("502 - OLD", history_file=history_file) is None


def test_find_recent_rename_source_returns_most_recent_match(tmp_path):
    history_file = tmp_path / "rename_history.json"

    record_rename("502 - X", "600 - X", history_file=history_file)
    record_rename("502 - X", "649 - X", history_file=history_file)

    entry = find_recent_rename_source("502 - X", history_file=history_file)
    assert entry["newName"] == "649 - X"
