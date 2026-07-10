import json
import threading
import time

from ready_jobs_watcher.metadata_cache import (
    consolidate_cnc_tracker,
    consolidate_hardwoods_tracker,
    _acquire_tracker_lock,
    _release_tracker_lock,
)
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


def test_tracker_lock_mutual_exclusion(tmp_path):
    # H-06 regression: the primitive itself must refuse a second acquire while the first
    # is still held, and allow it again once released.
    tracker_dir = tmp_path / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)

    assert _acquire_tracker_lock(tracker_dir) is True
    assert _acquire_tracker_lock(tracker_dir) is False

    _release_tracker_lock(tracker_dir)
    assert _acquire_tracker_lock(tracker_dir) is True
    _release_tracker_lock(tracker_dir)


def test_tracker_lock_only_one_thread_wins_concurrently(tmp_path):
    # Two real threads race to create the same lock file at (as close to) the same instant;
    # exactly one must win. This exercises the O_CREAT|O_EXCL atomicity itself, not just the
    # logic around it.
    tracker_dir = tmp_path / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)

    results = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        results.append(_acquire_tracker_lock(tracker_dir))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]


def test_concurrent_cnc_consolidation_second_attempt_skips_no_lost_update(tmp_path):
    # H-06 regression (METADATA_AUDIT.md): simulate a second consolidation attempt (a second
    # watcher host, or a future bulk-sweep utility) racing an in-progress consolidation for the
    # same job's CNC tracker. The second attempt must not interleave its own read-merge-write
    # with the first's in-progress pass -- it should skip cleanly (no exception, no partial
    # write), leaving the device file untouched so nothing is lost; the next, uncontested pass
    # then converges to the correct merged state.
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"},
                {"file": "123 - Maple.pdf", "page": 1, "action": "bad_part", "part": 5, "timestamp": "2026-06-09T10:01:00Z"},
            ],
        },
    )

    # Simulate process #1 already holding the lock for this tracker dir mid-pass.
    assert _acquire_tracker_lock(tracker_dir) is True
    try:
        consolidate_cnc_tracker(job)  # the "second" concurrent attempt

        # Nothing should have been touched by the skipped attempt.
        assert (tracker_dir / "tablet-a.json").exists()
        assert not (tracker_dir / "consolidated.json").exists()
    finally:
        _release_tracker_lock(tracker_dir)

    # Next (uncontested) pass converges to the correct merged state -- no lost update.
    consolidate_cnc_tracker(job)
    assert not (tracker_dir / "tablet-a.json").exists()
    cnc_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    assert {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"} in cnc_actions
    assert any(a["action"] == "bad_part" and a["part"] == 5 for a in cnc_actions)


def test_concurrent_cnc_consolidation_via_real_threads_converges(tmp_path, monkeypatch):
    # End-to-end version of the above using two real threads racing consolidate_cnc_tracker
    # itself (rather than manually holding the lock). We slow down the merge step so the
    # second thread's lock attempt reliably lands while the first still holds it, then assert
    # both calls complete without raising and the final on-disk state has no lost update.
    import ready_jobs_watcher.metadata_cache as metadata_cache

    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"},
            ],
        },
    )

    original_merge = metadata_cache._merge_cnc_actions

    def slow_merge(actions):
        time.sleep(0.2)
        return original_merge(actions)

    monkeypatch.setattr(metadata_cache, "_merge_cnc_actions", slow_merge)

    results = {}

    def run(name):
        try:
            metadata_cache.consolidate_cnc_tracker(job)
            results[name] = "ok"
        except Exception as exc:  # pragma: no cover - failure path under test
            results[name] = exc

    t1 = threading.Thread(target=run, args=("t1",))
    t2 = threading.Thread(target=run, args=("t2",))
    t1.start()
    time.sleep(0.05)  # give t1 a head start so it wins the lock first
    t2.start()
    t1.join()
    t2.join()

    # Neither call should raise: the loser skips cleanly rather than erroring out.
    assert results["t1"] == "ok"
    assert results["t2"] == "ok"

    # No lost update: exactly the winner's merge landed, the device file is gone, and the
    # action it carried is present in consolidated.json.
    assert not (tracker_dir / "tablet-a.json").exists()
    cnc_actions = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    assert {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"} in cnc_actions


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


def test_hardwoods_consolidation_preserves_all_action_types(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / ".metadata" / "hardwoods" / ".tracker"
    tracker_dir.mkdir(parents=True)

    _write_json(
        tracker_dir / "tablet-b.json",
        {
            "tabletId": "tablet-b",
            "actions": [
                # Row 1: set_done_count (val=2), then set_bad_count (val=1)
                {"docType": "FACE_FRAME_CUT_LIST", "rowId": "row-1", "action": "set_done_count", "value": 2, "timestamp": "2026-06-09T10:00:00Z"},
                {"docType": "FACE_FRAME_CUT_LIST", "rowId": "row-1", "action": "set_bad_count", "value": 1, "timestamp": "2026-06-09T10:01:00Z"},

                # Row 2: set_skipped (remains skipped)
                {"docType": "FACE_FRAME_CUT_LIST", "rowId": "row-2", "action": "set_skipped", "timestamp": "2026-06-09T10:02:00Z"},

                # Row 3: set_skipped, then clear_skipped (should result in no set_skipped emitted)
                {"docType": "FACE_FRAME_CUT_LIST", "rowId": "row-3", "action": "set_skipped", "timestamp": "2026-06-09T10:03:00Z"},
                {"docType": "FACE_FRAME_CUT_LIST", "rowId": "row-3", "action": "clear_skipped", "timestamp": "2026-06-09T10:04:00Z"},

                # Totals 1: add_totals_rip10_done_count (val=2), then set_totals_rip10_done_count (val=5), then add (val=3) => total 8
                {"docType": "BOARD_STOCK", "rowId": "", "totalsKey": "tally-1", "action": "add_totals_rip10_done_count", "value": 2, "timestamp": "2026-06-09T10:05:00Z"},
                {"docType": "BOARD_STOCK", "rowId": "", "totalsKey": "tally-1", "action": "set_totals_rip10_done_count", "value": 5, "timestamp": "2026-06-09T10:06:00Z"},
                {"docType": "BOARD_STOCK", "rowId": "", "totalsKey": "tally-1", "action": "add_totals_rip10_done_count", "value": 3, "timestamp": "2026-06-09T10:07:00Z"},
            ],
        },
    )

    consolidate_hardwoods_tracker(job)

    # Device file must be deleted after consolidation
    assert not (tracker_dir / "tablet-b.json").exists()

    hardwood_actions = json.loads(
        (tracker_dir / "consolidated.json").read_text(encoding="utf-8")
    )["actions"]

    # Verify set_done_count on Row 1
    done_action = next(a for a in hardwood_actions if a["rowId"] == "row-1" and a["action"] == "set_done_count")
    assert done_action["value"] == 2
    assert done_action["timestamp"] == "2026-06-09T10:00:00Z"

    # Verify set_bad_count on Row 1
    bad_action = next(a for a in hardwood_actions if a["rowId"] == "row-1" and a["action"] == "set_bad_count")
    assert bad_action["value"] == 1
    assert bad_action["timestamp"] == "2026-06-09T10:01:00Z"

    # Verify set_skipped on Row 2
    skipped_action = next(a for a in hardwood_actions if a["rowId"] == "row-2" and a["action"] == "set_skipped")
    assert skipped_action["timestamp"] == "2026-06-09T10:02:00Z"

    # Verify Row 3 has no set_skipped
    assert not any(a["rowId"] == "row-3" and a["action"] == "set_skipped" for a in hardwood_actions)

    # Verify rip10 totals action
    totals_action = next(a for a in hardwood_actions if a.get("totalsKey") == "tally-1")
    assert totals_action["action"] == "set_totals_rip10_done_count"
    assert totals_action["value"] == 8
    assert totals_action["timestamp"] == "2026-06-09T10:07:00Z"


def test_hardwoods_consolidation_uses_row_id_as_totals_key_fallback(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / ".metadata" / "hardwoods" / ".tracker"
    tracker_dir.mkdir(parents=True)

    _write_json(
        tracker_dir / "tablet-b.json",
        {
            "tabletId": "tablet-b",
            "actions": [
                # Totals 2: totalsKey is missing, rowId is "tally-2"
                {"docType": "BOARD_STOCK", "rowId": "tally-2", "action": "set_totals_rip10_done_count", "value": 4, "timestamp": "2026-06-09T10:00:00Z"},
            ],
        },
    )

    consolidate_hardwoods_tracker(job)

    hardwood_actions = json.loads(
        (tracker_dir / "consolidated.json").read_text(encoding="utf-8")
    )["actions"]

    totals_action = next(a for a in hardwood_actions if a.get("totalsKey") == "tally-2")
    assert totals_action["action"] == "set_totals_rip10_done_count"
    assert totals_action["value"] == 4
    assert totals_action["timestamp"] == "2026-06-09T10:00:00Z"


def test_consolidate_cnc_tracker_merges_ndjson_events(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    ndjson.parent.mkdir(parents=True)
    ndjson.write_text(
        json.dumps(
            {
                "eventId": "e1",
                "op": "set_complete_true",
                "payload": {"file": "123 - Maple.pdf", "page": 1, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:00Z"},
                "lamport": 1,
                "wallTime": "2026-07-09T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    consolidate_cnc_tracker(job)

    consolidated = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))
    assert {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-07-09T09:00:00Z", "fileFingerprint": "fp1"} in consolidated["actions"]
    # ndjson source file must survive the daytime pass (no compaction in this task)
    assert ndjson.exists()


def test_consolidate_cnc_tracker_detects_uppercase_json_extension(tmp_path):
    # The detect/delete scan must use the SAME filter as the loader (_load_legacy_json_actions,
    # case-insensitive .json). An uppercase-extension file is read+merged by the loader; if the
    # scan were case-sensitive it would be invisible and, as the only device file, trigger the
    # early-return so its actions never reach consolidated.json.
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    _write_json(
        tracker_dir / "tabletUP.JSON",
        {
            "tabletId": "tabletUP",
            "actions": [
                {"file": "123 - Maple.pdf", "page": 1, "action": "complete", "timestamp": "2026-06-09T10:00:00Z"},
            ],
        },
    )

    consolidate_cnc_tracker(job)

    consolidated = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))
    assert any(
        a.get("file") == "123 - Maple.pdf" and a.get("page") == 1 and a.get("action") == "complete"
        for a in consolidated["actions"]
    )


def test_compact_true_truncates_unchanged_ndjson_after_merge(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    ndjson.parent.mkdir(parents=True)
    ndjson.write_text(
        json.dumps(
            {
                "eventId": "e1",
                "op": "set_complete_true",
                "payload": {"file": "123 - Maple.pdf", "page": 1, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:00Z"},
                "lamport": 1,
                "wallTime": "2026-07-09T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    consolidate_cnc_tracker(job, compact=True)

    consolidated = json.loads((tracker_dir / "consolidated.json").read_text(encoding="utf-8"))
    assert any(a["action"] == "complete" for a in consolidated["actions"])
    assert not ndjson.exists()


def test_compact_false_never_touches_ndjson(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    ndjson.parent.mkdir(parents=True)
    ndjson.write_text(
        json.dumps(
            {
                "eventId": "e1",
                "op": "set_complete_true",
                "payload": {"file": "123 - Maple.pdf", "page": 1, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:00Z"},
                "lamport": 1,
                "wallTime": "2026-07-09T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    consolidate_cnc_tracker(job)  # compact defaults to False

    assert ndjson.exists()


def test_compact_true_skips_ndjson_that_changed_since_read(tmp_path, monkeypatch):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    tracker_dir = job / "CNC" / ".tracker"
    ndjson = tracker_dir / "events" / "tablet-a.ndjson"
    ndjson.parent.mkdir(parents=True)
    ndjson.write_text(
        json.dumps(
            {
                "eventId": "e1",
                "op": "set_complete_true",
                "payload": {"file": "123 - Maple.pdf", "page": 1, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:00Z"},
                "lamport": 1,
                "wallTime": "2026-07-09T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    import ready_jobs_watcher.metadata_cache as metadata_cache_module
    real_load = metadata_cache_module.load_cnc_tracker_actions

    def slow_load(tracker_dir_str, **kwargs):
        # Simulate a tablet appending a new line after this pass already stat'd the file
        # but before the merge+delete step -- the "unchanged since read" guard must catch it.
        ndjson.write_text(
            ndjson.read_text(encoding="utf-8")
            + json.dumps(
                {
                    "eventId": "e2",
                    "op": "set_complete_true",
                    "payload": {"file": "123 - Maple.pdf", "page": 2, "fileFingerprint": "fp1", "timestamp": "2026-07-09T09:00:01Z"},
                    "lamport": 2,
                    "wallTime": "2026-07-09T09:00:01Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return real_load(tracker_dir_str, **kwargs)

    monkeypatch.setattr(metadata_cache_module, "load_cnc_tracker_actions", slow_load)

    consolidate_cnc_tracker(job, compact=True)

    assert ndjson.exists()
    remaining = ndjson.read_text(encoding="utf-8")
    assert "page\": 2" in remaining or '"page": 2' in remaining
