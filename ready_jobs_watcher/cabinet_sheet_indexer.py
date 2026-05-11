"""
Cabinet-to-sheet index generation for Assembly Sheets and Plans & Elevations PDFs.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import fitz  # PyMuPDF

main_logger = logging.getLogger("main")

REFERENCE_INDEX_FILENAME = "cabinet_sheet_index.json"
ASSEMBLY_NAME_PATTERN = re.compile(r"ASSEMBLY\s*SHEETS?", re.IGNORECASE)
PLANS_NAME_PATTERN = re.compile(r"PLANS\s*&\s*ELEVATIONS", re.IGNORECASE)

# Legacy heuristic patterns (fallback when PDF has no structured markers)
ASSEMBLY_CAB_PATTERN = re.compile(r"Assembly\s*#\s*(\d{1,4})", re.IGNORECASE)

# Structured marker patterns — added to Cabinet Vision report templates.
# Format: ||CAB:42|| and optionally ||WALL:Room 1 - Wall A||
# The ||WALL:...|| value uses " - " as a separator between room name and wall name.
CAB_MARKER_PATTERN = re.compile(r"\|\|CAB:(\d{1,4})\|\|", re.IGNORECASE)
WALL_MARKER_PATTERN = re.compile(r"\|\|WALL:([^|]+)\|\|", re.IGNORECASE)

# Assembly sheet part-list extraction patterns
_SECTION_HEADER = re.compile(r"^\|\s*([^|]+?)\s*\|$")
_SECTION_EXCLUDES = re.compile(
    r"Assembly\s*#|\d+\s*\*\s*\d+|Wall\s*#|Room\s*#|Date:|"
    r"Full\s*Overlay|Overlay\b|Inset\b|\d{3,}\s*-",
    re.IGNORECASE,
)
_KNOWN_SECTIONS = {
    "face frame", "doors", "frame", "panel stock",
    "hardware", "drawer fronts", "drawer boxes",
}


@dataclass
class _DocumentParseResult:
    cabinet_to_pages: Dict[str, List[int]] = field(default_factory=dict)
    # key = str(page_number), value = {cabinets, room, wall}
    page_details: Dict[str, dict] = field(default_factory=dict)


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


def _extract_room_wall_parts(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract (room, wall) strings for pageDetails from Cabinet Vision PDF text.

    Handles two formats:
      Assembly Sheets  — combined on one line:  '| Room #1 (KITCHEN) - Wall #2 |'
      Plans pages      — separate pipe fields:  '| Wall #2 |'  +  '| Room #1 -  (KITCHEN) |'
    """
    # Combined form (assembly sheets): Room #N (NAME) - Wall #N
    combined = re.search(
        r"Room\s*#\d+\s*\([^)]+\)\s*-\s*Wall\s*#\d+",
        text, re.IGNORECASE
    )
    if combined:
        rm = re.search(r"Room\s*#\d+\s*\([^)]+\)", combined.group(0), re.IGNORECASE)
        wm = re.search(r"Wall\s*#\d+", combined.group(0), re.IGNORECASE)
        room = re.sub(r"\s+", " ", rm.group(0)).strip() if rm else None
        wall = re.sub(r"\s+", " ", wm.group(0)).strip() if wm else None
        return room, wall

    # Plans form — wall: '| Wall #N |'
    wall: Optional[str] = None
    wall_m = re.search(r"\|\s*(Wall\s*#\d+)\s*\|", text, re.IGNORECASE)
    if wall_m:
        wall = re.sub(r"\s+", " ", wall_m.group(1)).strip()

    # Plans form — room: '| Room #N - (NAME) |' or '| Room #N (NAME) |'
    room: Optional[str] = None
    room_m = re.search(
        r"\|\s*(Room\s*#\d+\s*(?:-\s*)?\([^)]+\))\s*\|",
        text, re.IGNORECASE
    )
    if room_m:
        raw = re.sub(r"\s+", " ", room_m.group(1)).strip()
        room = re.sub(r"\s*-\s*\(", " (", raw)  # "Room #1 - (KITCHEN)" → "Room #1 (KITCHEN)"

    return room, wall


def _extract_section_name(line: str) -> Optional[str]:
    """Return the section label from a '| Label |' line if it's a known parts section."""
    m = _SECTION_HEADER.match(line)
    if not m:
        return None
    content = m.group(1).strip()
    if _SECTION_EXCLUDES.search(content):
        return None
    lower = content.lower()
    if any(lower.startswith(k) for k in _KNOWN_SECTIONS):
        return content
    return None


