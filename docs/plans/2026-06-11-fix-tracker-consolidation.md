# Fix Tracker Consolidation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent progress loss during daily tracker sweeps by reading and merging existing `consolidated.json` actions with new device actions instead of overwriting the consolidated actions entirely.

**Architecture:** We will modify `ready_jobs_watcher/metadata_cache.py` to read any existing consolidated actions in both `consolidate_cnc_tracker` and `consolidate_hardwoods_tracker` before processing new files. If no new device files exist, the functions will return early to avoid unnecessary file writes, preventing view-only files or empty directories from wiping the history. We will implement regression tests for both CNC and hardwoods incremental consolidation.

**Tech Stack:** Python 3.13, pytest

---

### Task 1: Write Regression Tests for Incremental Tracker Consolidation

**Files:**
- Modify: `tests/test_tracker_condensing.py`

**Step 1: Write the failing tests**

Modify `tests/test_tracker_condensing.py` to append the following tests:

```python
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
```

**Step 2: Run test to verify it fails**

Run:
```powershell
.venv\Scripts\python -m pytest tests/test_tracker_condensing.py -v
```
Expected: FAIL on the newly added tests because history is overwritten and views wipe the existing actions.

**Step 3: Commit the test file changes**

```powershell
git add tests/test_tracker_condensing.py
git commit -m "test: add regression tests for incremental tracker consolidation"
```

---

### Task 2: Implement Incremental CNC Tracker Consolidation

**Files:**
- Modify: `ready_jobs_watcher/metadata_cache.py`

**Step 1: Write the minimal implementation**

Modify `consolidate_cnc_tracker` in `ready_jobs_watcher/metadata_cache.py` to read existing `consolidated.json` actions, combine them, and return early if no new device files are found:

```python
def consolidate_cnc_tracker(job_folder: Path):
    tracker_dir = job_folder / "CNC" / ".tracker"
    if not tracker_dir.exists():
        return

    actions = []
    device_files = []

    # Read existing consolidated actions first
    consolidated_file = tracker_dir / "consolidated.json"
    if consolidated_file.exists():
        try:
            data = _read_json(consolidated_file, {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
        except Exception:
            pass

    # Scan for new device-specific action files
    for entry in os.scandir(tracker_dir):
        if not entry.is_file() or not entry.name.endswith(".json") or entry.name == "consolidated.json":
            continue
        try:
            stat = entry.stat()
            data = _read_json(Path(entry.path), {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
                device_files.append((Path(entry.path), stat.st_mtime, stat.st_size))
        except Exception:
            pass

    # If no new device files exist, do not rewrite or delete anything
    if not device_files:
        return

    actions.sort(key=lambda a: a.get("timestamp", ""))
    page_states = {}
    for action_obj in actions:
        filename = action_obj.get("file")
        page = action_obj.get("page")
        action = action_obj.get("action")
        part = action_obj.get("part")
        timestamp = action_obj.get("timestamp", "")
        fingerprint = action_obj.get("fileFingerprint")
        if not filename or page is None or not action:
            continue
        key = (filename, page, fingerprint)
        page_states.setdefault(key, {"latest_action": "", "timestamp": "", "bad_parts": set()})
        state = page_states[key]
        if action == "bad_part" and part is not None:
            state["bad_parts"].add(part)
        elif action == "unbad_part" and part is not None:
            state["bad_parts"].discard(part)
        elif action in ("complete", "skip", "unskip"):
            if not state["timestamp"] or timestamp > state["timestamp"]:
                state["latest_action"] = action
                state["timestamp"] = timestamp

    consolidated_actions = []
    for (filename, page, fingerprint), state in page_states.items():
        if state["latest_action"] in ("complete", "skip"):
            act = {"file": filename, "page": page, "action": state["latest_action"], "timestamp": state["timestamp"]}
            if fingerprint:
                act["fileFingerprint"] = fingerprint
            consolidated_actions.append(act)
        for part in sorted(state["bad_parts"]):
            act = {
                "file": filename,
                "page": page,
                "action": "bad_part",
                "part": part,
                "timestamp": state["timestamp"] or "2026-01-01T00:00:00Z",
            }
            if fingerprint:
                act["fileFingerprint"] = fingerprint
            consolidated_actions.append(act)

    _atomic_write_json(tracker_dir / "consolidated.json", {"tabletId": "consolidated", "actions": consolidated_actions})
    _delete_unchanged_device_files(device_files)
```

