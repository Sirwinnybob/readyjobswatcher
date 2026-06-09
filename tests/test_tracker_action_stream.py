import json
from pathlib import Path

from ready_jobs_watcher.tracker_action_stream import load_cnc_tracker_actions


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
