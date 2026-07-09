import os
import threading
import time
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from ready_jobs_watcher.config import Config
from ready_jobs_watcher.metadata_inventory import classify_metadata_path, OwnershipMode
from ready_jobs_watcher.metadata_cache import (
    build_pdf_catalog,
    consolidate_cnc_tracker,
    consolidate_hardwoods_tracker,
    generate_static_cache,
    _iter_staleness_files,
)
from ready_jobs_watcher.tracker_action_stream import _load_legacy_json_actions
from ready_jobs_watcher.remake_candidates_indexer import (
    _load_metadata_by_pdf,
    cleanup_orphaned_cnc_metadata_for_job,
)
from ready_jobs_watcher.scheduler import sync_conflict_resolver_scheduler
from ready_jobs_watcher.main import Application


def test_classify_metadata_path_sync_conflict(tmp_path):
    conflict_path = tmp_path / "Job" / "some_file.sync-conflict-20260622-070801-DRK5N56.pdf"
    classification = classify_metadata_path(conflict_path, tmp_path)
    assert classification.ownership == OwnershipMode.IGNORED_GENERATED


def test_build_pdf_catalog_sync_conflict_exclusion(tmp_path):
    job_folder = tmp_path / "613 - Test Job"
    job_folder.mkdir()
    (job_folder / "Assembly Sheets.pdf").write_bytes(b"pdf data")
    (job_folder / "Assembly Sheets.sync-conflict-20260622-070801-DRK5N56.pdf").write_bytes(b"conflict data")
    
    catalog = build_pdf_catalog(job_folder)
    assert len(catalog["managedDocs"]) == 1
    assert catalog["managedDocs"][0]["pdfFilename"] == "Assembly Sheets.pdf"


def test_consolidate_cnc_tracker_sync_conflict_exclusion(tmp_path):
    job_folder = tmp_path / "613 - Test Job"
    tracker_dir = job_folder / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)
    
    # Write a normal device action file
    device_file = tracker_dir / "tablet_1.json"
    device_file.write_text(json.dumps({
        "actions": [{"file": "1.pdf", "page": 0, "action": "complete", "timestamp": "2026-06-22T08:00:00Z"}]
    }))
    
    # Write a sync conflict action file
    conflict_file = tracker_dir / "tablet_1.sync-conflict-20260622-070801-DRK5N56.json"
    conflict_file.write_text(json.dumps({
        "actions": [{"file": "1.pdf", "page": 0, "action": "complete", "timestamp": "2026-06-22T08:05:00Z"}]
    }))
    
    consolidate_cnc_tracker(job_folder)
    
    # Normal device file should be consolidated and deleted
    assert not device_file.exists()
    # Conflict file should NOT be deleted because it was ignored
    assert conflict_file.exists()


def test_consolidate_hardwoods_tracker_sync_conflict_exclusion(tmp_path):
    job_folder = tmp_path / "613 - Test Job"
    tracker_dir = job_folder / ".metadata" / "hardwoods" / ".tracker"
    tracker_dir.mkdir(parents=True)
    
    # Write normal device file
    device_file = tracker_dir / "tablet_1.json"
    device_file.write_text(json.dumps({
        "actions": [{"docType": "FRAME", "rowId": "1", "action": "set_done_count", "value": 5, "timestamp": "2026-06-22T08:00:00Z"}]
    }))
    
    # Write sync conflict file
    conflict_file = tracker_dir / "tablet_1.sync-conflict-20260622-070801-DRK5N56.json"
    conflict_file.write_text(json.dumps({
        "actions": [{"docType": "FRAME", "rowId": "1", "action": "set_done_count", "value": 10, "timestamp": "2026-06-22T08:05:00Z"}]
    }))
    
    consolidate_hardwoods_tracker(job_folder)
    
    assert not device_file.exists()
    assert conflict_file.exists()


