# Ready Jobs Singleton and Syncthing Conflict Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure only one Ready Jobs Watcher instance performs work on the Windows host and prevent the watcher from turning Syncthing's in-flight temporary files into repeated conflict archives.

**Architecture:** Replace the current check-then-write PID file with a Windows kernel-owned named mutex acquired before watcher startup. Keep the PID file only as diagnostic state, make daily restart replace the current process in place, and configure Task Scheduler to ignore duplicate launches. Make conflict handling explicitly ignore Syncthing/internal/temp paths, centralize resolution in one event path, and use a short stability check for genuine conflicts; retain the periodic scan as a recovery sweep.

**Tech Stack:** Python 3.13, Windows `ctypes` Win32 API, PyQt6, watchdog, pytest, PowerShell Task Scheduler, Syncthing-backed SMB share.

## Global Constraints

- Planning-only until explicit implementation approval; do not run deployment, stop processes, change Task Scheduler, or mutate `Y:\\Ready Jobs` during planning.
- Preserve unrelated dirty work in `C:\\Scripts\\Ready Jobs Watcher`; do not reset, stash, or rewrite unrelated files.
- The named mutex is the authoritative singleton; `ready_jobs_watcher.lock` remains diagnostic only.
- A duplicate launch must exit before creating observers, schedulers, GUI state, or duplicate logging handlers.
- Syncthing internal files (`.syncthing*`), dot-prefixed internal files, `*.tmp`, and `*.tmp-*` must never be restored or archived by the conflict resolver.
- Genuine conflict copies must remain recoverable; no existing `Y:\\Ready Jobs\\.metadata\\sync_conflicts` archive is deleted.
- Keep the existing per-tablet request contract: `delivery_schedule_request.<tabletId>.json` is written by KKCSheetTracker and consumed by Hours Tracker.
- Use PowerShell-safe `-LiteralPath` handling for paths with spaces.

---

### Task 1: Add a kernel-owned single-instance guard

**Files:**
- Create: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\single_instance.py`
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\main.py`
- Test: `C:\\Scripts\\Ready Jobs Watcher\\tests\\test_single_instance.py`

**Interfaces:**
- `SingleInstanceGuard(name: str = "Global\\\\KKC_ReadyJobsWatcher_SingleInstance", diagnostic_path: Path | None = None)`
- `acquire() -> bool`
- `release() -> None`
- `Application.acquire_lock()` and `Application.release_lock()` remain compatibility wrappers delegating to the guard.

- [ ] **Step 1: Write failing tests**

~~~python
def test_second_guard_cannot_acquire_same_named_mutex():
    first = SingleInstanceGuard(name="Local\\KKC_test_singleton")
    second = SingleInstanceGuard(name="Local\\KKC_test_singleton")
    assert first.acquire() is True
    assert second.acquire() is False
    second.release()
    first.release()


def test_release_does_not_delete_another_process_diagnostic_pid(tmp_path):
    diagnostic = tmp_path / "ready_jobs_watcher.lock"
    guard = SingleInstanceGuard(
        name="Local\\KKC_test_release_owner",
        diagnostic_path=diagnostic,
    )
    assert guard.acquire() is True
    diagnostic.write_text("999999", encoding="ascii")
    guard.release()
    assert diagnostic.read_text(encoding="ascii") == "999999"
~~~

Mark real Win32 tests skipped on non-Windows; do not replace them with a PID-file simulation.

- [ ] **Step 2: Run and verify failure**

~~~powershell
cd 'C:\Scripts\Ready Jobs Watcher'
.\.venv\Scripts\python.exe -m pytest tests/test_single_instance.py -q
~~~

Expected: FAIL because `SingleInstanceGuard` does not exist.

- [ ] **Step 3: Implement the mutex guard**

Use `ctypes.WinDLL("kernel32", use_last_error=True)` with `CreateMutexW`, `GetLastError`, and `CloseHandle`. Use `ERROR_ALREADY_EXISTS = 183`. If the named mutex already exists, close the returned handle and return `False`; never check and overwrite a shared PID file to acquire ownership.

Write the diagnostic PID only after the mutex is owned, using a temporary sibling and `os.replace`. On release, close only this process's handle and remove the PID file only when it still contains `os.getpid()`.

- [ ] **Step 4: Run focused tests**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_single_instance.py -q
~~~

Expected: all singleton tests pass on Windows.

- [ ] **Step 5: Commit**

