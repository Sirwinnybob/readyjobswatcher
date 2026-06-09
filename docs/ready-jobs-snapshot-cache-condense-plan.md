# Ready Jobs Snapshot Cache And Condense Migration Plan

## Summary

Move only snapshotting, cache publication, and CNC/hardwoods end-of-day condensing into Ready Jobs. Hours Tracker remains the permanent owner of all metadata it creates, including production order, custom rip items, specialty items, and supply data. Ready Jobs observes and preserves those files as source inputs, but does not migrate, rewrite, normalize, or take ownership of them.

## Ownership Boundaries

- Ready Jobs owns `.metadata/cache_static.json`, the local durable snapshot archive, CNC tracker condensing under `CNC/.tracker/consolidated.json`, and hardwoods tracker condensing under `.metadata/hardwoods/.tracker/consolidated.json`.
- Ready Jobs may read and archive, but must not own or write Hours Tracker production order, custom rip/manual board-stock data, specialty/admin metadata and trackers, supply metadata, or PGM Sorting CNC sidecars under `CNC/.metadata`.
- PGM Sorting remains the owner of CNC PDF sidecar metadata, thumbnails, and OCR fields.

## Cache And Snapshot Flow

- Ready Jobs watches relevant metadata and tracker paths, then waits 8 seconds after the last observed change before rebuilding `cache_static.json`.
- Repeated file changes reset the 8-second timer so partially written PDFs, sidecars, OCR updates, and Hours Tracker metadata writes settle before cache publication.
- Ready Jobs builds `cache_static.json` by reading current source files from Ready Jobs, Hours Tracker, and PGM Sorting-owned locations without modifying those source files.
- Ready Jobs writes append-only local archive snapshots containing source metadata bundles plus manifest hashes/timestamps so deleted live job folders still have recoverable metadata history.

## Tablet Support

- KKCSheetTracker continues using existing paths: `.metadata/cache_static.json`, raw `CNC/.metadata/*.json`, tracker folders, and Hours Tracker-owned specialty, supply, custom rip, and production-order data.
- Ready Jobs cache writes update `cache_static.json` mtime, which tablets already poll.
- Ready Jobs preserves existing `consolidated.json` tracker formats so tablet and server readers remain compatible.

## Future-Proofing

- The metadata inventory registry classifies files as `derived_owned`, `external_source`, or `ignored_generated`.
- Unknown future JSON files under registered metadata roots are automatically fingerprinted and archived as source data.
- Unknown files can trigger cache rebuilds if they are under watched source roots, but new tablet/server UI behavior still requires a consumer that understands their schema.

## End-Of-Day Condensing

- At 8:00 PM, Ready Jobs scans deployed jobs, consolidates only CNC and hardwoods tracker files, rebuilds stale or missing `cache_static.json`, and writes final archive snapshots.
- Ready Jobs does not condense Hours Tracker specialty/admin trackers or supply/custom-rip files.
- Hours Tracker's cache rebuild and CNC/hardwoods 8 PM condense jobs should be disabled only after Ready Jobs-generated cache output is verified compatible.

## Test Plan

- Verify Ready Jobs never writes Hours Tracker-owned production order, custom rip, specialty, supply, or admin metadata.
- Verify Hours Tracker-owned metadata changes are observed, debounced for 8 seconds, archived, and reflected in cache only where existing cache readers expect them.
- Verify PGM Sorting sidecar creation plus later OCR update settles into one final cache rebuild after the quiet period.
- Verify Ready Jobs-generated `cache_static.json` loads in KKCSheetTracker without tablet changes.
- Verify CNC and hardwoods `consolidated.json` output matches current Hours Tracker format.
- Verify local archive still contains recoverable metadata after a live job folder is deleted.
