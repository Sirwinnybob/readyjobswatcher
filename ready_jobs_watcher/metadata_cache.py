from __future__ import annotations

import json
import os
import re
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fitz
except Exception:  # pragma: no cover - dependency is optional for fallback page counts
    fitz = None

from .metadata_snapshot import archive_job_metadata


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


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def format_width(val: float) -> str:
    if val == -0.0:
        val = 0.0
    s = f"{val:.6f}".rstrip("0")
    if s.endswith("."):
        s = s[:-1]
    return s


def has_3d_assets(job_folder: Path) -> bool:
    three_d_dir = job_folder / "3D"
    if not three_d_dir.is_dir():
        return False
    for p in three_d_dir.glob("*"):
        if p.is_file() and p.suffix.lower() in (".glb", ".dae"):
            return True
        if p.is_dir():
            for sp in p.glob("*"):
                if sp.is_file() and sp.suffix.lower() in (".glb", ".dae"):
                    return True
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
            if entry.is_file() and entry.name.lower().endswith(".pdf"):
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


def consolidate_cnc_tracker(job_folder: Path):
    tracker_dir = job_folder / "CNC" / ".tracker"
    if not tracker_dir.exists():
        return

    actions = []
    device_files = []

    # Read existing consolidated actions first
    consolidated_file = tracker_dir / "consolidated.json"
    if consolidated_file.exists():
        try:
            data = _read_json(consolidated_file, {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
        except Exception:
            pass

    # Scan for new device-specific action files
    for entry in os.scandir(tracker_dir):
        if not entry.is_file() or not entry.name.endswith(".json") or entry.name == "consolidated.json":
            continue
        try:
            stat = entry.stat()
            data = _read_json(Path(entry.path), {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
                device_files.append((Path(entry.path), stat.st_mtime, stat.st_size))
        except Exception:
            pass

    # If no new device files exist, do not rewrite or delete anything
    if not device_files:
        return

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
        page_states.setdefault(key, {"latest_action": "", "timestamp": "", "bad_parts": set()})
        state = page_states[key]
        if action == "bad_part" and part is not None:
            state["bad_parts"].add(part)
        elif action == "unbad_part" and part is not None:
            state["bad_parts"].discard(part)
        elif action in ("complete", "skip", "unskip"):
            if not state["timestamp"] or timestamp > state["timestamp"]:
                state["latest_action"] = action
                state["timestamp"] = timestamp

    consolidated_actions = []
    for (filename, page, fingerprint), state in page_states.items():
        if state["latest_action"] in ("complete", "skip"):
            act = {"file": filename, "page": page, "action": state["latest_action"], "timestamp": state["timestamp"]}
            if fingerprint:
                act["fileFingerprint"] = fingerprint
            consolidated_actions.append(act)
        for part in sorted(state["bad_parts"]):
            act = {
                "file": filename,
                "page": page,
                "action": "bad_part",
                "part": part,
                "timestamp": state["timestamp"] or "2026-01-01T00:00:00Z",
            }
            if fingerprint:
                act["fileFingerprint"] = fingerprint
            consolidated_actions.append(act)

    _atomic_write_json(tracker_dir / "consolidated.json", {"tabletId": "consolidated", "actions": consolidated_actions})
    _delete_unchanged_device_files(device_files)


def consolidate_hardwoods_tracker(job_folder: Path):
    tracker_dir = job_folder / ".metadata" / "hardwoods" / ".tracker"
    if not tracker_dir.exists():
        return

    actions = []
    device_files = []

    # Read existing consolidated actions first
    consolidated_file = tracker_dir / "consolidated.json"
    if consolidated_file.exists():
        try:
            data = _read_json(consolidated_file, {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
        except Exception:
            pass

    # Scan for new device-specific action files
    for entry in os.scandir(tracker_dir):
        if not entry.is_file() or not entry.name.endswith(".json") or entry.name == "consolidated.json":
            continue
        try:
            stat = entry.stat()
            data = _read_json(Path(entry.path), {})
            if isinstance(data, dict) and "actions" in data:
                actions.extend(data["actions"])
                device_files.append((Path(entry.path), stat.st_mtime, stat.st_size))
        except Exception:
            pass

    # If no new device files exist, do not rewrite or delete anything
    if not device_files:
        return

    actions.sort(key=lambda a: a.get("timestamp", ""))
    done_count = {}
    skipped = set()
    timestamps = {}
    for action_obj in actions:
        doc_type = action_obj.get("docType")
        row_id = action_obj.get("rowId")
        action = action_obj.get("action")
        val = action_obj.get("value", 0)
        timestamp = action_obj.get("timestamp", "")
        if not doc_type or not row_id or not action:
            continue
        key = f"{doc_type}|{row_id}"
        timestamps[key] = timestamp
        if action == "set_done_count":
            done_count[key] = val
        elif action == "set_skipped":
            skipped.add(key)
        elif action == "clear_skipped":
            skipped.discard(key)

    consolidated_actions = []
    for key, ts in timestamps.items():
        doc_type, row_id = key.split("|", 1)
        val = done_count.get(key, 0)
        if val > 0:
            consolidated_actions.append(
                {"docType": doc_type, "rowId": row_id, "action": "set_done_count", "value": val, "timestamp": ts}
            )
        if key in skipped:
            consolidated_actions.append({"docType": doc_type, "rowId": row_id, "action": "set_skipped", "timestamp": ts})

    _atomic_write_json(tracker_dir / "consolidated.json", {"tabletId": "consolidated", "actions": consolidated_actions})
    _delete_unchanged_device_files(device_files)


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
                if entry.is_file() and predicate(path):
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
    force_rebuild: bool = False,
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
                consolidate_cnc_tracker(job_folder)
                consolidate_hardwoods_tracker(job_folder)

            cache_path = job_folder / ".metadata" / "cache_static.json"
            needs_rebuild = force_rebuild or not cache_path.exists()
            if not needs_rebuild:
                needs_rebuild = check_cache_needs_rebuild(job_folder, cache_path.stat().st_mtime)
            if needs_rebuild:
                generate_static_cache(job_folder, folder_name, lineup_positions.get(folder_name))
                summary["rebuilt"] += 1

            if archive and archive_root is not None:
                result = archive_job_metadata(base_path, job_folder, archive_root, reason="scheduled_cache_update")
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
    consolidate_trackers: bool = False,
) -> Dict[str, Any]:
    if not job_folder.is_dir():
        return {"skipped": "missing_job", "jobFolder": str(job_folder)}
    if not _read_deployed_flag(job_folder):
        return {"skipped": "not_deployed", "jobFolder": str(job_folder)}
    if consolidate_trackers:
        consolidate_cnc_tracker(job_folder)
        consolidate_hardwoods_tracker(job_folder)
    lineup_positions = {j["folderName"]: j["lineupPosition"] for j in compute_lineup(base_path, scan_jobs(base_path))}
    data = generate_static_cache(job_folder, job_folder.name, lineup_positions.get(job_folder.name))
    archive_result = None
    if archive_root is not None:
        archive_result = archive_job_metadata(base_path, job_folder, archive_root, reason=reason)
    return {"cache": data, "archive": archive_result}
