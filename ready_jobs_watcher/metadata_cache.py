from __future__ import annotations

import json
import logging
import os
import platform
import re
import time
from math import ceil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import fitz
except Exception:  # pragma: no cover - dependency is optional for fallback page counts
    fitz = None

from .atomic_write import atomic_write_json as _shared_atomic_write_json
from .metadata_snapshot import archive_job_metadata
from .tracker_action_stream import load_cnc_tracker_actions, load_hardwoods_tracker_actions


EMPTY_PROGRESS = {
    "totalSheets": 0,
    "done": 0,
    "bad": 0,
    "skipped": 0,
    "percentDone": 0,
}


def _read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> bool:
    # Skip-if-unchanged is extra semantics on top of a plain atomic write, so
    # that part stays here; the actual temp+replace write is delegated to the
    # shared helper (ready_jobs_watcher.atomic_write).
    if _read_json(path) == payload:
        return False
    _shared_atomic_write_json(path, payload, indent=2, ensure_ascii=False)
    return True


def format_width(val: float) -> str:
    if val == -0.0:
        val = 0.0
    s = f"{val:.6f}".rstrip("0")
    if s.endswith("."):
        s = s[:-1]
    return s


_3D_EXTENSIONS = frozenset((".glb", ".dae"))


def has_3d_assets(job_folder: Path) -> bool:
    three_d_dir = job_folder / "3D"
    if not three_d_dir.is_dir():
        return False
    try:
        for p in three_d_dir.iterdir():
            if p.is_file() and p.suffix.lower() in _3D_EXTENSIONS:
                return True
            if p.is_dir():
                for sp in p.iterdir():
                    if sp.is_file() and sp.suffix.lower() in _3D_EXTENSIONS:
                        return True
    except OSError:
        pass
    return False


def parse_job_folder_name(folder_name: str) -> tuple[str, str]:
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9-]*)\s+-\s+(.+)$", folder_name)
    if not m:
        return "", folder_name
    return m.group(1), m.group(2).strip()


def _read_hidden_flag(job_folder: Path) -> bool:
    gate = _read_json(job_folder / ".metadata" / "deployment_gate.json", {})
    return bool(gate.get("hiddenFromProduction", False)) if isinstance(gate, dict) else False


def _read_deployed_flag(job_folder: Path) -> bool:
    gate_path = job_folder / ".metadata" / "deployment_gate.json"
    if not gate_path.exists():
        return False
    gate = _read_json(gate_path, {})
    if not isinstance(gate, dict):
        return False
    return bool(gate.get("deployed", True))


def _pdf_page_count(pdf_path: Path, metadata: Optional[Dict[str, Any]]) -> tuple[int, Optional[str]]:
    if fitz is not None:
        try:
            doc = fitz.open(str(pdf_path))
            try:
                page_count = len(doc)
                if page_count <= 0 and metadata:
                    return len(metadata.get("pages", [])), "PDF reported zero pages; used sidecar metadata page count"
                return page_count, None
            finally:
                doc.close()
        except Exception as exc:
            fallback = len(metadata.get("pages", [])) if metadata else 0
            return fallback, str(exc)
    return len(metadata.get("pages", [])) if metadata else 0, "PyMuPDF unavailable"


