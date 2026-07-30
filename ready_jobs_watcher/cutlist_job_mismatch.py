"""
Detect when a hardwoods cutlist PDF's printed job number doesn't match the job
folder it's sitting in (wrong file dropped/copied into the wrong job).

Pure parsing/comparison logic only - no PyMuPDF or PyQt dependency, so this
module is testable with plain strings and importable from both the indexer
(background threads) and the GUI (main thread) without pulling in Qt.
"""
from __future__ import annotations

import json
import logging
import os
import re
import getpass
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Optional

from .atomic_write import atomic_write_json
from .file_handler import JobProcessor

main_logger = logging.getLogger("main")

MISMATCH_FLAG_FILENAME = "cutlist_job_mismatch.json"
MISMATCH_OVERRIDE_FILENAME = "cutlist_job_mismatch_overrides.json"

# Mirrors hardwoods_cutlist_indexer.DOC_TYPE_DOOR_LIST. Kept as a local literal
# (not imported) so this module never depends on hardwoods_cutlist_indexer,
# which imports this module - importing back would be circular.
DOC_TYPE_DOOR_LIST = "DOOR_LIST"
DOC_TYPE_FACE_FRAME = "FACE_FRAME_CUT_LIST"
DOC_TYPE_NAILER = "NAILER_CUT_LIST"
DOC_TYPE_DOOR_CUT = "DOOR_CUT_LIST"
DOC_TYPE_CLOSET_ROD = "CLOSET_ROD_CUT_LIST"

_TOKEN_PATTERN = re.compile(r"^(\d+)([A-Za-z]?)$")
_CUTLIST_HEADER_LINE_PATTERN = re.compile(r"^(\d+[A-Za-z]?)\s*-\s+\S")
_DOOR_LIST_HEADER_LINE_PATTERN = re.compile(r"Job:\s*.*\((\d+[A-Za-z]?)\)", re.IGNORECASE)


@dataclass(frozen=True)
class JobIdentifier:
    number: str
    suffix: Optional[str] = None

    def display(self) -> str:
        return f"{self.number}{self.suffix or ''}"


def parse_job_identifier(token: str) -> Optional[JobIdentifier]:
    match = _TOKEN_PATTERN.match(str(token or "").strip())
    if not match:
        return None
    number, suffix = match.group(1), match.group(2)
    # Reject all-zero numbers like "00", "000", etc.
    if int(number) == 0:
        return None
    return JobIdentifier(number=number, suffix=suffix.upper() if suffix else None)


def folder_job_identifier(job_folder_name: str) -> Optional[JobIdentifier]:
    token = JobProcessor.extract_job_number(str(job_folder_name or ""))
    if not token:
        return None
    return parse_job_identifier(token)


def extract_pdf_job_identifier(doc_type: str, first_lines: List[str]) -> Optional[JobIdentifier]:
    lines = [str(line or "").strip() for line in first_lines if str(line or "").strip()]
    if doc_type == DOC_TYPE_DOOR_LIST:
        for line in lines:
            match = _DOOR_LIST_HEADER_LINE_PATTERN.search(line)
            if match:
                return parse_job_identifier(match.group(1))
        return None
    for line in lines:
        match = _CUTLIST_HEADER_LINE_PATTERN.match(line)
        if match:
            return parse_job_identifier(match.group(1))
    return None


def is_job_mismatch(folder_id: JobIdentifier, pdf_id: JobIdentifier) -> bool:
    if folder_id.number != pdf_id.number:
        return True
    if folder_id.suffix and pdf_id.suffix and folder_id.suffix != pdf_id.suffix:
        return True
    return False


def mismatch_flag_path(job_folder_path: str) -> str:
    return os.path.join(job_folder_path, ".metadata", "hardwoods", MISMATCH_FLAG_FILENAME)


def mismatch_override_path(job_folder_path: str) -> str:
    return os.path.join(job_folder_path, ".metadata", "hardwoods", MISMATCH_OVERRIDE_FILENAME)


