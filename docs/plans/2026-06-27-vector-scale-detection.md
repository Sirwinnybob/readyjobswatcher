# Vector Scale Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatically detect a job PDF's real-world drawing scale (pdf-points-per-inch) from its embedded vector dimension-line geometry, and publish the result per-page into `cabinet_sheet_index.json` so KKCSheetTracker's PdfMarkup viewer can auto-calibrate instead of having no scale concept at all (see the companion plan `2026-06-27-pdf-markup-auto-calibration.md` in the KKCSheetTracker repo).

**Origin / reference implementation:** The detection algorithm below was designed and empirically validated against real shop PDFs in a standalone Node/pdfjs-dist script at `C:\Scripts\Measure2Scale\scratch\detect-scale.js` (and its sibling fix in `C:\Scripts\Measure2Scale\src\pdf-manager.ts`'s `extractVectorSegments()`). That script is the algorithm spec for this plan — **do not** add a Node.js dependency to this Python service; port the logic to PyMuPDF (`fitz`), which this codebase already uses throughout `cabinet_sheet_indexer.py`. Coordinate systems differ (pdf.js content-stream space is y-up; PyMuPDF's `page.get_drawings()`/`get_text()` use a y-down, top-left-origin space) but the algorithm only depends on *relative* geometry (segment length, axis-alignment, endpoint proximity), so this doesn't change any of the logic — only which axis a "vertical" check ends up named, which is purely cosmetic.

**Validation already done (informs the design below):**
- Tested against `617 - HARSHBARGER 2306 PARK GROVE\617 - PLANS & ELEVATIONS.pdf` and `564 - PASQUARELLI 2865 EMERALD ST\564 - PLANS & ELEVATIONS.pdf`: tick-marked dimension lines on Plans & Elevations sheets are reliably detected, with strong multi-candidate consensus (e.g. 12/16, 19/27, 11/12 candidates agreeing within 2% per page).
- Tested against 4 jobs' `ASSEMBLY SHEETS.pdf` files (Harshbarger, Pasquarelli, Rodd Hansen Stucky, Blankenship): coverage is **much lower and uneven** — only 20%–82% of pages yielded any candidate at all (vs. near-universal on Plans & Elevations). Two root causes found:
  1. Assembly sheets typically draw only **one** tick-marked dimension line per part (e.g. just the width); length/thickness are conveyed as title-block text, not drawn dimension lines — so there's often only 1 candidate on a page, with no independent cross-check.
  2. Some assembly-sheet detail/profile views (e.g. end-grain/scribe views) use **arrowhead-style** dimension indicators (`◄──24──►`) instead of perpendicular tick marks. The current algorithm only recognizes the tick-mark convention and correctly reports "no candidates" on these pages rather than guessing — a safe failure, but it means assembly-sheet coverage will stay partial unless arrowhead detection is added later (explicitly out of scope for this plan).