def generate_static_cache(job_folder: Path, folder_name: Optional[str] = None, lineup_position: int = None) -> Dict[str, Any]:
    folder_name = folder_name or job_folder.name
    job_number, job_name = parse_job_folder_name(folder_name)
    hidden_from_production = _read_hidden_flag(job_folder)

    if lineup_position is None:
        old_cache = _read_json(job_folder / ".metadata" / "cache_static.json", {})
        if isinstance(old_cache, dict):
            lineup_position = old_cache.get("jobInfo", {}).get("lineupPosition")

    job_info = {
        "folderName": folder_name,
        "jobNumber": job_number,
        "jobName": job_name,
        "hiddenFromProduction": hidden_from_production,
        "lineupPosition": lineup_position,
    }

    materials = []
    cnc_issues = []
    cnc_dir = job_folder / "CNC"
    if cnc_dir.exists():
        for entry in os.scandir(cnc_dir):
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".pdf"):
                continue
            if ".sync-conflict-" in entry.name.lower():
                continue
            if "all sheets" in entry.name.lower():
                continue
            if job_number and not entry.name.startswith(f"{job_number} - "):
                continue

            pdf_path = Path(entry.path)
            material_name = pdf_path.stem.replace(f"{job_number} - ", "", 1) if job_number else pdf_path.stem
            stat = pdf_path.stat()
            metadata_file = cnc_dir / ".metadata" / f"{pdf_path.stem}.json"
            metadata = None
            if metadata_file.exists():
                try:
                    metadata = _read_json(metadata_file, None)
                    if not isinstance(metadata, dict):
                        raise ValueError("metadata root is not an object")
                except Exception as exc:
                    cnc_issues.append(
                        {
                            "type": "INVALID_METADATA_JSON",
                            "jobFolderName": folder_name,
                            "materialName": material_name,
                            "pdfFilename": entry.name,
                            "detail": str(exc),
                        }
                    )
                    metadata = None
            else:
                cnc_issues.append(
                    {
                        "type": "MISSING_METADATA",
                        "jobFolderName": folder_name,
                        "materialName": material_name,
                        "pdfFilename": entry.name,
                    }
                )

            page_count, page_error = _pdf_page_count(pdf_path, metadata)
            if page_error:
                cnc_issues.append(
                    {
                        "type": "PAGE_COUNT_ERROR",
                        "jobFolderName": folder_name,
                        "materialName": material_name,
                        "pdfFilename": entry.name,
                        "detail": page_error,
                    }
                )

            materials.append(
                {
                    "pdfFilename": entry.name,
                    "materialName": material_name,
                    "pageCount": page_count,
                    "fileFingerprint": f"{stat.st_size}_{int(stat.st_mtime * 1000)}",
                    "metadata": metadata,
                }
            )
        materials.sort(key=lambda m: m["materialName"])

    cnc_job = {
        "folderName": folder_name,
        "jobNumber": job_number,
        "jobName": job_name,
        "materials": materials,
        "hiddenFromProduction": hidden_from_production,
        "lineupPosition": lineup_position,
    }

    hardwood_index = _read_json(job_folder / ".metadata" / "hardwoods" / "cutlist_index.json", None)
    hardwood_job = {
        "folderName": folder_name,
        "jobNumber": job_number,
        "jobName": job_name,
        "index": hardwood_index,
        "hiddenFromProduction": hidden_from_production,
        "lineupPosition": lineup_position,
    }
    hardwood_revision_history = _read_json(job_folder / ".metadata" / "hardwoods" / "cutlist_revisions.json", None)
    cabinet_sheet_index = _read_json(job_folder / ".metadata" / "cabinet_sheet_index.json", None)
    assembly_job = {
        "folderName": folder_name,
        "jobNumber": job_number,
        "jobName": job_name,
        "cabinetSheetIndex": cabinet_sheet_index,
        "hiddenFromProduction": hidden_from_production,
        "lineupPosition": lineup_position,
    }

    pdf_catalog = build_pdf_catalog(job_folder)
    board_stock_rows = build_board_stock_rows(job_folder, hardwood_index)

    static_data = {
        "jobInfo": job_info,
        "cncJob": cnc_job,
        "cncIssues": cnc_issues,
        "hardwoodJob": hardwood_job,
        "hardwoodRevisionHistory": hardwood_revision_history,
        "assemblyJob": assembly_job,
        "cabinetSheetIndex": cabinet_sheet_index,
        "pdfCatalog": pdf_catalog,
        "boardStockRows": board_stock_rows,
        "hasThreeDAssets": has_3d_assets(job_folder),
    }

    _atomic_write_json(job_folder / ".metadata" / "cache_static.json", static_data)
    return static_data


def build_pdf_catalog(job_folder: Path) -> Dict[str, Any]:
    root_pdfs = []
    if job_folder.exists():
        for entry in os.scandir(job_folder):
            if entry.is_file() and entry.name.lower().endswith(".pdf") and ".sync-conflict-" not in entry.name.lower():
                root_pdfs.append(entry.name)
    root_pdfs.sort(key=lambda x: x.lower())

    managed_docs = []
    other_docs = []
    delivery_sheet = None
    for pdf in root_pdfs:
        lower = pdf.lower()
        label = None
        if "delivery sheets" in lower:
            label = "Delivery Sheets"
        elif "assembly sheets" in lower:
            label = "Assembly Sheets"
        elif "plans & elevations" in lower or "plans and elevations" in lower:
            label = "Plans & Elevations"
        elif "door list" in lower:
            label = "Door List"
        elif "cut list" in lower or "cutlist" in lower:
            label = "Cut List"

        ref = {"pdfFilename": pdf, "label": label if label else Path(pdf).stem}
        if label:
            managed_docs.append(ref)
            if label == "Delivery Sheets" and not delivery_sheet:
                delivery_sheet = ref
        else:
            other_docs.append(ref)

    return {"deliverySheet": delivery_sheet, "managedDocs": managed_docs, "otherDocs": other_docs}


