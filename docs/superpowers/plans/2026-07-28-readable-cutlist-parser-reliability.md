# Readable Cut List Parser Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the three 3.0 cut lists readable while making Width authoritative for rip grouping and rejecting incomplete readable-table parses before they create bad metadata.

**Architecture:** CABINET VISION keeps its normal Material headers and line-item table. The three templates add only `W:` and `L:` prefixes in their existing dimension cells. RJW validates the readable 3.0 contract and aggregates board stock from each row's printed Width and Length, never from Totals.

**Tech Stack:** Python 3, PyMuPDF, pytest, SQL Server, CABINET VISION List & Label.

## Global Constraints

- Change only templates 86, 87, and 88, mapped to reports 281, 282, and 283.
- Keep normal readable rows. Do not add `RJW-*` text, helper lines, hidden columns, or rearranged columns.
- Use parsed Width exactly for `board_stock_rows.width`; never swap dimensions by numeric size.
- Ignore Totals for board-stock aggregation.
- Preserve 2.0 behavior and matching 3.0-title compatibility.
- Raise `TemplateMismatchError` for a malformed 3.0 readable-table contract instead of writing a partial hardwoods index.
- Preserve unrelated dirty files.

---

## File Structure

- `ready_jobs_watcher/metadata_cache.py`: aggregates parsed rows into board-stock rows.
- `ready_jobs_watcher/hardwoods_cutlist_indexer.py`: validates titles, Material headers, table headers, and parsed rows.
- `tests/test_metadata_cache.py`: proves Width authority and Totals isolation.
- `tests/test_hardwoods_cutlist_indexer.py`: proves readable 3.0 contract enforcement.
- `CVReport.dbo.ReportTemplates`: stores the three readable `W:`/`L:` cell formulas.

### Task 1: Keep Printed Width Authoritative

**Files:**

- Modify: `ready_jobs_watcher/metadata_cache.py:302-347`
- Modify: `tests/test_metadata_cache.py:131-164`

**Interfaces:**

- Consumes: row dictionaries with `material`, `width`, `length`, and `qty`.
- Produces: `build_board_stock_rows(...)` rows where `normalizedWidth == float(input_row["width"])` and footage uses `input_row["length"]`.

- [ ] **Step 1: Write the failing orientation test**

```python
def test_board_stock_rows_use_the_width_column_even_when_it_is_larger_than_length(tmp_path):
    index = {"documents": [{"docType": "DOOR_CUT_LIST", "rows": [{
        "material": "3/4 Solid White Oak Rift", "width": "69.0625",
        "length": "3.0625", "qty": 1,
    }], "totals": []}]}
    rows = build_board_stock_rows(tmp_path / "123 - Test Job", index)
    assert rows[0]["normalizedWidth"] == 69.0625
    assert rows[0]["width"] == "69.0625"
    assert rows[0]["totalFeet"] == 3.0625 / 12.0
```

- [ ] **Step 2: Run the test to verify RED**

Run: `python -m pytest tests/test_metadata_cache.py::test_board_stock_rows_use_the_width_column_even_when_it_is_larger_than_length -q`

Expected: FAIL because current code uses `min(width, length_inches)`.

- [ ] **Step 3: Write the minimal aggregation change**

```python
rip_width = width
rip_length_inches = length_inches
feet = (rip_length_inches * quantity) / 12.0
```

Do not alter the Totals-ignore loop, manual stock, grouping, or parsing.

- [ ] **Step 4: Run the focused board-stock tests to verify GREEN**

Run: `python -m pytest tests/test_metadata_cache.py::test_board_stock_rows_use_detail_rows_when_totals_cross_a_material_page_break tests/test_metadata_cache.py::test_board_stock_rows_use_the_width_column_even_when_it_is_larger_than_length -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated change**

Stage `ready_jobs_watcher/metadata_cache.py` and `tests/test_metadata_cache.py`, then commit with message `fix: keep cutlist width authoritative`.

### Task 2: Reject Incomplete Readable 3.0 Tables

**Files:**

- Modify: `ready_jobs_watcher/hardwoods_cutlist_indexer.py:788-958`
- Modify: `tests/test_hardwoods_cutlist_indexer.py:53-100`

**Interfaces:**

- Consumes: `_parse_document_rows(doc_type, pdf_path, job_folder_path)` page words.
- Produces: material-scoped rows only when a 3.0 PDF has its matching title, a Material header, a recognized table header, and no unparsed row-like detail lines; otherwise raises `TemplateMismatchError`.

- [ ] **Step 1: Write the missing-Material RED test**

```python
def test_v3_cutlist_without_a_material_header_is_rejected(tmp_path, monkeypatch):
    job_dir = tmp_path / "123 - Test Job"
    job_dir.mkdir()
    face_frame = job_dir / "Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")
    words = [_w(74, 100, "Face"), _w(108, 100, "Frame"), _w(150, 100, "Cut"), _w(174, 100, "List"), _w(210, 100, "3.0")]
    words += _std_header(160) + _std_row(182, 1, "Top Rail", "3", "56.5", "30")
    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=words)]))
    with pytest.raises(indexer.TemplateMismatchError, match="material"):
        indexer._parse_document_rows(indexer.DOC_TYPE_FACE_FRAME, str(face_frame), str(job_dir))