def test_generate_static_cache_sync_conflict_exclusion(tmp_path):
    job_folder = tmp_path / "613 - Test Job"
    cnc_dir = job_folder / "CNC"
    cnc_dir.mkdir(parents=True)
    
    # Create deployment gate
    (job_folder / ".metadata").mkdir()
    (job_folder / ".metadata" / "deployment_gate.json").write_text(json.dumps({"deployed": True}))
    
    pdf1 = cnc_dir / "613 - Material1.pdf"
    pdf1.write_bytes(b"pdf data")
    
    pdf_conflict = cnc_dir / "613 - Material1.sync-conflict-20260622-070801-DRK5N56.pdf"
    pdf_conflict.write_bytes(b"conflict data")
    
    # Also write metadata sidecar for pdf1
    (cnc_dir / ".metadata").mkdir()
    (cnc_dir / ".metadata" / "613 - Material1.json").write_text(json.dumps({"pdfFilename": "613 - Material1.pdf"}))
    
    # Run generate_static_cache
    static_cache = generate_static_cache(job_folder, lineup_position=1)
    
    materials = static_cache["cncJob"]["materials"]
    assert len(materials) == 1
    assert materials[0]["pdfFilename"] == "613 - Material1.pdf"


def test_iter_staleness_files_sync_conflict_exclusion(tmp_path):
    job_folder = tmp_path / "613 - Test Job"
    cnc_dir = job_folder / "CNC"
    cnc_dir.mkdir(parents=True)
    (cnc_dir / ".metadata").mkdir()
    
    (job_folder / "613 - Job.pdf").write_bytes(b"")
    (job_folder / "613 - Job.sync-conflict-20260622-070801-DRK5N56.pdf").write_bytes(b"")
    (cnc_dir / "613 - Sheet1.pdf").write_bytes(b"")
    (cnc_dir / "613 - Sheet1.sync-conflict-20260622-070801-DRK5N56.pdf").write_bytes(b"")
    (cnc_dir / ".metadata" / "613 - Sheet1.json").write_text("{}")
    (cnc_dir / ".metadata" / "613 - Sheet1.sync-conflict-20260622-070801-DRK5N56.json").write_text("{}")
    
    stale_files = list(_iter_staleness_files(job_folder))
    filenames = [p.name for p in stale_files]
    
    for name in filenames:
        assert ".sync-conflict-" not in name


def test_load_legacy_json_actions_sync_conflict_exclusion(tmp_path):
    tracker_dir = tmp_path / ".tracker"
    tracker_dir.mkdir()
    
    (tracker_dir / "tablet1.json").write_text(json.dumps({
        "actions": [{"file": "1.pdf", "page": 0, "action": "complete", "timestamp": "2026-06-22"}]
    }))
    (tracker_dir / "tablet1.sync-conflict-20260622-070801-DRK5N56.json").write_text(json.dumps({
        "actions": [{"file": "1.pdf", "page": 0, "action": "complete", "timestamp": "2026-06-22"}]
    }))
    
    actions = _load_legacy_json_actions([str(tracker_dir)])
    assert len(actions) == 1


def test_remake_candidates_metadata_sync_conflict_exclusion(tmp_path):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    
    (metadata_dir / "pdf1.json").write_text(json.dumps({"pdfFilename": "pdf1.pdf"}))
    (metadata_dir / "pdf1.sync-conflict-20260622-070801-DRK5N56.json").write_text(json.dumps({"pdfFilename": "pdf1.pdf"}))
    
    metadata = _load_metadata_by_pdf(str(metadata_dir))
    assert "pdf1.pdf" in metadata
    assert len(metadata) == 1
    
    
