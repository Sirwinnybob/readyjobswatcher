from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .metadata_inventory import OwnershipMode, classify_metadata_path


@dataclass(frozen=True)
class ArchiveResult:
    success: bool
    snapshot_dir: Path
    files: list[dict]
    errors: list[str]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_timestamp(value: Optional[str]) -> datetime.datetime:
    if value:
        try:
            parsed = datetime.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
        except ValueError:
            pass
    return datetime.datetime.now(datetime.timezone.utc)


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value).strip("_") or "snapshot"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f"{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(source, tmp)
    os.replace(tmp, destination)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _storage_relative_path(relative_path: str) -> Path:
    if relative_path.startswith("../"):
        return Path("__root__") / relative_path[3:]
    return Path(relative_path)


def _iter_candidate_files(root_dir: Path, job_folder: Path) -> Iterable[tuple[Path, str]]:
    if (root_dir / "production_order.json").is_file():
        yield root_dir / "production_order.json", "../production_order.json"

    global_metadata = root_dir / ".metadata"
    if global_metadata.is_dir():
        for path in sorted(global_metadata.rglob("*")):
            if path.is_file():
                yield path, "../" + path.relative_to(root_dir).as_posix()

    for metadata_root in (
        job_folder / ".metadata",
        job_folder / "CNC" / ".metadata",
        job_folder / "CNC" / ".tracker",
    ):
        if metadata_root.is_dir():
            for path in sorted(metadata_root.rglob("*")):
                if path.is_file():
                    yield path, path.relative_to(job_folder).as_posix()


def archive_job_metadata(
    root_dir: os.PathLike | str,
    job_folder: os.PathLike | str,
    archive_root: os.PathLike | str,
    *,
    reason: str,
    timestamp: Optional[str] = None,
) -> ArchiveResult:
    root = Path(root_dir)
    job = Path(job_folder)
    archive_base = Path(archive_root)
    created = _parse_timestamp(timestamp)
    date_dir = created.strftime("%Y-%m-%d")
    stamp = created.strftime("%H%M%S")
    reason_component = _safe_component(reason)
    job_component = _safe_component(job.name)
    snapshot_dir = archive_base / job_component / date_dir / f"{stamp}-{reason_component}"

    if snapshot_dir.exists():
        snapshot_dir = snapshot_dir.with_name(f"{snapshot_dir.name}-{uuid.uuid4().hex[:8]}")

    files: list[dict] = []
    errors: list[str] = []
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for source, relative_path in _iter_candidate_files(root, job):
        classification = classify_metadata_path(source, root)
        if classification.ownership == OwnershipMode.IGNORED_GENERATED:
            continue
        destination = snapshot_dir / "files" / _storage_relative_path(relative_path)
        try:
            stat = source.stat()
            _atomic_copy(source, destination)
            files.append(
                {
                    "relativePath": relative_path,
                    "ownership": classification.ownership.value,
                    "size": stat.st_size,
                    "modifiedAt": datetime.datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=datetime.timezone.utc,
                    ).isoformat(),
                    "sha256": _sha256(source),
                }
            )
        except Exception as exc:
            errors.append(f"{relative_path}: {exc}")

    manifest = {
        "schemaVersion": 1,
        "jobFolderName": job.name,
        "reason": reason,
        "createdAt": created.isoformat(),
        "sourceRoot": str(root),
        "success": not errors,
        "files": files,
        "errors": errors,
    }
    _atomic_write_json(snapshot_dir / "manifest.json", manifest)
    return ArchiveResult(success=not errors, snapshot_dir=snapshot_dir, files=files, errors=errors)
