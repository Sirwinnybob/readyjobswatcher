---
name: kkc-metadata-map
description: Use when tracing KKC Ready Jobs metadata ownership, stale tablet job data, missing jobs, CNC or hardwood cache files, .time_cards, production_order.json, delivery_schedule.json, admin metadata, or deciding whether KKCSheetTracker, Ready Jobs Watcher, Hours Tracker, timeclock-hub, or updater-agent owns a file.
---

# KKC Metadata Map

## Overview

Use this skill to answer: "Which system owns this metadata file, where is the source of truth, and where should debugging start?" Prefer owner evidence over guessing from symptoms.

## System Boundaries

| System | Role | First Path |
|---|---|---|
| KKCSheetTracker Android | Reads shared Ready Jobs metadata, writes tablet progress/actions, crash reports, local app state | `C:\Scripts\KKCSheetTracker` |
| Ready Jobs Watcher | Publishes per-job parse/cache metadata that tablets consume | `C:\Scripts\Ready Jobs Watcher` |
| Hours Tracker | Manages digital hours/admin metadata and some global Ready Jobs admin files | `C:\Scripts\Hours Tracker` |
| timeclock-hub | RTC-1000 punch clock REST hub and SQLite source of truth for punch-clock timeclock | `C:\Scripts\timeclock-hub` |
| updater-agent | Android helper for installs/silent update behavior | `C:\Scripts\KKCSheetTracker\updater-agent` |

Shared Ready Jobs usually appears on the PC as `Y:\Ready Jobs` and on the server as `\\192.168.1.15\KKC Jobs\Ready Jobs`.

## Ownership Map