def test_cleanup_orphaned_cnc_metadata_sync_conflict_exclusion(tmp_path):
    job_folder = tmp_path / "613 - Test Job"
    cnc_dir = job_folder / "CNC"
    metadata_dir = cnc_dir / ".metadata"
    metadata_dir.mkdir(parents=True)
    
    (metadata_dir / "orphaned.json").write_text(json.dumps({"pdfFilename": "orphaned.pdf"}))
    (metadata_dir / "orphaned.sync-conflict-20260622-070801-DRK5N56.json").write_text(json.dumps({"pdfFilename": "orphaned.pdf"}))
    
    config = MagicMock()
    config.ROOT_DIR = str(tmp_path)
    config.CNC_SUBDIR = "CNC"
    config.cnc_orphan_metadata_grace_minutes = 0
    
    os.utime(metadata_dir / "orphaned.json", (time.time() - 3600, time.time() - 3600))
    os.utime(metadata_dir / "orphaned.sync-conflict-20260622-070801-DRK5N56.json", (time.time() - 3600, time.time() - 3600))
    
    removed = cleanup_orphaned_cnc_metadata_for_job(config, "613 - Test Job")
    
    assert removed == 1
    assert not (metadata_dir / "orphaned.json").exists()
    assert (metadata_dir / "orphaned.sync-conflict-20260622-070801-DRK5N56.json").exists()


def test_sync_conflict_resolver_scheduler():
    config = MagicMock()
    config.ROOT_DIR = "/mock/root"
    
    stop_event = MagicMock(spec=threading.Event)
    stop_event.is_set.side_effect = [False, True]
    stop_event.wait.return_value = True
    
    with patch("ready_jobs_watcher.scheduler.scan_and_resolve_sync_conflicts") as mock_scan:
        sync_conflict_resolver_scheduler(config, stop_event)
        mock_scan.assert_called_once_with("/mock/root")


def test_application_starts_sync_conflict_thread():
    with patch("ready_jobs_watcher.main.QApplication"), \
         patch("ready_jobs_watcher.main.SettingsWindow"), \
         patch("ready_jobs_watcher.main.create_tray_icon"), \
         patch("ready_jobs_watcher.main.DeploymentGateManager"), \
         patch("ready_jobs_watcher.main.MetadataRefreshService"), \
         patch("ready_jobs_watcher.main.TrackerBadPartsMonitor"), \
         patch("ready_jobs_watcher.main.AlertCoordinator"), \
         patch("ready_jobs_watcher.main.setup_logging"):
        
        app = Application()
        with patch("threading.Thread") as mock_thread_class:
            mock_thread_instance = MagicMock()
            mock_thread_class.return_value = mock_thread_instance
            
            app.start_threads()
            
            called_names = [call.kwargs.get("name") for call in mock_thread_class.call_args_list]
            assert "SyncConflictResolverScheduler" in called_names
            assert mock_thread_instance.start.call_count > 0


def test_should_ignore_file_sync_conflict():
    from ready_jobs_watcher.file_handler import should_ignore_file
    assert should_ignore_file("Plans.sync-conflict-20260622-070801-DRK5N56.pdf") is True
    assert should_ignore_file("Plans.pdf") is False


def test_cabinet_sheet_indexer_collect_pdfs_sync_conflict_exclusion(tmp_path):
    from ready_jobs_watcher.cabinet_sheet_indexer import _collect_pdf_candidates
    (tmp_path / "valid.pdf").write_bytes(b"pdf")
    (tmp_path / "conflict.sync-conflict-20260622-070801-DRK5N56.pdf").write_bytes(b"pdf")
    
    candidates = _collect_pdf_candidates(str(tmp_path))
    filenames = [os.path.basename(c) for c in candidates]
    assert "valid.pdf" in filenames
    assert "conflict.sync-conflict-20260622-070801-DRK5N56.pdf" not in filenames


def test_hardwoods_cutlist_indexer_collect_pdfs_sync_conflict_exclusion(tmp_path):
    from ready_jobs_watcher.hardwoods_cutlist_indexer import _collect_pdf_candidates
    (tmp_path / "valid.pdf").write_bytes(b"pdf")
    (tmp_path / "conflict.sync-conflict-20260622-070801-DRK5N56.pdf").write_bytes(b"pdf")
    
    candidates = _collect_pdf_candidates(str(tmp_path))
    filenames = [os.path.basename(c) for c in candidates]
    assert "valid.pdf" in filenames
    assert "conflict.sync-conflict-20260622-070801-DRK5N56.pdf" not in filenames
