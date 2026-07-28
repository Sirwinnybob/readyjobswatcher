# Readable Cut List Parser Reliability

## Goal

Keep Face Frame, Nailer, and Door Cut List 3.0 PDFs readable in CABINET VISION while making Ready Jobs Watcher reject malformed reports instead of emitting incomplete or random rip data.

## Scope

- Preserve the readable 3.0 report layout: normal title, Material header, line-item table, static colors, and no Totals section.
- Keep report-title compatibility by cut-list type: a 3.0 Face Frame, Nailer, or Door PDF must be routed only to its matching parser.
- Derive rip data from parsed line items, never from a Totals block.
- Treat the printed Width column as the authoritative rip width, even when it is numerically larger than Length.
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
Readable PDF title + material headers + W:/L:-labeled table rows
                 -> strict parser validation
                 -> normalized line items
                 -> Width-column rip aggregation
                 -> cache_static.json board stock rows
```

## Failure Handling

- A 3.0 title that belongs to another cut-list type is rejected.
- A missing table header, material header, or valid row structure is rejected rather than partially indexed.
- Totals are ignored completely for rip generation.
- A malformed report leaves the last known good metadata intact and is surfaced through the existing parser/error path.

## Verification

- Unit tests cover title matching, missing required table/material structure, and Width-column rip orientation.
- Regression parsing uses the supplied Door, Face Frame, and Nailer PDFs after regeneration.
- Confirm every board-stock row uses the parsed Width value exactly, even when Length is numerically smaller.

## Out of Scope

- New inline machine annotations in CABINET VISION.
- Hidden helper columns or additional report rows.
- Restoring or parsing Totals blocks.
- Reordering Width and Length values based on their numeric size.