def build_board_stock_rows(job_folder: Path, hardwood_index: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    board_stock_rows = []
    aggregated = {}
    source_order = {"FRAME": 0, "NAILER": 1, "DOOR": 2, "MANUAL": 3}
    if isinstance(hardwood_index, dict):
        for doc in hardwood_index.get("documents", []):
            source = {
                "FACE_FRAME_CUT_LIST": "FRAME",
                "NAILER_CUT_LIST": "NAILER",
                "DOOR_CUT_LIST": "DOOR",
            }.get(doc.get("docType"))
            if not source:
                continue
            for block in doc.get("totals", []):
                material = str(block.get("material", "")).strip()
                widths = block.get("widthValues", [])
                lengths = block.get("lengthValues", [])
                for i in range(max(len(widths), len(lengths))):
                    width_raw = str(widths[i]).strip() if i < len(widths) else ""
                    feet_raw = str(lengths[i]).strip().replace(",", "") if i < len(lengths) else ""
                    try:
                        width = float(width_raw)
                        feet = float(feet_raw)
                    except ValueError:
                        continue
                    if feet <= 0.0:
                        continue
                    key = (material, width, source)
                    aggregated[key] = aggregated.get(key, 0.0) + feet

    for (material, width, source), feet in aggregated.items():
        board_stock_rows.append(
            {
                "stableKey": f"board_stock|{material}|{format_width(width)}|{source}",
                "material": material,
                "width": format_width(width),
                "normalizedWidth": width,
                "source": source,
                "sourceLabel": source,
                "totalFeet": feet,
                "neededRips": ceil(feet / 10.0),
            }
        )

    manual_stock_path = job_folder / ".metadata" / "hardwoods" / "board_stock_manual.json"
    manual_root = _read_json(manual_stock_path, {})
    if isinstance(manual_root, dict):
        for entry in manual_root.get("entries", []):
            material = str(entry.get("material", "")).strip()
            width_raw = entry.get("width") or entry.get("normalizedWidth", "")
            try:
                width = float(width_raw)
                feet = float(entry.get("totalFeet", 0.0))
            except (ValueError, TypeError):
                continue
            if feet <= 0.0:
                continue
            board_stock_rows.append(
                {
                    "stableKey": f"board_stock|{material}|{format_width(width)}|MANUAL",
                    "material": material,
                    "width": format_width(width),
                    "normalizedWidth": width,
                    "source": "MANUAL",
                    "sourceLabel": "MANUAL",
                    "totalFeet": feet,
                    "neededRips": ceil(feet / 10.0),
                    "manualCategory": entry.get("category"),
                    "manualSubtype": entry.get("subtype"),
                    "notes": entry.get("notes"),
                }
            )

    admin_stock_path = job_folder / ".metadata" / "admin" / "board_stock.json"
    admin_root = _read_json(admin_stock_path, {})
    if isinstance(admin_root, dict):
        for entry in admin_root.get("items", []):
            if not isinstance(entry, dict):
                continue
            material = str(entry.get("material", "")).strip()
            width_raw = entry.get("width") or entry.get("normalizedWidth") or entry.get("name", "")
            try:
                width = float(width_raw)
                feet = float(entry.get("totalFeet", entry.get("feet", 0.0)))
            except (ValueError, TypeError):
                continue
            if feet <= 0.0:
                continue
            item_id = str(entry.get("id") or format_width(width))
            board_stock_rows.append(
                {
                    "stableKey": f"board_stock|{material}|{format_width(width)}|MANUAL|{item_id}",
                    "material": material,
                    "width": format_width(width),
                    "normalizedWidth": width,
                    "source": "MANUAL",
                    "sourceLabel": "MANUAL",
                    "totalFeet": feet,
                    "neededRips": ceil(feet / 10.0),
                    "manualCategory": "admin_board_stock",
                    "manualSubtype": entry.get("mode"),
                    "notes": entry.get("notes") or entry.get("name"),
                }
            )

    board_stock_rows.sort(key=lambda r: (r["material"].lower(), -r["normalizedWidth"], source_order.get(r["source"], 99)))
    return board_stock_rows


# CROSS-PROGRAM (METADATA_AUDIT.md H-06): the read-existing-consolidated.json -> merge-in-device-
# files -> atomic-write -> delete-device-files sequence shared by consolidate_cnc_tracker and
# consolidate_hardwoods_tracker is internally torn-read-safe (the final write is atomic) but is NOT
# safe against a second concurrent *writer* -- two Ready Jobs Watcher processes (two hosts, or a
# future bulk-sweep utility) racing the same job's tracker dir could both read the same starting
# consolidated.json, merge independently, and the second atomic write would silently clobber the
# first (a lost update; Syncthing then quarantines the loser as a .sync-conflict copy that
# sync_conflict_resolver only archives, never merges back in). _tracker_consolidation_lock guards
# the whole sequence with a per-tracker-dir (i.e. per-job, per-CNC-or-hardwoods) file lock stored
# inside tracker_dir itself -- on the Syncthing-replicated Y:\ tree, so every host sees the same
# lock file, not just a local single-instance PID lock like Application.acquire_lock in main.py.
_CONSOLIDATE_LOCK_FILENAME = ".consolidate.lock"
_CONSOLIDATE_LOCK_TTL_SECONDS = 120  # generous vs. one consolidation pass; bounds a crashed holder


def _acquire_tracker_lock(tracker_dir: Path) -> bool:
    """Try to atomically create the per-tracker-dir consolidation lock file.

    Uses O_CREAT | O_EXCL, which is an atomic create-if-absent at the filesystem/SMB-protocol
    level (unlike a read-then-write lock file, there is no window where two processes can both
    observe "free" and both proceed). If the lock file already exists we treat it as held --
    unless it is older than _CONSOLIDATE_LOCK_TTL_SECONDS, in which case we assume the prior
    holder crashed mid-pass and reclaim it, so a dead watcher can never wedge consolidation
    forever.
    """
    lock_path = tracker_dir / _CONSOLIDATE_LOCK_FILENAME
    stale_reclaim_attempted = False
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()}@{platform.node()} {time.time()}".encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            if stale_reclaim_attempted:
                return False
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                # Lock file vanished between the failed create and our stat -- retry the create.
                stale_reclaim_attempted = True
                continue
            if age <= _CONSOLIDATE_LOCK_TTL_SECONDS:
                return False
            try:
                lock_path.unlink()
            except OSError:
                return False
            stale_reclaim_attempted = True
            continue
        except OSError:
            return False


