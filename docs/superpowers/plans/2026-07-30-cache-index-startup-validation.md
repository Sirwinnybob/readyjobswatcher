# Cache Index Startup Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate `cache_index.json` exists and is well-formed per-job during `update_all_jobs_cache()`, regenerating from `cache_static.json` when missing or corrupt.

**Architecture:** Hook `_validate_cache_index()` check into `update_all_jobs_cache()`. When `cache_static.json` is fresh but `cache_index.json` is bad, read `cache_static.json` from disk and regenerate. Reuses existing `generate_cache_index()` and `_read_json()`.

**Tech Stack:** Python, json, pathlib, existing `metadata_cache.py` patterns.

## Global Constraints

- No new dependencies. Use only stdlib + existing imports.
- `update_all_jobs_cache()` signature unchanged.
- Logging via `logging.getLogger(__name__)` — existing pattern.
- Tests in `tests/test_cache_index.py` matching existing patterns.

---

### Task 1: `_validate_cache_index()` function

**Files:**
- Modify: `ready_jobs_watcher/metadata_cache.py` (add function after `generate_cache_index()`)
- Test: `tests/test_cache_index.py`

**Interfaces:**
- Consumes: `Path` (job_folder), `_read_json()` (existing line 33), `logger` (existing)
- Produces: `_validate_cache_index(job_folder: Path) -> bool` — returns True if file exists and has all required fields

Validation rules:
- File `.metadata/cache_index.json` must exist
- Must parse as `dict`
- Must have keys: `jobInfo` (dict), `progressSummary` (dict)
- `jobInfo.folderName` must be non-empty string
- `progressSummary` must have keys: `cnc`, `hardwoods`, `hasDeliverySheet`, `has3DAssets`

- [ ] **Step 1: Write tests for `_validate_cache_index()`**

Add after the last test in `tests/test_cache_index.py`:

```python
from ready_jobs_watcher.metadata_cache import (
    _validate_cache_index,  # add to existing import line
)


def test_validate_cache_index_missing_file(tmp_path):
    """Missing cache_index.json returns False."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    assert _validate_cache_index(job) is False


def test_validate_cache_index_malformed_json(tmp_path):
    """Malformed cache_index.json returns False."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / ".metadata" / "cache_index.json").write_text("not json", encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_empty_json(tmp_path):
    """Empty JSON object returns False."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / ".metadata" / "cache_index.json").write_text("{}", encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_missing_progress(tmp_path):
    """Missing progressSummary returns False."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {"jobInfo": {"folderName": "123 - Test"}}
    (job / ".metadata" / "cache_index.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    assert _validate_cache_index(job) is False


def test_validate_cache_index_missing_jobInfo(tmp_path):
    """Missing jobInfo returns False."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {"progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False}}
    (job / ".metadata" / "cache_index.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    assert _validate_cache_index(job) is False


def test_validate_cache_index_null_folderName(tmp_path):
    """Null folderName returns False."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {"jobInfo": {"folderName": None}, "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False}}
    (job / ".metadata" / "cache_index.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    assert _validate_cache_index(job) is False


def test_validate_cache_index_valid(tmp_path):
    """Valid cache_index.json returns True."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": {"folderName": "123 - Test"},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    assert _validate_cache_index(job) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
python -m pytest tests/test_cache_index.py::test_validate_cache_index_missing_file tests/test_cache_index.py::test_validate_cache_index_malformed_json tests/test_cache_index.py::test_validate_cache_index_empty_json tests/test_cache_index.py::test_validate_cache_index_missing_progress tests/test_cache_index.py::test_validate_cache_index_missing_jobInfo tests/test_cache_index.py::test_validate_cache_index_null_folderName tests/test_cache_index.py::test_validate_cache_index_valid -v
```

Expected: 7 FAILED (ImportError: cannot import name `_validate_cache_index`)

- [ ] **Step 3: Implement `_validate_cache_index()`**

Add to `metadata_cache.py` after line 427 (after `generate_cache_index()`):

