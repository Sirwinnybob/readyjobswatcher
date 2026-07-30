from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton

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


class _FakeClickedSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class _FakeButton:
    instances = []

    def __init__(self, label):
        self.label = label
        self.clicked = _FakeClickedSignal()
        self.enabled = True
        self.__class__.instances.append(self)

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeDialog:
    def __init__(self, *_args):
        pass

    def setWindowTitle(self, _title):
        pass

    def setWindowModality(self, _modality):
        pass

    def accept(self):
        pass

    def exec(self):
        pass


class _FakeLayout:
    def __init__(self, *_args):
        pass

    def addWidget(self, _widget):
        pass

    def addLayout(self, _layout):
        pass

    def addStretch(self):
        pass


class _FakeLabel:
    instances = []

    def __init__(self, text):
        self.text = text
        self.__class__.instances.append(self)


class _OverrideApp:
    def __init__(self):
        self.calls = []

    def update_cutlist_job_mismatch_override(self, job_folder_name, *, allow, **identity):
        self.calls.append((job_folder_name, allow, identity))
        return {"success": True, "message": "Override updated."}


def _bare_settings_window_with_override_app():
    window = SettingsWindow.__new__(SettingsWindow)
    window.app_instance = _OverrideApp()
    return window


def _job_mismatch_payload(override_active, *, override_present=None):
    if override_present is None:
        override_present = override_active
    return {
        "mismatches": [{
            "docType": "Face Frame",
            "pdfFilename": "FFCL.pdf",
            "expectedJob": "123",
            "foundJob": "456",
            "overrideActive": override_active,
            "overridePresent": override_present,
        }]
    }


def _capture_mismatch_dialog_buttons(monkeypatch, window, payload):
    _FakeButton.instances = []
    _FakeLabel.instances = []
    monkeypatch.setattr("ready_jobs_watcher.gui.QDialog", _FakeDialog)
    monkeypatch.setattr("ready_jobs_watcher.gui.QVBoxLayout", _FakeLayout)
    monkeypatch.setattr("ready_jobs_watcher.gui.QHBoxLayout", _FakeLayout)
    monkeypatch.setattr("ready_jobs_watcher.gui.QLabel", _FakeLabel)
    monkeypatch.setattr("ready_jobs_watcher.gui.QPushButton", _FakeButton)
    window._show_cutlist_mismatch_job_dialog("123 - TEST JOB", payload)
    return [button.label for button in _FakeButton.instances]


def _captured_mismatch_dialog_text():
    return "\n".join(label.text for label in _FakeLabel.instances)


def test_mismatch_dialog_offers_allow_for_blocked_job_number(monkeypatch):
    window = _bare_settings_window_with_override_app()
    labels = _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(False))

    assert "Allow this PDF anyway" in labels
    assert "Remove allow and rebuild" not in labels


def test_mismatch_dialog_offers_revoke_for_allowed_job_number(monkeypatch):
    window = _bare_settings_window_with_override_app()
    labels = _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(True))

    assert "Remove allow and rebuild" in labels
    assert "Allow this PDF anyway" not in labels


def test_mismatch_dialog_offers_revoke_for_saved_but_inactive_override(monkeypatch):
    window = _bare_settings_window_with_override_app()
    labels = _capture_mismatch_dialog_buttons(
        monkeypatch,
        window,
        _job_mismatch_payload(False, override_present=True),
    )

    assert "Remove allow and rebuild" in labels
    assert "Allow this PDF anyway" not in labels


def test_mismatch_dialog_says_active_override_is_indexed(monkeypatch):
    window = _bare_settings_window_with_override_app()
    _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(True))

    text = _captured_mismatch_dialog_text()
    assert "Allowed override active" in text
    assert "This PDF is indexed" in text
    assert "NOT indexed" not in text


def test_mismatch_dialog_keeps_not_indexed_warning_for_blocked_entry(monkeypatch):
    window = _bare_settings_window_with_override_app()
    _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(False))

    text = _captured_mismatch_dialog_text()
    assert "Blocked mismatch" in text
    assert "This PDF was NOT indexed" in text
    assert "Allowed override active" not in text