def _release_tracker_lock(tracker_dir: Path) -> None:
    try:
        (tracker_dir / _CONSOLIDATE_LOCK_FILENAME).unlink()
    except OSError:
        pass


def _consolidate_tracker(
    tracker_dir: Path,
    merge_actions: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    load_tracker_actions: Callable[[Path], List[Dict[str, Any]]],
    compact: bool = False,
) -> None:
    """Shared read-merge-write-delete pipeline for the CNC and hardwoods trackers.

    See the H-06 CROSS-PROGRAM comment above for why this whole sequence is wrapped in a
    per-tracker-dir lock. If the lock is already held (another process is mid-consolidation for
    this same job's tracker), this pass is skipped cleanly rather than blocking or raising --
    consolidation runs on a recurring debounce/poll cycle (Config.metadata_cache_debounce_seconds),
    so a skipped pass simply retries next cycle.
    """
    if not tracker_dir.exists():
        return

    if not _acquire_tracker_lock(tracker_dir):
        logging.getLogger(__name__).info(
            "metadata_cache: skipping consolidation for %s, lock held by another process (H-06)",
            tracker_dir,
        )
        return

    try:
        # Filter MUST match _load_legacy_json_actions in tracker_action_stream.py exactly:
        # only detect/delete files the loader would actually read, or we get silent data loss
        # (delete a file the loader skipped) or missed consolidation (loader reads a file this
        # scan can't see). Case-insensitive .json, skip dotfiles, consolidated.json, sync-conflicts.
        legacy_device_files: List[tuple] = []
        for entry in os.scandir(tracker_dir):
            if (
                not entry.is_file()
                or not entry.name.lower().endswith(".json")
                or entry.name.startswith(".")
                or entry.name.lower() == "consolidated.json"
                or ".sync-conflict-" in entry.name.lower()
            ):
                continue
            try:
                stat = entry.stat()
                legacy_device_files.append((Path(entry.path), stat.st_mtime, stat.st_size))
            except OSError:
                pass

        events_dir = tracker_dir / "events"
        ndjson_device_files: List[tuple] = []
        if events_dir.is_dir():
            for entry in os.scandir(events_dir):
                if not entry.is_file() or not entry.name.lower().endswith(".ndjson") or ".sync-conflict-" in entry.name.lower():
                    continue
                try:
                    stat = entry.stat()
                    ndjson_device_files.append((Path(entry.path), stat.st_mtime, stat.st_size))
                except OSError:
                    pass

        # CROSS-PROGRAM (METADATA_AUDIT.md R-01): merge input comes from the same union reader
        # tracker_bad_parts.py/remake_candidates_indexer.py already use (ndjson events + legacy
        # <tabletId>.json + consolidated.json itself, since consolidated.json is just one more
        # *.json file with an "actions" key). Only legacy device files are deleted here; ndjson
        # event files are only deleted when compact=True (the after-hours compaction pass, which
        # the end-of-day sweep enables -- wired in Task 4) because each ndjson file has exactly one
        # writer (the owning tablet) and truncating it mid-day would race that tablet's live append.
        if not legacy_device_files and not ndjson_device_files:
            return

        actions = load_tracker_actions(tracker_dir)
        consolidated_actions = merge_actions(actions)
        _atomic_write_json(tracker_dir / "consolidated.json", {"tabletId": "consolidated", "actions": consolidated_actions})

        _delete_unchanged_device_files(legacy_device_files)

        if compact:
            # CROSS-PROGRAM (METADATA_AUDIT.md R-01): safe only because this only runs from the
            # after-hours end-of-day sweep (see scheduler.py's metadata_end_of_day_scheduler /
            # process_metadata_end_of_day_once), when no tablet is actively appending. The
            # mtime/size guard in _delete_unchanged_device_files still protects against a
            # genuinely-anomalous late writer.
            _delete_unchanged_device_files(ndjson_device_files)
    finally:
        _release_tracker_lock(tracker_dir)


