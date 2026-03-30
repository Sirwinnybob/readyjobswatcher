# Ready Jobs Watcher

Ready Jobs Watcher is a robust, modular Python application designed to automate and streamline workflow management for manufacturing or similar job-based environments. It actively monitors a designated file system for new job folders, automatically standardizing file names and checking for quality control issues.

## Key Features

*   **Automated File Renaming:** Watches a specified root directory (e.g., `Y:\Ready Jobs`) for new job folders. It automatically extracts job numbers from folder names and prefixes all files within the folder (and optionally a `CNC` subdirectory) with the job number, ensuring consistent and standardized file naming conventions.
*   **Automated Quality Control (Bad Part Detection):** Scans PDF files, specifically within `CNC` subdirectories, looking for a designated "BAD PART(S)" bounding box. Utilizing `PyMuPDF` and `Pillow`, it detects non-grayscale markings within this box, indicating a quality issue flagged by a user.
*   **Task Management Integration (Planka):** When a "bad part" is detected, the application automatically interfaces with a Planka Kanban board via the `plankapy` API. It creates a new card on a specified board and list, complete with a pre-defined checklist for rework and quality inspection, and tags it with an "AUTO ADDED" label.
*   **Intelligent Blacklisting:** Employs a two-tier blacklisting system for detected bad parts to prevent redundant notifications and processing:
    *   **Temporary Blacklist:** Files actively flagged are logged and temporarily ignored during subsequent automated scans.
    *   **Permanent Ignore:** Users can mark issues as resolved by modifying a centralized log file on their desktop (appending 'y' to a 'COMPLETE:' line). The application detects this change and moves the file to a permanent ignore list.
*   **Scheduled Automated Backups:** Includes a robust scheduling system to automatically backup configured directories (e.g., `Ready Jobs` and `Upcoming Jobs`) to a designated backup location at user-defined times. It also features automatic cleanup of backups older than 7 days.
*   **System Tray Integration & GUI Settings:** Runs unobtrusively in the system background with a system tray icon (via `pystray`). The tray icon provides quick access to a modern GUI settings panel (built with `tkinter` and styled with `sv_ttk`) where users can configure backup times, trigger manual backups, or initiate manual CNC scans.
*   **Comprehensive Logging:** Implements detailed, component-specific logging (main operations, backups, CNC scans, bad part detection, and Planka API interactions) for easy troubleshooting and auditing, including automatic rotation/clearing of logs older than 7 days.

## Technical Details

*   **Language:** Python 3
*   **Core Libraries:**
    *   `watchdog`: For efficient, real-time file system monitoring.
    *   `PyMuPDF` (`fitz`): For fast PDF parsing and rendering.
    *   `Pillow` (`PIL`): For image processing and color detection within PDFs.
    *   `plankapy`: For interacting with the Planka Kanban board API (includes custom overrides for compatibility with newer Planka server versions).
    *   `tkinter` & `sv_ttk`: For the graphical user interface.
    *   `pystray`: For system tray integration.

## Architecture

The application is structured around several key components:

*   `Config`: Manages application settings, loading and saving configurations (like backup schedules) from a JSON file.
*   `JobProcessor`: Handles the core logic of extracting job numbers and renaming files.
*   **Watchdog Event Handlers:**
    *   `RenameHandler`: Triggers file renaming when new folders are created or moved.
    *   `PdfChangeHandler`: Triggers bad part detection when PDFs are modified.
    *   `LogFileHandler`: Monitors the desktop bad parts log file for user completions.
*   **Schedulers:** Dedicated threads manage the automated backup (`backup_scheduler`) and daily CNC PDF scans (`cnc_scan_scheduler`).
*   **Planka Integration:** Custom classes (`CompatiblePlanka`, `CompatibleProject`, etc.) ensure reliable communication with the Planka API, handling card creation, checklist generation, and label assignment.

## Usage

This application is designed to run continuously in the background on a Windows system.

1.  **Configuration:** The primary configuration is handled via the `config.json` file (generated on first run) and the GUI settings panel accessible from the system tray. Default directories are hardcoded for specific internal use cases but can be modified in the `Config` class.
2.  **Running:** Execute the `ready_jobs_watcher.py` script. The application will initialize, perform an initial scan, and then settle into the background, accessible via the system tray icon.
3.  **Bad Parts Workflow:**
    *   A user marks a PDF with non-grayscale ink in the designated "BAD PART" area.
    *   The application detects this, logs it to `Desktop\Bad Parts Log.txt`, and creates a Planka card.
    *   The user resolves the issue, opens `Bad Parts Log.txt`, finds the relevant entry, and changes `COMPLETE: ` to `COMPLETE: y`.
    *   The application detects this log modification and permanently ignores that specific page in future scans.