def test_starting_one_override_action_disables_all_job_override_buttons(monkeypatch):
    app = QApplication.instance() or QApplication([])
    override_app = _OverrideApp()
    window = SettingsWindow(Config(), app_instance=override_app)
    window.refresh_jobs_dashboard = lambda: None
    payload = {
        "mismatches": [
            {
                "docType": "Face Frame",
                "pdfFilename": "FFCL.pdf",
                "expectedJob": "123",
                "foundJob": "456",
            },
            {
                "docType": "Nailer",
                "pdfFilename": "NCL.pdf",
                "expectedJob": "123",
                "foundJob": "457",
            },
        ]
    }

    _capture_mismatch_dialog_buttons(monkeypatch, window, payload)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr("ready_jobs_watcher.gui.QMessageBox.information", lambda *_args: None)

    override_buttons = [
        button for button in _FakeButton.instances
        if button.label in {"Allow this PDF anyway", "Remove allow and rebuild"}
    ]
    assert len(override_buttons) == 2
    override_buttons[0].clicked.callback()

    assert [button.enabled for button in override_buttons] == [False, False]


def test_mismatch_dialog_allow_rebuilds_with_the_exact_job_identity(monkeypatch):
    app = QApplication.instance() or QApplication([])
    override_app = _OverrideApp()
    window = SettingsWindow(Config(), app_instance=override_app)
    refreshes = []
    messages = []
    window.refresh_jobs_dashboard = lambda: refreshes.append(True)

    _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(False))
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    allow_button = next(button for button in _FakeButton.instances if button.label == "Allow this PDF anyway")
    allow_button.clicked.callback()

    assert allow_button.enabled is False
    assert override_app.calls == [(
        "123 - TEST JOB",
        True,
        {
            "doc_type": "Face Frame",
            "pdf_filename": "FFCL.pdf",
            "expected_job": "123",
            "found_job": "456",
        },
    )]
    assert refreshes == [True]
    assert messages == [("Cutlist mismatch", "Override updated.")]


def test_mismatch_dialog_allow_requires_confirmation_with_pdf_and_job_values(monkeypatch):
    app = QApplication.instance() or QApplication([])
    override_app = _OverrideApp()
    window = SettingsWindow(Config(), app_instance=override_app)
    confirmation = []

    _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(False))
    monkeypatch.setattr(
        "ready_jobs_watcher.gui.QMessageBox.question",
        lambda _parent, title, message, *_args: confirmation.append((title, message)) or QMessageBox.StandardButton.No,
    )

    allow_button = next(button for button in _FakeButton.instances if button.label == "Allow this PDF anyway")
    allow_button.clicked.callback()

    assert override_app.calls == []
    assert allow_button.enabled is True
    assert confirmation == [(
        "Allow cutlist mismatch",
        "Allow 'FFCL.pdf' even though it shows job 456 instead of expected job 123?\n\n"
        "This will rebuild the job so the tablet can use this PDF.",
    )]


def test_mismatch_dialog_leaves_template_errors_without_an_override_action(monkeypatch):
    window = _bare_settings_window_with_override_app()
    payload = {"mismatches": [{"docType": "Face Frame", "pdfFilename": "FFCL.pdf"}]}

    labels = _capture_mismatch_dialog_buttons(monkeypatch, window, payload)

    assert labels == ["Dismiss"]


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


def test_actions_tab_has_manual_sync_moldings_button():
    app = QApplication.instance() or QApplication([])
    window = SettingsWindow(Config())

    buttons = window.findChildren(QPushButton)
    labels = [button.text() for button in buttons]

    assert "Sync Moldings Library Now" in labels


def test_trigger_sync_moldings_success(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = SettingsWindow(Config())
    
    sync_calls = 0
    def mock_sync_moldings():
        nonlocal sync_calls
        sync_calls += 1
        return True
        
    window.app_instance = SimpleNamespace(sync_moldings=mock_sync_moldings)
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

    window.trigger_sync_moldings()

    assert sync_calls == 1
    assert messages[0] == ("info", "Molding Sync", "Molding library synchronization started in background.")
    assert messages[1] == ("info", "Molding Sync", "Molding library synchronization completed successfully.")


def test_trigger_sync_moldings_failure(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = SettingsWindow(Config())
    
    sync_calls = 0
    def mock_sync_moldings():
        nonlocal sync_calls
        sync_calls += 1
        return False
        
    window.app_instance = SimpleNamespace(sync_moldings=mock_sync_moldings)
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

    window.trigger_sync_moldings()

    assert sync_calls == 1
    assert messages[0] == ("info", "Molding Sync", "Molding library synchronization started in background.")
    assert messages[1] == ("warning", "Molding Sync", "Molding library synchronization failed. Check logs for details.")
