import os
import threading
import time
import json
from types import SimpleNamespace
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
from ready_jobs_watcher.watchers import RenameHandler, PdfChangeHandler
from ready_jobs_watcher import sync_conflict_resolver as sync_conflict_resolver_module


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


def _make_watcher_config(root):
    config = MagicMock()
    config.ROOT_DIR = str(root)
    config.new_folder_delay_seconds = 5
    config.pdf_conversion_delay_seconds = 5
    config.bad_parts_mode = "tracker"
    return config


def _make_handlers(config):
    job_processor = MagicMock()
    app_state = MagicMock()
    rename_handler = RenameHandler(config, job_processor, app_state)
    pdf_handler = PdfChangeHandler(config, rename_handler=rename_handler)
    return rename_handler, pdf_handler


def test_conflict_created_event_resolved_once_across_both_handlers(tmp_path):
    """
    RenameHandler and PdfChangeHandler run on two independent watchdog
    Observers over the same tree; both receive the same on_created event for
    a genuine sync-conflict file. Only one of them should invoke the
    resolver -- otherwise two threads race to move/archive the same file.
    """
    config = _make_watcher_config(tmp_path)
    rename_handler, pdf_handler = _make_handlers(config)

    conflict_path = str(
        tmp_path / "613 - Test Job" / "Door.sync-conflict-20260720-073405-ABC.pdf"
    )
    event = SimpleNamespace(src_path=conflict_path, is_directory=False)

    with patch("ready_jobs_watcher.watchers.resolve_sync_conflict_file") as mock_resolve:
        mock_resolve.return_value = None
        rename_handler.on_created(event)
        pdf_handler.on_created(event)

    assert mock_resolve.call_count == 1


def test_conflict_moved_event_only_resolved_by_rename_handler(tmp_path):
    """RenameHandler.on_moved is the only handler that owns move-triggered conflicts."""
    config = _make_watcher_config(tmp_path)
    rename_handler, pdf_handler = _make_handlers(config)

    dest_path = str(
        tmp_path / "613 - Test Job" / "Door.sync-conflict-20260720-073405-ABC.pdf"
    )
    event = SimpleNamespace(src_path=dest_path, dest_path=dest_path, is_directory=False)

    with patch("ready_jobs_watcher.watchers.resolve_sync_conflict_file") as mock_resolve:
        mock_resolve.return_value = None
        rename_handler.on_moved(event)
        # PdfChangeHandler has no on_moved override; nothing to dispatch there.

    assert mock_resolve.call_count == 1


def test_transient_conflict_event_ignored_by_both_handlers(tmp_path):
    """
    A `.syncthing...json.tmp`-shaped conflict marker must not be resolved by
    either handler: RenameHandler still calls into the resolver (which
    internally recognizes and skips transient paths with no side effects),
    while PdfChangeHandler no longer calls the resolver at all.
    """
    config = _make_watcher_config(tmp_path)
    rename_handler, pdf_handler = _make_handlers(config)

    transient = tmp_path / (
        ".syncthing.delivery_schedule_request.SM-X808U-6448."
        "sync-conflict-20260720-073405-2E2GGMF.json.tmp"
    )
    transient.write_bytes(b"")
    event = SimpleNamespace(src_path=str(transient), is_directory=False)

    rename_handler.on_created(event)
    pdf_handler.on_created(event)

    assert transient.exists()
    assert not list((tmp_path / ".metadata" / "sync_conflicts").rglob("manifest*.json"))


def test_resolver_in_flight_lock_skips_concurrent_duplicate_call(tmp_path):
    """
    Two threads (simulating RenameHandler's and PdfChangeHandler's observer
    threads) calling resolve_sync_conflict_file for the same conflict path
    at the same time must not both perform the resolution -- the second
    caller should be skipped (return None) while the first is in flight.
    """
    original = tmp_path / "job_board.json"
    conflict = tmp_path / "job_board.sync-conflict-20260622-070801-DRK5N56.json"
    original.write_text('{"jobs":[]}', encoding="utf-8")
    conflict.write_text('{"jobs":[]}', encoding="utf-8")

    entered_stability_check = threading.Event()
    release_first_call = threading.Event()
    real_is_stable_file = sync_conflict_resolver_module._is_stable_file

    def blocking_is_stable_file(path, *args, **kwargs):
        entered_stability_check.set()
        release_first_call.wait(timeout=5)
        return real_is_stable_file(path, *args, **kwargs)

    results = {}

    def _first_call():
        with patch.object(
            sync_conflict_resolver_module,
            "_is_stable_file",
            side_effect=blocking_is_stable_file,
        ):
            results["first"] = sync_conflict_resolver_module.resolve_sync_conflict_file(
                conflict, tmp_path
            )

    thread = threading.Thread(target=_first_call, name="FirstResolveCall")
    thread.start()
    try:
        assert entered_stability_check.wait(timeout=5)

        # A competing "second observer thread" call for the exact same path
        # while the first is still in flight must be skipped immediately.
        second_result = sync_conflict_resolver_module.resolve_sync_conflict_file(
            conflict, tmp_path
        )
        assert second_result is None
    finally:
        release_first_call.set()
        thread.join(timeout=5)

    assert results["first"] is not None
    assert not conflict.exists()
