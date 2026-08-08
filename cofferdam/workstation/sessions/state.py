"""Where a captured Remote Control link lives while its host is running.

One small JSON file per registered project, under Cofferdam's own state
directory, owner-only, written atomically. It holds the link and the identity of
the process generation that produced it — and nothing else that could grow into
a session record.

Why a file and not the database
--------------------------------

Because this state is **worthless after a reboot and must not survive one**. A
link points at a process that no longer exists; keeping it in the durable store
would mean a status screen could offer a dead capability URL, and every reader
would have to remember to check liveness. A file under ``state/`` that is
deleted on stop, on failure and on every new generation has the right lifetime
built in, and it is why this PR adds no migration.

Generation identity
-------------------

Each launch mints a generation id. Every read is checked against the generation
the caller believes is current, and a mismatch returns nothing rather than the
old value. This is the property that makes "restart the host and the old link
stops working" structural: a stale file from a previous launch cannot answer a
request about the current one, even if deletion failed.

Path safety
-----------

The filename is derived from a project id the registry already validated, so no
caller text becomes a path component. On top of that the directory and the file
are both checked for symlinks before use: state under ``~/cofferdam`` is
attacker-relevant if anything else on the machine can plant a link there, and a
capability URL written through a symlink is a capability URL written wherever
the link pointed.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..tasks.projects import valid_project_id
from .errors import SessionProjectUnknown, StateUnavailable

#: Subdirectory of ``config.state_dir``. Owned by this package.
STATE_SUBDIR = "remote-control"

#: Bounded: this file holds a link and four short strings.
MAX_STATE_BYTES = 8192


def new_generation() -> str:
    """A fresh launch identity.

    Time-ordered plus randomness, like the task ids: the prefix makes two
    generations comparable at a glance in a log, and the random tail is what
    makes them unforgeable by a caller.
    """
    return "%d-%s" % (int(time.time()), uuid.uuid4().hex[:16])


def _check_no_symlink(path: Path) -> None:
    """Refuse a path reached through a link, component by component.

    ``realpath`` alone would follow the link and report success. The same walk
    the project registry uses for roots, for the same reason.
    """
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StateUnavailable("the runtime state path cannot be read") from exc
        if stat.S_ISLNK(info.st_mode):
            raise StateUnavailable("the runtime state path is reached through a link")


class LinkStore:
    """Per-project Remote Control runtime state, on disk."""

    def __init__(self, config) -> None:
        self._config = config

    # -- paths ---------------------------------------------------------------

    @property
    def directory(self) -> Path:
        return Path(self._config.state_dir) / STATE_SUBDIR

    def path_for(self, project_id: str) -> Path:
        """``<state>/remote-control/<project-id>.json``, or refuse.

        The id is re-validated here rather than trusted from the caller, so this
        module is safe on its own terms: ``../../secrets/token`` is not a
        project id and never becomes a path component.
        """
        if not valid_project_id(project_id):
            raise SessionProjectUnknown()
        return self.directory / (project_id + ".json")

    def _ensure_directory(self) -> Path:
        directory = self.directory
        _check_no_symlink(directory)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(stat.S_IRWXU)
        except OSError as exc:
            raise StateUnavailable("the runtime state directory cannot be created") from exc
        return directory

    # -- writing -------------------------------------------------------------

    def write(
        self,
        project_id: str,
        *,
        generation: str,
        link: Optional[str] = None,
        auth_required: bool = False,
        error: Optional[str] = None,
        discovered_at: Optional[str] = None,
        observed_at: Optional[str] = None,
    ) -> None:
        """Replace this project's state atomically.

        ``mkstemp`` in the destination directory, owner-only before any content
        is written, ``fsync``, then ``os.replace`` — the same sequence the action
        store uses. Same-directory temp file so the rename is atomic rather than
        a cross-device copy, and the mode is set on the descriptor before the
        link is written so the value is never briefly world-readable.
        """
        path = self.path_for(project_id)
        directory = self._ensure_directory()
        _check_no_symlink(path)

        document = {
            "project_id": project_id,
            "generation": generation,
            "link": link,
            "auth_required": bool(auth_required),
            "error": error,
            "discovered_at": discovered_at,
            "observed_at": observed_at,
        }
        payload = json.dumps(document, ensure_ascii=False)
        if len(payload.encode("utf-8")) > MAX_STATE_BYTES:
            raise StateUnavailable("the runtime state document is too large")

        handle, temporary = tempfile.mkstemp(
            dir=str(directory), prefix="." + project_id + "-", suffix=".tmp"
        )
        try:
            os.fchmod(handle, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise StateUnavailable("the runtime state could not be written") from exc

    # -- reading -------------------------------------------------------------

    def read(self, project_id: str) -> Optional[Dict[str, Any]]:
        """This project's state, or ``None`` if there is none to read.

        A missing file is not an error — a host that has never started has no
        state — and neither is a malformed one: it is treated as absent and
        left for the next write to replace, because refusing to answer "is there
        a link" because the file is corrupt helps nobody.
        """
        path = self.path_for(project_id)
        try:
            _check_no_symlink(path)
            raw = path.read_text(encoding="utf-8")
        except StateUnavailable:
            raise
        except FileNotFoundError:
            return None
        except OSError:
            return None
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            return None
        try:
            document = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(document, dict):
            return None
        # The file is named after the project, but a file that disagrees with
        # its own name is not one to trust for a capability URL.
        if document.get("project_id") != project_id:
            return None
        return document

    def read_link(self, project_id: str, *, generation: str) -> Optional[Dict[str, Any]]:
        """The link **only if** it belongs to that generation.

        The cross-project and stale-generation guard in one place. A caller
        asking about generation *g* never receives a link minted by anything
        else, so a restart invalidates the old URL even if the file survived.
        """
        document = self.read(project_id)
        if document is None:
            return None
        if document.get("generation") != generation:
            return None
        if not document.get("link"):
            return None
        return document

    # -- clearing ------------------------------------------------------------

    def clear(self, project_id: str) -> None:
        """Forget this project's link. Safe to call when there is nothing.

        Called on stop, on failure, and before every new generation — the three
        moments after which the stored URL is no longer a live capability.
        """
        try:
            self.path_for(project_id).unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StateUnavailable("the runtime state could not be cleared") from exc


__all__ = ["MAX_STATE_BYTES", "STATE_SUBDIR", "LinkStore", "new_generation"]
