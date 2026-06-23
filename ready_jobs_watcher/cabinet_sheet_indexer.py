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
from .refresh_signals import touch_cnc_refresh_signal
from .utils import open_pdf_with_retry

main_logger = logging.getLogger("main")

REFERENCE_INDEX_FILENAME = "cabinet_sheet_index.json"
ASSEMBLY_NAME_PATTERN = re.compile(r"ASSEMBLY\s*SHEETS?", re.IGNORECASE)
PLANS_NAME_PATTERN = re.compile(r"PLANS\s*&\s*ELEVATIONS", re.IGNORECASE)
DELIVERY_NAME_PATTERN = re.compile(r"DELIVERY\s*SHEETS?", re.IGNORECASE)
ASSEMBLY_FF_PATTERN = re.compile(r"\bFF\b", re.IGNORECASE)
ASSEMBLY_FL_PATTERN = re.compile(r"\bFL\b", re.IGNORECASE)
MODE_FACE_FRAME_PATTERN = re.compile(r"\bFACE[\s\-]*FRAME\b", re.IGNORECASE)
MODE_FRAMELESS_PATTERN = re.compile(r"\bFRAMELESS\b", re.IGNORECASE)
MODE_BOTH_PATTERN = re.compile(r"\bBOTH\b", re.IGNORECASE)
MODE_SOURCE_DELIVERY = "DELIVERY_SHEET"
MODE_SOURCE_ASSEMBLY_FILENAME = "ASSEMBLY_FILENAMES"
MODE_SOURCE_UNKNOWN = "UNKNOWN"

# Legacy heuristic patterns (fallback when PDF has no structured markers)
ASSEMBLY_CAB_PATTERN = re.compile(r"Assembly\s*#\s*(\d{1,4})", re.IGNORECASE)

# Extracts unit name from "| Assembly #N - Unit Name |" lines present on every assembly page.
# e.g. "| Assembly #13 - Floating Shelf |"  →  cab="13", name="Floating Shelf"
ASSEMBLY_UNIT_NAME_PATTERN = re.compile(
    r"\|\s*Assembly\s*#(\d{1,4})\s*-\s*(.+?)\s*\|", re.IGNORECASE
)

# Structured marker patterns — added to Cabinet Vision report templates.
# Format: ||CAB:42|| and optionally ||WALL:Room 1 - Wall A||
# The ||WALL:...|| value uses " - " as a separator between room name and wall name.
CAB_MARKER_PATTERN = re.compile(r"\|\|CAB:(\d{1,4})\|\|", re.IGNORECASE)

# Cabinet number sort key — cabinet numbers are always integers, but guard against bad data.
def _cab_sort_key(c: str):
    try:
        return (0, int(c))
    except ValueError:
        return (1, c)


def _extract_assembly_cabinet_names(text: str) -> Dict[str, str]:
    """Extract {cab_number: unit_name} from '| Assembly #N - Unit Name |' lines.

    Cabinet Vision includes this on every assembly sheet page, e.g.:
        | Assembly #13 - Floating Shelf |
    This is the authoritative source for cabinet unit names — more reliable than
    the plans/elevations table because every cabinet always gets assembly sheets.
    """
    names: Dict[str, str] = {}
    for m in ASSEMBLY_UNIT_NAME_PATTERN.finditer(text):
        name = m.group(2).strip()
        if name:
            names[m.group(1)] = name
    return names


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
    return bool(
        ASSEMBLY_NAME_PATTERN.search(filename)
        or PLANS_NAME_PATTERN.search(filename)
        or DELIVERY_NAME_PATTERN.search(filename)
    )


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
    """A valid part description must have at least 2 chars and contain a letter."""
    return len(s) >= 2 and bool(re.search(r"[A-Za-z]", s))


_QTY_WIDTH_PATTERN = re.compile(r"^(\d+)\s+(\d+\.?\d*)$")
_NUMERIC_PATTERN = re.compile(r"^\d+\.?\d*$")


