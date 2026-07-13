# Job Rename Propagation & Duplicate-Job Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make job renames actually rename every derived file that carries the old job number, and stop a Syncthing sync race from silently spawning an untracked duplicate job.

**Architecture:** Two independent additions. (1) `rename_ready_job()` in `job_rename.py` gains a final recursive pass that renames any file still carrying the old job-number prefix. (2) A new small module, `duplicate_job_guard.py`, detects job-number collisions and is wired into the single choke point (`Application.on_new_job_folder_detected`) that all "a job folder appeared" events already funnel through; a suspected duplicate gets a marker file instead of a live `deployment_gate.json`, and is surfaced in the existing Jobs table/dialog rather than silently adopted.

**Tech Stack:** Python 3.13, PyQt6 (GUI, not unit-tested per project convention), pytest.

**Spec:** `docs/superpowers/specs/2026-07-13-job-rename-propagation-design.md`

---

## Task 1: Extract the shared filename-prefix helper

**Files:**
- Modify: `ready_jobs_watcher/job_rename.py` (add function near the top, after imports)
- Modify: `ready_jobs_watcher/main.py:632-639` (`_target_filename_for_job`)
- Test: `tests/test_job_rename_metadata.py`

This closes a small duplication gap before Task 2 needs the same logic: `main.py`'s
`Application._target_filename_for_job` computes "swap this filename's leading `<num> - `
prefix for a new job number" and Task 2's derived-file rename needs the exact same
computation. Putting it in one place means the two rename code paths can't drift into two
different filename conventions.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_job_rename_metadata.py` (top-level, alongside the existing imports):

```python
from ready_jobs_watcher.job_rename import apply_job_number_prefix


def test_apply_job_number_prefix_swaps_leading_number():
    assert apply_job_number_prefix("456", "123 - Assembly Sheets.pdf") == "456 - Assembly Sheets.pdf"


def test_apply_job_number_prefix_prepends_when_no_separator():
    assert apply_job_number_prefix("456", "Assembly Sheets.pdf") == "456 - Assembly Sheets.pdf"


def test_apply_job_number_prefix_is_noop_when_already_correct():
    assert apply_job_number_prefix("456", "456 - Assembly Sheets.pdf") == "456 - Assembly Sheets.pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_job_rename_metadata.py -k apply_job_number_prefix -v`
Expected: FAIL with `ImportError: cannot import name 'apply_job_number_prefix'`

- [ ] **Step 3: Add the function to job_rename.py**

In `ready_jobs_watcher/job_rename.py`, add this function right after the `ROOT_METADATA_FILES`
constant (after line 14, before the `JobRenameResult` dataclass):

```python
def apply_job_number_prefix(job_num: str, original_name: str) -> str:
    """Return `original_name` with its leading "<num> - " prefix swapped for `job_num`.

    If `original_name` has no " - " separator, `job_num` is prepended instead.
    """
    if " - " in original_name:
        prefix, rest = original_name.split(" - ", 1)
        if prefix == job_num:
            return original_name
        return job_num + " - " + rest
    return job_num + " - " + original_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_job_rename_metadata.py -k apply_job_number_prefix -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Delegate main.py's copy to the shared function**

In `ready_jobs_watcher/main.py`, replace the body of `_target_filename_for_job` (lines 632-639):

```python
    @staticmethod
    def _target_filename_for_job(job_num: str, original_name: str) -> str:
        if " - " in original_name:
            prefix, rest = original_name.split(" - ", 1)
            if prefix == job_num:
                return original_name
            return job_num + " - " + rest
        return job_num + " - " + original_name
```

with:

```python
    @staticmethod
    def _target_filename_for_job(job_num: str, original_name: str) -> str:
        from .job_rename import apply_job_number_prefix
        return apply_job_number_prefix(job_num, original_name)
