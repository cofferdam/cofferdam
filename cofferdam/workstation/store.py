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
from typing import List, Optional

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

    def record_spotify_event(
        self, operation: str, result: str, correlation_id: Optional[str] = None
    ) -> None:
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

        ``correlation_id`` (M2D.1) is the one addition, and it is safe precisely
        because it is meaningless on its own: random hex minted per operation,
        carrying nothing about the account or the track. It exists so a slow
        cold-start recovery can be lined up with the phase log it produced, which
        is what made diagnosing "Play now did nothing the first time" possible
        without recording what was played.
        """
        self.add(
            {
                "action_id": uuid.uuid4().hex,
                "action": operation,
                "status": "succeeded" if result == "ok" else "failed",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "params": {},
                "result": {"outcome": result, "correlation_id": correlation_id},
                "error": None if result == "ok" else {"code": result},
                "stub": False,
            }
        )

    def record_youtube_event(
        self, operation: str, result: str, correlation_id: Optional[str] = None
    ) -> None:
        """Audit one YouTube player action — applied, refused, or failed.

        As narrow as the Spotify audit above, and for the same reason: what
        someone watched, when, and how often is a detailed picture of a person,
        and an action log carrying video titles would quietly become a viewing
        history — kept on disk, shown in a list, and never asked for.

        So this records the operation and the outcome, and nothing else. Not the
        video id, title, channel or search query; not the queue contents; not
        the player's event payloads; not the volume; not the player URL or its
        port. "A video was skipped at 21:04 and it worked" is enough to audit
        the write path, and it is the most that can be recorded without
        describing someone's evening.

        ``correlation_id`` is safe precisely because it is meaningless on its
        own: random hex minted per operation, carrying nothing about the video.
        It exists so a slow player launch can be lined up with the phase log it
        produced, without recording what was played.
        """
        self.add(
            {
                "action_id": uuid.uuid4().hex,
                "action": operation,
                "status": "succeeded" if result == "ok" else "failed",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "params": {},
                "result": {"outcome": result, "correlation_id": correlation_id},
                "error": None if result == "ok" else {"code": result},
                "stub": False,
            }
        )

    def record_remote_control_event(
        self,
        operation: str,
        result: str,
        project_id: Optional[str] = None,
        unit: Optional[str] = None,
        generation: Optional[str] = None,
        state: Optional[str] = None,
    ) -> None:
        """Audit one native Remote Control lifecycle operation.

        Narrower than :meth:`record_task_event`, and for a sharper reason. The
        thing this lane handles that no other does is a **session URL**, which
        is capability material: anyone holding it can reach a live interactive
        agent inside a registered project. An action log is kept on disk, listed
        in the PWA beside the volume changes, and read by whoever is debugging —
        so a URL in it would be a credential in it.

        There is therefore **no parameter that can carry one**. The signature
        accepts six values and every one is either an id Cofferdam minted or a
        word from a closed vocabulary: which operation, how it turned out, which
        project, which unit, which process generation, and which lifecycle
        state. ``url_available`` is not recorded either — whether a capability
        exists is still information about that capability.

        The generation id is safe here for the reason the task id is: minted
        from a timestamp and randomness, derived from nothing about the session.
        """
        self.add(
            {
                "action_id": uuid.uuid4().hex,
                "action": operation,
                "status": "succeeded" if result in ("ok", "requested") else "failed",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "params": {"project_id": project_id, "unit": unit},
                "result": {
                    "outcome": result,
                    "generation": generation,
                    "state": state,
                },
                "error": None if result in ("ok", "requested") else {"code": result},
                "stub": False,
            }
        )

    def record_task_event(
        self,
        operation: str,
        result: str,
        task_id: Optional[str] = None,
        adapter_id: Optional[str] = None,
        project_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Audit one Task Core operation — created, started, refused, finished.

        The narrowest audit in this file, and the one where the reasoning is
        least optional: a task's *prompt* is somebody thinking out loud, its
        follow-ups are a conversation, and its result is the answer. An action
        log carrying any of the three would become a transcript — kept on disk,
        shown in a list beside the volume changes, and never asked for.

        So this records six things, and every one is either an id Cofferdam
        minted or a word from a closed vocabulary: which operation, how it
        turned out, which task, which adapter, which project, and the
        correlation id. There is **no parameter for content**, which is what
        makes "the audit cannot carry a prompt" a property of the signature
        rather than a habit every caller has to keep.

        The task id is safe here for the same reason the correlation id is: it
        is minted from a timestamp and randomness, and derived from nothing
        about what the task says.
        """
        self.add(
            {
                "action_id": uuid.uuid4().hex,
                "action": operation,
                "status": "succeeded" if result in ("ok", "requested") else "failed",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "params": {"adapter_id": adapter_id, "project_id": project_id},
                "result": {
                    "outcome": result,
                    "task_id": task_id,
                    "correlation_id": correlation_id,
                },
                "error": None if result in ("ok", "requested") else {"code": result},
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