def consolidate_cnc_tracker(job_folder: Path, compact: bool = False):
    # CROSS-PROGRAM: the per-device <tabletId>.json files and events/<tabletId>.ndjson streams
    # here are PRODUCED by KKCSheetTracker tablets (ProgressStore.kt) and CONSUMED by this
    # watcher. This function merges them into consolidated.json. Legacy device files are deleted
    # after a successful merge; ndjson event files are left alone unless compact=True (only the
    # after-hours sweep passes that, wired in Task 4).
    # FIXED (METADATA_AUDIT.md C-01/M-06): the merge tracks, per (file, page, fingerprint, part),
    # whether the part is currently bad and whether it has been submitted for the engineer alert
    # (tracker_bad_parts.py:448 requires a `bad_part_submitted` action to fire). Both `bad_part` and
    # `bad_part_submitted` are re-emitted into consolidated.json with their own original timestamps
    # (not a shared/fallback timestamp), and `unbad_part` resets the submitted flag, mirroring the
    # reactivation semantics in tracker_bad_parts.py so the alert survives device-file deletion.
    _consolidate_tracker(
        job_folder / "CNC" / ".tracker",
        _merge_cnc_actions,
        lambda tracker_dir: load_cnc_tracker_actions(str(tracker_dir)),
        compact=compact,
    )


