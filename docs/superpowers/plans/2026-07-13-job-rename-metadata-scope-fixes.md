# Job Rename Metadata Scope Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `rename_ready_job`'s JSON rewrite from corrupting unrelated jobs' data in shared multi-job files (`job_board.json`, `production_order.json`, `delivery_schedule.json`), and stop CNC progress from silently orphaning under a stale filename after the PDF it belongs to gets renamed.

**Architecture:** Two independent, narrowly-scoped fixes to existing modules. (1) `job_rename.py`'s generic recursive JSON rewriter gains a small "is this list item someone else's job record" check so it only rewrites the specific job's own entry inside a multi-job array, instead of blindly substring-replacing every string in the whole file. (2) `metadata_cache.py`'s CNC consolidation gains a fingerprint-based filename reconciliation step so progress recorded under a PDF's old name (before that PDF got renamed) still merges with progress recorded after, instead of being tracked as two separate, disconnected entries.

**Tech Stack:** Python 3.13, pytest.

**Background reading:** `docs/superpowers/plans/2026-07-13-job-rename-propagation.md` and its design spec (the effort that shipped folder/derived-file renaming and the duplicate-ghost guard this plan builds on top of).

---

## Metadata Source Audit

This is the full picture the user asked for: every metadata source that could plausibly need
attention when a job gets renamed, and its current status. Read this before touching code — it's
why only 2 of the sources below get a task in this plan.

