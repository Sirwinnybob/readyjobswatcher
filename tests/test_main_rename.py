import os
import threading
import types
import json

from ready_jobs_watcher.main import Application


class _FakeDeploymentGate:
    def load_state(self, job_name, create_if_missing=False, default_deployed=True):
        return {"jobFolderName": job_name, "deployed": default_deployed}

    def save_state(self, job_name, state):
        return None


class _FakePendingQueue:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.calls = []

    def rename_job_folder(self, old_name, new_name, old_num, new_num):
        self.calls.append((old_name, new_name, old_num, new_num))
        return dict(self.mapping)


class _FakePdfHandler:
    def __init__(self):
        self.mappings = []

    def retarget_pending_pdf_conversions(self, path_map):
        self.mappings.append(dict(path_map))


def _minimal_app(root, pending_queue=None, pdf_handler=None):
    app = Application.__new__(Application)
    app.config = types.SimpleNamespace(ROOT_DIR=str(root), RETRY_INTERVAL_MINUTES=15)
    app.pending_queue = pending_queue
    app.pdf_event_handler = pdf_handler
    app.tracker_monitor = None
    app.deployment_gate = _FakeDeploymentGate()
    app.settings_window = None
    app.PENDING_RENAMES = {}
    app.pending_renames_lock = threading.Lock()
    app.schedule_metadata_refresh_for_job = lambda job, reason: None  # type: ignore[method-assign]
    return app


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_rename_job_retargets_pending_renames_and_pdf_conversions(tmp_path):
    root = tmp_path / "Ready Jobs"
    old_job = root / "123 - OLD"
    new_job = root / "999 - NEW"
    old_cnc = old_job / "CNC"
    old_cnc.mkdir(parents=True)
    new_cnc = new_job / "CNC"
    old_pdf = old_cnc / "123 - Assembly Sheets.pdf"
    new_pdf = new_cnc / "999 - Assembly Sheets.pdf"
    pending_queue = _FakePendingQueue({os.path.normpath(str(old_pdf)): os.path.normpath(str(new_pdf))})
    pdf_handler = _FakePdfHandler()
    app = _minimal_app(root, pending_queue=pending_queue, pdf_handler=pdf_handler)
    next_retry = 1234.0
    app.PENDING_RENAMES[os.path.normpath(str(old_pdf))] = (
        "123",
        os.path.normpath(str(old_cnc)),
        "123 - Assembly Sheets.pdf",
        next_retry,
    )

    app.rename_job("123 - OLD", "999 - NEW")

    expected_pending_path = os.path.normpath(str(new_pdf))
    assert app.PENDING_RENAMES == {
        expected_pending_path: (
            "999",
            os.path.normpath(str(new_cnc)),
            "999 - Assembly Sheets.pdf",
            next_retry,
        )
    }
    assert pdf_handler.mappings == [{os.path.normpath(str(old_pdf)): os.path.normpath(str(new_pdf))}]


def test_rename_job_refreshes_metadata_when_folder_already_moved(tmp_path):
    root = tmp_path / "Ready Jobs"
    old_name = "123 - OLD"
    new_name = "123 - NEW"
    job = root / new_name
    (job / "CNC").mkdir(parents=True)
    _write_json(root / "production_order.json", [old_name])
    _write_json(job / ".metadata" / "deployment_gate.json", {"jobFolderName": old_name, "deployed": True})
    _write_json(job / ".metadata" / "cache_static.json", {"jobInfo": {"folderName": old_name}})

    app = _minimal_app(root)
    scheduled = []
    app.schedule_metadata_refresh_for_job = lambda job, reason: scheduled.append((job, reason))  # type: ignore[method-assign]

    app.rename_job(old_name, new_name)

    assert _read_json(root / "production_order.json") == [new_name]
    gate = _read_json(job / ".metadata" / "deployment_gate.json")
    assert gate["jobFolderName"] == new_name
    cache = _read_json(job / ".metadata" / "cache_static.json")
    assert cache["jobInfo"]["folderName"] == new_name
    assert scheduled == [(new_name, "job_renamed")]


def test_retry_pending_strips_wrong_existing_prefix_before_adding_new_prefix(tmp_path):
    root = tmp_path / "Ready Jobs"
    job_dir = root / "999 - NEW"
    job_dir.mkdir(parents=True)
    old_path = job_dir / "123 - Part.pdf"
    old_path.write_text("locked earlier", encoding="utf-8")
    app = _minimal_app(root)
    app.PAUSE_PROCESSING = False
    app.PENDING_RENAMES[str(old_path)] = ("999", str(job_dir), "123 - Part.pdf", 0)

    class _StopAfterOne:
        def __init__(self):
            self.done = False

        def is_set(self):
            return self.done

        def wait(self, _seconds):
            self.done = True
            return True

    app.stop_event = _StopAfterOne()

    app.retry_pending()

    assert not old_path.exists()
    assert (job_dir / "999 - Part.pdf").read_text(encoding="utf-8") == "locked earlier"
    assert not (job_dir / "999 - 123 - Part.pdf").exists()
    assert app.PENDING_RENAMES == {}
