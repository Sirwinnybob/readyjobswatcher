import json

from ready_jobs_watcher.metadata_cache import consolidate_cnc_tracker, consolidate_hardwoods_tracker
from ready_jobs_watcher.tracker_action_stream import load_cnc_tracker_actions
from ready_jobs_watcher.tracker_bad_parts import TrackerBadPartsMonitor


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_consolidates_only_cnc_and_hardwoods_trackers(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    _write_json(
        job / "CNC" / ".tracker" / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"},
                {"file": "123 - Maple.pdf", "page": 1, "action": "bad_part", "part": 5, "timestamp": "2026-06-09T10:01:00Z"},
            ],
        },
    )
    _write_json(
        job / ".metadata" / "hardwoods" / ".tracker" / "tablet-b.json",
        {
            "tabletId": "tablet-b",
            "actions": [
                {
                    "docType": "FACE_FRAME_CUT_LIST",
                    "rowId": "row-1",
                    "action": "set_done_count",
                    "value": 3,
                    "timestamp": "2026-06-09T10:02:00Z",
                }
            ],
        },
    )
    specialty = job / ".metadata" / "admin" / ".tracker" / "tablet-c.json"
    _write_json(specialty, {"deviceId": "tablet-c", "completions": {"item": True}})

    consolidate_cnc_tracker(job)
    consolidate_hardwoods_tracker(job)

    cnc_actions = json.loads((job / "CNC" / ".tracker" / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    hardwood_actions = json.loads(
        (job / ".metadata" / "hardwoods" / ".tracker" / "consolidated.json").read_text(encoding="utf-8")
    )["actions"]

    assert {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"} in cnc_actions
    assert any(action["action"] == "bad_part" and action["part"] == 5 for action in cnc_actions)
    assert hardwood_actions == [
        {
            "docType": "FACE_FRAME_CUT_LIST",
            "rowId": "row-1",
            "action": "set_done_count",
            "value": 3,
            "timestamp": "2026-06-09T10:02:00Z",
        }
    ]
    assert specialty.exists()
    assert not (specialty.parent / "consolidated.json").exists()


def test_incremental_cnc_consolidation_preserves_history(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)
    
    # Write existing consolidated.json with a complete action
    _write_json(
        tracker_dir / "consolidated.json",
        {
            "tabletId": "consolidated",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"},
            ],
        },
    )
    
    # Write new tablet file with a bad_part action
    _write_json(
        tracker_dir / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "bad_part", "part": 5, "timestamp": "2026-06-09T10:01:00Z"},
            ],
        },
    )
    
    consolidate_cnc_tracker(job)
    
    cnc_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    
    # Both old and new actions must exist
    assert {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"} in cnc_actions
    assert any(action["action"] == "bad_part" and action["part"] == 5 for action in cnc_actions)
    assert not (tracker_dir / "tablet-a.json").exists()


def test_cnc_consolidation_ignores_views_without_wiping_history(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)
    
    # Write existing consolidated.json
    _write_json(
        tracker_dir / "consolidated.json",
        {
            "tabletId": "consolidated",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"},
            ],
        },
    )
    
    # Write new tablet file containing only view actions
    _write_json(
        tracker_dir / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "view", "timestamp": "2026-06-09T10:01:00Z"},
            ],
        },
    )
    
    consolidate_cnc_tracker(job)
    
    cnc_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    
    # History preserved, view action ignored
    assert {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"} in cnc_actions
    assert not any(action.get("action") == "view" for action in cnc_actions)
    assert not (tracker_dir / "tablet-a.json").exists()


def test_incremental_hardwoods_consolidation_preserves_history(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / ".metadata" / "hardwoods" / ".tracker"
    tracker_dir.mkdir(parents=True)
    
    # Write existing consolidated.json
    _write_json(
        tracker_dir / "consolidated.json",
        {
            "tabletId": "consolidated",
            "actions": [
                {
                    "docType": "FACE_FRAME_CUT_LIST",
                    "rowId": "row-1",
                    "action": "set_done_count",
                    "value": 3,
                    "timestamp": "2026-06-09T10:02:00Z",
                }
            ],
        },
    )
    
    # Write new tablet file
    _write_json(
        tracker_dir / "tablet-b.json",
        {
            "tabletId": "tablet-b",
            "actions": [
                {
                    "docType": "FACE_FRAME_CUT_LIST",
                    "rowId": "row-2",
                    "action": "set_skipped",
                    "timestamp": "2026-06-09T10:03:00Z",
                }
            ],
        },
    )
    
    consolidate_hardwoods_tracker(job)
    
    hardwood_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    
    # Both old and new actions must exist
    assert any(action["rowId"] == "row-1" and action["action"] == "set_done_count" and action["value"] == 3 for action in hardwood_actions)
    assert any(action["rowId"] == "row-2" and action["action"] == "set_skipped" for action in hardwood_actions)
    assert not (tracker_dir / "tablet-b.json").exists()


def _make_bad_parts_monitor(tmp_path):
    from ready_jobs_watcher.config import Config

    cfg = Config()
    cfg.ROOT_DIR = str(tmp_path / "Ready Jobs")
    cfg.CNC_SUBDIR = "CNC"
    state_file = str(tmp_path / "tracker_bad_parts_state.json")
    return TrackerBadPartsMonitor(cfg, state_file=state_file)


def test_consolidation_preserves_bad_part_submitted_marker(tmp_path):
    # Regression test for METADATA_AUDIT.md C-01: a tablet flags a bad part and the
    # engineer submits it for the shop-floor alert. Consolidation must not silently
    # drop the `bad_part_submitted` marker before the source device file is deleted,
    # or the bad-parts alert monitor will never fire.
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {
                    "file": "123 - Maple.pdf",
                    "page": 1,
                    "action": "bad_part",
                    "part": 5,
                    "timestamp": "2026-06-09T10:01:00Z",
                    "fileFingerprint": "fp-a",
                },
                {
                    "file": "123 - Maple.pdf",
                    "page": 1,
                    "action": "bad_part_submitted",
                    "part": 5,
                    "timestamp": "2026-06-09T10:02:00Z",
                    "fileFingerprint": "fp-a",
                },
            ],
        },
    )

    consolidate_cnc_tracker(job)

    # The device file must be gone (this is what caused the data loss pre-fix).
    assert not (tracker_dir / "tablet-a.json").exists()

    cnc_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]

    bad_part_action = next(a for a in cnc_actions if a["action"] == "bad_part" and a["part"] == 5)
    submitted_action = next(a for a in cnc_actions if a["action"] == "bad_part_submitted" and a["part"] == 5)

    # M-06: each action must keep its own original timestamp, not a shared/fallback one.
    assert bad_part_action["timestamp"] == "2026-06-09T10:01:00Z"
    assert submitted_action["timestamp"] == "2026-06-09T10:02:00Z"

    # The raw action-stream reader (used by the bad-parts monitor) must also see both
    # actions once only consolidated.json remains on disk.
    raw_actions = load_cnc_tracker_actions(str(tracker_dir))
    assert any(a["action"] == "bad_part_submitted" and a.get("part") == 5 for a in raw_actions)

    # And the monitor itself must now consider the part an active, alertable event.
    monitor = _make_bad_parts_monitor(tmp_path)
    events = monitor.scan_once()
    assert len(events) == 1
    assert events[0].key.part_number == 5


