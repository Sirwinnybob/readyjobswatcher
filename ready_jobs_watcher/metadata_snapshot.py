from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import uuid
import re
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


def _read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _parse_snapshot_timestamp(snapshot_dir: Path) -> Optional[datetime.datetime]:
    try:
        date_part = snapshot_dir.parent.name
        stamp_part = snapshot_dir.name.split("-", 1)[0]
        if len(stamp_part) != 6 or not stamp_part.isdigit():
            return None
        snapshot_date = datetime.date.fromisoformat(date_part)
        snapshot_time = datetime.time(
            int(stamp_part[0:2]),
            int(stamp_part[2:4]),
            int(stamp_part[4:6]),
            tzinfo=datetime.timezone.utc,
        )
        return datetime.datetime.combine(snapshot_date, snapshot_time)
    except Exception:
        return None


def _snapshot_slot_for(created: datetime.datetime) -> str:
    hour = created.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _find_existing_snapshot_for_slot(job_root: Path, date_dir: str, slot: str) -> Optional[Path]:
    date_root = job_root / date_dir
    if not date_root.is_dir():
        return None

    for manifest_path in sorted(date_root.rglob("manifest.json")):
        manifest = _read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            continue
        if manifest.get("snapshotSlot") == slot:
            return manifest_path.parent
    return None


def _prune_snapshot_history(
    archive_base: Path,
    job_component: str,
    *,
    retention_days: Optional[int],
    max_snapshots_per_job: Optional[int] = None,
    keep_snapshot_dir: Optional[Path] = None,
) -> None:
    job_root = archive_base / job_component
    if not job_root.exists():
        return

    cutoff = None
    if retention_days is not None and retention_days >= 0:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)

    for date_dir in list(job_root.iterdir()):
        if not date_dir.is_dir():
            continue
        for snapshot_dir in list(date_dir.iterdir()):
            if not snapshot_dir.is_dir():
                continue
            if keep_snapshot_dir is not None and snapshot_dir == keep_snapshot_dir:
                continue
            snapshot_ts = _parse_snapshot_timestamp(snapshot_dir)
            if snapshot_ts is None:
                continue
            if cutoff is not None and snapshot_ts < cutoff:
                shutil.rmtree(snapshot_dir, ignore_errors=True)
        try:
            if date_dir.is_dir() and not any(date_dir.iterdir()):
                date_dir.rmdir()
        except OSError:
            pass

    if max_snapshots_per_job is not None and max_snapshots_per_job > 0 and job_root.exists():
        snapshots: list[tuple[datetime.datetime, Path]] = []
        for date_dir in job_root.iterdir():
            if not date_dir.is_dir():
                continue
            for snapshot_dir in date_dir.iterdir():
                if not snapshot_dir.is_dir():
                    continue
                snapshot_ts = _parse_snapshot_timestamp(snapshot_dir)
                if snapshot_ts is not None:
                    snapshots.append((snapshot_ts, snapshot_dir))
        snapshots.sort(key=lambda item: item[0], reverse=True)
        for _, snapshot_dir in snapshots[max_snapshots_per_job:]:
            if keep_snapshot_dir is not None and snapshot_dir == keep_snapshot_dir:
                continue
            shutil.rmtree(snapshot_dir, ignore_errors=True)

        for date_dir in list(job_root.iterdir()):
            try:
                if date_dir.is_dir() and not any(date_dir.iterdir()):
                    date_dir.rmdir()
            except OSError:
                pass

    try:
        if job_root.is_dir() and not any(job_root.iterdir()):
            job_root.rmdir()
    except OSError:
        pass


def _live_job_folder_names(root_dir: Path) -> set[str]:
    job_names: set[str] = set()
    if not root_dir.exists():
        return job_names

    for entry in root_dir.iterdir():
        if not entry.is_dir():
            continue
        if not re.match(r"^([A-Za-z0-9][A-Za-z0-9-]*)\s+-\s+(.+)$", entry.name):
            continue
        job_names.add(entry.name)
    return job_names


def prune_orphan_job_archives(
    root_dir: os.PathLike | str,
    archive_root: os.PathLike | str,
) -> dict[str, int]:
    root = Path(root_dir)
    archive_base = Path(archive_root)
    summary = {"scanned": 0, "removed": 0}

    if not archive_base.exists():
        return summary

    live_job_names = _live_job_folder_names(root)

    for job_component_dir in archive_base.iterdir():
        if not job_component_dir.is_dir():
            continue
        summary["scanned"] += 1

        job_folder_name = None
        manifest_paths = sorted(job_component_dir.rglob("manifest.json"))
        for manifest_path in manifest_paths:
            try:
                manifest = _read_json(manifest_path, {})
            except Exception:
                continue
            if isinstance(manifest, dict):
                candidate = manifest.get("jobFolderName")
                if isinstance(candidate, str) and candidate.strip():
                    job_folder_name = candidate
                    break

        if job_folder_name is None:
            continue
        if job_folder_name in live_job_names:
            continue

        shutil.rmtree(job_component_dir, ignore_errors=True)
        summary["removed"] += 1

    return summary


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
    retention_days: Optional[int] = None,
    max_snapshots_per_job: Optional[int] = None,
    daypart_limit: bool = False,
) -> ArchiveResult:
    root = Path(root_dir)
    job = Path(job_folder)
    archive_base = Path(archive_root)
    created = _parse_timestamp(timestamp)
    date_dir = created.strftime("%Y-%m-%d")
    stamp = created.strftime("%H%M%S")
    reason_component = _safe_component(reason)
    job_component = _safe_component(job.name)
    slot = _snapshot_slot_for(created)
    job_archive_root = archive_base / job_component
    if daypart_limit:
        existing_snapshot = _find_existing_snapshot_for_slot(job_archive_root, date_dir, slot)
        if existing_snapshot is not None:
            manifest = _read_json(existing_snapshot / "manifest.json", {})
            files = manifest.get("files", []) if isinstance(manifest, dict) else []
            return ArchiveResult(success=True, snapshot_dir=existing_snapshot, files=files, errors=[])

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
        "snapshotSlot": slot,
        "createdAt": created.isoformat(),
        "sourceRoot": str(root),
        "success": not errors,
        "files": files,
        "errors": errors,
    }
    _atomic_write_json(snapshot_dir / "manifest.json", manifest)
    _prune_snapshot_history(
        archive_base,
        job_component,
        retention_days=retention_days,
        max_snapshots_per_job=max_snapshots_per_job,
        keep_snapshot_dir=snapshot_dir,
    )
    return ArchiveResult(success=not errors, snapshot_dir=snapshot_dir, files=files, errors=errors)
