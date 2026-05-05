"""
Hardwoods cutlist index generation for job-root cut list PDFs.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF

main_logger = logging.getLogger("main")

HARDWOODS_METADATA_DIR = "hardwoods"
HARDWOODS_INDEX_FILENAME = "cutlist_index.json"

DOC_TYPE_FACE_FRAME = "FACE_FRAME_CUT_LIST"
DOC_TYPE_NAILER = "NAILER_CUT_LIST"
DOC_TYPE_DOOR_CUT = "DOOR_CUT_LIST"
DOC_TYPE_DOOR_LIST = "DOOR_LIST"

_DOC_DEFINITIONS = [
    (DOC_TYPE_FACE_FRAME, re.compile(r"FACE\s*FRAME\s*CUT\s*LIST", re.IGNORECASE)),
    (DOC_TYPE_NAILER, re.compile(r"NAILER\s*CUT\s*LIST", re.IGNORECASE)),
    (DOC_TYPE_DOOR_CUT, re.compile(r"DOOR\s*CUT\s*LIST", re.IGNORECASE)),
    (DOC_TYPE_DOOR_LIST, re.compile(r"DOOR\s*LIST", re.IGNORECASE)),
]

_NUMERIC_VALUE_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")
_DOOR_LIST_SIZE_PATTERN = re.compile(r"^\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?$", re.IGNORECASE)
_NUMERIC_CABINET_PATTERN = re.compile(r"(?<!\()\b(\d{1,5})\b(?!\))")
_MATERIAL_HEADER_PATTERN = re.compile(r"^\d+/\d+\s+.+$")
_SPLIT_WIDTH_PATTERN = re.compile(r"^\d+(?:\.\d+)?\s*x$", re.IGNORECASE)
_COMBINED_WIDTH_LENGTH_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)$", re.IGNORECASE)


def _normalize_slashes(path: str) -> str:
    return path.replace("/", "\\")


def _is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _doc_type_from_filename(filename: str) -> Optional[str]:
    for doc_type, pattern in _DOC_DEFINITIONS:
        if pattern.search(filename):
            return doc_type
    return None


def _is_hardwoods_doc_name(filename: str) -> bool:
    return _doc_type_from_filename(filename) is not None


def _extract_numeric_cabinets(raw_text: str) -> List[str]:
    found = {match.group(1) for match in _NUMERIC_CABINET_PATTERN.finditer(raw_text or "")}
    return sorted(found, key=lambda v: int(v))


def _extract_first_numeric_value(text: str) -> Optional[str]:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return match.group(0) if match else None


def _make_row_id(doc_type: str, page: int, row_ordinal: int, normalized_row_text: str) -> str:
    digest = hashlib.sha1(normalized_row_text.encode("utf-8")).hexdigest()[:16]
    return f"{doc_type}:{page}:{row_ordinal}:{digest}"


def _looks_like_split_width_line(line: str) -> bool:
    # e.g. "4.75 x", "33.19 x"
    return bool(_SPLIT_WIDTH_PATTERN.match(line))


def _parse_combined_width_length(line: str) -> Optional[Tuple[str, str]]:
    match = _COMBINED_WIDTH_LENGTH_PATTERN.match(line.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _looks_like_material_header(line: str) -> bool:
    clean = (line or "").strip()
    if not clean:
        return False
    if _is_metadata_or_header_line(clean):
        return False
    if not _MATERIAL_HEADER_PATTERN.match(clean):
        return False
    # Prevent very long title-like lines from being treated as material headers.
    if len(clean) > 64:
        return False
    return True


def _looks_like_row_start(lines: List[str], index: int) -> bool:
    if index + 3 >= len(lines):
        return False
    qty_line = lines[index]
    description = lines[index + 1]
    dim_line = lines[index + 2]
    next_line = lines[index + 3]
    if not qty_line.isdigit() or _is_metadata_or_header_line(description):
        return False

    # Layout A: width+length in one line, cabinets on next line.
    if _parse_combined_width_length(dim_line) and re.search(r"\d", next_line):
        return True

    # Layout B: width line then length line then cabinets.
    if index + 4 >= len(lines):
        return False
    length_line = lines[index + 3]
    cabinets_line = lines[index + 4]
    return (
        _looks_like_split_width_line(dim_line)
        and bool(_NUMERIC_VALUE_PATTERN.match(length_line))
        and bool(re.search(r"\d", cabinets_line))
    )


def _is_metadata_or_header_line(line: str) -> bool:
    upper = line.upper()
    if not line:
        return True
    return (
        upper.startswith("CREATED BY CABINET VISION")
        or upper.startswith("PAGE ")
        or upper.startswith("PRINT DATE:")
        or " - " in line and re.search(r"\(\d+\)", line)
        or upper in {"QTY", "DESCRIPTION", "WIDTH", "LENGTH", "X", "UN", "SHE", "BD", "FT", "TYPE", "HINGE"}
        or upper.startswith("CABINET")
        or upper.startswith("CAB (QTY)")
        or upper.startswith("TOTALS")
        or upper.startswith("RIPS")
        or upper.startswith("WIDTH X HEIGHT")
        or upper.startswith("OUTSIDE EDGE PROFILE:")
        or upper.startswith("PANEL DETAIL:")
        or upper.startswith("INSIDE EDGE PROFILE:")
        or upper.startswith("ROUTE PATTERN:")
        or upper.startswith("DOOR CUT LIST")
        or upper.startswith("FACE FRAME CUT LIST")
        or upper.startswith("NAILER CUT LIST")
        or upper.startswith("DOOR LIST")
        or upper.startswith("JOB:")
    )


def _parse_standard_cutlist_page(lines: List[str], doc_type: str, page: int) -> List[Dict]:
    rows: List[Dict] = []
    i = 0
    row_ordinal = 0

    while i + 4 < len(lines):
        if not _looks_like_row_start(lines, i):
            i += 1
            continue

        qty_line = lines[i]
        description = lines[i + 1]
        dim_line = lines[i + 2]
        maybe_combined = _parse_combined_width_length(dim_line)
        if maybe_combined:
            width_value, length_value = maybe_combined
            cabinets_line = lines[i + 3]
            j = i + 4
            base_advance = 4
        else:
            width_line = dim_line
            length_line = lines[i + 3]
            width_value = _extract_first_numeric_value(width_line) or ""
            length_value = _extract_first_numeric_value(length_line) or ""
            cabinets_line = lines[i + 4]
            j = i + 5
            base_advance = 5

        qty = int(qty_line)
        if qty <= 0:
            i += 1
            continue

        raw_cabinet_lines = [cabinets_line.strip()]
        while j < len(lines):
            line = lines[j].strip()
            if not line:
                j += 1
                continue
            if _looks_like_row_start(lines, j):
                break
            if _is_metadata_or_header_line(line) or _looks_like_material_header(line):
                break
            if re.match(r"^[\d\(\),\-\s]+$", line):
                raw_cabinet_lines.append(line)
                j += 1
                continue
            break

        raw_cabinet_text = " ".join(raw_cabinet_lines).strip()
        cabinets = _extract_numeric_cabinets(raw_cabinet_text)

        normalized_row = _normalize_text(
            f"{qty}|{description}|{width_value}|{length_value}|{raw_cabinet_text}"
        )
        row = {
            "rowId": _make_row_id(doc_type, page, row_ordinal, normalized_row),
            "page": page,
            "rowOrdinal": row_ordinal,
            "qty": qty,
            "description": description.strip(),
            "width": width_value,
            "length": length_value,
            "cabinets": cabinets,
            "rawCabinetText": raw_cabinet_text,
        }
        rows.append(row)
        row_ordinal += 1
        i = max(j, i + base_advance)

    return rows


def _parse_door_list_page(
    lines: List[str], page: int, active_description: str = "Door"
) -> Tuple[List[Dict], str]:
    rows: List[Dict] = []
    i = 0
    row_ordinal = 0
    last_description_candidate: Optional[str] = None

    while i < len(lines):
        line = lines[i]

        if not _is_metadata_or_header_line(line) and not line.isdigit():
            if (
                last_description_candidate
                and last_description_candidate.count("(") > last_description_candidate.count(")")
            ):
                last_description_candidate = last_description_candidate + " " + line
            else:
                last_description_candidate = line

        if line.upper() == "QTY" and last_description_candidate:
            active_description = last_description_candidate
            i += 1
            continue

        if i + 4 < len(lines) and line.isdigit():
            qty = int(line)
            size_line = lines[i + 1]
            type_line = lines[i + 2]
            hinge_line = lines[i + 3]
            cabinets_line = lines[i + 4]

            if qty > 0 and _DOOR_LIST_SIZE_PATTERN.match(size_line) and re.search(r"\d", cabinets_line):
                size_parts = [part.strip() for part in size_line.split("x", 1)]
                width_value = size_parts[0] if len(size_parts) > 0 else ""
                length_value = size_parts[1] if len(size_parts) > 1 else ""

                # Light guards so header text does not become a fake row.
                if len(type_line) <= 6 and len(hinge_line) <= 6:
                    raw_cabinet_text = cabinets_line.strip()
                    cabinets = _extract_numeric_cabinets(raw_cabinet_text)
                    normalized_row = _normalize_text(
                        f"{qty}|{active_description}|{width_value}|{length_value}|{raw_cabinet_text}"
                    )
                    row = {
                        "rowId": _make_row_id(DOC_TYPE_DOOR_LIST, page, row_ordinal, normalized_row),
                        "page": page,
                        "rowOrdinal": row_ordinal,
                        "qty": qty,
                        "description": active_description.strip() or "Door",
                        "width": width_value,
                        "length": length_value,
                        "cabinets": cabinets,
                        "rawCabinetText": raw_cabinet_text,
                    }
                    rows.append(row)
                    row_ordinal += 1
                    i += 5
                    continue

        i += 1

    return rows, active_description


def _is_totals_terminator_line(line: str) -> bool:
    upper = line.upper()
    if upper in {
        "QTY",
        "DESCRIPTION",
        "WIDTH",
        "LENGTH",
        "X",
        "UN",
        "SHE",
        "BD",
        "FT",
        "TYPE",
        "HINGE",
    }:
        return True
    if upper.startswith("CABINET") or upper.startswith("CAB (QTY)"):
        return True
    if upper.startswith("OUTSIDE EDGE PROFILE:") or upper.startswith("PANEL DETAIL:"):
        return True
    if upper.startswith("INSIDE EDGE PROFILE:") or upper.startswith("ROUTE PATTERN:"):
        return True
    if upper.startswith("DOOR CUT LIST") or upper.startswith("FACE FRAME CUT LIST"):
        return True
    if upper.startswith("NAILER CUT LIST") or upper.startswith("DOOR LIST"):
        return True
    if upper.startswith("CREATED BY CABINET VISION") or upper.startswith("PAGE "):
        return True
    if upper.startswith("PRINT DATE:") or upper.startswith("JOB:"):
        return True
    return False


def _parse_totals_blocks(lines: List[str], page: int) -> List[Dict]:
    blocks: List[Dict] = []
    i = 0
    while i < len(lines):
        if lines[i].strip().upper() != "TOTALS":
            i += 1
            continue

        j = i + 1
        current_label: Optional[str] = None
        width_values: List[str] = []
        length_values: List[str] = []
        rips_values: List[str] = []

        while j < len(lines):
            line = lines[j].strip()
            upper = line.upper()
            if not line:
                j += 1
                continue

            if upper in {"WIDTH", "LENGTH", "RIPS"}:
                current_label = upper.lower()
                j += 1
                continue

            if _is_totals_terminator_line(line):
                break

            if _NUMERIC_VALUE_PATTERN.match(line):
                if current_label == "length":
                    length_values.append(line)
                elif current_label == "rips":
                    rips_values.append(line)
                else:
                    # Default bucket for Totals rows that omit the explicit label ordering.
                    width_values.append(line)
                j += 1
                continue

            # Section/material headers usually mark the end of totals.
            if re.search(r"[A-Za-z]", line):
                break

            j += 1

        if width_values or length_values or rips_values:
            blocks.append(
                {
                    "page": page,
                    "widthValues": width_values,
                    "lengthValues": length_values,
                    "ripsValues": rips_values,
                }
            )

        i = max(j, i + 1)

    return blocks


def _parse_totals_blocks_from_page(page_obj, page_number: int) -> List[Dict]:
    """
    Prefer coordinate-aware totals parsing so values can be assigned to Width/Length/Rips columns.
    """
    words_raw = page_obj.get_text("words") or []
    words = []
    for w in words_raw:
        # PyMuPDF words tuple: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
        if len(w) < 5:
            continue
        text = str(w[4]).strip()
        if not text:
            continue
        words.append(
            {
                "x0": float(w[0]),
                "y0": float(w[1]),
                "x1": float(w[2]),
                "y1": float(w[3]),
                "text": text,
                "upper": text.upper(),
            }
        )

    totals_words = [w for w in words if w["upper"] == "TOTALS"]
    if not totals_words:
        return []

    totals_words.sort(key=lambda w: (w["y0"], w["x0"]))
    out: List[Dict] = []

    for idx, totals_word in enumerate(totals_words):
        next_totals_y = totals_words[idx + 1]["y0"] if idx + 1 < len(totals_words) else None
        totals_y = totals_word["y0"]

        labels = {}
        for w in words:
            if abs(w["y0"] - totals_y) <= 20.0 and w["upper"] in {"WIDTH", "LENGTH", "RIPS"}:
                labels[w["upper"].lower()] = (w["x0"] + w["x1"]) / 2.0

        if not labels:
            continue

        region_words = []
        for w in words:
            if w["y0"] <= totals_y + 3.0:
                continue
            if next_totals_y is not None and w["y0"] >= next_totals_y - 1.0:
                continue
            region_words.append(w)

        rows_by_y: Dict[float, List[Dict]] = {}
        for w in region_words:
            key = round(w["y0"], 1)
            rows_by_y.setdefault(key, []).append(w)

        width_values: List[str] = []
        length_values: List[str] = []
        rips_values: List[str] = []
        for y in sorted(rows_by_y.keys()):
            row_words = sorted(rows_by_y[y], key=lambda item: item["x0"])
            has_alpha = any(re.search(r"[A-Za-z]", rw["text"]) for rw in row_words)
            if has_alpha:
                continue
            numeric_words = [rw for rw in row_words if _NUMERIC_VALUE_PATTERN.match(rw["text"])]
            if len(numeric_words) < 2:
                continue
            for w in numeric_words:
                cx = (w["x0"] + w["x1"]) / 2.0
                nearest = min(labels.items(), key=lambda item: abs(item[1] - cx))[0]
                if nearest == "width":
                    width_values.append(w["text"])
                elif nearest == "length":
                    length_values.append(w["text"])
                else:
                    rips_values.append(w["text"])

        if width_values or length_values or rips_values:
            out.append(
                {
                    "page": page_number,
                    "blockY": totals_y,
                    "labelCenters": dict(labels),
                    "widthValues": width_values,
                    "lengthValues": length_values,
                    "ripsValues": rips_values,
                }
            )

    return out


def _parse_totals_continuation_from_page(page_obj, label_centers: Dict[str, float]) -> Optional[Dict]:
    words_raw = page_obj.get_text("words") or []
    words = []
    for w in words_raw:
        if len(w) < 5:
            continue
        text = str(w[4]).strip()
        if not text:
            continue
        words.append(
            {
                "x0": float(w[0]),
                "y0": float(w[1]),
                "x1": float(w[2]),
                "text": text,
            }
        )
    if not words:
        return None

    rows_by_y: Dict[float, List[Dict]] = {}
    for w in words:
        key = round(w["y0"], 1)
        rows_by_y.setdefault(key, []).append(w)

    width_values: List[str] = []
    length_values: List[str] = []
    rips_values: List[str] = []
    started = False

    for y in sorted(rows_by_y.keys()):
        row_words = sorted(rows_by_y[y], key=lambda item: item["x0"])
        row_text = " ".join(rw["text"] for rw in row_words).strip()
        if not row_text:
            continue
        if _is_metadata_or_header_line(row_text):
            continue

        has_alpha = any(re.search(r"[A-Za-z]", rw["text"]) for rw in row_words)
        numeric_words = [rw for rw in row_words if _NUMERIC_VALUE_PATTERN.match(rw["text"])]

        if not started:
            # Continuation rows are numeric-only row lines near top of spillover pages.
            if has_alpha or len(numeric_words) < 2:
                continue
            started = True
        else:
            if has_alpha or not numeric_words:
                break

        for w in numeric_words:
            cx = (w["x0"] + w["x1"]) / 2.0
            nearest = min(label_centers.items(), key=lambda item: abs(item[1] - cx))[0]
            if nearest == "width":
                width_values.append(w["text"])
            elif nearest == "length":
                length_values.append(w["text"])
            elif nearest == "rips":
                rips_values.append(w["text"])

    if width_values or length_values or rips_values:
        return {
            "widthValues": width_values,
            "lengthValues": length_values,
            "ripsValues": rips_values,
        }
    return None


def _extract_material_markers(page_obj) -> List[Tuple[float, str]]:
    try:
        text_dict = page_obj.get_text("dict") or {}
    except Exception:
        return []
    if not isinstance(text_dict, dict):
        return []
    markers: List[Tuple[float, str]] = []
    seen = set()
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            if not _looks_like_material_header(text):
                continue
            y = float(line.get("bbox", [0.0, 0.0, 0.0, 0.0])[1])
            key = (round(y, 1), text)
            if key in seen:
                continue
            seen.add(key)
            markers.append((y, text))
    markers.sort(key=lambda item: item[0])
    return markers


def _assign_material_to_totals(
    totals: List[Dict], material_markers: List[Tuple[float, str]], prior_material: Optional[str]
) -> Tuple[List[Dict], Optional[str]]:
    if not totals and not material_markers:
        return totals, prior_material

    running_material = prior_material
    marker_index = 0
    sorted_totals = sorted(totals, key=lambda t: float(t.get("blockY", 0.0)))
    markers_by_order = [m[1] for m in material_markers]
    ordered_fallback_index = 0
    for block in sorted_totals:
        if "blockY" in block:
            block_y = float(block.get("blockY", 0.0))
            while marker_index < len(material_markers) and material_markers[marker_index][0] <= block_y + 0.5:
                running_material = material_markers[marker_index][1]
                marker_index += 1
        elif markers_by_order:
            # Fallback parser has no coordinates; match in scan order.
            next_idx = min(ordered_fallback_index, len(markers_by_order) - 1)
            running_material = markers_by_order[next_idx]
            ordered_fallback_index += 1
        if running_material:
            block["material"] = running_material
        block.pop("blockY", None)

    # Carry forward the most recent in-page material when there is no totals on a continuation page.
    if material_markers:
        running_material = material_markers[-1][1]
    return sorted_totals, running_material


def _parse_document_rows(doc_type: str, pdf_path: str) -> Tuple[int, List[Dict], List[Dict]]:
    rows: List[Dict] = []
    totals: List[Dict] = []
    doc = fitz.open(pdf_path)
    active_material: Optional[str] = None
    open_totals_block: Optional[Dict] = None
    active_door_description = "Door"
    try:
        for page_index in range(doc.page_count):
            page_number = page_index + 1
            page_obj = doc[page_index]
            page_text = page_obj.get_text("text")
            lines = [line.strip() for line in page_text.splitlines() if line.strip()]
            material_markers = _extract_material_markers(page_obj)
            if doc_type in {DOC_TYPE_FACE_FRAME, DOC_TYPE_DOOR_CUT, DOC_TYPE_NAILER}:
                page_totals = _parse_totals_blocks_from_page(page_obj, page_number)
                if not page_totals:
                    # fallback for odd PDFs where words extraction is sparse
                    page_totals = _parse_totals_blocks(lines, page_number)
                if page_totals:
                    page_totals, active_material = _assign_material_to_totals(
                        page_totals, material_markers, active_material
                    )
                    for block in page_totals:
                        block["sourcePages"] = [page_number]
                    totals.extend(page_totals)
                    open_totals_block = page_totals[-1]
                else:
                    if material_markers:
                        active_material = material_markers[-1][1]
                    if open_totals_block and isinstance(open_totals_block.get("labelCenters"), dict):
                        continuation = _parse_totals_continuation_from_page(
                            page_obj, open_totals_block["labelCenters"]
                        )
                        if continuation:
                            open_totals_block["widthValues"].extend(continuation["widthValues"])
                            open_totals_block["lengthValues"].extend(continuation["lengthValues"])
                            open_totals_block["ripsValues"].extend(continuation["ripsValues"])
                            source_pages = open_totals_block.setdefault("sourcePages", [open_totals_block["page"]])
                            if page_number not in source_pages:
                                source_pages.append(page_number)
                        elif material_markers:
                            open_totals_block = None
            elif material_markers:
                active_material = material_markers[-1][1]
            if doc_type == DOC_TYPE_DOOR_LIST:
                page_rows, active_door_description = _parse_door_list_page(
                    lines, page_number, active_door_description
                )
            else:
                page_rows = _parse_standard_cutlist_page(lines, doc_type, page_number)
            rows.extend(page_rows)
        for block in totals:
            block.pop("blockY", None)
            block.pop("labelCenters", None)
            if not block.get("sourcePages"):
                block["sourcePages"] = [block["page"]]
        return doc.page_count, rows, totals
    finally:
        doc.close()


def _collect_pdf_candidates(folder_path: str) -> List[str]:
    if not os.path.isdir(folder_path):
        return []
    candidates: List[str] = []
    try:
        with os.scandir(folder_path) as entries:
            for entry in entries:
                if entry.is_file() and _is_pdf(entry.name):
                    candidates.append(entry.path)
    except OSError:
        return []
    return sorted(candidates)


def _find_hardwoods_docs(job_folder_path: str) -> Dict[str, Tuple[str, str]]:
    docs: Dict[str, Tuple[str, str]] = {}
    light_candidates = _collect_pdf_candidates(job_folder_path)

    dark_mode_dir = os.path.join(job_folder_path, "DARK MODE")
    dark_candidates = _collect_pdf_candidates(dark_mode_dir)

    for path in light_candidates:
        filename = os.path.basename(path)
        doc_type = _doc_type_from_filename(filename)
        if doc_type and doc_type not in docs:
            docs[doc_type] = (filename, path)

    for path in dark_candidates:
        filename = os.path.basename(path)
        doc_type = _doc_type_from_filename(filename)
        if doc_type and doc_type not in docs:
            docs[doc_type] = (filename, path)

    return docs


def _write_index(job_folder_path: str, payload: Dict) -> str:
    metadata_dir = os.path.join(job_folder_path, ".metadata", HARDWOODS_METADATA_DIR)
    os.makedirs(metadata_dir, exist_ok=True)
    out_path = os.path.join(metadata_dir, HARDWOODS_INDEX_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return out_path


def _remove_index_if_exists(job_folder_path: str) -> bool:
    out_path = os.path.join(job_folder_path, ".metadata", HARDWOODS_METADATA_DIR, HARDWOODS_INDEX_FILENAME)
    if os.path.exists(out_path):
        os.remove(out_path)
        return True
    return False


def build_hardwoods_cutlist_index_for_job(job_folder_path: str) -> bool:
    """
    Build or refresh hardwoods cutlist index for a job folder.
    Returns True when an index file was written or removed.
    """
    if not os.path.isdir(job_folder_path):
        return False

    docs = _find_hardwoods_docs(job_folder_path)
    if not docs:
        return _remove_index_if_exists(job_folder_path)

    serialized_docs: List[Dict] = []
    for doc_type, (filename, path) in sorted(docs.items(), key=lambda item: item[0]):
        try:
            page_count, rows, totals = _parse_document_rows(doc_type, path)
        except Exception as e:
            main_logger.error("Hardwoods parse failed: %s (%s)", path, e, exc_info=True)
            continue
        serialized_docs.append(
            {
                "docType": doc_type,
                "pdfFilename": filename,
                "pageCount": page_count,
                "rows": rows,
                "totals": totals,
            }
        )

    if not serialized_docs:
        return _remove_index_if_exists(job_folder_path)

    payload = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "documents": serialized_docs,
    }
    out_path = _write_index(job_folder_path, payload)
    main_logger.info(
        "Hardwoods cutlist index updated: job=%s docs=%s output=%s",
        os.path.basename(job_folder_path),
        len(serialized_docs),
        out_path,
    )
    return True


def build_hardwoods_cutlist_index_for_pdf_event(pdf_path: str) -> bool:
    """
    Rebuild hardwoods cutlist index for a specific modified/created/deleted PDF.
    """
    normalized = _normalize_slashes(pdf_path)
    filename = os.path.basename(normalized)
    if not _is_hardwoods_doc_name(filename):
        return False

    folder = os.path.dirname(normalized)
    if os.path.basename(folder).upper() == "DARK MODE":
        job_folder = os.path.dirname(folder)
    else:
        job_folder = folder

    if os.path.basename(job_folder).upper() == "CNC":
        return False

    return build_hardwoods_cutlist_index_for_job(job_folder)
