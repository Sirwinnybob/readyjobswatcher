# Optimization & Bug Fix Loop Plan

> **For Claude:** This is a `/loop` prompt reference. Work through items in order. Each iteration: pick next unchecked item, implement, run `python -m pytest`, verify passing, commit, move on. Skip items that feel risky — flag them with `[SKIPPED: reason]` and continue.

**Goal:** Incrementally improve speed, reliability, and code quality across the Ready Jobs Watcher codebase. Build on existing patterns — no framework migrations, no architecture rewrites.

**Constraints:**
- Do NOT change `deployment_gate.json` schema (Android app reads it).
- Do NOT migrate to websockets, async frameworks, or new UI toolkits.
- Run `python -m pytest` after every change. 3 `test_dae_converter.py` tests fail without `mapbox_earcut` — those are environmental, not regressions.
- Each commit should be one logical change. If a change touches multiple files for one purpose, that's fine.
- If you encounter something that would be a high-impact optimization but requires significant migration (e.g., replacing watchdog, switching to async I/O), DON'T implement it. Instead, append a section to this file under "## Migration Proposals" with: what it is, why it matters, estimated effort, and risk.

---

## Worklist (ordered by impact)

### Bug Fixes (do these first)

- [x] **BUG: `deployment_gate.py` `_atomic_write_json` is not atomic.** Lines 61-63 use `os.remove()` + `os.rename()`. A crash between those two calls loses the file. Fix: use `os.replace()` like `metadata_cache.py` already does. Remove the `if os.path.exists(path): os.remove(path)` guard and just call `os.replace(tmp_path, path)`.

- [x] **BUG: `gui.py` `render_pdf_page_to_pixmap` leaks fitz Document.** Line 49 opens `fitz.open(pdf_full_path)` but the `doc` object is never closed in the normal success path (only the `except` branch returns early). Wrap in try/finally or use a context manager (`with fitz.open(...) as doc:`).

- [x] **BUG: Config `load()` never reads `cnc_scan_times` from JSON.** The `CNC_SCAN_TIMES` dict at config.py:46 is hardcoded and neither the happy path (lines 218-253) nor the backup restore path (lines 266-301) applies it from the saved config. If this field is supposed to be configurable, add `self.CNC_SCAN_TIMES = config.get('cnc_scan_times', self.CNC_SCAN_TIMES)` and the corresponding entry in `save()`. If it's intentionally hardcoded, add a comment explaining why.

### Performance (high impact)

- [x] **PERF: `get_jobs_dashboard_rows()` runs `_backfill_modes_for_existing_jobs()` on every call.** This scans ALL jobs, reads each deployment gate JSON, and runs mode detection for UNKNOWN jobs. It's called on every dashboard refresh AND by `_get_job_row_by_name()` (gui.py:791) which just wants one row. Fix approach:
  1. Run `_backfill_modes_for_existing_jobs()` once at startup and once after each deploy/re-parse — not on every dashboard query.
  2. Make `_get_job_row_by_name()` use `deployment_gate.load_state(job_folder_name)` directly instead of calling `get_jobs_dashboard_rows()` to scan all jobs for one name.

- [x] **PERF: `initial_scan()` processes jobs serially** despite having `self.executor` (ThreadPoolExecutor with 20 workers). The `build_reference_index_for_job`, `build_hardwoods_cutlist_index_for_job`, and `convert_3d_models_for_job` calls at main.py:1185-1195 could be submitted to the executor. Be careful: each job's index builds are independent, but don't overwhelm the network share. Consider a small batch approach (e.g., submit 4-5 at a time).

- [x] **PERF: `_run_cabinet_index_startup_check()` walks entire job trees** via `os.walk` on a network share (main.py:1104) just to compare PDF mtimes against the index mtime. Optimization: use `os.scandir` instead of `os.walk` and stop at the first stale PDF found (`break` already exists but the `os.walk` is the bottleneck). Since CNC PDFs are the primary staleness trigger and they live in `<job>/CNC/`, check that directory first before walking the entire tree.

### Code Quality / Deduplication

- [x] **DEDUP: Config `load()` field-apply logic duplicated.** Lines 218-253 and 266-301 in config.py are near-identical. Extract a `_apply_config_dict(self, config)` method and call it from both paths.

- [x] **DEDUP: Debounce timer pattern repeated 5+ times.** watchers.py has `_tracker_scan_timer`, `_index_reparse_timers`, `_dae_reparse_timers`; metadata_refresh.py has `DebouncedMetadataRefreshScheduler`; main.py has `_pending_job_timers`. All do the same cancel-replace-start pattern. Extract a small `DebouncedTimer` utility class (single-key variant) and `DebouncedTimerMap` (multi-key variant). Keep it simple — just the cancel-replace pattern with a lock.

- [x] **DEDUP: `_is_root_available()` duplicated** in `Application` (main.py:879) and `PdfChangeHandler` (watchers.py:401). Move to a shared utility or pass a callable.

- [x] **DEDUP: `_resolve_job_folder_for_pdf` logic duplicated.** `PdfChangeHandler._resolve_job_folder_for_pdf` (watchers.py:430) and the inline check in `_run_index_refresh` (watchers.py:502-506) do the same DARK MODE parent resolution. Use the static method in both places.

### Minor Performance

- [x] **PERF: `_conversion_cooldown` dict grows unbounded.** PdfChangeHandler's cooldown dict (watchers.py:384) cleaned only every 100 conversions. On a busy network share, could accumulate thousands of entries. Change to a time-based cleanup (e.g., prune entries older than `2 * _cooldown_seconds` on each access, or use a bounded LRU-style approach).

- [x] **PERF: `has_3d_assets()` in metadata_cache.py does two-level glob.** Called during cache generation for every job. Add an early return after finding the first match (currently iterates all). Also consider caching per-session if called multiple times for the same job.

- [x] **PERF: Multiple `os.path.exists` / `os.path.isdir` calls on same path.** Audited all 91 occurrences across 19 files. Converted hot-path check-then-act patterns to EAFP across pending_queue, main, bad_parts_checker, tracker_bad_parts, hardwoods_cutlist_indexer, file_handler, gui, deployment_gate, utils, remake_candidates_indexer, tracker_action_stream, pdf_dark_mode, and watchers. Also fixed 4 additional non-atomic `os.remove + os.rename` races (pending_queue, both bad_parts blacklists, tracker_bad_parts state). Left LBYL guards at decision-branch points and existing test-mock contracts in place. 91 occurrences across 19 files. Many are guarding the same path multiple times (e.g., check exists, then open, then check again). Audit the hot paths and consolidate where possible — use try/except instead of check-then-act for filesystem operations over the network share (LBYL → EAFP).

---

## Migration Proposals

> Items below are NOT in the loop worklist. They're flagged as potentially high-impact but requiring significant effort or risk. Present these to the user for decision.

*(To be filled by the loop agent if it encounters migration-worthy patterns)*

---

## Notes for the Loop Agent

- This app monitors a network share (`\\192.168.1.15\KKC Jobs\Ready Jobs`). Network I/O is the primary bottleneck — filesystem stat calls, JSON reads, and PDF opens all go over SMB.
- The app runs 24/7 with a daily 3 AM restart. Memory leaks matter.
- `gui.py` (1370 lines) is the largest file but GUI code is not unit-tested. Changes there need manual verification via `QT_QPA_PLATFORM=offscreen` import smoke check.
- `deployment_gate.json` schema is sacred — Android app depends on it.
- The `ThreadPoolExecutor` has 20 workers. Don't create unbounded threads.
- Existing test suite has 28 test files. Run full suite after each change.
