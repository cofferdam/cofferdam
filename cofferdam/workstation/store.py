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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .config import Config


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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

    def record_overlay_event(self, operation: str, resource_id: str, result: str) -> None:
        """Audit one display-overlay write, successful or refused.

        Deliberately **without the label or the aliases**. They are the user's
        own words about their own home — "Büyük monitör", "Laptop ekranı" — and
        the action log is a broad surface: it is read by the PWA, kept on disk,
        and shown in a list beside everything else. Recording that a name was
        set, for which resource, and whether it worked is enough to audit the
        write path; recording *what* the name is would put personal content into
        a general-purpose log for no investigative gain.

        The resource id is a host-scoped digest, not a serial number or an EDID.
        """
        self.add(
            {
                "action_id": uuid.uuid4().hex,
                "action": operation,
                "status": "succeeded" if result == "ok" else "failed",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "params": {"resource_id": resource_id},
                "result": {"outcome": result},
                "error": None if result == "ok" else {"code": result},
                "stub": False,
            }
        )

    def record_audio_event(
        self,
        operation: str,
        resource_id: str | None,
        result: str,
        device_type: str | None = None,
    ) -> None:
        """Audit one audio action — applied, refused, or failed.

        Bounded on purpose, and narrower than it could be. What gets recorded is
        the operation, which resource it addressed, how it turned out, and the
        coarse device category. What does not get recorded is anything about
        *what was playing*: no stream titles, no application content, no
        property dumps. The same reasoning as display-overlay auditing — this
        log is read by the PWA, kept on disk, and shown beside everything else,
        so it carries what an audit needs and nothing that would turn it into a
        listening history.

        Volume levels are also left out. Knowing that the volume was changed and
        whether it worked is what makes this path auditable; a timestamped
        record of exactly how loud someone had their speakers all evening adds
        nothing to that and is a more personal trace than it first looks.
        """
        self.add(
            {
                "action_id": uuid.uuid4().hex,
                "action": operation,
                "status": "succeeded" if result == "ok" else "failed",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "params": {"resource_id": resource_id},
                "result": {"outcome": result, "device_type": device_type},
                "error": None if result == "ok" else {"code": result},
                "stub": False,
            }
        )

    def record_spotify_event(self, operation: str, result: str) -> None:
        """Audit one Spotify playback action — applied, refused, or failed.

        Narrower than any other audit in this file, because playback *is*
        personal activity. What someone listened to, when, and how often is a
        detailed picture of a person, and an action log carrying track titles
        would quietly become a listening history: kept on disk, shown in a list,
        and never asked for.

        So this records the operation and the outcome, and nothing else. Not the
        track, artist, album, or query; not the account; not the Spotify device
        id; not the volume. "A track was skipped at 21:04 and it worked" is
        enough to audit the write path, and it is the most that can be recorded
        without describing someone's evening.
        """
        self.add(
            {
                "action_id": uuid.uuid4().hex,
                "action": operation,
                "status": "succeeded" if result == "ok" else "failed",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "params": {},
                "result": {"outcome": result},
                "error": None if result == "ok" else {"code": result},
                "stub": False,
            }
        )

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
