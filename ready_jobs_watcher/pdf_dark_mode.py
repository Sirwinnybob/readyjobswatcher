import os
import subprocess
import logging
from typing import Optional

pdf_darkmode_logger = logging.getLogger('pdf_darkmode')

# PDF Dark Mode Converter configuration
PDF_DARK_MODE_CLI_PATH = r"C:\Scripts\PDF DARK MODE\pdf-dark-mode-converter\cli.py"
PDF_DARK_MODE_READY_JOBS_PATH = r"Y:\Ready Jobs"

# Paths to exclude from dark mode conversion
EXCLUDED_PATHS = [r"Y:\Ready Jobs\*\CNC"]


def should_use_direct_inversion(pdf_filename: str) -> bool:
    """
    Check if a PDF should use direct rasterization and inversion.

    Cover sheets and similar documents with full-page images don't work well
    with the vector-based color transformation. For these, we render each page
    as an image, invert it, and create a new PDF.

    Args:
        pdf_filename: The full path or just the filename of the PDF

    Returns:
        True if the PDF should use direct inversion (COVER SHEET)
    """
    filename_upper = os.path.basename(pdf_filename).upper()
    return "COVER SHEET" in filename_upper


def run_direct_inversion(input_path: str, output_path: str, dpi: int = 150) -> bool:
    """
    Convert a PDF to dark mode using direct rasterization and inversion.

    This renders each page as an image, inverts the colors, and creates a new PDF.
    Best for cover sheets and documents with full-page images.

    Args:
        input_path: Path to the input PDF file
        output_path: Path to save the output PDF file
        dpi: Resolution for rendering (higher = better quality but larger file)

    Returns:
        True if conversion was successful, False otherwise
    """
    try:
        import fitz  # PyMuPDF
        from PIL import Image, ImageOps
        import io

        pdf_darkmode_logger.info(f"Starting direct inversion for: {os.path.basename(input_path)}")

        # Open the source PDF
        src_doc = fitz.open(input_path)

        # Create a new PDF for output
        out_doc = fitz.open()

        for page_num in range(len(src_doc)):
            page = src_doc[page_num]

            # Render page to image at specified DPI
            mat = fitz.Matrix(dpi / 72, dpi / 72)  # 72 is default PDF DPI
            pix = page.get_pixmap(matrix=mat)

            # Convert to PIL Image
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # Invert the image colors
            if img.mode == 'RGBA':
                # Separate alpha channel, invert RGB, then recombine
                r, g, b, a = img.split()
                rgb_img = Image.merge('RGB', (r, g, b))
                inverted_rgb = ImageOps.invert(rgb_img)
                inverted_img = Image.merge('RGBA', (*inverted_rgb.split(), a))
            else:
                inverted_img = ImageOps.invert(img.convert('RGB'))

            # Convert inverted image back to bytes
            img_bytes = io.BytesIO()
            inverted_img.save(img_bytes, format='PNG')
            img_bytes.seek(0)

            # Create a new page with same dimensions
            page_rect = page.rect
            new_page = out_doc.new_page(width=page_rect.width, height=page_rect.height)

            # Insert the inverted image
            new_page.insert_image(page_rect, stream=img_bytes.read())

            pdf_darkmode_logger.debug(f"Inverted page {page_num + 1}/{len(src_doc)}")

            # Clean up pixmap memory
            del pix

        # Save the output PDF
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        out_doc.save(output_path)

        # Clean up
        src_doc.close()
        out_doc.close()

        pdf_darkmode_logger.info(f"Direct inversion complete: {os.path.basename(output_path)}")
        return True

    except ImportError as e:
        pdf_darkmode_logger.error(f"PyMuPDF (fitz) not available for direct inversion: {e}")
        return False
    except (OSError, ValueError, RuntimeError) as e:
        pdf_darkmode_logger.error(f"Direct inversion failed: {e}", exc_info=True)
        return False


def should_invert_images(pdf_filename: str) -> bool:
    """
    Check if a PDF should have images inverted based on its filename.

    Args:
        pdf_filename: The full path or just the filename of the PDF

    Returns:
        True if the PDF should have images inverted (Island Wings or COVER SHEET)
    """
    filename_upper = os.path.basename(pdf_filename).upper()
    return "ISLAND WINGS" in filename_upper or "COVER SHEET" in filename_upper

def is_dark_mode_available() -> bool:
    """Check if the PDF dark mode converter CLI is available."""
    if not os.path.exists(PDF_DARK_MODE_CLI_PATH):
        pdf_darkmode_logger.warning(f"PDF Dark Mode CLI not found at {PDF_DARK_MODE_CLI_PATH}")
        return False
    return True

