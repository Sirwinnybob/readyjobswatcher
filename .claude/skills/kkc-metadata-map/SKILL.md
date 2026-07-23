---
name: kkc-metadata-map
description: >-
  Use when tracing KKC Ready Jobs metadata ownership or parity, stale tablet
  job data, missing jobs, CNC or hardwood tracker streams, Syncthing
  conflicts, tablet request sidecars, supply schema/status/comments,
  safety concern reports/status/comments, .time_cards, timeclock API fields,
  production_order.json, delivery_schedule.json, update-feed metadata, admin
  metadata, molding/moulding profile, dimension-override, or frame-style tag
  files, or deciding whether KKCSheetTracker, Ready Jobs Watcher, Hours Tracker,
  timeclock-hub, or updater-agent owns a file.
metadata:
  sync:
    version: 3
---
# KKC Metadata Map

## Overview

Use this skill to answer: "Which system owns this metadata file, where is the source of truth, and where should debugging start?" Prefer owner evidence over guessing from symptoms.

> **Deep reference:** a full evidence-backed cross-program audit of the shared metadata contract
> (with write-safety analysis and an open-issue register) lives at
> `C:\Scripts\Hours Tracker\METADATA_AUDIT.md`. When this map and the code disagree, trust the code,
> fix the code, then update BOTH this skill and that audit doc (rules are in the audit's §1.4).

> **Mirror sync:** the six per-repo `.claude`/`.agents` copies of this skill (Hours Tracker,
> Ready Jobs Watcher, KKCSheetTracker) are kept byte-identical to this file automatically by
> `sync-kkc-metadata-map.ps1`, triggered on every edit here via a PostToolUse hook in the global
> Claude Code settings. Edit only this canonical copy — the mirrors are overwritten and
> auto-committed, so manual edits to a mirror will be silently replaced.

## System Boundaries

| System | Role | First Path |
|---|---|---|
| KKCSheetTracker Android | Reads shared Ready Jobs metadata, writes tablet progress/actions, crash reports, local app state | `C:\Scripts\KKCSheetTracker` |
| Ready Jobs Watcher | Publishes per-job parse/cache metadata that tablets consume | `C:\Scripts\Ready Jobs Watcher` |
| Hours Tracker | Manages digital hours/admin metadata and some global Ready Jobs admin files | `C:\Scripts\Hours Tracker` |
| timeclock-hub | RTC-1000 punch clock REST hub and SQLite source of truth for punch-clock timeclock | `C:\Scripts\timeclock-hub` |
| updater-agent | Android helper for installs/silent update behavior | `C:\Scripts\KKCSheetTracker\updater-agent` |

Shared Ready Jobs usually appears on the PC as `Y:\Ready Jobs` and on the Hours Tracker Docker server as `/mnt/KKC/Syncthing/KKC Jobs/Ready Jobs`.

Cabinet Vision (external CAD database, not one of the five programs above) is the source of truth for the profile geometry that Ready Jobs Watcher's molding sync pulls from — see the moldings row below.

## Ownership Map

| Metadata / Path Pattern | Owner | First Debug Check |
|---|---|---|
| `Y:\Ready Jobs\<job>\.metadata\deployment_gate.json` | Ready Jobs Watcher | Inspect `deployed`, `parseReady`, `hiddenFromProduction`, then `ready_jobs_watcher.log` |
| `Y:\Ready Jobs\<job>\.metadata\cache_static.json` | Ready Jobs Watcher | Check mtime/content, then `metadata_cache.py` and watcher logs |
| `Y:\Ready Jobs\<job>\CNC\.metadata\<pdf-stem>.json` | Ready Jobs Watcher | Check sidecar exists for each CNC PDF and parse errors in `cnc_scan.log` |
| `Y:\Ready Jobs\<job>\CNC\.metadata\remake_bad_parts_candidates.json` | Ready Jobs Watcher | Check CNC scan log and scheduled cache refresh entries |
| `Y:\Ready Jobs\<job>\CNC\.tracker\<tablet>.json` | KKCSheetTracker tablets | **LIVE channel** for CNC actions (carries `bad_part_submitted` alert marker, `ProgressStore.kt:818`). RJW consolidates then DELETES these files — consolidation is lossy for `bad_part_submitted` (see audit C-01) |
| `Y:\Ready Jobs\<job>\CNC\.tracker\events\**\*.ndjson` | (designed for tablets) Ready Jobs Watcher reads | **DORMANT** as of 2026-07-09 — RJW's `tracker_action_stream.py` reader exists but the tablet writes NO ndjson (grep-confirmed). Legacy `<tablet>.json` above is the real channel |
| `Y:\Ready Jobs\<job>\CNC\.tracker\consolidated.json` | Ready Jobs Watcher | Check tracker action stream/reconcile logs |
| `Y:\Ready Jobs\<job>\CNC\.tracker\watcher_refresh_watcher.json` | Ready Jobs Watcher | Refresh heartbeat; not source-of-truth progress |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\cutlist_index.json` | Ready Jobs Watcher | Compare against hardwood source files and watcher logs |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\cutlist_revisions.json` | Ready Jobs Watcher | Check revision state before blaming tablet UI |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\board_stock_manual.json` | **writer external/unconfirmed** (manual/admin); RJW + tablet READ-ONLY | Manual board-stock input folded into `cache_static.json`; no writer found in the 3 audited programs (audit SK-05) |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\<tablet>.json` | KKCSheetTracker tablets | Hardwood completion/progress state |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\events\**\*.ndjson` | (designed for tablets) Ready Jobs Watcher reads | **DORMANT** — no tablet producer as of 2026-07-09; live channel is `<tablet>.json` (audit SK-01) |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\<tablet>.markup.json` | KKCSheetTracker tablets | Hardwood ink/PDF markup state |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\.board_stock_*_<tablet>.json` | KKCSheetTracker tablets | Hardwood board-stock migration markers |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\watcher_refresh_watcher.json` | Ready Jobs Watcher | Hardwood refresh heartbeat |
| `Y:\Ready Jobs\<job>\.metadata\cabinet_sheet_index.json` | Ready Jobs Watcher | Check `cabinet_sheet_indexer.py` and root PDF mtimes |
| `Y:\Ready Jobs\.metadata\moldings\{Crown,Scribe,Base}\<profileId>.xml` | Ready Jobs Watcher (pulls from Cabinet Vision `Profile`/`Shape` tables, deletes obsolete profile files); Hours Tracker reads only | HT's molding library page caches parsed geometry by (mtime, size); check `moldings_sync.py` (skips rewrite when bytes match, not time-based), then Cabinet Vision DB connectivity (audit SK-06) |
| `Y:\Ready Jobs\.metadata\moldings_cache\{Crown,...}\<profileId>.svg`, `<profileId>_dim.svg`, `library.json`, `usage_index.json` | Hours Tracker (`molding_cache_publish.py`), published for KKCSheetTracker to read directly (no HTTP call) | Deliberately a SIBLING of `moldings\`, not a child — nesting it inside would make it show up as a bogus CV category. This is the ONLY bridge from HT's own molding sidecar stores to the tablet: `library.json`'s `moldings[].frameStyle` field and each `_dim.svg`'s baked-in lines are how `molding_frame_style.json` and `molding_dimensions.json` actually reach KKCSheetTracker, even though those sidecar files themselves never leave HT's `DATA_DIR`. Full rebuild via `publish_library_cache()` on every `PUT .../dimensions` or `PUT .../frame-style`, plus a 5-minute reconciliation sweep; stale cache after a tag/dimension edit means check the sweep/publish call, not Ready Jobs Watcher (audit SK-07) |
| `Y:\Ready Jobs\<job>\.metadata\pdf_markup\.tracker\<tablet>.markup.json` | KKCSheetTracker tablets | Root/reference PDF markup |
| `Y:\Ready Jobs\<job>\.metadata\pdf_markup\.tracker\<tablet>.json` | KKCSheetTracker tablets | Legacy PDF markup fallback |
| `Y:\Ready Jobs\.metadata\crashes\*.json` | KKCSheetTracker Android | Read latest crash JSON, then match app version and route/screen |
| `Y:\Ready Jobs\.metadata\material_mappings.json` | **writer external/unconfirmed**; Hours Tracker + KKCSheetTracker READ-ONLY | Shared material mapping for door-panel/specialty automation. HT backend only reads (`specialty_store.py:31`); no writer among the 3 audited programs (audit SK-04) |
| `Y:\Ready Jobs\.metadata\themes\active_theme.json`, `themes\*.json`, `themes\graphics\*.svg` | **external theme tool** (NOT Hours Tracker backend); KKCSheetTracker reads | Global tablet theme/graphics. HT backend has zero refs to these (audit SK-03) |
| `Y:\Ready Jobs\.metadata\timeclock_messages.json` | **external message tool** (NOT Hours Tracker backend); KKCSheetTracker reads | Global shop/tablet timeclock messages. HT backend has zero refs (audit SK-03) |
| `Y:\Ready Jobs\.metadata\sync_conflicts\<id>\manifest.json` | Ready Jobs Watcher | Root/global Syncthing conflict archive manifest |
| `Y:\Ready Jobs\production_order.json` | Hours Tracker/admin workflow; Ready Jobs Watcher reads it | Check Hours Tracker admin state, then cache refresh into jobs |
| `Y:\Ready Jobs\production_order_request.<tabletId>.json` | KKCSheetTracker tablet writes; Hours Tracker consumes | Per-tablet lineup request; malformed input may be quarantined, but transient I/O/lock/write failure must leave it for retry |
| `Y:\Ready Jobs\job_board.json` | Hours Tracker/admin workflow | Check Hours Tracker admin UI/backend first |
| `Y:\Ready Jobs\job_board_request.<tabletId>.json` | KKCSheetTracker tablet writes; Hours Tracker consumes | Per-tablet board request; apply oldest-first and preserve on transient failure |
| `Y:\Ready Jobs\.metadata\delivery_schedule.json` | Hours Tracker/admin workflow | Check Hours Tracker backend/admin paths |
| `Y:\Ready Jobs\delivery_schedule_request.<tabletId>.json` | KKCSheetTracker tablet writes; Hours Tracker consumes | Request lives at Ready Jobs root, while master lives under `.metadata`; preserve on transient failure |
| `Y:\Ready Jobs\.supply\categories.json` | Hours Tracker/admin workflow; tablets can read/write status | Supply category list/order |
| `Y:\Ready Jobs\.supply\schema.json` | Hours Tracker/admin workflow | Custom supply field schema |
| `Y:\Ready Jobs\.supply\items\<itemId>.json` | Hours Tracker/admin workflow; tablets can create/update | Supply item record |
| `Y:\Ready Jobs\.supply\status\<itemId>.<device>.json` | KKCSheetTracker tablets and Hours Tracker admin | Per-device supply item status |
| `Y:\Ready Jobs\.supply\comments\<itemId>\<commentId>.json` | KKCSheetTracker tablets and Hours Tracker admin | Supply item comments |
| `Y:\Ready Jobs\.supply\attachments\<itemId>\*` | Hours Tracker/admin workflow | Supply item uploaded attachments |
| `Y:\Ready Jobs\.safety\concerns\<id>.json` | KKCSheetTracker tablets (writer, `SafetyRepository.kt`) and Hours Tracker admin (writer via `POST /api/safety/concerns`) | Safety concern report; both writers use the same shared base path, `safety_store.get_safety_dir()` |
| `Y:\Ready Jobs\.safety\status\<id>.<tabletId\|server>.json` | KKCSheetTracker tablets and Hours Tracker admin | Latest-status-wins per concern (`OPEN`/`ACKNOWLEDGED`/`IN PROGRESS`/`RESOLVED`); resolved by max `at` across all `<id>.*.json` files |
| `Y:\Ready Jobs\.safety\comments\<id>\<commentId>.json` | KKCSheetTracker tablets and Hours Tracker admin | Safety concern comment thread |
| `Y:\Ready Jobs\.safety\attachments\*` | KKCSheetTracker tablets and Hours Tracker admin | Safety concern uploaded photos |
| `Y:\Ready Jobs\<job>\.metadata\admin\rip_items.json` | Hours Tracker/admin workflow | Check Hours Tracker admin state before Android |
| `Y:\Ready Jobs\<job>\.metadata\admin\checklist.json` | Hours Tracker/admin workflow; consumes tablet patch sidecars at read time | `admin_store.get_checklist` merges `checklist_patch.<tablet>.json` sidecars into this file and DELETES them — but only OUTSIDE a read-only context. A read inside `read_only_context()` (e.g. the handoff `checklist` source / `GET /api/handoff/sources`) merges in memory and leaves the sidecars intact (`admin_store.py:127,150,164`). Check HT admin state |
| `Y:\Ready Jobs\<job>\.metadata\admin\checklist_patch.<tablet>.json` | KKCSheetTracker tablets (writer); Hours Tracker admin read CONSUMES | Tablet-authored checklist completion overlay (H-04 write-contention fix). The admin GET path folds it into `checklist.json` and unlinks it; read-only handoff discovery must NOT consume it — that gating lives in `admin_store._apply_checklist_patches` / `get_checklist` |
| `Y:\Ready Jobs\<job>\.metadata\admin\rule_applications.json` | Hours Tracker/admin workflow | Check Hours Tracker admin rule code |
| `Y:\Ready Jobs\<job>\.metadata\admin\board_stock.json` | Hours Tracker/admin workflow | Check Hours Tracker board stock/admin paths |
| `Y:\Ready Jobs\<job>\.metadata\admin\specialty_items.json` | Hours Tracker/admin workflow; KKCSheetTracker may patch item fields | Check admin state and tablet specialty progress writes |
| `Y:\Ready Jobs\<job>\.metadata\admin\.tracker\<tablet>.json` | KKCSheetTracker tablets | Specialty item/station completion state |
| `Y:\Ready Jobs\<job>\.metadata\admin\tablet_items_<tablet>.json` | KKCSheetTracker tablets | Tablet-created specialty items |
| `Y:\Ready Jobs\<job>\.metadata\admin\sheet_rip_done.json` | **KKCSheetTracker tablets (writer); Hours Tracker READ-ONLY** | Manual sheet-rip completion state. HT only reads (`board_stock_store.py:232`); tablet is sole writer but shares one filename (lost-update risk, audit H-04/SK-02) |
| `Y:\Ready Jobs\<job>\.metadata\admin\checklist_attachments\<itemId>\*` | Hours Tracker/admin workflow; KKCSheetTracker reads | Uploaded checklist attachments |
| `Y:\Ready Jobs\<job>\.metadata\admin\specialty_attachments\<itemId>\*` | Hours Tracker/admin workflow; KKCSheetTracker reads | Uploaded specialty attachments |
| `Y:\Ready Jobs\<job>\.metadata\sync_conflicts\<id>\manifest.json` | Ready Jobs Watcher | Per-job Syncthing conflict archive manifest |
| `Y:\Ready Jobs\.time_cards\employees.json` | Hours Tracker | Check employee source and backend sync |
| `Y:\Ready Jobs\.time_cards\<Employee>\<YYYY-MM-DD>.json` | Hours Tracker Android/backend | Check weekly JSON first; SQLite is reporting cache |
| `Y:\Ready Jobs\.time_cards\<Employee>\<YYYY-MM-DD>.json.lock` | Hours Tracker Android/backend | Fresh tablet timecard active-write lease |
| `Y:\Ready Jobs\.time_cards\<Employee>\profile.json` | Hours Tracker Android primary; server reads/limited writes | Player profile, coins, stats, avatar, shop history |
| `Y:\Ready Jobs\.time_cards\<Employee>\profile.json.lock` | Hours Tracker Android/backend | Active profile/session lease |
| `Y:\Ready Jobs\.time_cards\<Employee>\granted_badges.json` | Hours Tracker backend; Android reads | Server-granted badges/XP |
| `Y:\Ready Jobs\.time_cards\<Employee>\activity_events.json` | Hours Tracker Android/backend | Badge/streak/shop activity feed |
| `Y:\Ready Jobs\.time_cards\<Employee>\alerts.json` | Hours Tracker backend | Server-authored employee alerts |
| `Y:\Ready Jobs\.time_cards\<Employee>\acknowledgements.json` | Hours Tracker Android | Tablet-authored alert acknowledgements |
| `Y:\Ready Jobs\.time_cards\<Employee>\avatar_pending.jpg` | Hours Tracker backend | Uploaded avatar staged for tablet adoption |
| `Y:\Ready Jobs\.time_cards\badges_config.json` | Hours Tracker backend; Android reads | Central badge definitions |
| `Y:\Ready Jobs\.time_cards\custom_badges.json` | Hours Tracker backend legacy migration | Legacy custom badge source migrated into `badges_config.json` |
| `Y:\Ready Jobs\.time_cards\.badge_images\*` | Hours Tracker backend; Android reads | Uploaded badge artwork |
| `Y:\Ready Jobs\.time_cards\challenges.json` | Hours Tracker backend; Android reads | Weekly challenge catalog |
| `Y:\Ready Jobs\.time_cards\pending_edits.json` | Hours Tracker | Check locks if edits are queued but not applied |
| `Y:\Ready Jobs\.time_cards\loaded_cards.json` | Hours Tracker | Check export/double-count state |
| `Y:\Ready Jobs\.time_cards\.locks\{shop,timecards,alerts,badges,employees}.lock` | Hours Tracker backend | Multi-server admin edit locks |
| `Y:\Ready Jobs\.time_cards\*.json.tmp`, per-employee `*.json.tmp` | Hours Tracker backend | Transient atomic-write temp files |
| `Y:\TimeCardUpdater\version.json` and `TimeCardTracker.exe` | Hours Tracker updater publishing | Use only for Hours Tracker PC app, not KKCSheetTracker |
| `Y:\Ready Jobs\.Updates\*.apk`, `Y:\Ready Jobs\Updates\*.apk` | KKCSheetTracker legacy updater | Release/manual APK update folders |
| `Y:\Ready Jobs\.Testing_Updates\*.apk` | KKCSheetTracker and Hours Tracker Android testing updates | Debug/testing APK update folder; verify package name |
| `Y:\Ready Jobs\.appupdates\device_policy.json` | updater-agent and KKCSheetTracker fallback updater | Silent-update policy |
| `Y:\Ready Jobs\.appupdates\apps\manifest.json` | update publishing workflow; updater-agent reads | Update feed with package/version/apk/hash/channel |
| `Y:\Ready Jobs\.appupdates\apps\<packageName>\<apkFile>.apk` | update publishing workflow; updater-agent installs | Actual APK artifact |
| `Y:\Ready Jobs\.appupdates\<tabletId>\install-log.ndjson` | updater-agent | Per-tablet install audit log |
| `Y:\Ready Jobs\.appupdates\<tabletId>\updater-fallback-required.json` | updater-agent writes; KKCSheetTracker reads | Signal to use legacy update prompt |
| `Y:\Ready Jobs\.appupdates\migration_complete.json` | KKCSheetTracker | Migration completion marker |

Important caveat: Hours Tracker normally does not own `<job>\.metadata\cache_static.json`. It only reads that file unless emergency legacy writes are enabled with `HOURS_TRACKER_ENABLE_LEGACY_CACHE_WRITES=1`.

Second caveat: if a Cabinet Vision molding profile is renamed or removed, Ready Jobs Watcher deletes its obsolete `.xml` (`moldings_sync.py:186-193`); dimensions saved under that `moldingId` in `molding_dimensions.json` are not deleted, just orphaned until re-linked to a live profile.

## Cross-System Contract Invariants

- Every metadata reader that globs shared files must exclude `.sync-conflict-*`, including NDJSON tracker streams and supply items, statuses, and comments. Do not assume a conflict filter in one loader protects sibling loaders.
- CNC and hardwood event ordering is `(timestamp, lamport, eventId, stable tie-breakers)` everywhere. Keep Android replay, watcher consolidation, and `consolidated.json` consistent.
- A compactor must not unlink a live per-tablet event stream after only an mtime/size check. That has a stat-to-unlink race. Use rotation/acknowledgement or an equivalent protocol that cannot delete a late append.
- Tablet request sidecars are per-tablet and consumed oldest-first. Distinguish malformed payloads from operational failures: quarantine/consume invalid input; retry transient I/O, lock, or master-write failures without deleting the request.
- Supply schema field `id` and `key` values must be nonblank and unique. Built-in definitions must stay canonical across Hours Tracker and Android. Supply per-writer status/comment JSON must be atomic, and latest-status resolution must ignore conflict copies and compare parsed instants.
- timeclock-hub SQLite is the punch source of truth. Punch duration rounds up to 15 minutes; sessions under 7 minutes are deleted silently; hub timezone is `America/Los_Angeles`.
- In the hub database, `employees.display_name` is the numeric RTC display ID and `nickname` is the RTC human label. In API responses, `display_name` is the effective human RTC name. Android must not discard it as though it were the numeric database field.
- Updater policy/manifest data is privileged input. Require a non-empty signer allowlist for every managed package, reject duplicate/blank package entries, and prove resolved APK paths remain under `.appupdates\apps\<packageName>`.

## Local State

| Path / State | Owner | First Debug Check |
|---|---|---|
| `C:\Scripts\Ready Jobs Watcher\config.json`, `.backup` | Ready Jobs Watcher | Root path, debounce, snapshot, queue, Assimp settings |
| `C:\Scripts\Ready Jobs Watcher\pending_queue.json`, `.backup`, `.save_backup`, `.tmp` | Ready Jobs Watcher | Restart-resumable delayed PDF/folder work |
| `C:\Scripts\Ready Jobs Watcher\tracker_bad_parts_state.json` | Ready Jobs Watcher | Active/seen/ack bad-part alert state |
| `C:\Scripts\Ready Jobs Watcher\metadata_snapshots\<job>\<date>\<stamp-reason>\manifest.json` | Ready Jobs Watcher | Snapshot inventory of per-job/global metadata |
| `C:\Scripts\Ready Jobs Watcher\*.log` | Ready Jobs Watcher | Main diagnostics; include `ready_jobs_watcher.log`, `cnc_scan.log`, `backup.log` |
| `C:\Scripts\Ready Jobs Watcher\bad_parts_blacklist.json`, `permanently_ignored_blacklist.json` | Ready Jobs Watcher legacy bad-parts flow | Legacy PDF-highlight suppression |
| `C:\Scripts\Hours Tracker\config.json`, `%APPDATA%\TimeCardTracker\config.json` | Hours Tracker | Local paths for update share, Excel export, timecards, DB |
| `C:\Scripts\Hours Tracker\backend\hours.db`, `%APPDATA%\TimeCardTracker\hours.db`, Docker `/data/hours.db` | Hours Tracker | SQLite reporting/read cache; JSON remains source of truth |
| `hours.db-wal`, `hours.db-shm` | SQLite | WAL sidecars for the Hours Tracker reporting DB |
| `C:\Scripts\Hours Tracker\backend\weekly_backup_log.json`, `%DATA_DIR%\weekly_backup_log.json` | Hours Tracker | Last weekly Excel backup/export run |
| `C:\Scripts\Hours Tracker\backend\results\*.json`, `dist\results\*.json` | Hours Tracker | Import/export/report result payloads served by backend |
| `C:\Scripts\Hours Tracker\backend\employee_mapping.json` | Hours Tracker | Alias/canonical employee mapping for imports/admin |
| `C:\Scripts\Hours Tracker\backend\checklist_rules.json`, `%DATA_DIR%\checklist_rules.json` | Hours Tracker | Global checklist automation rules |
| `C:\Scripts\Hours Tracker\backend\board_stock_materials.json`, `%DATA_DIR%\board_stock_materials.json` | Hours Tracker | Remembered board-stock material names |
| `C:\Scripts\Hours Tracker\backend\molding_dimensions.json`, `%DATA_DIR%\molding_dimensions.json` | Hours Tracker | User-assigned molding dimension overrides (segment/overall/manual), keyed by `moldingId` (e.g. `"Crown:105"`); lives outside `Y:\Ready Jobs`, never touched by Ready Jobs Watcher (audit SK-06) |
| `C:\Scripts\Hours Tracker\backend\molding_frame_style.json`, `%DATA_DIR%\molding_frame_style.json` | Hours Tracker | Crown-only Face Frame/Frameless tag, keyed by `moldingId`; same HT-local sidecar pattern as `molding_dimensions.json` above, but unlike dimensions this DOES flow into the published tablet cache — see the `moldings_cache` Ownership Map row (audit SK-07) |
| `C:\Scripts\timeclock-hub\data\timeclock.db` | timeclock-hub | SQLite source of truth for RTC punch-clock employees/punches |
| `C:\Scripts\timeclock-hub\data\timeclock.db.backup_*` | timeclock-hub cleanup/admin workflow | Backup before duplicate/local punch cleanup |
| `C:\Scripts\timeclock-hub\downloaded-timeclock.db` | timeclock-hub admin/debug workflow | Local copy from `/api/db/download` |
| `C:\Scripts\timeclock-hub\.env` | timeclock-hub deployment config | RTC URL/user/pass, poll interval, hub IP/port/admin token; do not paste secrets |
| `C:\Scripts\timeclock-hub\docker-compose.yml` | timeclock-hub deployment | Port `8765`, volume `./data:/app/data`, `TZ=America/Los_Angeles` |
| Docker logs for `timeclock-hub` | timeclock-hub runtime | Employee sync, punch sync, migrations, RTC failures |

## Android Local State

| State | Owner | Purpose |
|---|---|---|
| `SharedPreferences/kkc_tracker` | KKCSheetTracker | Base path, tablet ID, work mode, theme/UI flags, crash context |
| `SharedPreferences/kkc_clock_in` | KKCSheetTracker | Job clock-in overlay state |
| `SharedPreferences/UpdateManagerPrefs` | KKCSheetTracker legacy updater | Custom update path and skipped versions |
| DataStore `syncthing_settings` | KKCSheetTracker | Syncthing API/key settings |
| DataStore `timeclock_config` | KKCSheetTracker | Manual/cached timeclock hub URL; default manual IP may be `192.168.1.15` |
| DataStore `timeclock_background` | KKCSheetTracker | Timeclock background type/color/media path |
| DataStore `pinned_jobs` | KKCSheetTracker | Tablet pinned jobs |
| DataStore `assembly_viewer_defaults` | KKCSheetTracker | Assembly viewer defaults |
| DataStore `specialty_viewer_defaults` | KKCSheetTracker | Specialty viewer defaults |
| `filesDir\state\drafts\<job>\<tablet>.json` | KKCSheetTracker | Local bad-part drafts |
| `filesDir\state\ocr\<job>\<pdf>\<fingerprint>\p<page>.json` | KKCSheetTracker | OCR box cache |
| `filesDir\crash_reports\pending\*.json` | KKCSheetTracker | Pending crash fallback before shared path is available |
| `filesDir\timeclock_bg\*` | KKCSheetTracker | Copied timeclock background media |
| `filesDir\supply_subscriptions.json` | KKCSheetTracker | Local supply subscriptions |
| `SharedPreferences/kkc_tracker`, key `updater_tablet_id` | updater-agent | Stable tablet ID for `.appupdates\<tabletId>` files |
| WorkManager unique work `kkc_updater_periodic` | updater-agent | Periodic silent update worker state; inspect through logs/WorkManager |

## Generated Or Cache Artifacts

| Path Pattern | Owner | How To Treat It |
|---|---|---|
| `Y:\Ready Jobs\<job>\DARK MODE\*.pdf` | Ready Jobs Watcher | Generated dark-mode copies; not source PDFs |
| `Y:\Ready Jobs\<job>\3D\<room>\3d_medium.glb` | Ready Jobs Watcher | Generated Android 3D viewer asset from `3d.dae` |
| `Y:\Ready Jobs\<job>\CNC\.metadata\.thumbs\*`, `.fullimages\*`, `.fullImages\*` | Metadata/PDF render cache | Inspect for missing previews; do not treat as source metadata |
| `Y:\Ready Jobs\.metadata\.thumbs\*`, `.fullimages\*`, `.fullImages\*` | Hours Tracker/PDF render cache | Inspect for admin preview issues only |
| `Y:\Ready Jobs\<job>\**\*.tmp`, `*.ocr.tmp`, `.tmp_assimp_*` | Atomic writers/converters | Usually transient; investigate only if stuck/stale |
| `Y:\Ready Jobs\<job>\CNC\.tracker\watcher_refresh.json`, `watcher_refresh_splitter.json` | Legacy/historical refresh markers | Caveat only; current watcher signal is `watcher_refresh_watcher.json` |

## Symptom Routing

| Symptom | Start Here |
|---|---|
| Tablet does not show a job | `deployment_gate.json`, then `cache_static.json`, then Ready Jobs Watcher logs |
| Job appears but material counts/pages are wrong | `cache_static.json`, CNC sidecars, `cnc_scan.log` |
| CNC progress/bad parts stale | `CNC\.tracker\*.json`, `events\*.ndjson`, `consolidated.json` |
| Hardwoods rows/revisions wrong | `.metadata\hardwoods\cutlist_index.json`, `cutlist_revisions.json` |
| Assembly/cabinet view wrong | `.metadata\cabinet_sheet_index.json` |
| Molding profile geometry missing/wrong | `.metadata\moldings\<category>\<profileId>.xml`, `moldings_sync.py`, Cabinet Vision `Profile`/`Shape` tables |
| Molding dimension lines/annotations missing or reset | `molding_dimensions.json`, `molding_dimensions_store.py` |
| Crown Face Frame/Frameless tag not showing/grouping right on tablet | `molding_frame_style.json`, `PUT /api/moldings/{id}/frame-style`, confirm `publish_library_cache()` ran, then `moldings_cache\library.json`'s `frameStyle` field |
| Specialty/admin items wrong | Hours Tracker admin files, then KKCSheetTracker specialty repository |
| PDF markup missing | `.metadata\pdf_markup\.tracker\<tablet>.markup.json`, then tablet app version |
| Supply item/status wrong | `.supply\items`, `.supply\status`, `.supply\comments`, then Hours Tracker supply backend |
| Safety concern submitted on tablet not showing on Hours Tracker | Confirm the file landed in `.safety\concerns` under the SAME base path the rest of Hours Tracker uses (`.metadata`, `.supply`, `job_board.json`) -- check `safety_store.get_safety_dir()` resolves via `get_base_path()`, not a separately-derived path (audit-style bug, see Common Mistakes) |
| Production order/lineup wrong | `production_order.json`, Hours Tracker admin, then Ready Jobs Watcher cache refresh |
| Delivery schedule wrong | `Y:\Ready Jobs\.metadata\delivery_schedule.json`, Hours Tracker |
| Digital hours wrong | `.time_cards\<Employee>\<week>.json`, locks, `pending_edits.json` |
| Badge/profile/shop wrong | `.time_cards\<Employee>\profile.json`, `badges_config.json`, locks |
| Punch-clock timeclock wrong | `C:\Scripts\timeclock-hub\data\timeclock.db`, hub logs, not Hours Tracker |
| Hub name differs between browser and tablet | Compare hub API `display_name`, Android `TimecardRepository`, then Hours Tracker profile-name precedence |
| Install/update wrong | `.appupdates\<tabletId>\install-log.ndjson`, installed package versions, updater-agent logs |
| App crashed | `Y:\Ready Jobs\.metadata\crashes`, then ADB `AndroidRuntime` logs |

## First Commands

Ready Jobs Watcher:

```powershell
Get-Content "Y:\Ready Jobs\<job>\.metadata\deployment_gate.json"
Get-Item "Y:\Ready Jobs\<job>\.metadata\cache_static.json"
Get-Content "C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.log" -Tail 200
Get-Content "C:\Scripts\Ready Jobs Watcher\cnc_scan.log" -Tail 200
Get-Content "C:\Scripts\Ready Jobs Watcher\pending_queue.json"
Get-Content "C:\Scripts\Ready Jobs Watcher\tracker_bad_parts_state.json"
```

Hours Tracker:

```powershell
Get-ChildItem "Y:\Ready Jobs\.time_cards"
Get-Content "Y:\Ready Jobs\.time_cards\employees.json"
Get-Content "Y:\Ready Jobs\.time_cards\badges_config.json"
Get-Content "Y:\Ready Jobs\.time_cards\pending_edits.json"
Get-Content "Y:\Ready Jobs\.time_cards\loaded_cards.json"
Get-ChildItem "Y:\Ready Jobs\.time_cards\.locks"
Get-ChildItem "Y:\Ready Jobs\.supply" -Recurse -Depth 2
Get-ChildItem "Y:\Ready Jobs\.safety" -Recurse -Depth 2
```

KKCSheetTracker tablet:

```powershell
adb devices -l
adb shell dumpsys package com.kkc.sheettracker | Select-String "versionName|versionCode"
adb logcat -d -v time AndroidRuntime:E KKC_CRASH_REPORTER:* KKC_APP_STATE:* KKC_NAV:* *:S
```

Updater-agent:

```powershell
adb shell dumpsys package com.kkc.updateragent | Select-String "versionName|versionCode"
Get-Content "Y:\Ready Jobs\.appupdates\device_policy.json"
Get-Content "Y:\Ready Jobs\.appupdates\apps\manifest.json"
Get-ChildItem "Y:\Ready Jobs\.appupdates" -Recurse -Filter install-log.ndjson
```

timeclock-hub:

```powershell
docker compose -f "C:\Scripts\timeclock-hub\docker-compose.yml" logs --tail 200
Get-Item "C:\Scripts\timeclock-hub\data\timeclock.db"
```

Hours Tracker Android app:

```powershell
adb shell dumpsys package com.example.timecard | Select-String "versionName|versionCode"
```

## Code Entry Points

| Question | Read |
|---|---|
| How does KKCSheetTracker read job metadata? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\data` |
| How are crash files written? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\crash` |
| How does Ready Jobs Watcher publish gates/cache? | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\deployment_gate.py`, `metadata_cache.py` |
| How are CNC tracker events consolidated? | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\tracker_action_stream.py` |
| How does Android append/order CNC tracker events? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\data\ProgressStore.kt`, `TrackerEventLog.kt` |
| How does Android append/order hardwood events? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\data\HardwoodsProgressStore.kt`, `TrackerEventLog.kt` |
| How are cabinet/sheet indexes generated? | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\cabinet_sheet_indexer.py` |
| How does Ready Jobs Watcher publish the Cabinet Vision molding library? | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\moldings_sync.py` |
| How does Hours Tracker store molding dimension overrides? | `C:\Scripts\Hours Tracker\backend\routes\molding_dimensions_store.py` |
| How does Hours Tracker store the crown Face Frame/Frameless tag? | `C:\Scripts\Hours Tracker\backend\routes\molding_frame_style_store.py` |
| How does Hours Tracker publish the tablet-facing molding cache (SVGs, `library.json`, `usage_index.json`)? | `C:\Scripts\Hours Tracker\backend\routes\molding_cache_publish.py` |
| How does Hours Tracker sync JSON to reporting DB? | `C:\Scripts\Hours Tracker\backend\db.py` |
| What API serves Hours Tracker admin data? | `C:\Scripts\Hours Tracker\backend\main_v2.py` |
| How are tablet lineup/board/delivery requests consumed? | `C:\Scripts\Hours Tracker\backend\main_v2.py`: `_apply_production_order_requests`, `_apply_job_board_edit_requests`, `_apply_delivery_schedule_request` |
| How is the supply schema and shared supply state served? | `C:\Scripts\Hours Tracker\backend\routes\supply_store.py`, `C:\Scripts\Hours Tracker\frontend\components\JobManager\supply\SupplySchemaEditor.tsx` |
| What frontend calls Hours Tracker APIs? | `C:\Scripts\Hours Tracker\frontend\lib\api_kkc.ts` |
| How does RTC punch clock work? | `C:\Scripts\timeclock-hub\app.py` |
| How does Android interpret hub names/status? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\data\TimecardRepository.kt` |
| How do silent Android updates work? | `C:\Scripts\KKCSheetTracker\updater-agent\src\main\java\com\kkc\updateragent\update` |
| How does legacy Android update discovery work? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\update` |
| How are PDF markup files written? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\data\PdfMarkupStore.kt` |
| How are supply files read/written on tablet? | `C:\Scripts\KKCSheetTracker\app\src\main\java\com\kkc\sheettracker\data\SupplyRepository.kt` |

## Common Mistakes

| Mistake | Correction |
|---|---|
| Blaming Android for a missing job before checking `deployment_gate.json` | Gate and cache are the first evidence |
| Blaming Hours Tracker for stale `cache_static.json` | Ready Jobs Watcher owns cache publication |
| Treating Hours Tracker and timeclock-hub as the same thing | Hours Tracker is digital timecards/admin; timeclock-hub is RTC punch clock |
| Using Hours Tracker APK/version paths for KKCSheetTracker | Check package names: `com.example.timecard`, `com.kkc.sheettracker`, `com.kkc.updateragent` |
| Trusting SQLite first for digital hours | `.time_cards` JSON is source of truth; SQLite is reporting/cache |
| Trusting `.time_cards` for punch-clock data | RTC punch-clock source is `timeclock-hub\data\timeclock.db` |
| Ignoring cache debounce | Ready Jobs Watcher may delay cache refresh for several minutes |
| Assuming one Syncthing conflict filter covers every format | Audit every JSON and NDJSON glob independently; conflict copies must never enter active state |
| Deleting a valid tablet request after any exception | Consume invalid payloads only; leave requests intact on transient I/O, lock, or master-write failure |
| Sorting tracker events only by timestamp | Preserve `lamport` and `eventId` through every decoder and use the same total ordering on Android and watcher |
| Treating API `display_name` as the numeric RTC display ID | That distinction exists only in hub storage; API `display_name` is the effective human RTC name |
| Trusting stat/size before unlink during tracker compaction | A writer can append between stat and unlink; use rotation/ack instead |
| Allowing empty updater signer policy because SHA-256 matches | A writable feed can replace both manifest and hash; signer allowlist and path containment are required |
| Searching all hidden Syncthing folders as jobs | Filter to real job folders like `<jobnum> - <name>` |
| Treating thumbnails/fullimages as source metadata | They are render caches; debug source JSON first |
| Assuming mDNS should always work for timeclock | Current hub may have mDNS disabled; use manual/default IP checks |
| Assuming CNC actions flow through `events\*.ndjson` | That reader is dormant; the tablet writes legacy `<tablet>.json` only (audit SK-01) |
| Trusting a bad-part alert reached the engineer | RJW consolidation drops `bad_part_submitted` then deletes the source file (audit C-01) — verify the alert, don't assume |
| Treating a `.sync-conflict-*` file as harmless | No program filters them; every metadata scan ingests them as a phantom writer (audit H-03) |
| Assuming a "read-only" HT read never mutates | `admin_store.get_checklist` consumes+deletes tablet `checklist_patch.*.json` unless inside `read_only_context()`; any new read-only consumer (handoff sources) MUST enter that context or it steals tablet patches (audit H-04) |
| Assuming Ready Jobs Watcher's molding library sync can overwrite Hours Tracker's saved dimensions | Different trees entirely: RJW only writes `.metadata\moldings\*.xml` geometry under `Y:\Ready Jobs`; HT's `molding_dimensions.json` lives in HT's own `DATA_DIR` and RJW never touches it (audit SK-06) |
| Assuming an HT-local molding sidecar store never reaches the tablet because it lives outside `Y:\Ready Jobs` | Depends on the field: `publish_library_cache()` calls `molding_library_store.get_moldings()`, which merges in `frame_style_store.get_frame_style()` for Crown entries — `frameStyle` DOES flow into the published `moldings_cache\library.json`. Dimensions instead get baked into the rendered `_dim.svg` files, not exposed as a raw field. Check the actual cache-publish code path before assuming either way (audit SK-07) |
| A new Hours Tracker store module derives its own base path (e.g. from `config.digital_timecards_path`) instead of calling the shared `routes.utils.get_base_path()` | Every other store (`board.py`, `delivery.py`, `supply_store.py`, `molding_library_store.py`, ...) resolves its subfolder as `get_base_path() / "<name>"`. A module with bespoke path logic can silently resolve to a different directory in Docker/prod even when it "looks equivalent" locally -- this exact bug hid `.safety\concerns` writes from the API (fixed 2026-07-23, `safety_store.get_safety_dir()`) |
