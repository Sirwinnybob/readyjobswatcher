---
name: debug-android-tablet
description: Use when debugging KKCSheetTracker or updater-agent Android behavior, crashes, installs, UI failures, sync problems, timeclock issues, or any situation where a shop tablet is connected over ADB.
---

# Debug Android Tablet

## Overview

Debug from evidence on the connected tablet first. Always collect ADB state, logs, app data clues, and the matching code path before proposing fixes.

## First Moves

Run these before editing code:

```powershell
adb devices -l
adb shell getprop ro.product.model
adb shell getprop ro.build.version.release
adb shell dumpsys package com.kkc.sheettracker | Select-String version
adb shell pidof com.kkc.sheettracker
```

If multiple devices are attached, use `adb -s <serial> ...` for every command.

## Crash Or Freeze

1. Check local crash reports first:
   - Tablet app writes to `{basePath}/.metadata/crashes`
   - On the PC, usually `Y:\Ready Jobs\.metadata\crashes`
   - If shared storage was unavailable, reports may flush there after the next successful launch.
2. Pull current fatal logs:

```powershell
adb logcat -d -v time AndroidRuntime:E KKC_CRASH_REPORTER:* KKC_APP_STATE:* KKC_NAV:* *:S
```

3. For recent full context, capture a wider log:

```powershell
adb logcat -d -v time | Select-String "kkc|sheettracker|AndroidRuntime|FATAL|Exception|ANR|SQLite|Syncthing|timecard|timeclock"
```

4. For freezes or ANRs:

```powershell
adb shell dumpsys activity anr
adb shell dumpsys activity processes | Select-String com.kkc.sheettracker
adb shell dumpsys meminfo com.kkc.sheettracker
```

## Install Or Version Problems

Use the repo build path from `AGENTS.md`:

```powershell
.\gradlew.bat assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
adb shell monkey -p com.kkc.sheettracker 1
```

Verify installed versions:

```powershell
adb shell dumpsys package com.kkc.sheettracker | Select-String "versionName|versionCode|firstInstallTime|lastUpdateTime"
adb shell dumpsys package com.kkc.updateragent | Select-String "versionName|versionCode|firstInstallTime|lastUpdateTime"
```

If silent updates are involved, inspect both app logs and updater-agent logs:

```powershell
adb logcat -d -v time | Select-String "UpdateManager|updateragent|PackageInstaller|DeviceOwner|Fallback"
```

## App State And Storage

Confirm the app can see the expected shared data:

```powershell
adb shell ls -la "/storage/emulated/0/Ready Jobs"
adb shell ls -la "/storage/emulated/0/SyncJobs/Ready Jobs"
adb shell ls -la "/storage/emulated/0/Ready Jobs/.metadata"
```

## Metadata Owners

Do not assume the Android app created Ready Jobs metadata. Check the owner that publishes the file:

| Metadata | Owner / First Place To Look |
|---|---|
| Job visibility gate `.metadata/deployment_gate.json` | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\deployment_gate.py` |
| Static tablet cache `.metadata/cache_static.json` | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\metadata_cache.py` |
| CNC sidecars `CNC\.metadata\<pdf-stem>.json` | Ready Jobs Watcher |
| Cabinet index `.metadata\cabinet_sheet_index.json` | `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher\cabinet_sheet_indexer.py` |
| CNC tracker consolidation `CNC\.tracker\consolidated.json` | Ready Jobs Watcher tracker action stream |
| Hardwoods index/revisions `.metadata\hardwoods\*.json` | Ready Jobs Watcher |
| `production_order.json`, `job_board.json` | Hours Tracker/admin workflow; Ready Jobs Watcher reads these into cache lineup fields |
| Delivery schedule `.metadata\delivery_schedule.json` | Hours Tracker/admin workflow |
| Supply data `.supply\...` | Hours Tracker/admin workflow |
| Specialty/admin items `.metadata\admin\specialty_items.json`, `checklist.json`, `rip_items.json`, `board_stock.json` | Hours Tracker/admin workflow |
| Digital hours `.time_cards\...` | `C:\Scripts\Hours Tracker` |

Ready Jobs Watcher watches `\\192.168.1.15\KKC Jobs\Ready Jobs`. The PC usually sees the same share as `Y:\Ready Jobs`.

Important caveat: Hours Tracker intentionally does **not** own `<job>\.metadata\cache_static.json` except an emergency legacy mode. If KKCSheetTracker job lists/materials are stale, check Ready Jobs Watcher cache publication before Hours Tracker.

Useful repo locations:

