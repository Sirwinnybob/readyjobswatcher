"""
Graphical User Interface Module.

Provides the `SettingsWindow` class for the application, built with `PyQt6`.
Allows users to configure paths, backup schedules, operation delays,
and alert behavior, as well as view running logs.
"""
import logging
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QListWidget, QTimeEdit, QSpinBox, QTextEdit, QMessageBox,
    QFormLayout, QGroupBox, QInputDialog, QCheckBox, QComboBox, QDialog,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QScrollArea
)
from PyQt6.QtCore import QTime, QObject, pyqtSignal, Qt
from PyQt6.QtGui import QTextCursor, QPixmap, QPainter, QPen, QColor
from PyQt6.QtCore import QTimer
from .alert_coordinator import AlertBatch
from .tracker_bad_parts import BadPartDetailRecord, TrackerBadPartKey

main_logger = logging.getLogger('main')

class LogSignal(QObject):
    new_log = pyqtSignal(str)


class AlertSignal(QObject):
    new_batch = pyqtSignal(object)


class PendingJobSignal(QObject):
    new_job = pyqtSignal(str)


class AutoReleaseSignal(QObject):
    new_job = pyqtSignal(str)


class JobsDashboardSignal(QObject):
    refresh_requested = pyqtSignal()


class QtLogHandler(logging.Handler):
    def __init__(self, log_signal):
        super().__init__()
        self.log_signal = log_signal

    def emit(self, record):
        log_entry = self.format(record)
        self.log_signal.new_log.emit(log_entry)


class BadPartPreviewDialog(QDialog):
    def __init__(self, record: BadPartDetailRecord, parent=None):
        super().__init__(parent)
        self.record = record
        self.setWindowTitle(f"Page Preview - {record.pdf_filename} (Pg {record.page}, Part {record.part_number})")
        self.resize(1280, 860)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        identity = (
            f"{self.record.key.job_folder_name} | {self.record.material}\n"
            f"{self.record.pdf_filename} | Pg {self.record.page} | Part {self.record.part_number}: {self.record.part_name}"
        )
        layout.addWidget(QLabel(identity))

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setText("Preview unavailable.")
        self.image_label.setStyleSheet("background: #1e1e1e; color: #f0f0f0; padding: 12px;")

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image_label)
        layout.addWidget(scroll, 1)

        caption = QLabel(self._build_caption())
        layout.addWidget(caption)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._load_preview()

    def _build_caption(self) -> str:
        size = "?"
        if self.record.width is not None and self.record.length is not None:
            size = f'{self.record.width:.3f}" x {self.record.length:.3f}"'
        cab = str(self.record.cabinet_number) if self.record.cabinet_number is not None else "-"
        room = self.record.room or "-"
        return f"Size: {size}   Cabinet: {cab}   Room: {room}"

    def _load_preview(self):
        if not self.record.thumbnail_path:
            self.image_label.setText("Preview unavailable: no thumbnail metadata for this page.")
            return
        pixmap = QPixmap(self.record.thumbnail_path)
        if pixmap.isNull():
            self.image_label.setText("Preview unavailable: thumbnail image could not be loaded.")
            return

        if self.record.highlight_rect:
            left, top, right, bottom = self.record.highlight_rect
            painter = QPainter(pixmap)
            pen = QPen(QColor(255, 60, 60))
            pen.setWidth(6)
            painter.setPen(pen)
            painter.drawRect(left, top, max(1, right - left), max(1, bottom - top))
            painter.end()

        self.image_label.setPixmap(pixmap)
        self.image_label.adjustSize()