def _is_part_description(s: str) -> bool:
    """A valid part description must contain at least one letter (filters diagram data)."""
    return bool(re.search(r"[A-Za-z]", s))


def _parse_assembly_parts_for_page(text: str) -> List[dict]:
    """Extract the bill-of-materials from one assembly sheet page.

    Each part is 5 consecutive non-empty lines: qty, width, length, description, material.
    Stops within a section when lines no longer match that pattern (e.g. diagram data).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    parts: List[dict] = []
    current_section: Optional[str] = None
    i = 0
    while i < len(lines):
        section = _extract_section_name(lines[i])
        if section is not None:
            current_section = section
            i += 1
            continue
        if current_section is None or i + 4 >= len(lines):
            i += 1
            continue
        qty_raw, w_raw, l_raw, desc_raw, mat_raw = (
            lines[i], lines[i + 1], lines[i + 2], lines[i + 3], lines[i + 4]
        )
        qty_m = re.match(r"^(\d+)(\s*P)?$", qty_raw)
        if (
            not qty_m
            or not re.match(r"^\d+\.?\d*$", w_raw)
            or not re.match(r"^\d+\.?\d*$", l_raw)
            or not _is_part_description(desc_raw)
            or not _is_part_description(mat_raw)
        ):
            i += 1
            continue
        parts.append({
            "qty": int(qty_m.group(1)),
            "width": float(w_raw),
            "length": float(l_raw),
            "description": desc_raw,
            "material": mat_raw,
            "sectionType": current_section,
            "isPurchased": bool(qty_m.group(2)),
        })
        i += 5
    return parts


def _extract_marker_cabinets(text: str) -> Set[str]:
    return {m.group(1) for m in CAB_MARKER_PATTERN.finditer(text)}


def _extract_marker_wall(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (room, wall) from ||WALL:Room X - Wall Y|| marker.

    If the value has no " - " separator the entire value is treated as the wall.
    """
    m = WALL_MARKER_PATTERN.search(text)
    if not m:
        return None, None
    raw = m.group(1).strip()
    if " - " in raw:
        room_part, wall_part = raw.split(" - ", 1)
        return room_part.strip() or None, wall_part.strip() or None
    return None, raw or None


def _add_page(mapping: Dict[str, Set[int]], cabinets: Set[str], page_number: int) -> None:
    for cab in cabinets:
        mapping.setdefault(cab, set()).add(page_number)


def _try_parse_with_markers(doc: fitz.Document) -> Optional[_DocumentParseResult]:
    """Scan the entire document for ||CAB:xx|| markers.

    Returns a result when at least one marker is found anywhere in the document.
    Returns None when no markers are present (caller should use the heuristic fallback).
    """
    any_markers = False
    mapping: Dict[str, Set[int]] = {}
    page_details: Dict[str, dict] = {}

    for page_index in range(doc.page_count):
        page_num = page_index + 1
        text = doc[page_index].get_text("text")
        cabs = _extract_marker_cabinets(text)
        if not cabs:
            continue
        any_markers = True
        for cab in cabs:
            mapping.setdefault(cab, set()).add(page_num)
        room, wall = _extract_marker_wall(text)
        page_details[str(page_num)] = {
            "cabinets": sorted(cabs, key=lambda x: int(x)),
            "room": room,
            "wall": wall,
            "parts": _parse_assembly_parts_for_page(text),
        }

    if not any_markers:
        return None

    return _DocumentParseResult(
        cabinet_to_pages={cab: sorted(pages) for cab, pages in mapping.items()},
        page_details=page_details,
    )


def _parse_assembly_pdf(pdf_path: str) -> _DocumentParseResult:
    doc = fitz.open(pdf_path)
    try:
        marker_result = _try_parse_with_markers(doc)
        if marker_result is not None:
            return marker_result

        # Fallback: legacy heuristic using "Assembly # 42" text pattern
        mapping: Dict[str, Set[int]] = {}
        page_details: Dict[str, dict] = {}
        previous_cabinets: Set[str] = set()
        previous_context: Optional[str] = None

        for page_index in range(doc.page_count):
            page_num = page_index + 1
            text = doc[page_index].get_text("text")
            context = _extract_room_wall_context(text)
            cabinets = {m.group(1) for m in ASSEMBLY_CAB_PATTERN.finditer(text)}
            if cabinets:
                _add_page(mapping, cabinets, page_num)
                previous_cabinets = set(cabinets)
                previous_context = context
                room, wall = _extract_room_wall_parts(text)
                page_details[str(page_num)] = {
                    "cabinets": sorted(cabinets, key=lambda x: int(x)),
                    "room": room,
                    "wall": wall,
                    "parts": _parse_assembly_parts_for_page(text),
                }
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

    return _DocumentParseResult(
        cabinet_to_pages={cab: sorted(list(pages)) for cab, pages in mapping.items()},
        page_details=page_details,
    )


