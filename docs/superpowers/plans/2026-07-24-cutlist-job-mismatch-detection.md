# Cutlist Job-Mismatch Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect hardwoods cutlist PDFs whose printed job number doesn't match the job folder they're
sitting in, block that document's rows from reaching `cutlist_index.json` (so a wrong-job cutlist
never reaches the tablet/production floor), and surface the problem via a popup alert plus a
persistent, self-clearing dashboard flag.

**Architecture:** A new dependency-free module (`cutlist_job_mismatch.py`) extracts and compares job
identifiers (pure functions, no PyMuPDF/PyQt). `hardwoods_cutlist_indexer.py` calls it during its
existing page-1 checks, raises a new `JobMismatchError` per mismatched doc, writes a small persistent
JSON flag file, and invokes an optional callback. That callback is threaded through the existing
watcher/scheduler → GUI signal plumbing (the same pattern already used for bad-parts alerts) to pop an
always-on-top alert and light up a Jobs-tab badge.

**Tech Stack:** Python, PyMuPDF (fitz, already a dependency), PyQt6, pytest.

**Spec:** `docs/superpowers/specs/2026-07-24-cutlist-job-mismatch-detection-design.md`

---

### Task 1: Job-identifier parsing and comparison logic

**Files:**
- Create: `ready_jobs_watcher/cutlist_job_mismatch.py`
- Test: `tests/test_cutlist_job_mismatch.py`

- [ ] **Step 1: Write the failing tests**

```python
import ready_jobs_watcher.cutlist_job_mismatch as mismatch


def test_parse_job_identifier_plain_number():
    result = mismatch.parse_job_identifier("616")
    assert result == mismatch.JobIdentifier(number="616", suffix=None)


def test_parse_job_identifier_number_with_suffix():
    result = mismatch.parse_job_identifier("616b")
    assert result == mismatch.JobIdentifier(number="616", suffix="B")


def test_parse_job_identifier_rejects_dash_joined_number():
    assert mismatch.parse_job_identifier("123-4") is None


def test_parse_job_identifier_rejects_garbage():
    assert mismatch.parse_job_identifier("DC BIGLEY") is None
    assert mismatch.parse_job_identifier("") is None


def test_extract_pdf_job_identifier_cutlist_style_header():
    lines = [
        "Nailer Cut List 2.0",
        "656 - KENT WITHAM - HICKORY, SHAKER DOORS - 16 July, 2026",
        "Material: '3/4 Prefinished 19mm' | Units:Sheet |",
    ]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_NAILER, lines)
    assert result == mismatch.JobIdentifier(number="656", suffix=None)


def test_extract_pdf_job_identifier_suffixed_job_number():
    lines = ["Face Frame Cut List 2.0", "616b - KEVIN JANNI - Default - 17 June, 2026"]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_FACE_FRAME, lines)
    assert result == mismatch.JobIdentifier(number="616", suffix="B")


def test_extract_pdf_job_identifier_door_list_style_header():
    lines = ["Door List", "Job: BEECH-NEW BEVEL (582)", "Page 1 of 2"]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_DOOR_LIST, lines)
    assert result == mismatch.JobIdentifier(number="582", suffix=None)


def test_extract_pdf_job_identifier_door_list_style_with_suffix():
    lines = ["Door List", "Job: SOME JOB (530a)"]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_DOOR_LIST, lines)
    assert result == mismatch.JobIdentifier(number="530", suffix="A")


def test_extract_pdf_job_identifier_no_match_returns_none():
    lines = ["Nailer Cut List 2.0", "Material: '3/4 Maple' | Units:Sheet |"]
    assert mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_NAILER, lines) is None


def test_extract_pdf_job_identifier_title_line_not_mistaken_for_job_line():
    # "Nailer Cut List 2.0" must not match the "<num> - ..." pattern.
    lines = ["Nailer Cut List 2.0"]
    assert mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_NAILER, lines) is None


def test_is_job_mismatch_same_number_no_suffix_either_side():
    folder_id = mismatch.JobIdentifier(number="656", suffix=None)
    pdf_id = mismatch.JobIdentifier(number="656", suffix=None)
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_folder_has_suffix_pdf_does_not():
    folder_id = mismatch.JobIdentifier(number="616", suffix="B")
    pdf_id = mismatch.JobIdentifier(number="616", suffix=None)
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_pdf_has_suffix_folder_does_not():
    folder_id = mismatch.JobIdentifier(number="616", suffix=None)
    pdf_id = mismatch.JobIdentifier(number="616", suffix="B")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_both_have_same_suffix():
    folder_id = mismatch.JobIdentifier(number="616", suffix="B")
    pdf_id = mismatch.JobIdentifier(number="616", suffix="B")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_both_have_different_suffix():
    folder_id = mismatch.JobIdentifier(number="616", suffix="A")
    pdf_id = mismatch.JobIdentifier(number="616", suffix="B")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is True


def test_is_job_mismatch_different_number():
    folder_id = mismatch.JobIdentifier(number="530", suffix="A")
    pdf_id = mismatch.JobIdentifier(number="532", suffix="A")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is True


def test_folder_job_identifier_extracts_from_folder_name():
    result = mismatch.folder_job_identifier("530a - DC BIGLEY")
    assert result == mismatch.JobIdentifier(number="530", suffix="A")


def test_folder_job_identifier_returns_none_for_unrecognized_name():
    assert mismatch.folder_job_identifier("00 INSTALLATION DRAWINGS (FILLERS, CROWN)") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cutlist_job_mismatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ready_jobs_watcher.cutlist_job_mismatch'`

