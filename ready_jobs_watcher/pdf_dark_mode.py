"""
PDF Dark Mode Conversion Module.

Provides integration with an external PDF Dark Mode Converter CLI tool.
Handles both standard vector-based color inversion and direct rasterization
for complex documents like cover sheets.
"""
import os
import subprocess
import logging
from typing import Optional
from .utils import ALLOWED_SHEETS_PATTERN

pdf_darkmode_logger = logging.getLogger('pdf_darkmode')

# PDF Dark Mode Converter configuration
PDF_DARK_MODE_CLI_PATH = r"C:\Scripts\PDF DARK MODE\pdf-dark-mode-converter\cli.py"
PDF_DARK_MODE_READY_JOBS_PATH = r"Y:\Ready Jobs"

# Paths to exclude from dark mode conversion
EXCLUDED_PATHS = [r"Y:\Ready Jobs\*\CNC"]

def is_dark_mode_available() -> bool:
    """
    Check if the external PDF dark mode converter CLI is available.

    Returns:
        bool: True if the CLI tool exists at the configured path, False otherwise.
    """
    if not os.path.exists(PDF_DARK_MODE_CLI_PATH):
        pdf_darkmode_logger.warning(f"PDF Dark Mode CLI not found at {PDF_DARK_MODE_CLI_PATH}")
        return False
    return True

def run_dark_mode_conversion(dry_run: bool = False, theme: str = "classic", specific_file: Optional[str] = None, force: bool = False, invert_images: bool = False) -> bool:
    """
    Run the PDF dark mode converter on the Ready Jobs folder or a specific file.

    Args:
        dry_run (bool): If True, only preview what would be converted without actually converting.
        theme (str): The dark mode theme to use (classic, claude, chatgpt, sepia, midnight, forest).
        specific_file (Optional[str]): If provided, only convert this specific PDF file instead of scanning the entire folder.
        force (bool): If True, reconvert all files regardless of modification date.
        invert_images (bool): Whether to invert images during conversion.

    Returns:
        bool: True if the conversion was successful, False otherwise.
    """
    if not is_dark_mode_available():
        pdf_darkmode_logger.error("PDF Dark Mode converter not available, skipping conversion")
        return False

    # Skip conversion if the specific file is already in a DARK MODE folder
    if specific_file:
        normalized_path = os.path.abspath(specific_file).replace('/', '\\')
        path_parts = [part.upper() for part in normalized_path.split('\\')]
        if 'DARK MODE' in path_parts:
            pdf_darkmode_logger.debug(f"Skipping dark mode conversion for PDF in DARK MODE folder: {specific_file}")
            return True

    try:
        if specific_file:
            pdf_darkmode_logger.info(f"Starting PDF dark mode conversion for specific file: {specific_file}")
        else:
            pdf_darkmode_logger.info(f"Starting PDF dark mode conversion for all files (dry_run={dry_run}, theme={theme}, force={force})")

        # Build the command for standard vector-based conversion
        cmd = [
            "python",
            PDF_DARK_MODE_CLI_PATH,
            "--theme", theme,
            "--log-level", "WARNING"
        ]

        if invert_images:
            cmd.append("--invert-images")

        # If converting a specific file, pass it directly; otherwise use quick-scan for the folder
        if specific_file:
            # Convert single file to DARK MODE subfolder
            input_path = specific_file
            input_dir = os.path.dirname(input_path)
            input_filename = os.path.basename(input_path)

            # Find the base directory (skip any DARK MODE ancestors)
            base_dir = input_dir
            while os.path.basename(base_dir).upper() == "DARK MODE":
                base_dir = os.path.dirname(base_dir)

            # Create DARK MODE subfolder in the base dir
            dark_mode_dir = os.path.join(base_dir, "DARK MODE")
            os.makedirs(dark_mode_dir, exist_ok=True)

            # Output to DARK MODE subfolder with same filename
            output_path = os.path.join(dark_mode_dir, input_filename)

            if not force and os.path.exists(output_path):
                input_mtime = os.path.getmtime(input_path)
                output_mtime = os.path.getmtime(output_path)
                if output_mtime >= input_mtime:
                    pdf_darkmode_logger.debug(f"Skipping unmodified file: {input_path}")
                    return True

            cmd.extend(["--output", output_path])

            # Input file comes last as positional argument
            cmd.append(input_path)
        else:
            # Full folder scan (CNC folders are excluded by the watcher, not the CLI)
            cmd.append("--quick-scan")

        if dry_run:
            cmd.append("--dry-run")

        if force:
            cmd.append("--force")

        pdf_darkmode_logger.debug(f"Running command: {' '.join(cmd)}")

        # Run the conversion completely silently (no prompts, no console window)
        startupinfo = None
        creationflags = 0

        # On Windows, prevent console window from appearing
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            cmd,
            cwd=os.path.dirname(PDF_DARK_MODE_CLI_PATH),
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
            stdin=subprocess.DEVNULL,  # No input prompts
            startupinfo=startupinfo,
            creationflags=creationflags
        )

        if result.returncode == 0:
            pdf_darkmode_logger.info("PDF dark mode conversion completed successfully")
            if result.stdout:
                pdf_darkmode_logger.debug(f"Conversion output: {result.stdout}")
            return True
        else:
            pdf_darkmode_logger.error(f"PDF dark mode conversion failed with return code {result.returncode}")
            error_details = result.stderr if result.stderr else result.stdout
            if error_details:
                pdf_darkmode_logger.error(f"Error output: {error_details.strip()}")
            return False

    except subprocess.TimeoutExpired:
        pdf_darkmode_logger.error("PDF dark mode conversion timed out after 10 minutes")
        return False
    except (OSError, RuntimeError) as e:
        pdf_darkmode_logger.error(f"Failed to run PDF dark mode conversion: {e}", exc_info=True)
        return False