_PLANS_TABLE_HEADER = re.compile(r"\|\s*#\s*\|")
_PLANS_UNIT_NAME = re.compile(r"\|\s*Unit\s+Name\s*\|", re.IGNORECASE)


def _parse_plans_table(lines: List[str]) -> Set[str]:
    """Extract non-zero cabinet numbers from a pipe-delimited plans table.

    Cabinet Vision emits:
        | # | | Unit Name |   ← header (# and Unit Name on one line, or nearby)
        | Width |
        | Height |
        | Depth |
        | L.SCR | | R.SCR |
        1                      ← cabinet number (integer; 0 = appliance placeholder)
        Std Tall               ← unit name
        29                     ← width
        95.5                   ← height
        24                     ← depth
        0.5                    ← L.SCR
        0.5                    ← R.SCR
        2                      ← next cabinet ...
    """
    # Find the header line containing "| # |"
    header_idx = -1
    for i, line in enumerate(lines):
        if _PLANS_TABLE_HEADER.search(line):
            # Confirm "Unit Name" is on the same line or within 2 lines
            window = lines[i: i + 3]
            if any(_PLANS_UNIT_NAME.search(l) for l in window):
                header_idx = i
                break

    if header_idx < 0:
        return set()

    # Skip all remaining pipe-containing header lines
    data_start = header_idx + 1
    while data_start < len(lines) and "|" in lines[data_start]:
        data_start += 1

    # Read 7-line groups as table rows
    cabinets: Set[str] = set()
    i = data_start
    while i < len(lines):
        cab_line = lines[i]
        if not cab_line.isdigit():
            break
        if int(cab_line) > 0:
            cabinets.add(cab_line)
        i += 7  # consume: cab_num, name, width, height, depth, l_scr, r_scr

    return cabinets


def _parse_plans_pdf(pdf_path: str) -> _DocumentParseResult:
    doc = fitz.open(pdf_path)
    try:
        marker_result = _try_parse_with_markers(doc)
        if marker_result is not None:
            return marker_result

        # Fallback: pipe-delimited "| # | | Unit Name |" table structure.
        # Each table row is exactly 7 consecutive lines:
        #   cabinet_number (integer string), unit_name, width, height, depth, l_scr, r_scr
        # Cabinet number 0 means an appliance/non-cabinet placeholder — skip it.
        mapping: Dict[str, Set[int]] = {}
        page_details: Dict[str, dict] = {}
        previous_cabinets: Set[str] = set()
        previous_context: Optional[str] = None

        for page_index in range(doc.page_count):
            page_num = page_index + 1
            text = doc[page_index].get_text("text")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            context = _extract_room_wall_context(text)

            cabinets = _parse_plans_table(lines)

            if cabinets:
                _add_page(mapping, cabinets, page_num)
                previous_cabinets = set(cabinets)
                previous_context = context
                room, wall = _extract_room_wall_parts(text)
                page_details[str(page_num)] = {
                    "cabinets": sorted(cabinets, key=lambda x: int(x)),
                    "room": room,
                    "wall": wall,
                }
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

    return _DocumentParseResult(
        cabinet_to_pages={cab: sorted(list(pages)) for cab, pages in mapping.items()},
        page_details=page_details,
    )


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
    assembly_result = _parse_assembly_pdf(assembly_doc[1]) if assembly_doc else _DocumentParseResult()
    plans_filename = plans_doc[0] if plans_doc else ""
    plans_result = _parse_plans_pdf(plans_doc[1]) if plans_doc else _DocumentParseResult()

    payload = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "documents": {
            "assembly": {
                "pdfFilename": assembly_filename,
                "cabinetToPages": assembly_result.cabinet_to_pages,
                "pageDetails": assembly_result.page_details,
            },
            "plansElevations": {
                "pdfFilename": plans_filename,
                "cabinetToPages": plans_result.cabinet_to_pages,
                "pageDetails": plans_result.page_details,
            },
        },
    }
    _write_index(job_folder_path, payload)
    main_logger.info(
        "Reference index updated: job=%s assemblyCabs=%s plansCabs=%s",
        os.path.basename(job_folder_path),
        len(assembly_result.cabinet_to_pages),
        len(plans_result.cabinet_to_pages),
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
