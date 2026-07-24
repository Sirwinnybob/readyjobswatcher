# Recover CNC/Hardwood Tracker Actions From Archived Sync Conflicts

## Background

`sync_conflict_resolver.py` archives a genuinely divergent Syncthing conflict copy into
`<job>\.metadata\sync_conflicts\<archive_id>\` and writes a `manifest.json` recording the action
taken. It never overwrites the live original, and never merges the loser's content back in
(`resolve_sync_conflict_file` docstring: "The resolver never overwrites an existing original with
conflicting bytes"). That's correct for most conflicting files, but for one specific shape it
throws away real, unrecoverable data.

`CNC\.tracker\<tabletId>.json` and `.metadata\hardwoods\.tracker\<tabletId>.json` are the live
per-tablet action-log files KKCSheetTracker writes (`{"tabletId": ..., "actions": [...]}` — a list
of `view`/`complete`/`bad_part`/etc. events, each with its own timestamp). Two archived conflicts
found on the live share (`332 - ROSS KAUFMAN HAMMOND` and `582 - DC WILLIAMS`, both
`SM-X808U-5254.json`, `action: archived_divergent`) show exactly this: two tablet write sessions
diverged, the loser was shelved whole, and its `view`/`complete` entries for that tablet session
are gone from consolidation forever. `metadata_cache.py`'s own H-06 comment already documents this
as a known, unresolved gap: "Syncthing then quarantines the loser as a `.sync-conflict` copy that
`sync_conflict_resolver` only archives, never merges back in."

A survey of every other archived conflict on the share found no other lossy pattern:
- Two `.syncthing.*.json.tmp` entries are Syncthing's own internal bookkeeping, already excluded
  going forward by the existing `is_transient_conflict_path` fix (predates it).
- Seventeen entries under one job (`659 - WIECHERT...`), all `CNC\.tracker\events\<tablet>.ndjson`,
  are `archived_duplicate` (byte-identical content) — the resolver already keeps the original
  untouched in that case, so nothing is lost; just write-race noise.

Separately, `METADATA_AUDIT.md` issue C-01 ("consolidation drops `bad_part_submitted` before
deleting the source file") is referenced as an open problem in `kkc-metadata-map/SKILL.md`, but
`metadata_cache.py:589-594`'s comment and code show it was already fixed: `_merge_cnc_actions` now
re-emits both `bad_part` and `bad_part_submitted` with their original timestamps before the legacy
device file is deleted. That skill doc line is stale and gets corrected as part of this change; no
behavior fix is needed for C-01 itself.

## Fix: fold archived divergent tracker conflicts into the shared action reader

### Components

- `ready_jobs_watcher/tracker_action_stream.py` — `_load_tracker_actions` (the function shared by
  `load_cnc_tracker_actions` and `load_hardwoods_tracker_actions`) gains a step that looks for
  already-archived divergent conflicts belonging to the tracker directory being read, and folds
  their recorded actions into the same combined list `_load_legacy_json_actions` already returns.
  Every consumer of these two functions — `tracker_bad_parts.py`'s `TrackerBadPartsMonitor`,
  `metadata_cache.py`'s `consolidate_cnc_tracker`/`consolidate_hardwoods_tracker`, and
  `remake_candidates_indexer.py` — gets recovered actions automatically, with no changes to any of
  those three files. Because `_consolidate_tracker` calls this reader on every normal
  debounce/poll consolidation pass, recovered actions ride the same cache-update cycle as
  everything else; there is no separate or delayed recovery path.
- `ready_jobs_watcher/sync_conflict_resolver.py` — unchanged. The "never overwrite original bytes"
  invariant stays true; this fix only widens what consolidation *reads*, not what the resolver
  writes.
- `.claude/skills/kkc-metadata-map/SKILL.md` (canonical copy; mirrors sync automatically) —
  correct the stale C-01 "Common Mistake" line and add a row/note for the new recovery behavior.

### Data flow

1. Given a `tracker_dir` (`<job>\CNC\.tracker` or `<job>\.metadata\hardwoods\.tracker`), derive the
   job folder by walking up parents until a directory whose name matches the job-folder pattern is
   found (reuse the existing job-folder-name parsing already used elsewhere, rather than a fixed
   parent-count, so this doesn't silently break if either tracker path ever gains or loses a level).
2. Glob `<job folder>\.metadata\sync_conflicts\**\manifest.json`. This directory is a shared bucket
   for every conflict type in the job (root-level conflicts even mix in unrelated files like
   `delivery_schedule_request`), so every manifest must be filtered, not trusted blindly.
3. Fold a manifest's archived file only when **all** of the following hold:
   - `manifest["action"] == "archived_divergent"`.
   - `Path(manifest["originalPath"])`'s last two path parts case-insensitively equal `tracker_dir`'s
     own last two parts (`("CNC", ".tracker")` or `("hardwoods", ".tracker")` under `.metadata`).
     Comparing only the trailing parts (not the full path) tolerates the UNC-vs-mapped-drive-letter
     spelling difference already observed between archived manifests (`\\192.168.1.15\KKC Jobs\...`
     vs `Y:\Ready Jobs\...`).
   - The file at `manifest["archivePath"]` exists, parses as JSON, and matches the known shape: a
     dict with a `tabletId` string and an `actions` list of dicts.
4. For each qualifying manifest, extend the same rows list `_load_legacy_json_actions` builds with
   `(timestamp, path, idx, action)` tuples from that archived file's `actions`, using the archived
   file's own path (not the manifest path) as the tie-breaking `path` component, then let the
   existing sort (`_sort_combined_actions` / the legacy sort) run over the combined set exactly as
   today. No new merge/dedupe logic — an archived action that duplicates a live one collapses the
   same way any other duplicate action already does in the CNC/hardwoods per-key merge functions.
5. Once a manifest's actions have been folded in successfully, write a sibling `folded.json`
   (`{"foldedAt": <iso timestamp>}`) next to that `manifest.json`, atomically. Subsequent passes
   skip any archive directory that already has `folded.json`, so the glob-and-parse cost stays
   bounded as the archive grows over months instead of re-parsing every historical conflict on
   every consolidation cycle.

### Error handling

- A manifest that fails to parse, is missing expected fields, or whose `archivePath` is missing/
  unreadable/malformed is logged at `debug` and skipped for this pass — not fatal, retried next
  cycle (consistent with how malformed legacy tracker files are already handled).
- A `folded.json` write failure (e.g. permissions, transient share hiccup) is logged and leaves the
  manifest un-marked, so the next pass simply tries the fold again — folding is idempotent from the
  reader's perspective (it only ever adds rows into an in-memory list; nothing here mutates the
  live tracker file or the archive), so a repeated fold before the marker lands is harmless, just
  wasted work, not a correctness risk.
- This step never modifies or deletes anything under `sync_conflicts\`; it only reads
  `manifest.json`/`archivePath` and adds the new `folded.json` marker file.

### Testing

New tests in `tests/test_tracker_action_stream.py`:

1. **Recovery fixture** — reproduce the exact Ross Kaufman/DC Williams shape: a live
   `CNC\.tracker\<tablet>.json` with one set of actions, plus an archived
   `.metadata\sync_conflicts\<id>\<tablet>.json` + `manifest.json` (`action: archived_divergent`)
   with different actions for the same tablet. Assert `load_cnc_tracker_actions` returns the union
   of both action sets, correctly ordered.
2. **Idempotency** — call the loader twice; assert a `folded.json` marker appears after the first
   call and the second call doesn't duplicate the recovered actions or re-parse the archived file
   (spy/count file opens, or assert output identical and stable).
3. **Negative: unrelated conflict in the same bucket** — a manifest for something else entirely
   (e.g. an archived `delivery_schedule_request` conflict) sitting in the same
   `sync_conflicts\` directory must be ignored (not folded, no error).
4. **Negative: wrong action type** — an `archived_duplicate` manifest (content already identical)
   must not be folded a second time; its actions are already in the live file.
5. **Negative: path-shape mismatch** — a manifest whose `originalPath` doesn't end in
   `CNC\.tracker\<name>.json` or `hardwoods\.tracker\<name>.json` (defense in depth) is ignored even
   if it happens to parse with a `tabletId`/`actions` shape.
6. Existing `tests/test_metadata_scheduler_integration.py` / `tests/test_config.py` coverage for
   `_consolidate_tracker` continues to pass unchanged — this fix is purely additive to what the
   reader returns, not a change to the consolidation/merge functions themselves.

### Out of scope

- No change to `sync_conflict_resolver.py`'s archiving behavior, manifest schema, or the
  "never overwrite original bytes" invariant.
- No change to `_merge_cnc_actions`/`_merge_hardwoods_actions` or the bad-parts monitor's own
  logic — they already correctly handle whatever action rows they're given (including the C-01 fix
  already in place).
- No UI/GUI change. Recovered actions surface exactly like any other tracker action, through the
  existing consolidation → cache/bad-parts pipeline.
- `kkc-metadata-map/SKILL.md`'s C-01 line gets corrected as a doc fix alongside this change; no
  code change is needed for C-01 itself since it was already fixed.