def _parse_assembly_parts_for_page(text: str) -> List[dict]:
    """Extract the bill-of-materials from one assembly sheet page.

    Supports two Cabinet Vision BOM line formats:
      4-line: "qty width" on one line, then length, description, material
      5-line: qty, width, length, description, material on separate lines
    The 4-line format is tried first since it's more specific.
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
        if current_section is None:
            i += 1
            continue

        # 4-line format: "qty width" combined on one line
        qty_width_m = _QTY_WIDTH_PATTERN.match(lines[i])
        if qty_width_m and i + 3 < len(lines):
            l_raw, desc_raw, mat_raw = lines[i + 1], lines[i + 2], lines[i + 3]
            if (
                _NUMERIC_PATTERN.match(l_raw)
                and _is_part_description(desc_raw)
                and _is_part_description(mat_raw)
                and "|" not in mat_raw  # exclude section headers captured as material
            ):
                parts.append({
                    "qty": int(qty_width_m.group(1)),
                    "width": float(qty_width_m.group(2)),
                    "length": float(l_raw),
                    "description": desc_raw,
                    "material": mat_raw,
                    "sectionType": current_section,
                    "isPurchased": False,
                })
                i += 4
                continue

        # 5-line format: qty, width, length, description, material on separate lines
        if i + 4 >= len(lines):
            i += 1
            continue
        qty_raw, w_raw, l_raw, desc_raw, mat_raw = (
            lines[i], lines[i + 1], lines[i + 2], lines[i + 3], lines[i + 4]
        )
        qty_m = re.match(r"^(\d+)(\s*P)?$", qty_raw)
        if (
            not qty_m
            or not _NUMERIC_PATTERN.match(w_raw)
            or not _NUMERIC_PATTERN.match(l_raw)
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
            "cabinets": sorted(cabs, key=_cab_sort_key),
            "room": room,
            "wall": wall,
            "parts": _parse_assembly_parts_for_page(text),
            "cabinetNames": _extract_assembly_cabinet_names(text),
        }

    if not any_markers:
        return None

    return _DocumentParseResult(
        cabinet_to_pages={cab: sorted(pages) for cab, pages in mapping.items()},
        page_details=page_details,
    )


def _parse_assembly_pdf(pdf_path: str) -> _DocumentParseResult:
    doc = open_pdf_with_retry(pdf_path)
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
                    "cabinets": sorted(cabinets, key=_cab_sort_key),
                    "room": room,
                    "wall": wall,
                    "parts": _parse_assembly_parts_for_page(text),
                    "cabinetNames": _extract_assembly_cabinet_names(text),
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


def _parse_plans_table(lines: List[str]) -> Dict[str, str]:
    """Extract non-zero cabinet numbers and their unit names from a pipe-delimited plans table.

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

    Returns a dict mapping cabinet number string → unit name string.
    Appliance placeholders (cabinet number 0) are excluded.
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
        return {}

    # Skip all remaining pipe-containing header lines
    data_start = header_idx + 1
    while data_start < len(lines) and "|" in lines[data_start]:
        data_start += 1

    # Identify cabinet row start positions: a line that is a plain integer followed
    # by a non-numeric, non-pipe line (the unit name).  This handles variable row
    # lengths (some cabinet types emit 6 lines, others 7) without any stride assumption.
    def _is_numeric_str(s: str) -> bool:
        try:
            float(s)
            return True
        except ValueError:
            return False

    cab_positions: List[int] = []
    for j in range(data_start, len(lines) - 2):
        line = lines[j]
        next_line = lines[j + 1]
        width_line = lines[j + 2]
        # Cabinet row pattern: integer | unit_name (non-numeric, no pipe) | width (numeric)
        # The width check eliminates false positives from page numbers or other stray integers.
        if (line.isdigit()
                and not _is_numeric_str(next_line)
                and "|" not in next_line
                and _is_numeric_str(width_line)):
            cab_positions.append(j)

    # Cabinet number 0 is an appliance placeholder — skip it.
    cabinets: Dict[str, str] = {}
    for pos in cab_positions:
        cab_num = int(lines[pos])
        if cab_num > 0:
            cabinets[lines[pos]] = lines[pos + 1]

    return cabinets


