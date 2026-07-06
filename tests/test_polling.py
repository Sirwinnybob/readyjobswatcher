import json
from pathlib import Path
from types import SimpleNamespace

from ready_jobs_watcher.polling import ReadyJobsPoller


class _Recorder:
    def __init__(self):
        self.calls = []

    def handle_created_path(self, path, is_directory):
        self.calls.append(("created", Path(path).name, is_directory))

    def handle_modified_path(self, path, is_directory):
        self.calls.append(("modified", Path(path).name, is_directory))

    def handle_deleted_path(self, path, is_directory):
        self.calls.append(("deleted", Path(path).name, is_directory))

    def handle_moved_path(self, src_path, dest_path, is_directory):
        self.calls.append(("moved", Path(src_path).name, Path(dest_path).name, is_directory))


def _config(root, stable_count=1):
    return SimpleNamespace(
        ROOT_DIR=str(root),
        ready_jobs_stable_poll_count=stable_count,
    )


def test_first_poll_baselines_without_dispatching_existing_files(tmp_path):
    root = tmp_path / "Ready Jobs"
    root.mkdir()
    job = root / "123 - TEST"
    job.mkdir()
    (job / "123 - Assembly Sheets.pdf").write_text("one", encoding="utf-8")
    recorder = _Recorder()
    snapshot_path = tmp_path / "polling_snapshot.json"

    poller = ReadyJobsPoller(_config(root), snapshot_path, recorder, recorder)
    poller.poll_once(scan_root=True, scan_files=True)

    assert recorder.calls == []
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "123 - TEST/123 - Assembly Sheets.pdf" in data["entries"]


def test_poll_dispatches_created_modified_and_deleted_files_after_stable_count(tmp_path):
    root = tmp_path / "Ready Jobs"
    root.mkdir()
    job = root / "123 - TEST"
    job.mkdir()
    pdf = job / "123 - Assembly Sheets.pdf"
    recorder = _Recorder()
    poller = ReadyJobsPoller(_config(root, stable_count=2), tmp_path / "polling_snapshot.json", recorder, recorder)

    poller.poll_once(scan_root=True, scan_files=True)

    pdf.write_text("one", encoding="utf-8")
    poller.poll_once(scan_root=False, scan_files=True)
    assert recorder.calls == []
    poller.poll_once(scan_root=False, scan_files=True)
    assert ("created", pdf.name, False) in recorder.calls

    recorder.calls.clear()
    pdf.write_text("two changed", encoding="utf-8")
    poller.poll_once(scan_root=False, scan_files=True)
    assert recorder.calls == []
    poller.poll_once(scan_root=False, scan_files=True)
    assert ("modified", pdf.name, False) in recorder.calls

    recorder.calls.clear()
    pdf.unlink()
    poller.poll_once(scan_root=False, scan_files=True)
    assert recorder.calls == []
    poller.poll_once(scan_root=False, scan_files=True)
    assert ("deleted", pdf.name, False) in recorder.calls


def test_poll_treats_single_top_level_job_delete_create_as_likely_rename(tmp_path):
    root = tmp_path / "Ready Jobs"
    root.mkdir()
    old_job = root / "123 - OLD"
    old_job.mkdir()
    recorder = _Recorder()
    poller = ReadyJobsPoller(_config(root), tmp_path / "polling_snapshot.json", recorder, recorder)

    poller.poll_once(scan_root=True, scan_files=False)
    old_job.rename(root / "999 - NEW")
    poller.poll_once(scan_root=True, scan_files=False)

    assert recorder.calls == [("moved", "123 - OLD", "999 - NEW", True)]


def test_snapshot_for_different_root_is_discarded_without_delete_dispatch(tmp_path):
    old_root = tmp_path / "Old Ready Jobs"
    new_root = tmp_path / "New Ready Jobs"
    old_root.mkdir()
    new_root.mkdir()
    job = new_root / "123 - TEST"
    job.mkdir()
    pdf = job / "123 - Assembly Sheets.pdf"
    pdf.write_text("current", encoding="utf-8")
    snapshot_path = tmp_path / "polling_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "version": 1,
                "root_dir": str(old_root),
                "entries": {
                    "999 - OLD/999 - Assembly Sheets.pdf": {
                        "is_dir": False,
                        "size": 3,
                        "mtime_ns": 1,
                        "root_entry": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    recorder = _Recorder()

    poller = ReadyJobsPoller(_config(new_root), snapshot_path, recorder, recorder)
    poller.poll_once(scan_root=True, scan_files=True)

    assert recorder.calls == []
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert data["root_dir"] == str(new_root)
    assert "123 - TEST/123 - Assembly Sheets.pdf" in data["entries"]
    assert "999 - OLD/999 - Assembly Sheets.pdf" not in data["entries"]


def test_failed_file_scan_does_not_dispatch_deletes_or_mutate_snapshot(tmp_path):
    root = tmp_path / "Ready Jobs"
    root.mkdir()
    snapshot_path = tmp_path / "polling_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "version": 1,
                "root_dir": str(root),
                "entries": {
                    "123 - TEST/123 - Assembly Sheets.pdf": {
                        "is_dir": False,
                        "size": 7,
                        "mtime_ns": 10,
                        "root_entry": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    recorder = _Recorder()
    poller = ReadyJobsPoller(_config(root), snapshot_path, recorder, recorder)
    poller._scan_file_entries = lambda: None  # type: ignore[method-assign]

    poller.poll_once(scan_root=False, scan_files=True)

    assert recorder.calls == []
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "123 - TEST/123 - Assembly Sheets.pdf" in data["entries"]


def test_per_path_file_scan_failure_does_not_dispatch_delete_or_mutate_snapshot(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "123 - TEST"
    job.mkdir(parents=True)
    pdf = job / "123 - Assembly Sheets.pdf"
    pdf.write_text("current", encoding="utf-8")
    snapshot_path = tmp_path / "polling_snapshot.json"
    rel_path = "123 - TEST/123 - Assembly Sheets.pdf"
    snapshot_path.write_text(
        json.dumps(
            {
                "version": 1,
                "root_dir": str(root),
                "entries": {
                    rel_path: {
                        "is_dir": False,
                        "size": 7,
                        "mtime_ns": 10,
                        "root_entry": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    recorder = _Recorder()
    poller = ReadyJobsPoller(_config(root), snapshot_path, recorder, recorder)
    original_entry_for_path = poller._entry_for_path

    def fail_for_pdf(path, *, is_root_entry):
        if Path(path) == pdf:
            poller._scan_path_errors = True
            return None
        return original_entry_for_path(path, is_root_entry=is_root_entry)

    poller._entry_for_path = fail_for_pdf  # type: ignore[method-assign]

    poller.poll_once(scan_root=False, scan_files=True)

    assert recorder.calls == []
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert rel_path in data["entries"]