~~~powershell
git add -- 'ready_jobs_watcher/single_instance.py' 'ready_jobs_watcher/main.py' 'tests/test_single_instance.py'
git commit -m "fix: enforce watcher singleton with mutex"
~~~

---

### Task 2: Acquire before startup and make restart in-place

**Files:**
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\__main__.py`
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\main.py:745-755`
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\main.py:861-918`
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\tests\\test_main_observer_resilience.py`
- Test: `C:\\Scripts\\Ready Jobs Watcher\\tests\\test_main_entrypoint_singleton.py`

**Interfaces:**
- `__main__.py` acquires the guard before `setup_logging()`.
- `Application(instance_guard: SingleInstanceGuard | None = None)` accepts the already-acquired guard and supports direct/test construction.
- `Application.restart()` must not `Popen` a replacement watcher.

- [ ] **Step 1: Add failing startup/restart tests**

Reuse the existing `_build_minimal_app()` fixture pattern from `tests/test_main_observer_resilience.py`, adding `instance_guard` to the constructed object for the startup test.

~~~python
def test_duplicate_application_refuses_to_start_before_workers(monkeypatch):
    app = _build_minimal_app()
    app.instance_guard = FakeGuard(acquired=False)
    clear_logs = Mock()
    start_threads = Mock()
    monkeypatch.setattr("ready_jobs_watcher.main.clear_old_logs", clear_logs)
    monkeypatch.setattr(app, "start_threads", start_threads)
    with pytest.raises(SystemExit) as exc:
        app.start()
    assert exc.value.code == 0
    clear_logs.assert_not_called()
    start_threads.assert_not_called()


def test_restart_executes_in_place_after_stopping(monkeypatch):
    app = _build_minimal_app()
    app.release_lock = Mock()
    execv = Mock()
    monkeypatch.setattr("ready_jobs_watcher.main.os.execv", execv)
    app.restart()
    app.release_lock.assert_called_once()
    execv.assert_called_once_with(sys.executable, [sys.executable, *sys.argv])
~~~

Update the existing restart failure test to expect a failed `os.execv`, not a failed `subprocess.Popen`.

- [ ] **Step 2: Run and verify failure**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_observer_resilience.py tests/test_main_entrypoint_singleton.py -q
~~~

Expected: FAIL because startup configures logging before acquisition and restart spawns a child.

- [ ] **Step 3: Wire startup and restart**

In `__main__.py`, construct/acquire `SingleInstanceGuard` before `setup_logging()`. Exit with code 0 on a duplicate. Pass the guard into `Application` and release it exactly once in shutdown.

In `Application.start()`, retain a defensive acquisition path for direct callers, but perform it before `clear_old_logs()`, threads, observers, or Qt state.

In `Application.restart()`:

1. Set `stop_event` and stop/join observers and worker threads.
2. Hide/close tray and Qt state.
3. Release the mutex and diagnostic PID.
4. Replace the current process image with `os.execv(sys.executable, [sys.executable, *sys.argv])`.
5. Alert and exit nonzero if replacement fails.

This removes the current parent/child restart overlap observed with PIDs 3064 and 44572.

- [ ] **Step 4: Run focused tests**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_observer_resilience.py tests/test_main_entrypoint_singleton.py tests/test_config.py -q
~~~

Expected: PASS with no `Popen`-based restart assertions.

- [ ] **Step 5: Commit**

~~~powershell
git add -- 'ready_jobs_watcher/__main__.py' 'ready_jobs_watcher/main.py' 'tests/test_main_observer_resilience.py' 'tests/test_main_entrypoint_singleton.py'
git commit -m "fix: prevent duplicate watcher startup and restart"
~~~

---

### Task 3: Make the scheduled launcher reject duplicates

