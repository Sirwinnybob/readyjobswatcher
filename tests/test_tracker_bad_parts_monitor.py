import json
from pathlib import Path

from ready_jobs_watcher.config import Config
from ready_jobs_watcher.tracker_bad_parts import TrackerBadPartKey, TrackerBadPartsMonitor


def _write_tracker(path: Path, actions):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tabletId": "tablet-a", "actions": actions}, indent=2), encoding="utf-8")


def _make_monitor(tmp_path: Path):
    cfg = Config()
    cfg.ROOT_DIR = str(tmp_path / "Ready Jobs")
    cfg.CNC_SUBDIR = "CNC"
    state_file = str(tmp_path / "tracker_bad_parts_state.json")
    return TrackerBadPartsMonitor(cfg, state_file=state_file)


def test_bad_part_transition_and_resolution(tmp_path):
    root = tmp_path / "Ready Jobs" / "123 - TEST" / "CNC" / ".tracker"
    tracker_file = root / "tablet-a.json"
    _write_tracker(
        tracker_file,
        [
            {
                "file": "123 - Maple.pdf",
                "page": 2,
                "part": 77,
                "action": "bad_part",
                "timestamp": "2026-05-04T10:00:00Z",
                "fileFingerprint": "fp-a",
            }
        ],
    )

    monitor = _make_monitor(tmp_path)
    first = monitor.scan_once()
    assert len(first) == 1
    assert len(monitor.state.active_keys) == 1

    second = monitor.scan_once()
    assert second == []

    _write_tracker(
        tracker_file,
        [
            {
                "file": "123 - Maple.pdf",
                "page": 2,
                "part": 77,
                "action": "bad_part",
                "timestamp": "2026-05-04T10:00:00Z",
                "fileFingerprint": "fp-a",
            },
            {
                "file": "123 - Maple.pdf",
                "page": 2,
                "part": 77,
                "action": "unbad_part",
                "timestamp": "2026-05-04T10:05:00Z",
                "fileFingerprint": "fp-a",
            },
        ],
    )

    third = monitor.scan_once()
    assert third == []
    assert len(monitor.state.active_keys) == 0


def test_out_of_order_timestamp_replay_and_dedup(tmp_path):
    root = tmp_path / "Ready Jobs" / "456 - TEST" / "CNC" / ".tracker"
    tracker_a = root / "tablet-a.json"
    tracker_b = root / "tablet-b.json"

    # Out-of-order list in a single file, and duplicate bad_part from another file.
    _write_tracker(
        tracker_a,
        [
            {
                "file": "456 - Birch.pdf",
                "page": 1,
                "part": 12,
                "action": "unbad_part",
                "timestamp": "2026-05-04T10:10:00Z",
                "fileFingerprint": "fp-b",
            },
            {
                "file": "456 - Birch.pdf",
                "page": 1,
                "part": 12,
                "action": "bad_part",
                "timestamp": "2026-05-04T10:00:00Z",
                "fileFingerprint": "fp-b",
            },
        ],
    )
    _write_tracker(
        tracker_b,
        [
            {
                "file": "456 - Birch.pdf",
                "page": 1,
                "part": 12,
                "action": "bad_part",
                "timestamp": "2026-05-04T10:01:00Z",
                "fileFingerprint": "fp-b",
            }
        ],
    )

    monitor = _make_monitor(tmp_path)
    events = monitor.scan_once()
    # Because replay is timestamp-sorted, final state for this key is inactive.
    assert events == []
    assert len(monitor.state.active_keys) == 0


