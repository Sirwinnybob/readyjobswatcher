# Cutlist Job-Number Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator allow or revoke one persistent hardwoods cutlist job-number mismatch, rebuild that job immediately, and keep document-type/template mismatches blocked.

**Architecture:** `cutlist_job_mismatch.py` owns an atomically written, exact-match override ledger. The indexer checks it only after the report-title/type validation has passed, retains an `overrideActive` status entry, and indexes only an allowed job-number mismatch. `Application` owns the focused index/cache refresh; the Qt dialog calls that boundary on a background thread.

**Tech Stack:** Python 3, PyMuPDF, PyQt6, pytest, shared atomic JSON writer, metadata refresh service.

## Global Constraints

- An override applies only to `JobMismatchError`; it must never bypass `TemplateMismatchError`, placeholder checks, Readable 3.0 validation, or another parsing error.
- Exact identity is `docType`, `pdfFilename`, `expectedJob`, and `foundJob`; the record remains valid across rebuilds only while all four match.
- Required Readable 3.0 failures still return before index, revision, or mismatch-status publication.
- Override JSON uses the shared atomic writer and is deleted when no entries remain.
- Allowed mismatches remain visible in `cutlist_job_mismatch.json` as `overrideActive: true`.
- Approval/revocation rebuilds the selected job's hardwood index then directly calls `MetadataRefreshService.refresh_job_now`.
- Leave the unrelated existing edits in `ready_jobs_watcher/metadata_refresh.py`, `tests/test_metadata_refresh_scheduler.py`, and `tmp/` untouched.

---

## File Structure

- `ready_jobs_watcher/cutlist_job_mismatch.py`: exact-match override ledger.
- `ready_jobs_watcher/hardwoods_cutlist_indexer.py`: safe parser/indexer override flow.
- `ready_jobs_watcher/main.py`: focused rebuild/cache application boundary.
- `ready_jobs_watcher/gui.py`: allow/revoke controls in the Jobs mismatch dialog.
- `tests/test_cutlist_job_mismatch.py`, `tests/test_hardwoods_cutlist_indexer.py`, `tests/test_reparse_job.py`, `tests/test_gui_actions.py`: regression coverage.

## Task 1: Add the Persistent Override Ledger

**Files:**
- Modify: `ready_jobs_watcher/cutlist_job_mismatch.py:20-110`
- Test: `tests/test_cutlist_job_mismatch.py`

**Interfaces:**
- Produces `mismatch_override_path(job_folder_path: str) -> str`.
- Produces `allow_job_mismatch_override(job_folder_path: str, *, doc_type: str, pdf_filename: str, expected_job: str, found_job: str, approved_by: Optional[str] = None) -> bool`.
- Produces `remove_job_mismatch_override(job_folder_path: str, *, doc_type: str, pdf_filename: str, expected_job: str, found_job: str) -> bool` and `has_job_mismatch_override(job_folder_path: str, *, doc_type: str, pdf_filename: str, expected_job: str, found_job: str) -> bool`.
- Persists `cutlist_job_mismatch_overrides.json` as a version-1 object with an `overrides` array.

- [ ] **Step 1: Write the failing ledger tests**

```python
def test_override_matches_only_exact_document_identity(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    fields = dict(doc_type="NAILER_CUT_LIST", pdf_filename="530a - Nailer Cut List.pdf",
                  expected_job="530A", found_job="532")
    cutlist_mismatch.allow_job_mismatch_override(str(job_dir), approved_by="operator", **fields)
    assert cutlist_mismatch.has_job_mismatch_override(str(job_dir), **fields)
    assert not cutlist_mismatch.has_job_mismatch_override(
        str(job_dir), **{**fields, "found_job": "533"}
    )


def test_removing_final_override_removes_ledger_file(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    fields = dict(doc_type="NAILER_CUT_LIST", pdf_filename="530a - Nailer Cut List.pdf",
                  expected_job="530A", found_job="532")
    cutlist_mismatch.allow_job_mismatch_override(str(job_dir), **fields)
    assert cutlist_mismatch.remove_job_mismatch_override(str(job_dir), **fields)
    assert not os.path.exists(cutlist_mismatch.mismatch_override_path(str(job_dir)))
```

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_cutlist_job_mismatch.py -k override -v`

Expected: FAIL because the override API does not exist.

- [ ] **Step 3: Implement the minimal ledger**

```python
MISMATCH_OVERRIDE_FILENAME = "cutlist_job_mismatch_overrides.json"

def _override_identity(*, doc_type, pdf_filename, expected_job, found_job):
    return (str(doc_type), str(pdf_filename), str(expected_job), str(found_job))

def has_job_mismatch_override(job_folder_path: str, **identity: str) -> bool:
    wanted = _override_identity(**identity)
    return any(_override_identity(**entry) == wanted for entry in _load_override_entries(job_folder_path))
```

Use `atomic_write.atomic_write_json`, record `approvedAt` in UTC and `approvedBy` from the supplied value or `getpass.getuser()`, deduplicate identical adds, and remove the file when its entry list is empty.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_cutlist_job_mismatch.py -k override -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add ready_jobs_watcher/cutlist_job_mismatch.py tests/test_cutlist_job_mismatch.py
git commit -m "feat: persist cutlist mismatch overrides"
```

