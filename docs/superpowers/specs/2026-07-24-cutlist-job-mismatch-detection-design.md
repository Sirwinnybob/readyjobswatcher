# Detect And Block Wrong-Job Hardwoods Cutlist PDFs

## Background

`hardwoods_cutlist_indexer.py` parses the five hardwoods cutlist PDF types (Face Frame, Nailer,
Door Cut, Door List, Closet Rod) out of a job's root folder and writes
`.metadata\hardwoods\cutlist_index.json`, which KKCSheetTracker tablets read on the production
floor. If someone drops the wrong job's cutlist into a job folder — or two cutlists get name-swapped
during export/copy — today's indexer has no way to notice. It will happily parse whatever PDF is
sitting there (as long as the pipe-table template matches) and publish it as that job's cutlist. A
tablet on the shop floor would then show cut dimensions for a different job, with no warning
anywhere.

Investigation into today's earlier fix (placeholder-stub PDFs wrongly logging as template-mismatch
errors — see `hardwoods_cutlist_indexer.py:225`) surfaced that every cutlist PDF already prints its
own job identity on page 1:

- Face Frame / Nailer / Door Cut / Closet Rod Cut List: second line of the page, e.g.
  `656 - KENT WITHAM - HICKORY, SHAKER DOORS - 16 July, 2026` or
  `332 - ROSS KAUFMAN - HAMMOND - RUSTIC WHITE OAK & PAINT GRADE, SHAKER - 6 July, 2026`.
- Door List: a distinct header line, `Job: BEECH-NEW BEVEL (582)` — job number in parens.

This is a reliable, already-present signal — no OCR or new export change needed. The one wrinkle:
Cabinet Vision drops letter suffixes on split jobs. Folder `616b - KEVIN JANNI 1711 DUKE ST` exports
cutlists that print `616` (not `616b`) as the job number. A naive exact-string compare would flag
every suffixed job as a false positive. Confirmed with the user: suffix letters should only be
compared when **both sides have one** — `616` (folder) vs `616b` (PDF) is fine, but `616a` (folder)
vs `616b` (PDF) is a real mismatch.

## Fix: extract each cutlist's printed job identity, compare to the folder, block and flag on mismatch

### Components

- **New `ready_jobs_watcher/cutlist_job_mismatch.py`** — pure parsing/comparison logic, no PyMuPDF
  or PyQt dependency, independently unit-testable with plain strings:
  - `JobIdentifier` — small dataclass/namedtuple: `number: str`, `suffix: Optional[str]` (single
    uppercase letter or `None`).
  - `parse_job_identifier(token: str) -> Optional[JobIdentifier]` — matches `^(\d+)([A-Za-z]?)$`.
    Returns `None` for anything else (e.g. dash-joined job numbers like `123-4`), which is a
    deliberate fail-open: if the token shape isn't the simple `<digits><optional letter>` case this
    feature was designed for, skip the check entirely rather than guess.
  - `folder_job_identifier(job_folder_name: str) -> Optional[JobIdentifier]` — reuses
    `file_handler.JobProcessor.extract_job_number` (already the canonical folder-name-to-job-number
    extractor used for file prefixing) and feeds its result through `parse_job_identifier`.
  - `extract_pdf_job_identifier(doc_type: str, first_lines: List[str]) -> Optional[JobIdentifier]` —
    takes the first ~6 non-empty line-texts of page 1 (caller-supplied, so this module never touches
    fitz). For `DOC_TYPE_DOOR_LIST`, searches for `Job:\s*.*\((\d+[A-Za-z]?)\)`. For every other doc
    type, searches for a line starting with `<token> - ` (`^(\d+[A-Za-z]?)\s*-\s+\S`). Returns `None`
    if no line matches — fail-open again, e.g. an unrecognized future template shouldn't block
    indexing on a guess.
  - `is_job_mismatch(folder_id: JobIdentifier, pdf_id: JobIdentifier) -> bool` — `True` if base
    numbers differ; if numbers match, `True` only when both suffixes are present and differ; `False`
    otherwise (covers the 616-vs-616b case).