```

- [ ] **Step 2: Run the test to verify RED**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py::test_v3_cutlist_without_a_material_header_is_rejected -q`

Expected: FAIL with `DID NOT RAISE`.

- [ ] **Step 3: Add scoped 3.0 contract checks**

```python
is_v3_cutlist = bool(re.search(r"\b3\.0\b", title_line))
saw_material_marker = False
row_gap_pages: List[int] = []

# Per page: set saw_material_marker when markers exists; append the page
# when row_like_count > len(page_rows).
# Before return, only when is_v3_cutlist:
if not saw_material_marker:
    raise TemplateMismatchError("3.0 cut list material header not detected")
if row_gap_pages:
    raise TemplateMismatchError(f"3.0 cut list has unparsed detail rows on pages {row_gap_pages}")
if not rows:
    raise TemplateMismatchError("3.0 cut list has no valid detail rows")
```

Keep existing warning logs and all 2.0 behavior.

- [ ] **Step 4: Add the valid 3.0 control test and verify GREEN**

```python
def test_v3_cutlist_with_material_header_and_table_rows_parses(tmp_path, monkeypatch):
    job_dir = tmp_path / "123 - Test Job"
    job_dir.mkdir()
    face_frame = job_dir / "Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")
    words = [_w(74, 100, "Face"), _w(108, 100, "Frame"), _w(150, 100, "Cut"), _w(174, 100, "List"), _w(210, 100, "3.0")]
    words += [_w(74, 145, "Material:"), _w(124, 145, "'3/4"), _w(160, 145, "Maple'"), _w(230, 145, "|"), _w(240, 145, "Units:"), _w(280, 145, "BD"), _w(300, 145, "FT"), _w(320, 145, "|")]
    words += _std_header(160) + _std_row(182, 1, "Top Rail", "3", "56.5", "30")
    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=words)]))
    _, rows, _ = indexer._parse_document_rows(indexer.DOC_TYPE_FACE_FRAME, str(face_frame), str(job_dir))
    assert len(rows) == 1
    assert rows[0]["material"] == "3/4 Maple"
```

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py::test_v3_cutlist_without_a_material_header_is_rejected tests/test_hardwoods_cutlist_indexer.py::test_v3_cutlist_with_material_header_and_table_rows_parses -q`

Expected: PASS.

- [ ] **Step 5: Run parser tests and commit**

Run: `python -m pytest tests/test_hardwoods_cutlist_indexer.py -q`

Expected: PASS. Stage the parser and test file, then commit with message `fix: reject incomplete 3.0 cutlists`.

### Task 3: Add Readable `W:` and `L:` Prefixes

**Files:**

- Modify: `CVReport.dbo.ReportTemplates` IDs `86`, `87`, and `88` only.
- Verify: `CVReport.dbo.Reports` IDs `281`, `282`, and `283`.

**Interfaces:**

- Consumes: existing `Parts.Width_String` and `Parts.Length_String` cell formulas.
- Produces: unchanged table geometry with values rendered as `W: <width>` and `L: <length>`.

- [ ] **Step 1: Confirm template blast radius**

Run:

```powershell
& 'C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\sqlcmd.exe' -S .\CV24 -U claude -P SuperSecure -d CVReport -Q "SET NOCOUNT ON; SELECT r.ID,r.[Report Title],r.TemplateID,(SELECT COUNT(*) FROM Reports s WHERE s.TemplateID=r.TemplateID) AS SharedBy FROM Reports r WHERE r.ID IN (281,282,283) ORDER BY r.ID;"
```

Expected: each template is shared by exactly one report.

- [ ] **Step 2: Apply one parameterized transaction**

Require exactly one replacement per template:

```text
Text="| " + Parts.Width_String
Text=Parts.Length_String + " |"
```

becomes:

```text
Text="| W: " + Parts.Width_String
Text="L: " + Parts.Length_String + " |"
```

Roll back if any count differs. Do not alter widths, colors, headers, Material formulas, group footers, or cabinet cells.

- [ ] **Step 3: Read back template integrity**

For IDs 86, 87, and 88 require both replacement expressions and require absence of `RJW-`, `Text="Totals"`, `FFgColor`, `BkBkColor`, and `0_String`.

- [ ] **Step 4: Regenerate and inspect output**

Close and reopen CABINET VISION. Regenerate `C:\Testing\FFCL.pdf`, `C:\Testing\NCL.pdf`, and `C:\Testing\DCL.pdf`. Extract text to require `W:` and `L:` in every PDF, then render first pages to inspect for formula errors, clipped cells, duplicate lines, and shifted columns.

- [ ] **Step 5: Parse the generated PDFs**

Call `_parse_document_rows` with the matching document type for each PDF. Require nonzero row counts and confirm board-stock rows use each parsed Width directly. Do not commit database data.

## Final Verification

- [ ] Run `python -m pytest tests/test_hardwoods_cutlist_indexer.py tests/test_metadata_cache.py -q`; expect PASS.
- [ ] Run `python -m compileall -q ready_jobs_watcher`; expect exit code 0.
- [ ] Rebuild `Y:\Ready Jobs\368 - KATRINA 3484 MARILLA` after fresh readable 3.0 PDFs arrive, then verify every board-stock width is the printed Width and no Totals output contributes a row.