| Metadata / Path Pattern | Owner | First Debug Check |
|---|---|---|
| `Y:\Ready Jobs\<job>\.metadata\deployment_gate.json` | Ready Jobs Watcher | Inspect `deployed`, `parseReady`, `hiddenFromProduction`, then `ready_jobs_watcher.log` |
| `Y:\Ready Jobs\<job>\.metadata\cache_static.json` | Ready Jobs Watcher | Check mtime/content, then `metadata_cache.py` and watcher logs |
| `Y:\Ready Jobs\<job>\CNC\.metadata\<pdf-stem>.json` | Ready Jobs Watcher | Check sidecar exists for each CNC PDF and parse errors in `cnc_scan.log` |
| `Y:\Ready Jobs\<job>\CNC\.metadata\remake_bad_parts_candidates.json` | Ready Jobs Watcher | Check CNC scan log and scheduled cache refresh entries |
| `Y:\Ready Jobs\<job>\CNC\.tracker\<tablet>.json` | KKCSheetTracker tablets | Legacy CNC sheet/action state written by tablet |
| `Y:\Ready Jobs\<job>\CNC\.tracker\events\**\*.ndjson` | KKCSheetTracker tablets; Ready Jobs Watcher reads | Migrated CNC action stream; prefer over legacy JSON when present |
| `Y:\Ready Jobs\<job>\CNC\.tracker\consolidated.json` | Ready Jobs Watcher | Check tracker action stream/reconcile logs |
| `Y:\Ready Jobs\<job>\CNC\.tracker\watcher_refresh_watcher.json` | Ready Jobs Watcher | Refresh heartbeat; not source-of-truth progress |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\cutlist_index.json` | Ready Jobs Watcher | Compare against hardwood source files and watcher logs |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\cutlist_revisions.json` | Ready Jobs Watcher | Check revision state before blaming tablet UI |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\board_stock_manual.json` | Hours Tracker/admin or manual source; Ready Jobs Watcher reads | Manual board-stock input folded into `cache_static.json` |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\<tablet>.json` | KKCSheetTracker tablets | Hardwood completion/progress state |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\events\**\*.ndjson` | KKCSheetTracker tablets; Ready Jobs Watcher reads | Migrated hardwood action stream |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\<tablet>.markup.json` | KKCSheetTracker tablets | Hardwood ink/PDF markup state |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\.board_stock_*_<tablet>.json` | KKCSheetTracker tablets | Hardwood board-stock migration markers |
| `Y:\Ready Jobs\<job>\.metadata\hardwoods\.tracker\watcher_refresh_watcher.json` | Ready Jobs Watcher | Hardwood refresh heartbeat |
| `Y:\Ready Jobs\<job>\.metadata\cabinet_sheet_index.json` | Ready Jobs Watcher | Check `cabinet_sheet_indexer.py` and root PDF mtimes |
| `Y:\Ready Jobs\<job>\.metadata\pdf_markup\.tracker\<tablet>.markup.json` | KKCSheetTracker tablets | Root/reference PDF markup |
| `Y:\Ready Jobs\<job>\.metadata\pdf_markup\.tracker\<tablet>.json` | KKCSheetTracker tablets | Legacy PDF markup fallback |
| `Y:\Ready Jobs\.metadata\crashes\*.json` | KKCSheetTracker Android | Read latest crash JSON, then match app version and route/screen |
| `Y:\Ready Jobs\.metadata\material_mappings.json` | Hours Tracker/admin workflow; KKCSheetTracker reads | Shared material mapping for door-panel/specialty automation |
| `Y:\Ready Jobs\.metadata\themes\active_theme.json`, `themes\*.json`, `themes\graphics\*.svg` | Admin/theme publishing; KKCSheetTracker reads | Global tablet theme and graphics assets |
| `Y:\Ready Jobs\.metadata\timeclock_messages.json` | Admin/global message workflow; KKCSheetTracker reads | Global shop/tablet timeclock messages |
| `Y:\Ready Jobs\.metadata\sync_conflicts\<id>\manifest.json` | Ready Jobs Watcher | Root/global Syncthing conflict archive manifest |
| `Y:\Ready Jobs\production_order.json` | Hours Tracker/admin workflow; Ready Jobs Watcher reads it | Check Hours Tracker admin state, then cache refresh into jobs |
| `Y:\Ready Jobs\job_board.json` | Hours Tracker/admin workflow | Check Hours Tracker admin UI/backend first |
| `Y:\Ready Jobs\.metadata\delivery_schedule.json` | Hours Tracker/admin workflow | Check Hours Tracker backend/admin paths |
| `Y:\Ready Jobs\.supply\categories.json` | Hours Tracker/admin workflow; tablets can read/write status | Supply category list/order |
| `Y:\Ready Jobs\.supply\schema.json` | Hours Tracker/admin workflow | Custom supply field schema |
| `Y:\Ready Jobs\.supply\items\<itemId>.json` | Hours Tracker/admin workflow; tablets can create/update | Supply item record |
| `Y:\Ready Jobs\.supply\status\<itemId>.<device>.json` | KKCSheetTracker tablets and Hours Tracker admin | Per-device supply item status |
| `Y:\Ready Jobs\.supply\comments\<itemId>\<commentId>.json` | KKCSheetTracker tablets and Hours Tracker admin | Supply item comments |
| `Y:\Ready Jobs\.supply\attachments\<itemId>\*` | Hours Tracker/admin workflow | Supply item uploaded attachments |
| `Y:\Ready Jobs\<job>\.metadata\admin\rip_items.json` | Hours Tracker/admin workflow | Check Hours Tracker admin state before Android |
| `Y:\Ready Jobs\<job>\.metadata\admin\checklist.json` | Hours Tracker/admin workflow | Check Hours Tracker admin state |
| `Y:\Ready Jobs\<job>\.metadata\admin\rule_applications.json` | Hours Tracker/admin workflow | Check Hours Tracker admin rule code |
| `Y:\Ready Jobs\<job>\.metadata\admin\board_stock.json` | Hours Tracker/admin workflow | Check Hours Tracker board stock/admin paths |
| `Y:\Ready Jobs\<job>\.metadata\admin\specialty_items.json` | Hours Tracker/admin workflow; KKCSheetTracker may patch item fields | Check admin state and tablet specialty progress writes |
| `Y:\Ready Jobs\<job>\.metadata\admin\.tracker\<tablet>.json` | KKCSheetTracker tablets | Specialty item/station completion state |
| `Y:\Ready Jobs\<job>\.metadata\admin\tablet_items_<tablet>.json` | KKCSheetTracker tablets | Tablet-created specialty items |
| `Y:\Ready Jobs\<job>\.metadata\admin\sheet_rip_done.json` | KKCSheetTracker tablets and Hours Tracker/admin | Manual sheet-rip completion state |
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
| Specialty/admin items wrong | Hours Tracker admin files, then KKCSheetTracker specialty repository |
| PDF markup missing | `.metadata\pdf_markup\.tracker\<tablet>.markup.json`, then tablet app version |
| Supply item/status wrong | `.supply\items`, `.supply\status`, `.supply\comments`, then Hours Tracker supply backend |
| Production order/lineup wrong | `production_order.json`, Hours Tracker admin, then Ready Jobs Watcher cache refresh |
| Delivery schedule wrong | `Y:\Ready Jobs\.metadata\delivery_schedule.json`, Hours Tracker |
| Digital hours wrong | `.time_cards\<Employee>\<week>.json`, locks, `pending_edits.json` |
| Badge/profile/shop wrong | `.time_cards\<Employee>\profile.json`, `badges_config.json`, locks |
| Punch-clock timeclock wrong | `C:\Scripts\timeclock-hub\data\timeclock.db`, hub logs, not Hours Tracker |
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
| How are cabinet/sheet indexes generated? | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\cabinet_sheet_indexer.py` |
| How does Hours Tracker sync JSON to reporting DB? | `C:\Scripts\Hours Tracker\backend\db.py` |
| What API serves Hours Tracker admin data? | `C:\Scripts\Hours Tracker\backend\main_v2.py` |
| What frontend calls Hours Tracker APIs? | `C:\Scripts\Hours Tracker\frontend\lib\api_kkc.ts` |
| How does RTC punch clock work? | `C:\Scripts\timeclock-hub\app.py` |
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
| Searching all hidden Syncthing folders as jobs | Filter to real job folders like `<jobnum> - <name>` |
| Treating thumbnails/fullimages as source metadata | They are render caches; debug source JSON first |
| Assuming mDNS should always work for timeclock | Current hub may have mDNS disabled; use manual/default IP checks |