```

- [ ] **Step 6: Run the existing rename test suite to confirm no regression**

Run: `python -m pytest tests/test_main_rename.py tests/test_job_rename_metadata.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/job_rename.py ready_jobs_watcher/main.py tests/test_job_rename_metadata.py
git commit -m "refactor: extract shared job-number filename-prefix helper"
```

---

## Task 2: Rename derived files after a job rename (Fix A)

**Files:**
- Modify: `ready_jobs_watcher/job_rename.py`
- Test: `tests/test_job_rename_metadata.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_job_rename_metadata.py`:

```python
def test_rename_ready_job_renames_orphaned_derived_files(tmp_path):
    root = tmp_path / "Ready Jobs"
    old_name = "123 - OLD CUSTOMER"
    new_name = "456 - NEW CUSTOMER"
    job = root / old_name
    dark_mode = job / "DARK MODE"
    dark_mode.mkdir(parents=True)
    (dark_mode / "123 - ASSEMBLY SHEETS.pdf").write_bytes(b"pdf")
    (dark_mode / "123 - DELIVERY SHEETS.pdf").write_bytes(b"pdf")
    # Already-correct-prefix file must be left alone (no rename attempted, no error).
    (dark_mode / "456 - ALREADY CORRECT.pdf").write_bytes(b"pdf")
    # A tablet-authored tracker file must never be touched even if its name happens
    # to start with the old job number.
    tracker_dir = job / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "123 - tablet-07.json").write_text("{}", encoding="utf-8")

    result = rename_ready_job(root, old_name, new_name, archive_root=None)

    new_dark_mode = root / new_name / "DARK MODE"
    assert (new_dark_mode / "456 - ASSEMBLY SHEETS.pdf").exists()
    assert (new_dark_mode / "456 - DELIVERY SHEETS.pdf").exists()
    assert (new_dark_mode / "456 - ALREADY CORRECT.pdf").exists()
    assert not (new_dark_mode / "123 - ASSEMBLY SHEETS.pdf").exists()

    new_tracker_dir = root / new_name / "CNC" / ".tracker"
    assert (new_tracker_dir / "123 - tablet-07.json").exists()

    renamed_names = {p.name for p in result.renamed_derived_files}
    assert renamed_names == {"456 - ASSEMBLY SHEETS.pdf", "456 - DELIVERY SHEETS.pdf"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_job_rename_metadata.py -k orphaned_derived_files -v`
Expected: FAIL (`AttributeError: 'JobRenameResult' object has no attribute 'renamed_derived_files'`
or the DARK MODE files are still `123 -` prefixed)

- [ ] **Step 3: Add the renamed_derived_files field to JobRenameResult**

In `ready_jobs_watcher/job_rename.py`, change the dataclass:

```python
@dataclass(frozen=True)
class JobRenameResult:
    old_name: str
    new_name: str
    renamed_folder: bool
    rewritten_files: tuple[Path, ...]
    cache_refreshed: bool
```

to:

```python
@dataclass(frozen=True)
class JobRenameResult:
    old_name: str
    new_name: str
    renamed_folder: bool
    rewritten_files: tuple[Path, ...]
    cache_refreshed: bool
    renamed_derived_files: tuple[Path, ...] = tuple()
```

- [ ] **Step 4: Add the recursive derived-file rename helper**

In `ready_jobs_watcher/job_rename.py`, add this near the other module-level helpers (after
`_iter_root_metadata_json`, before `_normalize_deployment_gate`):

```python
# Any ".metadata" directory (job-root .metadata, CNC\.metadata, .metadata\hardwoods, etc.)
# is skipped entirely: JSON sidecars in there already have their *content* rewritten by
# the _iter_job_metadata_json step above, and their filenames are corrected separately by
# the normal PDF-reprocessing pipeline that runs right after a rename - renaming them here
# too would collide with that. Any ".tracker" directory (CNC\.tracker,
# .metadata\hardwoods\.tracker) is also skipped: those hold live tablet-authored files
# named by tablet ID, never by job number.
def _iter_renamable_files(job_folder: Path):
    for path in job_folder.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(job_folder).parts
        if ".metadata" in parts or ".tracker" in parts:
            continue
        yield path


def _rename_derived_files(job_folder: Path, *, old_num: str, new_num: str) -> tuple[Path, ...]:
    if not old_num or not new_num or old_num == new_num:
        return tuple()
    old_prefix = f"{old_num} - "
    renamed: list[Path] = []
    for path in _iter_renamable_files(job_folder):
        if not path.name.startswith(old_prefix):
            continue
        new_name = apply_job_number_prefix(new_num, path.name)
        new_path = path.with_name(new_name)
        if new_path.exists():
            continue
        try:
            path.rename(new_path)
            renamed.append(new_path)
        except OSError as exc:
            main_logger.warning("Could not rename derived file %s during job rename: %s", path, exc)
    return tuple(renamed)
```

- [ ] **Step 5: Add the logger and wire the new step into rename_ready_job**

In `ready_jobs_watcher/job_rename.py`, add a module logger near the top (after the existing
imports, before `JOB_NUMBER_KEYS`):

```python
import logging

main_logger = logging.getLogger("main")
```

Then in `rename_ready_job`, insert the new step right after the `for path in
_iter_job_metadata_json(new_path):` loop and before `_normalize_deployment_gate(root, new_name)`:

```python
    renamed_derived_files = _rename_derived_files(new_path, old_num=old_num, new_num=new_num)

    _normalize_deployment_gate(root, new_name)
```

And update the final return statement to include the new field:

```python
    return JobRenameResult(
        old_name=old_name,
        new_name=new_name,
        renamed_folder=renamed_folder,
        rewritten_files=tuple(rewritten_files),
        cache_refreshed=cache_refreshed,
        renamed_derived_files=renamed_derived_files,
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_job_rename_metadata.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/job_rename.py tests/test_job_rename_metadata.py
git commit -m "fix: rename orphaned derived files (DARK MODE, etc.) after a job rename"
```

---

## Task 3: Duplicate job-number detection module

**Files:**
- Create: `ready_jobs_watcher/duplicate_job_guard.py`
- Test: `tests/test_duplicate_job_guard.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_duplicate_job_guard.py`:

```python
from ready_jobs_watcher.duplicate_job_guard import (
    clear_duplicate_suspect_marker,
    find_job_number_collision,
    read_duplicate_suspect_marker,
    write_duplicate_suspect_marker,
)


def test_find_job_number_collision_detects_shared_number(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "502 - HARTFORD McCASLIN REFACE").mkdir(parents=True)
    (root / "649 - HARTFORD McCASLIN REFACE").mkdir(parents=True)

    collision = find_job_number_collision(str(root), "649 - HARTFORD McCASLIN REFACE", "649")

    assert collision is None  # only one folder has "649"


def test_find_job_number_collision_returns_other_folder_name(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "502 - HARTFORD McCASLIN REFACE").mkdir(parents=True)
    (root / "502 - HARTFORD MCCASLIN REFACE COPY").mkdir(parents=True)

    collision = find_job_number_collision(str(root), "502 - HARTFORD MCCASLIN REFACE COPY", "502")

    assert collision == "502 - HARTFORD McCASLIN REFACE"


def test_write_and_read_duplicate_suspect_marker(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    job.mkdir(parents=True)

    write_duplicate_suspect_marker(str(root), job.name, "502 - HARTFORD McCASLIN REFACE")
    marker = read_duplicate_suspect_marker(str(root), job.name)

    assert marker["suspectedDuplicateOf"] == "502 - HARTFORD McCASLIN REFACE"
    assert marker["reason"] == "job_number_collision"
    assert "detectedAt" in marker


def test_read_duplicate_suspect_marker_returns_none_when_absent(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "649 - HARTFORD McCASLIN REFACE").mkdir(parents=True)

    assert read_duplicate_suspect_marker(str(root), "649 - HARTFORD McCASLIN REFACE") is None


def test_clear_duplicate_suspect_marker_removes_file(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    job.mkdir(parents=True)
    write_duplicate_suspect_marker(str(root), job.name, "502 - HARTFORD McCASLIN REFACE")

    clear_duplicate_suspect_marker(str(root), job.name)

    assert read_duplicate_suspect_marker(str(root), job.name) is None
    # Clearing an already-absent marker must not raise.
    clear_duplicate_suspect_marker(str(root), job.name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_duplicate_job_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ready_jobs_watcher.duplicate_job_guard'`

- [ ] **Step 3: Create the module**

Create `ready_jobs_watcher/duplicate_job_guard.py`:

```python
"""
Detects and marks suspected duplicate job folders.

A duplicate can appear when a job folder is renamed while the shared Ready Jobs
root is being synced by Syncthing across multiple devices: a peer that hasn't
yet observed the rename can push its own stale copy of the old-named folder
back onto the share. Ready Jobs Watcher's normal "a new job folder appeared"
handling has no way to tell that apart from a genuinely new job, so instead of
silently adopting it as a live job (creating a deployment_gate.json and
prompting an operator), this module lets the caller mark it for manual review.

The marker file (duplicate_suspect.json) is intentionally separate from
deployment_gate.json - that file's schema is a stable contract with the
Android app and must not gain new fields for this.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Optional

from .atomic_write import atomic_write_json

main_logger = logging.getLogger("main")

DUPLICATE_SUSPECT_FILENAME = "duplicate_suspect.json"


def _marker_path(root_dir: str, job_folder_name: str) -> Path:
    return Path(root_dir) / job_folder_name / ".metadata" / DUPLICATE_SUSPECT_FILENAME


def find_job_number_collision(root_dir: str, job_folder_name: str, job_num: str) -> Optional[str]:
    """Return the name of another top-level folder sharing `job_num`, if any."""
    if not job_num:
        return None
    from .file_handler import JobProcessor

    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name == job_folder_name:
                    continue
                if JobProcessor.extract_job_number(entry.name) == job_num:
                    return entry.name
    except OSError as exc:
        main_logger.error("Failed scanning %s for job-number collisions: %s", root_dir, exc)
        return None
    return None


def write_duplicate_suspect_marker(root_dir: str, job_folder_name: str, collided_with: str) -> None:
    path = _marker_path(root_dir, job_folder_name)
    payload = {
        "schemaVersion": 1,
        "suspectedDuplicateOf": collided_with,
        "detectedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": "job_number_collision",
    }
    atomic_write_json(path, payload, indent=2, ensure_ascii=False)


def read_duplicate_suspect_marker(root_dir: str, job_folder_name: str) -> Optional[dict]:
    path = _marker_path(root_dir, job_folder_name)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception as exc:
        main_logger.error("Failed reading duplicate suspect marker %s: %s", path, exc)
        return None


def clear_duplicate_suspect_marker(root_dir: str, job_folder_name: str) -> None:
    path = _marker_path(root_dir, job_folder_name)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        main_logger.error("Failed clearing duplicate suspect marker %s: %s", path, exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_duplicate_job_guard.py -v`
Expected: all PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/duplicate_job_guard.py tests/test_duplicate_job_guard.py
git commit -m "feat: add duplicate job-number detection and marker module"
```

---

## Task 4: Wire the guard into on_new_job_folder_detected

**Files:**
- Modify: `ready_jobs_watcher/main.py:226-267` (`on_new_job_folder_detected`)
- Test: `tests/test_duplicate_job_guard.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_duplicate_job_guard.py`:

```python
import threading
import types

from ready_jobs_watcher.main import Application
from ready_jobs_watcher.deployment_gate import DeploymentGateManager


def _minimal_app(root):
    app = Application.__new__(Application)
    app.config = types.SimpleNamespace(ROOT_DIR=str(root))
    app.deployment_gate = DeploymentGateManager(str(root))
    app.settings_window = None
    app._pending_job_prompts = []
    app._pending_job_prompt_lock = threading.Lock()
    return app


def test_new_job_folder_with_colliding_job_number_is_quarantined(tmp_path):
    root = tmp_path / "Ready Jobs"
    existing = root / "502 - HARTFORD McCASLIN REFACE"
    duplicate = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    existing.mkdir(parents=True)
    duplicate.mkdir(parents=True)
    app = _minimal_app(root)

    app.on_new_job_folder_detected(str(duplicate))

    assert not (duplicate / ".metadata" / "deployment_gate.json").exists()
    marker = read_duplicate_suspect_marker(str(root), duplicate.name)
    assert marker is not None
    assert marker["suspectedDuplicateOf"] == existing.name
    assert app._pending_job_prompts == []


def test_new_job_folder_without_collision_is_adopted_normally(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "649 - HARTFORD McCASLIN REFACE"
    job.mkdir(parents=True)
    app = _minimal_app(root)

    app.on_new_job_folder_detected(str(job))

    assert (job / ".metadata" / "deployment_gate.json").exists()
    assert read_duplicate_suspect_marker(str(root), job.name) is None
    assert app._pending_job_prompts == [job.name]
```

(`read_duplicate_suspect_marker` is already imported at the top of this test file from Task 3.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_duplicate_job_guard.py -k quarantined -v`
Expected: FAIL — a `deployment_gate.json` gets created for the duplicate (current behavior has no guard)

- [ ] **Step 3: Add the guard to on_new_job_folder_detected**

In `ready_jobs_watcher/main.py`, add the import near the other local-module imports at the top
of the file (after the `.cabinet_sheet_indexer` import block, e.g. after line 43):

```python
from .duplicate_job_guard import find_job_number_collision, write_duplicate_suspect_marker
```

Then in `on_new_job_folder_detected` (main.py:226-244), change:

```python
    def on_new_job_folder_detected(self, folder_path: str):
        root_norm = os.path.normcase(os.path.normpath(self.config.ROOT_DIR))
        parent_norm = os.path.normcase(os.path.normpath(os.path.dirname(folder_path)))
        if parent_norm != root_norm or not JobProcessor.is_job_folder(folder_path):
            logging.debug("Ignoring pending gate creation for non-root/non-job folder: %s", folder_path)
            return
        job_folder_name = os.path.basename(os.path.normpath(folder_path))
        detected_mode = "UNKNOWN"
        detection_source = "UNKNOWN"
```

to:

```python
    def on_new_job_folder_detected(self, folder_path: str):
        root_norm = os.path.normcase(os.path.normpath(self.config.ROOT_DIR))
        parent_norm = os.path.normcase(os.path.normpath(os.path.dirname(folder_path)))
        if parent_norm != root_norm or not JobProcessor.is_job_folder(folder_path):
            logging.debug("Ignoring pending gate creation for non-root/non-job folder: %s", folder_path)
            return
        job_folder_name = os.path.basename(os.path.normpath(folder_path))

        job_num = JobProcessor.extract_job_number(job_folder_name) or ""
        collided_with = find_job_number_collision(self.config.ROOT_DIR, job_folder_name, job_num)
        if collided_with:
            write_duplicate_suspect_marker(self.config.ROOT_DIR, job_folder_name, collided_with)
            logging.warning(
                "Suspected duplicate job folder: %s shares job number %s with existing job %s; "
                "not adopting as a new job (see .metadata/duplicate_suspect.json).",
                job_folder_name,
                job_num,
                collided_with,
            )
            return

        detected_mode = "UNKNOWN"
        detection_source = "UNKNOWN"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_duplicate_job_guard.py -v`
Expected: all PASS (7 passed)

- [ ] **Step 5: Run the full test suite to confirm no regression**

Run: `python -m pytest tests/ -v`
Expected: all PASS except the 3 pre-existing `test_dae_converter.py` failures noted in
`CLAUDE.md` (environmental, need `mapbox_earcut`, unrelated to this change)

- [ ] **Step 6: Commit**

```bash
git add ready_jobs_watcher/main.py tests/test_duplicate_job_guard.py
git commit -m "fix: quarantine job folders that collide with an existing job number"
```

---

## Task 5: Surface duplicates in the Jobs dashboard

**Files:**
- Modify: `ready_jobs_watcher/main.py:351-352` (`get_jobs_dashboard_rows`)
- Modify: `ready_jobs_watcher/gui.py:799-838` (`_populate_jobs_table`)
- Test: `tests/test_duplicate_job_guard.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_duplicate_job_guard.py`:

```python
def test_get_jobs_dashboard_rows_tags_duplicate_suspects(tmp_path):
    root = tmp_path / "Ready Jobs"
    existing = root / "502 - HARTFORD McCASLIN REFACE"
    duplicate = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    existing.mkdir(parents=True)
    duplicate.mkdir(parents=True)
    app = _minimal_app(root)

    app.on_new_job_folder_detected(str(existing))
    app.on_new_job_folder_detected(str(duplicate))

    rows = {row["jobFolderName"]: row for row in app.get_jobs_dashboard_rows()}
    assert "duplicateSuspect" not in rows[existing.name]
    assert rows[duplicate.name]["duplicateSuspect"]["suspectedDuplicateOf"] == existing.name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_duplicate_job_guard.py -k dashboard_rows -v`
Expected: FAIL with `KeyError: 'duplicateSuspect'`

- [ ] **Step 3: Update get_jobs_dashboard_rows**

In `ready_jobs_watcher/main.py`, replace:

```python
    def get_jobs_dashboard_rows(self):
        return self.deployment_gate.list_job_states()
```

with:

```python
    def get_jobs_dashboard_rows(self):
        from .duplicate_job_guard import read_duplicate_suspect_marker

        rows = self.deployment_gate.list_job_states()
        for row in rows:
            job_folder_name = str(row.get("jobFolderName", "")).strip()
            if not job_folder_name:
                continue
            marker = read_duplicate_suspect_marker(self.config.ROOT_DIR, job_folder_name)
            if marker is not None:
                row["duplicateSuspect"] = marker
        return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_duplicate_job_guard.py -v`
Expected: all PASS (8 passed)

- [ ] **Step 5: Add the DUPLICATE style and rendering branch in gui.py**

In `ready_jobs_watcher/gui.py`, in `_populate_jobs_table` (gui.py:799-838), change:

```python
        state_styles = {
            "PENDING": (QColor("#FEF3C7"), QColor("#92400E")),
            "PARSING": (QColor("#DBEAFE"), QColor("#1E40AF")),
            "ACTIVE":  (QColor("#D1FAE5"), QColor("#065F46")),
        }
        hidden_style = (QColor("#E2E8F0"), QColor("#334155"))

        self.jobs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            timers = row.get("timers", {}) if isinstance(row.get("timers"), dict) else {}
            state_name = derive_state(row)
            is_hidden_from_production = bool(row.get("hiddenFromProduction", False))
            if is_hidden_from_production:
                bg, fg = hidden_style
                display_state = f"{state_name} (Hidden)"
            else:
                bg, fg = state_styles.get(state_name, (None, None))
                display_state = state_name
```

to:

```python
        state_styles = {
            "PENDING": (QColor("#FEF3C7"), QColor("#92400E")),
            "PARSING": (QColor("#DBEAFE"), QColor("#1E40AF")),
            "ACTIVE":  (QColor("#D1FAE5"), QColor("#065F46")),
            "DUPLICATE": (QColor("#FEE2E2"), QColor("#991B1B")),
        }
        hidden_style = (QColor("#E2E8F0"), QColor("#334155"))

        self.jobs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            timers = row.get("timers", {}) if isinstance(row.get("timers"), dict) else {}
            duplicate_marker = row.get("duplicateSuspect")
            if isinstance(duplicate_marker, dict):
                state_name = "DUPLICATE"
                collided_with = str(duplicate_marker.get("suspectedDuplicateOf") or "?")
                display_state = f"DUPLICATE (of {collided_with})"
                bg, fg = state_styles["DUPLICATE"]
            else:
                state_name = derive_state(row)
                is_hidden_from_production = bool(row.get("hiddenFromProduction", False))
                if is_hidden_from_production:
                    bg, fg = hidden_style
                    display_state = f"{state_name} (Hidden)"
                else:
                    bg, fg = state_styles.get(state_name, (None, None))
                    display_state = state_name
```

- [ ] **Step 6: Headless smoke check**

Run: `python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; import ready_jobs_watcher.gui"`
Expected: exits with no traceback (confirms the edited module still imports cleanly — gui.py is
not unit-tested per CLAUDE.md, this just catches syntax/import errors before manual verification)

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/main.py ready_jobs_watcher/gui.py tests/test_duplicate_job_guard.py
git commit -m "feat: surface suspected duplicate jobs in the Jobs dashboard table"
```

---

## Task 6: Resolve-duplicate dialog actions

**Files:**
- Modify: `ready_jobs_watcher/gui.py:855-859` (`_open_selected_job_dialog`)
- Modify: `ready_jobs_watcher/gui.py` (add new method `_show_duplicate_job_dialog`)

This task is GUI-only. Per `CLAUDE.md`, `gui.py` is not unit-tested — verify with the headless
import smoke check plus a manual walkthrough, not pytest.

- [ ] **Step 1: Route duplicate rows to a dedicated dialog**

In `ready_jobs_watcher/gui.py`, change `_open_selected_job_dialog` (gui.py:855-859):

```python
    def _open_selected_job_dialog(self, *args):
        job_folder_name = self._selected_job_folder_name()
        if not job_folder_name:
            return
        self._show_pending_job_prompt_dialog(job_folder_name)
```

to:

```python
    def _open_selected_job_dialog(self, *args):
        job_folder_name = self._selected_job_folder_name()
        if not job_folder_name:
            return
        from .duplicate_job_guard import read_duplicate_suspect_marker
        marker = read_duplicate_suspect_marker(self.config.ROOT_DIR, job_folder_name)
        if marker is not None:
            self._show_duplicate_job_dialog(job_folder_name, marker)
            return
        self._show_pending_job_prompt_dialog(job_folder_name)
```

- [ ] **Step 2: Add the new dialog method**

In `ready_jobs_watcher/gui.py`, add this new method directly above `_show_pending_job_prompt_dialog`:

```python
    def _show_duplicate_job_dialog(self, job_folder_name: str, marker: dict):
        collided_with = str(marker.get("suspectedDuplicateOf") or "unknown job")
        detected_at = str(marker.get("detectedAt") or "unknown time")

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Suspected Duplicate: {job_folder_name}")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"'{job_folder_name}' shares a job number with existing job '{collided_with}'.\n"
            f"Detected at {detected_at}.\n\n"
            "This usually happens when a stale copy of a renamed job's folder gets synced\n"
            "back from another device. It has not been adopted as a live job."
        ))

        action_row = QHBoxLayout()
        not_duplicate_btn = QPushButton("Not a duplicate — track normally")
        delete_btn = QPushButton("This is a duplicate — delete folder")
        cancel_btn = QPushButton("Cancel")

        def _track_normally():
            from .duplicate_job_guard import clear_duplicate_suspect_marker
            import os
            import threading
            clear_duplicate_suspect_marker(self.config.ROOT_DIR, job_folder_name)
            job_path = os.path.join(self.config.ROOT_DIR, job_folder_name)
            if self.app_instance and hasattr(self.app_instance, "on_new_job_folder_detected"):
                threading.Thread(
                    target=self.app_instance.on_new_job_folder_detected,
                    args=(job_path,),
                    daemon=True,
                ).start()
            self.refresh_jobs_dashboard()
            dialog.accept()

        def _delete_folder():
            reply = QMessageBox.question(
                dialog,
                "Delete Duplicate Folder",
                f"Permanently delete '{job_folder_name}' and everything in it?\n\n"
                "This cannot be undone. If another device still has an un-renamed copy, "
                "this folder may reappear and get flagged again.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            import os
            import shutil
            job_path = os.path.join(self.config.ROOT_DIR, job_folder_name)
            try:
                shutil.rmtree(job_path)
            except Exception as exc:
                QMessageBox.critical(dialog, "Delete Failed", f"Failed to delete folder:\n{exc}")
                return
            self.refresh_jobs_dashboard()
            dialog.accept()

        not_duplicate_btn.clicked.connect(_track_normally)
        delete_btn.clicked.connect(_delete_folder)
        cancel_btn.clicked.connect(dialog.reject)

        action_row.addWidget(not_duplicate_btn)
        action_row.addWidget(delete_btn)
        action_row.addStretch()
        action_row.addWidget(cancel_btn)
        layout.addLayout(action_row)

        dialog.exec()
```

- [ ] **Step 3: Headless smoke check**

Run: `python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; import ready_jobs_watcher.gui"`
Expected: exits with no traceback

- [ ] **Step 4: Manual walkthrough**

Run: `python -m ready_jobs_watcher` pointed at a scratch `ROOT_DIR` (not the live `Y:\Ready Jobs`
share). Create two sibling folders with the same leading job number (e.g. `999 - TEST A` and
`999 - TEST B`), let the watcher pick up the second one, and confirm in the Jobs tab: the second
folder shows as "DUPLICATE (of 999 - TEST A)" with the red style, double-clicking it opens the
new dialog, "Not a duplicate" clears the marker and the row becomes a normal PENDING job on
refresh, and "This is a duplicate" deletes the folder after confirmation.

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "feat: add resolve actions for suspected duplicate jobs"
```

---

## Self-Review Notes

- **Spec coverage:** Fix A (spec section "Fix A") is covered by Tasks 1-2. Fix B (spec section
  "Fix B") is covered by Tasks 3-6, including the dashboard-surfacing wrinkle the spec calls out
  and both resolve actions.
- **CLAUDE.md constraint:** `deployment_gate.json`'s schema is never modified by this plan — the
  new marker lives in a separate file (`duplicate_suspect.json`), and the guard's only effect on
  the gate file is *not creating one* for a suspected duplicate.
- **Type/name consistency checked:** `apply_job_number_prefix` (Task 1) is the one function name
  used in both `job_rename.py` and `main.py`'s delegating wrapper, and again in Task 2's
  `_rename_derived_files`. `duplicate_suspect.json` / `DUPLICATE_SUSPECT_FILENAME` /
  `find_job_number_collision` / `write_duplicate_suspect_marker` / `read_duplicate_suspect_marker`
  / `clear_duplicate_suspect_marker` are the same names across Tasks 3-6.