| Source | Owner | Status | Notes |
|---|---|---|---|
| Job folder name itself | Ready Jobs Watcher | **Fixed** (2026-07-13 effort) | `rename_ready_job` |
| Derived files (DARK MODE PDFs, etc.) | Ready Jobs Watcher | **Fixed** (2026-07-13 effort) | Task 2 of the prior plan |
| Ghost/duplicate job resurrection | Ready Jobs Watcher | **Fixed** (2026-07-13 effort) | Tasks 3-7 of the prior plan |
| `job_board.json`, `production_order.json` (root) | Hours Tracker writes, RJW reads+rewrites | **Bug found, fixed here** | Task 1 below — blast-radius bug: rewrite touches every string in the whole file, not just the renamed job's own record |
| `.metadata/delivery_schedule.json` (root) | Hours Tracker | **Same bug class, fixed here** | Covered by the same Task 1 fix (it's walked by `_iter_root_metadata_json`, same rewrite code path) |
| `<job>/.metadata/deployment_gate.json` | Ready Jobs Watcher | Already safe | Single-job file, one record, no blast radius possible |
| `<job>/.metadata/cache_static.json`, `cabinet_sheet_index.json`, `hardwoods/cutlist_index.json` | Ready Jobs Watcher | Already safe | Single-job files |
| `<job>/.metadata/admin/specialty_items.json`, `rip_items.json`, `checklist.json`, etc. | Hours Tracker writes, RJW rewrites content | Already safe | Single-job files, and their own item records don't embed job folder name/number at all (confirmed against a real specialty_items.json) — nothing to corrupt |
| CNC progress (`consolidated.json` under `CNC/.tracker`, from tablet ndjson/legacy json) | Tablets write, Ready Jobs Watcher consolidates | **Bug found, fixed here** | Task 2 below — progress keyed by `(filename, page, fingerprint)`; a PDF rename orphans pre-rename progress under the old filename |
| Hardwoods progress (`consolidated.json` under `.metadata/hardwoods/.tracker`) | Tablets write, Ready Jobs Watcher consolidates | Already safe, no fix needed | Keyed by `(docType, rowId)` — never references a filename at all, so a rename can't orphan it. Checked `_merge_hardwoods_actions` directly to confirm before assuming symmetry with the CNC bug |
| Bad-parts acknowledged/seen/active state (`tracker_bad_parts_state.json`, local app state) | Ready Jobs Watcher | Already safe, pre-existing | `TrackerBadPartsMonitor.rename_job_folder()` already migrates every persisted key from the old job_folder_name+pdf_filename to the new one — already wired into `Application.rename_job`. Not something this effort touched; verified it's real and already called |
| `.supply/*` (categories, items, status, comments) | Hours Tracker only | Out of scope | No code in this repo (`ready_jobs_watcher/`) reads or writes `.supply` at all — grepped and confirmed zero hits. If these ever need rename-awareness, that's Hours Tracker's own codebase, not fixable from here |
| Hours Tracker's own SQLite reporting DB, `.time_cards` | Hours Tracker only | Out of scope | Explicitly documented elsewhere as Hours Tracker's own read cache, not a source of truth this repo touches |
| KKCSheetTracker Android's own display/matching logic (does it match tablet-side by filename or fingerprint when showing "is this page done") | KKCSheetTracker (separate Android repo) | **Unknown, not verifiable from here** | This repo produces `consolidated.json`; the Android app is what actually renders per-page completion state from it. Whether the app's own matching is filename-strict or fingerprint-tolerant can't be checked without that repo's source. Task 2 below makes the *data this repo produces* correct (current filename, once fingerprint-reconciled) regardless of how the app matches it |

---

## Task 1: Scope multi-job JSON rewrites to the renamed job's own record

**Files:**
- Modify: `ready_jobs_watcher/job_rename.py`
- Test: `tests/test_job_rename_metadata.py`

**The bug, confirmed live on `Y:\Ready Jobs\job_board.json`:** `_rewrite_json_value` recurses into
*every* string in the entire JSON document and replaces any occurrence of the old job's folder
name, with no concept of "which job's record this string belongs to." `job_board.json` holds an
array of per-job dicts (`folder_name`, `job_number`, `job_name`, `construction_method`, etc.) — one
entry per job on the board. `construction_method` mirrors RJW's own delivery-sheet mode detection
(`detect_mode_for_job` in `cabinet_sheet_indexer.py`, stored per-job in `deployment_gate.json`'s
`selectedMode`/`modeDetection`) — confirmed by checking job `587 - BLANKENSHIP 38984 DEXTER`'s own
`deployment_gate.json` directly: `"selectedMode": "FACE-FRAME"`, `"modeDetection": {"source":
"DELIVERY_SHEET"}` — RJW's own detection is correct and uncorrupted. It's Hours Tracker's *mirrored
copy* of that value in `job_board.json` that got clobbered: when job `502 - HARTFORD McCASLIN
REFACE` was renamed to `649 - HARTFORD McCASLIN REFACE` this morning, 8 *other, unrelated* jobs'
`construction_method` fields in `job_board.json` got overwritten to the literal string
`"649 - HARTFORD McCASLIN REFACE"`, because those fields already contained the old job's name as
their value before the rename, and the rewrite doesn't know those fields belong to a different
job's record — it's a straightforward consequence of this repo's own blind substring replace, not
a Hours Tracker bug or a bad detection anywhere.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_job_rename_metadata.py` (the file already has `import json` at the top):

```python
def test_rename_ready_job_does_not_corrupt_unrelated_job_records(tmp_path):
    root = tmp_path / "Ready Jobs"
    old_name = "502 - HARTFORD MCCASLIN REFACE"
    new_name = "649 - HARTFORD MCCASLIN REFACE"
    (root / old_name).mkdir(parents=True)

    _write_json(
        root / "job_board.json",
        {
            "jobs": [
                {
                    "folder_name": old_name,
                    "job_number": "502",
                    "job_name": "HARTFORD MCCASLIN REFACE",
                    "construction_method": "FACE-FRAME",
                },
                {
                    "folder_name": "999 - UNRELATED JOB",
                    "job_number": "999",
                    "job_name": "UNRELATED JOB",
                    # This unrelated job's construction_method happens to already contain
                    # the renamed job's old name as a value (a real, separate, pre-existing
                    # Hours Tracker data bug) - a blanket substring replace would corrupt it
                    # even though this record belongs to a completely different job.
                    "construction_method": old_name,
                },
            ]
        },
    )

    rename_ready_job(
        root, old_name, new_name, archive_root=None, rename_history_file=tmp_path / "rename_history.json"
    )

    job_board = _read_json(root / "job_board.json")
    renamed_entry = job_board["jobs"][0]
    unrelated_entry = job_board["jobs"][1]

    assert renamed_entry["folder_name"] == new_name
    assert renamed_entry["job_number"] == "649"
    assert unrelated_entry["folder_name"] == "999 - UNRELATED JOB"
    assert unrelated_entry["job_number"] == "999"
    assert unrelated_entry["construction_method"] == old_name  # untouched - not this job's record
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_job_rename_metadata.py -k does_not_corrupt_unrelated -v`
Expected: FAIL — `unrelated_entry["construction_method"]` comes back as `new_name`, not `old_name`
(the bug reproduces)

- [ ] **Step 3: Add the record-scoping check**

In `ready_jobs_watcher/job_rename.py`, add this constant right after `JOB_NUMBER_KEYS` (line 16):

```python
JOB_FOLDER_NAME_KEYS = {"folder_name", "folderName", "jobFolderName"}
```

Then add this function right after `_replace_text` (after line 58), before `_rewrite_json_value`:

```python
def _job_record_scope(item: Any, *, old_name: str, old_num: str) -> Optional[bool]:
    """Determine whether a list item is a per-job record belonging to the job being renamed.

    Returns False when `item` is a dict carrying a recognizable job-identity key (folder name
    or job number) whose value does NOT match - this is a DIFFERENT job's record and must not
    be touched, even if some other unrelated field happens to contain a matching substring
    (this is exactly what let a rename corrupt an unrelated job's `construction_method` field
    in job_board.json - a blanket substring replace has no concept of "which record"). Returns
    True when the identity key matches (this job's own record - rewrite normally). Returns
    None when `item` has no such key at all (not recognizably a job record), so the caller
    falls back to the original blanket-recursive rewrite for anything not covered by this
    heuristic (this is the safe default - it never makes existing behavior more restrictive
    for structures this function doesn't recognize).
    """
    if not isinstance(item, dict):
        return None
    for key in JOB_FOLDER_NAME_KEYS:
        if key in item:
            return str(item.get(key) or "") == old_name
    for key in JOB_NUMBER_KEYS:
        if key in item:
            return str(item.get(key) or "") == old_num
    return None
```

- [ ] **Step 4: Use the scope check in the list-rewrite branch**

In `ready_jobs_watcher/job_rename.py`, in `_rewrite_json_value`, change the list-handling branch
(currently):

```python
    if isinstance(value, list):
        changed = False
        rewritten_items = []
        for item in value:
            rewritten_item, item_changed = _rewrite_json_value(
                item,
                key=key,
                old_name=old_name,
                new_name=new_name,
                old_num=old_num,
                new_num=new_num,
                old_job_name=old_job_name,
                new_job_name=new_job_name,
            )
            rewritten_items.append(rewritten_item)
            changed = changed or item_changed
        return rewritten_items, changed
```

to:

```python
    if isinstance(value, list):
        changed = False
        rewritten_items = []
        for item in value:
            if _job_record_scope(item, old_name=old_name, old_num=old_num) is False:
                rewritten_items.append(item)
                continue
            rewritten_item, item_changed = _rewrite_json_value(
                item,
                key=key,
                old_name=old_name,
                new_name=new_name,
                old_num=old_num,
                new_num=new_num,
                old_job_name=old_job_name,
                new_job_name=new_job_name,
            )
            rewritten_items.append(rewritten_item)
            changed = changed or item_changed
        return rewritten_items, changed
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_job_rename_metadata.py -v`
Expected: all PASS (7 passed)

- [ ] **Step 6: Run the full test suite to confirm no regression**

Run: `python -m pytest tests/ -q`
Expected: all PASS (381 passed — 380 from before this plan, plus this new test)

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/job_rename.py tests/test_job_rename_metadata.py
git commit -m "fix: scope multi-job JSON rewrites to the renamed job's own record"
```

---

## Task 2: Reconcile CNC progress under a renamed PDF's old filename

**Files:**
- Modify: `ready_jobs_watcher/metadata_cache.py`
- Test: `tests/test_tracker_condensing.py`

**The gap:** `_merge_cnc_actions` keys progress by `(filename, page, fingerprint)`, where
`filename` comes straight from the historical action record (what the tablet wrote at the time,
which for an action recorded before a job rename still says the OLD PDF name). `fileFingerprint`
is `f"{size}_{mtime}"` (see `metadata_cache.py:201`), which a plain OS-level rename does NOT
change (rename touches neither file content nor mtime) — so it's a reliable, rename-proof way to
recognize "this is the same physical PDF" even when the filename differs. Today, nothing
reconciles a stale filename against the fingerprint, so progress recorded before a PDF rename and
progress recorded after end up as two separate, never-merged entries in `consolidated.json`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tracker_condensing.py`:

```python
def test_cnc_consolidation_reconciles_stale_filename_via_fingerprint(tmp_path):
    job = tmp_path / "Ready Jobs" / "649 - Test Job"
    cnc_dir = job / "CNC"
    cnc_dir.mkdir(parents=True)
    pdf_path = cnc_dir / "649 - Maple.pdf"
    pdf_path.write_bytes(b"pdf-bytes")
    stat = pdf_path.stat()
    fingerprint = f"{stat.st_size}_{int(stat.st_mtime * 1000)}"

    _write_json(
        job / "CNC" / ".tracker" / "tablet-a.json",
        {
            "tabletId": "tablet-a",
            "actions": [
                {
                    # Recorded before the job was renamed 502 -> 649 - the tablet wrote the
                    # PDF's name as it was at the time, which this repo's rename code never
                    # goes back and rewrites inside historical tracker records.
                    "file": "502 - Maple.pdf",
                    "page": 1,
                    "action": "complete",
                    "timestamp": "2026-06-09T10:00:00Z",
                    "fileFingerprint": fingerprint,
                },
            ],
        },
    )

    consolidate_cnc_tracker(job)

    cnc_actions = json.loads((job / "CNC" / ".tracker" / "consolidated.json").read_text(encoding="utf-8"))["actions"]
    assert cnc_actions == [
        {
            "file": "649 - Maple.pdf",
            "page": 1,
            "action": "complete",
            "timestamp": "2026-06-09T10:00:00Z",
            "fileFingerprint": fingerprint,
        }
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker_condensing.py -k reconciles_stale_filename -v`
Expected: FAIL — `cnc_actions[0]["file"]` comes back as `"502 - Maple.pdf"` (the stale, unreconciled name)

- [ ] **Step 3: Add the fingerprint-map builder and thread it through the merge**

In `ready_jobs_watcher/metadata_cache.py`, add this function right before `_merge_cnc_actions`:

```python
def _build_cnc_fingerprint_map(job_folder: Path) -> Dict[str, str]:
    """Map each current CNC PDF's fingerprint to its current filename.

    Used to reconcile progress actions recorded under a PDF's OLD filename (before that PDF
    got renamed along with its job) back onto the CURRENT filename - a plain OS rename changes
    neither a file's size nor its mtime, so the fingerprint is a reliable link across the rename
    even though the filename in the historical action record is stale.
    """
    cnc_dir = job_folder / "CNC"
    mapping: Dict[str, str] = {}
    if not cnc_dir.is_dir():
        return mapping
    for entry in os.scandir(cnc_dir):
        if not entry.is_file() or not entry.name.lower().endswith(".pdf"):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        fingerprint = f"{stat.st_size}_{int(stat.st_mtime * 1000)}"
        mapping[fingerprint] = entry.name
    return mapping
```

Then change the `_merge_cnc_actions` signature and its filename lookup (currently):

```python
def _merge_cnc_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
```

to:

```python
def _merge_cnc_actions(
    actions: List[Dict[str, Any]],
    *,
    fingerprint_to_current_filename: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    fingerprint_to_current_filename = fingerprint_to_current_filename or {}
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
        current_filename = fingerprint_to_current_filename.get(fingerprint)
        if current_filename:
            filename = current_filename
        key = (filename, page, fingerprint)
```

(Everything below that in the function already uses the local `filename` variable to build both
`page_states`'s key and every `act`/`submitted_act` dict's `"file"` field, so reconciling it once
here at the top flows through to the output automatically - no other lines in this function need
to change.)

- [ ] **Step 4: Pass the fingerprint map in from consolidate_cnc_tracker**

In `ready_jobs_watcher/metadata_cache.py`, change `consolidate_cnc_tracker` (currently):

```python
def consolidate_cnc_tracker(job_folder: Path, compact: bool = False):
    # CROSS-PROGRAM: the per-device <tabletId>.json files and events/<tabletId>.ndjson streams
    # here are PRODUCED by KKCSheetTracker tablets (ProgressStore.kt) and CONSUMED by this
    # watcher. This function merges them into consolidated.json. Legacy device files are deleted
    # after a successful merge; ndjson event files are left alone unless compact=True (only the
    # after-hours sweep passes that, wired in Task 4).
    # FIXED (METADATA_AUDIT.md C-01/M-06): the merge tracks, per (file, page, fingerprint, part),
    # whether the part is currently bad and whether it has been submitted for the engineer alert
    # (tracker_bad_parts.py:448 requires a `bad_part_submitted` action to fire). Both `bad_part` and
    # `bad_part_submitted` are re-emitted into consolidated.json with their own original timestamps
    # (not a shared/fallback timestamp), and `unbad_part` resets the submitted flag, mirroring the
    # reactivation semantics in tracker_bad_parts.py so the alert survives device-file deletion.
    _consolidate_tracker(
        job_folder / "CNC" / ".tracker",
        _merge_cnc_actions,
        lambda tracker_dir: load_cnc_tracker_actions(str(tracker_dir)),
        compact=compact,
    )
```

to:

```python
def consolidate_cnc_tracker(job_folder: Path, compact: bool = False):
    # CROSS-PROGRAM: the per-device <tabletId>.json files and events/<tabletId>.ndjson streams
    # here are PRODUCED by KKCSheetTracker tablets (ProgressStore.kt) and CONSUMED by this
    # watcher. This function merges them into consolidated.json. Legacy device files are deleted
    # after a successful merge; ndjson event files are left alone unless compact=True (only the
    # after-hours sweep passes that, wired in Task 4).
    # FIXED (METADATA_AUDIT.md C-01/M-06): the merge tracks, per (file, page, fingerprint, part),
    # whether the part is currently bad and whether it has been submitted for the engineer alert
    # (tracker_bad_parts.py:448 requires a `bad_part_submitted` action to fire). Both `bad_part` and
    # `bad_part_submitted` are re-emitted into consolidated.json with their own original timestamps
    # (not a shared/fallback timestamp), and `unbad_part` resets the submitted flag, mirroring the
    # reactivation semantics in tracker_bad_parts.py so the alert survives device-file deletion.
    fingerprint_map = _build_cnc_fingerprint_map(job_folder)
    _consolidate_tracker(
        job_folder / "CNC" / ".tracker",
        lambda actions: _merge_cnc_actions(actions, fingerprint_to_current_filename=fingerprint_map),
        lambda tracker_dir: load_cnc_tracker_actions(str(tracker_dir)),
        compact=compact,
    )
```

- [ ] **Step 5: Fix an existing test's monkeypatch wrapper to forward the new keyword argument**

`tests/test_tracker_condensing.py` already has
`test_concurrent_cnc_consolidation_via_real_threads_converges`, which monkeypatches
`metadata_cache._merge_cnc_actions` with a `slow_merge` wrapper. Since Task 2's new call site in
`consolidate_cnc_tracker` calls `_merge_cnc_actions(actions,
fingerprint_to_current_filename=fingerprint_map)`, and monkeypatching replaces that name at the
module level, `slow_merge` now receives that keyword argument too — but it's currently defined to
accept only `actions`. Without this fix, that test fails with
`TypeError: slow_merge() got an unexpected keyword argument 'fingerprint_to_current_filename'`.

Change (currently):

```python
    def slow_merge(actions):
        time.sleep(0.2)
        return original_merge(actions)
```

to:

```python
    def slow_merge(actions, **kwargs):
        time.sleep(0.2)
        return original_merge(actions, **kwargs)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_tracker_condensing.py -v`
Expected: all PASS (check the file's current test count and confirm one more than before)

- [ ] **Step 7: Run the full test suite to confirm no regression**

Run: `python -m pytest tests/ -q`
Expected: all PASS (382 passed — 381 from Task 1, plus this new test)

- [ ] **Step 8: Commit**

```bash
git add ready_jobs_watcher/metadata_cache.py tests/test_tracker_condensing.py
git commit -m "fix: reconcile CNC progress under a renamed PDF's old filename via fingerprint"
```

---

## Self-Review Notes

- **Spec coverage:** every row in the Metadata Source Audit table marked "Bug found, fixed here"
  has a task above. Rows marked "Already safe" or "Out of scope" are documented, not coded against
  — adding code for them would be unnecessary (YAGNI) since they either already work correctly or
  aren't fixable from this repo.
- **Task 1 and Task 2 are independent** — either can be implemented and merged without the other.
- **Type/name consistency:** `_job_record_scope` (Task 1) and `_build_cnc_fingerprint_map` /
  `fingerprint_to_current_filename` (Task 2) are each used consistently within their own task; the
  two tasks don't share any new names.
- **Backward compatibility checked:** Task 1's change only alters behavior for list items that
  carry a job-identity key with a *non-matching* value (previously incorrectly rewritten, now
  correctly skipped) — every other case (matching identity, or no identity key at all) behaves
  exactly as before. Task 2's `_merge_cnc_actions` signature change adds an optional keyword-only
  parameter — this DOES require a fix to an existing test
  (`test_concurrent_cnc_consolidation_via_real_threads_converges`), which monkeypatches
  `_merge_cnc_actions` with a wrapper that only forwarded a single positional argument. Caught
  during this plan's own self-review (not by running the tests) - Task 2 Step 5 updates that
  wrapper to accept and forward `**kwargs` before it would otherwise break.
