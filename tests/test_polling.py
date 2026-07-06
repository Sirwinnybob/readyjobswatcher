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
    pdf.write_text("two", encoding="utf-8")
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