## Task 2: Index Only an Exact Approved Job-Number Mismatch

**Files:**
- Modify: `ready_jobs_watcher/hardwoods_cutlist_indexer.py:807-839,1687-1779`
- Test: `tests/test_hardwoods_cutlist_indexer.py:1882-2025`

**Interfaces:**
- Consumes `has_job_mismatch_override(job_folder_path, *, doc_type, pdf_filename, expected_job, found_job)`.
- Extends `_parse_document_rows(doc_type, pdf_path, job_folder_path, *, allow_job_mismatch=False)`.
- Emits `overrideActive: True` only for an indexed, exact approved mismatch.

- [ ] **Step 1: Write the failing behavior tests**

```python
def test_matching_override_indexes_document_and_keeps_visible_status(tmp_path, monkeypatch):
    job_dir, nailer = _make_wrong_job_nailer(tmp_path, monkeypatch)
    cutlist_mismatch.allow_job_mismatch_override(
        str(job_dir), doc_type=indexer.DOC_TYPE_NAILER, pdf_filename=nailer.name,
        expected_job="530A", found_job="532", approved_by="operator",
    )
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    assert [doc["docType"] for doc in payload["documents"]] == [indexer.DOC_TYPE_NAILER]
    assert indexer._load_existing_mismatch_flags(str(job_dir))[indexer.DOC_TYPE_NAILER]["overrideActive"] is True


def test_override_never_bypasses_mismatched_v3_report_title(tmp_path, monkeypatch):
    job_dir, pdf_path = _make_v3_face_frame_filename_with_door_title(tmp_path, monkeypatch)
    cutlist_mismatch.allow_job_mismatch_override(
        str(job_dir), doc_type=indexer.DOC_TYPE_FACE_FRAME, pdf_filename=pdf_path.name,
        expected_job="530A", found_job="532",
    )
    with pytest.raises(indexer.TemplateMismatchError):
        indexer._parse_document_rows(indexer.DOC_TYPE_FACE_FRAME, str(pdf_path), str(job_dir),
                                     allow_job_mismatch=True)
```

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_hardwoods_cutlist_indexer.py -k "matching_override or never_bypasses" -v`

Expected: FAIL because the indexer has no approved-retry path.

- [ ] **Step 3: Implement the narrow retry**

```python
def _parse_document_rows(doc_type: str, pdf_path: str, job_folder_path: str,
                         *, allow_job_mismatch: bool = False) -> Tuple[int, List[Dict], List[Dict]]:
    # The compatible report-title check remains before this condition.
    if pdf_id is not None and is_job_mismatch(folder_id, pdf_id) and not allow_job_mismatch:
        raise JobMismatchError(expected=folder_id, found=pdf_id)

# Inside build_hardwoods_cutlist_index_for_job's JobMismatchError handler:
allowed = has_job_mismatch_override(
    job_folder_path, doc_type=doc_type, pdf_filename=filename,
    expected_job=entry["expectedJob"], found_job=entry["foundJob"],
)
current_mismatches.append({**entry, **({"overrideActive": True} if allowed else {})})
if allowed:
    page_count, rows, totals = _parse_document_rows(
        doc_type, path, job_folder_path, allow_job_mismatch=True
    )
else:
    continue
```

Do not catch or downgrade `TemplateMismatchError` from the retry. It must retain the existing required-v3 early return before `_write_mismatch_flags`. Notify only for a newly blocked mismatch, not a matching approved one.

- [ ] **Step 4: Run GREEN and current mismatch regressions**

Run: `pytest tests/test_hardwoods_cutlist_indexer.py -k "job_mismatch or matching_override or never_bypasses" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add ready_jobs_watcher/hardwoods_cutlist_indexer.py tests/test_hardwoods_cutlist_indexer.py
git commit -m "feat: index approved cutlist job mismatches"
```

## Task 3: Add the Focused Application Rebuild Boundary

**Files:**
- Modify: `ready_jobs_watcher/main.py:541-715`
- Test: `tests/test_reparse_job.py`

**Interfaces:**
- Produces `Application.update_cutlist_job_mismatch_override(job_folder_name: str, *, allow: bool, doc_type: str, pdf_filename: str, expected_job: str, found_job: str) -> dict`.
- Result shape: `{"success": bool, "message": str}`.

- [ ] **Step 1: Write the failing focused-operation test**

```python
def test_update_cutlist_override_rebuilds_job_then_refreshes_cache(mock_app, monkeypatch):
    monkeypatch.setattr(main, "allow_job_mismatch_override", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "build_hardwoods_cutlist_index_for_job", lambda *args, **kwargs: True)
    result = mock_app.update_cutlist_job_mismatch_override(
        "530a - TEST", allow=True, doc_type="NAILER_CUT_LIST",
        pdf_filename="530a - Nailer Cut List.pdf", expected_job="530A", found_job="532",
    )
    assert result["success"] is True
    mock_app.metadata_refresh_service.refresh_job_now.assert_called_once()
