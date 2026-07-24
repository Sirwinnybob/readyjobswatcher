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
from dataclasses import dataclass
from typing import List, Optional

from .file_handler import JobProcessor

main_logger = logging.getLogger("main")

MISMATCH_FLAG_FILENAME = "cutlist_job_mismatch.json"

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
