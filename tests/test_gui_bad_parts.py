from PyQt6.QtWidgets import QApplication
from ready_jobs_watcher.config import Config
from ready_jobs_watcher.gui import SettingsWindow

def test_settings_window_bad_parts_tab():
    app = QApplication.instance() or QApplication([])
    config = Config()
    window = SettingsWindow(config)
    
    tab_texts = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Bad Parts" in tab_texts
