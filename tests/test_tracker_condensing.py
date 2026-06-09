import json

from ready_jobs_watcher.metadata_cache import consolidate_cnc_tracker, consolidate_hardwoods_tracker


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
