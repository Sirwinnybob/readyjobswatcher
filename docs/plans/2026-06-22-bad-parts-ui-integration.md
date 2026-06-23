# Bad Parts UI Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate the Bad Parts Center directly into the main PyQt6 settings UI as a horizontal split-pane tab, featuring a high-res highlighted sheet preview alongside parts metadata and action controls.

**Architecture:** Add a new tab `Bad Parts` to `SettingsWindow` using `QSplitter` to separate the parts tables (Unacknowledged/Acknowledged) on the left from the `QScrollArea`-wrapped image preview and metadata details on the right. Selecting a row dynamically draws the highlight box on the page thumbnail and updates the details.

**Tech Stack:** Python, PyQt6 (QtWidgets, QtGui, QtCore), Pillow

---

### Task 1: Add GUI Imports and Tab Registration

**Files:**
- Modify: [ready_jobs_watcher/gui.py](file:///c:/Scripts/Ready%20Jobs%20Watcher/ready_jobs_watcher/gui.py:11-30)

**Step 1: Write the failing test**
Create a new file `tests/test_gui_bad_parts.py`:
```python
from PyQt6.QtWidgets import QApplication
from ready_jobs_watcher.config import Config
from ready_jobs_watcher.gui import SettingsWindow

def test_settings_window_bad_parts_tab():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    tab_texts = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Bad Parts" in tab_texts
```

**Step 2: Run test to verify it fails**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: FAIL with `AssertionError: assert 'Bad Parts' in [...]`

**Step 3: Write minimal implementation**
1. Add `QSplitter` and `QGridLayout` to the PyQt6 imports in [ready_jobs_watcher/gui.py](file:///c:/Scripts/Ready%20Jobs%20Watcher/ready_jobs_watcher/gui.py).
2. Register `self.setup_bad_parts_tab()` in `SettingsWindow.init_ui`.
3. Create a placeholder method `setup_bad_parts_tab(self)` that adds a tab with name `"Bad Parts"`.

**Step 4: Run test to verify it passes**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_gui_bad_parts.py ready_jobs_watcher/gui.py
git commit -m "feat: register Bad Parts tab in SettingsWindow"
```

---

### Task 2: Implement Bad Parts Tab UI Layout

**Files:**
- Modify: [ready_jobs_watcher/gui.py](file:///c:/Scripts/Ready%20Jobs%20Watcher/ready_jobs_watcher/gui.py)

**Step 1: Write the failing test**
Add a test in `tests/test_gui_bad_parts.py`:
```python
def test_bad_parts_tab_has_splitter_and_tables():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    # Verify presence of left-side tables and right-side preview/labels
    assert hasattr(window, 'unack_table_widget')
    assert hasattr(window, 'ack_table_widget')
    assert hasattr(window, 'bad_part_preview_label')
```

**Step 2: Run test to verify it fails**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: FAIL with `AttributeError` for `unack_table_widget`

**Step 3: Write minimal implementation**
Implement `setup_bad_parts_tab` in `gui.py` to build the horizontal `QSplitter`:
- **Left Widget**: A `QTabWidget` containing two `QTableWidget`s (`self.unack_table_widget`, `self.ack_table_widget`) with headers: `["Job", "Material", "Page", "Part #", "Part Name"]`.
- **Right Widget**: A `QWidget` containing a `QVBoxLayout` with:
  - A `QScrollArea` wrapping `self.bad_part_preview_label` (`QLabel`).
  - A `QGroupBox` with a `QFormLayout` containing metadata labels (`self.bad_part_job_lbl`, `self.bad_part_material_lbl`, `self.bad_part_pdf_lbl`, `self.bad_part_page_part_lbl`, `self.bad_part_size_lbl`, `self.bad_part_location_lbl`, `self.bad_part_detected_lbl`).
  - A horizontal layout with action buttons: Acknowledge, Unacknowledge, Refresh, Scan CNC.

**Step 4: Run test to verify it passes**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add ready_jobs_watcher/gui.py tests/test_gui_bad_parts.py
git commit -m "feat: build Bad Parts tab split-screen layout"
```

---

### Task 3: Implement Data Loading and Selection Preview Logic

**Files:**
- Modify: [ready_jobs_watcher/gui.py](file:///c:/Scripts/Ready%20Jobs%20Watcher/ready_jobs_watcher/gui.py)

**Step 1: Write the failing test**
Add a test in `tests/test_gui_bad_parts.py`:
```python
def test_refresh_populates_records():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    # Assert refresh method exists and runs without crashing
    assert hasattr(window, 'refresh_bad_parts')
    window.refresh_bad_parts()
```

**Step 2: Run test to verify it fails**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: FAIL with `AttributeError: 'SettingsWindow' object has no attribute 'refresh_bad_parts'`

**Step 3: Write minimal implementation**
1. Implement `refresh_bad_parts(self)`:
   - Call `alert_coordinator.get_bad_parts_snapshot()` if available.
   - Populate `self.unack_records` and `self.ack_records`.
   - Populate both table widgets.
2. Implement selection change handler `_on_bad_part_selection_changed(self)`:
   - Determine which table is active, retrieve the selected row index and the corresponding `BadPartDetailRecord`.
   - Update `self.bad_part_preview_label` with the page thumbnail, drawing a red bounding box if `highlight_rect` is available.
   - Update form labels with metadata.
   - Toggle Acknowledge/Unacknowledge buttons dynamically.
3. Hook table selection signals to the handler.
4. Hook Acknowledge/Unacknowledge button click actions to call `alert_coordinator.acknowledge_keys` / `unacknowledge_keys`.

**Step 4: Run test to verify it passes**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add ready_jobs_watcher/gui.py tests/test_gui_bad_parts.py
git commit -m "feat: implement bad parts data population and preview rendering"
```

---

### Task 4: Integrate the Tab with Tray Icon and Alerts

**Files:**
- Modify: [ready_jobs_watcher/gui.py](file:///c:/Scripts/Ready%20Jobs%20Watcher/ready_jobs_watcher/gui.py)

**Step 1: Write the failing test**
Add a test in `tests/test_gui_bad_parts.py` to verify the redirect behaves correctly:
```python
def test_show_bad_parts_center_switches_tab():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    # Mock alert_coordinator
    class DummyAlertCoord:
        def get_bad_parts_snapshot(self, include_resolved):
            return {"unacknowledged": [], "acknowledged": []}
    window.alert_coordinator = DummyAlertCoord()
    
    window.show_bad_parts_center()
    active_tab_text = window.tabs.tabText(window.tabs.currentIndex())
    assert active_tab_text == "Bad Parts"
```

**Step 2: Run test to verify it fails**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: FAIL with `AssertionError: assert 'Status' == 'Bad Parts'` (since the old method opened `BadPartsCenterDialog` and didn't change settings tabs)

**Step 3: Write minimal implementation**
1. Modify `SettingsWindow.show_bad_parts_center(self)`:
   - Call `self.show_window()`.
   - Call `self.refresh_bad_parts()`.
   - Locate the `"Bad Parts"` tab index and call `self.tabs.setCurrentIndex(index)`.
2. Modify `SettingsWindow._show_bad_parts_alert_dialog(self, batch)`:
   - Instead of displaying a modal table and double-clicking, it shows a simple `QMessageBox` warning dialog with a button "View in Bad Parts Center" which triggers `show_bad_parts_center()`.

**Step 4: Run test to verify it passes**
Run: `.venv\Scripts\pytest tests/test_gui_bad_parts.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add ready_jobs_watcher/gui.py tests/test_gui_bad_parts.py
git commit -m "feat: redirect tray click and detection alert dialog to the Bad Parts tab"
```

---

### Task 5: Clean Up and Resize Main Window

**Files:**
- Modify: [ready_jobs_watcher/gui.py](file:///c:/Scripts/Ready%20Jobs%20Watcher/ready_jobs_watcher/gui.py)

**Step 1: Write the failing test**
No new code behavior, verify full test suite passes.
Run: `.venv\Scripts\pytest`
Expected: PASS

**Step 2: Write minimal implementation**
1. Increase default window size in `SettingsWindow.__init__` from `600, 500` to `1000, 700` to properly accommodate the split layout.
2. Remove any unused definitions of the old `BadPartsCenterDialog` class to keep the code clean and maintainable.

**Step 3: Run test to verify it passes**
Run: `.venv\Scripts\pytest`
Expected: PASS

**Step 4: Commit**
```bash
git add ready_jobs_watcher/gui.py
git commit -m "cleanup: resize main window and clean up old dialog class"
```