def run_dark_mode_conversion(dry_run: bool = False, theme: str = "classic", specific_file: Optional[str] = None, force: bool = False, invert_images: bool = False) -> bool:
    """
    Run the PDF dark mode converter on the Ready Jobs folder or a specific file.

    Args:
        dry_run: If True, only preview what would be converted without actually converting
        theme: The dark mode theme to use (classic, claude, chatgpt, sepia, midnight, forest)
        specific_file: If provided, only convert this specific PDF file instead of scanning the entire folder
        force: If True, reconvert all files regardless of modification date
        invert_images: If True, invert images (for PDFs with white background images)

    Returns:
        True if the conversion was successful, False otherwise
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

        # For specific files, check if we should use direct inversion (for cover sheets)
        if specific_file and should_use_direct_inversion(specific_file):
            input_path = specific_file
            input_dir = os.path.dirname(input_path)
            input_filename = os.path.basename(input_path)

            # Find the base directory (skip any DARK MODE ancestors)
            base_dir = input_dir
            while os.path.basename(base_dir).upper() == "DARK MODE":
                base_dir = os.path.dirname(base_dir)

            # Create DARK MODE subfolder in the base dir
            dark_mode_dir = os.path.join(base_dir, "DARK MODE")
            output_path = os.path.join(dark_mode_dir, input_filename)

            pdf_darkmode_logger.info(f"Using direct inversion for cover sheet: {input_filename}")

            if dry_run:
                pdf_darkmode_logger.info(f"[DRY RUN] Would directly invert: {input_path} -> {output_path}")
                return True

            return run_direct_inversion(input_path, output_path)

        # Build the command for standard vector-based conversion
        cmd = [
            "python",
            PDF_DARK_MODE_CLI_PATH,
            "--theme", theme,
            "--log-level", "WARNING"
        ]

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

            cmd.extend(["--output", output_path])

            # For single files, apply --invert-images if requested
            if invert_images:
                cmd.append("--invert-images")

            # Input file comes last as positional argument
            cmd.append(input_path)
        else:
            # Full folder scan (CNC folders are excluded by the watcher, not the CLI)
            cmd.append("--quick-scan")

            # For batch scans, we need to handle two separate filters since CLI only supports one at a time
            # We'll use the COVER filter first, then handle Island Wings in a second pass (see below)
            if invert_images:
                # Use COVER to match "COVER SHEET" PDFs (first pass)
                cmd.extend(["--invert-images-filter", "COVER"])
                pdf_darkmode_logger.info("Image inversion enabled for COVER SHEET PDFs (first pass)")

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

            # If we did a batch scan with image inversion, we need a second pass for Island Wings
            # since CLI only supports one filter at a time
            if not specific_file and invert_images:
                pdf_darkmode_logger.info("Running second pass for Island Wings PDFs with image inversion...")
                cmd_island = [
                    "python",
                    PDF_DARK_MODE_CLI_PATH,
                    "--theme", theme,
                    "--log-level", "WARNING",
                    "--quick-scan",
                    "--invert-images-filter", "ISLAND"
                ]
                if dry_run:
                    cmd_island.append("--dry-run")
                if force:
                    cmd_island.append("--force")

                result2 = subprocess.run(
                    cmd_island,
                    cwd=os.path.dirname(PDF_DARK_MODE_CLI_PATH),
                    capture_output=True,
                    text=True,
                    timeout=600,
                    stdin=subprocess.DEVNULL,
                    startupinfo=startupinfo,
                    creationflags=creationflags
                )

                if result2.returncode == 0:
                    pdf_darkmode_logger.info("Second pass for Island Wings PDFs completed successfully")
                else:
                    pdf_darkmode_logger.warning(f"Second pass for Island Wings PDFs failed with return code {result2.returncode}")

            return True
        else:
            pdf_darkmode_logger.error(f"PDF dark mode conversion failed with return code {result.returncode}")
            if result.stderr:
                pdf_darkmode_logger.error(f"Error output: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        pdf_darkmode_logger.error("PDF dark mode conversion timed out after 10 minutes")
        return False
    except (OSError, RuntimeError) as e:
        pdf_darkmode_logger.error(f"Failed to run PDF dark mode conversion: {e}", exc_info=True)
        return False

def run_dark_mode_conversion_async(dry_run: bool = False, theme: str = "classic", specific_file: Optional[str] = None, force: bool = False, invert_images: bool = False) -> None:
    """
    Run the PDF dark mode converter asynchronously in a separate thread.
    This is useful for running the conversion without blocking the main application.

    Args:
        dry_run: If True, only preview what would be converted without actually converting
        theme: The dark mode theme to use
        specific_file: If provided, only convert this specific PDF file
        force: If True, reconvert all files regardless of modification date
        invert_images: If True, invert images (for PDFs with white background images)
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
