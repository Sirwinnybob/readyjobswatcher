from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QPushButton

from ready_jobs_watcher.config import Config
from ready_jobs_watcher.gui import SettingsWindow


class _ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        self.target(*self.args, **self.kwargs)


class _MetadataRefreshService:
    def __init__(self):
        self.calls = []

    def run_scheduled_sweep(self, *, consolidate_trackers=True):
        self.calls.append(consolidate_trackers)
        return {"processed": 3, "rebuilt": 2, "archived": 1, "errors": 0}


def test_actions_tab_has_manual_tracker_consolidation_button():
    app = QApplication.instance() or QApplication([])
    window = SettingsWindow(Config())

    buttons = window.findChildren(QPushButton)
    labels = [button.text() for button in buttons]

    assert "Run Tracker Consolidation Now" in labels


def test_trigger_run_consolidation_runs_sweep_in_background(monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = _MetadataRefreshService()
    window = SettingsWindow(Config())
    window.app_instance = SimpleNamespace(metadata_refresh_service=service)
    messages = []

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.information",
        lambda _parent, title, message: messages.append(("info", title, message)),
    )
    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.warning",
        lambda _parent, title, message: messages.append(("warning", title, message)),
    )
    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.critical",
        lambda _parent, title, message: messages.append(("critical", title, message)),
    )

    window.trigger_run_consolidation()

    assert service.calls == [True]
    assert messages[0] == ("info", "Consolidation", "Tracker consolidation started in background.")
    assert messages[1][0:2] == ("info", "Consolidation Complete")
    assert "Processed: 3" in messages[1][2]
    assert "Rebuilt: 2" in messages[1][2]
    assert "Archived: 1" in messages[1][2]
    assert "Errors: 0" in messages[1][2]


def test_trigger_run_consolidation_warns_when_service_missing(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = SettingsWindow(Config())
    window.app_instance = SimpleNamespace()
    messages = []

    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.warning",
        lambda _parent, title, message: messages.append((title, message)),
    )

    window.trigger_run_consolidation()

    assert messages == [("Consolidation", "Metadata refresh service is not initialized.")]
