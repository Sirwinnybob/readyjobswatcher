# Ready Jobs Watcher - Installation Guide

## Executable Location

The deployed build is the **light launcher**, produced by `build_light.bat`
(`ready_jobs_watcher_light.spec`). It is a thin PyInstaller wrapper that
launches the real app with the repo's own `.venv` (`pythonw.exe -m
ready_jobs_watcher`), rather than bundling every dependency into the exe —
rebuilds are fast because there is no `fitz`/`trimesh`/`collada` payload to
repackage. After building, it is located at:
```
C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcherLight.exe
```

There is a second, self-contained build (`build.bat` /
`ready_jobs_watcher.spec`) that bundles all dependencies directly, producing
`dist\ReadyJobsWatcher\ReadyJobsWatcher.exe`. It is not the build the
scheduled task runs; keep it around only if you need a copy that doesn't
depend on the repo's `.venv` being present (e.g. testing on another
machine). Day-to-day installs and the live Task Scheduler task use the
light launcher above.

## Quick Start

### Option 1: Manual Start
Double-click:
```
C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcherLight.exe
```

### Option 2: Windows Startup (Recommended)

1. Press `Win + R`, type `shell:startup`, press Enter.
2. Create a shortcut to:
   `C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcherLight.exe`

### Option 3: Task Scheduler (production deployment)

See [Launcher and Deployment Policy](#launcher-and-deployment-policy) below —
this is how the watcher actually runs on the production machine today.

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

## Launcher and Deployment Policy

**Exactly one Task Scheduler task is supported: `ReadyJobsWatcher`.** It
runs `dist\ReadyJobsWatcherLight.exe`, is currently `Ready` (enabled), and
is configured with `MultipleInstancesPolicy=IgnoreNew` — if Task Scheduler
finds the task's action already running when a trigger fires, it skips
starting a new instance instead of launching a second one.

There is an older task, literally named `Ready Jobs Watcher` (with spaces,
a distinct Task Scheduler task from `ReadyJobsWatcher`), which points at
the raw `ready_jobs_watcher.py` script path instead of the built exe. **This
legacy task must stay disabled.** It exists in Task Scheduler for historical
reasons only; do not re-enable it, and do not use it as a template for new
triggers. Only `ReadyJobsWatcher` is the supported launcher.

### Defense in depth: the named mutex

Task Scheduler's `IgnoreNew` policy prevents *Task Scheduler itself* from
double-launching the task, but it does not stop a second instance started
some other way (a manual double-click, a leftover Startup shortcut, a stuck
process from a previous deploy) from running concurrently. The application
guards against that independently: on startup, `ready_jobs_watcher.main`
acquires a Windows kernel-owned named mutex,
`Global\KKC_ReadyJobsWatcher_SingleInstance` (see
`ready_jobs_watcher/single_instance.py`, `SingleInstanceGuard`), before
touching logging, config, or creating any watchers/threads/GUI state. A
second launch — from any source, not just Task Scheduler — fails to
acquire the mutex and exits immediately with code `0` before doing any
work. This is the authoritative singleton check; the diagnostic PID file at
`C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.lock` is advisory only
(written for humans to see which PID currently holds the lock) and is never
read back to decide ownership.

### Safe deployment sequence

Before stopping or starting anything, audit the current state with the
read-only script:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\verify_ready_jobs_launcher.ps1
```
This reports the `ReadyJobsWatcher` and legacy `Ready Jobs Watcher` task
state/actions/triggers, every running watcher process with its exact PID,
and the diagnostic PID file content. It performs no mutating action — it
never stops a process, disables/unregisters a task, or deletes a file.

For an approved deployment (rebuild + redeploy), follow this order:

1. Run the audit script and record the exact watcher PID(s) currently
   running. Never stop a process based on name alone — confirm it's a
   verified Ready Jobs Watcher process from the audit output first.
2. Stop only those verified watcher PID(s).
3. Start the `ReadyJobsWatcher` scheduled task (or let its normal trigger
   fire).
4. Run the audit script again and confirm exactly one watcher process is
   running, with a diagnostic PID matching that single process.

The existing Syncthing conflict archive under
`Y:\Ready Jobs\.metadata\sync_conflicts` is never touched by any of the
above and must be preserved across deployments.

## Rebuild

Light launcher (what the scheduled task runs — rebuild this for normal
changes):
```bat
build_light.bat
```

Full self-contained build (only needed for a standalone copy that doesn't
depend on the repo's `.venv`):
```bat
build.bat
```

## Uninstall

1. Remove startup shortcut/task if configured.
2. Quit from tray.
3. Delete `C:\Scripts\Ready Jobs Watcher`.
