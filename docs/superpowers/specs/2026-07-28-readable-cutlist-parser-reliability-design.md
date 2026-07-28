# Readable Cut List Parser Reliability

## Goal

Keep Face Frame, Nailer, and Door Cut List 3.0 PDFs readable in CABINET VISION while making Ready Jobs Watcher reject malformed reports instead of emitting incomplete or random rip data.

## Scope

- Preserve the readable 3.0 report layout: normal title, Material header, line-item table, static colors, and no Totals section.
- Keep report-title compatibility by cut-list type: a 3.0 Face Frame, Nailer, or Door PDF must be routed only to its matching parser.
- Derive rip data from parsed line items, never from a Totals block.
- Treat the short side of a rectangular part as the rip width.
- Fail safely when the required readable-table contract is incomplete.

## Contract

Each supported 3.0 cut list must contain:

1. A matching report title on the first line.
2. At least one readable Material header with a recognizable unit.
3. A recognizable line-item table header.
4. Rows with a positive quantity, required dimensions, and cabinet data when that document type requires it.

RJW must not use decorative colors, Totals, rips summaries, embedded machine tags, hidden columns, or full-width helper lines as data sources.

## Data Flow

```text
Readable PDF title + material headers + table rows
                 -> strict parser validation
                 -> normalized line items
                 -> short-side rip aggregation
                 -> cache_static.json board stock rows
```

## Failure Handling

- A 3.0 title that belongs to another cut-list type is rejected.
- A missing table header, material header, or valid row structure is rejected rather than partially indexed.
- Totals are ignored completely for rip generation.
- A malformed report leaves the last known good metadata intact and is surfaced through the existing parser/error path.

## Verification

- Unit tests cover title matching, missing required table/material structure, and short-side rip orientation.
- Regression parsing uses the supplied Door, Face Frame, and Nailer PDFs after regeneration.
- Confirm no row with a rip width greater than its paired long dimension is emitted solely because the dimensions are printed in the opposite order.

## Out of Scope

- New inline machine annotations in CABINET VISION.
- Hidden helper columns or additional report rows.
- Restoring or parsing Totals blocks.