def _merge_cnc_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    actions.sort(key=lambda a: a.get("timestamp", ""))
    page_states = {}
    for action_obj in actions:
        filename = action_obj.get("file")
        page = action_obj.get("page")
        action = action_obj.get("action")
        part = action_obj.get("part")
        timestamp = action_obj.get("timestamp", "")
        fingerprint = action_obj.get("fileFingerprint")
        if not filename or page is None or not action:
            continue
        key = (filename, page, fingerprint)
        page_states.setdefault(key, {"latest_action": "", "timestamp": "", "bad_parts": {}, "reNested": None})
        state = page_states[key]
        if action == "bad_part" and part is not None:
            part_state = state["bad_parts"].setdefault(
                part, {"bad": False, "bad_ts": "", "submitted": False, "submitted_ts": ""}
            )
            if not part_state["bad"]:
                # Reactivation (or first flag) resets any prior submission, matching the
                # reactivated-token handling in tracker_bad_parts.py.
                part_state["submitted"] = False
                part_state["submitted_ts"] = ""
            part_state["bad"] = True
            part_state["bad_ts"] = timestamp
        elif action == "unbad_part" and part is not None:
            part_state = state["bad_parts"].get(part)
            if part_state is not None:
                part_state["bad"] = False
                part_state["submitted"] = False
                part_state["submitted_ts"] = ""
        elif action == "bad_part_submitted" and part is not None:
            part_state = state["bad_parts"].setdefault(
                part, {"bad": False, "bad_ts": "", "submitted": False, "submitted_ts": ""}
            )
            part_state["submitted"] = True
            part_state["submitted_ts"] = timestamp
        elif action in ("complete", "skip", "unskip"):
            if not state["timestamp"] or timestamp > state["timestamp"]:
                state["latest_action"] = action
                state["timestamp"] = timestamp
                state["reNested"] = action_obj.get("reNested")

    consolidated_actions = []
    for (filename, page, fingerprint), state in page_states.items():
        if state["latest_action"] in ("complete", "skip"):
            act = {"file": filename, "page": page, "action": state["latest_action"], "timestamp": state["timestamp"]}
            if fingerprint:
                act["fileFingerprint"] = fingerprint
            if state["reNested"] is not None:
                act["reNested"] = state["reNested"]
            consolidated_actions.append(act)
        for part in sorted(state["bad_parts"]):
            part_state = state["bad_parts"][part]
            if not part_state["bad"]:
                continue
            bad_ts = part_state["bad_ts"] or "2026-01-01T00:00:00Z"
            act = {
                "file": filename,
                "page": page,
                "action": "bad_part",
                "part": part,
                "timestamp": bad_ts,
            }
            if fingerprint:
                act["fileFingerprint"] = fingerprint
            consolidated_actions.append(act)
            if part_state["submitted"]:
                submitted_act = {
                    "file": filename,
                    "page": page,
                    "action": "bad_part_submitted",
                    "part": part,
                    "timestamp": part_state["submitted_ts"] or bad_ts,
                }
                if fingerprint:
                    submitted_act["fileFingerprint"] = fingerprint
                consolidated_actions.append(submitted_act)

    return consolidated_actions


def consolidate_hardwoods_tracker(job_folder: Path, compact: bool = False):
    _consolidate_tracker(
        job_folder / ".metadata" / "hardwoods" / ".tracker",
        _merge_hardwoods_actions,
        lambda tracker_dir: load_hardwoods_tracker_actions([str(tracker_dir)]),
        compact=compact,
    )


def _merge_hardwoods_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # CROSS-PROGRAM: consumed by KKCSheetTracker tablet (HardwoodsProgressStore.kt)
    # and by Hours Tracker backend (job_store.py::read_hardwoods_progress).
    # FIXED (METADATA_AUDIT.md C-02): Merges all 6 action types written by the tablet
    # (set_done_count, set_bad_count, set_skipped, clear_skipped, add_totals_rip10_done_count,
    # set_totals_rip10_done_count) so that bad part tallies and board-stock rip-tally totals
    # are preserved instead of silently dropped during consolidation.
    # Actions are processed chronologically to reconstruct final row and totals states.
    actions.sort(key=lambda a: a.get("timestamp", ""))

    row_states = {}
    totals_states = {}

    for action_obj in actions:
        doc_type = action_obj.get("docType")
        row_id = action_obj.get("rowId") or ""
        action = action_obj.get("action")
        timestamp = action_obj.get("timestamp", "")

        if not doc_type or not action:
            continue

        # Safely coerce value to integer
        val_raw = action_obj.get("value")
        if val_raw is None:
            val = 0
        else:
            try:
                val = int(val_raw)
            except Exception:
                try:
                    val = int(float(val_raw))
                except Exception:
                    val = 0

        if action in ("set_done_count", "set_bad_count", "set_skipped", "clear_skipped"):
            if not row_id:
                continue
            key = (doc_type, row_id)
            state = row_states.setdefault(key, {
                "done_count": 0,
                "done_ts": "",
                "bad_count": 0,
                "bad_ts": "",
                "skipped": False,
                "skipped_ts": ""
            })
            if action == "set_done_count":
                state["done_count"] = val
                state["done_ts"] = timestamp
            elif action == "set_bad_count":
                state["bad_count"] = val
                state["bad_ts"] = timestamp
            elif action == "set_skipped":
                state["skipped"] = True
                state["skipped_ts"] = timestamp
            elif action == "clear_skipped":
                state["skipped"] = False
                state["skipped_ts"] = timestamp

        elif action in ("add_totals_rip10_done_count", "set_totals_rip10_done_count"):
            totals_key = action_obj.get("totalsKey") or ""
            if not totals_key:
                totals_key = row_id
            if not totals_key:
                continue

            state = totals_states.setdefault(totals_key, {
                "value": 0,
                "docType": doc_type,
                "rowId": row_id,
                "timestamp": ""
            })
            state["docType"] = doc_type
            state["rowId"] = row_id
            state["timestamp"] = timestamp

            if action == "add_totals_rip10_done_count":
                state["value"] = max(0, state["value"] + val)
            elif action == "set_totals_rip10_done_count":
                state["value"] = max(0, val)

    consolidated_actions = []

    # Sort for deterministic output structure
    for (doc_type, row_id), state in sorted(row_states.items()):
        if state["done_count"] > 0:
            consolidated_actions.append({
                "docType": doc_type,
                "rowId": row_id,
                "action": "set_done_count",
                "value": state["done_count"],
                "timestamp": state["done_ts"] or "2026-01-01T00:00:00Z"
            })
        if state["bad_count"] > 0:
            consolidated_actions.append({
                "docType": doc_type,
                "rowId": row_id,
                "action": "set_bad_count",
                "value": state["bad_count"],
                "timestamp": state["bad_ts"] or "2026-01-01T00:00:00Z"
            })
        if state["skipped"]:
            consolidated_actions.append({
                "docType": doc_type,
                "rowId": row_id,
                "action": "set_skipped",
                "timestamp": state["skipped_ts"] or "2026-01-01T00:00:00Z"
            })

    for totals_key, state in sorted(totals_states.items()):
        if state["value"] > 0:
            consolidated_actions.append({
                "docType": state["docType"],
                "rowId": state["rowId"],
                "totalsKey": totals_key,
                "action": "set_totals_rip10_done_count",
                "value": state["value"],
                "timestamp": state["timestamp"] or "2026-01-01T00:00:00Z"
            })

    return consolidated_actions


