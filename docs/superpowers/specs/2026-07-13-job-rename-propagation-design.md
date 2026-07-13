# Job Rename Propagation & Duplicate-Job Guard

## Background

Renaming a job (e.g. `502 - HARTFORD McCASLIN REFACE` -> `649 - HARTFORD McCASLIN REFACE`) is
supposed to propagate everywhere: folder name, JSON metadata content, and every on-disk file that
carries the old job number in its filename. In practice two separate bugs were found on
2026-07-13 while diagnosing a live rename that appeared to "do nothing":

1. **Derived files aren't renamed.** `rename_ready_job()` in `job_rename.py` renames the job
   folder and rewrites JSON *content* under `.metadata` and `CNC\.metadata`, but never renames
   on-disk filenames. Root/CNC PDFs happen to get corrected anyway via the normal
   `job_processor.process_job_folder()` prefix-fixing pass that runs right after a rename, but
   generated/derived files that pass doesn't touch (confirmed: `DARK MODE\502 - ASSEMBLY
   SHEETS.pdf` etc. survived a rename to `649` untouched) are never renamed or cleaned up.

2. **A rename can spawn an untracked duplicate job.** `Y:\Ready Jobs` is Syncthing-synced across
   multiple devices. When a job is renamed locally, another peer that hasn't yet observed the
   rename can push its stale copy of the old-named folder back onto the share. Windows/SMB
   reports this as a move event with a synthetic source path
   (`.::TMPNAME:D:<serial>%<fileid>:<name>`) that isn't a resolvable filesystem path. The rename
   heuristic in `RenameHandler.on_moved` (`watchers.py`) requires `old_folder_name != folder_name`
   and matching parent directories under `ROOT_DIR`; a synthetic source path fails that check, so
   the event is treated as "a new job folder appeared" instead of "this is the same rename,"
   and RJW starts tracking a fully independent ghost job under the old number. That ghost then
   accumulated real admin data (specialty items added by mistake against it) before it was
   noticed.

This spec covers two independent fixes. They can be implemented and tested separately.

## Fix A: Rename derived files in place

### Components

- `ready_jobs_watcher/job_rename.py` — `rename_ready_job()` gains a final step after the existing
  folder-rename / JSON-rewrite / gate-normalize / cache-refresh steps.
- A shared filename-prefix helper, extracted from `main.py`'s existing
  `JobProcessor._target_filename_for_job` (currently: replace a leading `"<num> - "` with the new
  number, or prepend `"<new_num> - "` if there's no `" - "` separator). This gets moved to a
  single shared location (`job_rename.py`, imported by `main.py`) so the two rename code paths
  can't drift into two different filename conventions.

### Data flow

1. After the JSON-content rewrite and cache refresh already in `rename_ready_job()`, walk `new_path`
   recursively with `os.walk` (or `Path.rglob("*")`), skipping `CNC\.tracker` and
   `.metadata\hardwoods\.tracker` (tablet-authored, named by tablet ID — never matches a job-number
   prefix, excluded as an explicit safety fence rather than relying on that).
2. For each file whose basename starts with `f"{old_num} - "`, compute the renamed basename via
   the shared helper and `Path.rename()` it in place, within the same parent directory.
3. Skip files that already start with `f"{new_num} - "` (idempotent — a second rename attempt or
   partial prior success won't double-rename or error).
4. Collect every path successfully renamed into a new field on the result.

### Error handling

Each individual rename is wrapped in its own `try/except`; a locked or in-use file (e.g. open on
a tablet) is logged as a warning and skipped, it does not abort the rest of the walk or the
overall rename call — consistent with how `main.py:rename_job` already treats each of its
sub-steps (pending queue, tracker monitor, blacklist, `rename_ready_job` itself) as independent
and non-fatal.

### Data returned

`JobRenameResult` gains a new field:

```python
renamed_derived_files: tuple[Path, ...]
```

populated with every file path that was actually renamed by this new step (separate from
`rewritten_files`, which tracks JSON content rewrites).

### Testing

Extend `tests/test_job_rename_metadata.py`: create `DARK MODE\123 - Something.pdf` before calling
`rename_ready_job`, assert it becomes `DARK MODE\456 - Something.pdf` after, and assert it appears
in `result.renamed_derived_files`. Add a case for a file already correctly prefixed (no rename,
no error).

## Fix B: Duplicate job-number guard

### Components

- `main.py`'s `on_new_job_folder_detected()` — single choke point for all "a job folder appeared"
  events (`watchers.py`'s `on_created`, both branches of `on_moved`, and the startup initial-scan
  path all call into this one method already).
- A new per-job marker file: `<job>\.metadata\duplicate_suspect.json` — new file, does not modify
  `deployment_gate.json`'s schema (per the CLAUDE.md rule to keep that schema's field names/types
  stable).
- `main.py:get_jobs_dashboard_rows()` and `gui.py:_populate_jobs_table()` — surfaces the
  suspected-duplicate state in the existing Jobs table rather than a new UI surface.

### Data flow

1. At the top of `on_new_job_folder_detected(folder_path)`, before any mode detection or gate
   creation: extract the new folder's job number via `JobProcessor.extract_job_number`.
2. Scan sibling top-level folders directly under `ROOT_DIR` (`os.scandir`, matching the same scope
   `list_job_states()` already uses) for any *other* folder whose extracted job number is
   identical to the new folder's.
3. If a collision is found:
   - Write `duplicate_suspect.json` under the new folder's `.metadata`:
     ```json
     {
       "schemaVersion": 1,
       "suspectedDuplicateOf": "<other job folder name>",
       "detectedAt": "<iso timestamp>",
       "reason": "job_number_collision"
     }
     ```
     via the same atomic-write helper (`atomic_write.py:atomic_write_json`) everything else in
     this codebase uses.
   - Log a `WARNING` with both folder names and the shared job number.
   - Return immediately — skip `ensure_pending_for_new_job`, mode detection, template-mismatch
     check, and the pending-job GUI prompt. No `deployment_gate.json` gets created for it.
4. No collision: existing behavior, unchanged.

### Dashboard surfacing

`DeploymentGateManager.list_job_states()` already scans every top-level folder in `ROOT_DIR`
(not just ones with a gate file) and returns a synthetic default "PENDING" state for folders with
no gate file yet. That means a quarantined folder appears in the Jobs table regardless — the fix
doesn't hide it, it makes sure it doesn't look like an ordinary new job:

- `get_jobs_dashboard_rows()` checks for `duplicate_suspect.json` per row's `jobFolderName` and,
  if present, adds an in-memory `row["duplicateSuspect"] = {...}` key (this is a transient dict
  used only for rendering — it is not written back to `deployment_gate.json`).
- `_populate_jobs_table()` checks for that key first, before falling back to `derive_state()`, and
  renders the row with a new "DUPLICATE" entry in the existing `state_styles` color map instead of
  "PENDING."

### Resolving a flagged duplicate

Double-clicking a DUPLICATE row (reusing the existing state-aware per-job dialog) offers two
actions:

- **"Not a duplicate — track normally"**: delete `duplicate_suspect.json`, then run the same
  adoption flow `on_new_job_folder_detected` would have run (mode detection + `ensure_pending_for_new_job`).
- **"This is a duplicate — delete this folder"**: recursive delete of the folder. Logged with a
  caveat (learned firsthand on 2026-07-13): if another Syncthing peer still holds its own copy of
  the old-named folder, it can reappear — in which case it will simply get quarantined again by
  this same guard, rather than silently re-adopted as a live job.

### Error handling

The job-number collision scan and marker write are wrapped the same way the rest of
`on_new_job_folder_detected` already is (the method's existing broad exception handling at its
call sites in `watchers.py` covers this). A failure to write the marker file falls back to
logging an error and proceeding with normal adoption — a failed safety check should not be worse
than no safety check.

### Testing

New `tests/test_duplicate_job_guard.py`:

1. Two folders with the same job number, different names -> `on_new_job_folder_detected` on the
   second one writes `duplicate_suspect.json`, does not create `deployment_gate.json`, does not
   queue a pending-job prompt.
2. No collision -> unchanged current behavior (gate created, prompt queued).
3. `get_jobs_dashboard_rows()` includes the `duplicateSuspect` tag for a folder with the marker
   file present.