def test_state_persistence_prevents_realert_on_restart(tmp_path):
    root = tmp_path / "Ready Jobs" / "789 - TEST" / "CNC" / ".tracker"
    tracker_file = root / "tablet-a.json"
    _write_tracker(
        tracker_file,
        [
            {
                "file": "789 - Oak.pdf",
                "page": 3,
                "part": 21,
                "action": "bad_part",
                "timestamp": "2026-05-04T11:00:00Z",
                "fileFingerprint": "fp-c",
            }
        ],
    )

    monitor_a = _make_monitor(tmp_path)
    assert len(monitor_a.scan_once()) == 1

    # New monitor with same state file should not re-alert unchanged active state.
    monitor_b = _make_monitor(tmp_path)
    assert monitor_b.scan_once() == []


def test_ack_and_reactivation_behavior(tmp_path):
    root = tmp_path / "Ready Jobs" / "900 - TEST" / "CNC" / ".tracker"
    tracker_file = root / "tablet-a.json"
    key = TrackerBadPartKey(
        job_folder_name="900 - TEST",
        pdf_filename="900 - Walnut.pdf",
        page=4,
        file_fingerprint="fp-d",
        part_number=5,
    )

    _write_tracker(
        tracker_file,
        [
            {
                "file": key.pdf_filename,
                "page": key.page,
                "part": key.part_number,
                "action": "bad_part",
                "timestamp": "2026-05-04T12:00:00Z",
                "fileFingerprint": key.file_fingerprint,
            }
        ],
    )
    monitor = _make_monitor(tmp_path)
    events = monitor.scan_once()
    assert len(events) == 1
    assert monitor.acknowledge_keys([key]) == 1

    # Resolve key, then reactivate later; should alert again after resolution clears ack for this key.
    _write_tracker(
        tracker_file,
        [
            {
                "file": key.pdf_filename,
                "page": key.page,
                "part": key.part_number,
                "action": "bad_part",
                "timestamp": "2026-05-04T12:00:00Z",
                "fileFingerprint": key.file_fingerprint,
            },
            {
                "file": key.pdf_filename,
                "page": key.page,
                "part": key.part_number,
                "action": "unbad_part",
                "timestamp": "2026-05-04T12:05:00Z",
                "fileFingerprint": key.file_fingerprint,
            },
            {
                "file": key.pdf_filename,
                "page": key.page,
                "part": key.part_number,
                "action": "bad_part",
                "timestamp": "2026-05-04T12:10:00Z",
                "fileFingerprint": key.file_fingerprint,
            },
        ],
    )
    events_after_reactivation = monitor.scan_once()
    assert len(events_after_reactivation) == 1


def test_snapshot_ack_and_unack_partition(tmp_path):
    root = tmp_path / "Ready Jobs" / "111 - TEST" / "CNC"
    tracker_file = root / ".tracker" / "tablet-a.json"
    _write_tracker(
        tracker_file,
        [
            {
                "file": "111 - Maple.pdf",
                "page": 1,
                "part": 3,
                "action": "bad_part",
                "timestamp": "2026-05-06T08:00:00Z",
                "fileFingerprint": "fp-1",
            },
            {
                "file": "111 - Maple.pdf",
                "page": 1,
                "part": 7,
                "action": "bad_part",
                "timestamp": "2026-05-06T08:01:00Z",
                "fileFingerprint": "fp-1",
            },
        ],
    )

    monitor = _make_monitor(tmp_path)
    new_events = monitor.scan_once()
    assert len(new_events) == 2

    first_key = new_events[0].key
    assert monitor.acknowledge_keys([first_key]) == 1

    snapshot = monitor.get_bad_parts_snapshot(include_resolved=False)
    assert len(snapshot["unacknowledged"]) == 1
    assert len(snapshot["acknowledged"]) == 1

    assert monitor.unacknowledge_keys([first_key]) == 1
    snapshot_after_unack = monitor.get_bad_parts_snapshot(include_resolved=False)
    assert len(snapshot_after_unack["unacknowledged"]) == 2
    assert len(snapshot_after_unack["acknowledged"]) == 0