def _delete_unchanged_device_files(device_files):
    for path, mtime, size in device_files:
        try:
            if path.exists():
                stat = path.stat()
                if stat.st_mtime == mtime and stat.st_size == size:
                    path.unlink()
        except Exception:
            pass


def _iter_staleness_files(job_folder: Path):
    for path in (
        job_folder / ".metadata" / "deployment_gate.json",
        job_folder / ".metadata" / "cabinet_sheet_index.json",
        job_folder / ".metadata" / "hardwoods" / "cutlist_index.json",
        job_folder / ".metadata" / "hardwoods" / "cutlist_revisions.json",
        job_folder / ".metadata" / "hardwoods" / "board_stock_manual.json",
        job_folder / ".metadata" / "admin" / "board_stock.json",
    ):
        yield path

    for folder, predicate in (
        (job_folder, lambda p: p.suffix.lower() == ".pdf"),
        (job_folder / "CNC", lambda p: p.suffix.lower() == ".pdf" and "all sheets" not in p.name.lower()),
        (job_folder / "CNC" / ".metadata", lambda p: p.suffix.lower() == ".json"),
    ):
        if folder.exists():
            for entry in os.scandir(folder):
                path = Path(entry.path)
                if entry.is_file() and predicate(path) and ".sync-conflict-" not in entry.name.lower():
                    yield path


def check_cache_needs_rebuild(job_folder: Path, cache_mtime: float) -> bool:
    for file_path in _iter_staleness_files(job_folder):
        try:
            if file_path.exists() and file_path.stat().st_mtime > cache_mtime:
                return True
        except OSError:
            continue
    return False


def scan_jobs(base_path: Path) -> List[Dict[str, Any]]:
    results = []
    if not base_path.exists():
        return results
    for entry in os.scandir(base_path):
        if not entry.is_dir():
            continue
        folder = Path(entry.path)
        if not (folder / ".metadata" / "deployment_gate.json").exists():
            continue
        if _read_hidden_flag(folder):
            continue
        job_number, job_name = parse_job_folder_name(entry.name)
        results.append(
            {
                "folderName": entry.name,
                "jobNumber": job_number,
                "jobName": job_name,
                "hiddenFromProduction": False,
                "cnc": EMPTY_PROGRESS.copy(),
                "hardwoods": EMPTY_PROGRESS.copy(),
                "assembly": EMPTY_PROGRESS.copy(),
            }
        )
    results.sort(key=lambda x: x["folderName"], reverse=True)
    return results