def should_invert_images(pdf_path: str) -> bool:
    """
    Determine if a PDF file should have its images inverted during dark mode conversion.

    Args:
        pdf_path (str): Full path to the PDF file.

    Returns:
        bool: True if images should be inverted, False otherwise.
    """
    # Image inversion has been requested to be completely disabled for Assembly Sheets and Plans
    return False

def process_directory(directory_path: str, force: bool = False):
    """
    Process all PDFs in a directory (recursive) and convert them to dark mode.

    Args:
        directory_path (str): Root directory to scan.
        force (bool): If True, reconvert files even if they've been modified recently.
    """
    pdf_darkmode_logger.info(f"Scanning directory for dark mode conversion: {directory_path}")
    for root, dirs, files in os.walk(directory_path):
        # Skip DARK MODE folders to avoid loops
        if "DARK MODE" in [d.upper() for d in root.split(os.sep)]:
            continue

        for file in files:
            if file.lower().endswith('.pdf'):
                # Basic normalization of path style for logging/matching
                pdf_path = os.path.join(root, file)

                # Check if the filename belongs to the allowed list (case-insensitive)
                is_allowed = ALLOWED_SHEETS_PATTERN.search(os.path.basename(pdf_path))
                
                if not is_allowed:
                    continue

                # Use the same CNC check as watchers.py
                normalized_path = pdf_path.replace('/', '\\')
                if '\\CNC\\' in normalized_path or normalized_path.endswith('\\CNC'):
                    continue

                # Trigger conversion
                invert = should_invert_images(pdf_path)
                run_dark_mode_conversion(specific_file=pdf_path, force=force, invert_images=invert)

def run_dark_mode_conversion_async(dry_run: bool = False, theme: str = "classic", specific_file: Optional[str] = None, force: bool = False, invert_images: bool = False) -> None:
    """
    Run the PDF dark mode converter asynchronously in a separate thread.

    This is useful for running the conversion without blocking the main application flow.

    Args:
        dry_run (bool): If True, only preview what would be converted without actually converting.
        theme (str): The dark mode theme to use.
        specific_file (Optional[str]): If provided, only convert this specific PDF file.
        force (bool): If True, reconvert all files regardless of modification date.
        invert_images (bool): Whether to invert images during conversion.
    """
    import threading

    def _run():
        try:
            run_dark_mode_conversion(dry_run=dry_run, theme=theme, specific_file=specific_file, force=force, invert_images=invert_images)
        except Exception as e:
            pdf_darkmode_logger.error(f"Error in async dark mode conversion: {e}", exc_info=True)

    thread = threading.Thread(target=_run, daemon=True, name="PDFDarkModeConversion")
    thread.start()

    if specific_file:
        pdf_darkmode_logger.info(f"Started PDF dark mode conversion for {specific_file} in background thread")
    else:
        force_msg = " (force reconvert all)" if force else ""
        pdf_darkmode_logger.info(f"Started PDF dark mode conversion for all files{force_msg} in background thread")
