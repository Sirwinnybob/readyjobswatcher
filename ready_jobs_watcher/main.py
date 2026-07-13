"""
Core Application Module for Ready Jobs Watcher.

This module contains the primary Application class responsible for coordinating
file watchers, background tasks, GUI elements, and the system tray icon.
"""
import os
import sys
import threading
import logging
import time
import atexit
import datetime

from watchdog.observers import Observer
from concurrent.futures import ThreadPoolExecutor


from .config import Config, BASE_DATA_DIR
from .file_handler import JobProcessor, is_retryable_os_error
from .utils import clear_old_logs, is_hidden
from .bad_parts_checker import load_blacklist, load_permanently_ignored_blacklist
from .watchers import RenameHandler, PdfChangeHandler
from .scheduler import (
    backup_scheduler,
    cnc_scan_scheduler,
    stats_logger_scheduler,
    daily_restart_scheduler,
    pending_autorelease_scheduler,
    pending_reminder_scheduler,
    metadata_end_of_day_scheduler,
    sync_conflict_resolver_scheduler,
)
from PyQt6.QtWidgets import QApplication
from .gui import SettingsWindow
from .tray_icon import create_tray_icon
from .tracker_bad_parts import TrackerBadPartsMonitor
from .alert_coordinator import AlertCoordinator, AlertBatch
from .cabinet_sheet_indexer import (
    build_reference_index_for_job,
    detect_mode_for_job,
    detect_mode_template_mismatch_for_job,
)
from .duplicate_job_guard import find_job_number_collision, write_duplicate_suspect_marker
from .hardwoods_cutlist_indexer import build_hardwoods_cutlist_index_for_job
from .dae_converter import convert_3d_models_for_job, scan_root_for_missing_glbs
from .deployment_gate import DeploymentGateManager
from .notifications import send_notification, send_critical_alert
from .metadata_refresh import MetadataRefreshService
from .polling import ReadyJobsPoller

ROOT_RECONNECT_POLL_SECONDS = 30


# --- Logging Setup ---
def setup_logging():
    """
    Configure multiple loggers for distinct application components.

    Returns:
        logging.Logger: The configured main logger instance.
    """
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # main.py itself logs via bare logging.info/warning/...() calls (root logger),
    # not a named logger. Without handlers here those records were silently
    # dropped (INFO/DEBUG) or only reached stderr via Python's WARNING+ last-resort
    # handler (never ready_jobs_watcher.log) - including every root-availability,
    # reconnect, and observer-start message. Give the root logger real handlers so
    # bare calls are actually persisted; 'main' below relies on propagation to them.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_file_handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, 'ready_jobs_watcher.log'))
    root_file_handler.setFormatter(formatter)
    root_logger.addHandler(root_file_handler)
    root_console = logging.StreamHandler()
    root_console.setFormatter(formatter)
    root_console.setLevel(logging.INFO)
    root_logger.addHandler(root_console)

    loggers = {
        'backup': 'backup.log',
        'cnc': 'cnc_scan.log',
        'badparts': 'bad_parts.log',
        'pdf_darkmode': 'send_notification.log',
        'pending_queue': 'ready_jobs_watcher.log',
    }

    for name, filename in loggers.items():
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, filename))
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        # Keep this subsystem's records out of the root handlers above so they
        # don't get duplicated into ready_jobs_watcher.log.
        logger.propagate = False

    # 'main' gets no handler of its own; it propagates to the root logger's
    # handlers configured above, which write to the same ready_jobs_watcher.log.
    main_logger = logging.getLogger('main')
    main_logger.setLevel(logging.DEBUG)

    return main_logger

