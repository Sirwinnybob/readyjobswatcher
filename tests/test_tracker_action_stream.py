import json
from pathlib import Path

from ready_jobs_watcher.tracker_action_stream import load_cnc_tracker_actions


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_cnc_tracker_actions_supports_multiline_json_stream(tmp_path):
    tracker_dir = tmp_path / "job" / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    _write(
        ndjson,
        """{
  "eventId": "e1",
  "op": "set_complete_true",
  "payload": {
    "file": "A.pdf",
    "page": 1,
    "fileFingerprint": "fp1"
  },
  "lamport": 1,
  "wallTime": "2026-05-12T10:00:00Z"
}
{
  "eventId": "e2",
  "op": "set_bad_part_true",
  "payload": {
    "file": "A.pdf",
    "page": 1,
    "part": 3,
    "fileFingerprint": "fp1"
  },
  "lamport": 2,
  "wallTime": "2026-05-12T10:00:01Z"
}
""",
    )

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 2
    assert actions[0]["action"] == "complete"
    assert actions[0]["file"] == "A.pdf"
    assert actions[0]["page"] == 1
    assert actions[1]["action"] == "bad_part"
    assert actions[1]["part"] == 3


def test_load_cnc_tracker_actions_still_supports_single_line_ndjson(tmp_path):
    tracker_dir = tmp_path / "job" / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    rows = [
        {
            "eventId": "e1",
            "op": "set_complete_true",
            "payload": {"file": "A.pdf", "page": 1, "fileFingerprint": "fp1"},
            "lamport": 1,
            "wallTime": "2026-05-12T10:00:00Z",
        },
        {
            "eventId": "e2",
            "op": "set_complete_false",
            "payload": {"file": "A.pdf", "page": 1, "fileFingerprint": "fp1"},
            "lamport": 2,
            "wallTime": "2026-05-12T10:00:01Z",
        },
    ]
    _write(ndjson, "\n".join(json.dumps(row) for row in rows) + "\n")

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 2
    assert [a["action"] for a in actions] == ["complete", "uncomplete"]


def test_load_cnc_tracker_actions_handles_top_level_array(tmp_path):
    tracker_dir = tmp_path / "job" / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    payload = [
        {
            "eventId": "e1",
            "op": "set_complete_true",
            "payload": {"file": "A.pdf", "page": 2, "fileFingerprint": "fp2"},
            "lamport": 1,
            "wallTime": "2026-05-12T10:00:00Z",
        }
    ]
    _write(ndjson, json.dumps(payload))

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 1
    assert actions[0]["file"] == "A.pdf"
    assert actions[0]["page"] == 2


def test_load_cnc_tracker_actions_merges_migrated_and_legacy_sources(tmp_path):
    tracker_dir = tmp_path / "job" / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    legacy = tracker_dir / "tablet-b.json"
    _write(
        ndjson,
        json.dumps(
            {
                "eventId": "e1",
                "op": "set_complete_true",
                "payload": {"file": "A.pdf", "page": 1, "fileFingerprint": "fp1"},
                "lamport": 1,
                "wallTime": "2026-05-12T10:00:00Z",
            }
        )
        + "\n",
    )
    _write(
        legacy,
        json.dumps(
            {
                "actions": [
                    {
                        "file": "B.pdf",
                        "page": 2,
                        "action": "bad_part",
                        "part": 7,
                        "timestamp": "2026-05-12T10:00:01Z",
                    }
                ]
            }
        ),
    )

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 2
    assert [action["file"] for action in actions] == ["A.pdf", "B.pdf"]


