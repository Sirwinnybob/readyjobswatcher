import threading
import types

from ready_jobs_watcher.duplicate_job_guard import (
    clear_duplicate_suspect_marker,
    find_job_number_collision,
    read_duplicate_suspect_marker,
    write_duplicate_suspect_marker,
)
from ready_jobs_watcher.main import Application
from ready_jobs_watcher.deployment_gate import DeploymentGateManager


def _minimal_app(root):
    app = Application.__new__(Application)
    app.config = types.SimpleNamespace(ROOT_DIR=str(root))
    app.deployment_gate = DeploymentGateManager(str(root))
    app.settings_window = None
    app._pending_job_prompts = []
    app._pending_job_prompt_lock = threading.Lock()
    return app


def test_find_job_number_collision_detects_shared_number(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "502 - HARTFORD McCASLIN REFACE").mkdir(parents=True)
    (root / "649 - HARTFORD McCASLIN REFACE").mkdir(parents=True)

    collision = find_job_number_collision(str(root), "649 - HARTFORD McCASLIN REFACE", "649")

    assert collision is None  # only one folder has "649"


def test_find_job_number_collision_returns_other_folder_name(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "502 - HARTFORD McCASLIN REFACE").mkdir(parents=True)
    (root / "502 - HARTFORD MCCASLIN REFACE COPY").mkdir(parents=True)

    collision = find_job_number_collision(str(root), "502 - HARTFORD MCCASLIN REFACE COPY", "502")

    assert collision == "502 - HARTFORD McCASLIN REFACE"


def test_write_and_read_duplicate_suspect_marker(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    job.mkdir(parents=True)

    write_duplicate_suspect_marker(str(root), job.name, "502 - HARTFORD McCASLIN REFACE")
    marker = read_duplicate_suspect_marker(str(root), job.name)

    assert marker["suspectedDuplicateOf"] == "502 - HARTFORD McCASLIN REFACE"
    assert marker["reason"] == "job_number_collision"
    assert "detectedAt" in marker


def test_read_duplicate_suspect_marker_returns_none_when_absent(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "649 - HARTFORD McCASLIN REFACE").mkdir(parents=True)

    assert read_duplicate_suspect_marker(str(root), "649 - HARTFORD McCASLIN REFACE") is None


def test_clear_duplicate_suspect_marker_removes_file(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    job.mkdir(parents=True)
    write_duplicate_suspect_marker(str(root), job.name, "502 - HARTFORD McCASLIN REFACE")

    clear_duplicate_suspect_marker(str(root), job.name)

    assert read_duplicate_suspect_marker(str(root), job.name) is None
    # Clearing an already-absent marker must not raise.
    clear_duplicate_suspect_marker(str(root), job.name)


def test_new_job_folder_with_colliding_job_number_is_quarantined(tmp_path):
    root = tmp_path / "Ready Jobs"
    existing = root / "502 - HARTFORD McCASLIN REFACE"
    duplicate = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    existing.mkdir(parents=True)
    duplicate.mkdir(parents=True)
    app = _minimal_app(root)

    app.on_new_job_folder_detected(str(duplicate))

    assert not (duplicate / ".metadata" / "deployment_gate.json").exists()
    marker = read_duplicate_suspect_marker(str(root), duplicate.name)
    assert marker is not None
    assert marker["suspectedDuplicateOf"] == existing.name
    assert app._pending_job_prompts == []


def test_new_job_folder_without_collision_is_adopted_normally(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "649 - HARTFORD McCASLIN REFACE"
    job.mkdir(parents=True)
    app = _minimal_app(root)

    app.on_new_job_folder_detected(str(job))

    assert (job / ".metadata" / "deployment_gate.json").exists()
    assert read_duplicate_suspect_marker(str(root), job.name) is None
    assert app._pending_job_prompts == [job.name]


def test_resurrected_old_name_is_quarantined_even_after_number_changed(tmp_path, monkeypatch):
    from ready_jobs_watcher import rename_history

    root = tmp_path / "Ready Jobs"
    root.mkdir(parents=True)
    monkeypatch.setattr(rename_history, "RENAME_HISTORY_FILE", tmp_path / "rename_history.json")

    # The real job was already renamed 502 -> 649 at some point in the past; only 649
    # exists on disk now, matching the actual production incident this guard exists for.
    rename_history.record_rename("502 - HARTFORD McCASLIN REFACE", "649 - HARTFORD McCASLIN REFACE")
    live_job = root / "649 - HARTFORD McCASLIN REFACE"
    live_job.mkdir(parents=True)

    # A Syncthing peer resurrects a stale copy under the OLD name. No other folder on disk
    # currently carries job number "502" - find_job_number_collision alone would miss this.
    ghost = root / "502 - HARTFORD McCASLIN REFACE"
    ghost.mkdir(parents=True)

    app = _minimal_app(root)
    app.on_new_job_folder_detected(str(ghost))

    assert not (ghost / ".metadata" / "deployment_gate.json").exists()
    marker = read_duplicate_suspect_marker(str(root), ghost.name)
    assert marker is not None
    assert marker["suspectedDuplicateOf"] == "649 - HARTFORD McCASLIN REFACE"
    assert marker["reason"] == "rename_history_match"
    assert app._pending_job_prompts == []


def test_get_jobs_dashboard_rows_tags_duplicate_suspects(tmp_path):
    root = tmp_path / "Ready Jobs"
    existing = root / "502 - HARTFORD McCASLIN REFACE"
    duplicate = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    app = _minimal_app(root)

    # existing must be adopted (gate created) before duplicate appears on disk,
    # matching real watcher chronology. find_job_number_collision scans whatever
    # is on disk at call time with no notion of order, so if both folders already
    # existed when `existing` were processed, it would itself be flagged as a
    # collision of `duplicate` (symmetric false positive) -- not the behavior
    # under test here.
    existing.mkdir(parents=True)
    app.on_new_job_folder_detected(str(existing))

    duplicate.mkdir(parents=True)
    app.on_new_job_folder_detected(str(duplicate))

    rows = {row["jobFolderName"]: row for row in app.get_jobs_dashboard_rows()}
    assert "duplicateSuspect" not in rows[existing.name]
    assert rows[duplicate.name]["duplicateSuspect"]["suspectedDuplicateOf"] == existing.name
