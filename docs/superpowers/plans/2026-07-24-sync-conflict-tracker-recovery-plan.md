# Recover CNC/Hardwood Tracker Actions From Archived Sync Conflicts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop silently losing tablet CNC/hardwood tracker actions when Syncthing produces a genuinely divergent conflict on `<tabletId>.json`, by folding the archived loser's actions back into the same shared reader every tracker consumer already uses.

**Architecture:** `ready_jobs_watcher/tracker_action_stream.py`'s `_load_legacy_json_actions` (shared by `load_cnc_tracker_actions` and `load_hardwoods_tracker_actions`, in turn used by `tracker_bad_parts.py`, `metadata_cache.py` consolidation, and `remake_candidates_indexer.py`) gains a read-only step: for the tracker directory being read, find its enclosing job folder, look at `<job>/.metadata/sync_conflicts/*/manifest.json` for entries this loader would otherwise never see again, and fold in any that are (a) genuinely divergent (not already-identical duplicates), (b) actually belong to this exact tracker directory (not some other conflict type sharing the same per-job archive bucket), and (c) shaped like the known `{tabletId, actions: [...]}` tracker file. No new files are written — every pass simply re-reads and re-folds, which is cheap and safe (see spec).

**Tech Stack:** Python 3.13, pytest, pathlib.

**Spec:** `docs/superpowers/specs/2026-07-23-sync-conflict-tracker-recovery-design.md`

---

### Task 1: Recover archived divergent conflict actions in the shared tracker reader

**Files:**
- Modify: `ready_jobs_watcher/tracker_action_stream.py` (imports near top; new helpers before `_load_legacy_json_actions`; one new line inside `_load_legacy_json_actions`)
- Test: `tests/test_tracker_action_stream.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tracker_action_stream.py` (it already imports `json`, `Path`, and `load_cnc_tracker_actions` — add a small JSON-write helper alongside the existing `_write` text helper, and the four test functions below):

```python
def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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
```

- [ ] **Step 2: Run and verify failure**

```powershell
cd 'C:\Scripts\Ready Jobs Watcher'
.\.venv\Scripts\python.exe -m pytest tests/test_tracker_action_stream.py -q
```

