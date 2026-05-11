# Ready Jobs Watcher - Installation Guide

## Executable Location

After building with PyInstaller, the executable is located at:
```
C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe
```

## Quick Start

### Option 1: Manual Start
Double-click:
```
C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe
```

### Option 2: Windows Startup (Recommended)

1. Press `Win + R`, type `shell:startup`, press Enter.
2. Create a shortcut to:
   `C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe`

## Application Features

The Ready Jobs Watcher automatically:

1. File Processing
- Monitors `Y:\Ready Jobs` for new/renamed job folders
- Renames files with job number prefix
- Retries locked files automatically

2. PDF Dark Mode Conversion
- Runs on matching PDF updates
- Runs in background with cooldown controls

3. Bad Parts Detection
- Uses tracker mode by default
- Shows popup/toast/sound alerts for new active bad parts
- Runs startup and periodic reconcile scans

4. Automated Backups
- Runs on configured schedule
- Backs up configured folders
- Prunes old backups based on retention policy

5. System Tray + Settings
- Open settings from tray
- Manual actions: Backup Now, Scan CNC Now, Scan Ready Jobs Now

## Logs

Primary logs:
- `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.log`
- `C:\Scripts\Ready Jobs Watcher\backup.log`
- `C:\Scripts\Ready Jobs Watcher\cnc_scan.log`
- `C:\Scripts\Ready Jobs Watcher\bad_parts.log`
- `C:\Scripts\Ready Jobs Watcher\send_notification.log`

## Rebuild

From project root:
```bat
build.bat
```

## Uninstall

1. Remove startup shortcut/task if configured.
2. Quit from tray.
3. Delete `C:\Scripts\Ready Jobs Watcher`.