def test_load_cnc_tracker_actions_maps_bad_part_submitted_and_view_and_renested(tmp_path):
    tracker_dir = tmp_path / "job" / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    rows = [
        {
            "eventId": "e1",
            "op": "view",
            "payload": {"file": "A.pdf", "page": 1, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:00Z"},
            "lamport": 1,
            "wallTime": "2026-07-09T09:00:00Z",
        },
        {
            "eventId": "e2",
            "op": "set_bad_part_true",
            "payload": {"file": "A.pdf", "page": 1, "part": 3, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:01Z"},
            "lamport": 2,
            "wallTime": "2026-07-09T09:00:01Z",
        },
        {
            "eventId": "e3",
            "op": "bad_part_submitted",
            "payload": {"file": "A.pdf", "page": 1, "part": 3, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:02Z"},
            "lamport": 3,
            "wallTime": "2026-07-09T09:00:02Z",
        },
        {
            "eventId": "e4",
            "op": "set_skipped_true",
            "payload": {"file": "A.pdf", "page": 5, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:03Z", "reNested": True},
            "lamport": 4,
            "wallTime": "2026-07-09T09:00:03Z",
        },
    ]
    _write(ndjson, "\n".join(json.dumps(row) for row in rows) + "\n")

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert [a["action"] for a in actions] == ["view", "bad_part", "bad_part_submitted", "skip"]
    assert actions[2]["part"] == 3
    assert actions[3]["reNested"] is True


def test_aud05_excludes_flat_sync_conflict_ndjson(tmp_path):
    tracker_dir = tmp_path / "job" / "CNC" / ".tracker"
    events = tracker_dir / "events"
    active = events / "tablet-a.ndjson"
    _write(active, json.dumps({
        "eventId": "e1", "op": "set_complete_true",
        "payload": {"file": "A.pdf", "page": 1, "fileFingerprint": "fp1"},
        "lamport": 1, "wallTime": "2026-05-12T10:00:00Z",
    }))
    # A Syncthing conflict copy that would otherwise resurrect a stale/divergent action.
    conflict = events / "tablet-a.sync-conflict-20260512-100000-ABCDEF.ndjson"
    _write(conflict, json.dumps({
        "eventId": "eX", "op": "set_bad_part_true",
        "payload": {"file": "A.pdf", "page": 1, "part": 9, "fileFingerprint": "fp1"},
        "lamport": 99, "wallTime": "2026-05-12T09:00:00Z",
    }))

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 1
    assert actions[0]["action"] == "complete"
    assert all(a.get("part") != 9 for a in actions)


def test_aud05_excludes_nested_sync_conflict_ndjson(tmp_path):
    tracker_dir = tmp_path / "job" / "CNC" / ".tracker"
    events = tracker_dir / "events"
    _write(events / "tablet-a.ndjson", json.dumps({
        "eventId": "e1", "op": "set_complete_true",
        "payload": {"file": "A.pdf", "page": 1, "fileFingerprint": "fp1"},
        "lamport": 1, "wallTime": "2026-05-12T10:00:00Z",
    }))
    # Conflict copy nested one directory deep, and inside a conflicted sub-directory.
    _write(events / "sub" / "tablet-b.sync-conflict-20260512-100000-ABCDEF.ndjson", json.dumps({
        "eventId": "eY", "op": "set_bad_part_true",
        "payload": {"file": "A.pdf", "page": 1, "part": 7, "fileFingerprint": "fp1"},
        "lamport": 50, "wallTime": "2026-05-12T09:00:00Z",
    }))
    _write(events / ".sync-conflict-dir" / "tablet-c.ndjson", json.dumps({
        "eventId": "eZ", "op": "set_bad_part_true",
        "payload": {"file": "A.pdf", "page": 1, "part": 8, "fileFingerprint": "fp1"},
        "lamport": 60, "wallTime": "2026-05-12T09:00:00Z",
    }))

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 1
    assert actions[0]["action"] == "complete"
    assert all(a.get("part") not in (7, 8) for a in actions)


def test_load_cnc_tracker_actions_recovers_archived_divergent_conflict(tmp_path):
    job_folder = tmp_path / "332 - TESTJOB"
    tracker_dir = job_folder / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {"actions": [{"file": "A.pdf", "page": 1, "action": "complete", "timestamp": "2026-07-08T19:53:41Z"}]},
    )

    archive_dir = job_folder / ".metadata" / "sync_conflicts" / "20260708-125930-2E2GGMF"
    _write_json(
        archive_dir / "tablet-a.json",
        {"tabletId": "tablet-a", "actions": [{"file": "A.pdf", "page": 2, "action": "view", "timestamp": "2026-07-08T19:53:45Z"}]},
    )
    _write_json(
        archive_dir / "manifest.json",
        {
            "action": "archived_divergent",
            "originalPath": str(tracker_dir / "tablet-a.json"),
            "archivePath": str(archive_dir / "tablet-a.json"),
            "resolvedAt": "2026-07-08T20:01:23Z",
            "sameContent": False,
        },
    )

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 2
    assert actions[0]["action"] == "complete"
    assert actions[1]["action"] == "view"


def test_load_cnc_tracker_actions_ignores_unrelated_conflict_in_shared_bucket(tmp_path):
    # The per-job sync_conflicts bucket holds every conflict type for that job, not just
    # tracker files -- a conflict for something unrelated (e.g. a delivery schedule request)
    # sitting in the same bucket must never be folded into tracker actions.
    job_folder = tmp_path / "332 - TESTJOB"
    tracker_dir = job_folder / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {"actions": [{"file": "A.pdf", "page": 1, "action": "complete", "timestamp": "2026-07-08T19:53:41Z"}]},
    )

    archive_dir = job_folder / ".metadata" / "sync_conflicts" / "20260707-090000-ABCDEF"
    _write_json(archive_dir / "delivery_schedule_request.tablet-a.json", {"requestedAt": "new"})
    _write_json(
        archive_dir / "manifest.json",
        {
            "action": "archived_divergent",
            "originalPath": str(job_folder / "delivery_schedule_request.tablet-a.json"),
            "archivePath": str(archive_dir / "delivery_schedule_request.tablet-a.json"),
            "resolvedAt": "2026-07-07T09:05:00Z",
            "sameContent": False,
        },
    )

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 1
    assert actions[0]["action"] == "complete"


def test_load_cnc_tracker_actions_ignores_archived_duplicate_manifest(tmp_path):
    # sameContent=True / action="archived_duplicate" means the original already has this
    # exact content -- nothing to recover, folding it in would just duplicate existing data.
    job_folder = tmp_path / "332 - TESTJOB"
    tracker_dir = job_folder / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {"actions": [{"file": "A.pdf", "page": 1, "action": "complete", "timestamp": "2026-07-08T19:53:41Z"}]},
    )

    archive_dir = job_folder / ".metadata" / "sync_conflicts" / "20260721-065159-2E2GGMF"
    _write_json(
        archive_dir / "tablet-a.json",
        {"tabletId": "tablet-a", "actions": [{"file": "A.pdf", "page": 9, "action": "view", "timestamp": "2026-07-21T06:51:59Z"}]},
    )
    _write_json(
        archive_dir / "manifest.json",
        {
            "action": "archived_duplicate",
            "originalPath": str(tracker_dir / "tablet-a.json"),
            "archivePath": str(archive_dir / "tablet-a.json"),
            "resolvedAt": "2026-07-21T06:52:00Z",
            "sameContent": True,
        },
    )

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 1
    assert actions[0]["action"] == "complete"


def test_load_cnc_tracker_actions_ignores_non_actions_shape_conflict(tmp_path):
    # Defense in depth: even a divergent, path-matching manifest must not be folded if the
    # archived file doesn't actually look like a tracker action log.
    job_folder = tmp_path / "332 - TESTJOB"
    tracker_dir = job_folder / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {"actions": [{"file": "A.pdf", "page": 1, "action": "complete", "timestamp": "2026-07-08T19:53:41Z"}]},
    )

    archive_dir = job_folder / ".metadata" / "sync_conflicts" / "20260710-000000-ABCDEF"
    _write_json(archive_dir / "tablet-a.json", {"somethingElse": True})
    _write_json(
        archive_dir / "manifest.json",
        {
            "action": "archived_divergent",
            "originalPath": str(tracker_dir / "tablet-a.json"),
            "archivePath": str(archive_dir / "tablet-a.json"),
            "resolvedAt": "2026-07-10T00:00:01Z",
            "sameContent": False,
        },
    )

    actions = load_cnc_tracker_actions(str(tracker_dir))

    assert len(actions) == 1
    assert actions[0]["action"] == "complete"
