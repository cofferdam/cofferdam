"""Remembering the volume to restore, because Spotify has no mute.

The current Spotify documentation publishes no mute operation anywhere in the
player API; volume is the only mechanism. So "mute" here is **setting the
volume to zero**, and the product says exactly that: the published flag is
``muted_by_cofferdam``, never ``muted``, so no client can render it as a Spotify
feature.

That makes unmuting a question Spotify cannot answer: back to *what*? The level
before muting is knowledge only Cofferdam has, so it is written down here —
deliberately **not** in the OAuth secret file, which holds a credential and
should contain nothing that changes during ordinary use.

    $COFFERDAM_HOME/state/spotify_mute.json

When the answer is unknown — a fresh install, a cleared state directory, a
device muted from the Spotify app itself — unmuting **refuses and asks the
user to pick a level**. Restoring to 50% "because that is reasonable" would be
Cofferdam choosing how loud someone's speakers get, which is precisely the kind
of small invention this codebase does not make.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

STATE_FILENAME = "spotify_mute.json"

# A record is per-device: muting a phone says nothing about a kitchen speaker.
MAX_RECORDS = 16


class MuteStateStore:
    """Bounded, per-device memory of the level to restore."""

    def __init__(self, config) -> None:
        self._config = config
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return Path(self._config.state_dir) / STATE_FILENAME

    def _read(self) -> Dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            document = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(document, dict):
            return {}
        records = document.get("devices")
        return records if isinstance(records, dict) else {}

    def _write(self, records: Dict[str, Any]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "devices": records}, ensure_ascii=False, indent=2, sort_keys=True
        )
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".spotify-mute-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, path)
        except OSError:
            # Losing this memory costs an "unmute to what?" prompt, never an
            # action. It must not fail the mute that is otherwise working.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    def remember(self, device_resource_id: str, volume_percent: int) -> None:
        """Record the level a device was at before being muted.

        A zero is never recorded: it is not something to restore *to*, and
        storing it would turn "unmute" into "set to silent", which reads as a
        broken button.
        """
        if not isinstance(volume_percent, int) or volume_percent <= 0 or volume_percent > 100:
            return
        with self._lock:
            records = self._read()
            records[device_resource_id] = {"restore_volume_percent": volume_percent}
            if len(records) > MAX_RECORDS:
                # Bounded: drop the oldest-inserted extras rather than growing
                # a file with every speaker that ever appeared.
                for key in list(records)[: len(records) - MAX_RECORDS]:
                    records.pop(key, None)
            self._write(records)

    def restore_value(self, device_resource_id: str) -> Optional[int]:
        with self._lock:
            record = self._read().get(device_resource_id)
        if not isinstance(record, dict):
            return None
        value = record.get("restore_volume_percent")
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if 0 < value <= 100 else None

    def forget(self, device_resource_id: str) -> None:
        with self._lock:
            records = self._read()
            if records.pop(device_resource_id, None) is not None:
                self._write(records)

    def clear(self) -> None:
        """Drop every record — used when an account is disconnected."""
        with self._lock:
            try:
                self.path.unlink()
            except OSError:
                pass


__all__ = ["MAX_RECORDS", "MuteStateStore", "STATE_FILENAME"]