- [ ] **Step 3: Write the implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cutlist_job_mismatch.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/cutlist_job_mismatch.py tests/test_cutlist_job_mismatch.py
git commit -m "feat: add cutlist job-identifier parsing and comparison logic"
```

---

### Task 2: Flag-file write/clear helpers and `JobMismatchError`

**Files:**
- Modify: `ready_jobs_watcher/hardwoods_cutlist_indexer.py`
- Test: `tests/test_hardwoods_cutlist_indexer.py`

This task adds the exception type and the three flag-file helpers to the indexer module, without
wiring them into the parse/build flow yet (that's Task 3 and Task 4). Keeping this separate lets each
piece be tested in isolation before the integration step.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hardwoods_cutlist_indexer.py`:

```python
def test_write_and_load_mismatch_flags_round_trip(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    entries = [
        {
            "docType": indexer.DOC_TYPE_NAILER,
            "pdfFilename": "530a - Nailer Cut List.pdf",
            "expectedJob": "530a",
            "foundJob": "532",
            "detectedAt": "2026-07-24T00:00:00+00:00",
        }
    ]
    indexer._write_mismatch_flags(str(job_dir), entries)
    loaded = indexer._load_existing_mismatch_flags(str(job_dir))
    assert loaded == {indexer.DOC_TYPE_NAILER: entries[0]}


def test_write_mismatch_flags_with_empty_list_removes_file(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    entries = [
        {
            "docType": indexer.DOC_TYPE_NAILER,
            "pdfFilename": "530a - Nailer Cut List.pdf",
            "expectedJob": "530a",
            "foundJob": "532",
            "detectedAt": "2026-07-24T00:00:00+00:00",
        }
    ]
    indexer._write_mismatch_flags(str(job_dir), entries)
    assert os.path.exists(cutlist_mismatch.mismatch_flag_path(str(job_dir)))
    indexer._write_mismatch_flags(str(job_dir), [])
    assert not os.path.exists(cutlist_mismatch.mismatch_flag_path(str(job_dir)))


def test_load_existing_mismatch_flags_returns_empty_dict_when_no_file(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    assert indexer._load_existing_mismatch_flags(str(job_dir)) == {}
```

Add near the top of `tests/test_hardwoods_cutlist_indexer.py`, alongside the existing imports:

```python
import ready_jobs_watcher.cutlist_job_mismatch as cutlist_mismatch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v -k mismatch_flags`
Expected: FAIL with `AttributeError: module 'ready_jobs_watcher.hardwoods_cutlist_indexer' has no attribute '_write_mismatch_flags'`

- [ ] **Step 3: Write the implementation**

In `ready_jobs_watcher/hardwoods_cutlist_indexer.py`, add the import near the top (with the other
relative imports, around line 19-22):

```python
from .cutlist_job_mismatch import (
    JobIdentifier,
    extract_pdf_job_identifier,
    folder_job_identifier,
    is_job_mismatch,
    mismatch_flag_path,
)
```

Add the new exception class next to the existing ones (around line 68-73):

```python
class TemplateMismatchError(Exception):
    pass


class SkippableDocumentError(Exception):
    pass


class JobMismatchError(Exception):
    def __init__(self, expected: JobIdentifier, found: JobIdentifier):
        self.expected = expected
        self.found = found
        super().__init__(f"job mismatch: expected {expected.display()}, found {found.display()}")
```

Add the flag-file helpers near the other per-job path/load/write helpers, right after
`_write_revision_state` (around line 1091, before `_normalize_match_text`):

