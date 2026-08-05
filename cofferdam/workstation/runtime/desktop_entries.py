"""Desktop-entry metadata, used as *evidence* about what a running group is.

Why this module exists
----------------------
The applications collection discovers every launched application scope on the
session. That is the right answer for an inventory and the wrong answer for the
front page of a control plane: on the validation host it puts
``evolution-alarm-notify``, ``gsd-disk-utility-notify`` and ``update-notifier``
beside Opera and Firefox, as if the user might want to click them.

Those three are not user-facing applications, and the system already says so —
in their ``.desktop`` entries. Every one of them carries ``NoDisplay=true`` and
lives in an XDG **autostart** directory. Neither fact is a guess, a name, or a
substring: they are the freedesktop-specified way an application declares "do
not show me in the menu" and "start me automatically at login".

So classification reads that declaration rather than inventing a judgement.

What this module will not do
----------------------------
* **No substring matching.** ``update-notifier`` is background because its entry
  says ``NoDisplay=true``, not because its name contains "notifier". A future
  application called ``notifier-pro`` with a visible entry classifies as
  user-facing, correctly.
* **No hardcoded host inventory.** Nothing here lists the applications observed
  on this machine. The rules are general; this host merely happens to exercise
  them.
* **No caller-supplied paths.** The directory list is code-owned and closed, and
  an application ID is accepted only if it matches
  :data:`_SAFE_APPLICATION_ID`. A systemd unit name is not user input, but it is
  still parsed text, and a desktop entry is a file read — so the two are kept
  from meeting on any path a request could influence.
* **No verdict without evidence.** An entry that cannot be found produces
  ``None``, and the caller reports the group as unclassified rather than
  assuming either way.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

# Menu directories: an entry here is a candidate for the application menu.
_MENU_DIRECTORIES: Tuple[str, ...] = (
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
)

# Autostart directories: an entry here is started automatically at login, which
# is what a background helper does and what a user-launched application does
# not. Presence is positive evidence on its own.
_AUTOSTART_DIRECTORIES: Tuple[str, ...] = ("/etc/xdg/autostart",)

_USER_MENU_DIRECTORIES: Tuple[str, ...] = (
    ".local/share/applications",
    ".local/share/flatpak/exports/share/applications",
)

_USER_AUTOSTART_DIRECTORIES: Tuple[str, ...] = (".config/autostart",)

# Application IDs come from systemd unit names. Reversed-DNS names, digits,
# dashes and underscores only — anything with a separator or a traversal
# sequence is rejected rather than sanitised, so no crafted unit name can
# escape the directory list above.
_SAFE_APPLICATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Only the keys that carry classification evidence are read; the rest of the
# entry, including anything user-written, is ignored.
_KEYS_OF_INTEREST = frozenset({"nodisplay", "hidden", "type", "categories", "terminal"})

_TRUE = frozenset({"true", "yes", "1"})


class DesktopEntry:
    """The classification-relevant part of one ``.desktop`` file."""

    __slots__ = ("application_id", "path", "no_display", "hidden", "entry_type",
                 "categories", "autostart")

    def __init__(
        self,
        application_id: str,
        path: str,
        no_display: bool,
        hidden: bool,
        entry_type: Optional[str],
        categories: Tuple[str, ...],
        autostart: bool,
    ) -> None:
        self.application_id = application_id
        self.path = path
        self.no_display = no_display
        self.hidden = hidden
        self.entry_type = entry_type
        self.categories = categories
        self.autostart = autostart

    @property
    def is_background(self) -> bool:
        """Declares itself out of the menu, or starts itself at login.

        Either is sufficient. ``NoDisplay=true`` is the application saying it is
        not something the user picks; an autostart entry is the session saying
        the user never picked it.
        """
        return self.no_display or self.hidden or self.autostart

    @property
    def is_visible_application(self) -> bool:
        """A normal menu entry: the shape of something a user opens."""
        return (
            self.entry_type == "Application"
            and not self.no_display
            and not self.hidden
            and not self.autostart
        )

    def evidence(self) -> str:
        """The specific reason, for display and for the audit trail."""
        if self.hidden:
            return "desktop-entry-hidden"
        if self.no_display:
            return "desktop-entry-nodisplay"
        if self.autostart:
            return "xdg-autostart-entry"
        if self.is_visible_application:
            return "desktop-entry-visible"
        return "desktop-entry-inconclusive"


def _parse(path: Path, application_id: str, autostart: bool) -> Optional[DesktopEntry]:
    """Read the ``[Desktop Entry]`` group only.

    Later groups are actions and per-locale overrides; neither changes whether
    the application is user-facing, and stopping at the first group keeps a
    malformed or hostile file from contributing anything.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    values: Dict[str, str] = {}
    in_entry = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if in_entry:
                break
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        # Locale-suffixed keys (``Name[tr]``) are irrelevant here and dropped.
        if key in _KEYS_OF_INTEREST:
            values.setdefault(key, value.strip())

    if not in_entry and not values:
        return None

    categories = tuple(
        part for part in values.get("categories", "").split(";") if part
    )
    return DesktopEntry(
        application_id=application_id,
        path=str(path),
        no_display=values.get("nodisplay", "").lower() in _TRUE,
        hidden=values.get("hidden", "").lower() in _TRUE,
        entry_type=values.get("type") or None,
        categories=categories,
        autostart=autostart,
    )


class DesktopEntryIndex:
    """Looks up a desktop entry by application ID, once per scan.

    Constructed per snapshot so a newly installed application is seen on the
    next refresh without restarting the service, and so one scan cannot see two
    different answers for the same ID.
    """

    def __init__(
        self,
        menu_directories: Sequence[str] = _MENU_DIRECTORIES,
        autostart_directories: Sequence[str] = _AUTOSTART_DIRECTORIES,
        home: Optional[str] = None,
    ) -> None:
        home_path = Path(home) if home else Path(os.path.expanduser("~"))
        self._menu = [Path(directory) for directory in menu_directories]
        self._menu += [home_path / relative for relative in _USER_MENU_DIRECTORIES]
        self._autostart = [Path(directory) for directory in autostart_directories]
        self._autostart += [home_path / relative for relative in _USER_AUTOSTART_DIRECTORIES]
        self._cache: Dict[str, Optional[DesktopEntry]] = {}

    def lookup(self, application_id: Optional[str]) -> Optional[DesktopEntry]:
        """The entry for ``application_id``, or ``None`` when there is none.

        ``None`` is a real answer and must not be read as "background" or as
        "user-facing": it means this group left no desktop-entry evidence.
        """
        if not application_id or not _SAFE_APPLICATION_ID.match(application_id):
            return None
        if application_id in self._cache:
            return self._cache[application_id]

        entry: Optional[DesktopEntry] = None
        filename = application_id + ".desktop"
        # Autostart is checked first: an application shipping both a menu entry
        # and an autostart entry is still autostarted, and that is the fact that
        # decides whether the user chose to open it.
        for directory in self._autostart:
            candidate = directory / filename
            if candidate.is_file():
                entry = _parse(candidate, application_id, autostart=True)
                break
        if entry is None:
            for directory in self._menu:
                candidate = directory / filename
                if candidate.is_file():
                    entry = _parse(candidate, application_id, autostart=False)
                    break

        self._cache[application_id] = entry
        return entry


__all__ = [
    "DesktopEntry",
    "DesktopEntryIndex",
]
