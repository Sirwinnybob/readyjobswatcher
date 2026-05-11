"""
Hardwoods cutlist index generation for job-root cut list PDFs.
New-template-only parser (pipe-delimited table layout).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from decimal import Decimal
from decimal import InvalidOperation
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from .tracker_action_stream import load_hardwoods_tracker_actions

main_logger = logging.getLogger("main")

HARDWOODS_METADATA_DIR = "hardwoods"
HARDWOODS_INDEX_FILENAME = "cutlist_index.json"
HARDWOODS_REVISION_FILENAME = "cutlist_revisions.json"

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
_NUMERIC_CABINET_PATTERN = re.compile(r"(?<!\()\b(\d{1,5})\b(?!\))")
_ROW_QTY_PATTERN = re.compile(r"^\d+$")
_HEADER_LENGTH_PATTERN = re.compile(r"^(LENGTH|HEIGHT)$", re.IGNORECASE)
_MATERIAL_LINE_PATTERN = re.compile(r"^MATERIAL\s*:\s*(.+)$", re.IGNORECASE)
_DOOR_TYPE_LINE_PATTERN = re.compile(r"^DOOR\s+TYPE\s*:\s*(.+)$", re.IGNORECASE)
_CABINET_SKIP_MARKER = "|@cab:"
_TRACKER_SET_DONE_COUNT = "set_done_count"
_TRACKER_SET_BAD_COUNT = "set_bad_count"


class TemplateMismatchError(Exception):
    pass


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


def _make_row_id(doc_type: str, page: int, row_ordinal: int, normalized_row_text: str) -> str:
    digest = hashlib.sha1(normalized_row_text.encode("utf-8")).hexdigest()[:16]
    return f"{doc_type}:{page}:{row_ordinal}:{digest}"


def _clean_marker_value(value: str) -> str:
    cleaned = (value or "").strip()
    cleaned = cleaned.strip("| ")
    quoted = re.search(r"'([^']+)'", cleaned)
    if quoted:
        cleaned = quoted.group(1)
    elif cleaned.startswith("'") and cleaned.endswith("'") and len(cleaned) >= 2:
        cleaned = cleaned[1:-1]
    cleaned = re.sub(r"\b(UN|SHE|BD|FT)\b$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned.strip()


def _collect_words(page_obj) -> List[Dict]:
    words_raw = page_obj.get_text("words") or []
    out: List[Dict] = []
    for w in words_raw:
        if len(w) < 5:
            continue
        text = str(w[4]).strip()
        if not text:
            continue
        out.append(
            {
                "x0": float(w[0]),
                "y0": float(w[1]),
                "x1": float(w[2]),
                "y1": float(w[3]),
                "text": text,
                "upper": text.upper(),
            }
        )
    out.sort(key=lambda item: (round(item["y0"], 1), item["x0"]))
    return out


def _group_words_by_y(words: List[Dict]) -> List[Tuple[float, List[Dict]]]:
    rows: List[Tuple[float, List[Dict]]] = []
    for word in words:
        y = word["y0"]
        if not rows:
            rows.append((y, [word]))
            continue
        last_y, last_words = rows[-1]
        if abs(y - last_y) <= 1.2:
            last_words.append(word)
            rows[-1] = ((last_y + y) / 2.0, last_words)
        else:
            rows.append((y, [word]))
    return [(y, sorted(group, key=lambda item: item["x0"])) for y, group in rows]


def _line_text(words: List[Dict]) -> str:
    return " ".join(w["text"] for w in words).strip()


def _extract_section_markers(doc_type: str, rows_by_y: List[Tuple[float, List[Dict]]]) -> List[Tuple[float, str]]:
    markers: List[Tuple[float, str]] = []
    for y, row_words in rows_by_y:
        line = _line_text(row_words)
        if not line:
            continue
        if doc_type == DOC_TYPE_DOOR_LIST:
            m = _DOOR_TYPE_LINE_PATTERN.match(line)
        else:
            m = _MATERIAL_LINE_PATTERN.match(line)
        if not m:
            continue
        marker = _clean_marker_value(m.group(1))
        if marker:
            markers.append((y, marker))
    return markers


def _find_header_rows(doc_type: str, rows_by_y: List[Tuple[float, List[Dict]]]) -> List[Dict]:
    found: List[Dict] = []
    for y, row_words in rows_by_y:
        uppers = [w["upper"] for w in row_words]
        pipes = [w for w in row_words if w["text"] == "|"]
        if len(pipes) < 4:
            continue

        has_qty = "QTY" in uppers
        has_width = "WIDTH" in uppers
        has_lengthish = any(_HEADER_LENGTH_PATTERN.match(u) for u in uppers)
        has_cab = "CABINET" in uppers or "CAB" in uppers

        if doc_type == DOC_TYPE_DOOR_LIST:
            has_type = "TYPE" in uppers
            has_hinge = "HINGE" in uppers
            if not (has_qty and has_width and has_lengthish and has_cab and has_type and has_hinge):
                continue
        else:
            has_description = "DESCRIPTION" in uppers
            if not (has_qty and has_description and has_width and has_lengthish and has_cab):
                continue

        centers: Dict[str, float] = {}
        for w in row_words:
            u = w["upper"]
            cx = (w["x0"] + w["x1"]) / 2.0
            if u == "QTY":
                centers["qty"] = cx
            elif u == "DESCRIPTION":
                centers["description"] = cx
            elif u == "WIDTH":
                centers["width"] = cx
            elif _HEADER_LENGTH_PATTERN.match(u):
                centers["length"] = cx
            elif u in {"CABINET", "CAB"}:
                centers["cabinet"] = cx
            elif u == "TYPE":
                centers["type"] = cx
            elif u == "HINGE":
                centers["hinge"] = cx

        required = ["qty", "width", "length", "cabinet"]
        if doc_type != DOC_TYPE_DOOR_LIST:
            required.append("description")
        if any(k not in centers for k in required):
            continue

        found.append({"y": y, "centers": centers})
    return found


def _find_header_row(doc_type: str, rows_by_y: List[Tuple[float, List[Dict]]]) -> Optional[Dict]:
    rows = _find_header_rows(doc_type, rows_by_y)
    return rows[0] if rows else None


def _column_key_for_word(word: Dict, centers: Dict[str, float]) -> str:
    cx = (word["x0"] + word["x1"]) / 2.0
    return min(centers.items(), key=lambda item: abs(item[1] - cx))[0]


def _looks_like_cabinet_continuation(columns: Dict[str, List[str]], doc_type: str) -> bool:
    qty_tokens = columns.get("qty", [])
    desc_tokens = columns.get("description", [])
    width_tokens = columns.get("width", [])
    length_tokens = columns.get("length", [])
    type_tokens = columns.get("type", [])
    hinge_tokens = columns.get("hinge", [])
    cab_tokens = columns.get("cabinet", [])
    if qty_tokens or desc_tokens or width_tokens or length_tokens:
        return False
    if doc_type == DOC_TYPE_DOOR_LIST and (type_tokens or hinge_tokens):
        return False
    joined = " ".join(cab_tokens)
    return bool(cab_tokens and re.search(r"\d", joined))


def _parse_row_values(
    doc_type: str,
    columns: Dict[str, List[str]],
    active_material: str,
    page_number: int,
    row_ordinal: int,
) -> Optional[Dict]:
    qty_tokens = columns.get("qty", [])
    if not qty_tokens:
        return None
    qty_text = qty_tokens[0].strip()
    if not _ROW_QTY_PATTERN.match(qty_text):
        return None
    qty = int(qty_text)
    if qty <= 0:
        return None

    width_tokens = [t for t in columns.get("width", []) if _NUMERIC_VALUE_PATTERN.match(t)]
    length_tokens = [t for t in columns.get("length", []) if _NUMERIC_VALUE_PATTERN.match(t)]
    if not width_tokens or not length_tokens:
        return None

    raw_cabinet_text = " ".join(columns.get("cabinet", [])).strip(" |")
    if not re.search(r"\d", raw_cabinet_text):
        return None

    if doc_type == DOC_TYPE_DOOR_LIST:
        description = active_material or "Door"
    else:
        description = " ".join(columns.get("description", [])).strip()
        if not description:
            return None

    width_value = width_tokens[0]
    length_value = length_tokens[0]
    cabinets = _extract_numeric_cabinets(raw_cabinet_text)

    normalized_row = _normalize_text(
        f"{qty}|{description}|{width_value}|{length_value}|{raw_cabinet_text}|{active_material}"
    )
    return {
        "rowId": _make_row_id(doc_type, page_number, row_ordinal, normalized_row),
        "page": page_number,
        "rowOrdinal": row_ordinal,
        "qty": qty,
        "description": description,
        "width": width_value,
        "length": length_value,
        "cabinets": cabinets,
        "rawCabinetText": raw_cabinet_text,
        "material": active_material,
    }


def _parse_rows_from_page(
    doc_type: str,
    rows_by_y: List[Tuple[float, List[Dict]]],
    header_info: Optional[Dict],
    page_number: int,
    starting_row_ordinal: int,
    prior_material: str,
    material_markers: List[Tuple[float, str]],
    stop_before_y: Optional[float] = None,
) -> Tuple[List[Dict], int]:
    if not header_info:
        return [], starting_row_ordinal

    header_y = float(header_info["y"])
    centers = dict(header_info["centers"])
    out: List[Dict] = []
    row_ordinal = starting_row_ordinal

    parsed_rows_by_y = []
    for y, row_words in rows_by_y:
        if y <= header_y + 2.0:
            continue
        if stop_before_y is not None and y >= stop_before_y - 0.5:
            break
        uppers = [w["upper"] for w in row_words]
        if "TOTALS" in uppers:
            break

        columns: Dict[str, List[str]] = {}
        for word in row_words:
            if word["text"] == "|":
                continue
            key = _column_key_for_word(word, centers)
            columns.setdefault(key, []).append(word["text"])

        parsed_rows_by_y.append((y, columns))

    idx = 0
    running_material = prior_material
    marker_index = 0
    while idx < len(parsed_rows_by_y):
        y, columns = parsed_rows_by_y[idx]
        while marker_index < len(material_markers) and material_markers[marker_index][0] <= y + 0.5:
            running_material = material_markers[marker_index][1]
            marker_index += 1

        row = _parse_row_values(doc_type, columns, running_material, page_number, row_ordinal)
        if row is None:
            idx += 1
            continue

        j = idx + 1
        while j < len(parsed_rows_by_y):
            next_y, next_columns = parsed_rows_by_y[j]
            preview_material = running_material
            preview_marker_index = marker_index
            while preview_marker_index < len(material_markers) and material_markers[preview_marker_index][0] <= next_y + 0.5:
                preview_material = material_markers[preview_marker_index][1]
                preview_marker_index += 1

            if _parse_row_values(doc_type, next_columns, preview_material, page_number, row_ordinal + 1):
                break
            if _looks_like_cabinet_continuation(next_columns, doc_type):
                tail = " ".join(next_columns.get("cabinet", [])).strip()
                if tail:
                    row["rawCabinetText"] = f"{row['rawCabinetText']} {tail}".strip()
                    row["cabinets"] = _extract_numeric_cabinets(row["rawCabinetText"])
                j += 1
                continue
            break

        out.append(row)
        row_ordinal += 1
        idx = j

    return out, row_ordinal


def _is_totals_terminator_text(text: str) -> bool:
    upper = text.upper().strip()
    if not upper:
        return True
    if upper in {
        "QTY",
        "DESCRIPTION",
        "WIDTH",
        "LENGTH",
        "HEIGHT",
        "TYPE",
        "HINGE",
        "CABINET (QTY)",
        "CAB (QTY)",
    }:
        return True
    return (
        upper.startswith("MATERIAL:")
        or upper.startswith("DOOR TYPE:")
        or upper.startswith("OUTSIDE EDGE PROFILE:")
        or upper.startswith("INSIDE EDGE PROFILE:")
        or upper.startswith("ROUTE PATTERN:")
        or upper.startswith("PANEL DETAIL:")
    )


def _parse_totals_blocks_from_words(rows_by_y: List[Tuple[float, List[Dict]]], page_number: int) -> List[Dict]:
    blocks: List[Dict] = []
    idx = 0
    while idx < len(rows_by_y):
        y, row_words = rows_by_y[idx]
        row_text = _line_text(row_words)
        if "TOTALS" not in row_text.upper().split():
            idx += 1
            continue

        label_centers: Dict[str, float] = {}
        for look_ahead in range(idx, min(idx + 4, len(rows_by_y))):
            _, label_words = rows_by_y[look_ahead]
            for word in label_words:
                u = word["upper"]
                if u in {"WIDTH", "LENGTH", "HEIGHT", "RIPS"}:
                    key = "length" if u == "HEIGHT" else u.lower()
                    label_centers[key] = (word["x0"] + word["x1"]) / 2.0
            if {"width", "length"}.issubset(label_centers.keys()):
                break

        if "width" not in label_centers or "length" not in label_centers:
            idx += 1
            continue

        width_values: List[str] = []
        length_values: List[str] = []
        rips_values: List[str] = []

        j = idx + 1
        started = False
        while j < len(rows_by_y):
            _, value_words = rows_by_y[j]
            text = _line_text(value_words)
            if not text:
                j += 1
                continue
            if _is_totals_terminator_text(text):
                if started:
                    break
                j += 1
                continue
            if "TOTALS" in text.upper().split():
                break

            numeric_words = [w for w in value_words if _NUMERIC_VALUE_PATTERN.match(w["text"])]
            has_alpha = any(re.search(r"[A-Za-z]", w["text"]) for w in value_words)
            if has_alpha:
                if started:
                    break
                j += 1
                continue
            if len(numeric_words) < 2:
                if started:
                    break
                j += 1
                continue

            started = True
            for word in numeric_words:
                cx = (word["x0"] + word["x1"]) / 2.0
                nearest = min(label_centers.items(), key=lambda item: abs(item[1] - cx))[0]
                if nearest == "width":
                    width_values.append(word["text"])
                elif nearest == "length":
                    length_values.append(word["text"])
                elif nearest == "rips":
                    rips_values.append(word["text"])
            j += 1

        if width_values or length_values or rips_values:
            blocks.append(
                {
                    "page": page_number,
                    "blockY": y,
                    "labelCenters": dict(label_centers),
                    "widthValues": width_values,
                    "lengthValues": length_values,
                    "ripsValues": rips_values,
                    "sourcePages": [page_number],
                }
            )

        idx = max(j, idx + 1)

    return blocks


def _parse_totals_continuation(rows_by_y: List[Tuple[float, List[Dict]]], label_centers: Dict[str, float]) -> Optional[Dict]:
    width_values: List[str] = []
    length_values: List[str] = []
    rips_values: List[str] = []
    started = False

    for _, row_words in rows_by_y:
        text = _line_text(row_words)
        if not text:
            continue
        if _is_totals_terminator_text(text):
            if started:
                break
            continue

        numeric_words = [w for w in row_words if _NUMERIC_VALUE_PATTERN.match(w["text"])]
        has_alpha = any(re.search(r"[A-Za-z]", w["text"]) for w in row_words)

        if has_alpha:
            if started:
                break
            continue

        if len(numeric_words) < 2:
            if started:
                break
            continue

        started = True
        for word in numeric_words:
            cx = (word["x0"] + word["x1"]) / 2.0
            nearest = min(label_centers.items(), key=lambda item: abs(item[1] - cx))[0]
            if nearest == "width":
                width_values.append(word["text"])
            elif nearest == "length":
                length_values.append(word["text"])
            elif nearest == "rips":
                rips_values.append(word["text"])

    if width_values or length_values or rips_values:
        return {
            "widthValues": width_values,
            "lengthValues": length_values,
            "ripsValues": rips_values,
        }
    return None


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _normalize_totals_lengths_for_doc_type(doc_type: str, totals: List[Dict]) -> None:
    # Door Cut List totals are exported in inches; normalize to feet in the index.
    if doc_type != DOC_TYPE_DOOR_CUT:
        return
    divisor = Decimal("12")
    for block in totals:
        raw_values = block.get("lengthValues", [])
        if not isinstance(raw_values, list):
            continue
        normalized_values: List[str] = []
        for raw in raw_values:
            text = str(raw or "").strip()
            if not text:
                normalized_values.append(text)
                continue
            try:
                value = Decimal(text)
            except InvalidOperation:
                normalized_values.append(text)
                continue
            converted = value / divisor
            normalized_values.append(_format_decimal(converted))
        block["lengthValues"] = normalized_values


def _assign_material_to_totals(
    totals: List[Dict], material_markers: List[Tuple[float, str]], prior_material: Optional[str]
) -> Tuple[List[Dict], Optional[str]]:
    running_material = prior_material
    marker_index = 0
    totals_sorted = sorted(totals, key=lambda block: float(block.get("blockY", 0.0)))

    for block in totals_sorted:
        block_y = float(block.get("blockY", 0.0))
        while marker_index < len(material_markers) and material_markers[marker_index][0] <= block_y + 0.5:
            running_material = material_markers[marker_index][1]
            marker_index += 1
        if running_material:
            block["material"] = running_material

    if material_markers:
        running_material = material_markers[-1][1]

    for block in totals_sorted:
        block.pop("blockY", None)

    return totals_sorted, running_material


def _parse_document_rows(doc_type: str, pdf_path: str) -> Tuple[int, List[Dict], List[Dict]]:
    rows: List[Dict] = []
    totals: List[Dict] = []
    template_detected = False
    active_material: Optional[str] = None
    open_totals_block: Optional[Dict] = None
    last_header_info: Optional[Dict] = None

    doc = fitz.open(pdf_path)
    try:
        for page_index in range(doc.page_count):
            page_number = page_index + 1
            page_obj = doc[page_index]
            words = _collect_words(page_obj)
            rows_by_y = _group_words_by_y(words)

            markers = _extract_section_markers(doc_type, rows_by_y)
            if markers and active_material is None:
                active_material = markers[0][1]

            header_rows = _find_header_rows(doc_type, rows_by_y)
            if header_rows:
                template_detected = True
                last_header_info = {"y": header_rows[-1]["y"], "centers": dict(header_rows[-1]["centers"])}

            page_rows: List[Dict] = []
            if header_rows:
                row_ordinal = 0
                for idx, header_info in enumerate(header_rows):
                    table_y = float(header_info["y"])
                    next_header_y: Optional[float] = None
                    if idx + 1 < len(header_rows):
                        next_header_y = float(header_rows[idx + 1]["y"])

                    in_table_markers = []
                    for y, material in markers:
                        if y <= table_y + 0.5:
                            active_material = material
                        elif next_header_y is None or y < next_header_y - 0.5:
                            in_table_markers.append((y, material))

                    parsed_rows, row_ordinal = _parse_rows_from_page(
                        doc_type=doc_type,
                        rows_by_y=rows_by_y,
                        header_info=header_info,
                        page_number=page_number,
                        starting_row_ordinal=row_ordinal,
                        prior_material=active_material or "",
                        material_markers=in_table_markers,
                        stop_before_y=next_header_y,
                    )
                    page_rows.extend(parsed_rows)
            elif template_detected and last_header_info:
                # Spillover/continuation page without repeated header:
                # reuse the previous detected table geometry and parse full page rows.
                synthetic_header = {"y": -1_000_000.0, "centers": dict(last_header_info["centers"])}
                page_rows, _ = _parse_rows_from_page(
                    doc_type=doc_type,
                    rows_by_y=rows_by_y,
                    header_info=synthetic_header,
                    page_number=page_number,
                    starting_row_ordinal=0,
                    prior_material=active_material or "",
                    material_markers=markers,
                )

            rows.extend(page_rows)

            if markers:
                active_material = markers[-1][1]

            page_totals = _parse_totals_blocks_from_words(rows_by_y, page_number)
            if page_totals:
                page_totals, active_material = _assign_material_to_totals(page_totals, markers, active_material)
                totals.extend(page_totals)
                open_totals_block = page_totals[-1]
            else:
                if open_totals_block and isinstance(open_totals_block.get("labelCenters"), dict):
                    continuation = _parse_totals_continuation(rows_by_y, open_totals_block["labelCenters"])
                    if continuation:
                        open_totals_block["widthValues"].extend(continuation["widthValues"])
                        open_totals_block["lengthValues"].extend(continuation["lengthValues"])
                        open_totals_block["ripsValues"].extend(continuation["ripsValues"])
                        source_pages = open_totals_block.setdefault("sourcePages", [open_totals_block["page"]])
                        if page_number not in source_pages:
                            source_pages.append(page_number)
                    elif markers:
                        open_totals_block = None

        if not template_detected:
            raise TemplateMismatchError("new template header/pipe structure not detected")

        _normalize_totals_lengths_for_doc_type(doc_type, totals)

        for block in totals:
            block.pop("labelCenters", None)
            block.pop("blockY", None)
            if not block.get("sourcePages"):
                block["sourcePages"] = [block["page"]]

        # Enforce material on all rows (required for new-template-only contract).
        rows = [row for row in rows if row.get("material")]
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


def _index_path_for_job(job_folder_path: str) -> str:
    return os.path.join(job_folder_path, ".metadata", HARDWOODS_METADATA_DIR, HARDWOODS_INDEX_FILENAME)


def _revision_path_for_job(job_folder_path: str) -> str:
    return os.path.join(job_folder_path, ".metadata", HARDWOODS_METADATA_DIR, HARDWOODS_REVISION_FILENAME)


def _load_existing_index(job_folder_path: str) -> Optional[Dict]:
    out_path = _index_path_for_job(job_folder_path)
    if not os.path.isfile(out_path):
        return None
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        docs = payload.get("documents")
        if not isinstance(docs, list):
            return None
        return payload
    except Exception:
        return None


def _load_existing_revision_state(job_folder_path: str) -> Optional[Dict]:
    out_path = _revision_path_for_job(job_folder_path)
    if not os.path.isfile(out_path):
        return None
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def _write_revision_state(job_folder_path: str, payload: Dict) -> str:
    metadata_dir = os.path.join(job_folder_path, ".metadata", HARDWOODS_METADATA_DIR)
    os.makedirs(metadata_dir, exist_ok=True)
    out_path = _revision_path_for_job(job_folder_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return out_path


def _normalize_match_text(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip()).upper()


def _normalize_dimension_value(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        value = float(text)
    except ValueError:
        return _normalize_match_text(text)
    normalized = f"{value:.6f}".rstrip("0").rstrip(".")
    return normalized if normalized else "0"


def _normalize_description_value(raw: str) -> str:
    return _normalize_match_text(raw)


def _normalize_cabinet_tokens(raw_tokens: List[str]) -> str:
    cleaned = []
    for token in raw_tokens or []:
        text = _normalize_match_text(token)
        if text:
            cleaned.append(text)
    deduped = sorted(set(cleaned))
    return "|".join(deduped)


def _row_revision_match_key(doc_type: str, row: Dict) -> str:
    material = _normalize_match_text(row.get("material", ""))
    description = _normalize_description_value(row.get("description", ""))
    cabinets = _normalize_cabinet_tokens(list(row.get("cabinets", []) or []))
    return f"{doc_type}|{material}|{description}|{cabinets}"


def _row_dimension_tuple(row: Dict) -> Tuple[str, str, str]:
    qty = str(int(row.get("qty", 0) or 0))
    width = _normalize_dimension_value(row.get("width", ""))
    length = _normalize_dimension_value(row.get("length", ""))
    return qty, width, length


def _serialize_revision_row(doc_type: str, row: Dict) -> Dict:
    return {
        "docType": doc_type,
        "rowId": str(row.get("rowId", "") or ""),
        "page": int(row.get("page", 0) or 0),
        "rowOrdinal": int(row.get("rowOrdinal", 0) or 0),
        "qty": int(row.get("qty", 0) or 0),
        "material": str(row.get("material", "") or ""),
        "description": str(row.get("description", "") or ""),
        "width": str(row.get("width", "") or ""),
        "length": str(row.get("length", "") or ""),
        "cabinets": sorted(list(row.get("cabinets", []) or []), key=lambda v: str(v)),
    }


def _extract_rows_by_doc(index_payload: Optional[Dict]) -> Dict[str, List[Dict]]:
    if not isinstance(index_payload, dict):
        return {}
    docs = index_payload.get("documents", [])
    if not isinstance(docs, list):
        return {}
    out: Dict[str, List[Dict]] = {}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        doc_type = str(doc.get("docType", "") or "")
        if not doc_type:
            continue
        rows = [row for row in doc.get("rows", []) if isinstance(row, dict)]
        out[doc_type] = rows
    return out


def _tracker_done_by_row(job_folder_path: str) -> Dict[Tuple[str, str], int]:
    return {
        key: values[0]
        for key, values in _tracker_completion_priority(job_folder_path).items()
    }


def _build_revision_delta(
    previous_index: Optional[Dict],
    next_docs: List[Dict],
) -> Dict:
    old_rows_by_doc = _extract_rows_by_doc(previous_index)
    new_rows_by_doc = _extract_rows_by_doc({"documents": next_docs})
    doc_types = sorted(set(old_rows_by_doc.keys()) | set(new_rows_by_doc.keys()))

    added: List[Dict] = []
    removed: List[Dict] = []
    modified: List[Dict] = []

    for doc_type in doc_types:
        old_rows = old_rows_by_doc.get(doc_type, [])
        new_rows = new_rows_by_doc.get(doc_type, [])

        old_buckets: Dict[str, List[Dict]] = defaultdict(list)
        new_buckets: Dict[str, List[Dict]] = defaultdict(list)
        for row in old_rows:
            old_buckets[_row_revision_match_key(doc_type, row)].append(row)
        for row in new_rows:
            new_buckets[_row_revision_match_key(doc_type, row)].append(row)

        all_keys = sorted(set(old_buckets.keys()) | set(new_buckets.keys()))
        for key in all_keys:
            old_bucket = sorted(old_buckets.get(key, []), key=_row_order_key)
            new_bucket = sorted(new_buckets.get(key, []), key=_row_order_key)
            paired_count = min(len(old_bucket), len(new_bucket))

            for idx in range(paired_count):
                old_row = old_bucket[idx]
                new_row = new_bucket[idx]
                old_dims = _row_dimension_tuple(old_row)
                new_dims = _row_dimension_tuple(new_row)
                if old_dims == new_dims:
                    continue
                changed_fields: List[str] = []
                if old_dims[0] != new_dims[0]:
                    changed_fields.append("qty")
                if old_dims[1] != new_dims[1]:
                    changed_fields.append("width")
                if old_dims[2] != new_dims[2]:
                    changed_fields.append("length")
                modified.append(
                    {
                        "before": _serialize_revision_row(doc_type, old_row),
                        "after": _serialize_revision_row(doc_type, new_row),
                        "changedFields": changed_fields,
                    }
                )

            for old_row in old_bucket[paired_count:]:
                removed.append(_serialize_revision_row(doc_type, old_row))
            for new_row in new_bucket[paired_count:]:
                added.append(_serialize_revision_row(doc_type, new_row))

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
    }


def _upsert_revision_state(job_folder_path: str, previous_index: Optional[Dict], next_docs: List[Dict]) -> Optional[Dict]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state = _load_existing_revision_state(job_folder_path) or {}
    revisions = state.get("revisions", [])
    if not isinstance(revisions, list):
        revisions = []
    row_states = state.get("currentRowStates", [])
    if not isinstance(row_states, list):
        row_states = []

    if previous_index is None or not revisions:
        baseline = {
            "revision": 1,
            "kind": "SNAPSHOT",
            "timestamp": now,
            "added": [],
            "removed": [],
            "modified": [],
        }
        payload = {
            "schemaVersion": 1,
            "updatedAt": now,
            "currentRevision": 1,
            "revisions": [baseline],
            "currentRowStates": [],
        }
        _write_revision_state(job_folder_path, payload)
        return payload

    delta = _build_revision_delta(previous_index, next_docs)
    has_changes = bool(delta["added"] or delta["removed"] or delta["modified"])
    current_revision = int(state.get("currentRevision", 1) or 1)

    new_rows_by_doc = _extract_rows_by_doc({"documents": next_docs})
    next_row_map: Dict[Tuple[str, str], Dict] = {}
    for doc_type, rows in new_rows_by_doc.items():
        for row in rows:
            row_id = str(row.get("rowId", "") or "")
            if row_id:
                next_row_map[(doc_type, row_id)] = row

    done_counts = _tracker_done_by_row(job_folder_path)
    carried: Dict[Tuple[str, str], Dict] = {}
    for item in row_states:
        if not isinstance(item, dict):
            continue
        doc_type = str(item.get("docType", "") or "")
        row_id = str(item.get("rowId", "") or "")
        key = (doc_type, row_id)
        if key not in next_row_map:
            continue
        latest_revision = int(item.get("latestRevision", 0) or 0)
        changed_pending = bool(item.get("changedPendingRecut", False))
        carried[key] = {
            "docType": doc_type,
            "rowId": row_id,
            "latestRevision": latest_revision,
            "changedPendingRecut": changed_pending,
        }

    if has_changes:
        current_revision += 1
        revision_entry = {
            "revision": current_revision,
            "kind": "DIFF",
            "timestamp": now,
            "added": delta["added"],
            "removed": delta["removed"],
            "modified": delta["modified"],
        }
        revisions = list(revisions) + [revision_entry]

        for mod in delta["modified"]:
            before = mod.get("before", {})
            after = mod.get("after", {})
            doc_type = str(after.get("docType", "") or "")
            new_row_id = str(after.get("rowId", "") or "")
            old_row_id = str(before.get("rowId", "") or "")
            if not doc_type or not new_row_id:
                continue
            old_qty = int(before.get("qty", 0) or 0)
            old_done = done_counts.get((doc_type, old_row_id), 0)
            changed_pending = old_qty > 0 and old_done >= old_qty
            carried[(doc_type, new_row_id)] = {
                "docType": doc_type,
                "rowId": new_row_id,
                "latestRevision": current_revision,
                "changedPendingRecut": changed_pending,
            }

        for added in delta["added"]:
            doc_type = str(added.get("docType", "") or "")
            row_id = str(added.get("rowId", "") or "")
            if not doc_type or not row_id:
                continue
            carried[(doc_type, row_id)] = {
                "docType": doc_type,
                "rowId": row_id,
                "latestRevision": current_revision,
                "changedPendingRecut": False,
            }

    # Keep pending-recut state synced with current completion when possible.
    for key, item in list(carried.items()):
        if not item.get("changedPendingRecut", False):
            continue
        row = next_row_map.get(key)
        if row is None:
            continue
        qty = int(row.get("qty", 0) or 0)
        done = done_counts.get(key, 0)
        if qty > 0 and done >= qty:
            item["changedPendingRecut"] = False

    current_row_states = sorted(
        carried.values(),
        key=lambda item: (
            str(item.get("docType", "")),
            str(item.get("rowId", "")),
        ),
    )
    payload = {
        "schemaVersion": 1,
        "updatedAt": now,
        "currentRevision": current_revision,
        "revisions": revisions,
        "currentRowStates": current_row_states,
    }
    _write_revision_state(job_folder_path, payload)
    return payload


def _row_match_key(doc_type: str, row: Dict) -> Optional[str]:
    material = _normalize_match_text(row.get("material", ""))
    length = _normalize_dimension_value(row.get("length", ""))
    width = _normalize_dimension_value(row.get("width", ""))
    if not material or not length or not width:
        return None
    return f"{doc_type}|{material}|{length}|{width}"


def _row_order_key(row: Dict) -> Tuple[int, int, str]:
    page = int(row.get("page", 0) or 0)
    row_ordinal = int(row.get("rowOrdinal", 0) or 0)
    row_id = str(row.get("rowId", "") or "")
    return (page, row_ordinal, row_id)


def _collect_tracker_action_stream(job_folder_path: str) -> List[Dict]:
    return load_hardwoods_tracker_actions(
        tracker_dirs=[
        os.path.join(job_folder_path, ".metadata", HARDWOODS_METADATA_DIR, ".tracker"),
        os.path.join(job_folder_path, "Hardwoods", ".tracker"),
        ],
        logger=main_logger,
    )


def _tracker_completion_priority(job_folder_path: str) -> Dict[Tuple[str, str], Tuple[int, int]]:
    out: Dict[Tuple[str, str], List[int]] = {}
    for action in _collect_tracker_action_stream(job_folder_path):
        doc_type = str(action.get("docType", "") or "").strip()
        row_id = str(action.get("rowId", "") or "").strip()
        if not doc_type or not row_id or _CABINET_SKIP_MARKER in row_id:
            continue
        key = (doc_type, row_id)
        if key not in out:
            out[key] = [0, 0]
        kind = str(action.get("action", "") or "").strip()
        value = action.get("value", 0)
        try:
            numeric = max(0, int(value))
        except Exception:
            numeric = 0
        if kind == _TRACKER_SET_DONE_COUNT:
            out[key][0] = numeric
        elif kind == _TRACKER_SET_BAD_COUNT:
            out[key][1] = numeric
    return {key: (vals[0], vals[1]) for key, vals in out.items()}


def _reconcile_rows_with_previous_index(job_folder_path: str, serialized_docs: List[Dict]) -> None:
    previous = _load_existing_index(job_folder_path)
    if not previous:
        return

    completion_priority = _tracker_completion_priority(job_folder_path)
    previous_docs = {
        str(doc.get("docType", "") or ""): doc
        for doc in previous.get("documents", [])
        if isinstance(doc, dict)
    }

    for new_doc in serialized_docs:
        doc_type = str(new_doc.get("docType", "") or "")
        if not doc_type:
            continue
        prev_doc = previous_docs.get(doc_type)
        if not isinstance(prev_doc, dict):
            continue

        old_rows = prev_doc.get("rows", [])
        new_rows = new_doc.get("rows", [])
        if not isinstance(old_rows, list) or not isinstance(new_rows, list):
            continue

        candidates_by_key: Dict[str, List[Dict]] = defaultdict(list)
        for old_row in old_rows:
            if not isinstance(old_row, dict):
                continue
            old_row_id = str(old_row.get("rowId", "") or "").strip()
            if not old_row_id:
                continue
            key = _row_match_key(doc_type, old_row)
            if not key:
                continue
            done_count, bad_count = completion_priority.get((doc_type, old_row_id), (0, 0))
            candidates_by_key[key].append(
                {
                    "rowId": old_row_id,
                    "doneCount": done_count,
                    "badCount": bad_count,
                    "orderKey": _row_order_key(old_row),
                }
            )

        for key, bucket in candidates_by_key.items():
            bucket.sort(key=lambda item: (-item["doneCount"], -item["badCount"], item["orderKey"]))

        # Deterministic assignment for duplicate rows.
        indexed_new_rows = [(idx, row) for idx, row in enumerate(new_rows) if isinstance(row, dict)]
        indexed_new_rows.sort(key=lambda item: _row_order_key(item[1]))
        for _, new_row in indexed_new_rows:
            key = _row_match_key(doc_type, new_row)
            if not key:
                continue
            bucket = candidates_by_key.get(key)
            if not bucket:
                continue
            chosen = bucket.pop(0)
            new_row["rowId"] = chosen["rowId"]


def _remove_index_if_exists(job_folder_path: str) -> bool:
    out_path = os.path.join(job_folder_path, ".metadata", HARDWOODS_METADATA_DIR, HARDWOODS_INDEX_FILENAME)
    revision_path = _revision_path_for_job(job_folder_path)
    removed = False
    if os.path.exists(out_path):
        os.remove(out_path)
        removed = True
    if os.path.exists(revision_path):
        os.remove(revision_path)
        removed = True
    return removed


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
        except TemplateMismatchError as e:
            main_logger.error("Hardwoods parse skipped (template mismatch): %s (%s)", path, e)
            continue
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

    previous_index = _load_existing_index(job_folder_path)
    _reconcile_rows_with_previous_index(job_folder_path, serialized_docs)

    payload = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "documents": serialized_docs,
    }
    out_path = _write_index(job_folder_path, payload)
    revision_payload = _upsert_revision_state(
        job_folder_path=job_folder_path,
        previous_index=previous_index,
        next_docs=serialized_docs,
    )
    main_logger.info(
        "Hardwoods cutlist index updated: job=%s docs=%s output=%s revision=%s",
        os.path.basename(job_folder_path),
        len(serialized_docs),
        out_path,
        revision_payload.get("currentRevision") if isinstance(revision_payload, dict) else "n/a",
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
