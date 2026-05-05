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

