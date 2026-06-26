import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication, QWidget, QDialog, QPushButton, QComboBox
import ready_jobs_watcher.gui as gui

app = QApplication(sys.argv)

fake_app_instance = MagicMock()
fake_app_instance.get_jobs_dashboard_rows.return_value = [{
    'jobFolderName': '999 - SMOKE TEST',
    'deployed': True,
    'parseReady': True,
    'selectedMode': 'FRAMELESS',
    'modeDetection': {'candidate': 'FRAMELESS', 'source': 'DELIVERY_SHEET'},
}]

win = gui.SettingsWindow.__new__(gui.SettingsWindow)
QWidget.__init__(win)
win.app_instance = fake_app_instance
win.jobs_table = None

original_exec = QDialog.exec


def fake_exec(self):
    combo = self.findChildren(QComboBox)[0]
    combo.setCurrentText('FACE-FRAME')  # operator corrects the mode
    buttons = {b.text(): b for b in self.findChildren(QPushButton)}
    buttons['Save Mode'].click()


QDialog.exec = fake_exec
try:
    win._show_pending_job_prompt_dialog('999 - SMOKE TEST')
finally:
    QDialog.exec = original_exec

print('set_job_selected_mode calls:', fake_app_instance.set_job_selected_mode.call_args_list)
fake_app_instance.set_job_selected_mode.assert_called_once_with('999 - SMOKE TEST', 'FACE-FRAME')
print('OK: clicking Save Mode on a released job persists the corrected construction type.')