```python
_REQUIRED_PROGRESS_KEYS = frozenset({"cnc", "hardwoods", "hasDeliverySheet", "has3DAssets"})


def _validate_cache_index(job_folder: Path) -> bool:
    index_path = job_folder / ".metadata" / "cache_index.json"
    if not index_path.exists():
        logging.getLogger(__name__).warning("cache_index.json missing for %s", job_folder.name)
        return False
    data = _read_json(index_path)
    if not isinstance(data, dict):
        logging.getLogger(__name__).warning("cache_index.json malformed for %s", job_folder.name)
        return False
    job_info = data.get("jobInfo")
    progress = data.get("progressSummary")
    missing = []
    if not isinstance(job_info, dict):
        missing.append("jobInfo")
    else:
        folder_name = job_info.get("folderName")
        if not isinstance(folder_name, str) or not folder_name.strip():
            missing.append("jobInfo.folderName")
    if not isinstance(progress, dict):
        missing.append("progressSummary")
    elif not _REQUIRED_PROGRESS_KEYS.issubset(progress.keys()):
        missing.append(f"progressSummary.{_REQUIRED_PROGRESS_KEYS - progress.keys()}")
    if missing:
        logging.getLogger(__name__).warning(
            "cache_index.json incomplete for %s: missing %s", job_folder.name, ", ".join(missing)
        )
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
python -m pytest tests/test_cache_index.py::test_validate_cache_index_missing_file tests/test_cache_index.py::test_validate_cache_index_malformed_json tests/test_cache_index.py::test_validate_cache_index_empty_json tests/test_cache_index.py::test_validate_cache_index_missing_progress tests/test_cache_index.py::test_validate_cache_index_missing_jobInfo tests/test_cache_index.py::test_validate_cache_index_null_folderName tests/test_cache_index.py::test_validate_cache_index_valid -v
```

Expected: 7 PASSED

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
python -m pytest
```

Expected: All existing tests still pass (known 3 failures in test_dae_converter.py are pre-existing).

- [ ] **Step 6: Commit**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
git add ready_jobs_watcher/metadata_cache.py tests/test_cache_index.py
git commit -m "feat: add _validate_cache_index() function"
```

---

### Task 2: Hook validation into `update_all_jobs_cache()`

**Files:**
- Modify: `ready_jobs_watcher/metadata_cache.py:1196-1203` (extend the rebuild block)
- Test: `tests/test_cache_index.py` (add integration test)

**Interfaces:**
- Consumes: `_validate_cache_index()` (Task 1), `_read_json()` (existing), `generate_cache_index()` (existing), `update_all_jobs_cache()` (existing)
- Produces: Extended `update_all_jobs_cache()` that regenerates `cache_index.json` when validation fails

- [ ] **Step 1: Write integration test**

Add to `tests/test_cache_index.py`:

```python
def test_update_all_jobs_cache_fixes_bad_index(tmp_path):
    """When cache_static is fresh but cache_index is missing, update regenerates it."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / "CNC").mkdir(parents=True)
    gate = {"deployed": True, "parseReady": True, "hiddenFromProduction": False}
    (job / ".metadata" / "deployment_gate.json").write_text(json.dumps(gate), encoding="utf-8")
    static_data = {
        "jobInfo": {"folderName": "123 - Test"},
        "cncJob": {"materials": [{"materialName": "FRAME", "pageCount": 2, "pdfFilename": "f.pdf"}]},
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    (job / ".metadata" / "cache_static.json").write_text(
        json.dumps(static_data), encoding="utf-8"
    )
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "c", "actions": []}), encoding="utf-8"
    )
    # cache_index.json does NOT exist yet
    result = update_all_jobs_cache(tmp_path, consolidate_trackers=False, archive=False)
    assert (job / ".metadata" / "cache_index.json").exists()
    assert result["rebuilt"] >= 1
    index_data = json.loads((job / ".metadata" / "cache_index.json").read_text(encoding="utf-8"))
    assert index_data["jobInfo"]["folderName"] == "123 - Test"


def test_update_all_jobs_cache_fixes_corrupt_index(tmp_path):
    """When cache_index is corrupt but cache_static is fresh, update regenerates it."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / "CNC").mkdir(parents=True)
    gate = {"deployed": True, "parseReady": True, "hiddenFromProduction": False}
    (job / ".metadata" / "deployment_gate.json").write_text(json.dumps(gate), encoding="utf-8")
    static_data = {
        "jobInfo": {"folderName": "123 - Test"},
        "cncJob": {"materials": [{"materialName": "FRAME", "pageCount": 2, "pdfFilename": "f.pdf"}]},
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    (job / ".metadata" / "cache_static.json").write_text(
        json.dumps(static_data), encoding="utf-8"
    )
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "c", "actions": []}), encoding="utf-8"
    )
    (job / ".metadata" / "cache_index.json").write_text("corrupt", encoding="utf-8")
    result = update_all_jobs_cache(tmp_path, consolidate_trackers=False, archive=False)
    assert result["rebuilt"] >= 1
    index_data = json.loads((job / ".metadata" / "cache_index.json").read_text(encoding="utf-8"))
    assert index_data["jobInfo"]["folderName"] == "123 - Test"


def test_update_all_jobs_cache_skips_valid_index(tmp_path):
    """When cache_index is valid and cache_static is fresh, no rebuild."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / "CNC").mkdir(parents=True)
    gate = {"deployed": True, "parseReady": True, "hiddenFromProduction": False}
    (job / ".metadata" / "deployment_gate.json").write_text(json.dumps(gate), encoding="utf-8")
    static_data = {
        "jobInfo": {"folderName": "123 - Test"},
        "cncJob": {"materials": [{"materialName": "FRAME", "pageCount": 2, "pdfFilename": "f.pdf"}]},
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    (job / ".metadata" / "cache_static.json").write_text(
        json.dumps(static_data), encoding="utf-8"
    )
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "c", "actions": []}), encoding="utf-8"
    )
    valid_index = {
        "jobInfo": {"folderName": "123 - Test"},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(
        json.dumps(valid_index), encoding="utf-8"
    )
    result = update_all_jobs_cache(tmp_path, consolidate_trackers=False, archive=False)
    assert result["rebuilt"] == 0
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
python -m pytest tests/test_cache_index.py::test_update_all_jobs_cache_fixes_bad_index tests/test_cache_index.py::test_update_all_jobs_cache_fixes_corrupt_index tests/test_cache_index.py::test_update_all_jobs_cache_skips_valid_index -v
```

