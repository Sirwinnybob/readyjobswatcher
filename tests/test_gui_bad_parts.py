from PyQt6.QtWidgets import QApplication
from ready_jobs_watcher.config import Config
from ready_jobs_watcher.gui import SettingsWindow
from ready_jobs_watcher.tracker_bad_parts import BadPartDetailRecord, TrackerBadPartKey

def test_settings_window_bad_parts_tab():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    tab_texts = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Bad Parts" in tab_texts

def test_bad_parts_tab_has_splitter_and_tables():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    # Verify presence of left-side tables and right-side preview/labels
    assert hasattr(window, 'unack_table_widget')
    assert hasattr(window, 'ack_table_widget')
    assert hasattr(window, 'bad_part_preview_label')

def test_refresh_populates_records():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    # Assert refresh method exists and runs without crashing
    assert hasattr(window, 'refresh_bad_parts')
    window.refresh_bad_parts()

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

def test_select_row_displays_details_without_crashing():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    key = TrackerBadPartKey("JobA", "file.pdf", 1, "fp", 42)
    record = BadPartDetailRecord(
        key=key,
        token="token",
        is_acknowledged=False,
        material="MDF",
        pdf_filename="file.pdf",
        pdf_full_path="path/file.pdf",
        page=1,
        part_number=42,
        part_name="Door",
        width=12.5,
        length=24.0,
        cabinet_number=10,
        room="Kitchen",
        detected_at="2026-06-22",
        thumbnail_path=None,
        highlight_rect=None
    )
    
    class DummyAlertCoord:
        def get_bad_parts_snapshot(self, include_resolved):
            return {"unacknowledged": [record], "acknowledged": []}
    
    window.alert_coordinator = DummyAlertCoord()
    window.refresh_bad_parts()
    
    # Select the first row
    window.unack_table_widget.selectRow(0)
    
    # Assert details are loaded
    assert window.bad_part_job_lbl.text() == "JobA"
    assert window.bad_part_material_lbl.text() == "MDF"
    assert window.bad_part_pdf_lbl.text() == "file.pdf"