Expected: the first test (`..._recovers_archived_divergent_conflict`) FAILS with `assert 1 == 2` (the archived action isn't recovered yet). The other three new tests are expected to already PASS at this point — they assert the archive is *not* folded, which is trivially true before the feature exists. They stay in the suite as regression guards once the feature is implemented with its guards intact.

- [ ] **Step 3: Add the imports and helpers**

In `ready_jobs_watcher/tracker_action_stream.py`, change the top of the file:

```python
from __future__ import annotations

import json
import os
import glob
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .file_handler import JobProcessor
```

(only the `from pathlib import Path` line and the `from .file_handler import JobProcessor` line are new; everything else already exists.)

Then add these functions immediately before `def _load_legacy_json_actions(` (i.e. right after `_next_recovery_index`):

```python
# Bound on how many parent directories to walk while looking for the enclosing job folder from
# a tracker_dir (e.g. "<job>/CNC/.tracker" is 2 levels down, "<job>/.metadata/hardwoods/.tracker"
# is 3). Generous relative to both known shapes so a future tracker path nested one level deeper
# still resolves, without walking indefinitely on an unexpected layout.
_JOB_FOLDER_SEARCH_DEPTH = 8


def _find_job_folder(tracker_dir: Path) -> Optional[Path]:
    current = tracker_dir.parent
    for _ in range(_JOB_FOLDER_SEARCH_DEPTH):
        try:
            if JobProcessor.is_job_folder(str(current)):
                return current
        except Exception:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _original_matches_tracker_dir(original: Path, tracker_dir: Path) -> bool:
    """
    True when a conflict manifest's derived original path belongs to this exact tracker_dir.

    Compares only the trailing two path components (case-insensitively), e.g. ("CNC", ".tracker")
    or ("hardwoods", ".tracker"), rather than the full path -- archived manifests on the live share
    record originalPath using whatever spelling Syncthing saw it under (both UNC
    "\\\\host\\share\\..." and mapped "Y:\\..." forms have been observed for the same file), so a
    full-path compare would spuriously fail even for a genuine match.
    """
    original_tail = tuple(part.lower() for part in original.parent.parts[-2:])
    tracker_tail = tuple(part.lower() for part in tracker_dir.parts[-2:])
    return len(tracker_tail) == 2 and original_tail == tracker_tail


def _load_recovered_conflict_rows(
    tracker_dir: Path,
    logger=None,
) -> List[Tuple[str, str, int, Dict[str, Any]]]:
    """
    Recover actions from archived divergent Syncthing conflicts belonging to tracker_dir.

    sync_conflict_resolver.py archives a genuinely divergent conflict copy under
    "<job>/.metadata/sync_conflicts/<archive_id>/" and never merges it back into the live file (by
    design -- it never overwrites original bytes). Without this, whatever actions only existed on
    the losing side of that conflict are gone from consolidation forever. This reads them back in
    as ordinary historical action rows, every pass -- no "already folded" marker. A marker written
    here would be unsafe: this reader is also called by read-only consumers (tracker_bad_parts.py,
    remake_candidates_indexer.py) that never persist anything, so one of them reading first could
    mark an archive done before metadata_cache.py's actual consolidation pass ever saw it, silently
    dropping the recovery. Re-reading every pass is cheap at realistic archive volumes and safe
    since the CNC/hardwoods merge functions are already idempotent over repeated historical rows.
    """
    job_folder = _find_job_folder(tracker_dir)
    if job_folder is None:
        return []

    sync_conflicts_root = job_folder / ".metadata" / "sync_conflicts"
    if not sync_conflicts_root.is_dir():
        return []

    rows: List[Tuple[str, str, int, Dict[str, Any]]] = []
    for manifest_path in sorted(sync_conflicts_root.glob("*/manifest.json")):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as exc:
            if logger is not None:
                logger.debug("Skipping unreadable sync-conflict manifest %s (%s)", manifest_path, exc)
            continue

        if not isinstance(manifest, dict) or manifest.get("action") != "archived_divergent":
            continue

        original_path = manifest.get("originalPath")
        archive_path = manifest.get("archivePath")
        if not isinstance(original_path, str) or not isinstance(archive_path, str):
            continue
        if not _original_matches_tracker_dir(Path(original_path), tracker_dir):
            continue

        archived_file = Path(archive_path)
        try:
            with open(archived_file, "r", encoding="utf-8") as f:
                archived_payload = json.load(f)
        except Exception as exc:
            if logger is not None:
                logger.debug("Skipping unreadable archived tracker file %s (%s)", archived_file, exc)
            continue

        if (
            not isinstance(archived_payload, dict)
            or not isinstance(archived_payload.get("tabletId"), str)
            or not isinstance(archived_payload.get("actions"), list)
        ):
            continue

        archived_path_str = str(archived_file)
        for idx, action in enumerate(archived_payload["actions"]):
            if not isinstance(action, dict):
                continue
            ts = str(action.get("timestamp", "") or "")
            rows.append((ts, archived_path_str, idx, action))

    return rows
```

- [ ] **Step 4: Wire the recovery rows into `_load_legacy_json_actions`**

In `ready_jobs_watcher/tracker_action_stream.py`, change:

```python
def _load_legacy_json_actions(
    tracker_dirs: Sequence[str],
    logger=None,
) -> List[Dict[str, Any]]:
    rows: List[Tuple[str, str, int, Dict[str, Any]]] = []
    for tracker_dir in tracker_dirs:
        if not os.path.isdir(tracker_dir):
            continue
        for name in sorted(os.listdir(tracker_dir)):
            if not name.lower().endswith(".json"):
                continue
            if not _is_active_stream_file(name):
                continue
            path = os.path.join(tracker_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:
                if logger is not None:
                    logger.warning("Skipping malformed tracker file %s (%s)", path, exc)
                continue
            if not isinstance(payload, dict):
                continue
            raw_actions = payload.get("actions")
            if not isinstance(raw_actions, list):
                continue
            for idx, action in enumerate(raw_actions):
                if not isinstance(action, dict):
                    continue
                ts = str(action.get("timestamp", "") or "")
                rows.append((ts, path, idx, action))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows]
```

to:

```python
def _load_legacy_json_actions(
    tracker_dirs: Sequence[str],
    logger=None,
) -> List[Dict[str, Any]]:
    rows: List[Tuple[str, str, int, Dict[str, Any]]] = []
    for tracker_dir in tracker_dirs:
        if not os.path.isdir(tracker_dir):
            continue
        for name in sorted(os.listdir(tracker_dir)):
            if not name.lower().endswith(".json"):
                continue
            if not _is_active_stream_file(name):
                continue
            path = os.path.join(tracker_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:
                if logger is not None:
                    logger.warning("Skipping malformed tracker file %s (%s)", path, exc)
                continue
            if not isinstance(payload, dict):
                continue
            raw_actions = payload.get("actions")
            if not isinstance(raw_actions, list):
                continue
            for idx, action in enumerate(raw_actions):
                if not isinstance(action, dict):
                    continue
                ts = str(action.get("timestamp", "") or "")
                rows.append((ts, path, idx, action))

        rows.extend(_load_recovered_conflict_rows(Path(tracker_dir), logger=logger))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows]
```

(the only change is the new `rows.extend(...)` line, still inside the `for tracker_dir in tracker_dirs:` loop, after the existing inner `for name in ...` loop.)

- [ ] **Step 5: Run and verify all four new tests pass**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tracker_action_stream.py -q
```

Expected: PASS, all tests including the four new ones.

- [ ] **Step 6: Run the full existing test file to confirm no regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tracker_action_stream.py tests/test_tracker_bad_parts_monitor.py tests/test_tracker_condensing.py tests/test_sync_conflict_resolver.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add -- 'ready_jobs_watcher/tracker_action_stream.py' 'tests/test_tracker_action_stream.py'
git commit -m "fix: recover archived divergent tracker conflicts during consolidation"
```

---

### Task 2: Confirm the same recovery works for the hardwoods tracker

**Files:**
- Test: `tests/test_tracker_action_stream.py`

The implementation in Task 1 lives entirely in the shape-agnostic legacy-JSON loader shared by both `load_cnc_tracker_actions` and `load_hardwoods_tracker_actions` — no hardwoods-specific code exists to write. This task is a verification-only test proving that promise holds, using `.metadata/hardwoods/.tracker` (three levels below the job folder, versus CNC's two) to exercise `_find_job_folder`'s walk-up loop over more than one hop.

- [ ] **Step 1: Write the test**

Add to `tests/test_tracker_action_stream.py` (add `load_hardwoods_tracker_actions` to the existing import line from `ready_jobs_watcher.tracker_action_stream`):

```python
def test_load_hardwoods_tracker_actions_recovers_archived_divergent_conflict(tmp_path):
    job_folder = tmp_path / "332 - TESTJOB"
    tracker_dir = job_folder / ".metadata" / "hardwoods" / ".tracker"
    _write_json(
        tracker_dir / "tablet-a.json",
        {
            "actions": [
                {"docType": "cutlist", "rowId": "row-1", "action": "set_done_count", "value": 3, "timestamp": "2026-07-08T19:53:41Z"}
            ]
        },
    )

    archive_dir = job_folder / ".metadata" / "sync_conflicts" / "20260708-125930-2E2GGMF"
    _write_json(
        archive_dir / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {"docType": "cutlist", "rowId": "row-2", "action": "set_done_count", "value": 5, "timestamp": "2026-07-08T19:53:45Z"}
            ],
        },
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

    actions = load_hardwoods_tracker_actions([str(tracker_dir)])

    assert len(actions) == 2
    assert {a["rowId"] for a in actions} == {"row-1", "row-2"}
```

- [ ] **Step 2: Run and verify it passes without further code changes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tracker_action_stream.py -q
```

Expected: PASS. If this fails, `_find_job_folder`'s walk-up loop or `_original_matches_tracker_dir`'s trailing-parts comparison has a hardwoods-specific bug — fix it in `tracker_action_stream.py` before proceeding (don't special-case hardwoods; the loader must stay shape-agnostic).

- [ ] **Step 3: Commit**

```powershell
git add -- 'tests/test_tracker_action_stream.py'
git commit -m "test: confirm archived-conflict recovery covers hardwoods tracker"
```

---

### Task 3: Repeat-call stability and full suite verification

**Files:**
- Test: `tests/test_tracker_action_stream.py`

- [ ] **Step 1: Write the repeat-call stability test**

```python
def test_load_cnc_tracker_actions_recovery_is_stable_across_repeated_calls(tmp_path):
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

    first = load_cnc_tracker_actions(str(tracker_dir))
    second = load_cnc_tracker_actions(str(tracker_dir))

    assert first == second
    assert len(first) == 2
```

- [ ] **Step 2: Run and verify it passes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tracker_action_stream.py -q
```

Expected: PASS (this should already hold given Task 1's implementation — the test exists to lock in the "no marker, always safe to re-read" behavior described in the spec, so a future change can't silently reintroduce a stateful marker without this test failing).

- [ ] **Step 3: Run the full project test suite**

```powershell
cd 'C:\Scripts\Ready Jobs Watcher'
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS, except the 3 pre-existing `tests/test_dae_converter.py` failures documented in `CLAUDE.md` as environmental (missing optional `mapbox_earcut`) — not regressions.

- [ ] **Step 4: Commit**

```powershell
git add -- 'tests/test_tracker_action_stream.py'
git commit -m "test: lock in stable repeated-read behavior for conflict recovery"
```

---

## Self-review

- **Spec coverage:** every numbered item in the spec's Data Flow section maps to Task 1 Step 3/4 (job-folder walk-up, glob, three-part filter, row extension, no-marker re-read). The Testing section's six points map: #1→Task 1 test 1, #2→Task 3, #3→Task 1 test 2, #4→Task 1 test 3, #5→Task 1 test 4, #6→Task 3 Step 3. The spec's hardwoods promise ("every consumer... gets recovered actions for free") maps to Task 2.
- **Placeholder scan:** no TBD/TODO; every step has complete, runnable code.
- **Type consistency:** `_load_recovered_conflict_rows(tracker_dir: Path, logger=None) -> List[Tuple[str, str, int, Dict[str, Any]]]` matches the row shape `_load_legacy_json_actions` already builds (`(timestamp, path, idx, action)`), so `rows.extend(...)` is a straight list-of-same-tuple-shape concatenation, not a type mismatch.
- **Out of scope, not carried into this plan:** the stale C-01 line in `kkc-metadata-map/SKILL.md` is a cross-repo synced doc with unrelated uncommitted changes already sitting in this repo's git status — fixing it here risks colliding with that other in-flight work. Flagged separately instead of bundled into this branch.