**Files:**
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\INSTALLATION.md`
- Create: `C:\\Scripts\\Ready Jobs Watcher\\tools\\verify_ready_jobs_launcher.ps1`
- Test: `C:\\Scripts\\Ready Jobs Watcher\\tests\\test_launcher_policy_documentation.py`

**Interfaces:**
- `ReadyJobsWatcher` is the supported launcher.
- The legacy `Ready Jobs Watcher` task remains disabled.
- The PowerShell audit is read-only by default.

- [ ] **Step 1: Add failing documentation test**

Assert that `INSTALLATION.md` contains `ReadyJobsWatcher`, `MultipleInstancesPolicy=IgnoreNew`, the legacy-task restriction, and the named mutex.

- [ ] **Step 2: Run and verify failure**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_launcher_policy_documentation.py -q
~~~

Expected: FAIL until the launcher policy is documented.

- [ ] **Step 3: Add the read-only audit**

The script must report task actions, triggers, duplicate watcher processes, and the diagnostic PID using:

~~~powershell
Get-ScheduledTask -TaskName 'ReadyJobsWatcher'
Get-ScheduledTask -TaskName 'Ready Jobs Watcher' -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process
~~~

It must not contain `Stop-Process`, `Disable-ScheduledTask`, `Unregister-ScheduledTask`, `Remove-Item`, or other mutating commands.

- [ ] **Step 4: Document deployment policy**

Document the supported task, `IgnoreNew` policy, named mutex behavior, and the safe sequence: audit exact PIDs, stop only verified watcher PIDs during approved deployment, start `ReadyJobsWatcher`, then verify one process. State that the existing conflict archive is preserved.

- [ ] **Step 5: Run audit and tests**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_launcher_policy_documentation.py -q
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\verify_ready_jobs_launcher.ps1
~~~

Expected: documentation passes and the audit identifies exactly one supported launcher.

- [ ] **Step 6: Commit**

~~~powershell
git add -- 'INSTALLATION.md' 'tools/verify_ready_jobs_launcher.ps1' 'tests/test_launcher_policy_documentation.py'
git commit -m "docs: define one watcher launcher policy"
~~~

---

### Task 4: Exclude Syncthing and atomic-write temp conflicts

**Files:**
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\sync_conflict_resolver.py:25-49,126-183`
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\tests\\test_sync_conflict_resolver.py`

**Interfaces:**
- Add `is_transient_conflict_path(path) -> bool`.
- Return `True` for conflicts whose derived original is under `.metadata\\sync_conflicts` or `.stversions`, dot-prefixed/internal, starts with `.syncthing`, or ends in `.tmp`/`.tmp-*`.
- `resolve_sync_conflict_file()` returns `None` without moving transient files.
- `_iter_conflicts()` does not yield transient files.

- [ ] **Step 1: Add failing tests**

~~~python
def test_syncthing_internal_temp_conflict_is_ignored(tmp_path):
    conflict = tmp_path / (
        ".syncthing.delivery_schedule_request.SM-X808U-6448."
        "sync-conflict-20260720-073405-2E2GGMF.json.tmp"
    )
    conflict.write_bytes(b"")
    result = resolve_sync_conflict_file(conflict, tmp_path)
    assert result is None
    assert conflict.exists()
    assert not list((tmp_path / ".metadata" / "sync_conflicts").rglob("manifest*.json"))


def test_regular_request_conflict_is_still_archived(tmp_path):
    original = tmp_path / "delivery_schedule_request.tablet-a.json"
    conflict = tmp_path / "delivery_schedule_request.tablet-a.sync-conflict-20260720-073405-ABC.json"
    original.write_text('{"requestedAt":"old"}', encoding="utf-8")
    conflict.write_text('{"requestedAt":"new"}', encoding="utf-8")
    result = resolve_sync_conflict_file(conflict, tmp_path)
    assert result is not None
    assert result.action == "archived_divergent"
    assert original.exists()
    assert not conflict.exists()
~~~

- [ ] **Step 2: Run and verify failure**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sync_conflict_resolver.py -q
~~~

Expected: the internal-temp test fails because the current resolver accepts the path.

- [ ] **Step 3: Implement filtering**

Derive the original path first, then reject only transient/internal paths. Apply the predicate both at the public resolver entrypoint and recursive scanning so event-driven and scheduled paths behave identically. Do not reject ordinary `delivery_schedule_request.<tablet>.json` conflicts.

- [ ] **Step 4: Add a stable-file guard**

For non-transient conflicts, sample `(st_size, st_mtime_ns)` twice over a short bounded interval. If the sample changes or the file disappears, return `None\) and leave it for the next event/sweep. Do not require nonzero size because a genuine empty file is valid content.

- [ ] **Step 5: Run resolver tests**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sync_conflict_resolver.py -q
~~~

Expected: temp conflicts remain untouched and genuine conflicts retain archive/restore behavior.

- [ ] **Step 6: Commit**

~~~powershell
git add -- 'ready_jobs_watcher/sync_conflict_resolver.py' 'tests/test_sync_conflict_resolver.py'
git commit -m "fix: ignore Syncthing temporary conflict files"
~~~

---

### Task 5: Deduplicate event-driven conflict resolution