| Symptom | Start Reading |
|---|---|
| Startup, permissions, migration, update prompt | `app/src/main/java/com/kkc/sheettracker/MainActivity.kt` |
| Crash report writing | `app/src/main/java/com/kkc/sheettracker/crash/` |
| Navigation/current screen | `app/src/main/java/com/kkc/sheettracker/navigation/` |
| CNC job scan/cache | `data/ScanCoordinator.kt`, `data/unified/FileBackedUnifiedMetadataEngine.kt` |
| Hardwoods | `data/HardwoodsRepository.kt`, `ui/hardwoods/` |
| Specialty | `data/SpecialtyRepository.kt`, `data/SpecialtyProgressStore.kt`, `ui/specialty/` |
| Timeclock tablet UI | `ui/timecard/`, `data/TimecardRepository.kt`, `data/TimecardDiscovery.kt` |
| Timeclock hub | `C:\Scripts\timeclock-hub\app.py` |
| Ready Jobs Watcher metadata | `C:\Scripts\Ready Jobs Watcher`, especially `ready_jobs_watcher.log`, `cnc_scan.log`, `pending_queue.json`, `config.json` |
| Hours Tracker metadata | `C:\Scripts\Hours Tracker`, especially `backend/main_v2.py`, `backend/db.py`, `config.json` |
| Syncthing | `sync/SyncthingSupervisor.kt`, `sync/DataStoreSyncthingPreferencesStore.kt` |
| PDF markup/viewer | `data/PdfMarkupStore.kt`, `ui/markup/`, `ui/viewer/` |
| Updater agent | `updater-agent/src/main/java/com/kkc/updateragent/update/` |

## Ready Jobs Watcher Checks

For missing or stale jobs/materials on KKCSheetTracker tablets, check in this order:

```powershell
Get-Content "Y:\Ready Jobs\<job>\.metadata\deployment_gate.json"
Get-Item "Y:\Ready Jobs\<job>\.metadata\cache_static.json"
Get-ChildItem "Y:\Ready Jobs\<job>\CNC\.metadata" -Filter *.json
Get-Content "C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.log" -Tail 200
Get-Content "C:\Scripts\Ready Jobs Watcher\cnc_scan.log" -Tail 200
Get-Content "C:\Scripts\Ready Jobs Watcher\pending_queue.json"
```

Key fields in `deployment_gate.json`: `deployed`, `parseReady`, `hiddenFromProduction`, `selectedMode`, `modeDetection`, `timers`. Missing or false `deployed` hides the job. False `parseReady` means released but still parsing. `hiddenFromProduction=true` hides production builds.

`metadata_cache_debounce_seconds` may be around 600 seconds in `C:\Scripts\Ready Jobs Watcher\config.json`, so some stale cache behavior can be normal for several minutes after source changes.

## Hours Tracker Checks

For digital hours/timecard behavior, remember this is separate from `C:\Scripts\timeclock-hub` and uses a different Android package, `com.example.timecard`.

Primary source of truth:

```powershell
Get-ChildItem "Y:\Ready Jobs\.time_cards"
Get-Content "Y:\Ready Jobs\.time_cards\employees.json"
Get-Content "Y:\Ready Jobs\.time_cards\<Employee>\<YYYY-MM-DD>.json"
```

Look for locks and queued admin edits:

```powershell
Get-ChildItem "Y:\Ready Jobs\.time_cards\<Employee>" -Filter *.lock
Get-ChildItem "Y:\Ready Jobs\.time_cards\.locks"
Get-Content "Y:\Ready Jobs\.time_cards\pending_edits.json"
Get-Content "Y:\Ready Jobs\.time_cards\loaded_cards.json"
```

Backend/API entry points:

| Item | Path |
|---|---|
| FastAPI service | `C:\Scripts\Hours Tracker\backend\main_v2.py` |
| Reporting DB sync/cache | `C:\Scripts\Hours Tracker\backend\db.py` |
| Config | `C:\Scripts\Hours Tracker\config.json`, `backend\config.py` |
| Admin frontend API client | `C:\Scripts\Hours Tracker\frontend\lib\api_kkc.ts` |
| Local reporting cache | `%APPDATA%\TimeCardTracker\hours.db` |
| Docker service | `hourtracker`, port `5002` |

Useful endpoints include `/admin/sync-status`, `/digital-timecards-list`, `/digital-timecard-detail`, `/pending-edits`, `/loaded-cards`, `/stats`, and `/recent-entries`.

## Debugging Rules

- Do not guess from code alone when a tablet is connected.
- Keep the first diagnosis evidence-based: exact tablet model, app version, route/screen, logs, and crash JSON if present.
- Reproduce once if safe; clear logs before reproducing when noise is high:

```powershell
adb logcat -c
```

- After identifying a root cause, add or update the smallest relevant test, then run the narrow test before the full build.
- Before claiming fixed, run the relevant verification:

```powershell
.\gradlew.bat app:testDebugUnitTest
.\gradlew.bat assembleDebug
```

## Common Mistakes

| Mistake | Better Move |
|---|---|
| Reading code first for a live tablet issue | Pull crash JSON and logcat first |
| Ignoring version drift | Compare installed `versionName/versionCode` to `app/build.gradle.kts` |
| Treating missing jobs as parser bugs | Check Ready Jobs Watcher, `.metadata/deployment_gate.json`, and `cache_static.json` first |
| Blaming Hours Tracker for stale KKCSheetTracker job cache | Hours Tracker does not normally write `cache_static.json`; check Ready Jobs Watcher |
| Confusing hours systems | `C:\Scripts\timeclock-hub` is RTC punch clock; `C:\Scripts\Hours Tracker` is digital timecard/admin + `com.example.timecard` |
| Debugging timeclock only in Android | Check hub health/logs and `C:\Scripts\timeclock-hub\app.py` too |
| Forgetting updater-agent | Inspect `com.kkc.updateragent` when installs or silent updates are involved |
