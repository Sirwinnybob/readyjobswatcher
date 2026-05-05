"""
Cabinet-to-sheet index generation for Assembly Sheets and Plans & Elevations PDFs.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
from typing import Dict, List, Optional, Set, Tuple

import fitz  # PyMuPDF

main_logger = logging.getLogger("main")

REFERENCE_INDEX_FILENAME = "cabinet_sheet_index.json"
ASSEMBLY_NAME_PATTERN = re.compile(r"ASSEMBLY\s*SHEETS?", re.IGNORECASE)
PLANS_NAME_PATTERN = re.compile(r"PLANS\s*&\s*ELEVATIONS", re.IGNORECASE)
ASSEMBLY_CAB_PATTERN = re.compile(r"Assembly\s*#\s*(\d{1,4})", re.IGNORECASE)


def _normalize_slashes(path: str) -> str:
    return path.replace("/", "\\")


def _is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def _is_reference_pdf_name(filename: str) -> bool:
    return bool(ASSEMBLY_NAME_PATTERN.search(filename) or PLANS_NAME_PATTERN.search(filename))


def _extract_room_wall_context(text: str) -> Optional[str]:
    match = re.search(r"Room\s*#\d+\s*\([^)]+\)\s*-\s*Wall\s*#\d+", text, re.IGNORECASE)
    if match:
        return re.sub(r"\s+", " ", match.group(0).strip()).upper()
    wall = re.search(r"Wall\s*#\d+", text, re.IGNORECASE)
    if wall:
        return re.sub(r"\s+", " ", wall.group(0).strip()).upper()
    return None


def _add_page(mapping: Dict[str, Set[int]], cabinets: Set[str], page_number: int) -> None:
    for cab in cabinets:
        mapping.setdefault(cab, set()).add(page_number)


def _parse_assembly_pdf(pdf_path: str) -> Dict[str, List[int]]:
    mapping: Dict[str, Set[int]] = {}
    previous_cabinets: Set[str] = set()
    previous_context: Optional[str] = None

    doc = fitz.open(pdf_path)
    try:
        for page_index in range(doc.page_count):
            page_num = page_index + 1
            text = doc[page_index].get_text("text")
            context = _extract_room_wall_context(text)
            cabinets = {m.group(1) for m in ASSEMBLY_CAB_PATTERN.finditer(text)}
            if cabinets:
                _add_page(mapping, cabinets, page_num)
                previous_cabinets = set(cabinets)
                previous_context = context
            else:
                can_carry = bool(
                    previous_cabinets and
                    context and
                    previous_context and
                    context == previous_context and
                    text.strip()
                )
                if can_carry:
                    _add_page(mapping, previous_cabinets, page_num)
    finally:
        doc.close()

    return {cab: sorted(list(pages)) for cab, pages in mapping.items()}


def _parse_plans_pdf(pdf_path: str) -> Dict[str, List[int]]:
    mapping: Dict[str, Set[int]] = {}
    previous_cabinets: Set[str] = set()
    previous_context: Optional[str] = None

    doc = fitz.open(pdf_path)
    try:
        for page_index in range(doc.page_count):
            page_num = page_index + 1
            text = doc[page_index].get_text("text")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            context = _extract_room_wall_context(text)

            cabinets: Set[str] = set()
            hash_idx = next((idx for idx, line in enumerate(lines) if line == "#"), -1)
            has_unit_header = any("UNIT NAME" in line.upper() for line in lines[hash_idx: hash_idx + 6]) if hash_idx >= 0 else False
            if hash_idx >= 0 and has_unit_header:
                i = hash_idx + 1
                while i < len(lines):
                    line = lines[i]
                    if line.upper().startswith("ROOM #"):
                        break
                    if line.isdigit():
                        next_line = lines[i + 1] if i + 1 < len(lines) else ""
                        if any(ch.isalpha() for ch in next_line):
                            cabinets.add(line)
                    i += 1

            if cabinets:
                _add_page(mapping, cabinets, page_num)
                previous_cabinets = set(cabinets)
                previous_context = context
            else:
                can_carry = bool(
                    previous_cabinets and
                    context and
                    previous_context and
                    context == previous_context and
                    hash_idx >= 0 and
                    has_unit_header
                )
                if can_carry:
                    _add_page(mapping, previous_cabinets, page_num)
    finally:
        doc.close()

    return {cab: sorted(list(pages)) for cab, pages in mapping.items()}


def _find_reference_docs(job_folder_path: str) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]]]:
    assembly: Optional[Tuple[str, str]] = None
    plans: Optional[Tuple[str, str]] = None

    light_candidates = []
    try:
        with os.scandir(job_folder_path) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if not _is_pdf(entry.name):
                    continue
                light_candidates.append(entry.path)
    except OSError:
        return None, None

    dark_mode_dir = os.path.join(job_folder_path, "DARK MODE")
    dark_candidates = []
    if os.path.isdir(dark_mode_dir):
        try:
            with os.scandir(dark_mode_dir) as entries:
                for entry in entries:
                    if entry.is_file() and _is_pdf(entry.name):
                        dark_candidates.append(entry.path)
        except OSError:
            pass

    def pick_doc(pattern: re.Pattern[str]) -> Optional[Tuple[str, str]]:
        for path in sorted(light_candidates):
            filename = os.path.basename(path)
            if pattern.search(filename):
                return filename, path
        for path in sorted(dark_candidates):
            filename = os.path.basename(path)
            if pattern.search(filename):
                return filename, path
        return None

    assembly = pick_doc(ASSEMBLY_NAME_PATTERN)
    plans = pick_doc(PLANS_NAME_PATTERN)
    return assembly, plans


def _write_index(job_folder_path: str, payload: Dict) -> None:
    metadata_dir = os.path.join(job_folder_path, ".metadata")
    os.makedirs(metadata_dir, exist_ok=True)
    out_path = os.path.join(metadata_dir, REFERENCE_INDEX_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def build_reference_index_for_job(job_folder_path: str) -> bool:
    """
    Build or refresh cabinet-to-sheet index for a job folder.
    Returns True when an index file was written.
    """
    if not os.path.isdir(job_folder_path):
        return False

    assembly_doc, plans_doc = _find_reference_docs(job_folder_path)
    if assembly_doc is None and plans_doc is None:
        return False

    assembly_filename = assembly_doc[0] if assembly_doc else ""
    assembly_map = _parse_assembly_pdf(assembly_doc[1]) if assembly_doc else {}
    plans_filename = plans_doc[0] if plans_doc else ""
    plans_map = _parse_plans_pdf(plans_doc[1]) if plans_doc else {}

    payload = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "documents": {
            "assembly": {
                "pdfFilename": assembly_filename,
                "cabinetToPages": assembly_map,
            },
            "plansElevations": {
                "pdfFilename": plans_filename,
                "cabinetToPages": plans_map,
            },
        },
    }
    _write_index(job_folder_path, payload)
    main_logger.info(
        "Reference index updated: job=%s assemblyCabs=%s plansCabs=%s",
        os.path.basename(job_folder_path),
        len(assembly_map),
        len(plans_map),
    )
    return True


def build_reference_index_for_pdf_event(pdf_path: str) -> bool:
    """
    Rebuild index for a specific modified/created PDF when it matches reference docs.
    """
    normalized = _normalize_slashes(pdf_path)
    filename = os.path.basename(normalized)
    if not _is_reference_pdf_name(filename):
        return False

    folder = os.path.dirname(normalized)
    if os.path.basename(folder).upper() == "DARK MODE":
        job_folder = os.path.dirname(folder)
    else:
        job_folder = folder

    if os.path.basename(job_folder).upper() == "CNC":
        return False
    return build_reference_index_for_job(job_folder)
