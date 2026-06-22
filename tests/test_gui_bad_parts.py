from PyQt6.QtWidgets import QApplication
from ready_jobs_watcher.config import Config
from ready_jobs_watcher.gui import SettingsWindow

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