class BadPartsCenterDialog(QDialog):
    HEADERS = ["Job", "Material", "PDF", "Page", "Part #", "Part Name", "Size", "Cabinet", "Room", "Detected", "View"]

    def __init__(self, settings_window, parent=None):
        super().__init__(parent)
        self.settings_window = settings_window
        self.alert_coordinator = settings_window.alert_coordinator
        self.unack_records: List[BadPartDetailRecord] = []
        self.ack_records: List[BadPartDetailRecord] = []
        self.setWindowTitle("Bad Parts Center")
        self.resize(1500, 760)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self._init_ui()
        self.refresh_data()

    @staticmethod
    def _size_text(record: BadPartDetailRecord) -> str:
        if record.width is None or record.length is None:
            return "-"
        return f'{record.width:.3f}" x {record.length:.3f}"'

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)

        self.unack_table = self._create_table()
        self.ack_table = self._create_table()
        self.tabs.addTab(self.unack_table, "Unacknowledged")
        self.tabs.addTab(self.ack_table, "Acknowledged")
        layout.addWidget(self.tabs)

        action_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_data)
        self.ack_selected_btn = QPushButton("Acknowledge Selected")
        self.ack_selected_btn.clicked.connect(self.acknowledge_selected)
        self.unack_selected_btn = QPushButton("Unacknowledge Selected")
        self.unack_selected_btn.clicked.connect(self.unacknowledge_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        action_row.addWidget(self.refresh_btn)
        action_row.addStretch()
        action_row.addWidget(self.ack_selected_btn)
        action_row.addWidget(self.unack_selected_btn)
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)

    def _create_table(self) -> QTableWidget:
        table = QTableWidget(0, len(self.HEADERS), self)
        table.setHorizontalHeaderLabels(self.HEADERS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        return table

    def _populate_table(self, table: QTableWidget, records: List[BadPartDetailRecord]):
        table.setRowCount(len(records))
        for row_index, record in enumerate(records):
            table.setItem(row_index, 0, QTableWidgetItem(record.key.job_folder_name))
            table.setItem(row_index, 1, QTableWidgetItem(record.material))
            table.setItem(row_index, 2, QTableWidgetItem(record.pdf_filename))
            table.setItem(row_index, 3, QTableWidgetItem(str(record.page)))
            table.setItem(row_index, 4, QTableWidgetItem(str(record.part_number)))
            table.setItem(row_index, 5, QTableWidgetItem(record.part_name))
            table.setItem(row_index, 6, QTableWidgetItem(self._size_text(record)))
            table.setItem(row_index, 7, QTableWidgetItem(str(record.cabinet_number) if record.cabinet_number is not None else "-"))
            table.setItem(row_index, 8, QTableWidgetItem(record.room or "-"))
            table.setItem(row_index, 9, QTableWidgetItem(record.detected_at or "-"))

            view_btn = QPushButton("View Page")
            view_btn.clicked.connect(lambda _, rec=record: self.settings_window.show_part_preview(rec))
            table.setCellWidget(row_index, 10, view_btn)

    def refresh_data(self):
        if self.alert_coordinator is None:
            QMessageBox.warning(self, "Bad Parts", "Alert coordinator is not initialized.")
            return
        snapshot = self.alert_coordinator.get_bad_parts_snapshot(include_resolved=False)
        self.unack_records = list(snapshot.get("unacknowledged", []))
        self.ack_records = list(snapshot.get("acknowledged", []))
        self._populate_table(self.unack_table, self.unack_records)
        self._populate_table(self.ack_table, self.ack_records)
        self.tabs.setTabText(0, f"Unacknowledged ({len(self.unack_records)})")
        self.tabs.setTabText(1, f"Acknowledged ({len(self.ack_records)})")

    @staticmethod
    def _selected_rows(table: QTableWidget) -> List[int]:
        selection = table.selectionModel()
        if selection is None:
            return []
        return sorted({index.row() for index in selection.selectedRows()})

    def acknowledge_selected(self):
        rows = self._selected_rows(self.unack_table)
        if not rows:
            QMessageBox.information(self, "Bad Parts", "Select at least one unacknowledged row.")
            return
        keys: List[TrackerBadPartKey] = [self.unack_records[row].key for row in rows if row < len(self.unack_records)]
        if not keys:
            return
        self.alert_coordinator.acknowledge_keys(keys)
        self.refresh_data()

    def unacknowledge_selected(self):
        rows = self._selected_rows(self.ack_table)
        if not rows:
            QMessageBox.information(self, "Bad Parts", "Select at least one acknowledged row.")
            return
        keys: List[TrackerBadPartKey] = [self.ack_records[row].key for row in rows if row < len(self.ack_records)]
        if not keys:
            return
        self.alert_coordinator.unacknowledge_keys(keys)
        self.refresh_data()

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
        self.alert_signal = AlertSignal()
        self.alert_signal.new_batch.connect(self._show_bad_parts_alert_dialog)
        self.pending_job_signal = PendingJobSignal()
        self.pending_job_signal.new_job.connect(self._show_pending_job_prompt_dialog)
        self.auto_release_signal = AutoReleaseSignal()
        self.auto_release_signal.new_job.connect(self._show_auto_release_dialog)
        self.jobs_dashboard_signal = JobsDashboardSignal()
        self.jobs_dashboard_signal.refresh_requested.connect(self._refresh_jobs_dashboard)
        self.alert_coordinator = None
        self.bad_parts_center_dialog = None
        self.jobs_table = None

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
        self.setup_processing_tab()
        self.setup_actions_tab()
        self.setup_jobs_tab()
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

        # Restart Time + Retention
        form_layout = QFormLayout()
        self.restart_time_edit = QTimeEdit()
        self.restart_time_edit.setDisplayFormat("HH:mm")
        form_layout.addRow("Daily Restart Time:", self.restart_time_edit)

        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 365)
        self.retention_spin.setSuffix(" days")
        form_layout.addRow("Keep Backups For:", self.retention_spin)

        layout.addWidget(backup_group)
        layout.addLayout(form_layout)
        layout.addStretch()
        self.tabs.addTab(tab, "Schedule")

    def setup_processing_tab(self):
        tab = QWidget()
        root_layout = QVBoxLayout(tab)
        layout = QFormLayout()

        self.pdf_delay_spin = QSpinBox()
        self.pdf_delay_spin.setMaximum(100000)
        self.folder_delay_spin = QSpinBox()
        self.folder_delay_spin.setMaximum(100000)

        layout.addRow("PDF Conversion Delay (s):", self.pdf_delay_spin)
        layout.addRow("New Folder Delay (s):", self.folder_delay_spin)

        root_layout.addLayout(layout)

        alerts_group = QGroupBox("Bad Parts Alerts")
        alerts_layout = QFormLayout()

        self.bad_parts_mode_combo = QComboBox()
        self.bad_parts_mode_combo.addItem("Tracker Mode", "tracker")
        self.bad_parts_mode_combo.addItem("Legacy PDF Highlight Mode", "legacy")

        self.bad_parts_popup_checkbox = QCheckBox("Show always-on-top alert popup")
        self.bad_parts_toast_checkbox = QCheckBox("Show Windows toast notifications")

        self.bad_parts_sound_combo = QComboBox()
        self.bad_parts_sound_combo.addItem("Triple Beep", "triple_beep")
        self.bad_parts_sound_combo.addItem("No Sound", "none")

        self.test_bad_parts_alert_btn = QPushButton("Test Alert")
        self.test_bad_parts_alert_btn.clicked.connect(self.trigger_test_bad_parts_alert)

        alerts_layout.addRow("Detection Mode:", self.bad_parts_mode_combo)
        alerts_layout.addRow("", self.bad_parts_popup_checkbox)
        alerts_layout.addRow("", self.bad_parts_toast_checkbox)
        alerts_layout.addRow("Sound Profile:", self.bad_parts_sound_combo)
        alerts_layout.addRow("", self.test_bad_parts_alert_btn)
        alerts_group.setLayout(alerts_layout)

        root_layout.addWidget(alerts_group)
        root_layout.addStretch()

        self.tabs.addTab(tab, "Processing & Alerts")


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

    def setup_jobs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info_label = QLabel(
            "Job state from each job's .metadata/deployment_gate.json. "
            "Double-click a row to release, snooze, or re-parse."
        )
        layout.addWidget(info_label)

        headers = [
            "Job",
            "State",
            "Selected Mode",
            "Detected Mode",
            "Mode Source",
            "Remind At",
            "Updated At",
        ]
        self.jobs_table = QTableWidget(0, len(headers), tab)
        self.jobs_table.setHorizontalHeaderLabels(headers)
        self.jobs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.setAlternatingRowColors(True)
        self.jobs_table.setSortingEnabled(False)
        self.jobs_table.itemDoubleClicked.connect(self._open_selected_job_dialog)
        header = self.jobs_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.jobs_table)

        actions = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_jobs_dashboard)
        actions.addWidget(refresh_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self.tabs.addTab(tab, "Jobs")
        self.refresh_jobs_dashboard()

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

    def set_alert_coordinator(self, alert_coordinator):
        self.alert_coordinator = alert_coordinator

    def show_part_preview(self, record: BadPartDetailRecord):
        dialog = BadPartPreviewDialog(record, parent=self)
        dialog.exec()

    def show_bad_parts_center(self):
        if self.config.bad_parts_mode != "tracker":
            QMessageBox.information(
                self,
                "Bad Parts",
                "Bad Parts Center is available only in Tracker mode.",
            )
            return
        if self.alert_coordinator is None:
            QMessageBox.warning(self, "Bad Parts", "Alert coordinator is not initialized.")
            return

        if self.bad_parts_center_dialog is None:
            self.bad_parts_center_dialog = BadPartsCenterDialog(self, parent=self)
        self.bad_parts_center_dialog.refresh_data()
        self.bad_parts_center_dialog.show()
        self.bad_parts_center_dialog.raise_()
        self.bad_parts_center_dialog.activateWindow()

    def emit_bad_parts_alert(self, batch: AlertBatch):
        self.alert_signal.new_batch.emit(batch)

    def emit_pending_job_prompt(self, job_folder_name: str):
        self.pending_job_signal.new_job.emit(str(job_folder_name))

    def emit_auto_release_notice(self, job_folder_name: str):
        self.auto_release_signal.new_job.emit(str(job_folder_name))

    def refresh_jobs_dashboard(self):
        self.jobs_dashboard_signal.refresh_requested.emit()

    def _refresh_jobs_dashboard(self):
        if self.jobs_table is None:
            return
        if not self.app_instance or not hasattr(self.app_instance, "get_jobs_dashboard_rows"):
            self.jobs_table.setRowCount(0)
            return
        rows = self.app_instance.get_jobs_dashboard_rows()
        self._populate_jobs_table(rows)

    def _populate_jobs_table(self, rows: List[Dict]):
        if self.jobs_table is None:
            return
        from .deployment_gate import derive_state

        state_styles = {
            "PENDING": (QColor("#FEF3C7"), QColor("#92400E")),
            "PARSING": (QColor("#DBEAFE"), QColor("#1E40AF")),
            "ACTIVE":  (QColor("#D1FAE5"), QColor("#065F46")),
        }

        self.jobs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            timers = row.get("timers", {}) if isinstance(row.get("timers"), dict) else {}
            state_name = derive_state(row)
            bg, fg = state_styles.get(state_name, (None, None))

            values = [
                str(row.get("jobFolderName", "")),
                state_name,
                str(row.get("selectedMode", "UNKNOWN")),
                str(mode_detection.get("candidate", "UNKNOWN")),
                str(mode_detection.get("source", "UNKNOWN")),
                str(timers.get("remindAt") or "-"),
                str(row.get("updatedAt") or "-"),
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if bg is not None:
                    item.setBackground(bg)
                    item.setForeground(fg)
                self.jobs_table.setItem(row_index, col_index, item)

    def _selected_job_folder_name(self) -> Optional[str]:
        if self.jobs_table is None:
            return None
        selection = self.jobs_table.selectionModel()
        if selection is None:
            return None
        rows = selection.selectedRows()
        if not rows:
            return None
        row_index = rows[0].row()
        item = self.jobs_table.item(row_index, 0)
        if item is None:
            return None
        return item.text().strip() or None

    def _open_selected_job_dialog(self, *args):
        job_folder_name = self._selected_job_folder_name()
        if not job_folder_name:
            return
        self._show_pending_job_prompt_dialog(job_folder_name)

    def _get_job_row_by_name(self, job_folder_name: str) -> Optional[Dict]:
        if not self.app_instance or not hasattr(self.app_instance, "get_jobs_dashboard_rows"):
            return None
        name = str(job_folder_name or "").strip()
        if not name:
            return None
        for row in self.app_instance.get_jobs_dashboard_rows():
            if str(row.get("jobFolderName", "")).strip().lower() == name.lower():
                return row
        return None

    def _show_pending_job_prompt_dialog(self, job_folder_name: str):
        if not self.app_instance:
            return
        job_folder_name = str(job_folder_name or "").strip()
        if not job_folder_name:
            return

        from .deployment_gate import derive_state

        state = self._get_job_row_by_name(job_folder_name) or {}
        derived = derive_state(state)
        mode_detection = state.get("modeDetection", {}) if isinstance(state.get("modeDetection"), dict) else {}
        detected_mode = str(mode_detection.get("candidate") or "UNKNOWN")
        detected_source = str(mode_detection.get("source") or "UNKNOWN")
        selected_mode = str(state.get("selectedMode") or "UNKNOWN")
        default_mode = selected_mode if selected_mode and selected_mode != "UNKNOWN" else detected_mode
        if not default_mode:
            default_mode = "UNKNOWN"

        eyebrow = "New job pending" if derived == "PENDING" else f"Released job ({derived})"

        dialog = QDialog(self)
        dialog.setObjectName("jobActionDialog")
        dialog.setWindowTitle(f"Job: {job_folder_name}")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dialog.resize(540, 260)
        dialog.setStyleSheet(
            "QDialog#jobActionDialog { background: #F8FAFC; }"
            "QLabel#dialogEyebrow { color: #F97316; font-size: 11px; font-weight: 600; }"
            "QLabel#dialogJobName { color: #334155; font-size: 16px; font-weight: 600; }"
            "QPushButton { padding: 6px 14px; border: 1px solid #CBD5E1; border-radius: 6px; "
            "background: #FFFFFF; color: #334155; }"
            "QPushButton:hover { background: #F1F5F9; }"
            "QPushButton#primaryButton { background: #F97316; color: #FFFFFF; border: 1px solid #F97316; }"
            "QPushButton#primaryButton:hover { background: #EA580C; }"
        )

        layout = QVBoxLayout(dialog)

        eyebrow_label = QLabel(eyebrow)
        eyebrow_label.setObjectName("dialogEyebrow")
        layout.addWidget(eyebrow_label)
        job_label = QLabel(job_folder_name)
        job_label.setObjectName("dialogJobName")
        layout.addWidget(job_label)
        layout.addWidget(QLabel(f"Detected mode: {detected_mode} ({detected_source})"))

        form = QFormLayout()
        mode_combo = QComboBox(dialog)
        mode_combo.setEditable(True)
        mode_combo.addItems(["FACE-FRAME", "FRAMELESS", "BOTH", "UNKNOWN"])
        mode_combo.setCurrentText(default_mode)
        if derived != "PENDING":
            mode_combo.setEnabled(False)
        form.addRow("Deploy Mode:", mode_combo)
        layout.addLayout(form)

        action_row = QHBoxLayout()

        if derived == "PENDING":
            remind_label = QLabel("Remind in")
            remind_spin = QSpinBox(dialog)
            remind_spin.setRange(1, 720)
            remind_spin.setValue(15)
            remind_spin.setSuffix(" min")
            snooze_btn = QPushButton("Snooze")
            cancel_btn = QPushButton("Cancel")
            release_btn = QPushButton("Release")
            release_btn.setObjectName("primaryButton")

            def _snooze_action():
                self.app_instance.remind_pending_job(job_folder_name, minutes=remind_spin.value())
                self.refresh_jobs_dashboard()
                dialog.accept()

            def _release_action():
                selected = mode_combo.currentText().strip() or "UNKNOWN"
                import threading
                threading.Thread(
                    target=self.app_instance.deploy_pending_job,
                    args=(job_folder_name, selected),
                    daemon=True,
                ).start()
                self.refresh_jobs_dashboard()
                dialog.accept()

            snooze_btn.clicked.connect(_snooze_action)
            cancel_btn.clicked.connect(dialog.reject)
            release_btn.clicked.connect(_release_action)

            action_row.addWidget(remind_label)
            action_row.addWidget(remind_spin)
            action_row.addWidget(snooze_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)
            action_row.addWidget(release_btn)
        else:
            reparse_btn = QPushButton("Re-parse")
            cancel_btn = QPushButton("Cancel")

            def _reparse_action():
                reply = QMessageBox.question(
                    dialog,
                    "Re-parse Job",
                    f"Are you sure you want to fully re-parse job '{job_folder_name}'?\n\n"
                    "This will remove all generated metadata, GLBs, and dark mode PDFs, then re-process them.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    import threading
                    threading.Thread(
                        target=self.app_instance.reparse_job,
                        args=(job_folder_name,),
                        daemon=True,
                    ).start()
                    QMessageBox.information(
                        dialog,
                        "Re-parse Job",
                        f"Re-parsing for job '{job_folder_name}' has been started in the background.",
                    )
                    self.refresh_jobs_dashboard()
                    dialog.accept()

            reparse_btn.clicked.connect(_reparse_action)
            cancel_btn.clicked.connect(dialog.reject)

            action_row.addWidget(reparse_btn)
            action_row.addStretch()
            action_row.addWidget(cancel_btn)

        layout.addLayout(action_row)
        dialog.exec()

    def _show_auto_release_dialog(self, job_folder_name: str):
        if not self.app_instance:
            return
        job_folder_name = str(job_folder_name or "").strip()
        if not job_folder_name:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Job Auto-Released: {job_folder_name}")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dialog.resize(500, 180)

        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "This job auto-released after 30 hours with no action.\n"
                "It has been deployed and made visible in production."
            )
        )
        layout.addWidget(QLabel(job_folder_name))

        action_row = QHBoxLayout()
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.clicked.connect(dialog.accept)
        action_row.addStretch()
        action_row.addWidget(dismiss_btn)
        layout.addLayout(action_row)
        dialog.exec()

    def _show_bad_parts_alert_dialog(self, batch: AlertBatch):
        if not batch.events:
            return
        if self.alert_coordinator is None:
            return

        records = self.alert_coordinator.build_detail_records_for_events(batch.events)
        if not records:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"BAD PART ALERT ({len(records)})")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dialog.resize(1480, 650)

        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "New bad parts were detected from tracker data.\n"
                "Review and acknowledge all or selected rows."
            )
        )

        headers = ["Job", "Material", "PDF", "Page", "Part #", "Part Name", "Size", "Cabinet", "Room", "Detected", "View"]
        table = QTableWidget(len(records), len(headers), dialog)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        def _size_text(record: BadPartDetailRecord) -> str:
            if record.width is None or record.length is None:
                return "-"
            return f'{record.width:.3f}" x {record.length:.3f}"'

        for row_index, record in enumerate(records):
            table.setItem(row_index, 0, QTableWidgetItem(record.key.job_folder_name))
            table.setItem(row_index, 1, QTableWidgetItem(record.material))
            table.setItem(row_index, 2, QTableWidgetItem(record.pdf_filename))
            table.setItem(row_index, 3, QTableWidgetItem(str(record.page)))
            table.setItem(row_index, 4, QTableWidgetItem(str(record.part_number)))
            table.setItem(row_index, 5, QTableWidgetItem(record.part_name))
            table.setItem(row_index, 6, QTableWidgetItem(_size_text(record)))
            table.setItem(row_index, 7, QTableWidgetItem(str(record.cabinet_number) if record.cabinet_number is not None else "-"))
            table.setItem(row_index, 8, QTableWidgetItem(record.room or "-"))
            table.setItem(row_index, 9, QTableWidgetItem(record.detected_at or "-"))
            view_btn = QPushButton("View Page")
            view_btn.clicked.connect(lambda _, rec=record: self.show_part_preview(rec))
            table.setCellWidget(row_index, 10, view_btn)

        layout.addWidget(table)

        actions = QHBoxLayout()
        actions.addStretch()

        acknowledge_btn = QPushButton("Acknowledge All")
        acknowledge_selected_btn = QPushButton("Acknowledge Selected")
        dismiss_btn = QPushButton("Dismiss")

        def _acknowledge_and_close():
            if self.alert_coordinator:
                self.alert_coordinator.acknowledge_batch(batch)
            dialog.accept()

        def _acknowledge_selected_and_close():
            selection = table.selectionModel()
            if selection is None:
                QMessageBox.information(dialog, "Bad Parts", "Select at least one row.")
                return
            row_indexes = sorted({index.row() for index in selection.selectedRows()})
            if not row_indexes:
                QMessageBox.information(dialog, "Bad Parts", "Select at least one row.")
                return
            keys: List[TrackerBadPartKey] = []
            for row in row_indexes:
                if 0 <= row < len(records):
                    keys.append(records[row].key)
            if not keys:
                QMessageBox.information(dialog, "Bad Parts", "No selectable records found.")
                return
            self.alert_coordinator.acknowledge_keys(keys)
            dialog.accept()

        acknowledge_btn.clicked.connect(_acknowledge_and_close)
        acknowledge_selected_btn.clicked.connect(_acknowledge_selected_and_close)
        dismiss_btn.clicked.connect(dialog.reject)

        actions.addWidget(dismiss_btn)
        actions.addWidget(acknowledge_selected_btn)
        actions.addWidget(acknowledge_btn)
        layout.addLayout(actions)
        dialog.exec()

    def trigger_test_bad_parts_alert(self):
        if self.alert_coordinator:
            self.alert_coordinator.test_alert()
            QMessageBox.information(self, "Bad Parts Alert", "Test alert queued.")
        else:
            QMessageBox.warning(self, "Bad Parts Alert", "Alert coordinator is not initialized yet.")

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

        self.retention_spin.setValue(getattr(self.config, 'backup_retention_days', 7))

        h, m = map(int, self.config.daily_restart_time.split(':'))
        self.restart_time_edit.setTime(QTime(h, m))

        self.pdf_delay_spin.setValue(self.config.pdf_conversion_delay_seconds)
        self.folder_delay_spin.setValue(self.config.new_folder_delay_seconds)

        mode_index = self.bad_parts_mode_combo.findData(self.config.bad_parts_mode)
        self.bad_parts_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.bad_parts_popup_checkbox.setChecked(bool(self.config.bad_parts_popup_enabled))
        self.bad_parts_toast_checkbox.setChecked(bool(self.config.bad_parts_toast_enabled))
        sound_index = self.bad_parts_sound_combo.findData(self.config.bad_parts_sound_profile)
        self.bad_parts_sound_combo.setCurrentIndex(sound_index if sound_index >= 0 else 0)

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
        self.config.backup_retention_days = self.retention_spin.value()

        self.config.daily_restart_time = self.restart_time_edit.time().toString("HH:mm")

        self.config.pdf_conversion_delay_seconds = self.pdf_delay_spin.value()
        self.config.new_folder_delay_seconds = self.folder_delay_spin.value()
        self.config.bad_parts_mode = self.bad_parts_mode_combo.currentData()
        self.config.bad_parts_popup_enabled = self.bad_parts_popup_checkbox.isChecked()
        self.config.bad_parts_toast_enabled = self.bad_parts_toast_checkbox.isChecked()
        self.config.bad_parts_sound_profile = self.bad_parts_sound_combo.currentData()

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