- **`ready_jobs_watcher/hardwoods_cutlist_indexer.py`** changes:
  - New `JobMismatchError(Exception)` alongside the existing `TemplateMismatchError` /
    `SkippableDocumentError`, carrying `expected: JobIdentifier` and `found: JobIdentifier`.
  - `_parse_document_rows` gains a `job_folder_path: str` parameter (its one call site, inside
    `build_hardwoods_cutlist_index_for_job`, already has this value available). In the existing
    page-1 checks block (right after the placeholder/legacy-template checks, so a placeholder stub
    never triggers a mismatch alert), extract the PDF's job identifier from the first 6 line-texts
    (built via the existing `_line_text` helper over `rows_by_y[:6]`) and the folder's job identifier
    from `job_folder_path`. If both resolve and `is_job_mismatch(...)` is `True`, raise
    `JobMismatchError`.
  - `build_hardwoods_cutlist_index_for_job` gains a new `except JobMismatchError as e:` branch,
    parallel to the existing `TemplateMismatchError` branch: logs
    `main_logger.error("Hardwoods parse skipped (job mismatch): %s (expected %s, found %s)", ...)`,
    records the mismatch (see Storage below), invokes an optional `on_job_mismatch` callback, and
    `continue`s to the next doc type — exactly like today's other skip paths, so a mismatched Nailer
    Cut List doesn't stop Face Frame/Door Cut/etc. in the same job from indexing normally.
  - `build_hardwoods_cutlist_index_for_job` gains an `on_job_mismatch: Optional[Callable[[dict], None]]`
    parameter (same shape/spirit as the existing `deployment_gate` parameter — an optional collaborator
    threaded through from the caller, not a hard dependency). Called once per newly-detected mismatch
    with a plain dict payload (job folder name, doc type, pdf filename, expected/found job strings,
    timestamp) — no Qt types cross into this module.

### Storage: persistent per-job flag file

`<job>\.metadata\hardwoods\cutlist_job_mismatch.json`, RJW-owned, written via the existing shared
`_shared_atomic_write_json` helper (same atomicity guarantee as `cutlist_index.json` and
`cutlist_revisions.json`). Rewritten from scratch on every `build_hardwoods_cutlist_index_for_job`
call for that job:

```json
{
  "updatedAt": "2026-07-24T14:03:00+00:00",
  "mismatches": [
    {
      "docType": "NAILER_CUT_LIST",
      "pdfFilename": "530a - Nailer Cut List.pdf",
      "expectedJob": "530a",
      "foundJob": "532",
      "detectedAt": "2026-07-24T14:03:00+00:00"
    }
  ]
}
```

Self-healing by construction: each rebuild only writes entries for doc types that are *currently*
mismatched. Once the wrong PDF is replaced with the correct one (or removed), the next rebuild finds
no mismatch for that doc type and it drops out of the list — no acknowledgement/dismiss state to
track or get stuck. If `mismatches` would be empty, the file is removed (mirrors
`_remove_index_if_exists`'s pattern for the main index) rather than written as `{"mismatches": []}`,
so "no file" and "no problem" stay the same thing.

### Alerting

Mirrors the existing bad-parts-alert plumbing (`AlertCoordinator` / `AlertSignal` /
`_show_bad_parts_alert_dialog` in `gui.py`), which already solves "background thread detects a
problem, main-thread Qt dialog needs to show it":

- `main.py` (or wherever `build_hardwoods_cutlist_index_for_job` is invoked from the watcher/scheduler
  paths) passes an `on_job_mismatch` callback that forwards the payload into a new
  `JobMismatchSignal(QObject)` (`pyqtSignal(object)`), following the same construction pattern as
  `AlertSignal`/`AutoReleaseSignal` in `gui.py`.
- `gui.py` connects that signal to a new `_show_cutlist_mismatch_alert_dialog(payload)`: an
  always-on-top `QMessageBox` — "CUTLIST JOB MISMATCH: job `<folder>`'s `<doc type>` cut list shows
  job `<found>` — expected `<expected>`. Parsing skipped; fix the file and it re-indexes
  automatically." Buttons: "View Job" (selects the row in the Jobs tab) and "Dismiss" — dismiss only
  closes the dialog, it does not touch the flag file (the flag only clears when the underlying file
  is actually fixed, per Storage above).
- **Dashboard badge**: `_populate_jobs_table` (`gui.py:832`) reads each job's
  `cutlist_job_mismatch.json` (if present) alongside the existing `derive_state` gate-state read, and
  renders a visible warning badge/color on that job's row — so a missed popup (app not focused,
  restarted since) still surfaces the problem next time the Jobs tab is viewed. The existing
  double-click state-aware job dialog (`_open_selected_job_dialog`) shows the mismatch details
  (doc type, expected vs. found) when the flag file is present.