# --- Core Application Class ---
class Application:
    """
    Main application controller managing background threads, observers, and state.

    Responsibilities include initializing the file system observers, coordinating
    configuration state, executing scheduled tasks, and properly acquiring system locks.
    """
    def __init__(self):
        """Initialize the Application instance with default state and component setup."""
        self.config = Config()
        self.stop_event = threading.Event()

        self.PAUSE_PROCESSING = False
        self.PENDING_RENAMES = {}
        self.pending_renames_lock = threading.Lock()  # Lock for thread-safe access to PENDING_RENAMES
        self.LAST_BACKUP_TIME = None

        # Thread pool for background operations (prevents unbounded thread creation)
        self.executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="RJW-Worker")

        self.job_processor = JobProcessor(self.config, self)

        # Initialize persistent pending queue
        from .pending_queue import PendingQueue
        queue_file = os.path.join(BASE_DATA_DIR, 'pending_queue.json')
        self.pending_queue = PendingQueue(queue_file, executor=self.executor)

        self.observer = Observer()
        self.pdf_observer = Observer()
        self.desktop_observer = Observer()
        self.rename_event_handler = None
        self.pdf_event_handler = None
        self._observer_lock = threading.RLock()
        self._pending_operations_restored = False
        self.poller = None
        self.poller_thread = None
        self._polling_started = False
        self._root_unavailable_logged = False
        self._root_offline_since = None

        self.retry_thread = None
        self.backup_thread = None
        self.cnc_scan_thread = None
        self.stats_thread = None
        self.sync_conflict_thread = None
        self.restart_thread = None
        self.pending_autorelease_thread = None
        self.pending_reminder_thread = None
        self.metadata_end_of_day_thread = None
        self.observer_monitor_thread = None
        self.tray_thread = None

        self.qapp = None
        self.settings_window = None
        self.icon = None
        self._pending_alert_batches = []
        self._pending_alert_lock = threading.Lock()
        self._pending_job_prompts = []
        self._pending_job_prompt_lock = threading.Lock()
        from .debounce import DebouncedTimerMap
        self._pending_job_timers = DebouncedTimerMap(name_prefix="PendingPrompt")
        self._pending_auto_release_notices = []
        self._pending_auto_release_notices_lock = threading.Lock()
        self.deployment_gate = DeploymentGateManager(self.config.ROOT_DIR)
        self.metadata_refresh_service = MetadataRefreshService(self.config)
        self.tracker_monitor = TrackerBadPartsMonitor(self.config, deployment_gate=self.deployment_gate)
        self.alert_coordinator = AlertCoordinator(
            self.config,
            self.tracker_monitor,
            popup_notifier=self._queue_bad_parts_popup
        )

    def _queue_bad_parts_popup(self, batch: AlertBatch):
        if self.settings_window:
            self.settings_window.emit_bad_parts_alert(batch)
            return
        with self._pending_alert_lock:
            self._pending_alert_batches.append(batch)

    def _flush_pending_bad_parts_popups(self):
        if not self.settings_window:
            return
        with self._pending_alert_lock:
            batches = list(self._pending_alert_batches)
            self._pending_alert_batches.clear()
        for batch in batches:
            self.settings_window.emit_bad_parts_alert(batch)

    def _queue_pending_job_prompt(self, job_folder_name: str):
        if self.settings_window:
            self.settings_window.emit_pending_job_prompt(job_folder_name)
            return
        with self._pending_job_prompt_lock:
            self._pending_job_prompts.append(job_folder_name)

    def _flush_pending_job_prompts(self):
        if not self.settings_window:
            return
        with self._pending_job_prompt_lock:
            jobs = list(self._pending_job_prompts)
            self._pending_job_prompts.clear()
        for job in jobs:
            self.settings_window.emit_pending_job_prompt(job)

    def _queue_auto_release_notice(self, job_folder_name: str):
        if self.settings_window:
            self.settings_window.emit_auto_release_notice(job_folder_name)
            return
        with self._pending_auto_release_notices_lock:
            self._pending_auto_release_notices.append(job_folder_name)

    def _flush_pending_auto_release_notices(self):
        if not self.settings_window:
            return
        with self._pending_auto_release_notices_lock:
            jobs = list(self._pending_auto_release_notices)
            self._pending_auto_release_notices.clear()
        for job in jobs:
            self.settings_window.emit_auto_release_notice(job)

    def on_new_job_folder_detected(self, folder_path: str):
        root_norm = os.path.normcase(os.path.normpath(self.config.ROOT_DIR))
        parent_norm = os.path.normcase(os.path.normpath(os.path.dirname(folder_path)))
        if parent_norm != root_norm or not JobProcessor.is_job_folder(folder_path):
            logging.debug("Ignoring pending gate creation for non-root/non-job folder: %s", folder_path)
            return
        job_folder_name = os.path.basename(os.path.normpath(folder_path))

        job_num = JobProcessor.extract_job_number(job_folder_name) or ""
        collided_with = find_job_number_collision(self.config.ROOT_DIR, job_folder_name, job_num)
        if collided_with:
            write_duplicate_suspect_marker(self.config.ROOT_DIR, job_folder_name, collided_with)
            logging.warning(
                "Suspected duplicate job folder: %s shares job number %s with existing job %s; "
                "not adopting as a new job (see .metadata/duplicate_suspect.json).",
                job_folder_name,
                job_num,
                collided_with,
            )
            return

        detected_mode = "UNKNOWN"
        detection_source = "UNKNOWN"
        try:
            detected_mode, detection_source = detect_mode_for_job(folder_path)
        except Exception as exc:
            logging.warning("Mode detection failed for %s: %s", folder_path, exc)
        state = self.deployment_gate.ensure_pending_for_new_job(
            job_folder_name,
            detected_mode=detected_mode,
            detection_source=detection_source,
        )
        self._queue_pending_job_prompt(job_folder_name)
        try:
            mismatch = detect_mode_template_mismatch_for_job(folder_path)
            if mismatch is not None:
                expected = mismatch.get("deliveryMode", "UNKNOWN")
                exported = mismatch.get("assemblyMode", "UNKNOWN")
                warning = (
                    f"{job_folder_name}: template mismatch detected. "
                    f"Delivery mode is {expected}, but assembly export looks like {exported}."
                )
                logging.warning(warning)
                send_notification(
                    title="Ready Jobs Template Mismatch",
                    message=warning,
                    duration="long",
                )
        except Exception as exc:
            logging.warning("Template mismatch validation failed for %s: %s", folder_path, exc)
        logging.info(
            "New job marked pending: job=%s modeCandidate=%s source=%s",
            job_folder_name,
            state.get("modeDetection", {}).get("candidate"),
            state.get("modeDetection", {}).get("source"),
        )

    def _schedule_pending_job_prompt(self, job_folder_name: str, delay_seconds: int):
        delay_seconds = max(1, int(delay_seconds))
        self._pending_job_timers.schedule(
            job_folder_name,
            delay_seconds,
            lambda: self._queue_pending_job_prompt(job_folder_name),
        )

    def remind_pending_job(self, job_folder_name: str, minutes: int = 15):
        self.deployment_gate.schedule_reminder(job_folder_name, minutes=minutes)
        self._schedule_pending_job_prompt(job_folder_name, delay_seconds=minutes * 60)

    def _remind_pending_job_due(self, job_folder_name: str) -> None:
        self._queue_pending_job_prompt(job_folder_name)
        logging.info("Pending job reminder due, re-prompting: %s", job_folder_name)

    def set_job_selected_mode(self, job_folder_name: str, selected_mode: str):
        state = self.deployment_gate.set_selected_mode(job_folder_name, selected_mode)
        self.schedule_metadata_refresh_for_job(job_folder_name, "deployment_gate_updated")
        logging.info(
            "Job selected mode updated: job=%s selectedMode=%s",
            job_folder_name,
            state.get("selectedMode", "UNKNOWN"),
        )
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def set_job_detected_mode(self, job_folder_name: str, detected_mode: str, source: str = "MANUAL_OVERRIDE"):
        state = self.deployment_gate.set_mode_detection(
            job_folder_name,
            detected_mode,
            source=source,
            mark_as_operator_action=True,
        )
        self.schedule_metadata_refresh_for_job(job_folder_name, "deployment_gate_updated")
        logging.info(
            "Job detected mode updated: job=%s detectedMode=%s source=%s",
            job_folder_name,
            (state.get("modeDetection") or {}).get("candidate", "UNKNOWN"),
            (state.get("modeDetection") or {}).get("source", "UNKNOWN"),
        )
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def _backfill_modes_for_existing_jobs(self):
        rows = self.deployment_gate.list_job_states()
        for row in rows:
            job_folder_name = str(row.get("jobFolderName", "")).strip()
            if not job_folder_name:
                continue
            job_path = os.path.join(self.config.ROOT_DIR, job_folder_name)
            if not os.path.isdir(job_path) or not JobProcessor.is_job_folder(job_path):
                continue

            selected_mode = str(row.get("selectedMode") or "UNKNOWN")
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            detected_mode = str(mode_detection.get("candidate") or "UNKNOWN")
            detection_source = str(mode_detection.get("source") or "UNKNOWN")

            next_detected_mode = detected_mode
            next_detection_source = detection_source
            if detected_mode == "UNKNOWN":
                try:
                    next_detected_mode, next_detection_source = detect_mode_for_job(job_path)
                except Exception as exc:
                    logging.warning("Mode backfill detection failed for %s: %s", job_path, exc)
                    next_detected_mode, next_detection_source = "UNKNOWN", "UNKNOWN"
                if next_detected_mode != detected_mode or next_detection_source != detection_source:
                    self.deployment_gate.set_mode_detection(
                        job_folder_name,
                        candidate=next_detected_mode,
                        source=next_detection_source,
                        mark_as_operator_action=False,
                    )

            if selected_mode == "UNKNOWN" and next_detected_mode != "UNKNOWN":
                self.deployment_gate.set_selected_mode(
                    job_folder_name,
                    next_detected_mode,
                    mark_as_operator_action=False,
                )

    def get_jobs_dashboard_rows(self):
        from .duplicate_job_guard import read_duplicate_suspect_marker

        rows = self.deployment_gate.list_job_states()
        for row in rows:
            job_folder_name = str(row.get("jobFolderName", "")).strip()
            if not job_folder_name:
                continue
            marker = read_duplicate_suspect_marker(self.config.ROOT_DIR, job_folder_name)
            if marker is not None:
                row["duplicateSuspect"] = marker
        return rows

    def deploy_pending_job(self, job_folder_name: str, selected_mode: str):
        self.deployment_gate.mark_deployed(job_folder_name, selected_mode=selected_mode)
        self.deployment_gate.clear_timers(job_folder_name)
        self._pending_job_timers.cancel(job_folder_name)
        parse_ready = self._parse_job_after_deploy(job_folder_name)
        self.deployment_gate.mark_parse_ready(job_folder_name, parse_ready=parse_ready)
        self.schedule_metadata_refresh_for_job(job_folder_name, "job_deployed")
        logging.info(
            "Job deployed: job=%s parseReady=%s selectedMode=%s",
            job_folder_name,
            parse_ready,
            selected_mode,
        )
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def auto_release_pending_job(self, job_folder_name: str, selected_mode: str) -> bool:
        state = self.deployment_gate.load_state(job_folder_name, create_if_missing=False, default_deployed=False)
        if bool(state.get("deployed", True)):
            return False
        self.deployment_gate.update_state(job_folder_name, hiddenFromProduction=False, operator_action=False)
        self.deploy_pending_job(job_folder_name, selected_mode)
        self._queue_auto_release_notice(job_folder_name)
        logging.info("Pending job auto-released after inactivity: %s", job_folder_name)
        return True

    def schedule_pending_job_deploy(self, job_folder_name: str, deploy_at: datetime.datetime, selected_mode: str) -> None:
        state = self.deployment_gate.schedule_deploy(job_folder_name, deploy_at, selected_mode=selected_mode)
        logging.info(
            "Job deploy scheduled: job=%s deployAt=%s selectedMode=%s",
            job_folder_name,
            state.get("timers", {}).get("autoReleaseAt"),
            state.get("selectedMode", "UNKNOWN"),
        )
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def cancel_pending_job_schedule(self, job_folder_name: str) -> None:
        self.deployment_gate.cancel_scheduled_deploy(job_folder_name)
        logging.info("Job deploy schedule cancelled: job=%s", job_folder_name)
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def hide_job_from_production(self, job_folder_name: str) -> None:
        self.deployment_gate.hide_from_production(job_folder_name)
        logging.info("Job hidden from production: job=%s", job_folder_name)
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def show_job_in_production(self, job_folder_name: str) -> None:
        self.deployment_gate.show_in_production(job_folder_name)
        logging.info("Job shown in production: job=%s", job_folder_name)
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def pull_job_from_deployment(self, job_folder_name: str) -> None:
        self.deployment_gate.pull_from_deployment(job_folder_name)
        self._queue_pending_job_prompt(job_folder_name)
        self.schedule_metadata_refresh_for_job(job_folder_name, "job_pulled_from_deployment")
        logging.info("Job pulled from deployment: job=%s", job_folder_name)
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

    def _parse_job_after_deploy(self, job_folder_name: str) -> bool:
        job_path = os.path.join(self.config.ROOT_DIR, job_folder_name)
        if not os.path.isdir(job_path):
            return False
        try:
            self.job_processor.process_job_folder(job_path)
        except Exception as exc:
            logging.error("Job processor failed during deploy parse for %s: %s", job_folder_name, exc, exc_info=True)
        reference_ok = False
        hardwood_ok = False
        try:
            reference_ok = bool(build_reference_index_for_job(job_path))
        except Exception as exc:
            logging.error("Reference index build failed during deploy parse for %s: %s", job_folder_name, exc, exc_info=True)
        try:
            hardwood_ok = bool(build_hardwoods_cutlist_index_for_job(job_path, deployment_gate=self.deployment_gate))
        except Exception as exc:
            logging.error("Hardwoods index build failed during deploy parse for %s: %s", job_folder_name, exc, exc_info=True)
        return reference_ok or hardwood_ok

    def reparse_job(self, job_folder_name: str) -> bool:
        """
        Delete all program-generated files/folders for this job, and re-parse it from scratch.
        """
        job_path = os.path.join(self.config.ROOT_DIR, job_folder_name)
        if not os.path.isdir(job_path):
            logging.error("Cannot re-parse job: %s is not a directory.", job_path)
            return False

        logging.info("Re-parsing job %s (removing old parsed data first)...", job_folder_name)

        import shutil

        # 1. Safely remove generated files:
        # a) Delete DARK MODE subfolder
        dark_mode_dir = os.path.join(job_path, "DARK MODE")
        try:
            shutil.rmtree(dark_mode_dir)
            logging.info("Deleted DARK MODE folder for %s", job_folder_name)
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.error("Failed to delete DARK MODE directory for %s: %s", job_folder_name, e)

        # b) Delete generated remake bad parts candidate file inside CNC/.metadata/
        from .remake_candidates_indexer import REMAKE_CANDIDATES_FILENAME
        candidates_file = os.path.join(job_path, self.config.CNC_SUBDIR, ".metadata", REMAKE_CANDIDATES_FILENAME)
        try:
            os.remove(candidates_file)
            logging.info("Deleted generated remake bad parts candidate file: %s", candidates_file)
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.error("Failed to delete remake bad parts candidate file %s: %s", candidates_file, e)

        # c) Delete generated 3d_medium.glb files inside 3D/<ROOM>/3d_medium.glb
        three_d_dir = os.path.join(job_path, "3D")
        try:
            for root, dirs, files in os.walk(three_d_dir):
                for file in files:
                    if file == "3d_medium.glb":
                        file_path = os.path.join(root, file)
                        try:
                            os.remove(file_path)
                            logging.info("Deleted generated GLB file: %s", file_path)
                        except FileNotFoundError:
                            pass
                        except Exception as e:
                            logging.error("Failed to delete GLB file %s: %s", file_path, e)
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.error("Failed to scan/delete GLB files for %s: %s", job_folder_name, e)

        # d) Delete specific program-created metadata files/folders and cache_static.json
        metadata_dir = os.path.join(job_path, ".metadata")
        for fname in ("cabinet_sheet_index.json", "cache_static.json"):
            path = os.path.join(metadata_dir, fname)
            try:
                os.remove(path)
                logging.info("Deleted metadata file: %s", path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logging.error("Failed to delete metadata file %s: %s", path, e)

        hardwoods_dir = os.path.join(metadata_dir, "hardwoods")
        try:
            shutil.rmtree(hardwoods_dir)
            logging.info("Deleted metadata folder: %s", hardwoods_dir)
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.error("Failed to delete metadata directory %s: %s", hardwoods_dir, e)

        # 2. Reset parseReady to False in deployment gate
        self.deployment_gate.mark_parse_ready(job_folder_name, parse_ready=False)
        self.schedule_metadata_refresh_for_job(job_folder_name, "job_reparse_started")

        # 3. Perform re-parsing steps
        # a) Process job folder (prefixes files if needed)
        try:
            self.job_processor.process_job_folder(job_path)
        except Exception as exc:
            logging.error("Job processor failed during re-parse for %s: %s", job_folder_name, exc, exc_info=True)

        # b) Build reference index
        reference_ok = False
        try:
            reference_ok = bool(build_reference_index_for_job(job_path))
        except Exception as exc:
            logging.error("Reference index build failed during re-parse for %s: %s", job_folder_name, exc, exc_info=True)

        # c) Build hardwoods cutlist index
        hardwood_ok = False
        try:
            hardwood_ok = bool(build_hardwoods_cutlist_index_for_job(job_path, deployment_gate=self.deployment_gate))
        except Exception as exc:
            logging.error("Hardwoods index build failed during re-parse for %s: %s", job_folder_name, exc, exc_info=True)

        # d) Convert 3D DAE models to GLB
        try:
            convert_3d_models_for_job(job_path)
        except Exception as exc:
            logging.error("3D model conversion failed during re-parse for %s: %s", job_folder_name, exc, exc_info=True)

        # e) Convert PDFs to dark mode
        try:
            from .pdf_dark_mode import process_directory
            process_directory(job_path, force=True)
        except Exception as exc:
            logging.error("PDF dark mode conversion failed during re-parse for %s: %s", job_folder_name, exc, exc_info=True)

        # 4. Re-evaluate parseReady state and mark in deployment gate
        parse_ready = reference_ok or hardwood_ok
        self.deployment_gate.mark_parse_ready(job_folder_name, parse_ready=parse_ready)
        self.schedule_metadata_refresh_for_job(job_folder_name, "job_reparse_complete")

        logging.info("Re-parse complete for %s. Parse ready: %s", job_folder_name, parse_ready)

        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()

        return True


    def _configure_assimp_path(self):
        """
        Apply optional config-level Assimp override to the process environment.
        """
        configured = (self.config.assimp_path or "").strip() if hasattr(self.config, "assimp_path") else ""
        if configured:
            os.environ["ASSIMP_PATH"] = configured
            logging.info(f"Using configured ASSIMP_PATH: {configured}")
        elif os.environ.get("ASSIMP_PATH"):
            logging.info(f"Using existing ASSIMP_PATH from environment: {os.environ.get('ASSIMP_PATH')}")
        else:
            logging.info("ASSIMP_PATH not configured; dae_converter will use default assimp discovery.")

    def schedule_metadata_refresh_for_job(self, job_folder_name: str, reason: str):
        try:
            job_folder = os.path.join(self.config.ROOT_DIR, job_folder_name)
            self.metadata_refresh_service.schedule_job(job_folder, reason)
        except Exception as exc:
            logging.error("Failed scheduling metadata refresh for %s: %s", job_folder_name, exc, exc_info=True)

    def rename_job(self, old_name: str, new_name: str) -> None:
        old_name = str(old_name or "").strip()
        new_name = str(new_name or "").strip()
        if not old_name or not new_name or old_name == new_name:
            return

        old_num = JobProcessor.extract_job_number(old_name) or ""
        new_num = JobProcessor.extract_job_number(new_name) or ""
        pending_pdf_path_map = {}
        try:
            if self.pending_queue is not None and hasattr(self.pending_queue, "rename_job_folder"):
                result = self.pending_queue.rename_job_folder(old_name, new_name, old_num, new_num)
                if isinstance(result, dict):
                    pending_pdf_path_map = result
        except Exception as exc:
            logging.error("Failed updating pending queue after job rename %s -> %s: %s", old_name, new_name, exc, exc_info=True)

        self._retarget_pending_renames(old_name, new_name, old_num, new_num)

        try:
            handler = getattr(self, "pdf_event_handler", None)
            if handler is not None and hasattr(handler, "retarget_pending_pdf_conversions"):
                handler.retarget_pending_pdf_conversions(pending_pdf_path_map)
        except Exception as exc:
            logging.error("Failed retargeting PDF conversion timers after job rename %s -> %s: %s", old_name, new_name, exc, exc_info=True)

        try:
            if self.tracker_monitor is not None and hasattr(self.tracker_monitor, "rename_job_folder"):
                self.tracker_monitor.rename_job_folder(old_name, new_name, old_num, new_num)
        except Exception as exc:
            logging.error("Failed updating tracker state after job rename %s -> %s: %s", old_name, new_name, exc, exc_info=True)

        try:
            from .bad_parts_checker import rename_blacklist_job
            rename_blacklist_job(old_name, new_name, old_num, new_num)
        except Exception as exc:
            logging.error("Failed updating legacy bad-parts blacklist after job rename %s -> %s: %s", old_name, new_name, exc, exc_info=True)

        try:
            from .job_rename import rename_ready_job
            rename_ready_job(self.config.ROOT_DIR, old_name, new_name)
        except Exception as exc:
            logging.error("Failed executing rename_ready_job in rename_job %s -> %s: %s", old_name, new_name, exc, exc_info=True)

        self.schedule_metadata_refresh_for_job(new_name, "job_renamed")
        if self.settings_window:
            self.settings_window.refresh_jobs_dashboard()
        logging.info("Job rename propagated: %s -> %s", old_name, new_name)

    @staticmethod
    def _target_filename_for_job(job_num: str, original_name: str) -> str:
        from .job_rename import apply_job_number_prefix
        return apply_job_number_prefix(job_num, original_name)

    @staticmethod
    def _retarget_path_for_job_rename(
        path: str,
        old_name: str,
        new_name: str,
        old_num: str,
        new_num: str,
        *,
        update_leaf_filename: bool = True,
    ) -> str:
        from pathlib import Path

        parts = list(Path(path).parts)
        changed = False
        for idx, part in enumerate(parts):
            if part == old_name:
                parts[idx] = new_name
                changed = True
        if not changed:
            return os.path.normpath(path)
        if update_leaf_filename and parts and new_num:
            filename = parts[-1]
            if old_num and filename.startswith(old_num + " - "):
                filename = new_num + " - " + filename[len(old_num + " - "):]
            elif " - " in filename:
                prefix, rest = filename.split(" - ", 1)
                if prefix != new_num:
                    filename = new_num + " - " + rest
            else:
                filename = new_num + " - " + filename
            parts[-1] = filename
        return os.path.normpath(str(Path(*parts)))

    def _retarget_pending_renames(self, old_name: str, new_name: str, old_num: str, new_num: str) -> None:
        if not getattr(self, "PENDING_RENAMES", None):
            return
        with self.pending_renames_lock:
            updated = {}
            for old_path, (job_num, dir_path, original_name, next_retry) in self.PENDING_RENAMES.items():
                normalized_old_path = os.path.normpath(old_path)
                new_path = self._retarget_path_for_job_rename(
                    normalized_old_path,
                    old_name,
                    new_name,
                    old_num,
                    new_num,
                    update_leaf_filename=True,
                )
                new_dir = self._retarget_path_for_job_rename(
                    dir_path,
                    old_name,
                    new_name,
                    old_num,
                    new_num,
                    update_leaf_filename=False,
                )
                next_job_num = new_num if new_path != normalized_old_path else job_num
                next_original_name = original_name
                if new_path != normalized_old_path and new_num:
                    next_original_name = self._target_filename_for_job(new_num, original_name)
                updated[new_path] = (next_job_num, new_dir, next_original_name, next_retry)
            self.PENDING_RENAMES = updated

    def start(self):
        """Initializes and starts all application components."""
        os.makedirs(self.config.BACKUP_DIR, exist_ok=True)
        os.makedirs(BASE_DATA_DIR, exist_ok=True)

        clear_old_logs()

        if not self.acquire_lock():
            logging.warning("Another instance is already running. Exiting.")
            sys.exit(0)

        if self.config.bad_parts_mode == "legacy":
            load_blacklist()
            load_permanently_ignored_blacklist()
        else:
            logging.info("Tracker bad-parts mode enabled; legacy PDF-highlight blacklists are not loaded.")

        self._configure_assimp_path()

        self.alert_coordinator.start()

        self.start_threads()
        self.start_observers()
        self.scan_cnc_pdfs_for_bad_parts()

        self.setup_gui()

    def stop(self):
        """Stops all threads and observers gracefully."""
        logging.info("Shutting down application...")
        self.stop_event.set()

        # Stop observers and wait for them to finish
        observers = [
            ('main_observer', self.observer),
            ('pdf_observer', self.pdf_observer),
            ('desktop_observer', self.desktop_observer)
        ]

        for name, obs in observers:
            if obs and obs.is_alive():
                try:
                    obs.stop()
                except Exception as e:
                    logging.error(f"Error stopping {name}: {e}")

        for name, obs in observers:
            if obs and obs.is_alive():
                try:
                    obs.join(timeout=5)
                except RuntimeError as e:
                    logging.error(f"Error joining {name}: {e}")
                if obs.is_alive():
                    logging.warning(f"{name} did not stop within timeout")

        # Wait for background threads
        threads = [
            ('retry_thread', self.retry_thread),
            ('backup_thread', self.backup_thread),
            ('cnc_scan_thread', self.cnc_scan_thread),
            ('stats_thread', self.stats_thread),
            ('restart_thread', self.restart_thread),
            ('pending_autorelease_thread', self.pending_autorelease_thread),
            ('pending_reminder_thread', self.pending_reminder_thread),
            ('metadata_end_of_day_thread', self.metadata_end_of_day_thread),
            ('poller_thread', self.poller_thread),
            ('observer_monitor_thread', self.observer_monitor_thread),
            ('tray_thread', self.tray_thread),
        ]

        for name, thread in threads:
            if thread and thread.is_alive():
                try:
                    thread.join(timeout=5)
                except RuntimeError as e:
                    logging.error(f"Error joining {name}: {e}")
                if thread.is_alive():
                    logging.warning(f"{name} did not stop within timeout")

        try:
            self.metadata_refresh_service.stop()
        except Exception as e:
            logging.error(f"Error stopping metadata refresh service: {e}")

        # Stop thread pool executor
        if self.executor:
            try:
                logging.info("Shutting down thread pool executor...")
                self.executor.shutdown(wait=True, cancel_futures=False)
                logging.info("Thread pool executor shut down successfully")
            except Exception as e:
                logging.error(f"Error shutting down executor: {e}")

        if self.alert_coordinator:
            try:
                self.alert_coordinator.stop()
            except Exception as e:
                logging.error(f"Error stopping alert coordinator: {e}")

        self._pending_job_timers.cancel_all()

        # Stop tray icon and GUI
        if self.icon:
            try:
                self.icon.hide()
            except Exception as e:
                logging.error(f"Error stopping tray icon: {e}")

        if hasattr(self, 'qapp') and self.qapp:
            try:
                self.qapp.quit()
            except Exception as e:
                logging.error(f"Error quitting QApplication: {e}")

        logging.info("Shutdown complete.")

    def restart(self):
        """
        Safely restarts the application.

        This method stops background threads, explicitly releases the single-instance
        lock, starts a new process of the application, and then forcefully exits
        the current process to avoid Tkinter deadlocks during shutdown.
        """
        import subprocess

        logging.info("Initiating safe application restart...")

        # Stop background loops from doing more work
        self.stop_event.set()

        # Give pending operations a moment to save
        time.sleep(2)

        # Cleanly hide the tray icon to prevent ghost icons on Windows
        if self.icon:
            try:
                self.icon.hide()
            except Exception as e:
                logging.error(f"Error hiding tray icon during restart: {e}")

        # Release the lock file so the new instance doesn't think we're still running
        self.release_lock()

        # Start a new instance
        try:
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                executable = sys.executable
                args = [executable]
            else:
                # Running as script
                executable = sys.executable
                args = [executable] + sys.argv

            # Use subprocess.Popen to start a new process
            # DETACHED_PROCESS flag ensures the new process is independent
            if os.name == 'nt':
                # Windows
                DETACHED_PROCESS = 0x00000008
                subprocess.Popen(
                    args,
                    creationflags=DETACHED_PROCESS,
                    close_fds=True,
                    start_new_session=True
                )
            else:
                # Unix-like
                subprocess.Popen(
                    args,
                    start_new_session=True,
                    close_fds=True
                )

            logging.info("New instance started. Exiting current process forcefully.")

            # Force exit completely bypassing Tkinter cleanup which deadlocks on background threads
            os._exit(0)

        except Exception as e:
            logging.error(f"Failed to spawn new process during restart: {e}")
            send_critical_alert(
                "Ready Jobs Watcher restart failed",
                "Ready Jobs Watcher could not start a replacement process and will exit. "
                "Start it manually to resume monitoring.",
            )
            os._exit(1)

    def _is_process_running(self, pid):
        """
        Check if a process with given PID is running and is a python process.

        Args:
            pid (int): The process ID to check.

        Returns:
            bool: True if running, False otherwise.
        """
        try:
            # On Windows, try to open the process and check its image name
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(1024)
                res = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
                kernel32.CloseHandle(handle)
                if res:
                    exe_name = buf.value.lower()
                    return 'python' in exe_name
            return False
        except Exception:
            # If we can't check, assume it's not running
            return False

    def acquire_lock(self):
        """
        Acquires a single-instance lock using PID-based locking.
        This is more robust than file locking and survives crashes.

        Returns:
            bool: True if lock was acquired, False if another instance exists.
        """
        lock_file = os.path.join(BASE_DATA_DIR, "ready_jobs_watcher.lock")
        current_pid = os.getpid()

        try:
            try:
                with open(lock_file, 'r') as f:
                    existing_pid = int(f.read().strip())
                if self._is_process_running(existing_pid):
                    logging.warning(f"Another instance is already running (PID: {existing_pid}). Exiting.")
                    return False
                logging.info(f"Found stale lock file from PID {existing_pid}, removing it.")
                os.remove(lock_file)
            except FileNotFoundError:
                pass
            except (ValueError, IOError) as e:
                logging.warning(f"Invalid or unreadable lock file, removing it: {e}")
                try:
                    os.remove(lock_file)
                except Exception:
                    pass

            # Write our PID to the lock file
            with open(lock_file, 'w') as f:
                f.write(str(current_pid))

            # Register cleanup handler
            atexit.register(self.release_lock)

            logging.info(f"Acquired single instance lock (PID: {current_pid}).")
            return True

        except Exception as e:
            logging.error(f"Failed to acquire lock: {e}")
            return False

    def release_lock(self):
        """Releases the single-instance lock by removing the PID file."""
        lock_file = os.path.join(BASE_DATA_DIR, "ready_jobs_watcher.lock")
        try:
            try:
                with open(lock_file, 'r') as f:
                    existing_pid = int(f.read().strip())
                if existing_pid == os.getpid():
                    os.remove(lock_file)
                    logging.info("Released single instance lock.")
                else:
                    logging.warning(f"Lock file contains different PID ({existing_pid} vs {os.getpid()}), not removing.")
            except FileNotFoundError:
                pass
            except Exception:
                # If we can't read it, just try to remove it
                try:
                    os.remove(lock_file)
                except FileNotFoundError:
                    pass
                    logging.info("Released single instance lock.")
        except Exception as e:
            logging.error(f"Error releasing lock: {e}")

    def start_threads(self):
        """Starts the background threads for retries and scheduled tasks."""
        self.deployment_gate.migrate_clear_legacy_autorelease_timers()

        self.retry_thread = threading.Thread(target=self.retry_pending, daemon=True)
        self.retry_thread.start()

        self.backup_thread = threading.Thread(target=backup_scheduler, args=(self.config, self.stop_event, self), daemon=True)
        self.backup_thread.start()

        self.cnc_scan_thread = threading.Thread(
            target=cnc_scan_scheduler,
            args=(self.config, self.stop_event, self.tracker_monitor, self.alert_coordinator, self.deployment_gate),
            daemon=True
        )
        self.cnc_scan_thread.start()

        self.stats_thread = threading.Thread(target=stats_logger_scheduler, args=(self.stop_event,), daemon=True)
        self.stats_thread.start()

        self.sync_conflict_thread = threading.Thread(
            target=sync_conflict_resolver_scheduler,
            args=(self.config, self.stop_event),
            daemon=True,
            name="SyncConflictResolverScheduler",
        )
        self.sync_conflict_thread.start()

        self.restart_thread = threading.Thread(target=daily_restart_scheduler, args=(self.config, self.stop_event, self), daemon=True)
        self.restart_thread.start()
        logging.info(f"Daily restart scheduled for {self.config.daily_restart_time}")

        self.pending_autorelease_thread = threading.Thread(
            target=pending_autorelease_scheduler,
            args=(self.deployment_gate, self.auto_release_pending_job, self.stop_event),
            daemon=True,
            name="PendingAutoReleaseScheduler",
        )
        self.pending_autorelease_thread.start()
        self.pending_reminder_thread = threading.Thread(
            target=pending_reminder_scheduler,
            args=(self.deployment_gate, self._remind_pending_job_due, self.stop_event),
            daemon=True,
            name="PendingReminderScheduler",
        )
        self.pending_reminder_thread.start()
        self.metadata_end_of_day_thread = threading.Thread(
            target=metadata_end_of_day_scheduler,
            args=(self.config, self.stop_event, self.metadata_refresh_service),
            daemon=True,
            name="MetadataEndOfDayScheduler",
        )
        self.metadata_end_of_day_thread.start()
        self.observer_monitor_thread = threading.Thread(
            target=self._observer_reconnect_loop,
            args=(self.stop_event,),
            daemon=True,
            name="ObserverReconnectScheduler",
        )
        self.observer_monitor_thread.start()

    def restore_pending_operations(self, rename_handler, pdf_handler):
        """
        Restore pending operations from the queue after a restart.

        Args:
            rename_handler (RenameHandler): Event handler for tracking folder changes.
            pdf_handler (PdfChangeHandler): Event handler for scheduling PDF conversions.
        """
        # Use the improved resume method from PendingQueue which properly handles delays and uses synchronous conversion
        self.pending_queue.resume_pending_operations(pdf_handler, rename_handler)

    def _is_root_available(self) -> bool:
        from .utils import is_root_available
        return is_root_available(self.config)

    def _is_root_available_at_startup(self, attempts: int = 5, delay_seconds: float = 1.0) -> bool:
        """Bounded retry for the availability checks that fire right after launch/restart.

        A freshly spawned process can briefly see the share as unavailable while
        the OS re-negotiates the SMB session torn down with the previous process
        (app.restart() kills and respawns within ~2 seconds). Without this, a
        routine restart logs a spurious "deferred" warning and then waits for the
        next 30s reconnect poll instead of the share settling within a second or two.
        """
        for attempt in range(attempts):
            if self._is_root_available():
                return True
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        return False

    def _observers_are_running(self) -> bool:
        return bool(
            self.observer
            and self.observer.is_alive()
            and self.pdf_observer
            and self.pdf_observer.is_alive()
        )

    def _stop_observer_if_running(self, observer, name: str):
        if not observer:
            return
        try:
            if observer.is_alive():
                observer.stop()
        except Exception as exc:
            logging.warning("Failed stopping %s: %s", name, exc)
        try:
            if observer.is_alive():
                observer.join(timeout=3)
        except Exception as exc:
            logging.warning("Failed joining %s: %s", name, exc)

    def _maybe_restart_after_root_offline(self, now_monotonic: float) -> None:
        restart_minutes = float(getattr(self.config, "root_offline_restart_minutes", 0) or 0)
        if restart_minutes <= 0 or self._root_offline_since is None:
            return

        offline_seconds = now_monotonic - self._root_offline_since
        threshold_seconds = restart_minutes * 60
        if offline_seconds < threshold_seconds:
            return

        logging.warning(
            "Ready Jobs root has stayed unavailable for %.1f minutes; restarting app to refresh SMB state.",
            offline_seconds / 60,
        )
        send_critical_alert(
            "Ready Jobs Watcher Needs Attention",
            (
                "Ready Jobs Watcher has not been able to reach the Ready Jobs network share "
                f"for {offline_seconds / 60:.1f} minutes:\n\n"
                f"{self.config.ROOT_DIR}\n\n"
                "It will restart after this alert is dismissed to try to refresh the network connection."
            ),
        )
        self._root_offline_since = now_monotonic
        self.restart()

    def _start_polling_if_needed(self, rename_handler, pdf_handler) -> bool:
        mode = str(getattr(self.config, "filesystem_monitor_mode", "hybrid") or "hybrid").lower()
        if mode not in ("hybrid", "polling"):
            return False
        if self._polling_started and self.poller_thread and self.poller_thread.is_alive():
            return True

        snapshot_file = os.path.join(BASE_DATA_DIR, "polling_snapshot.json")
        self.poller = ReadyJobsPoller(self.config, snapshot_file, rename_handler, pdf_handler)
        self.poller_thread = threading.Thread(
            target=self.poller.run,
            args=(self.stop_event,),
            daemon=True,
            name="ReadyJobsPollingReconciler",
        )
        self.poller_thread.start()
        self._polling_started = True
        logging.info(
            "Ready Jobs polling reconciler started: root=%ss files=%ss stable=%s mode=%s",
            getattr(self.config, "ready_jobs_root_poll_seconds", 10),
            getattr(self.config, "ready_jobs_file_poll_seconds", 60),
            getattr(self.config, "ready_jobs_stable_poll_count", 2),
            mode,
        )
        return True

    def _observer_reconnect_loop(self, stop_event: threading.Event):
        poll_seconds = max(5, int(getattr(self.config, "root_offline_retry_seconds", ROOT_RECONNECT_POLL_SECONDS)))
        was_available = None
        while not stop_event.is_set():
            is_available = self._is_root_available()

            if is_available:
                self._root_offline_since = None
                started = self.start_observers()
                if was_available is False and started:
                    self._run_root_catchup_scans("after reconnect")
            else:
                now_monotonic = time.monotonic()
                if self._root_offline_since is None:
                    self._root_offline_since = now_monotonic
                if was_available is not False:
                    logging.warning(
                        "Ready Jobs root is offline/unavailable: %s. Watchers paused; auto-retry is active.",
                        self.config.ROOT_DIR,
                    )
                if self._observers_are_running():
                    with self._observer_lock:
                        self._stop_observer_if_running(self.observer, "main_observer")
                        self._stop_observer_if_running(self.pdf_observer, "pdf_observer")
                        self.observer = Observer()
                        self.pdf_observer = Observer()
                self._root_unavailable_logged = True
                self._maybe_restart_after_root_offline(now_monotonic)

            was_available = is_available
            if stop_event.wait(poll_seconds):
                break

    def start_observers(self):
        """Starts filesystem monitoring. Polling is authoritative in hybrid/polling modes."""
        root_dir = self.config.ROOT_DIR
        if not self._is_root_available():
            if not self._root_unavailable_logged:
                logging.warning(
                    "Ready Jobs root unavailable at startup: %s. App will keep running and retry observers.",
                    root_dir,
                )
                self._root_unavailable_logged = True
            return False

        with self._observer_lock:
            mode = str(getattr(self.config, "filesystem_monitor_mode", "hybrid") or "hybrid").lower()
            polling_enabled = mode in ("hybrid", "polling")
            watchdog_enabled = mode in ("hybrid", "watchdog")

            if watchdog_enabled and self._observers_are_running():
                if polling_enabled:
                    self._root_unavailable_logged = False
                    return self._start_polling_if_needed(
                        self.observer.scheduled[0][0] if getattr(self.observer, "scheduled", None) else None,
                        self.pdf_observer.scheduled[0][0] if getattr(self.pdf_observer, "scheduled", None) else None,
                    )
                self._root_unavailable_logged = False
                return True

            event_handler = RenameHandler(
                self.config,
                self.job_processor,
                self,
                pending_queue=self.pending_queue,
                executor=self.executor,
                deployment_gate=self.deployment_gate,
            )
            # Pass rename_handler reference so PDF handler can check for pending folders
            pdf_event_handler = PdfChangeHandler(
                self.config,
                rename_handler=event_handler,
                pending_queue=self.pending_queue,
                executor=self.executor,
                tracker_monitor=self.tracker_monitor,
                alert_coordinator=self.alert_coordinator,
                deployment_gate=self.deployment_gate,
                metadata_refresh_service=getattr(self, "metadata_refresh_service", None),
            )
            self.rename_event_handler = event_handler
            self.pdf_event_handler = pdf_event_handler

            if polling_enabled:
                self._start_polling_if_needed(event_handler, pdf_event_handler)

            # In polling modes, restore as soon as the authoritative poller is running.
            if polling_enabled and not self._pending_operations_restored:
                self.restore_pending_operations(event_handler, pdf_event_handler)
                self._pending_operations_restored = True

            if not watchdog_enabled:
                self._stop_observer_if_running(self.observer, "main_observer")
                self._stop_observer_if_running(self.pdf_observer, "pdf_observer")
                self.observer = Observer()
                self.pdf_observer = Observer()
                logging.info("Watchdog observers skipped; filesystem_monitor_mode=%s.", mode)
                self._root_unavailable_logged = False
                return True

            # Ensure fresh observer instances before attempting restart.
            self._stop_observer_if_running(self.observer, "main_observer")
            self._stop_observer_if_running(self.pdf_observer, "pdf_observer")
            self.observer = Observer()
            self.pdf_observer = Observer()

            try:
                self.observer.schedule(event_handler, root_dir, recursive=True)
                self.observer.start()
                self.pdf_observer.schedule(pdf_event_handler, root_dir, recursive=True)
                self.pdf_observer.start()
            except Exception as exc:
                logging.warning(
                    "Failed to start watchdog observers for %s: %s.",
                    root_dir,
                    exc,
                )
                self._stop_observer_if_running(self.observer, "main_observer")
                self._stop_observer_if_running(self.pdf_observer, "pdf_observer")
                self.observer = Observer()
                self.pdf_observer = Observer()
                if polling_enabled:
                    logging.warning("Continuing with polling reconciliation; watchdog will retry later.")
                    self._root_unavailable_logged = False
                    return True
                self._root_unavailable_logged = True
                return False

            logging.info(f"Watching {root_dir} for folder changes...")
            logging.info(f"Watching {root_dir} for PDF changes...")
            self._root_unavailable_logged = False

            if not self._pending_operations_restored:
                self.restore_pending_operations(event_handler, pdf_event_handler)
                self._pending_operations_restored = True

            logging.info(
                "Desktop bad-parts log resolver watcher is disabled. "
                "Ready Jobs Watcher runs notification/indexing only; "
                "resolution is owned by process_run_folders_v2.py."
            )
            return True

    def setup_gui(self):
        """Sets up the PyQt6 QApplication, settings window, and system tray icon."""
        # QApplication must be created before any QWidget or QSystemTrayIcon
        if not QApplication.instance():
            self.qapp = QApplication(sys.argv)
        else:
            self.qapp = QApplication.instance()

        self.qapp.setQuitOnLastWindowClosed(False)

        self.settings_window = SettingsWindow(self.config, self)
        self.settings_window.set_alert_coordinator(self.alert_coordinator)
        self.icon = create_tray_icon(self.settings_window, self.config, self)
        self.icon.show()
        self._flush_pending_bad_parts_popups()
        self._flush_pending_job_prompts()
        self._flush_pending_auto_release_notices()

        # We start the initial scan slightly delayed similar to Tkinter's after
        import threading
        threading.Timer(0.1, self.initial_scan).start()

        # Dedicated startup check: convert any DAE files that have no GLB yet.
        # Runs in its own thread so it doesn't delay the initial scan or UI.
        glb_thread = threading.Thread(target=self._run_startup_glb_check, daemon=True, name="StartupGlbCheck")
        glb_thread.start()

        # Startup check: rebuild cabinet_sheet_index.json for any deployed job that is missing one.
        index_thread = threading.Thread(
            target=self._run_cabinet_index_startup_check,
            daemon=True,
            name="StartupCabinetIndexCheck",
        )
        index_thread.start()

        # Start the event loop
        sys.exit(self.qapp.exec())

    def _run_root_catchup_scans(self, reason: str):
        """Run all root-scoped catch-up work when the Ready Jobs share is available."""
        logging.info("Ready Jobs root is online. Running catch-up scans (%s).", reason)
        for label, scan_func in (
            ("initial scan", self.initial_scan),
            ("GLB startup check", self._run_startup_glb_check),
            ("cabinet index startup check", self._run_cabinet_index_startup_check),
        ):
            if self.stop_event.is_set():
                break
            try:
                scan_func()
            except Exception as exc:
                logging.error("Catch-up %s failed %s: %s", label, reason, exc, exc_info=True)

    def _run_startup_glb_check(self):
        """Convert any DAE files under the root that are still missing GLBs."""
        if not self._is_root_available_at_startup():
            logging.warning(
                "Startup GLB check deferred; Ready Jobs root is unavailable: %s",
                self.config.ROOT_DIR,
            )
            return False
        try:
            scan_root_for_missing_glbs(self.config.ROOT_DIR)
            return True
        except Exception as e:
            logging.error(f"Startup GLB check failed: {e}", exc_info=True)
            return False

    def _run_cabinet_index_startup_check(self):
        """Rebuild stale cabinet indexes for jobs that were already indexed once."""
        if not self._is_root_available_at_startup():
            logging.warning(
                "Startup cabinet index check deferred; Ready Jobs root is unavailable: %s",
                self.config.ROOT_DIR,
            )
            return False
        try:
            import os as _os
            from .cabinet_sheet_indexer import REFERENCE_INDEX_FILENAME

            root = self.config.ROOT_DIR
            for entry in _os.scandir(root):
                if not entry.is_dir():
                    continue
                index_path = _os.path.join(entry.path, ".metadata", REFERENCE_INDEX_FILENAME)
                if not _os.path.exists(index_path):
                    continue  # never indexed; watcher will catch it when PDFs appear
                # Rebuild if the index is stale (older than any PDF in the job folder).
                # Check CNC/ first — most common staleness source — then scan other dirs.
                index_mtime = _os.path.getmtime(index_path)
                needs_rebuild = False

                def _has_stale_pdf(directory):
                    try:
                        with _os.scandir(directory) as sd:
                            for child in sd:
                                if child.is_file() and child.name.lower().endswith(".pdf"):
                                    if child.stat().st_mtime > index_mtime:
                                        return True
                                elif child.is_dir() and not child.name.startswith("."):
                                    if _has_stale_pdf(child.path):
                                        return True
                    except OSError:
                        pass
                    return False

                cnc_dir = _os.path.join(entry.path, self.config.CNC_SUBDIR)
                if _os.path.isdir(cnc_dir):
                    needs_rebuild = _has_stale_pdf(cnc_dir)
                if not needs_rebuild:
                    needs_rebuild = _has_stale_pdf(entry.path)
                if needs_rebuild:
                    logging.info("Startup: rebuilding stale cabinet index for %s", entry.name)
                    try:
                        build_reference_index_for_job(entry.path)
                    except Exception as exc:
                        logging.error("Startup cabinet index rebuild failed for %s: %s", entry.name, exc, exc_info=True)
            return True
        except Exception as e:
            logging.error("Startup cabinet index check failed: %s", e, exc_info=True)
            return False

    def perform_backup(self):
        """Execute an immediate manual backup based on configured paths."""
        from .scheduler import perform_backup
        perform_backup(self.config, self)

    def scan_cnc_pdfs_for_bad_parts(self):
        """Trigger an immediate scan of all existing CNC PDFs."""
        from .scheduler import scan_cnc_pdfs_for_bad_parts
        try:
            scan_cnc_pdfs_for_bad_parts(
                self.config,
                self.tracker_monitor,
                self.alert_coordinator,
                self.deployment_gate,
            )
        except Exception as exc:
            logging.error("Immediate CNC scan failed (will retry on schedule): %s", exc, exc_info=True)

    def _bootstrap_new_job_folders(self):
        """
        Stamp a hidden gate on any folder that doesn't have one yet — this ensures
        new/unknown folders are invisible to tablets until explicitly deployed.

        Folders that turn out to be real job folders are routed through
        on_new_job_folder_detected, the same path live watcher events use, so they
        get mode detection, an auto-release timer, and the pending-job alert popup
        instead of being silently gated.
        """
        from .deployment_gate import ensure_hidden_gates_for_all_folders
        newly_gated = ensure_hidden_gates_for_all_folders(self.config.ROOT_DIR)
        for folder_name in newly_gated:
            folder_path = os.path.join(self.config.ROOT_DIR, folder_name)
            if JobProcessor.is_job_folder(folder_path):
                self.on_new_job_folder_detected(folder_path)

    def initial_scan(self):
        """Performs an initial scan of the root directory to handle any backlog."""
        if self.PAUSE_PROCESSING:
            logging.info("Initial scan skipped (GUI open).")
            return False
        if not self._is_root_available_at_startup():
            logging.warning(
                "Initial scan deferred; Ready Jobs root is unavailable: %s. Auto-retry is active.",
                self.config.ROOT_DIR,
            )
            return False
        logging.info("Starting initial scan...")
        self._bootstrap_new_job_folders()
        try:
            eligible_paths = []
            with os.scandir(self.config.ROOT_DIR) as it:
                for entry in it:
                    if entry.is_dir():
                        full_path = entry.path
                        if is_hidden(full_path):
                            logging.info(f"Skipping hidden item: {full_path}")
                            continue
                        if not self.deployment_gate.should_process_job_folder(full_path):
                            logging.info(f"Skipping pending job during initial parse: {full_path}")
                            continue
                        self.job_processor.process_job_folder(full_path)
                        eligible_paths.append(full_path)

            def _build_indexes_for_job(job_path):
                try:
                    build_reference_index_for_job(job_path)
                except Exception as e:
                    logging.error(f"Reference index build failed for {job_path}: {e}", exc_info=True)
                try:
                    build_hardwoods_cutlist_index_for_job(job_path, deployment_gate=self.deployment_gate)
                except Exception as e:
                    logging.error(f"Hardwoods cutlist index build failed for {job_path}: {e}", exc_info=True)
                try:
                    convert_3d_models_for_job(job_path)
                except Exception as e:
                    logging.error(f"3D model conversion failed for {job_path}: {e}", exc_info=True)

            from concurrent.futures import as_completed
            futures = {self.executor.submit(_build_indexes_for_job, p): p for p in eligible_paths}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logging.error(f"Initial scan index build failed for {futures[future]}: {e}", exc_info=True)
        except OSError as e:
            logging.error(f"Error during initial scan: {e}")
            return False
        self._backfill_modes_for_existing_jobs()
        logging.info("Initial scan complete.")
        return True

    def retry_pending(self):
        """Periodically retries failed file rename operations using a background thread."""
        while not self.stop_event.is_set():
            if self.PAUSE_PROCESSING:
                self.stop_event.wait(5)
                continue

            current_time = time.time()
            to_remove = []

            # Create a snapshot with lock protection
            with self.pending_renames_lock:
                pending_snapshot = dict(self.PENDING_RENAMES)

            for old_path, (job_num, dir_path, original_name, next_retry) in pending_snapshot.items():
                if current_time >= next_retry:
                    # Retry the rename operation
                    new_name = self._target_filename_for_job(job_num, original_name)
                    new_path = os.path.join(dir_path, new_name)
                    try:
                        # Check if source file exists
                        if not os.path.exists(old_path):
                            logging.warning(f"Retry skipped: {old_path} no longer exists.")
                            to_remove.append(old_path)
                            continue

                        # Check if destination already exists (might have been renamed manually)
                        if os.path.exists(new_path):
                            logging.info(f"Retry skipped: destination already exists: {new_path}")
                            to_remove.append(old_path)
                            continue

                        os.rename(old_path, new_path)
                        logging.info(f"Retry successful: {old_path} -> {new_path}")
                        to_remove.append(old_path)
                    except PermissionError:
                        # Still locked, reschedule
                        next_retry_time = time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60)
                        with self.pending_renames_lock:
                            self.PENDING_RENAMES[old_path] = (job_num, dir_path, original_name, next_retry_time)
                        logging.warning(f"Retry failed (still locked): {old_path}. Will retry later.")
                    except FileNotFoundError:
                        logging.warning(f"Retry failed: file disappeared: {old_path}")
                        to_remove.append(old_path)
                    except OSError as e:
                        if is_retryable_os_error(e):
                            next_retry_time = time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60)
                            with self.pending_renames_lock:
                                self.PENDING_RENAMES[old_path] = (job_num, dir_path, original_name, next_retry_time)
                            logging.warning(f"Retry failed (transient OS error): {old_path} ({e}). Will retry later.")
                        else:
                            logging.error(f"Retry failed for {old_path}: {e}")
                            to_remove.append(old_path)
                    except Exception as e:
                        logging.error(f"Retry failed for {old_path}: {e}")
                        to_remove.append(old_path)

            # Remove successfully renamed files from pending (with lock protection)
            with self.pending_renames_lock:
                for path in to_remove:
                    self.PENDING_RENAMES.pop(path, None)

            self.stop_event.wait(60)

# --- Entry Point ---
if __name__ == "__main__":
    setup_logging()
    from . import __version__
    logging.info(f"Ready Jobs Watcher v{__version__} starting...")
    app = Application()
    try:
        app.start()
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        app.stop()