- Found and fixed a real bug in `extractVectorSegments()`'s `pdfjs-dist` 6.x path decoding (Measure2Scale-only; not applicable to this PyMuPDF codebase, but noted here so nobody re-discovers it independently).
- Found that dimension-line numbers consistently use one dedicated font on the page (e.g. `g_d0_f3`), distinct from cabinet labels, room names, and even cutlist-table numbers elsewhere on the same page — confirmed across multiple pages and two different jobs. Using this as a confirmation signal (only match tick-confirmed lines to text in that font) measurably reduced false positives in testing (e.g. one job's page-1 candidate count dropped from 30→27 with the same 19 correct matches, i.e. pure noise removal).

**Architecture:** Add a new module `ready_jobs_watcher/scale_detector.py` implementing the ported algorithm, called from `cabinet_sheet_indexer.py`'s page-detail extraction for both `plansElevations` and `assembly` documents (assembly results explicitly flagged lower-confidence per the coverage data above — do not let assembly-sheet noise degrade trust in plans-elevations results). Bump `cabinet_sheet_index.json`'s `schemaVersion` to `3` and add a `scale` field to each page's `pageDetails` entry.

**Tech Stack:** Python, PyMuPDF (`fitz`), existing `cabinet_sheet_indexer.py` pipeline.

---

### Task 1: Extract vector segments and text spans with font identity via PyMuPDF

**Files:**
- Create: `ready_jobs_watcher/scale_detector.py`
- Create: `tests/test_scale_detector.py`

**Step 1: Write the failing test**

```python
import fitz
from ready_jobs_watcher.scale_detector import extract_segments, extract_text_spans

def test_extract_segments_and_text_from_real_pdf():
    doc = fitz.open(r"Y:\Ready Jobs\617 - HARSHBARGER 2306 PARK GROVE\617 - PLANS & ELEVATIONS.pdf")
    page = doc[0]
    segments = extract_segments(page)
    spans = extract_text_spans(page)
    assert len(segments) > 1000  # this page has ~3539 line segments per the JS reference run
    assert any(s["text"] == "60" for s in spans)
    doc.close()
```

If `Y:\Ready Jobs` isn't mounted in CI, mark this test `@pytest.mark.skipif` on the path not existing, matching how other tests in this repo guard against the live share (check `tests/test_hardwoods_cutlist_indexer.py` for the existing pattern).

**Step 2: Run test to verify it fails**
Run: `.venv\Scripts\pytest tests/test_scale_detector.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`.

**Step 3: Write minimal implementation**

`extract_segments(page) -> list[dict]`: use `page.get_drawings()`. Each drawing item has an `"items"` list of `("l", p1, p2)` (line), `("c", p1, p2, p3, p4)` (cubic bezier — approximate as a straight line `p1`→`p4`, matching the JS reference's curve handling), and `("re", rect)` (rectangle — expand to its 4 edges, matching `OPS.rectangle` handling in the JS reference). Also capture `drawing["color"]` and `drawing["width"]` (stroke color/width) per segment — not used for detection yet, but capture now since Task 1 of the JS reference's follow-on investigation found these only *weakly* correlate with dimension lines (shared with unrelated thin border lines), so don't gate on them, just keep them available for future tuning.

`extract_text_spans(page) -> list[dict]`: use `page.get_text("dict")["blocks"]` → `["lines"]` → `["spans"]`. Each span has `"text"`, `"origin"` (x, y), and **`"font"`** — PyMuPDF gives the real font name directly here (e.g. `"CIDFont+F3"` per the embedded-font names found in the reference investigation), no alias resolution needed, which is actually simpler than the pdf.js `fontName`/`commonObjs` lookup the JS reference had to do.

**Step 4: Run test to verify it passes**
Run: `.venv\Scripts\pytest tests/test_scale_detector.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add ready_jobs_watcher/scale_detector.py tests/test_scale_detector.py
git commit -m "feat: extract vector segments and text spans for scale detection"
```

---

### Task 2: Port tick/run/candidate detection and consensus voting

**Files:**
- Modify: `ready_jobs_watcher/scale_detector.py`
- Modify: `tests/test_scale_detector.py`

**Step 1: Write the failing test**

```python
def test_detect_scale_on_harshbarger_page1():
    doc = fitz.open(r"Y:\Ready Jobs\617 - HARSHBARGER 2306 PARK GROVE\617 - PLANS & ELEVATIONS.pdf")
    page = doc[0]
    result = detect_page_scale(page)
    assert result is not None
    assert result["agreeing"] >= 10
    assert abs(result["pdfPointsPerInch"] - 1.873) < 0.01
    doc.close()
```

This exact expected ratio (1.873 pt/in, 12/16 candidates agreeing) was measured directly against this file with the JS reference implementation — use it as the ground-truth assertion for the port.

**Step 2: Run test to verify it fails**
Run: `.venv\Scripts\pytest tests/test_scale_detector.py -v`
Expected: FAIL (`detect_page_scale` doesn't exist yet).

**Step 3: Write minimal implementation**

Port these functions from `detect-scale.js` 1:1 (same thresholds — they were empirically tuned against real sheets, don't re-derive them):
- `classify_segments`: split into `long_segs` (axis-aligned, length ≥ `LONG_MIN_LEN=10pt`) and `tick_segs` (diagonal, `1.0pt ≤ length ≤ 10pt`).
- `find_tick_near`: nearest tick within `TICK_ENDPOINT_TOL=2.5pt` of a point.
- `build_runs`: group collinear long segments sharing an axis coordinate (`AXIS_GROUP_TOL=0.5pt`), merging pieces broken by a text gap (`MAX_TEXT_GAP=30pt`) **only when there's no tick at the boundary** — this is the fix that resolved the original "84 vs 60 ratio mismatch" found during the JS prototype's exploration; don't skip it, it's load-bearing.
- `build_candidates`: a run whose two outer ends both land in a tick cluster, matched to the nearest dimension-shaped text within `TEXT_SEARCH_RADIUS=40pt` of a gap-center anchor (or run midpoint if unbroken).
- `compute_numeric_fonts`: group text spans by font name; any font whose entire set of strings all parse as a plain number or feet-inches pattern is treated as the page's dimension-text font. When non-empty, `build_candidates` must only match text in that font set — this is the false-positive-elimination signal found during testing (cutlist-table numbers in a different font no longer get mismatched to drawing lines).
- `compute_consensus`: median ratio, agreement within `CONSENSUS_TOLERANCE=0.02` (2%).
- `detect_page_scale(page) -> dict | None`: ties the above together, returns `None` when no candidates exist (e.g. cover sheets, or assembly-sheet pages using arrowhead-style dimensioning), otherwise `{"pdfPointsPerInch": ratio, "agreeing": n, "total": m, "candidates": [...]}`.

Treat each page **independently** — never average across pages. A multi-page job PDF routinely mixes an overall floor plan with zoomed-in detail views at a different scale.

**Step 4: Run test to verify it passes**
Run: `.venv\Scripts\pytest tests/test_scale_detector.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add ready_jobs_watcher/scale_detector.py tests/test_scale_detector.py
git commit -m "feat: port tick-mark dimension-line detection and consensus voting"
```

---

### Task 3: Wire scale detection into the cabinet/sheet index

**Files:**
- Modify: `ready_jobs_watcher/cabinet_sheet_indexer.py`
- Modify: `tests/test_hardwoods_cutlist_indexer.py` or create `tests/test_cabinet_sheet_indexer_scale.py`

**Step 1: Write the failing test**

```python
def test_reference_index_includes_scale_for_plans_page(tmp_path):
    # set up a job folder with a real PLANS & ELEVATIONS.pdf fixture (or the live share path)
    build_reference_index_for_job(job_folder)
    index = json.loads((job_folder / ".metadata" / "cabinet_sheet_index.json").read_text())
    assert index["schemaVersion"] == 3
    page1 = index["documents"]["plansElevations"]["pageDetails"]["1"]
    assert "scale" in page1
    assert page1["scale"]["pdfPointsPerInch"] > 0
```

**Step 2: Run test to verify it fails**
Run: `.venv\Scripts\pytest tests/test_cabinet_sheet_indexer_scale.py -v`
Expected: FAIL (`schemaVersion` still `2`, no `scale` key).

**Step 3: Write minimal implementation**

1. Bump `payload["schemaVersion"]` from `2` to `3` in `build_reference_index_for_job` ([cabinet_sheet_indexer.py:825](ready_jobs_watcher/cabinet_sheet_indexer.py:825)).
2. In `_parse_plans_pdf` ([cabinet_sheet_indexer.py:440](ready_jobs_watcher/cabinet_sheet_indexer.py:440)) and `_parse_assembly_pdf` ([cabinet_sheet_indexer.py:315](ready_jobs_watcher/cabinet_sheet_indexer.py:315)), call `scale_detector.detect_page_scale(doc[page_index])` per page and add the result to each page's `page_details[str(page_num)]` dict under the key `"scale"`:
   ```python
   scale_result = detect_page_scale(doc[page_index])
   page_details[str(page_num)] = {
       ...,  # existing keys unchanged
       "scale": {
           "pdfPointsPerInch": round(scale_result["pdfPointsPerInch"], 4),
           "agreeing": scale_result["agreeing"],
           "total": scale_result["total"],
       } if scale_result else None,
   }
   ```
   Omit/null when `scale_result is None` — this matches the existing nullable pattern already used for `room`/`wall` in this same dict, so KKCSheetTracker's Gson parsing needs no special-casing.
3. Do **not** add scale detection to `_parse_delivery_pdf_metadata` — delivery sheets aren't drawings and weren't tested.
4. Mark assembly-sheet `scale` results as lower-confidence in the payload (e.g. an explicit `"sourceType": "ASSEMBLY"` vs `"PLANS_ELEVATIONS"` alongside the scale block, or simply document the asymmetric reliability in this file's module docstring) so any future consumer can choose to prefer/require the plans-elevations value when both exist for the same cabinet — per the coverage numbers above, assembly-sheet scale should be treated as a weak hint, not a primary source.

**Step 4: Run test to verify it passes**
Run: `.venv\Scripts\pytest tests/test_cabinet_sheet_indexer_scale.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add ready_jobs_watcher/cabinet_sheet_indexer.py tests/test_cabinet_sheet_indexer_scale.py
git commit -m "feat: publish detected page scale into cabinet_sheet_index.json"
```

---

### Task 4: Performance guard for large multi-page jobs

**Files:**
- Modify: `ready_jobs_watcher/scale_detector.py`

**Step 1: Write the failing test**

```python
def test_scale_detection_skips_huge_documents_gracefully():
    # a 50+ page assembly sheet PDF (Pasquarelli's is 31 pages, Blankenship's is 57)
    # should not block the watcher's debounced refresh path for an unreasonable time
    import time
    start = time.monotonic()
    doc = fitz.open(r"Y:\Ready Jobs\587 - BLANKENSHIP 38984 DEXTER\587 - ASSEMBLY SHEETS.pdf")
    for page in doc:
        detect_page_scale(page)
    elapsed = time.monotonic() - start
    assert elapsed < 30  # generous ceiling; tune once real timings are known
    doc.close()
```

**Step 2: Run test to verify it fails or passes as a baseline**
Run: `.venv\Scripts\pytest tests/test_scale_detector.py -v -k huge`
Record the actual elapsed time — this test's main purpose is to establish a baseline so a future regression is caught, not to enforce an arbitrary number on day one.

**Step 3: Write minimal implementation (only if the baseline is concerning)**

If detection is slow on large documents, this work already runs inside `build_reference_index_for_pdf_event`, which is itself invoked from the debounced PDF-watcher path (`watchers.py`'s `PdfChangeHandler`) — it does not need to be synchronous with the file-system event. No new async plumbing should be added speculatively; only add it if the baseline measurement shows a real problem.

**Step 4: Commit (only if changes were made)**
```bash
git add ready_jobs_watcher/scale_detector.py tests/test_scale_detector.py
git commit -m "perf: guard scale detection against pathological multi-page documents"
```

---

## Explicitly out of scope for this plan

- Arrowhead-style (`◄──24──►`) dimension-line detection — confirmed missing on at least one real assembly-sheet page; a real gap, but a separate, self-contained follow-up.
- Any sidecar file other than `cabinet_sheet_index.json` — no new file format, reuses the existing one.
- Anything on the KKCSheetTracker/Android side — see the companion plan `2026-06-27-pdf-markup-auto-calibration.md` in that repo.
- Cross-page or cross-document scale averaging — each page's scale is independent, by design.