**Step 2: Run test to verify CNC tests pass**

Run:
```powershell
.venv\Scripts\python -m pytest tests/test_tracker_condensing.py -k cnc -v
```
Expected: PASS.

**Step 3: Commit**

```powershell
git add ready_jobs_watcher/metadata_cache.py
git commit -m "fix: implement incremental CNC tracker consolidation"
```

---

### Task 3: Implement Incremental Hardwoods Tracker Consolidation

**Files:**
- Modify: `ready_jobs_watcher/metadata_cache.py`

**Step 1: Write the minimal implementation**

Modify `consolidate_hardwoods_tracker` in `ready_jobs_watcher/metadata_cache.py` to read existing `consolidated.json` actions, combine them, and return early if no new device files are found:

```python
def consolidate_hardwoods_tracker(job_folder: Path):
    tracker_dir = job_folder / ".metadata" / "hardwoods" / ".tracker"
    if not tracker_dir.exists():
        return

    actions = []
    device_files = []

    # Read existing consolidated actions first
    consolidated_file = tracker_dir / "consolidated.json"
    if consolidated_file.exists():
        try:
            data = _read_json(consolidated_file, {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
        except Exception:
            pass

    # Scan for new device-specific action files
    for entry in os.scandir(tracker_dir):
        if not entry.is_file() or not entry.name.endswith(".json") or entry.name == "consolidated.json":
            continue
        try:
            stat = entry.stat()
            data = _read_json(Path(entry.path), {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
                device_files.append((Path(entry.path), stat.st_mtime, stat.st_size))
        except Exception:
            pass

    # If no new device files exist, do not rewrite or delete anything
    if not device_files:
        return

    actions.sort(key=lambda a: a.get("timestamp", ""))
    done_count = {}
    skipped = set()
    timestamps = {}
    for action_obj in actions:
        doc_type = action_obj.get("docType")
        row_id = action_obj.get("rowId")
        action = action_obj.get("action")
        val = action_obj.get("value", 0)
        timestamp = action_obj.get("timestamp", "")
        if not doc_type or not row_id or not action:
            continue
        key = f"{doc_type}|{row_id}"
        timestamps[key] = timestamp
        if action == "set_done_count":
            done_count[key] = val
        elif action == "set_skipped":
            skipped.add(key)
        elif action == "clear_skipped":
            skipped.discard(key)

    consolidated_actions = []
    for key, ts in timestamps.items():
        doc_type, row_id = key.split("|", 1)
        val = done_count.get(key, 0)
        if val > 0:
            consolidated_actions.append(
                {"docType": doc_type, "rowId": row_id, "action": "set_done_count", "value": val, "timestamp": ts}
            )
        if key in skipped:
            consolidated_actions.append({"docType": doc_type, "rowId": row_id, "action": "set_skipped", "timestamp": ts})

    _atomic_write_json(tracker_dir / "consolidated.json", {"tabletId": "consolidated", "actions": consolidated_actions})
    _delete_unchanged_device_files(device_files)
```

**Step 2: Run all tests to verify everything passes**

Run:
```powershell
.venv\Scripts\python -m pytest tests/test_tracker_condensing.py -v
```
Expected: PASS.

**Step 3: Commit**

```powershell
git add ready_jobs_watcher/metadata_cache.py
git commit -m "fix: implement incremental hardwoods tracker consolidation"
```
