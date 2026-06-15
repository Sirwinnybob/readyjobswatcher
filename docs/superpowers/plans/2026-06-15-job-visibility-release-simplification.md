# Job Visibility & Release Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the redundant visibility/release/retry/remind controls with a single derived job state (PENDING/PARSING/ACTIVE), a state-aware double-click dialog, and one "Remind in N min" snooze — without changing the `deployment_gate.json` schema the Android app reads.

**Architecture:** Job state is *derived* from the existing `deployed`/`parseReady` booleans rather than stored. The desktop UI stops writing `hiddenFromProduction=True` and `retryAt`, and the now-unused backend methods are deleted. All per-job actions move into one state-aware dialog opened by double-clicking a dashboard row.

**Tech Stack:** Python 3.13, PyQt6, `unittest` + `pytest` (run via `python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-06-15-job-visibility-release-simplification-design.md`

**Hard constraint:** The on-disk `deployment_gate.json` schema must not change. Every field (`deployed`, `parseReady`, `hiddenFromProduction`, `selectedMode`, `modeDetection`, `timers.{retryAt,remindAt,autoReleaseAt,lastActionAt}`, etc.) stays present with the same name and type. Only *write-paths* and *UI* change.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `ready_jobs_watcher/deployment_gate.py` | Metadata read/write + state logic | Add `derive_state()`; stop stamping `hiddenFromProduction=True`; delete `schedule_retry()`, `set_hidden_from_production()` |
| `ready_jobs_watcher/main.py` | App-level job actions | Delete `retry_pending_job()`, `set_job_hidden_from_production()` |
| `ready_jobs_watcher/gui.py` | PyQt presentation | Rebuild Jobs tab table + toolbar; state-aware dialog; drop auto-release Undo button; QSS |
| `tests/test_deployment_gate.py` | Backend unit tests | Add `derive_state` tests; flip new-job hidden assertion; remove tests for deleted methods |

**GUI testing note:** `gui.py` changes are verified by (a) a headless import smoke check and (b) a manual checklist (Task 8). PyQt dialogs are not unit-tested in this repo. Backend tasks (1, 2, 6) use full TDD.

---

## Task 1: Add `derive_state()` helper (TDD)

**Files:**
- Modify: `ready_jobs_watcher/deployment_gate.py` (add module-level function near `load_job_gate_state`, ~line 335)
- Test: `tests/test_deployment_gate.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deployment_gate.py`. First add `derive_state` to the import block at the top (line 6-10):

```python
from ready_jobs_watcher.deployment_gate import (
    MODE_BOTH,
    MODE_UNKNOWN,
    DeploymentGateManager,
    derive_state,
)
```

Then add this test class above `if __name__ == "__main__":`:

```python
class TestDeriveState(unittest.TestCase):
    def test_pending_when_not_deployed(self):
        self.assertEqual(derive_state({"deployed": False, "parseReady": False}), "PENDING")
        self.assertEqual(derive_state({"deployed": False, "parseReady": True}), "PENDING")

    def test_parsing_when_deployed_but_not_parse_ready(self):
        self.assertEqual(derive_state({"deployed": True, "parseReady": False}), "PARSING")

    def test_active_when_deployed_and_parse_ready(self):
        self.assertEqual(derive_state({"deployed": True, "parseReady": True}), "ACTIVE")

    def test_missing_keys_default_to_active(self):
        # Existing/legacy gates default deployed=True, parseReady=True.
        self.assertEqual(derive_state({}), "ACTIVE")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_deployment_gate.py::TestDeriveState -v`
Expected: FAIL with `ImportError: cannot import name 'derive_state'`

- [ ] **Step 3: Implement `derive_state()`**

In `ready_jobs_watcher/deployment_gate.py`, add after the `load_job_gate_state` function (after line 336):

```python
def derive_state(state: Dict) -> str:
    """
    Derive a single presentation state from raw gate booleans.

    PENDING  -> not deployed (awaiting operator release)
    PARSING  -> deployed but parse not yet complete
    ACTIVE   -> deployed and parse complete (visible to production)

    Defaults match load_state defaults (deployed=True, parseReady=True) so a
    legacy gate with missing keys reads as ACTIVE.
    """
    if not bool(state.get("deployed", True)):
        return "PENDING"
    if not bool(state.get("parseReady", True)):
        return "PARSING"
    return "ACTIVE"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_deployment_gate.py::TestDeriveState -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/deployment_gate.py tests/test_deployment_gate.py
git commit -m "feat: derive job state (PENDING/PARSING/ACTIVE) from gate booleans"
```

---

## Task 2: Stop stamping `hiddenFromProduction=True` on new jobs (TDD)

New pending jobs are already invisible via `deployed=False`; the hide flag is redundant. The field stays in the JSON (default `False`).

**Files:**
- Modify: `ready_jobs_watcher/deployment_gate.py:205-206` (inside `ensure_pending_for_new_job`)
- Test: `tests/test_deployment_gate.py:25`

- [ ] **Step 1: Update the failing assertion**

In `tests/test_deployment_gate.py`, change line 25 inside `test_new_job_starts_pending_and_blocked`:

```python
            self.assertFalse(state["hiddenFromProduction"])
```

(was `self.assertTrue(...)`). The field must still be present and `False`.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_deployment_gate.py::TestDeploymentGateManager::test_new_job_starts_pending_and_blocked -v`
Expected: FAIL — `AssertionError: True is not false` (current code still sets it True)

- [ ] **Step 3: Remove the hide-stamping lines**

In `ready_jobs_watcher/deployment_gate.py`, inside `ensure_pending_for_new_job`, delete lines 205-206:

```python
        if not had_existing_state or not was_pending:
            state["hiddenFromProduction"] = True
```

The surrounding code (`state["deployed"] = False`, `state["parseReady"] = False`, mode detection, timers) stays. `_coerce_state` already defaults `hiddenFromProduction` to `False`, so the field remains present.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_deployment_gate.py::TestDeploymentGateManager::test_new_job_starts_pending_and_blocked -v`
Expected: PASS

Also confirm the schema round-trip still has the key:

Run: `python -c "import tempfile,os; from ready_jobs_watcher.deployment_gate import DeploymentGateManager as M; d=tempfile.mkdtemp(); os.makedirs(os.path.join(d,'j')); s=M(d).ensure_pending_for_new_job('j'); print('hiddenFromProduction' in s, s['hiddenFromProduction'])"`
Expected: `True False`

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/deployment_gate.py tests/test_deployment_gate.py
git commit -m "feat: stop stamping hiddenFromProduction on new pending jobs"
```

---

## Task 3: Rebuild the Jobs tab table + toolbar (GUI)

Replace the four boolean columns with a single State badge, color-code rows, trim the toolbar to Refresh, and wire double-click to open the dialog.

**Files:**
- Modify: `ready_jobs_watcher/gui.py` — `setup_jobs_tab` (~500-582), `_populate_jobs_table` (~663-690), `_sync_mode_combos_to_selected_row` (~723-729)

- [ ] **Step 1: Rebuild `setup_jobs_tab`**

Replace the body of `setup_jobs_tab` (lines 500-582) with:

```python
    def setup_jobs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info_label = QLabel(
            "Job state from each job's .metadata/deployment_gate.json. "
            "Double-click a row to release, snooze, or re-parse."
        )
        layout.addWidget(info_label)

        headers = [
            "Job",
            "State",
            "Selected Mode",
            "Detected Mode",
            "Mode Source",
            "Remind At",
            "Updated At",
        ]
        self.jobs_table = QTableWidget(0, len(headers), tab)
        self.jobs_table.setHorizontalHeaderLabels(headers)
        self.jobs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.setAlternatingRowColors(True)
        self.jobs_table.setSortingEnabled(False)
        self.jobs_table.itemDoubleClicked.connect(self._open_selected_job_dialog)
        header = self.jobs_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.jobs_table)

        actions = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_jobs_dashboard)
        actions.addWidget(refresh_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self.tabs.addTab(tab, "Jobs")
        self.refresh_jobs_dashboard()
```

This removes: the two mode comboboxes (`jobs_selected_mode_combo`, `jobs_detected_mode_combo`) and their Set buttons, and the Deploy/Re-parse/Retry/Remind/Hide/Unhide buttons. It removes the `itemSelectionChanged -> _sync_mode_combos_to_selected_row` wiring (replaced by double-click).

- [ ] **Step 2: Rebuild `_populate_jobs_table` with state badges + row tint**

Replace `_populate_jobs_table` (lines 663-690) with:

```python
    def _populate_jobs_table(self, rows: List[Dict]):
        if self.jobs_table is None:
            return
        from .deployment_gate import derive_state

        state_styles = {
            "PENDING": (QColor("#FEF3C7"), QColor("#92400E")),
            "PARSING": (QColor("#DBEAFE"), QColor("#1E40AF")),
            "ACTIVE":  (QColor("#D1FAE5"), QColor("#065F46")),
        }

        self.jobs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            timers = row.get("timers", {}) if isinstance(row.get("timers"), dict) else {}
            state_name = derive_state(row)
            bg, fg = state_styles.get(state_name, (None, None))

            values = [
                str(row.get("jobFolderName", "")),
                state_name,
                str(row.get("selectedMode", "UNKNOWN")),
                str(mode_detection.get("candidate", "UNKNOWN")),
                str(mode_detection.get("source", "UNKNOWN")),
                str(timers.get("remindAt") or "-"),
                str(row.get("updatedAt") or "-"),
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if bg is not None:
                    item.setBackground(bg)
                    item.setForeground(fg)
                self.jobs_table.setItem(row_index, col_index, item)
```

The `_sync_mode_combos_to_selected_row()` call at the end is removed (combos no longer exist).

- [ ] **Step 3: Delete now-dead helper methods**

Delete these methods from `gui.py` (they reference removed widgets/actions):
- `_sync_mode_combos_to_selected_row` (~723-729)
- `_selected_row_mode_value` (~707-721)
- `_set_selected_job_hidden` (~731-739)
- `_remind_selected_job` (~741-749)
- `_retry_selected_job` (~751-759)
- `_deploy_selected_job` (~761-766)
- `_reparse_selected_job` (~768-794) — its logic moves into the dialog in Task 4
- `_set_selected_mode_for_job` (~796-807)
- `_set_detected_mode_for_job` (~809-819)

Keep `_selected_job_folder_name` (~692-705) — still used.

- [ ] **Step 4: Add the double-click handler**

Add this method to the `SettingsWindow` class (place near `_selected_job_folder_name`):

```python
    def _open_selected_job_dialog(self, *args):
        job_folder_name = self._selected_job_folder_name()
        if not job_folder_name:
            return
        self._show_pending_job_prompt_dialog(job_folder_name)
```

- [ ] **Step 5: Remove the now-unused combo attributes**

Search `gui.py` for `jobs_selected_mode_combo` and `jobs_detected_mode_combo`. Remove any remaining initializer lines (e.g. `self.jobs_selected_mode_combo = None` in `__init__` if present). Run:

Run: `python -c "import ast,sys; ast.parse(open('ready_jobs_watcher/gui.py').read()); print('syntax ok')"`
Expected: `syntax ok`

Then grep to confirm no dangling references:

Run: `python -m pytest -q 2>&1 | tail -5` (ensures backend suite still green; gui not imported by tests)
Expected: existing tests pass.

- [ ] **Step 6: Headless import smoke check**

Run: `python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; from PyQt6.QtWidgets import QApplication; app=QApplication([]); import ready_jobs_watcher.gui as g; print('gui import ok')"`
Expected: `gui import ok` (if PyQt6 offscreen platform is available; if it errors on environment, note it and rely on Task 8 manual check)

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "feat: collapse Jobs tab to state badges with double-click dialog"
```

---

## Task 4: State-aware job action dialog (GUI)

The dialog adapts its action row to the job's derived state. PENDING jobs get Remind/Snooze/Release; released jobs (PARSING/ACTIVE) get Re-parse.

**Files:**
- Modify: `ready_jobs_watcher/gui.py` — `_show_pending_job_prompt_dialog` (~833-926)

- [ ] **Step 1: Replace the dialog action row**

Replace `_show_pending_job_prompt_dialog` (lines 833-926) with:

```python
    def _show_pending_job_prompt_dialog(self, job_folder_name: str):
        if not self.app_instance:
            return
        job_folder_name = str(job_folder_name or "").strip()
        if not job_folder_name:
            return

        from .deployment_gate import derive_state

        state = self._get_job_row_by_name(job_folder_name) or {}
        derived = derive_state(state)
        mode_detection = state.get("modeDetection", {}) if isinstance(state.get("modeDetection"), dict) else {}
        detected_mode = str(mode_detection.get("candidate") or "UNKNOWN")
        detected_source = str(mode_detection.get("source") or "UNKNOWN")
        selected_mode = str(state.get("selectedMode") or "UNKNOWN")
        default_mode = selected_mode if selected_mode and selected_mode != "UNKNOWN" else detected_mode
        if not default_mode:
            default_mode = "UNKNOWN"

        eyebrow = "New job pending" if derived == "PENDING" else f"Released job ({derived})"

        dialog = QDialog(self)
        dialog.setObjectName("jobActionDialog")
        dialog.setWindowTitle(f"Job: {job_folder_name}")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dialog.resize(540, 260)

        layout = QVBoxLayout(dialog)

        eyebrow_label = QLabel(eyebrow)
        eyebrow_label.setObjectName("dialogEyebrow")
        layout.addWidget(eyebrow_label)
        job_label = QLabel(job_folder_name)
        job_label.setObjectName("dialogJobName")
        layout.addWidget(job_label)
        layout.addWidget(QLabel(f"Detected mode: {detected_mode} ({detected_source})"))

        form = QFormLayout()
        mode_combo = QComboBox(dialog)
        mode_combo.setEditable(True)
        mode_combo.addItems(["FACE-FRAME", "FRAMELESS", "BOTH", "UNKNOWN"])
        mode_combo.setCurrentText(default_mode)
        if derived != "PENDING":
            mode_combo.setEnabled(False)
        form.addRow("Deploy Mode:", mode_combo)
        layout.addLayout(form)

        action_row = QHBoxLayout()

        if derived == "PENDING":
            remind_label = QLabel("Remind in")
            remind_spin = QSpinBox(dialog)
            remind_spin.setRange(1, 720)
            remind_spin.setValue(15)
            remind_spin.setSuffix(" min")
            snooze_btn = QPushButton("Snooze")
            cancel_btn = QPushButton("Cancel")
            release_btn = QPushButton("Release")
            release_btn.setObjectName("primaryButton")

            def _snooze_action():
                self.app_instance.remind_pending_job(job_folder_name, minutes=remind_spin.value())
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _release_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                import threading
                threading.Thread(
                    target=self.app_instance.deploy_pending_job,
                    args=(job_folder_name, selected),
                    daemon=True,
                ).start()
                self.refresh_jobs_dashboard()
                dialog.accept()

            snooze_btn.clicked.connect(_snooze_action)
            cancel_btn.clicked.connect(dialog.reject)
            release_btn.clicked.connect(_release_action)

            action_row.addWidget(remind_label)
            action_row.addWidget(remind_spin)
            action_row.addWidget(snooze_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)
            action_row.addWidget(release_btn)
        else:
            reparse_btn = QPushButton("Re-parse")
            cancel_btn = QPushButton("Cancel")

            def _reparse_action():
                reply = QMessageBox.question(
                    dialog,
                    "Re-parse Job",
                    f"Are you sure you want to fully re-parse job '{job_folder_name}'?\n\n"
                    "This will remove all generated metadata, GLBs, and dark mode PDFs, then re-process them.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    import threading
                    threading.Thread(
                        target=self.app_instance.reparse_job,
                        args=(job_folder_name,),
                        daemon=True,
                    ).start()
                    QMessageBox.information(
                        dialog,
                        "Re-parse Job",
                        f"Re-parsing for job '{job_folder_name}' has been started in the background.",
                    )
                    self.refresh_jobs_dashboard()
                    dialog.accept()

            reparse_btn.clicked.connect(_reparse_action)
            cancel_btn.clicked.connect(dialog.reject)

            action_row.addWidget(reparse_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)

        layout.addLayout(action_row)
        dialog.exec()
```

- [ ] **Step 2: Verify syntax + import**

Run: `python -c "import ast; ast.parse(open('ready_jobs_watcher/gui.py').read()); print('syntax ok')"`
Expected: `syntax ok`

Run: `python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; from PyQt6.QtWidgets import QApplication; app=QApplication([]); import ready_jobs_watcher.gui; print('gui import ok')"`
Expected: `gui import ok`

- [ ] **Step 3: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "feat: state-aware job dialog (release/snooze for pending, re-parse for released)"
```

---

## Task 5: Drop the auto-release "Undo (Re-Hide)" button (GUI)

`hiddenFromProduction` is retired, so re-hiding makes no sense. The dialog stays as an informational notice.

**Files:**
- Modify: `ready_jobs_watcher/gui.py` — `_show_auto_release_dialog` (~928-965)

- [ ] **Step 1: Replace the action row**

In `_show_auto_release_dialog`, replace the action-row block (lines 950-964) with:

```python
        action_row = QHBoxLayout()
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.clicked.connect(dialog.accept)
        action_row.addStretch()
        action_row.addWidget(dismiss_btn)
        layout.addLayout(action_row)
        dialog.exec()
```

This removes the `undo_btn`, `_undo_action`, and its call to the (soon-deleted) `set_job_hidden_from_production`.

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('ready_jobs_watcher/gui.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Confirm no GUI references to removed backend methods remain**

Run: `python -m pytest -q 2>&1 | tail -3` (backend suite unaffected)
Search the file manually for `set_job_hidden_from_production`, `retry_pending_job`, `_retry_selected_job`, `_set_selected_job_hidden` — there should be zero matches in `gui.py` now.

- [ ] **Step 4: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "feat: auto-release dialog becomes dismiss-only notice"
```

---

## Task 6: Delete unused backend methods (TDD)

Now that no caller remains, remove the dead methods and the tests that exercised them.

**Files:**
- Modify: `ready_jobs_watcher/deployment_gate.py` — delete `schedule_retry` (~267-273), `set_hidden_from_production` (~233-234)
- Modify: `ready_jobs_watcher/main.py` — delete `retry_pending_job` (~262-264), `set_job_hidden_from_production` (~266-275)
- Modify: `tests/test_deployment_gate.py` — remove tests for deleted methods

- [ ] **Step 1: Update the tests first (they must stop calling deleted methods)**

In `tests/test_deployment_gate.py`:

(a) Delete the entire `test_hidden_from_production_only_affects_non_debug_visibility` method (lines 46-57) — it calls `set_hidden_from_production`.

(b) In `test_operator_action_helpers_extend_pending_auto_release` (lines 86-109), remove the `hidden_update` block (95-97) and the `retry_update` block (103-105). The method becomes:

```python
    def test_operator_action_helpers_extend_pending_auto_release(self):
        with tempfile.TemporaryDirectory() as root:
            job = "1002B - TEST"
            os.makedirs(os.path.join(root, job), exist_ok=True)
            gate = DeploymentGateManager(root)

            state = gate.ensure_pending_for_new_job(job)
            initial = datetime.fromisoformat(state["timers"]["autoReleaseAt"])

            mode_update = gate.set_selected_mode(job, "FACE-FRAME")
            mode_after = datetime.fromisoformat(mode_update["timers"]["autoReleaseAt"])
            self.assertGreaterEqual(mode_after, initial)

            remind_update = gate.schedule_reminder(job, minutes=1)
            remind_after = datetime.fromisoformat(remind_update["timers"]["autoReleaseAt"])
            self.assertGreaterEqual(remind_after, mode_after)
```

`test_clear_timers_clears_auto_release_and_action_clock` (which reads `timers["retryAt"]`) stays unchanged — `retryAt` is still a JSON field, just never written.

- [ ] **Step 2: Run to verify the suite fails on the deleted-method call sites only after deletion** (first confirm current green)

Run: `python -m pytest tests/test_deployment_gate.py -q`
Expected: PASS (tests no longer call the methods, but methods still exist — green)

- [ ] **Step 3: Delete `schedule_retry` from `deployment_gate.py`**

Remove the method (lines 267-273):

```python
    def schedule_retry(self, job_folder_name: str, minutes: int = 3, *, mark_as_operator_action: bool = True) -> Dict:
        retry_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat()
        return self.update_state(
            job_folder_name,
            timers={"retryAt": retry_at},
            operator_action=mark_as_operator_action,
        )
```

- [ ] **Step 4: Delete `set_hidden_from_production` from `deployment_gate.py`**

Remove the method (lines 233-234):

```python
    def set_hidden_from_production(self, job_folder_name: str, hidden: bool) -> Dict:
        return self.update_state(job_folder_name, hiddenFromProduction=bool(hidden), operator_action=True)
```

Note: `update_state` still accepts `hiddenFromProduction` in its whitelist (line 165-167) — leave that intact so `auto_release_pending_job` can still set it `False`.

- [ ] **Step 5: Delete the two `main.py` methods**

Remove `retry_pending_job` (lines 262-264) and `set_job_hidden_from_production` (lines 266-275). Leave `remind_pending_job` (258-260) and `set_job_selected_mode` (277+) intact.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/test_deployment_gate.py tests/test_pending_autorelease_scheduler.py tests/test_watchers_deployment_gate.py tests/test_metadata_cache.py tests/test_reparse_job.py -q`
Expected: PASS

Then the whole suite:

Run: `python -m pytest -q`
Expected: PASS (no references to deleted symbols anywhere)

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/deployment_gate.py ready_jobs_watcher/main.py tests/test_deployment_gate.py
git commit -m "refactor: remove unused schedule_retry and hidden-from-production write paths"
```

---

## Task 7: Apply dialog + table QSS styling (GUI)

Industrial grey + safety-orange CTA, status-pill colors already on table items. Style the dialog primary button and header labels.

**Files:**
- Modify: `ready_jobs_watcher/gui.py` — locate where the window/app stylesheet is set (search for `setStyleSheet`); if none exists for the dialog, set it on the dialog in `_show_pending_job_prompt_dialog`.

- [ ] **Step 1: Find existing stylesheet usage**

Run: `python -c "import re; src=open('ready_jobs_watcher/gui.py').read(); print([m for m in re.findall(r'setStyleSheet', src)])"`
Expected: prints a list (possibly empty). This tells you whether a global QSS already exists to extend, or whether to scope QSS to the dialog.

- [ ] **Step 2: Add scoped QSS to the dialog**

In `_show_pending_job_prompt_dialog`, immediately after `layout = QVBoxLayout(dialog)`, add:

```python
        dialog.setStyleSheet(
            "QDialog#jobActionDialog { background: #F8FAFC; }"
            "QLabel#dialogEyebrow { color: #F97316; font-size: 11px; font-weight: 600; }"
            "QLabel#dialogJobName { color: #334155; font-size: 16px; font-weight: 600; }"
            "QPushButton { padding: 6px 14px; border: 1px solid #CBD5E1; border-radius: 6px; "
            "background: #FFFFFF; color: #334155; }"
            "QPushButton:hover { background: #F1F5F9; }"
            "QPushButton#primaryButton { background: #F97316; color: #FFFFFF; border: 1px solid #F97316; }"
            "QPushButton#primaryButton:hover { background: #EA580C; }"
        )
```

- [ ] **Step 3: Verify syntax + import**

Run: `python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; from PyQt6.QtWidgets import QApplication; app=QApplication([]); import ready_jobs_watcher.gui; print('gui import ok')"`
Expected: `gui import ok`

- [ ] **Step 4: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "style: industrial grey + orange CTA on job action dialog"
```

---

## Task 8: Full verification + manual checklist

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -q`
Expected: all pass. If any test references a deleted symbol, fix per Task 6.

- [ ] **Step 2: Grep for orphaned references**

Confirm zero matches outside of git history for the deleted symbols in source:

Run: `python -c "import subprocess" ` then manually search `ready_jobs_watcher/` for: `schedule_retry`, `set_hidden_from_production`, `retry_pending_job`, `set_job_hidden_from_production`, `_retry_selected_job`, `_remind_selected_job`, `jobs_selected_mode_combo`, `jobs_detected_mode_combo`. Expected: none in `ready_jobs_watcher/*.py`.

- [ ] **Step 3: Launch the app and walk the checklist**

Start the app (`python -m ready_jobs_watcher`) and verify:
- Tab is labeled **"Jobs"**.
- Table shows: Job · State · Selected Mode · Detected Mode · Mode Source · Remind At · Updated At.
- A pending job shows amber `PENDING`; a deployed-not-parsed job shows blue `PARSING`; a fully-deployed job shows green `ACTIVE`. Rows are tinted to match.
- Toolbar has only **Refresh**.
- Double-click a `PENDING` row → dialog with Remind-in spinbox + Snooze, Cancel, orange Release.
  - Release deploys; after parse completes the row turns `ACTIVE` and the job appears in Android.
  - Snooze with N min writes `remindAt` and re-prompts after N minutes.
  - Cancel closes with no change.
- Double-click an `ACTIVE`/`PARSING` row → dialog with Re-parse (confirm prompt) + Cancel; mode selector disabled.
- Auto-release notice dialog shows only **Dismiss**.

- [ ] **Step 4: Confirm Android metadata unchanged**

Inspect one job's `.metadata/deployment_gate.json` after a Release and after a Snooze. Confirm all original keys are present (`deployed`, `parseReady`, `hiddenFromProduction`, `selectedMode`, `modeDetection`, `timers.{retryAt,remindAt,autoReleaseAt,lastActionAt}`, `createdAt`, `updatedAt`). `hiddenFromProduction` should read `false`; `retryAt` should be `null`/unchanged.

- [ ] **Step 5: Final commit (if any checklist fixes were needed)**

```bash
git add -A
git commit -m "test: verify job visibility simplification end-to-end"
```

---

## Self-Review Notes

- **Spec coverage:** derive-state (Task 1), retire hidden write (Task 2), single remind (Task 4 dialog), Release=deploy+visible (Task 4), remove-unused (Task 6), Jobs tab redesign (Task 3), state-aware dialog incl. re-parse (Task 4), auto-release Undo removal (Task 5), QSS (Task 7), Android schema preserved (Task 2 + Task 8 step 4). All spec sections mapped.
- **Schema safety:** No field is removed from serialization. `_coerce_state`/`_default_state` keep every key; `update_state` whitelist keeps `hiddenFromProduction` writable for `auto_release_pending_job`.
- **Type consistency:** `derive_state(state: Dict) -> str` returns the exact literals `"PENDING"`/`"PARSING"`/`"ACTIVE"` used by both the table styling map and the dialog branch.