```python
def _load_existing_mismatch_flags(job_folder_path: str) -> Dict[str, Dict]:
    path = mismatch_flag_path(job_folder_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("mismatches")
    if not isinstance(entries, list):
        return {}
    out: Dict[str, Dict] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("docType"):
            out[str(entry["docType"])] = entry
    return out


def _write_mismatch_flags(job_folder_path: str, entries: List[Dict]) -> None:
    path = mismatch_flag_path(job_folder_path)
    if not entries:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            main_logger.warning("hardwoods_cutlist_indexer: could not remove mismatch flag for %s: %s", job_folder_path, exc)
        return
    payload = {
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mismatches": entries,
    }
    try:
        metadata_dir = os.path.dirname(path)
        os.makedirs(metadata_dir, exist_ok=True)
        _shared_atomic_write_json(path, payload, indent=2, ensure_ascii=False)
    except OSError as exc:
        main_logger.warning("hardwoods_cutlist_indexer: could not write mismatch flag for %s: %s", job_folder_path, exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v -k mismatch_flags`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full indexer test suite to check nothing else broke**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v`
Expected: PASS (43 tests: 37 existing + 3 new + the pre-existing placeholder-related ones from
today's earlier fix)

- [ ] **Step 6: Commit**

```bash
git add ready_jobs_watcher/hardwoods_cutlist_indexer.py tests/test_hardwoods_cutlist_indexer.py
git commit -m "feat: add JobMismatchError and mismatch-flag file helpers to hardwoods indexer"
```

---

### Task 3: Wire the mismatch check into page-1 parsing

**Files:**
- Modify: `ready_jobs_watcher/hardwoods_cutlist_indexer.py`
- Test: `tests/test_hardwoods_cutlist_indexer.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_hardwoods_cutlist_indexer.py`. This mirrors the real `616b`-folder /
`616`-printed-in-PDF shape found during the design investigation, plus a genuine mismatch:

```python
def test_mismatched_job_number_raises_job_mismatch_error(tmp_path, monkeypatch):
    job_dir = tmp_path / "530a - DC BIGLEY"
    job_dir.mkdir()
    nailer = job_dir / "530a - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    job_line_y = 130.0
    job_line_words = [
        _w(80, job_line_y, "532"),
        _w(100, job_line_y, "-"),
        _w(112, job_line_y, "WRONG"),
        _w(160, job_line_y, "JOB"),
    ]
    page_words = list(job_line_words)
    page_words += _std_header(160)
    page_words += _std_row(182, 2, "Bottom Rail", "4.75", "54", "15, 16")

    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=page_words)]))

    with pytest.raises(indexer.JobMismatchError) as excinfo:
        indexer._parse_document_rows(indexer.DOC_TYPE_NAILER, str(nailer), str(job_dir))
    assert excinfo.value.expected.display() == "530A"  # parse_job_identifier uppercases the suffix
    assert excinfo.value.found.display() == "532"


def test_suffix_dropped_by_cabinet_vision_is_not_a_mismatch(tmp_path, monkeypatch):
    job_dir = tmp_path / "616b - KEVIN JANNI 1711 DUKE ST"
    job_dir.mkdir()
    face_frame = job_dir / "616b - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    job_line_y = 130.0
    job_line_words = [
        _w(80, job_line_y, "616"),
        _w(100, job_line_y, "-"),
        _w(112, job_line_y, "KEVIN"),
        _w(160, job_line_y, "JANNI"),
    ]
    page_words = list(job_line_words)
    page_words += _std_header(160)
    page_words += _std_row(182, 2, "Bottom Rail", "4.75", "54", "15, 16")

    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=page_words)]))

    page_count, rows, totals = indexer._parse_document_rows(
        indexer.DOC_TYPE_FACE_FRAME, str(face_frame), str(job_dir)
    )
    assert len(rows) == 1


def test_no_job_line_present_does_not_block_parsing(tmp_path, monkeypatch):
    # Existing fixtures across this file have no job-identity line before the
    # header - confirms the check fails open when the PDF side can't be read.
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = _std_header(160) + _std_row(182, 2, "Bottom Rail", "4.75", "54", "15, 16")
    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=page_words)]))

    page_count, rows, totals = indexer._parse_document_rows(
        indexer.DOC_TYPE_FACE_FRAME, str(face_frame), str(job_dir)
    )
    assert len(rows) == 1
```

`tests/test_hardwoods_cutlist_indexer.py` does not currently import `pytest`. Add it as the first line:

```python
import pytest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v -k "mismatch_error or suffix_dropped or no_job_line"`
Expected: FAIL — `TypeError: _parse_document_rows() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Write the implementation**

In `ready_jobs_watcher/hardwoods_cutlist_indexer.py`, change the `_parse_document_rows` signature
(currently at line 758) to accept the job folder path:

```python
def _parse_document_rows(doc_type: str, pdf_path: str, job_folder_path: str) -> Tuple[int, List[Dict], List[Dict]]:
```

In the page-1 checks block inside `_parse_document_rows` (currently lines 778-782), add the job
identifier check after the existing placeholder/legacy checks so a placeholder stub never triggers a
mismatch alert:

```python
            if page_index == 0:
                if _is_placeholder_document(rows_by_y):
                    raise SkippableDocumentError("placeholder document")
                if _is_legacy_cabinet_vision_cutlist(rows_by_y, doc_type):
                    raise SkippableDocumentError("legacy cabinet vision cut list layout")
                folder_id = folder_job_identifier(os.path.basename(job_folder_path))
                if folder_id is not None:
                    first_lines = [_line_text(row_words) for _, row_words in rows_by_y[:6]]
                    pdf_id = extract_pdf_job_identifier(doc_type, first_lines)
                    if pdf_id is not None and is_job_mismatch(folder_id, pdf_id):
                        raise JobMismatchError(expected=folder_id, found=pdf_id)
```

Update the one call site inside `build_hardwoods_cutlist_index_for_job` (currently line 1520) to pass
the job folder path:

