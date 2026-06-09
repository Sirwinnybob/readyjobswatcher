"""
Unresolved bad-parts indexer for remake selection.

Builds per-job candidate files from CNC tracker action streams so downstream tools
can read precomputed remake candidates without re-parsing tracker logs.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Dict, List, Optional, Tuple

from .config import Config
from .tracker_action_stream import load_cnc_tracker_actions

main_logger = logging.getLogger("main")

REMAKE_CANDIDATES_FILENAME = "remake_bad_parts_candidates.json"


def _candidates_output_path(config: Config, job_folder_name: str) -> str:
    return os.path.join(
        config.ROOT_DIR,
        job_folder_name,
        config.CNC_SUBDIR,
        ".metadata",
        REMAKE_CANDIDATES_FILENAME,
    )


def _write_candidates(config: Config, job_folder_name: str, candidates: List[Dict]) -> None:
    out_path = _candidates_output_path(config, job_folder_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = {"jobFolderName": job_folder_name, "candidates": candidates}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _remove_candidates_file(config: Config, job_folder_name: str) -> None:
    out_path = _candidates_output_path(config, job_folder_name)
    if os.path.exists(out_path):
        os.remove(out_path)


def _collect_tracker_actions(tracker_dir: str) -> List[Dict]:
    return load_cnc_tracker_actions(tracker_dir, logger=main_logger)


def _load_metadata_by_pdf(metadata_dir: str) -> Dict[str, Dict]:
    metadata_by_pdf: Dict[str, Dict] = {}
    for mf in glob.glob(os.path.join(metadata_dir, "*.json")):
        try:
            with open(mf, "r", encoding="utf-8") as f:
                md = json.load(f)
            if isinstance(md, dict):
                pdf_name = md.get("pdfFilename")
                if pdf_name:
                    metadata_by_pdf[str(pdf_name)] = md
        except Exception as exc:
            main_logger.warning(f"Skipping malformed metadata file {mf}: {exc}")
    return metadata_by_pdf


def _build_candidates(job_folder_name: str, actions: List[Dict], metadata_by_pdf: Dict[str, Dict]) -> List[Dict]:
    sheets: Dict[Tuple[str, int], Dict] = {}
    for action in actions:
        pdf = action.get("file")
        page = action.get("page")
        action_name = str(action.get("action", "") or "")
        if not pdf or not isinstance(page, int) or not action_name:
            continue
        key = (str(pdf), page)
        entry = sheets.setdefault(
            key,
            {"complete": False, "skipped": False, "badParts": {}, "lastTs": ""},
        )
        if action_name == "complete":
            entry["complete"] = True
        elif action_name == "uncomplete":
            entry["complete"] = False
        elif action_name == "skip":
            entry["skipped"] = True
        elif action_name == "unskip":
            entry["skipped"] = False
        elif action_name in ("bad_part", "unbad_part"):
            part = action.get("part")
            if isinstance(part, int):
                entry["badParts"][part] = {
                    "isBad": action_name == "bad_part",
                    "fileFingerprint": str(action.get("fileFingerprint", "") or ""),
                }
        entry["lastTs"] = str(action.get("timestamp", entry["lastTs"]) or entry["lastTs"])

    unresolved: List[Dict] = []
    for (pdf, page), entry in sheets.items():
        active_bad_parts = sorted(
            [
                p
                for p, status in entry["badParts"].items()
                if (status.get("isBad") if isinstance(status, dict) else bool(status))
            ]
        )
        if not entry["complete"] or entry["skipped"] or not active_bad_parts:
            continue

        md = metadata_by_pdf.get(pdf, {})
        material_name = md.get("material") or os.path.splitext(pdf)[0]
        page_meta = None
        for pmd in md.get("pages", []):
            if pmd.get("pageNumber") == page:
                page_meta = pmd
                break
        parts_lookup = {}
        if page_meta:
            for part in page_meta.get("parts", []):
                num = part.get("number")
                if isinstance(num, int):
                    parts_lookup[num] = part

        for part_num in active_bad_parts:
            part_obj = parts_lookup.get(part_num, {})
            part_status = entry["badParts"].get(part_num, {})
            part_fp = ""
            if isinstance(part_status, dict):
                part_fp = str(part_status.get("fileFingerprint", "") or "")
            unresolved.append(
                {
                    "id": f"{pdf}|{page}|{part_num}",
                    "jobFolderName": job_folder_name,
                    "materialName": material_name,
                    "pdfFilename": pdf,
                    "sheetPage": page,
                    "partNumber": part_num,
                    "partName": part_obj.get("name", f"Part {part_num}"),
                    "fileFingerprint": part_fp,
                    "lastTouchedAt": entry["lastTs"],
                }
            )

    unresolved.sort(key=lambda x: (x["materialName"], x["sheetPage"], x["partNumber"]))
    return unresolved


def refresh_unresolved_bad_parts_for_job(config: Config, job_folder_name: str, deployment_gate=None) -> bool:
    job_root = os.path.join(config.ROOT_DIR, job_folder_name)
    if deployment_gate is not None and not deployment_gate.should_process_job_folder(job_root):
        _remove_candidates_file(config, job_folder_name)
        return False
    cnc_dir = os.path.join(job_root, config.CNC_SUBDIR)
    tracker_dir = os.path.join(cnc_dir, ".tracker")
    metadata_dir = os.path.join(cnc_dir, ".metadata")
    if not os.path.isdir(cnc_dir):
        _remove_candidates_file(config, job_folder_name)
        return False
    actions = _collect_tracker_actions(tracker_dir)
    if not actions:
        _remove_candidates_file(config, job_folder_name)
        return False
    metadata_by_pdf = _load_metadata_by_pdf(metadata_dir)
    candidates = _build_candidates(job_folder_name, actions, metadata_by_pdf)
    _write_candidates(config, job_folder_name, candidates)
    main_logger.info(
        "Remake candidates index updated: job=%s count=%s",
        job_folder_name,
        len(candidates),
    )
    return True


def refresh_unresolved_bad_parts_all(config: Config, deployment_gate=None) -> int:
    refreshed = 0
    root_dir = config.ROOT_DIR
    if not os.path.isdir(root_dir):
        return 0
    try:
        with os.scandir(root_dir) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                if refresh_unresolved_bad_parts_for_job(config, entry.name, deployment_gate=deployment_gate):
                    refreshed += 1
    except Exception as exc:
        main_logger.error(f"Failed refreshing unresolved bad parts across jobs: {exc}", exc_info=True)
    return refreshed


def derive_job_from_tracker_path(config: Config, src_path: str) -> Optional[str]:
    normalized = os.path.normpath(src_path)
    root = os.path.normpath(config.ROOT_DIR)
    try:
        rel = os.path.relpath(normalized, root)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    parts = rel.split(os.sep)
    if not parts:
        return None
    return parts[0]