def test_detail_record_metadata_resolution_with_thumbnail_and_ocr(tmp_path):
    root = tmp_path / "Ready Jobs" / "222 - TEST" / "CNC"
    tracker_file = root / ".tracker" / "tablet-a.json"
    _write_tracker(
        tracker_file,
        [
            {
                "file": "222 - Birch.pdf",
                "page": 4,
                "part": 11,
                "action": "bad_part",
                "timestamp": "2026-05-06T09:00:00Z",
                "fileFingerprint": "fp-2",
            }
        ],
    )

    metadata_dir = root / ".metadata"
    thumbs_dir = metadata_dir / ".thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    thumb_file = thumbs_dir / "222 - Birch_p004.png"
    thumb_file.write_bytes(b"fake-image")
    (root / "222 - Birch.pdf").write_bytes(b"%PDF-1.7\n")

    metadata_payload = {
        "material": "Birch Material",
        "pdfFilename": "222 - Birch.pdf",
        "pages": [
            {
                "pageNumber": 4,
                "thumbnailPath": ".metadata/.thumbs/222 - Birch_p004.png",
                "parts": [
                    {
                        "number": 11,
                        "name": "Shelf",
                        "width": 12.5,
                        "length": 30.25,
                        "cabNumber": 4,
                        "room": "KITCHEN",
                    }
                ],
                "ocrBoxes": {
                    "11": [{"left": 10, "top": 20, "right": 110, "bottom": 220}]
                },
            }
        ],
    }
    (metadata_dir / "222 - Birch.json").write_text(json.dumps(metadata_payload), encoding="utf-8")

    monitor = _make_monitor(tmp_path)
    events = monitor.scan_once()
    assert len(events) == 1

    detail = monitor.get_detail_record(events[0].key, detected_at=events[0].detected_at, is_acknowledged=False)
    assert detail.material == "Birch Material"
    assert detail.part_name == "Shelf"
    assert detail.width == 12.5
    assert detail.length == 30.25
    assert detail.cabinet_number == 4
    assert detail.room == "KITCHEN"
    assert detail.thumbnail_path is not None
    assert detail.highlight_rect == (10, 20, 110, 220)


def test_rename_job_folder_state(tmp_path):
    monitor = _make_monitor(tmp_path)

    key1 = TrackerBadPartKey(
        job_folder_name="123 - TEST",
        pdf_filename="123 - Maple.pdf",
        page=2,
        file_fingerprint="fp-a",
        part_number=77,
    )
    key2 = TrackerBadPartKey(
        job_folder_name="456 - OTHER",
        pdf_filename="456 - Oak.pdf",
        page=1,
        file_fingerprint="fp-b",
        part_number=12,
    )

    monitor.state.active_keys = {key1.to_token(), key2.to_token()}
    monitor.state.seen_keys = {key1.to_token(), key2.to_token()}
    monitor.state.acknowledged_keys = {key1.to_token()}
    monitor._save_state()

    # Rename "123 - TEST" to "999 - NEW"
    monitor.rename_job_folder("123 - TEST", "999 - NEW", "123", "999")

    # Reload and check
    monitor2 = _make_monitor(tmp_path)

    # key1 should be updated:
    # job_folder_name="999 - NEW"
    # pdf_filename="999 - Maple.pdf"
    new_key1 = TrackerBadPartKey(
        job_folder_name="999 - NEW",
        pdf_filename="999 - Maple.pdf",
        page=2,
        file_fingerprint="fp-a",
        part_number=77,
    )

    assert new_key1.to_token() in monitor2.state.active_keys
    assert key2.to_token() in monitor2.state.active_keys
    assert key1.to_token() not in monitor2.state.active_keys

    assert new_key1.to_token() in monitor2.state.seen_keys
    assert key2.to_token() in monitor2.state.seen_keys
    assert key1.to_token() not in monitor2.state.seen_keys

    assert new_key1.to_token() in monitor2.state.acknowledged_keys
    assert key1.to_token() not in monitor2.state.acknowledged_keys

