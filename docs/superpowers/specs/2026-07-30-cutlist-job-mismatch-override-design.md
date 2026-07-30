# Cutlist Job-Number Override Design

## Goal

Allow an operator to intentionally index one cutlist PDF whose printed job number differs from its Ready Jobs folder, without weakening document-type validation. The decision is per file and persists through normal watcher rebuilds until an operator revokes it.

## Scope and safety boundary

- Applies only to `JobMismatchError`, after the file has passed its filename-to-report-title compatibility check.
- Does not apply to `TemplateMismatchError`. A file named Face Frame Cut List whose report title or layout is a Door List remains rejected and has no allow action.
- Is specific to the job folder, detected document type, PDF filename, expected folder job number, and found printed job number. A renamed file or a PDF that starts reporting another job number is blocked again and requires an explicit new decision.
- A matching override remains effective when the same file is rebuilt, including normal watcher-triggered rebuilds and full job/cache rebuilds.
- The override is intentionally visible and revocable; it is never an invisible “ignore” state.

## Persistence

Store operator decisions at:

`<job>/.metadata/hardwoods/cutlist_job_mismatch_overrides.json`

The file contains a versioned list of allow entries. Each entry records `docType`, `pdfFilename`, `expectedJob`, `foundJob`, `approvedAt`, and `approvedBy` (the local Windows username when available). Matching uses the first four fields only. Writes use the repository atomic JSON writer.

The existing `cutlist_job_mismatch.json` remains the current-status signal. A persisted mismatch continues to be recorded when it is allowed, marked `overrideActive: true`. This keeps the dashboard warning and revoke action available even though the document has been indexed. A non-overridden mismatch remains excluded as today.

## Parse and cache flow

1. The parser validates that the filename-selected document type matches the first-page report title before considering an override.
2. On a job-number mismatch, the indexer checks for an exact allow entry.
3. With no entry, it skips the document and records the normal mismatch status.
4. With a matching entry, it records the mismatch as `overrideActive`, then parses and indexes that document. Sibling documents keep their current independent behavior.
5. Allowing or revoking an entry runs a focused hardwoods-index rebuild followed by the existing single-job metadata-cache refresh. The tablet therefore receives the current index/cache rather than waiting for a later file event.

## Operator UI

In **Settings → Jobs**, a red `CUTLIST MISMATCH` row remains visible for both blocked and allowed mismatches. Double-clicking it opens the existing detail dialog.

- Blocked job-number mismatch: show **Allow this PDF anyway** and **Dismiss**. The allow action has a confirmation that names the file, folder job, and printed job.
- Allowed job-number mismatch: label it **Allowed override active** and show **Remove allow and rebuild**.
- Any template/document-type mismatch: no allow button is created. It continues through the existing parser error/log path. In particular, the required 3.0 failure path must not write a new status flag, because it deliberately preserves the prior index, revision, and mismatch status unchanged.

The action is performed on a background worker, disables duplicate clicks while it runs, refreshes the Jobs dashboard when complete, and reports any rebuild/cache error without deleting the operator’s recorded decision.

## Tests

Add focused tests proving:

1. A matching job-number override permits only its matching file to enter the index and leaves an `overrideActive` mismatch status.
2. A changed filename, document type, expected job, or found job does not match the override and stays blocked.
3. A document-type/template mismatch remains blocked even when an override record with matching job fields exists.
4. Revoking an override excludes the document on the next rebuild.
5. The application action rebuilds the hardwoods index and refreshes that job’s metadata cache.
6. The dialog exposes allow/revoke only for the respective job-number states.

## Non-goals

- No override for malformed 3.0 report templates, placeholder PDFs, document-type mismatches, or unrelated parsing errors.
- No global “ignore all mismatches” setting.
- No changes to deployment gating, mode-selection overrides, or non-hardwoods PDF processing.
