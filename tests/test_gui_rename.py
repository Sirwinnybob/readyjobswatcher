import os
from types import SimpleNamespace

from ready_jobs_watcher.gui import SettingsWindow


class _App:
    def __init__(self):
        self.renames = []

    def rename_job(self, old_name, new_name):
        self.renames.append((old_name, new_name))


class _TextInput:
    def __init__(self, value):
        self.value = value

    def text(self):
        return self.value


class _EmptyList:
    def count(self):
        return 0

    def item(self, index):
        raise IndexError(index)


class _ValueInput:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _TimeInput:
    def __init__(self, value):
        self.value = value

    def time(self):
        return SimpleNamespace(toString=lambda _format: self.value)


class _ComboInput:
    def __init__(self, value):
        self.value = value

    def currentData(self):
        return self.value


class _CheckInput:
    def __init__(self, checked):
        self.checked = checked

    def isChecked(self):
        return self.checked


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


def test_save_settings_warns_restart_required_when_root_changes(monkeypatch):
    saved = []
    window = SettingsWindow.__new__(SettingsWindow)
    window.config = SimpleNamespace(
        ROOT_DIR=r"Y:\Ready Jobs",
        CNC_SUBDIR="CNC",
        BACKUP_DIR=r"C:\Backups",
        save=lambda: saved.append("saved"),
    )
    window.app_instance = None
    window.root_dir_input = _TextInput(r"Z:\Ready Jobs")
    window.cnc_subdir_input = _TextInput("CNC")
    window.backup_dir_input = _TextInput(r"C:\Backups")
    window.backup_folders_list = _EmptyList()
    window.backup_times_list = _EmptyList()
    window.retention_spin = _ValueInput(7)
    window.restart_time_edit = _TimeInput("03:00")
    window.pdf_delay_spin = _ValueInput(30)
    window.folder_delay_spin = _ValueInput(10)
    window.bad_parts_mode_combo = _ComboInput("popup")
    window.bad_parts_popup_checkbox = _CheckInput(True)
    window.bad_parts_toast_checkbox = _CheckInput(False)
    window.bad_parts_sound_combo = _ComboInput("default")
    messages = []

    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    window.save_settings()

    assert saved == ["saved"]
    assert window.config.ROOT_DIR == r"Z:\Ready Jobs"
    assert messages
    assert "restart" in messages[0][1].lower()
    assert "root" in messages[0][1].lower()
