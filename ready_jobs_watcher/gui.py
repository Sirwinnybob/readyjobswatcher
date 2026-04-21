"""
Graphical User Interface Module.

Provides the `SettingsWindow` class for the application, built with `PyQt6`.
Allows users to configure paths, backup schedules, Planka integrations, and operation
delays, as well as view running logs.
"""
import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QListWidget, QTimeEdit, QSpinBox, QTextEdit, QMessageBox,
    QFormLayout, QGroupBox, QInputDialog
)
from PyQt6.QtCore import QTime, QObject, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtCore import QTimer
import keyring

KEYRING_SERVICE = "ReadyJobsWatcher"

main_logger = logging.getLogger('main')

class LogSignal(QObject):
    new_log = pyqtSignal(str)

class QtLogHandler(logging.Handler):
    def __init__(self, log_signal):
        super().__init__()
        self.log_signal = log_signal

    def emit(self, record):
        log_entry = self.format(record)
        self.log_signal.new_log.emit(log_entry)

class SettingsWindow(QWidget):
    """
    Main configuration interface for Ready Jobs Watcher using PyQt6.
    """
    def __init__(self, config, app_instance=None):
        super().__init__()
        self.config = config
        self.app_instance = app_instance

        self.setWindowTitle("Ready Jobs Watcher Settings")
        self.resize(600, 500)

        # Setup Logger Signal for UI updates
        self.log_signal = LogSignal()
        self.log_signal.new_log.connect(self.append_log)

        self.qt_handler = QtLogHandler(self.log_signal)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        self.qt_handler.setFormatter(formatter)
        logging.getLogger().addHandler(self.qt_handler)

        self.init_ui()
        self.load_settings()

        # Status refresh timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(10000) # Update every 10 seconds

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.setup_status_tab()
        self.setup_paths_tab()
        self.setup_schedule_tab()
        self.setup_planka_tab()
        self.setup_actions_tab()
        self.setup_log_tab()

        # Bottom Buttons
        button_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save_settings)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close)

        button_layout.addStretch()
        button_layout.addWidget(self.save_btn)
        button_layout.addWidget(self.cancel_btn)

        main_layout.addLayout(button_layout)


    def setup_status_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        status_group = QGroupBox("Application Status")
        status_layout = QVBoxLayout()

        self.last_backup_label = QLabel("Last Backup: None")
        self.next_backup_label = QLabel("Next Backup: Calculating...")
        self.pending_replacements_label = QLabel("Pending Replacements: 0")

        status_layout.addWidget(self.last_backup_label)
        status_layout.addWidget(self.next_backup_label)
        status_layout.addWidget(self.pending_replacements_label)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)
        layout.addStretch()

        self.tabs.addTab(tab, "Status")

    def update_status(self):
        if not self.app_instance:
            return

        # Update Backup Status
        if getattr(self.app_instance, 'LAST_BACKUP_TIME', None):
            self.last_backup_label.setText(f"Last Backup: {self.app_instance.LAST_BACKUP_TIME.strftime('%Y-%m-%d %H:%M')}")
        else:
            self.last_backup_label.setText("Last Backup: None")

        next_time = self.config.get_next_backup_time()
        self.next_backup_label.setText(f"Next Backup: {next_time.strftime('%Y-%m-%d %H:%M')}")

        # Update Pending Replacements
        if hasattr(self.app_instance, 'pending_queue'):
            count = self.app_instance.pending_queue.qsize()
            self.pending_replacements_label.setText(f"Pending Replacements: {count}")

    def setup_paths_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form_layout = QFormLayout()

        self.root_dir_input = QLineEdit()
        self.cnc_subdir_input = QLineEdit()
        self.backup_dir_input = QLineEdit()

        form_layout.addRow("Root Directory:", self.root_dir_input)
        form_layout.addRow("CNC Subdirectory:", self.cnc_subdir_input)
        form_layout.addRow("Backup Destination:", self.backup_dir_input)

        layout.addLayout(form_layout)

        folders_group = QGroupBox("Folders to Backup")
        folders_layout = QVBoxLayout()
        self.backup_folders_list = QListWidget()
        folders_layout.addWidget(self.backup_folders_list)

        btn_layout = QHBoxLayout()
        add_folder_btn = QPushButton("Add Folder")
        add_folder_btn.clicked.connect(self.add_backup_folder)
        remove_folder_btn = QPushButton("Remove Selected")
        remove_folder_btn.clicked.connect(self.remove_backup_folder)

        btn_layout.addWidget(add_folder_btn)
        btn_layout.addWidget(remove_folder_btn)
        folders_layout.addLayout(btn_layout)
        folders_group.setLayout(folders_layout)

        layout.addWidget(folders_group)
        self.tabs.addTab(tab, "Paths")

    def setup_schedule_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Backup Times Group
        backup_group = QGroupBox("Backup Times")
        backup_layout = QVBoxLayout()
        self.backup_times_list = QListWidget()
        backup_layout.addWidget(self.backup_times_list)

        btn_layout = QHBoxLayout()
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        add_time_btn = QPushButton("Add Time")
        add_time_btn.clicked.connect(self.add_backup_time)
        remove_time_btn = QPushButton("Remove Selected")
        remove_time_btn.clicked.connect(self.remove_backup_time)

        btn_layout.addWidget(self.time_edit)
        btn_layout.addWidget(add_time_btn)
        btn_layout.addWidget(remove_time_btn)
        backup_layout.addLayout(btn_layout)
        backup_group.setLayout(backup_layout)

        # Restart Time
        form_layout = QFormLayout()
        self.restart_time_edit = QTimeEdit()
        self.restart_time_edit.setDisplayFormat("HH:mm")
        form_layout.addRow("Daily Restart Time:", self.restart_time_edit)

        layout.addWidget(backup_group)
        layout.addLayout(form_layout)
        layout.addStretch()
        self.tabs.addTab(tab, "Schedule")

    def setup_planka_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)

        self.planka_url_input = QLineEdit()
        self.planka_user_input = QLineEdit()
        self.planka_board_input = QLineEdit()
        self.planka_list_input = QLineEdit()
        self.planka_pass_input = QLineEdit()
        self.planka_pass_input.setEchoMode(QLineEdit.EchoMode.Password)

        layout.addRow("Planka Base URL:", self.planka_url_input)
        layout.addRow("Username:", self.planka_user_input)
        layout.addRow("Password:", self.planka_pass_input)
        layout.addRow("Board Identifier:", self.planka_board_input)
        layout.addRow("List Name:", self.planka_list_input)

        self.pdf_delay_spin = QSpinBox()
        self.pdf_delay_spin.setMaximum(100000)
        self.folder_delay_spin = QSpinBox()
        self.folder_delay_spin.setMaximum(100000)

        layout.addRow("PDF Conversion Delay (s):", self.pdf_delay_spin)
        layout.addRow("New Folder Delay (s):", self.folder_delay_spin)

        self.tabs.addTab(tab, "Planka & Delays")


    def setup_actions_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        backup_btn = QPushButton("Backup Now")
        backup_btn.clicked.connect(self.trigger_backup)

        scan_cnc_btn = QPushButton("Scan CNC Now")
        scan_cnc_btn.clicked.connect(self.trigger_scan_cnc)

        scan_ready_jobs_btn = QPushButton("Scan Ready Jobs Now")
        scan_ready_jobs_btn.clicked.connect(self.trigger_scan_ready_jobs)

        convert_pdfs_btn = QPushButton("Convert PDFs to Dark Mode")
        convert_pdfs_btn.clicked.connect(self.trigger_convert_pdfs)

        force_convert_pdfs_btn = QPushButton("Force Convert All PDFs")
        force_convert_pdfs_btn.clicked.connect(self.trigger_force_convert_pdfs)

        layout.addWidget(backup_btn)
        layout.addWidget(scan_cnc_btn)
        layout.addWidget(scan_ready_jobs_btn)
        layout.addWidget(convert_pdfs_btn)
        layout.addWidget(force_convert_pdfs_btn)
        layout.addStretch()

        self.tabs.addTab(tab, "Actions")

    def trigger_backup(self):
        if self.app_instance:
            import threading
            threading.Thread(target=self.app_instance.perform_backup, daemon=True).start()
            QMessageBox.information(self, "Backup", "Backup triggered in background.")

    def trigger_scan_cnc(self):
        if self.app_instance:
            import threading
            threading.Thread(target=self.app_instance.scan_cnc_pdfs_for_bad_parts, daemon=True).start()
            QMessageBox.information(self, "Scan", "CNC Scan triggered in background.")

    def trigger_scan_ready_jobs(self):
        if self.app_instance:
            import threading
            threading.Thread(target=self.app_instance.initial_scan, daemon=True).start()
            QMessageBox.information(self, "Scan", "Ready Jobs scan triggered in background.")

    def trigger_convert_pdfs(self):
        if self.app_instance:
            import threading
            from ready_jobs_watcher.pdf_dark_mode import process_directory
            threading.Thread(target=process_directory, args=(self.config.ROOT_DIR, False), daemon=True).start()
            QMessageBox.information(self, "Convert", "PDF conversion started.")

    def trigger_force_convert_pdfs(self):
        if self.app_instance:
            import threading
            from ready_jobs_watcher.pdf_dark_mode import process_directory
            threading.Thread(target=process_directory, args=(self.config.ROOT_DIR, True), daemon=True).start()
            QMessageBox.information(self, "Convert", "Forced PDF conversion started.")

    def setup_log_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)

        self.tabs.addTab(tab, "Running Log")

    def append_log(self, text):
        self.log_output.moveCursor(QTextCursor.MoveOperation.End)
        self.log_output.insertPlainText(text + "\n")
        self.log_output.moveCursor(QTextCursor.MoveOperation.End)

    def load_settings(self):
        self.root_dir_input.setText(self.config.ROOT_DIR)
        self.cnc_subdir_input.setText(self.config.CNC_SUBDIR)
        self.backup_dir_input.setText(self.config.BACKUP_DIR)

        self.backup_folders_list.clear()
        self.backup_folders_list.addItems(self.config.BACKUP_FOLDERS)

        self.backup_times_list.clear()
        self.backup_times_list.addItems(self.config.BACKUP_TIMES)

        h, m = map(int, self.config.daily_restart_time.split(':'))
        self.restart_time_edit.setTime(QTime(h, m))

        self.planka_url_input.setText(self.config.planka_base_url or "")
        self.planka_user_input.setText(self.config.planka_username or "")

        # Load password
        password = ""
        if self.config.planka_username:
            try:
                password = keyring.get_password(KEYRING_SERVICE, self.config.planka_username) or ""
            except Exception as e:
                main_logger.warning(f"Could not load password from keyring: {e}")
        self.planka_pass_input.setText(password)

        self.planka_board_input.setText(self.config.planka_board_identifier)
        self.planka_list_input.setText(self.config.planka_list_name)

        self.pdf_delay_spin.setValue(self.config.pdf_conversion_delay_seconds)
        self.folder_delay_spin.setValue(self.config.new_folder_delay_seconds)

    def add_backup_folder(self):
        folder, ok = QInputDialog.getText(self, "Add Folder", "Enter folder path:")
        if ok and folder:
            self.backup_folders_list.addItem(folder)

    def remove_backup_folder(self):
        row = self.backup_folders_list.currentRow()
        if row >= 0:
            self.backup_folders_list.takeItem(row)

    def add_backup_time(self):
        time_str = self.time_edit.time().toString("HH:mm")
        # Check if already exists
        items = [self.backup_times_list.item(i).text() for i in range(self.backup_times_list.count())]
        if time_str not in items:
            self.backup_times_list.addItem(time_str)

    def remove_backup_time(self):
        row = self.backup_times_list.currentRow()
        if row >= 0:
            self.backup_times_list.takeItem(row)

    def save_settings(self):
        self.config.ROOT_DIR = self.root_dir_input.text()
        self.config.CNC_SUBDIR = self.cnc_subdir_input.text()
        self.config.BACKUP_DIR = self.backup_dir_input.text()

        self.config.BACKUP_FOLDERS = [self.backup_folders_list.item(i).text() for i in range(self.backup_folders_list.count())]
        self.config.BACKUP_TIMES = [self.backup_times_list.item(i).text() for i in range(self.backup_times_list.count())]

        self.config.daily_restart_time = self.restart_time_edit.time().toString("HH:mm")

        self.config.planka_base_url = self.planka_url_input.text() or None
        self.config.planka_username = self.planka_user_input.text() or None

        # Save password
        password = self.planka_pass_input.text()
        if self.config.planka_username and password:
            try:
                keyring.set_password(KEYRING_SERVICE, self.config.planka_username, password)
            except Exception as e:
                main_logger.error(f"Failed to save password to keyring: {e}")
                QMessageBox.warning(self, "Warning", "Failed to save Planka password to credential manager.")
        elif self.config.planka_username and not password:
            try:
                keyring.delete_password(KEYRING_SERVICE, self.config.planka_username)
            except Exception:
                pass

        self.config.planka_board_identifier = self.planka_board_input.text()
        self.config.planka_list_name = self.planka_list_input.text()

        self.config.pdf_conversion_delay_seconds = self.pdf_delay_spin.value()
        self.config.new_folder_delay_seconds = self.folder_delay_spin.value()

        self.config.save()
        QMessageBox.information(self, "Success", "Settings saved successfully.")

        # Optionally schedule backup update in app_instance if it exists
        if self.app_instance and hasattr(self.app_instance, 'scheduler'):
            # In Tkinter version we triggered UI update for next backup.
            # Here we might need a signal or just let app_instance handle it on its own.
            pass

    def show_window(self):
        if self.app_instance:
            self.app_instance.PAUSE_PROCESSING = True
            main_logger.info("Paused background processing while settings are open.")
        self.update_status()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.app_instance:
            self.app_instance.PAUSE_PROCESSING = False
            main_logger.info("Resumed background processing.")
        super().closeEvent(event)