```python
            page_count, rows, totals = _parse_document_rows(doc_type, path, job_folder_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v -k "mismatch_error or suffix_dropped or no_job_line"`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full indexer test suite**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v`
Expected: PASS, all tests (confirms none of the ~37 pre-existing fixtures - which have no job-identity
line before their header rows - accidentally trip the new check; the `pdf_id is not None` guard is
what makes this fail open for them)

- [ ] **Step 6: Commit**

```bash
git add ready_jobs_watcher/hardwoods_cutlist_indexer.py tests/test_hardwoods_cutlist_indexer.py
git commit -m "feat: detect wrong-job cutlist PDFs during page-1 template checks"
```

---

### Task 4: Block the mismatched doc, persist the flag, fire the callback

**Files:**
- Modify: `ready_jobs_watcher/hardwoods_cutlist_indexer.py`
- Test: `tests/test_hardwoods_cutlist_indexer.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hardwoods_cutlist_indexer.py`:

```python
def test_job_mismatch_excludes_doc_but_keeps_sibling_docs(tmp_path, monkeypatch):
    job_dir = tmp_path / "530a - DC BIGLEY"
    job_dir.mkdir()
    nailer = job_dir / "530a - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")
    face_frame = job_dir / "530a - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    wrong_job_words = [_w(80, 130, "532"), _w(100, 130, "-"), _w(112, 130, "WRONG"), _w(160, 130, "JOB")]
    right_job_words = [_w(80, 130, "530a"), _w(112, 130, "-"), _w(140, 130, "DC"), _w(160, 130, "BIGLEY")]
    table_words = _std_header(160) + _std_row(182, 2, "Bottom Rail", "4.75", "54", "15, 16")

    doc_map = {
        str(nailer): _FakeDoc([_FakePage(words=wrong_job_words + table_words)]),
        str(face_frame): _FakeDoc([_FakePage(words=right_job_words + table_words)]),
    }
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    doc_types = {doc["docType"] for doc in payload["documents"]}
    assert doc_types == {indexer.DOC_TYPE_FACE_FRAME}


def test_job_mismatch_writes_flag_file_then_clears_it_once_fixed(tmp_path, monkeypatch):
    job_dir = tmp_path / "530a - DC BIGLEY"
    job_dir.mkdir()
    nailer = job_dir / "530a - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    wrong_job_words = [_w(80, 130, "532"), _w(100, 130, "-"), _w(112, 130, "WRONG"), _w(160, 130, "JOB")]
    right_job_words = [_w(80, 130, "530a"), _w(112, 130, "-"), _w(140, 130, "DC"), _w(160, 130, "BIGLEY")]
    table_words = _std_header(160) + _std_row(182, 2, "Bottom Rail", "4.75", "54", "15, 16")

    monkeypatch.setattr(
        indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=wrong_job_words + table_words)])
    )
    indexer.build_hardwoods_cutlist_index_for_job(str(job_dir))
    flags = indexer._load_existing_mismatch_flags(str(job_dir))
    assert flags[indexer.DOC_TYPE_NAILER]["expectedJob"] == "530A"  # parse_job_identifier uppercases the suffix
    assert flags[indexer.DOC_TYPE_NAILER]["foundJob"] == "532"

    monkeypatch.setattr(
        indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=right_job_words + table_words)])
    )
    indexer.build_hardwoods_cutlist_index_for_job(str(job_dir))
    assert indexer._load_existing_mismatch_flags(str(job_dir)) == {}
    assert not os.path.exists(cutlist_mismatch.mismatch_flag_path(str(job_dir)))


def test_on_job_mismatch_callback_fires_once_then_not_again_for_same_unfixed_doc(tmp_path, monkeypatch):
    job_dir = tmp_path / "530a - DC BIGLEY"
    job_dir.mkdir()
    nailer = job_dir / "530a - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    wrong_job_words = [_w(80, 130, "532"), _w(100, 130, "-"), _w(112, 130, "WRONG"), _w(160, 130, "JOB")]
    table_words = _std_header(160) + _std_row(182, 2, "Bottom Rail", "4.75", "54", "15, 16")
    monkeypatch.setattr(
        indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=wrong_job_words + table_words)])
    )

    calls = []
    indexer.build_hardwoods_cutlist_index_for_job(str(job_dir), on_job_mismatch=calls.append)
    indexer.build_hardwoods_cutlist_index_for_job(str(job_dir), on_job_mismatch=calls.append)

    assert len(calls) == 1
    assert calls[0]["docType"] == indexer.DOC_TYPE_NAILER
    assert calls[0]["jobFolderName"] == "530a - DC BIGLEY"
    assert calls[0]["expectedJob"] == "530A"  # parse_job_identifier uppercases the suffix
    assert calls[0]["foundJob"] == "532"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v -k "excludes_doc_but_keeps or writes_flag_file or callback_fires"`
Expected: FAIL — the Nailer doc still raises `JobMismatchError` uncaught inside
`build_hardwoods_cutlist_index_for_job` (no `except JobMismatchError` branch yet), so the whole call
raises instead of returning `True`.

- [ ] **Step 3: Write the implementation**

In `ready_jobs_watcher/hardwoods_cutlist_indexer.py`, update `build_hardwoods_cutlist_index_for_job`'s
signature (currently line 1502):

```python
def build_hardwoods_cutlist_index_for_job(
    job_folder_path: str,
    deployment_gate=None,
    on_job_mismatch: Optional[Callable[[Dict], None]] = None,
) -> bool:
```

Add `Callable` to the existing `from typing import ...` import line near the top of the file (line 16):

```python
from typing import Callable, Dict, List, Optional, Tuple
```

Inside `build_hardwoods_cutlist_index_for_job`, right after the `docs = _find_hardwoods_docs(...)` /
`if not docs:` early-return block (currently lines 1513-1515), clear any stale mismatch flags when
there are no hardwoods docs at all:

```python
    docs = _find_hardwoods_docs(job_folder_path)
    if not docs:
        _write_mismatch_flags(job_folder_path, [])
        return _remove_index_if_exists(job_folder_path)