**Files:**
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\watchers.py:178-188,266-276,799-809`
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\ready_jobs_watcher\\sync_conflict_resolver.py`
- Modify: `C:\\Scripts\\Ready Jobs Watcher\\tests\\test_sync_conflict_watcher.py`

- [ ] **Step 1: Add failing handler tests**

Dispatch the same conflict-created event through both handlers, mock the resolver, and assert it is called once. Also assert that a `.syncthing...tmp` event is ignored by both handlers.

- [ ] **Step 2: Run and verify failure**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sync_conflict_watcher.py tests/test_sync_conflict_resolver.py -q
~~~

Expected: the duplicate-call test fails because both handlers currently resolve the same event.

- [ ] **Step 3: Centralize and guard resolution**

Keep conflict dispatch in `RenameHandler` for created/moved events. In `PdfChangeHandler`, detect conflict paths and return without resolving them. Add a process-wide `threading.Lock` and normalized-path in-flight set around resolver calls, removing the path in `finally`. Run the transient predicate before acquiring the in-flight slot.

- [ ] **Step 4: Run focused tests**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sync_conflict_watcher.py tests/test_sync_conflict_resolver.py -q
~~~

Expected: one resolver call per genuine event and no handler activity for Syncthing temp conflicts.

- [ ] **Step 5: Commit**

~~~powershell
git add -- 'ready_jobs_watcher/watchers.py' 'ready_jobs_watcher/sync_conflict_resolver.py' 'tests/test_sync_conflict_watcher.py'
git commit -m "fix: deduplicate conflict event handling"
~~~

---

### Task 6: Full verification and controlled live deployment

**Files:**
- Modify only files from Tasks 1-5 if test findings require it.
- Do not modify existing live files under `Y:\\Ready Jobs\\.metadata\\sync_conflicts`.

- [ ] **Step 1: Run the complete suite and build**

~~~powershell
cd 'C:\Scripts\Ready Jobs Watcher'
.\.venv\Scripts\python.exe -m pytest -q
.\build_light.bat
~~~

Expected: full pytest passes and `ReadyJobsWatcherLight.exe` rebuilds successfully.

- [ ] **Step 2: Review diff without discarding unrelated work**

~~~powershell
git status --short
git diff --check
git diff --stat HEAD
~~~

Confirm unrelated existing edits are preserved; resolve overlaps manually if implementation touches the same files.

- [ ] **Step 3: Audit launcher before stopping anything**

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\verify_ready_jobs_launcher.ps1
~~~

Record exact watcher PIDs and task actions. Do not stop a process based only on its name.

- [ ] **Step 4: Controlled deployment after explicit approval**

Stop only verified Ready Jobs Watcher PIDs, start the single `ReadyJobsWatcher` task, and preserve the existing conflict archive. This is a deployment action and is not authorized by this planning document alone.

- [ ] **Step 5: Verify one active instance**

~~~powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'Ready Jobs Watcher.*__main__\.py' } |
  Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine

Get-Content -LiteralPath 'C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.lock'
~~~

Expected: exactly one active watcher process and a matching diagnostic PID. A second manual launch must exit without starting observers.

- [ ] **Step 6: Verify the exact conflict fixture**

Run the resolver tests against the exact `.syncthing...json.tmp` shape and confirm it remains untouched with no new manifest.

- [ ] **Step 7: Monitor the next real request**

Record the request file, watcher log timestamps, and conflict archive count before and after the next tablet delivery-schedule request. Acceptance:

- no archive entry is created for `.syncthing...json.tmp`;
- no repeated `WinError 2`/`WinError 5` resolver loop occurs;
- a genuine final request conflict, if produced, is handled once;
- `.metadata\\delivery_schedule.json` is updated only by Hours Tracker.

- [ ] **Step 8: Commit the verified result**

~~~powershell
git add -- 'ready_jobs_watcher' 'tests' 'INSTALLATION.md' 'tools'
git commit -m "fix: harden Ready Jobs sync conflict handling"
~~~

Do not claim the live issue is fixed until the rebuilt launcher is deployed and live monitoring passes.

---

## Self-review

- The singleton authority is a kernel mutex, not a PID file.
- Startup, scheduled launches, manual launches, and daily restart are covered.
- The exact live `.syncthing...json.tmp` shape has a regression test.
- Genuine request conflicts remain supported.
- Duplicate Rename/PDF handler resolution is covered.
- Existing archives are preserved.
- Tests, build, process-count verification, and live artifact verification are separate gates.
- No implementation or deployment action is authorized by this plan alone.
