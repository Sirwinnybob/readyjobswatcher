"""Polling reconciliation for Ready Jobs filesystem changes.

The network share can miss watchdog notifications. This module keeps a local
snapshot and dispatches stable differences through the existing watcher
handlers so polling and watchdog share the same side effects.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from .file_handler import JobProcessor, should_ignore_folder
from .utils import is_hidden


main_logger = logging.getLogger("main")


SnapshotEntry = Dict[str, object]
PendingKey = Tuple[str, str, str, str]


class ReadyJobsPoller:
    def __init__(self, config, snapshot_path, rename_handler, pdf_handler):
        self.config = config
        self.snapshot_path = Path(snapshot_path)
        self.rename_handler = rename_handler
        self.pdf_handler = pdf_handler
        self.stable_poll_count = max(1, int(getattr(config, "ready_jobs_stable_poll_count", 2)))
        self._pending: Dict[PendingKey, int] = {}
        self._snapshot = self._load_snapshot()

    def _load_snapshot(self) -> Dict:
        try:
            with self.snapshot_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                return data
        except FileNotFoundError:
            pass
        except Exception as exc:
            main_logger.warning("Failed loading polling snapshot %s: %s", self.snapshot_path, exc)
        return {"version": 1, "root_dir": self.config.ROOT_DIR, "entries": {}}

    def _save_snapshot(self) -> None:
        try:
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.snapshot_path.with_suffix(self.snapshot_path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(self._snapshot, f, indent=2, sort_keys=True)
            os.replace(temp_path, self.snapshot_path)
        except Exception as exc:
            main_logger.error("Failed saving polling snapshot %s: %s", self.snapshot_path, exc, exc_info=True)

    def _rel_path(self, path: Path) -> str:
        return path.relative_to(self.config.ROOT_DIR).as_posix()

    @staticmethod
    def _entry_signature(entry: SnapshotEntry) -> str:
        return f"{entry.get('is_dir')}:{entry.get('size')}:{entry.get('mtime_ns')}"

    def _entry_for_path(self, path: Path, *, is_root_entry: bool) -> Optional[SnapshotEntry]:
        try:
            stat = path.stat()
            is_dir = path.is_dir()
            return {
                "is_dir": is_dir,
                "size": None if is_dir else stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "root_entry": bool(is_root_entry),
            }
        except OSError as exc:
            main_logger.debug("Polling skipped unavailable path %s: %s", path, exc)
            return None

    def _scan_root_entries(self) -> Dict[str, SnapshotEntry]:
        root = Path(self.config.ROOT_DIR)
        entries: Dict[str, SnapshotEntry] = {}
        try:
            for child in root.iterdir():
                try:
                    if not child.is_dir():
                        continue
                    if child.name.startswith(".") or should_ignore_folder(child.name) or is_hidden(str(child)):
                        continue
                    entry = self._entry_for_path(child, is_root_entry=True)
                    if entry is not None:
                        entries[self._rel_path(child)] = entry
                except OSError as exc:
                    main_logger.debug("Polling skipped root entry %s: %s", child, exc)
        except OSError as exc:
            main_logger.warning("Ready Jobs root polling failed for %s: %s", root, exc)
        return entries

    def _scan_file_entries(self) -> Dict[str, SnapshotEntry]:
        root = Path(self.config.ROOT_DIR)
        entries: Dict[str, SnapshotEntry] = {}
        try:
            iterator: Iterable[Path] = root.rglob("*")
            for child in iterator:
                try:
                    if not child.is_file():
                        continue
                    entry = self._entry_for_path(child, is_root_entry=False)
                    if entry is not None:
                        entries[self._rel_path(child)] = entry
                except OSError as exc:
                    main_logger.debug("Polling skipped file entry %s: %s", child, exc)
        except OSError as exc:
            main_logger.warning("Ready Jobs file polling failed for %s: %s", root, exc)
        return entries

    def _is_stable(self, event_type: str, src_rel: str, dest_rel: str, signature: str) -> bool:
        key = (event_type, src_rel, dest_rel, signature)
        count = self._pending.get(key, 0) + 1
        self._pending = {existing: value for existing, value in self._pending.items() if existing[:3] != key[:3]}
        self._pending[key] = count
        if count >= self.stable_poll_count:
            self._pending.pop(key, None)
            return True
        return False

    def _abs(self, rel_path: str) -> str:
        return str(Path(self.config.ROOT_DIR) / Path(rel_path))

    def _dispatch_created(self, rel_path: str, entry: SnapshotEntry) -> None:
        path = self._abs(rel_path)
        is_dir = bool(entry.get("is_dir"))
        if hasattr(self.rename_handler, "handle_created_path"):
            self.rename_handler.handle_created_path(path, is_dir)
        if not is_dir and hasattr(self.pdf_handler, "handle_created_path"):
            self.pdf_handler.handle_created_path(path, False)

    def _dispatch_modified(self, rel_path: str, entry: SnapshotEntry) -> None:
        if bool(entry.get("is_dir")):
            return
        path = self._abs(rel_path)
        if hasattr(self.rename_handler, "handle_modified_path"):
            self.rename_handler.handle_modified_path(path, False)
        if hasattr(self.pdf_handler, "handle_modified_path"):
            self.pdf_handler.handle_modified_path(path, False)

    def _dispatch_deleted(self, rel_path: str, entry: SnapshotEntry) -> None:
        path = self._abs(rel_path)
        is_dir = bool(entry.get("is_dir"))
        if not is_dir and hasattr(self.pdf_handler, "handle_deleted_path"):
            self.pdf_handler.handle_deleted_path(path, False)

    def _dispatch_moved(self, src_rel: str, dest_rel: str, entry: SnapshotEntry) -> None:
        if hasattr(self.rename_handler, "handle_moved_path"):
            self.rename_handler.handle_moved_path(self._abs(src_rel), self._abs(dest_rel), bool(entry.get("is_dir")))

    def _looks_like_job_dir(self, rel_path: str, entry: SnapshotEntry) -> bool:
        if not bool(entry.get("is_dir")):
            return False
        if "/" in rel_path:
            return False
        return JobProcessor.is_job_folder(self._abs(rel_path))

    def poll_once(self, *, scan_root: bool, scan_files: bool) -> None:
        entries: Dict[str, SnapshotEntry] = dict(self._snapshot.get("entries", {}))
        current: Dict[str, SnapshotEntry] = {}
        scanned_rels = set()

        if scan_root:
            root_entries = self._scan_root_entries()
            current.update(root_entries)
            previous_root = {
                rel
                for rel, entry in entries.items()
                if bool(entry.get("root_entry"))
            }
            scanned_rels.update(previous_root | set(root_entries))

        if scan_files:
            file_entries = self._scan_file_entries()
            current.update(file_entries)
            previous_files = {
                rel
                for rel, entry in entries.items()
                if not bool(entry.get("is_dir"))
            }
            scanned_rels.update(previous_files | set(file_entries))

        if not scanned_rels:
            self._snapshot = {"version": 1, "root_dir": self.config.ROOT_DIR, "entries": entries}
            self._save_snapshot()
            return

        if not entries:
            entries.update(current)
            self._snapshot = {"version": 1, "root_dir": self.config.ROOT_DIR, "entries": entries}
            self._save_snapshot()
            return

        created = [rel for rel in scanned_rels if rel in current and rel not in entries]
        deleted = [rel for rel in scanned_rels if rel in entries and rel not in current]
        modified = [
            rel
            for rel in scanned_rels
            if rel in current
            and rel in entries
            and self._entry_signature(current[rel]) != self._entry_signature(entries[rel])
        ]

        handled_created = set()
        handled_deleted = set()
        root_created = [rel for rel in created if self._looks_like_job_dir(rel, current[rel])]
        root_deleted = [rel for rel in deleted if self._looks_like_job_dir(rel, entries[rel])]
        if scan_root and len(root_created) == 1 and len(root_deleted) == 1:
            src_rel = root_deleted[0]
            dest_rel = root_created[0]
            signature = self._entry_signature(current[dest_rel])
            if self._is_stable("moved", src_rel, dest_rel, signature):
                self._dispatch_moved(src_rel, dest_rel, current[dest_rel])
                entries.pop(src_rel, None)
                entries[dest_rel] = current[dest_rel]
                handled_created.add(dest_rel)
                handled_deleted.add(src_rel)

        for rel in created:
            if rel in handled_created:
                continue
            signature = self._entry_signature(current[rel])
            if self._is_stable("created", "", rel, signature):
                self._dispatch_created(rel, current[rel])
                entries[rel] = current[rel]

        for rel in modified:
            signature = self._entry_signature(current[rel])
            if self._is_stable("modified", rel, "", signature):
                self._dispatch_modified(rel, current[rel])
                entries[rel] = current[rel]

        for rel in deleted:
            if rel in handled_deleted:
                continue
            signature = self._entry_signature(entries[rel])
            if self._is_stable("deleted", rel, "", signature):
                self._dispatch_deleted(rel, entries[rel])
                entries.pop(rel, None)

        self._snapshot = {"version": 1, "root_dir": self.config.ROOT_DIR, "entries": entries}
        self._save_snapshot()

    def run(self, stop_event) -> None:
        root_seconds = max(1.0, float(getattr(self.config, "ready_jobs_root_poll_seconds", 10)))
        file_seconds = max(1.0, float(getattr(self.config, "ready_jobs_file_poll_seconds", 60)))
        next_root = 0.0
        next_file = 0.0
        while not stop_event.is_set():
            now = time.monotonic()
            scan_root = now >= next_root
            scan_files = now >= next_file
            if scan_root or scan_files:
                self.poll_once(scan_root=scan_root, scan_files=scan_files)
                now = time.monotonic()
                if scan_root:
                    next_root = now + root_seconds
                if scan_files:
                    next_file = now + file_seconds
            wait_seconds = max(0.5, min(next_root, next_file) - time.monotonic())
            stop_event.wait(wait_seconds)