```

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_reparse_job.py -k cutlist_override -v`

Expected: FAIL because the application method does not exist.

- [ ] **Step 3: Implement the application boundary**

```python
def update_cutlist_job_mismatch_override(self, job_folder_name: str, *, allow: bool, **identity: str) -> dict:
    job_path = os.path.join(self.config.ROOT_DIR, str(job_folder_name).strip())
    if not os.path.isdir(job_path):
        return {"success": False, "message": "Job folder no longer exists."}
    if allow:
        allow_job_mismatch_override(job_path, **identity)
    else:
        remove_job_mismatch_override(job_path, **identity)
    try:
        rebuilt = build_hardwoods_cutlist_index_for_job(
            job_path, deployment_gate=self.deployment_gate,
            on_job_mismatch=self._queue_job_mismatch_notice,
        )
    except Exception as exc:
        logging.error("Cutlist override rebuild failed for %s: %s", job_folder_name, exc, exc_info=True)
        return {"success": False, "message": f"Override saved, but rebuild failed: {exc}"}
    if not rebuilt:
        return {"success": False, "message": "Override saved, but hardwoods rebuild did not complete."}
    self.metadata_refresh_service.refresh_job_now(Path(job_path), "cutlist_job_mismatch_override_updated")
    return {"success": True, "message": "Cutlist mismatch override updated and cache refreshed."}
```

Refresh the Jobs dashboard after a successful operation. Keep the recorded decision if rebuilding or refreshing errors.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_reparse_job.py -k cutlist_override -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add ready_jobs_watcher/main.py tests/test_reparse_job.py
git commit -m "feat: rebuild cache after cutlist override"
```

## Task 4: Add Allow and Revoke Controls to the Jobs Dialog

**Files:**
- Modify: `ready_jobs_watcher/gui.py:1033-1064`
- Test: `tests/test_gui_actions.py`

**Interfaces:**
- Consumes `Application.update_cutlist_job_mismatch_override(job_folder_name: str, *, allow: bool, doc_type: str, pdf_filename: str, expected_job: str, found_job: str) -> dict`.
- Uses `overrideActive: bool` to choose Allow versus Remove; entries without both `expectedJob` and `foundJob` have neither action.

- [ ] **Step 1: Write the failing UI tests**

```python
def test_mismatch_dialog_offers_allow_for_blocked_job_number(monkeypatch):
    window = _bare_settings_window_with_override_app()
    labels = _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(False))
    assert "Allow this PDF anyway" in labels
    assert "Remove allow and rebuild" not in labels


def test_mismatch_dialog_offers_revoke_for_allowed_job_number(monkeypatch):
    window = _bare_settings_window_with_override_app()
    labels = _capture_mismatch_dialog_buttons(monkeypatch, window, _job_mismatch_payload(True))
    assert "Remove allow and rebuild" in labels
    assert "Allow this PDF anyway" not in labels
```

Use `SettingsWindow.__new__`, a minimal app double, and fake dialog/button objects so the tests assert labels and application calls without opening a Qt modal dialog.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_gui_actions.py -k mismatch_dialog -v`

Expected: FAIL because the dialog creates only Dismiss.

- [ ] **Step 3: Implement the operator actions**

```python
def _run_override_action(allow: bool, entry: dict) -> None:
    result = self.app_instance.update_cutlist_job_mismatch_override(
        job_folder_name, allow=allow, doc_type=str(entry["docType"]),
        pdf_filename=str(entry["pdfFilename"]), expected_job=str(entry["expectedJob"]),
        found_job=str(entry["foundJob"]),
    )
    self.refresh_jobs_dashboard()
    QMessageBox.information(dialog, "Cutlist mismatch", str(result["message"]))
```

For blocked entries, ask for confirmation naming the PDF, expected job, and found job, then start the action in a daemon `threading.Thread` with the clicked button disabled. For allowed entries show **Remove allow and rebuild**. Leave document-type/template errors without an action.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_gui_actions.py -k mismatch_dialog -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add ready_jobs_watcher/gui.py tests/test_gui_actions.py
git commit -m "feat: manage cutlist mismatch overrides in jobs"
```

## Task 5: Verify and Hand Off

**Files:**
- Verify: all files above.

- [ ] **Step 1: Run the focused suite**

Run: `pytest tests/test_cutlist_job_mismatch.py tests/test_hardwoods_cutlist_indexer.py tests/test_reparse_job.py tests/test_gui_actions.py -v`

Expected: PASS.

- [ ] **Step 2: Run static checks**

Run: `python -m compileall -q ready_jobs_watcher tests; git diff --check HEAD~4..HEAD`

Expected: exit code 0.

- [ ] **Step 3: Inspect change scope**

Run: `git status --short; git log --oneline -4`

Expected: only override-slice changes are committed; the named pre-existing files and `tmp/` remain untouched.

- [ ] **Step 4: Hand off the operator workflow**

State: **Settings → Jobs → double-click the red mismatch row → Allow this PDF anyway** rebuilds the hardwoods index and that job’s cache. A report/document-type mismatch stays blocked and has no bypass.
