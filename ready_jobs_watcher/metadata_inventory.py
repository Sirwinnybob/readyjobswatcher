from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class OwnershipMode(str, Enum):
    DERIVED_OWNED = "derived_owned"
    EXTERNAL_SOURCE = "external_source"
    IGNORED_GENERATED = "ignored_generated"


@dataclass(frozen=True)
class MetadataClassification:
    ownership: OwnershipMode
    relative_path: str
    reason: str


_DERIVED_OWNED_SUFFIXES = {
    ".metadata/cache_static.json",
    ".metadata/deployment_gate.json",
    ".metadata/cabinet_sheet_index.json",
    ".metadata/hardwoods/cutlist_index.json",
    ".metadata/hardwoods/cutlist_revisions.json",
    "CNC/.tracker/consolidated.json",
    ".metadata/hardwoods/.tracker/consolidated.json",
}


def _relative(path: Path, root_dir: Path) -> str:
    try:
        rel = path.resolve().relative_to(root_dir.resolve())
    except (OSError, ValueError):
        rel = path
    return rel.as_posix()


def _parts_lower(rel: str) -> list[str]:
    return [part.lower() for part in rel.replace("\\", "/").split("/")]


def classify_metadata_path(path: os.PathLike | str, root_dir: os.PathLike | str) -> MetadataClassification:
    root = Path(root_dir)
    p = Path(path)
    rel = _relative(p, root)
    rel_lower = rel.lower()
    parts = _parts_lower(rel)
    name = p.name.lower()

    if name.endswith(".tmp") or name.endswith(".ocr.tmp") or ".tmp." in name:
        return MetadataClassification(OwnershipMode.IGNORED_GENERATED, rel, "temporary_file")
    if ".thumbs" in parts or ".fullimages" in parts:
        return MetadataClassification(OwnershipMode.IGNORED_GENERATED, rel, "generated_media_cache")
    if name.startswith("."):
        return MetadataClassification(OwnershipMode.IGNORED_GENERATED, rel, "hidden_generated_marker")

    normalized_rel = rel.replace("\\", "/")
    for suffix in _DERIVED_OWNED_SUFFIXES:
        if normalized_rel.endswith(suffix):
            return MetadataClassification(OwnershipMode.DERIVED_OWNED, rel, "ready_jobs_derived")

    if rel_lower == "production_order.json":
        return MetadataClassification(OwnershipMode.EXTERNAL_SOURCE, rel, "hours_tracker_production_order")
    if rel_lower.startswith(".metadata/"):
        return MetadataClassification(OwnershipMode.EXTERNAL_SOURCE, rel, "hours_tracker_global_metadata")
    if "/.metadata/admin/" in rel_lower:
        return MetadataClassification(OwnershipMode.EXTERNAL_SOURCE, rel, "hours_tracker_admin_metadata")
    if "/cnc/.metadata/" in rel_lower:
        return MetadataClassification(OwnershipMode.EXTERNAL_SOURCE, rel, "pgm_sorting_cnc_sidecar")
    if "/.metadata/" in rel_lower and (name.endswith(".json") or "." not in name):
        return MetadataClassification(OwnershipMode.EXTERNAL_SOURCE, rel, "metadata_source")
    if "/.tracker/" in rel_lower and name.endswith(".json"):
        return MetadataClassification(OwnershipMode.EXTERNAL_SOURCE, rel, "tracker_source")
    if name.endswith(".pdf"):
        return MetadataClassification(OwnershipMode.EXTERNAL_SOURCE, rel, "pdf_source")

    return MetadataClassification(OwnershipMode.IGNORED_GENERATED, rel, "outside_metadata_inventory")


def is_rebuild_trigger(path: os.PathLike | str, root_dir: os.PathLike | str) -> bool:
    classification = classify_metadata_path(path, root_dir)
    if classification.ownership == OwnershipMode.IGNORED_GENERATED:
        return False
    if classification.ownership == OwnershipMode.DERIVED_OWNED:
        return False
    return True


def find_job_folder_for_path(path: os.PathLike | str, root_dir: os.PathLike | str) -> Optional[Path]:
    root = Path(root_dir)
    p = Path(path)
    try:
        rel = p.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not rel.parts:
        return None
    candidate = root / rel.parts[0]
    if candidate == root or str(rel.parts[0]).startswith("."):
        return None
    return candidate