```

Right before the `serialized_docs: List[Dict] = []` line (currently line 1517), load the previously
recorded mismatches so the callback only fires for newly-detected ones:

```python
    previous_mismatches = _load_existing_mismatch_flags(job_folder_path)
    serialized_docs: List[Dict] = []
    current_mismatches: List[Dict] = []
```

Add a new `except JobMismatchError` branch in the per-doc-type loop (currently lines 1518-1529),
placed after the existing `except TemplateMismatchError` branch and before the generic
`except Exception`:

```python
    for doc_type, (filename, path) in sorted(docs.items(), key=lambda item: item[0]):
        try:
            page_count, rows, totals = _parse_document_rows(doc_type, path, job_folder_path)
        except SkippableDocumentError as e:
            main_logger.info("Hardwoods parse skipped: %s (%s)", path, e)
            continue
        except TemplateMismatchError as e:
            main_logger.error("Hardwoods parse skipped (template mismatch): %s (%s)", path, e)
            continue
        except JobMismatchError as e:
            entry = {
                "docType": doc_type,
                "pdfFilename": filename,
                "expectedJob": e.expected.display(),
                "foundJob": e.found.display(),
                "detectedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            current_mismatches.append(entry)
            main_logger.error(
                "Hardwoods parse skipped (job mismatch): %s (expected %s, found %s)",
                path,
                entry["expectedJob"],
                entry["foundJob"],
            )
            if on_job_mismatch is not None and doc_type not in previous_mismatches:
                try:
                    on_job_mismatch({**entry, "jobFolderName": os.path.basename(job_folder_path)})
                except Exception as exc:
                    main_logger.warning("on_job_mismatch callback failed for %s: %s", path, exc)
            continue
        except Exception as e:
            main_logger.error("Hardwoods parse failed: %s (%s)", path, e, exc_info=True)
            continue
```

Right after the loop, before `if not serialized_docs:` (currently line 1541), write the current
mismatch state (this runs every call, so a fixed doc's entry naturally drops off):

```python
    _write_mismatch_flags(job_folder_path, current_mismatches)

    if not serialized_docs:
        return _remove_index_if_exists(job_folder_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v -k "excludes_doc_but_keeps or writes_flag_file or callback_fires"`
Expected: PASS (3 tests)

- [ ] **Step 5: Thread `on_job_mismatch` through the PDF-event entry point**

`build_hardwoods_cutlist_index_for_pdf_event` (currently line 1567) is the entry point the file
watcher uses; it needs to accept and forward the same parameter:

```python
def build_hardwoods_cutlist_index_for_pdf_event(
    pdf_path: str,
    deployment_gate=None,
    on_job_mismatch: Optional[Callable[[Dict], None]] = None,
) -> bool:
```

And its call into `build_hardwoods_cutlist_index_for_job` (currently line 1590):

```python
    return build_hardwoods_cutlist_index_for_job(
        job_folder, deployment_gate=deployment_gate, on_job_mismatch=on_job_mismatch
    )
```

- [ ] **Step 6: Run the full indexer test suite**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -v`
Expected: PASS, all tests

- [ ] **Step 7: Commit**

```bash
git add ready_jobs_watcher/hardwoods_cutlist_indexer.py tests/test_hardwoods_cutlist_indexer.py
git commit -m "feat: block wrong-job cutlist rows, persist flag, notify on mismatch"
```

---

### Task 5: Wire `on_job_mismatch` through `watchers.py`

**Files:**
- Modify: `ready_jobs_watcher/watchers.py`

No new automated test for this task — `PdfChangeHandler` is exercised indirectly through the watcher
integration tests already in the suite, and the change here is a straight parameter passthrough with
no new branching logic. Verified by Task 9's full-suite run plus the manual walkthrough.

- [ ] **Step 1: Add the constructor parameter**

In `ready_jobs_watcher/watchers.py`, `PdfChangeHandler.__init__` (currently starting at line 369), add
the new parameter and store it:

```python
    def __init__(
        self,
        config,
        rename_handler=None,
        pending_queue=None,
        executor=None,
        tracker_monitor: Optional[TrackerBadPartsMonitor] = None,
        alert_coordinator: Optional[AlertCoordinator] = None,
        deployment_gate=None,
        metadata_refresh_service=None,
        on_job_mismatch=None,
    ):
```

```python
        self.deployment_gate = deployment_gate
        self.metadata_refresh_service = metadata_refresh_service
        self.on_job_mismatch = on_job_mismatch
```

- [ ] **Step 2: Pass it through at both `build_hardwoods_cutlist_index_for_pdf_event` call sites**

First call site, `_run_index_refresh` (currently line 530):

```python
        try:
            build_hardwoods_cutlist_index_for_pdf_event(
                pdf_path, deployment_gate=self.deployment_gate, on_job_mismatch=self.on_job_mismatch
            )
        except Exception as e:
            main_logger.error(f"Hardwoods cutlist index refresh failed ({reason}): {pdf_path} ({e})", exc_info=True)
```

Second call site, the deleted-file handler (currently line 893):

```python
                    try:
                        build_hardwoods_cutlist_index_for_pdf_event(
                            event.src_path, deployment_gate=self.deployment_gate, on_job_mismatch=self.on_job_mismatch
                        )
                    except Exception as e:
                        main_logger.error(f"Hardwoods cutlist index refresh failed (deleted): {event.src_path} ({e})", exc_info=True)
```

- [ ] **Step 3: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "from ready_jobs_watcher.watchers import PdfChangeHandler; print('ok')"`
Expected: prints `ok`, no traceback

- [ ] **Step 4: Commit**

```bash
git add ready_jobs_watcher/watchers.py
git commit -m "feat: thread on_job_mismatch callback through PdfChangeHandler"
```

---

### Task 6: Queue/flush the mismatch notice in `main.py` and wire it to every call site

**Files:**
- Modify: `ready_jobs_watcher/main.py`

Mirrors the existing `_queue_bad_parts_popup` / `_flush_pending_bad_parts_popups` pattern (lines
248-263) exactly, since a mismatch can be detected before the GUI window exists yet (startup scan runs
before `SettingsWindow` is constructed).

- [ ] **Step 1: Add the queue/flush pair**

In `ready_jobs_watcher/main.py`, add state alongside the existing pending-alert state (currently lines
231-238):

```python
        self._pending_alert_batches = []
        self._pending_alert_lock = threading.Lock()
        self._pending_job_prompts = []
        self._pending_job_prompt_lock = threading.Lock()
        from .debounce import DebouncedTimerMap
        self._pending_job_timers = DebouncedTimerMap(name_prefix="PendingPrompt")
        self._pending_auto_release_notices = []
        self._pending_auto_release_notices_lock = threading.Lock()
        self._pending_job_mismatch_notices = []
        self._pending_job_mismatch_notices_lock = threading.Lock()
```

Add the queue/flush methods alongside the existing ones (currently after line 294, right after
`_flush_pending_auto_release_notices`):

```python
    def _queue_job_mismatch_notice(self, payload: dict):
        if self.settings_window:
            self.settings_window.emit_cutlist_mismatch_notice(payload)
            return
        with self._pending_job_mismatch_notices_lock:
            self._pending_job_mismatch_notices.append(payload)

    def _flush_pending_job_mismatch_notices(self):
        if not self.settings_window:
            return
        with self._pending_job_mismatch_notices_lock:
            notices = list(self._pending_job_mismatch_notices)
            self._pending_job_mismatch_notices.clear()
        for payload in notices:
            self.settings_window.emit_cutlist_mismatch_notice(payload)
```

- [ ] **Step 2: Flush on window creation**

Add to the flush sequence right after `SettingsWindow` is constructed (currently lines 1407-1409):

```python
        self._flush_pending_bad_parts_popups()
        self._flush_pending_job_prompts()
        self._flush_pending_auto_release_notices()
        self._flush_pending_job_mismatch_notices()
```

- [ ] **Step 3: Wire `on_job_mismatch` into all three `build_hardwoods_cutlist_index_for_job` call sites**

Currently line 538:

```python
        try:
            hardwood_ok = bool(
                build_hardwoods_cutlist_index_for_job(
                    job_path, deployment_gate=self.deployment_gate, on_job_mismatch=self._queue_job_mismatch_notice
                )
            )
        except Exception as exc:
            logging.error("Hardwoods index build failed during deploy parse for %s: %s", job_folder_name, exc, exc_info=True)
```

Currently line 639 (same replacement, `# c) Build hardwoods cutlist index` block):

```python
        try:
            hardwood_ok = bool(
                build_hardwoods_cutlist_index_for_job(
                    job_path, deployment_gate=self.deployment_gate, on_job_mismatch=self._queue_job_mismatch_notice
                )
            )
        except Exception as exc:
            logging.error("Hardwoods index build failed during re-parse for %s: %s", job_folder_name, exc, exc_info=True)
```

Currently line 1589 (`_build_indexes_for_job`):

```python
                try:
                    build_hardwoods_cutlist_index_for_job(
                        job_path, deployment_gate=self.deployment_gate, on_job_mismatch=self._queue_job_mismatch_notice
                    )
                except Exception as e:
                    logging.error(f"Hardwoods cutlist index build failed for {job_path}: {e}", exc_info=True)
```

- [ ] **Step 4: Wire it into `PdfChangeHandler` construction**

Currently lines 1320-1329:

```python
            pdf_event_handler = PdfChangeHandler(
                self.config,
                rename_handler=event_handler,
                pending_queue=self.pending_queue,
                executor=self.executor,
                tracker_monitor=self.tracker_monitor,
                alert_coordinator=self.alert_coordinator,
                deployment_gate=self.deployment_gate,
                metadata_refresh_service=getattr(self, "metadata_refresh_service", None),
                on_job_mismatch=self._queue_job_mismatch_notice,
            )
```

- [ ] **Step 5: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "import ready_jobs_watcher.main; print('ok')"`
Expected: prints `ok`, no traceback. `self.settings_window.emit_cutlist_mismatch_notice(...)` is only
called inside a method body, not at import time, so this passes even though `SettingsWindow` doesn't
get that method until Task 7 — Python doesn't resolve the attribute until the line actually runs.

- [ ] **Step 6: Commit**

```bash
git add ready_jobs_watcher/main.py
git commit -m "feat: queue/flush cutlist mismatch notices and wire callback into all call sites"
```

---

### Task 7: GUI signal and always-on-top alert dialog

**Files:**
- Modify: `ready_jobs_watcher/gui.py`

- [ ] **Step 1: Add the signal class**

Add alongside the other `QObject` signal classes (currently lines 183-205, after
`MoldingSyncSignal`):

```python
class JobMismatchSignal(QObject):
    new_mismatch = pyqtSignal(object)
```

- [ ] **Step 2: Construct and connect it**

Add alongside the other signal construction in `SettingsWindow.__init__` (currently lines 326-329,
after `consolidation_signal`/`molding_sync_signal` setup):

```python
        self.job_mismatch_signal = JobMismatchSignal()
        self.job_mismatch_signal.new_mismatch.connect(self._show_cutlist_mismatch_alert_dialog)
```

- [ ] **Step 3: Add the emit method**

Add alongside the other `emit_*` methods (currently lines 811-818, after `emit_auto_release_notice`):

```python
    def emit_cutlist_mismatch_notice(self, payload: dict):
        self.job_mismatch_signal.new_mismatch.emit(payload)
```

- [ ] **Step 4: Add the alert dialog**

Add alongside `_show_bad_parts_alert_dialog` (currently lines 1280-1304, right after it):

```python
    def _show_cutlist_mismatch_alert_dialog(self, payload: dict):
        job_folder_name = str(payload.get("jobFolderName") or "unknown job")
        pdf_filename = str(payload.get("pdfFilename") or "unknown file")
        expected_job = str(payload.get("expectedJob") or "?")
        found_job = str(payload.get("foundJob") or "?")

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("CUTLIST JOB MISMATCH")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        msg_box.setText(
            f"'{pdf_filename}' in job '{job_folder_name}' shows job {found_job}, "
            f"but this folder is job {expected_job}.\n\n"
            "This cutlist was NOT indexed - the tablet will not see it until the "
            "correct file replaces it. It will re-index automatically once fixed."
        )
        msg_box.addButton("Dismiss", QMessageBox.ButtonRole.AcceptRole)
        msg_box.exec()
```

- [ ] **Step 5: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "from ready_jobs_watcher.gui import SettingsWindow, JobMismatchSignal; import ready_jobs_watcher.main; print('ok')"`
Expected: prints `ok`, no traceback

- [ ] **Step 6: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "feat: add always-on-top popup alert for cutlist job mismatches"
```

---

### Task 8: Jobs-tab badge and double-click detail dialog

**Files:**
- Modify: `ready_jobs_watcher/gui.py`

- [ ] **Step 1: Add the badge to `_populate_jobs_table`**

Replace the whole method (currently lines 832-879) with this version — it adds the
`from .cutlist_job_mismatch import read_job_mismatch_flags` import once at the top (not per-row), adds
`mismatch_style`, and layers the mismatch check on top of `display_state`/`bg`/`fg` right before the
`values = [...]` list is built. Mismatch wins visually regardless of gate state, same precedence
`DUPLICATE` already has:

```python
    def _populate_jobs_table(self, rows: List[Dict]):
        if self.jobs_table is None:
            return
        from .deployment_gate import derive_state
        from .cutlist_job_mismatch import read_job_mismatch_flags

        state_styles = {
            "PENDING": (QColor("#FEF3C7"), QColor("#92400E")),
            "PARSING": (QColor("#DBEAFE"), QColor("#1E40AF")),
            "ACTIVE":  (QColor("#D1FAE5"), QColor("#065F46")),
            "DUPLICATE": (QColor("#FEE2E2"), QColor("#991B1B")),
        }
        hidden_style = (QColor("#E2E8F0"), QColor("#334155"))
        mismatch_style = (QColor("#FEE2E2"), QColor("#991B1B"))

        self.jobs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            mode_detection = row.get("modeDetection", {}) if isinstance(row.get("modeDetection"), dict) else {}
            timers = row.get("timers", {}) if isinstance(row.get("timers"), dict) else {}
            duplicate_marker = row.get("duplicateSuspect")
            if isinstance(duplicate_marker, dict):
                state_name = "DUPLICATE"
                collided_with = str(duplicate_marker.get("suspectedDuplicateOf") or "?")
                display_state = f"DUPLICATE (of {collided_with})"
                bg, fg = state_styles["DUPLICATE"]
            else:
                state_name = derive_state(row)
                is_hidden_from_production = bool(row.get("hiddenFromProduction", False))
                if is_hidden_from_production:
                    bg, fg = hidden_style
                    display_state = f"{state_name} (Hidden)"
                else:
                    bg, fg = state_styles.get(state_name, (None, None))
                    display_state = state_name

            mismatch_payload = read_job_mismatch_flags(self.config.ROOT_DIR, str(row.get("jobFolderName", "")))
            mismatch_entries = mismatch_payload.get("mismatches") if isinstance(mismatch_payload, dict) else None
            if isinstance(mismatch_entries, list) and mismatch_entries:
                bg, fg = mismatch_style
                display_state = f"{display_state} - CUTLIST MISMATCH ({len(mismatch_entries)})"

            values = [
                str(row.get("jobFolderName", "")),
                display_state,
                str(row.get("selectedMode", "UNKNOWN")),
                str(mode_detection.get("candidate", "UNKNOWN")),
                str(mode_detection.get("source", "UNKNOWN")),
                str(timers.get("remindAt") or "-"),
                str(row.get("updatedAt") or "-"),
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if bg is not None:
                    item.setBackground(bg)
                    item.setForeground(fg)
                self.jobs_table.setItem(row_index, col_index, item)
```

- [ ] **Step 2: Add the detail dialog**

Add alongside `_show_duplicate_job_dialog` (after it ends, before `_get_job_row_by_name`'s current
position - place this new method right after `_show_duplicate_job_dialog`'s closing, before line
907's `_get_job_row_by_name`):

```python
    def _show_cutlist_mismatch_job_dialog(self, job_folder_name: str, payload: dict):
        entries = payload.get("mismatches", []) if isinstance(payload, dict) else []
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Cutlist Job Mismatch: {job_folder_name}")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"The following cutlist document(s) in '{job_folder_name}' print a different job "
            "number than this folder. They were NOT indexed - the tablet does not see them.\n"
            "Replace the file with the correct job's cutlist and it will re-index automatically."
        ))
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            doc_type = str(entry.get("docType") or "?")
            pdf_filename = str(entry.get("pdfFilename") or "?")
            expected_job = str(entry.get("expectedJob") or "?")
            found_job = str(entry.get("foundJob") or "?")
            layout.addWidget(QLabel(
                f"• {doc_type}: '{pdf_filename}' shows job {found_job} (expected {expected_job})"
            ))

        action_row = QHBoxLayout()
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.clicked.connect(dialog.accept)
        action_row.addStretch()
        action_row.addWidget(dismiss_btn)
        layout.addLayout(action_row)
        dialog.exec()
```

- [ ] **Step 3: Wire it into the double-click handler**

In `_open_selected_job_dialog` (currently lines 896-905), check for mismatch flags after the existing
duplicate check and before falling through to the normal pending-job dialog:

```python
    def _open_selected_job_dialog(self, *args):
        job_folder_name = self._selected_job_folder_name()
        if not job_folder_name:
            return
        from .duplicate_job_guard import read_duplicate_suspect_marker
        marker = read_duplicate_suspect_marker(self.config.ROOT_DIR, job_folder_name)
        if marker is not None:
            self._show_duplicate_job_dialog(job_folder_name, marker)
            return
        from .cutlist_job_mismatch import read_job_mismatch_flags
        mismatch_payload = read_job_mismatch_flags(self.config.ROOT_DIR, job_folder_name)
        if isinstance(mismatch_payload, dict) and mismatch_payload.get("mismatches"):
            self._show_cutlist_mismatch_job_dialog(job_folder_name, mismatch_payload)
            return
        self._show_pending_job_prompt_dialog(job_folder_name)
```

- [ ] **Step 4: Headless import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "from ready_jobs_watcher.gui import SettingsWindow; print('ok')"`
Expected: prints `ok`, no traceback

- [ ] **Step 5: Commit**

```bash
git add ready_jobs_watcher/gui.py
git commit -m "feat: show cutlist job-mismatch badge and detail dialog in Jobs tab"
```

---

### Task 9: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest`
Expected: PASS except the 3 pre-existing `test_dae_converter.py` failures noted in `CLAUDE.md`
(missing optional `mapbox_earcut`, environmental, unrelated to this change)

- [ ] **Step 2: Headless GUI import smoke check**

Run: `QT_QPA_PLATFORM=offscreen python -c "from ready_jobs_watcher.gui import SettingsWindow; from ready_jobs_watcher.main import Application; print('ok')"`
Expected: prints `ok`, no traceback

- [ ] **Step 3: Manual walkthrough against the live `530a - DC BIGLEY` job**

`530a - DC BIGLEY`'s real PDFs are placeholder stubs (fixed earlier today), so they won't exercise
this feature directly. Use a scratch copy instead:

1. Copy any real job's Nailer Cut List PDF (e.g. `656 - KENT WITHAM\656 - Nailer Cut List.pdf`) into a
   throwaway test job folder under the watched root, renamed to that folder's job-number prefix (the
   watcher auto-renames on drop, but the PDF's own internal job line still reads `656 - KENT WITHAM -
   ...`).
2. Start the app (`python -m ready_jobs_watcher`) pointed at a root containing that throwaway folder,
   or trigger a manual re-parse from the Jobs tab.
3. Confirm: the always-on-top "CUTLIST JOB MISMATCH" popup appears; `ready_jobs_watcher.log` has a
   `Hardwoods parse skipped (job mismatch): ...` line; `<job>\.metadata\hardwoods\cutlist_job_mismatch.json`
   exists; `<job>\.metadata\hardwoods\cutlist_index.json` does NOT contain a `NAILER_CUT_LIST` entry;
   the Jobs tab row shows the mismatch badge; double-clicking the row shows the detail dialog.
4. Replace the file with a cutlist that actually matches the folder's job number, trigger a re-parse,
   and confirm the flag file is removed and the badge clears.
5. Delete the throwaway test job folder when done.

- [ ] **Step 4: Confirm no unrelated regressions**

Run: `git status --short` and `git diff --stat master` (or the appropriate base branch) to confirm
only the files listed in this plan's tasks changed.