### Error handling

- Both `parse_job_identifier` and `extract_pdf_job_identifier` fail open (return `None`) on anything
  they don't confidently recognize. `_parse_document_rows` only raises `JobMismatchError` when
  *both* the folder and the PDF resolve to a `JobIdentifier` and they actually disagree — an
  unparseable folder name or an unrecognized page-1 layout silently skips the check, never blocks
  indexing on a guess.
- Flag-file read/write failures follow the same pattern as every other sidecar in this module
  (`_write_index`, `_write_revision_state`): caught, logged at `warning`, non-fatal — a failure to
  persist the flag never prevents the (correct) behavior of skipping the mismatched doc's rows.
- The `on_job_mismatch` callback call is wrapped in try/except inside the indexer, same as
  `deployment_gate.should_process_job_folder` calls elsewhere — a GUI-side callback failure must
  never break index building.

### Testing

- **New `tests/test_cutlist_job_mismatch.py`**:
  - `parse_job_identifier`: plain number, number+letter, dash-joined number (→ `None`), garbage (→
    `None`).
  - `extract_pdf_job_identifier`: both header formats (cut-list-style and Door-List-style), no match
    found (→ `None`), doc-type title line correctly not mistaken for the job line.
  - `is_job_mismatch` truth table: same number/no suffix either side → `False`; same number, folder
    has suffix PDF doesn't (616b/616) → `False`; same number, both have same suffix → `False`; same
    number, both have different suffix (616a/616b) → `True`; different number → `True`.
- **Additions to `tests/test_hardwoods_cutlist_indexer.py`**:
  - A doc whose page-1 job line doesn't match the job folder gets excluded from
    `cutlist_index.json`, while a sibling correctly-matching doc in the same job still indexes.
  - `cutlist_job_mismatch.json` is written with the right shape when a mismatch is found, and is
    removed on a subsequent rebuild once the PDF is fixed.
  - The 616-vs-616b (folder has no suffix, PDF does — and the reverse) cases both parse cleanly,
    proving the suffix rule doesn't false-positive on real Cabinet Vision export behavior.
  - `on_job_mismatch` callback fires exactly once per newly-detected mismatched doc, and is not
    called again on a subsequent rebuild if the same mismatch is still present but was already
    reported (avoids re-alerting every debounce cycle for an unfixed file) — accomplished by only
    invoking the callback for doc types not already present in the previous flag file's
    `mismatches`, mirroring how `_reconcile_rows_with_previous_index` already compares against the
    previously-written state.
- GUI changes (`_show_cutlist_mismatch_alert_dialog`, Jobs-tab badge) are verified via the existing
  project convention: headless import smoke check (`QT_QPA_PLATFORM=offscreen`) plus manual
  walkthrough — `gui.py` is not unit-tested per `CLAUDE.md`.

### Out of scope

- Jobs whose folder name doesn't reduce to the simple `<digits><optional single letter>` shape (e.g.
  dash-joined job numbers) are not checked — `folder_job_identifier` returns `None` and the check is
  skipped for that job entirely. No attempt to special-case that format in this change.
- No change to `TemplateMismatchError` / `SkippableDocumentError` handling for any existing case —
  this adds a third, independent failure mode checked after those two, not a replacement.
- No content-level cross-check beyond the printed job number (e.g. comparing customer name text) —
  confirmed with the user that number-only is the desired signal; name-text matching is noisier and
  not needed.
- No changes to non-hardwoods documents (Assembly Sheets, Delivery Sheets, Plans & Elevations, CNC
  sidecars) — scope is the five hardwoods cutlist doc types already parsed by this indexer.
- No acknowledgement/dismiss persistence for the flag file — confirmed self-healing (fixed file →
  flag clears on next rebuild) is the desired behavior, not a manual-clear workflow.