def test_consolidation_resets_submission_on_reactivation(tmp_path):
    # M-06/C-01 companion case: once a bad part is unflagged, a later re-flag must not
    # inherit the earlier submission — the engineer has to re-submit before the alert
    # monitor treats it as active again.
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "bad_part", "part": 5, "timestamp": "2026-06-09T10:01:00Z"},
                {"file": "123 - Maple.pdf", "page": 1, "action": "bad_part_submitted", "part": 5, "timestamp": "2026-06-09T10:02:00Z"},
                {"file": "123 - Maple.pdf", "page": 1, "action": "unbad_part", "part": 5, "timestamp": "2026-06-09T10:03:00Z"},
                {"file": "123 - Maple.pdf", "page": 1, "action": "bad_part", "part": 5, "timestamp": "2026-06-09T10:04:00Z"},
            ],
        },
    )

    consolidate_cnc_tracker(job)

    cnc_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    assert any(a["action"] == "bad_part" and a["part"] == 5 for a in cnc_actions)
    assert not any(a["action"] == "bad_part_submitted" for a in cnc_actions)


def test_consolidation_returns_early_without_device_files(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)
    
    # Write consolidated.json
    _write_json(
        tracker_dir / "consolidated.json",
        {
            "tabletId": "consolidated",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"},
            ],
        },
    )
    
    consolidate_cnc_tracker(job)
    
    cnc_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    assert len(cnc_actions) == 1
