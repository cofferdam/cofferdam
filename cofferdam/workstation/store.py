"""Bounded JSON persistence for recent action records.

M1 needs to remember very little: the last N action records (so the phone shows
history after a reconnect or restart) and the screenshot files those records
point at. A database would be premature — this is a single-user, single-process
service writing a small capped list, so an atomically-replaced JSON file is the
honest choice. If task/update records (M4/M6) outgrow it, they get SQLite.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import List

from .config import Config


class ActionStore:
    """A capped, atomically-persisted list of action records (newest first)."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._records: List[dict] = self._read()

    # -- persistence ---------------------------------------------------------

    def _read(self) -> List[dict]:
        try:
            raw = self._config.actions_path.read_text(encoding="utf-8")
        except OSError:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            return []  # corrupt file: start clean rather than crash the service
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)][: self._config.max_action_records]

    def _write_locked(self) -> None:
        path = self._config.actions_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._records, ensure_ascii=False, indent=2, sort_keys=True)
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".actions-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, path)
        except OSError:
            # Persistence is best-effort: losing history must never fail an action.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    # -- API -----------------------------------------------------------------

    def add(self, record: dict) -> None:
        with self._lock:
            self._records.insert(0, record)
            del self._records[self._config.max_action_records :]
            self._write_locked()

    def update(self, action_id: str, record: dict) -> None:
        with self._lock:
            for index, existing in enumerate(self._records):
                if existing.get("action_id") == action_id:
                    self._records[index] = record
                    break
            else:
                self._records.insert(0, record)
                del self._records[self._config.max_action_records :]
            self._write_locked()

    def recent(self, limit: int = 20) -> List[dict]:
        with self._lock:
            return list(self._records[: max(0, limit)])

    def get(self, action_id: str) -> dict | None:
        with self._lock:
            for record in self._records:
                if record.get("action_id") == action_id:
                    return dict(record)
        return None


def prune_screenshots(config: Config) -> None:
    """Keep only the newest ``max_screenshots`` PNG artifacts."""
    directory = config.screenshots_dir
    try:
        files = sorted(
            (p for p in directory.glob("*.png") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in files[config.max_screenshots :]:
        try:
            stale.unlink()
        except OSError:
            pass


def screenshot_path(config: Config, action_id: str) -> Path:
    """Path for an action's screenshot. ``action_id`` is service-generated."""
    return config.screenshots_dir / f"{action_id}.png"
