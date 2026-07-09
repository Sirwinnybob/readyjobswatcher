from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .atomic_write import atomic_write_json as _shared_atomic_write_json
from .deployment_gate import DeploymentGateManager
from .metadata_cache import parse_job_folder_name, refresh_single_job


JOB_NUMBER_KEYS = {"jobNumber", "job_number", "jobNum", "job_num"}
ROOT_METADATA_FILES = ("production_order.json", "job_board.json")


@dataclass(frozen=True)
class JobRenameResult:
    old_name: str
    new_name: str
    renamed_folder: bool
    rewritten_files: tuple[Path, ...]
    cache_refreshed: bool


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write_json(path: Path, payload: Any) -> None:
    _shared_atomic_write_json(path, payload, indent=2, ensure_ascii=False)


def _replace_text(value: str, *, old_name: str, new_name: str, old_job_name: str, new_job_name: str) -> str:
    updated = value
    if old_name:
        updated = updated.replace(old_name, new_name)
    if old_job_name:
        updated = updated.replace(old_job_name, new_job_name)
    return updated


def _rewrite_json_value(
    value: Any,
    *,
    key: Optional[str],
    old_name: str,
    new_name: str,
    old_num: str,
    new_num: str,
    old_job_name: str,
    new_job_name: str,
) -> tuple[Any, bool]:
    if isinstance(value, dict):
        changed = False
        rewritten: dict[Any, Any] = {}
        for raw_key, raw_value in value.items():
            new_key = raw_key
            if isinstance(raw_key, str):
                replaced_key = _replace_text(
                    raw_key,
                    old_name=old_name,
                    new_name=new_name,
                    old_job_name=old_job_name,
                    new_job_name=new_job_name,
                )
                if replaced_key != raw_key:
                    new_key = replaced_key
                    changed = True
            rewritten_value, value_changed = _rewrite_json_value(
                raw_value,
                key=str(new_key) if isinstance(new_key, str) else None,
                old_name=old_name,
                new_name=new_name,
                old_num=old_num,
                new_num=new_num,
                old_job_name=old_job_name,
                new_job_name=new_job_name,
            )
            rewritten[new_key] = rewritten_value
            changed = changed or value_changed
        return rewritten, changed

    if isinstance(value, list):
        changed = False
        rewritten_items = []
        for item in value:
            rewritten_item, item_changed = _rewrite_json_value(
                item,
                key=key,
                old_name=old_name,
                new_name=new_name,
                old_num=old_num,
                new_num=new_num,
                old_job_name=old_job_name,
                new_job_name=new_job_name,
            )
            rewritten_items.append(rewritten_item)
            changed = changed or item_changed
        return rewritten_items, changed

    if isinstance(value, str):
        updated = _replace_text(
            value,
            old_name=old_name,
            new_name=new_name,
            old_job_name=old_job_name,
            new_job_name=new_job_name,
        )
        if key in JOB_NUMBER_KEYS and old_num and value == old_num:
            updated = new_num
        return updated, updated != value

    return value, False


def _rewrite_json_file(
    path: Path,
    *,
    old_name: str,
    new_name: str,
    old_num: str,
    new_num: str,
    old_job_name: str,
    new_job_name: str,
) -> bool:
    if not path.is_file() or path.suffix.lower() != ".json":
        return False
    try:
        data = _read_json(path)
    except Exception:
        return False
    rewritten, changed = _rewrite_json_value(
        data,
        key=None,
        old_name=old_name,
        new_name=new_name,
        old_num=old_num,
        new_num=new_num,
        old_job_name=old_job_name,
        new_job_name=new_job_name,
    )
    if changed:
        _atomic_write_json(path, rewritten)
    return changed


def _iter_job_metadata_json(job_folder: Path):
    for folder in (job_folder / ".metadata", job_folder / "CNC" / ".metadata"):
        if not folder.is_dir():
            continue
        yield from (path for path in folder.rglob("*.json") if path.is_file())


def _iter_root_metadata_json(root: Path):
    metadata_root = root / ".metadata"
    if not metadata_root.is_dir():
        return
    yield from (path for path in metadata_root.rglob("*.json") if path.is_file())


def _normalize_deployment_gate(root: Path, job_name: str) -> None:
    gate = DeploymentGateManager(str(root))
    state = gate.load_state(job_name, create_if_missing=True, default_deployed=True)
    gate.save_state(job_name, state)


def rename_ready_job(
    root_dir: str | Path,
    old_name: str,
    new_name: str,
    *,
    archive_root: str | Path | None = None,
) -> JobRenameResult:
    root = Path(root_dir)
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if not old_name:
        raise ValueError("old_name is required")
    if not new_name:
        raise ValueError("new_name is required")
    if old_name == new_name:
        return JobRenameResult(old_name, new_name, False, tuple(), False)

    old_path = root / old_name
    new_path = root / new_name
    renamed_folder = False
    if old_path.exists():
        if new_path.exists():
            raise FileExistsError(f"Destination job already exists: {new_path}")
        old_path.rename(new_path)
        renamed_folder = True
    elif not new_path.exists():
        raise FileNotFoundError(f"Job folder does not exist: {old_path}")

    old_num, old_job_name = parse_job_folder_name(old_name)
    new_num, new_job_name = parse_job_folder_name(new_name)
    rewritten_files: list[Path] = []

    for root_file in ROOT_METADATA_FILES:
        path = root / root_file
        if _rewrite_json_file(
            path,
            old_name=old_name,
            new_name=new_name,
            old_num=old_num,
            new_num=new_num,
            old_job_name=old_job_name,
            new_job_name=new_job_name,
        ):
            rewritten_files.append(path)

    for path in _iter_root_metadata_json(root) or ():
        if _rewrite_json_file(
            path,
            old_name=old_name,
            new_name=new_name,
            old_num=old_num,
            new_num=new_num,
            old_job_name=old_job_name,
            new_job_name=new_job_name,
        ):
            rewritten_files.append(path)

    for path in _iter_job_metadata_json(new_path):
        if _rewrite_json_file(
            path,
            old_name=old_name,
            new_name=new_name,
            old_num=old_num,
            new_num=new_num,
            old_job_name=old_job_name,
            new_job_name=new_job_name,
        ):
            rewritten_files.append(path)

    _normalize_deployment_gate(root, new_name)
    cache_result = refresh_single_job(
        root,
        new_path,
        reason="job_renamed",
        archive_root=Path(archive_root) if archive_root is not None else None,
        consolidate_trackers=False,
    )
    cache_refreshed = "cache" in cache_result

    return JobRenameResult(
        old_name=old_name,
        new_name=new_name,
        renamed_folder=renamed_folder,
        rewritten_files=tuple(rewritten_files),
        cache_refreshed=cache_refreshed,
    )
