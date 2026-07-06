import os
from types import SimpleNamespace

from ready_jobs_watcher.gui import SettingsWindow


class _App:
    def __init__(self):
        self.renames = []

    def rename_job(self, old_name, new_name):
        self.renames.append((old_name, new_name))


def test_gui_rename_calls_application_rename_job_after_disk_rename(monkeypatch, tmp_path):
    root = tmp_path / "Ready Jobs"
    root.mkdir()
    old_job = root / "123 - OLD"
    old_job.mkdir()

    window = SettingsWindow.__new__(SettingsWindow)
    window.config = SimpleNamespace(ROOT_DIR=str(root))
    window.app_instance = _App()
    window._selected_job_folder_name = lambda: "123 - OLD"  # type: ignore[method-assign]
    window.refresh_jobs_dashboard = lambda: None  # type: ignore[method-assign]

    monkeypatch.setattr("ready_jobs_watcher.gui.QInputDialog.getText", lambda *args, **kwargs: ("999 - NEW", True))
    monkeypatch.setattr("ready_jobs_watcher.gui.QMessageBox.information", lambda *args, **kwargs: None)
    monkeypatch.setattr("ready_jobs_watcher.gui.QMessageBox.warning", lambda *args, **kwargs: None)
    monkeypatch.setattr("ready_jobs_watcher.gui.QMessageBox.critical", lambda *args, **kwargs: None)

    window.trigger_rename_job()

    assert not old_job.exists()
    assert os.path.isdir(root / "999 - NEW")
    assert window.app_instance.renames == [("123 - OLD", "999 - NEW")]