Expected: 2 FAILED (the fix_bad_index and fix_corrupt_index ones), 1 PASSED (skips_valid_index — because `update_all_jobs_cache` already returns `rebuilt: 0` when cache_static is fresh, even though the validation hook isn't there yet)

Wait — the skips_valid_index test expects `rebuilt == 0`. With current code, `rebuilt` will indeed be 0 because cache_static.json exists and is fresh. So this test will PASS even before the change. That's correct — it's testing the current behavior plus the new validation.

The fix_bad_index and fix_corrupt_index tests will FAIL because `rebuilt` will be 0 (current code doesn't check cache_index.json validity).

- [ ] **Step 3: Modify `update_all_jobs_cache()` to add validation hook**

Replace lines 1196-1203 in `metadata_cache.py`:

Current:
```python
            cache_path = job_folder / ".metadata" / "cache_static.json"
            needs_rebuild = force_rebuild or not cache_path.exists()
            if not needs_rebuild:
                needs_rebuild = check_cache_needs_rebuild(job_folder, cache_path.stat().st_mtime)
            if needs_rebuild:
                static_data = generate_static_cache(job_folder, folder_name, lineup_positions.get(folder_name))
                generate_cache_index(job_folder, static_data)
                summary["rebuilt"] += 1
```

New:
```python
            cache_path = job_folder / ".metadata" / "cache_static.json"
            needs_rebuild = force_rebuild or not cache_path.exists()
            if not needs_rebuild:
                needs_rebuild = check_cache_needs_rebuild(job_folder, cache_path.stat().st_mtime)
            if needs_rebuild:
                static_data = generate_static_cache(job_folder, folder_name, lineup_positions.get(folder_name))
                generate_cache_index(job_folder, static_data)
                summary["rebuilt"] += 1
            elif not _validate_cache_index(job_folder):
                static_data = _read_json(cache_path)
                if static_data:
                    generate_cache_index(job_folder, static_data)
                    summary["rebuilt"] += 1
                    logging.getLogger(__name__).info("Regenerated cache_index.json for %s", folder_name)
                else:
                    logging.getLogger(__name__).warning(
                        "Cannot regenerate cache_index.json for %s: cache_static.json missing or corrupt",
                        folder_name,
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
python -m pytest tests/test_cache_index.py -v
```

Expected: All existing tests pass + 3 new tests pass (known 3 failures in test_dae_converter.py are pre-existing).

- [ ] **Step 5: Run full test suite**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
python -m pytest
```

Expected: Same pass/fail as before (only pre-existing dae_converter failures).

- [ ] **Step 6: Commit**

```bash
cd "C:\Scripts\Ready Jobs Watcher"
git add ready_jobs_watcher/metadata_cache.py tests/test_cache_index.py
git commit -m "feat: validate and repair cache_index.json in update_all_jobs_cache()"
```