def get_production_order(base_path: Path) -> List[str]:
    data = _read_json(base_path / "production_order.json", [])
    if isinstance(data, list):
        return [item for item in data if isinstance(item, str)]
    return []


def compute_lineup(base_path: Path, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active_jobs = {j["folderName"]: j for j in jobs}
    computed_order = []
    for folder_name in get_production_order(base_path):
        if folder_name in active_jobs:
            computed_order.append(active_jobs.pop(folder_name))
    computed_order.extend(sorted(active_jobs.values(), key=lambda x: x["folderName"], reverse=True))
    for idx, job in enumerate(computed_order):
        job["lineupPosition"] = idx + 1
    return computed_order


def update_all_jobs_cache(
    base_path: Path,
    *,
    consolidate_trackers: bool = True,
    archive: bool = True,
    archive_root: Optional[Path] = None,
    archive_retention_days: Optional[int] = None,
    archive_max_snapshots_per_job: Optional[int] = None,
    archive_daypart_limit: bool = False,
    force_rebuild: bool = False,
    compact_tracker_events: bool = False,
) -> Dict[str, int]:
    scanned = scan_jobs(base_path)
    lineup_jobs = compute_lineup(base_path, scanned)
    lineup_positions = {j["folderName"]: j["lineupPosition"] for j in lineup_jobs}
    summary = {"processed": 0, "rebuilt": 0, "archived": 0, "errors": 0}

    if not base_path.exists():
        return summary

    for entry in os.scandir(base_path):
        if not entry.is_dir():
            continue
        folder_name = entry.name
        job_folder = Path(entry.path)
        if not re.match(r"^([A-Za-z0-9][A-Za-z0-9-]*)\s+-\s+(.+)$", folder_name) and not (job_folder / ".metadata").exists():
            continue
        if not _read_deployed_flag(job_folder):
            continue

        summary["processed"] += 1
        try:
            if consolidate_trackers:
                consolidate_cnc_tracker(job_folder, compact=compact_tracker_events)
                consolidate_hardwoods_tracker(job_folder, compact=compact_tracker_events)

            cache_path = job_folder / ".metadata" / "cache_static.json"
            needs_rebuild = force_rebuild or not cache_path.exists()
            if not needs_rebuild:
                needs_rebuild = check_cache_needs_rebuild(job_folder, cache_path.stat().st_mtime)
            if needs_rebuild:
                generate_static_cache(job_folder, folder_name, lineup_positions.get(folder_name))
                summary["rebuilt"] += 1

            if archive and archive_root is not None:
                result = archive_job_metadata(
                    base_path,
                    job_folder,
                    archive_root,
                    reason="scheduled_cache_update",
                    retention_days=archive_retention_days,
                    max_snapshots_per_job=archive_max_snapshots_per_job,
                    daypart_limit=archive_daypart_limit,
                )
                if result.success:
                    summary["archived"] += 1
                else:
                    summary["errors"] += 1
        except Exception:
            summary["errors"] += 1
    return summary


def refresh_single_job(
    base_path: Path,
    job_folder: Path,
    *,
    reason: str,
    archive_root: Optional[Path],
    archive_retention_days: Optional[int] = None,
    archive_max_snapshots_per_job: Optional[int] = None,
    archive_daypart_limit: bool = False,
    consolidate_trackers: bool = False,
    compact_tracker_events: bool = False,
) -> Dict[str, Any]:
    if not job_folder.is_dir():
        return {"skipped": "missing_job", "jobFolder": str(job_folder)}
    if not _read_deployed_flag(job_folder):
        return {"skipped": "not_deployed", "jobFolder": str(job_folder)}
    if consolidate_trackers:
        consolidate_cnc_tracker(job_folder, compact=compact_tracker_events)
        consolidate_hardwoods_tracker(job_folder, compact=compact_tracker_events)
    lineup_positions = {j["folderName"]: j["lineupPosition"] for j in compute_lineup(base_path, scan_jobs(base_path))}
    data = generate_static_cache(job_folder, job_folder.name, lineup_positions.get(job_folder.name))
    archive_result = None
    if archive_root is not None:
        archive_result = archive_job_metadata(
            base_path,
            job_folder,
            archive_root,
            reason=reason,
            retention_days=archive_retention_days,
            max_snapshots_per_job=archive_max_snapshots_per_job,
            daypart_limit=archive_daypart_limit,
        )
    return {"cache": data, "archive": archive_result}