def _parse_plans_pdf(pdf_path: str) -> _DocumentParseResult:
    doc = open_pdf_with_retry(pdf_path)
    try:
        marker_result = _try_parse_with_markers(doc)
        if marker_result is not None:
            return marker_result

        # Fallback: pipe-delimited "| # | | Unit Name |" table structure.
        # Row length varies (6 or 7 lines depending on cabinet type); positions are
        # detected by finding integer lines followed by a non-numeric unit-name line.
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

            cabinet_names_map = _parse_plans_table(lines)
            cabinets: Set[str] = set(cabinet_names_map.keys())

            if cabinets:
                _add_page(mapping, cabinets, page_num)
                previous_cabinets = set(cabinets)
                previous_context = context
                room, wall = _extract_room_wall_parts(text)
                page_details[str(page_num)] = {
                    "cabinets": sorted(cabinets, key=_cab_sort_key),
                    "room": room,
                    "wall": wall,
                    "cabinetNames": cabinet_names_map,
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


def _collect_pdf_candidates(folder_path: str) -> List[str]:
    out: List[str] = []
    try:
        with os.scandir(folder_path) as entries:
            for entry in entries:
                if entry.is_file() and _is_pdf(entry.name):
                    out.append(entry.path)
    except OSError:
        return []
    return sorted(out)


def _classify_assembly_variant(filename: str) -> str:
    if ASSEMBLY_FF_PATTERN.search(filename):
        return "FACE_FRAME"
    if ASSEMBLY_FL_PATTERN.search(filename):
        return "FRAMELESS"
    return "BASE"


def _detect_mode_from_text(text: str) -> Optional[str]:
    if MODE_BOTH_PATTERN.search(text):
        return "BOTH"
    has_frame = bool(MODE_FACE_FRAME_PATTERN.search(text))
    has_frameless = bool(MODE_FRAMELESS_PATTERN.search(text))
    if has_frame and has_frameless:
        return "BOTH"
    if has_frameless:
        return "FRAMELESS"
    if has_frame:
        return "FACE-FRAME"
    return None


def _mode_from_assembly_filenames(assembly_by_variant: Dict[str, Tuple[str, str]]) -> Optional[str]:
    has_ff = "FACE_FRAME" in assembly_by_variant
    has_fl = "FRAMELESS" in assembly_by_variant
    if has_ff and has_fl:
        return "BOTH"
    if has_ff:
        return "FACE-FRAME"
    if has_fl:
        return "FRAMELESS"
    return None


def _extract_known_delivery_fields(lines: List[str]) -> Dict[str, Optional[str]]:
    pairs: Dict[str, str] = {}
    for idx, line in enumerate(lines):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = re.sub(r"\s+", " ", key).strip().lower()
        value = re.sub(r"\s+", " ", value).strip()
        if not value and idx + 1 < len(lines):
            candidate = lines[idx + 1].strip()
            if candidate and ":" not in candidate:
                value = candidate
        if key and value:
            pairs[key] = value

    def _first(*keys: str) -> Optional[str]:
        for key in keys:
            if key in pairs:
                return pairs[key]
        return None

    return {
        "jobNumber": _first("job #", "job number"),
        "jobName": _first("job name"),
        "date": _first("date"),
        "draftedBy": _first("drafted by"),
        "revisedBy": _first("revised by"),
        "engineeredBy": _first("engineered by"),
        "city": _first("city"),
        "state": _first("state"),
        "address": _first("address"),
        "phone": _first("phone"),
    }


def _parse_delivery_pdf_metadata(pdf_path: str) -> Dict:
    doc = open_pdf_with_retry(pdf_path)
    try:
        page_payloads: List[Dict] = []
        mode_candidate: Optional[str] = None
        first_page_lines: List[str] = []
        for page_index in range(doc.page_count):
            text = doc[page_index].get_text("text")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if page_index == 0:
                first_page_lines = lines
            detected = _detect_mode_from_text(text)
            if detected and mode_candidate is None:
                mode_candidate = detected
            page_payloads.append(
                {
                    "page": page_index + 1,
                    "text": text,
                    "lines": lines,
                }
            )
        known_fields = _extract_known_delivery_fields(first_page_lines)
        return {
            "pageCount": doc.page_count,
            "modeCandidate": mode_candidate or "UNKNOWN",
            "knownFields": known_fields,
            "rawDump": {
                "pages": page_payloads,
            },
        }
    finally:
        doc.close()


def detect_mode_for_job(job_folder_path: str) -> Tuple[str, str]:
    docs = _find_reference_docs(job_folder_path)
    delivery = docs.get("delivery")
    if delivery is not None:
        try:
            delivery_meta = _parse_delivery_pdf_metadata(delivery[1])
            mode = str(delivery_meta.get("modeCandidate", "UNKNOWN") or "UNKNOWN")
            if mode != "UNKNOWN":
                return mode, MODE_SOURCE_DELIVERY
        except Exception:
            pass
    mode = _mode_from_assembly_filenames(docs.get("assemblyByVariant", {}))
    if mode:
        return mode, MODE_SOURCE_ASSEMBLY_FILENAME
    return "UNKNOWN", MODE_SOURCE_UNKNOWN


def detect_mode_template_mismatch_for_job(job_folder_path: str) -> Optional[Dict[str, str]]:
    """Detect delivery-vs-assembly construction mode mismatches for a job.

    Delivery sheet mode is treated as the source-of-truth when available.
    Returns None when no actionable mismatch can be determined.
    """
    docs = _find_reference_docs(job_folder_path)
    delivery_mode = "UNKNOWN"
    assembly_mode = "UNKNOWN"

    delivery = docs.get("delivery")
    if delivery is not None:
        try:
            delivery_meta = _parse_delivery_pdf_metadata(delivery[1])
            delivery_mode = str(delivery_meta.get("modeCandidate", "UNKNOWN") or "UNKNOWN")
        except Exception:
            delivery_mode = "UNKNOWN"

    inferred_assembly_mode = _mode_from_assembly_filenames(docs.get("assemblyByVariant", {}))
    if inferred_assembly_mode:
        assembly_mode = inferred_assembly_mode

    if delivery_mode == "UNKNOWN" or assembly_mode == "UNKNOWN":
        return None
    if delivery_mode == assembly_mode:
        return None

    return {
        "deliveryMode": delivery_mode,
        "assemblyMode": assembly_mode,
    }


def _find_reference_docs(job_folder_path: str) -> Dict[str, object]:
    light_candidates = _collect_pdf_candidates(job_folder_path)
    dark_mode_dir = os.path.join(job_folder_path, "DARK MODE")
    dark_candidates = _collect_pdf_candidates(dark_mode_dir) if os.path.isdir(dark_mode_dir) else []

    plans: Optional[Tuple[str, str]] = None
    delivery: Optional[Tuple[str, str]] = None
    assembly_by_variant: Dict[str, Tuple[str, str]] = {}

    for path in list(light_candidates) + list(dark_candidates):
        filename = os.path.basename(path)
        if plans is None and PLANS_NAME_PATTERN.search(filename):
            plans = (filename, path)
            continue
        if delivery is None and DELIVERY_NAME_PATTERN.search(filename):
            delivery = (filename, path)
            continue
        if ASSEMBLY_NAME_PATTERN.search(filename):
            variant = _classify_assembly_variant(filename)
            if variant not in assembly_by_variant:
                assembly_by_variant[variant] = (filename, path)

    return {
        "assemblyByVariant": assembly_by_variant,
        "plans": plans,
        "delivery": delivery,
    }


def _build_virtual_combined_assembly(
    assembly_by_variant: Dict[str, Tuple[str, str]],
    parsed_by_variant: Dict[str, _DocumentParseResult],
) -> Dict:
    variant_priority = ["FACE_FRAME", "FRAMELESS", "BASE"]
    all_cabs: Set[str] = set()
    for result in parsed_by_variant.values():
        all_cabs.update(result.cabinet_to_pages.keys())

    cabinet_order = sorted(all_cabs, key=_cab_sort_key)
    virtual_page_to_source: Dict[str, Dict] = {}
    virtual_page_details: Dict[str, Dict] = {}
    cabinet_to_virtual_pages: Dict[str, List[int]] = {}
    entries_by_cabinet: Dict[str, List[Dict]] = {}
    page_counter = 0

    for cabinet in cabinet_order:
        entries: List[Dict] = []
        virtual_pages: List[int] = []
        for variant in variant_priority:
            result = parsed_by_variant.get(variant)
            if result is None:
                continue
            source = assembly_by_variant.get(variant)
            if source is None:
                continue
            pages = result.cabinet_to_pages.get(cabinet, [])
            for page in pages:
                page_counter += 1
                entry = {
                    "variant": variant,
                    "pdfFilename": source[0],
                    "page": page,
                    "virtualPage": page_counter,
                }
                entries.append(entry)
                virtual_pages.append(page_counter)
                virtual_page_to_source[str(page_counter)] = {
                    "variant": variant,
                    "pdfFilename": source[0],
                    "page": page,
                    "cabinet": cabinet,
                }
                source_page_detail = result.page_details.get(str(page), {})
                detail = {
                    "cabinets": source_page_detail.get("cabinets", [cabinet]),
                    "room": source_page_detail.get("room"),
                    "wall": source_page_detail.get("wall"),
                    "parts": source_page_detail.get("parts", []),
                    "cabinetNames": source_page_detail.get("cabinetNames", {}),
                    "sourceVariant": variant,
                    "sourcePdfFilename": source[0],
                    "sourcePage": page,
                }
                virtual_page_details[str(page_counter)] = detail
        if entries:
            entries_by_cabinet[cabinet] = entries
            cabinet_to_virtual_pages[cabinet] = virtual_pages

    return {
        "cabinetOrder": cabinet_order,
        "entriesByCabinet": entries_by_cabinet,
        "cabinetToPages": cabinet_to_virtual_pages,
        "virtualPageToSource": virtual_page_to_source,
        "pageDetails": virtual_page_details,
        "totalVirtualPages": page_counter,
    }


def _write_index(job_folder_path: str, payload: Dict) -> None:
    try:
        metadata_dir = os.path.join(job_folder_path, ".metadata")
        os.makedirs(metadata_dir, exist_ok=True)
        out_path = os.path.join(metadata_dir, REFERENCE_INDEX_FILENAME)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        main_logger.warning("cabinet_sheet_indexer: could not write index for %s: %s", job_folder_path, exc)


def build_reference_index_for_job(job_folder_path: str) -> bool:
    """
    Build or refresh cabinet-to-sheet index for a job folder.
    Returns True when an index file was written.
    """
    if not os.path.isdir(job_folder_path):
        return False

    docs = _find_reference_docs(job_folder_path)
    assembly_by_variant: Dict[str, Tuple[str, str]] = docs.get("assemblyByVariant", {})
    plans_doc: Optional[Tuple[str, str]] = docs.get("plans")
    delivery_doc: Optional[Tuple[str, str]] = docs.get("delivery")
    if not assembly_by_variant and plans_doc is None and delivery_doc is None:
        return False

    parsed_assembly_by_variant: Dict[str, _DocumentParseResult] = {}
    for variant, (_, path) in assembly_by_variant.items():
        parsed_assembly_by_variant[variant] = _parse_assembly_pdf(path)
    primary_variant = "FACE_FRAME" if "FACE_FRAME" in assembly_by_variant else ("FRAMELESS" if "FRAMELESS" in assembly_by_variant else ("BASE" if "BASE" in assembly_by_variant else ""))
    assembly_filename = assembly_by_variant.get(primary_variant, ("", ""))[0] if primary_variant else ""
    assembly_result = parsed_assembly_by_variant.get(primary_variant, _DocumentParseResult())

    plans_filename = plans_doc[0] if plans_doc else ""
    plans_result = _parse_plans_pdf(plans_doc[1]) if plans_doc else _DocumentParseResult()
    delivery_filename = delivery_doc[0] if delivery_doc else ""
    delivery_metadata = _parse_delivery_pdf_metadata(delivery_doc[1]) if delivery_doc else {
        "pageCount": 0,
        "modeCandidate": "UNKNOWN",
        "knownFields": {},
        "rawDump": {"pages": []},
    }
    mode = delivery_metadata.get("modeCandidate", "UNKNOWN")
    mode_source = MODE_SOURCE_DELIVERY if mode != "UNKNOWN" else MODE_SOURCE_UNKNOWN
    if mode == "UNKNOWN":
        mode_from_filenames = _mode_from_assembly_filenames(assembly_by_variant)
        if mode_from_filenames:
            mode = mode_from_filenames
            mode_source = MODE_SOURCE_ASSEMBLY_FILENAME

    virtual_combined = _build_virtual_combined_assembly(assembly_by_variant, parsed_assembly_by_variant)
    assembly_sources = []
    for variant in ["FACE_FRAME", "FRAMELESS", "BASE"]:
        source = assembly_by_variant.get(variant)
        result = parsed_assembly_by_variant.get(variant)
        if source is None or result is None:
            continue
        assembly_sources.append(
            {
                "variant": variant,
                "pdfFilename": source[0],
                "cabinetToPages": result.cabinet_to_pages,
                "pageDetails": result.page_details,
            }
        )

    payload = {
        "schemaVersion": 2,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "documents": {
            "assembly": {
                "pdfFilename": assembly_filename,
                "cabinetToPages": assembly_result.cabinet_to_pages,
                "pageDetails": assembly_result.page_details,
                "mode": mode,
                "modeSource": mode_source,
                "sources": assembly_sources,
                "virtualCombined": virtual_combined,
            },
            "plansElevations": {
                "pdfFilename": plans_filename,
                "cabinetToPages": plans_result.cabinet_to_pages,
                "pageDetails": plans_result.page_details,
            },
            "delivery": {
                "pdfFilename": delivery_filename,
                "mode": mode,
                "modeSource": mode_source,
                "knownFields": delivery_metadata.get("knownFields", {}),
                "rawDump": delivery_metadata.get("rawDump", {"pages": []}),
            },
        },
    }
    _write_index(job_folder_path, payload)
    touch_cnc_refresh_signal(
        job_folder_path=job_folder_path,
        reason="reference_index_updated",
        source="cabinet_sheet_indexer",
    )
    main_logger.info(
        "Reference index updated: job=%s assemblyCabs=%s plansCabs=%s",
        os.path.basename(job_folder_path),
        len(virtual_combined.get("cabinetToPages", {})),
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
