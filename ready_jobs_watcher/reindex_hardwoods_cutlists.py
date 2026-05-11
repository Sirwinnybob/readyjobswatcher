"""
One-time hardwoods cutlist reindex entrypoint.

Rebuilds `.metadata/hardwoods/cutlist_index.json` for job folders by
reusing the existing watcher indexer path so row-ID reconciliation remains
consistent with normal runtime behavior.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from dataclasses import asdict
from dataclasses import dataclass
from typing import Iterable
from typing import List
from typing import Optional
from typing import Sequence
from typing import Set

from .hardwoods_cutlist_indexer import build_hardwoods_cutlist_index_for_job


LOGGER = logging.getLogger("hardwoods_reindex")


@dataclass
class JobResult:
    jobFolder: str
    status: str
    detail: str = ""


@dataclass
class ReindexSummary:
    startedAt: str
    completedAt: str
    root: str
    dryRun: bool
    jobsRequested: List[str]
    jobsDiscovered: int
    jobsProcessed: int
    jobsSucceeded: int
    jobsFailed: int
    missingJobs: List[str]
    results: List[JobResult]


def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _normalize_job_filter(values: Sequence[str]) -> Set[str]:
    out: Set[str] = set()
    for value in values:
        for token in str(value or "").split(","):
            cleaned = token.strip()
            if cleaned:
                out.add(cleaned)
    return out


def _discover_job_folders(root: str) -> List[str]:
    try:
        entries = os.scandir(root)
    except OSError:
        return []
    with entries:
        jobs = [
            entry.name
            for entry in entries
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    jobs.sort(key=lambda name: name.lower())
    return jobs


def run_reindex(
    root: str,
    *,
    dry_run: bool = False,
    jobs: Optional[Iterable[str]] = None,
) -> ReindexSummary:
    if not os.path.isdir(root):
        raise ValueError(f"--root must be an existing directory: {root}")

    started_at = dt.datetime.now(dt.timezone.utc)
    discovered_jobs = _discover_job_folders(root)
    discovered_set = set(discovered_jobs)
    requested = sorted(set(jobs or []))

    if requested:
        selected_jobs = [job for job in discovered_jobs if job in requested]
        missing_jobs = sorted(job for job in requested if job not in discovered_set)
    else:
        selected_jobs = discovered_jobs
        missing_jobs = []

    results: List[JobResult] = []
    succeeded = 0
    failed = 0

    for job_folder in selected_jobs:
        job_path = os.path.join(root, job_folder)
        if dry_run:
            result = JobResult(jobFolder=job_folder, status="would_reindex", detail="dry-run")
            results.append(result)
            LOGGER.info("hardwoods_reindex_job job=%s status=%s", job_folder, result.status)
            succeeded += 1
            continue

        try:
            ok = build_hardwoods_cutlist_index_for_job(job_path)
            if ok:
                result = JobResult(jobFolder=job_folder, status="success")
                succeeded += 1
            else:
                result = JobResult(jobFolder=job_folder, status="failed", detail="indexer returned false")
                failed += 1
        except Exception as exc:  # pragma: no cover - defensive branch
            result = JobResult(jobFolder=job_folder, status="failed", detail=str(exc))
            failed += 1

        results.append(result)
        LOGGER.info("hardwoods_reindex_job job=%s status=%s detail=%s", job_folder, result.status, result.detail)

    completed_at = dt.datetime.now(dt.timezone.utc)
    return ReindexSummary(
        startedAt=started_at.isoformat(),
        completedAt=completed_at.isoformat(),
        root=root,
        dryRun=dry_run,
        jobsRequested=requested,
        jobsDiscovered=len(discovered_jobs),
        jobsProcessed=len(selected_jobs),
        jobsSucceeded=succeeded,
        jobsFailed=failed,
        missingJobs=missing_jobs,
        results=results,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild hardwoods cutlist indexes for Ready Jobs folders."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Ready Jobs root directory.",
    )
    parser.add_argument(
        "--job",
        action="append",
        default=[],
        help="Optional job folder filter. Repeat flag or pass comma-separated values.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be reindexed without writing any index files.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_logging()
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    jobs_filter = _normalize_job_filter(args.job)

    try:
        summary = run_reindex(
            args.root,
            dry_run=bool(args.dry_run),
            jobs=sorted(jobs_filter),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    serializable = asdict(summary)
    serializable["results"] = [asdict(item) for item in summary.results]
    print(json.dumps(serializable, indent=2))
    return 1 if summary.jobsFailed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
