import json
import logging
import os
import time
import threading
from typing import Dict, Optional

pending_queue_logger = logging.getLogger('pending_queue')

class PendingQueue:
    """
    Persistent queue for tracking pending operations (PDF conversions, folder processing).
    Saves pending operations to disk so they can be resumed after program restarts.
    """

    def __init__(self, queue_file: str, executor=None):
        """
        Initialize the pending queue.

        Args:
            queue_file: Path to the JSON file where pending operations are stored
            executor: ThreadPoolExecutor for background tasks (optional)
        """
        self.queue_file = queue_file
        self.lock = threading.Lock()
        self.executor = executor

        # Pending PDF conversions: {file_path: {"scheduled_time": timestamp, "invert_images": bool}}
        self.pending_pdfs: Dict[str, Dict] = {}

        # Pending folder processing: {folder_path: {"scheduled_time": timestamp}}
        self.pending_folders: Dict[str, Dict] = {}

        self.load()

    def load(self):
        """Load pending operations from disk."""
        if not os.path.exists(self.queue_file):
            pending_queue_logger.info(f"No existing pending queue found at {self.queue_file}")
            return

        try:
            with open(self.queue_file, 'r') as f:
                data = json.load(f)

            self.pending_pdfs = data.get('pending_pdfs', {})
            self.pending_folders = data.get('pending_folders', {})

            # Create backup of successfully loaded queue
            backup_file = self.queue_file + '.backup'
            try:
                with open(backup_file, 'w') as f:
                    json.dump(data, f, indent=2)
                pending_queue_logger.debug(f"Created backup of pending queue at {backup_file}")
            except Exception as backup_error:
                pending_queue_logger.warning(f"Failed to create backup: {backup_error}")

            # Clean up expired entries (more than 24 hours old)
            current_time = time.time()
            expired_pdfs = [
                path for path, info in self.pending_pdfs.items()
                if current_time - info['scheduled_time'] > 86400  # 24 hours
            ]
            expired_folders = [
                path for path, info in self.pending_folders.items()
                if current_time - info['scheduled_time'] > 86400
            ]

            for path in expired_pdfs:
                del self.pending_pdfs[path]
            for path in expired_folders:
                del self.pending_folders[path]

            if expired_pdfs or expired_folders:
                pending_queue_logger.info(f"Cleaned up {len(expired_pdfs)} expired PDFs and {len(expired_folders)} expired folders")
                with self.lock:
                    self.save()

            pending_queue_logger.info(f"Loaded {len(self.pending_pdfs)} pending PDF conversions and {len(self.pending_folders)} pending folders")

        except Exception as e:
            pending_queue_logger.error(f"Failed to load pending queue: {e}")
            # Try to restore from backup
            backup_file = self.queue_file + '.backup'
            if os.path.exists(backup_file):
                try:
                    pending_queue_logger.info(f"Attempting to restore from backup: {backup_file}")
                    with open(backup_file, 'r') as f:
                        data = json.load(f)
                    self.pending_pdfs = data.get('pending_pdfs', {})
                    self.pending_folders = data.get('pending_folders', {})
                    pending_queue_logger.info(f"Successfully restored from backup")
                    return
                except Exception as backup_error:
                    pending_queue_logger.error(f"Failed to restore from backup: {backup_error}")
            # Start with empty queues if load and backup both fail
            self.pending_pdfs = {}
            self.pending_folders = {}

    def save(self):
        """
        Save pending operations to disk atomically.
        NOTE: Caller must hold self.lock before calling this method.
        Uses a safer atomic write pattern for Windows.
        """
        temp_file = self.queue_file + '.tmp'
        backup_file = self.queue_file + '.save_backup'

        try:
            data = {
                'pending_pdfs': self.pending_pdfs,
                'pending_folders': self.pending_folders
            }

            # Step 1: Write to temp file first
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)

            # Step 2: Verify temp file was written correctly
            try:
                with open(temp_file, 'r') as f:
                    json.load(f)  # Verify it's valid JSON
            except json.JSONDecodeError:
                pending_queue_logger.error("Temp file contains invalid JSON, aborting save")
                os.remove(temp_file)
                return

            # Step 3: Create backup of existing file (if it exists)
            if os.path.exists(self.queue_file):
                try:
                    if os.path.exists(backup_file):
                        os.remove(backup_file)
                    os.rename(self.queue_file, backup_file)
                except Exception as e:
                    pending_queue_logger.warning(f"Failed to create save backup: {e}")

            # Step 4: Rename temp file to target (atomic on same filesystem)
            try:
                os.rename(temp_file, self.queue_file)
            except Exception as e:
                # If rename fails, try to restore from backup
                pending_queue_logger.error(f"Failed to rename temp file: {e}")
                if os.path.exists(backup_file) and not os.path.exists(self.queue_file):
                    try:
                        os.rename(backup_file, self.queue_file)
                        pending_queue_logger.info("Restored queue file from backup after failed save")
                    except Exception:
                        pass
                raise

            # Step 5: Clean up backup file on success
            if os.path.exists(backup_file):
                try:
                    os.remove(backup_file)
                except Exception:
                    pass  # Not critical if cleanup fails

        except Exception as e:
            pending_queue_logger.error(f"Failed to save pending queue: {e}", exc_info=True)
            # Clean up temp file on failure
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

    def add_pending_pdf(self, file_path: str, scheduled_time: float, invert_images: bool = False):
        """
        Add a PDF to the pending conversion queue.

        Args:
            file_path: Full path to the PDF file
            scheduled_time: Unix timestamp when conversion should occur
            invert_images: Whether to invert images during conversion
        """
        with self.lock:
            self.pending_pdfs[file_path] = {
                'scheduled_time': scheduled_time,
                'invert_images': invert_images
            }
            self.save()
            pending_queue_logger.debug(f"Added pending PDF: {file_path} (scheduled: {scheduled_time})")

    def add_pending_folder(self, folder_path: str, scheduled_time: float):
        """
        Add a folder to the pending processing queue.

        Args:
            folder_path: Full path to the folder
            scheduled_time: Unix timestamp when processing should occur
        """
        with self.lock:
            self.pending_folders[folder_path] = {
                'scheduled_time': scheduled_time
            }
            self.save()
            pending_queue_logger.debug(f"Added pending folder: {folder_path} (scheduled: {scheduled_time})")

    def remove_pending_pdf(self, file_path: str):
        """Remove a PDF from the pending queue."""
        with self.lock:
            if file_path in self.pending_pdfs:
                del self.pending_pdfs[file_path]
                self.save()
                pending_queue_logger.debug(f"Removed pending PDF: {file_path}")

    def remove_pending_folder(self, folder_path: str):
        """Remove a folder from the pending queue."""
        with self.lock:
            if folder_path in self.pending_folders:
                del self.pending_folders[folder_path]
                self.save()
                pending_queue_logger.debug(f"Removed pending folder: {folder_path}")

    def get_pending_pdf(self, file_path: str) -> Optional[Dict]:
        """Get pending PDF info if it exists."""
        with self.lock:
            return self.pending_pdfs.get(file_path)

    def get_pending_folder(self, folder_path: str) -> Optional[Dict]:
        """Get pending folder info if it exists."""
        with self.lock:
            return self.pending_folders.get(folder_path)

    def get_all_pending_pdfs(self) -> Dict[str, Dict]:
        """Get all pending PDFs."""
        with self.lock:
            return dict(self.pending_pdfs)

    def get_all_pending_folders(self) -> Dict[str, Dict]:
        """Get all pending folders."""
        with self.lock:
            return dict(self.pending_folders)

    def is_pdf_pending(self, file_path: str) -> bool:
        """Check if a PDF is in the pending queue."""
        with self.lock:
            return file_path in self.pending_pdfs

    def is_folder_pending(self, folder_path: str) -> bool:
        """Check if a folder is in the pending queue."""
        with self.lock:
            return folder_path in self.pending_folders

    def resume_pending_operations(self, pdf_handler, rename_handler):
        """
        Resume pending operations after program restart.
        Schedules conversions for pending PDFs and processing for pending folders.

        Args:
            pdf_handler: PdfChangeHandler instance to schedule PDF conversions
            rename_handler: RenameHandler instance to schedule folder processing
        """
        current_time = time.time()

        # Resume pending PDF conversions
        pdfs_to_resume = self.get_all_pending_pdfs()
        for pdf_path, info in pdfs_to_resume.items():
            scheduled_time = info['scheduled_time']
            invert_images = info.get('invert_images', False)

            # Check if file still exists
            if not os.path.exists(pdf_path):
                pending_queue_logger.info(f"Skipping pending PDF (file no longer exists): {pdf_path}")
                self.remove_pending_pdf(pdf_path)
                continue

            # Calculate remaining delay
            time_remaining = scheduled_time - current_time
            if time_remaining < 0:
                # Already past scheduled time, convert immediately
                time_remaining = 0

            pending_queue_logger.info(f"Resuming PDF conversion: {pdf_path} (delay: {time_remaining}s)")

            # Update cooldown to prevent duplicate conversions (thread-safe)
            with pdf_handler._cooldown_lock:
                pdf_handler._conversion_cooldown[pdf_path] = current_time

            # Schedule the conversion with remaining delay
            def _delayed_convert(path=pdf_path, invert=invert_images, delay=time_remaining):
                try:
                    time.sleep(delay)
                    from .pdf_dark_mode import run_dark_mode_conversion
                    run_dark_mode_conversion(specific_file=path, invert_images=invert)
                    self.remove_pending_pdf(path)
                except Exception as e:
                    pending_queue_logger.error(f"Error in resumed PDF conversion for {path}: {e}", exc_info=True)

            # Use executor if available, fallback to thread
            if self.executor:
                self.executor.submit(_delayed_convert)
            else:
                thread = threading.Thread(target=_delayed_convert, daemon=True, name=f"ResumedPDF-{os.path.basename(pdf_path)}")
                thread.start()

        # Resume pending folder processing
        folders_to_resume = self.get_all_pending_folders()
        for folder_path, info in folders_to_resume.items():
            scheduled_time = info['scheduled_time']

            # Check if folder still exists
            if not os.path.exists(folder_path):
                pending_queue_logger.info(f"Skipping pending folder (no longer exists): {folder_path}")
                self.remove_pending_folder(folder_path)
                continue

            # Calculate remaining delay
            time_remaining = scheduled_time - current_time
            if time_remaining < 0:
                # Already past scheduled time, process immediately
                time_remaining = 0

            pending_queue_logger.info(f"Resuming folder processing: {folder_path} (delay: {time_remaining}s)")

            # Add to rename_handler's pending folders tracking (thread-safe)
            with rename_handler._pending_folders_lock:
                rename_handler._pending_folders[folder_path] = scheduled_time

            # Schedule the processing with remaining delay
            def _delayed_process(path=folder_path, delay=time_remaining, job_processor=rename_handler.job_processor, handler=rename_handler):
                try:
                    time.sleep(delay)
                    pending_queue_logger.info(f"Processing resumed folder: {path}")
                    # Thread-safe access to pending folders
                    with handler._pending_folders_lock:
                        if path in handler._pending_folders:
                            del handler._pending_folders[path]
                    job_processor.process_job_folder(path)
                    self.remove_pending_folder(path)
                except Exception as e:
                    pending_queue_logger.error(f"Error in resumed folder processing for {path}: {e}", exc_info=True)

            # Use executor if available, fallback to thread
            if self.executor:
                self.executor.submit(_delayed_process)
            else:
                thread = threading.Thread(target=_delayed_process, daemon=True, name=f"ResumedFolder-{os.path.basename(folder_path)}")
                thread.start()

        if pdfs_to_resume or folders_to_resume:
            pending_queue_logger.info(f"Resumed {len(pdfs_to_resume)} PDF conversions and {len(folders_to_resume)} folder operations")