def _override_identity(*, doc_type: str, pdf_filename: str, expected_job: str, found_job: str):
    return (str(doc_type), str(pdf_filename), str(expected_job), str(found_job))


def _entry_override_identity(entry: dict):
    try:
        return _override_identity(
            doc_type=entry["doc_type"],
            pdf_filename=entry["pdf_filename"],
            expected_job=entry["expected_job"],
            found_job=entry["found_job"],
        )
    except KeyError:
        return None


def _load_override_entries(job_folder_path: str) -> Optional[List[dict]]:
    try:
        with open(mismatch_override_path(job_folder_path), "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as exc:
        main_logger.error("Failed reading cutlist job mismatch overrides for %s: %s", job_folder_path, exc)
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        main_logger.error("Invalid cutlist job mismatch override ledger for %s", job_folder_path)
        return None
    entries = payload.get("overrides")
    if not isinstance(entries, list):
        main_logger.error("Invalid cutlist job mismatch override entries for %s", job_folder_path)
        return None
    if any(not isinstance(entry, dict) or _entry_override_identity(entry) is None for entry in entries):
        main_logger.error("Invalid cutlist job mismatch override entry for %s", job_folder_path)
        return None
    return entries


def _write_override_entries(job_folder_path: str, entries: List[dict]) -> None:
    path = mismatch_override_path(job_folder_path)
    if entries:
        atomic_write_json(path, {"version": 1, "overrides": entries}, indent=2, ensure_ascii=False)
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def has_job_mismatch_override(
    job_folder_path: str, *, doc_type: str, pdf_filename: str, expected_job: str, found_job: str
) -> bool:
    wanted = _override_identity(
        doc_type=doc_type,
        pdf_filename=pdf_filename,
        expected_job=expected_job,
        found_job=found_job,
    )
    entries = _load_override_entries(job_folder_path)
    return bool(entries and any(_entry_override_identity(entry) == wanted for entry in entries))


def allow_job_mismatch_override(
    job_folder_path: str,
    *,
    doc_type: str,
    pdf_filename: str,
    expected_job: str,
    found_job: str,
    approved_by: Optional[str] = None,
) -> bool:
    wanted = _override_identity(
        doc_type=doc_type,
        pdf_filename=pdf_filename,
        expected_job=expected_job,
        found_job=found_job,
    )
    entries = _load_override_entries(job_folder_path)
    if entries is None:
        return False
    if any(_entry_override_identity(entry) == wanted for entry in entries):
        return True
    entries.append({
        "doc_type": wanted[0],
        "pdf_filename": wanted[1],
        "expected_job": wanted[2],
        "found_job": wanted[3],
        "approvedAt": datetime.now(timezone.utc).isoformat(),
        "approvedBy": str(approved_by) if approved_by is not None else getpass.getuser(),
    })
    _write_override_entries(job_folder_path, entries)
    return True


def remove_job_mismatch_override(
    job_folder_path: str, *, doc_type: str, pdf_filename: str, expected_job: str, found_job: str
) -> bool:
    wanted = _override_identity(
        doc_type=doc_type,
        pdf_filename=pdf_filename,
        expected_job=expected_job,
        found_job=found_job,
    )
    entries = _load_override_entries(job_folder_path)
    if entries is None:
        return False
    retained = []
    removed = False
    for entry in entries:
        matches = _entry_override_identity(entry) == wanted
        if matches:
            removed = True
        else:
            retained.append(entry)
    if removed:
        _write_override_entries(job_folder_path, retained)
    return removed


def read_job_mismatch_flags(root_dir: str, job_folder_name: str) -> Optional[dict]:
    path = mismatch_flag_path(os.path.join(root_dir, job_folder_name))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception as exc:
        main_logger.error("Failed reading cutlist job mismatch flag %s: %s", path, exc)
        return None
