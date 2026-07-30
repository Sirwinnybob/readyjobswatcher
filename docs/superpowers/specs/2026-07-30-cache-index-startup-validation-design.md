# cache_index.json Startup Validation

## Problem

`update_all_jobs_cache()` only regenerates `cache_index.json` when `cache_static.json` needs rebuilding. If `cache_index.json` is missing, corrupt, or stale while `cache_static.json` is fresh, it never gets fixed. This leaves jobs with missing/incomplete cache data until the next forced rebuild.

## Design

Hook cache_index.json validation into `update_all_jobs_cache()` in `metadata_cache.py`.

### Validation logic (after existing `needs_rebuild` block)

```python
# existing rebuild block
if needs_rebuild:
    static_data = generate_static_cache(...)
    generate_cache_index(job_folder, static_data)
    summary["rebuilt"] += 1
# NEW: validate cache_index.json when cache_static is fresh
elif _validate_cache_index(job_folder) is False:
    static_data = _read_json(cache_path)  # cache_static.json
    if static_data:
        generate_cache_index(job_folder, static_data)
        summary["rebuilt"] += 1
    else:
        logger.warning(...)
```

### Validation function

`_validate_cache_index(job_folder: Path) -> bool`:

- File `.metadata/cache_index.json` must exist
- Must parse as a dict
- Must have top-level keys: `jobInfo` (dict), `progressSummary` (dict)
- `jobInfo.folderName` must be non-empty string
- `progressSummary` must have keys: `cnc`, `hardwoods`, `hasDeliverySheet`, `has3DAssets`

Returns `False` with logger warning on any failure. Log includes specific missing fields.

### Logging

- `"cache_index.json missing for {name}"` — file absent
- `"cache_index.json incomplete for {name}: missing {fields}"` — corrupt/incomplete
- `"Regenerated cache_index.json for {name}"` — after regeneration

### Edge cases

- `cache_static.json` missing/corrupt → falls through to `needs_rebuild` block (existing)
- Job not deployed → skipped (existing)
- `_read_json(cache_path)` returns None/empty → log error, skip regeneration
- Both `cache_static.json` and `cache_index.json` missing → `needs_rebuild` handles it, `elif` never reached

## Files changed

- `ready_jobs_watcher/metadata_cache.py` — add `_validate_cache_index()`, extend `update_all_jobs_cache()`
- `tests/test_cache_index.py` — tests for validation function
