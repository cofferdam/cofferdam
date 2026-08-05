"""Which applications are actually running — as instances, not as processes.

The problem this module exists to solve
---------------------------------------
A modern desktop application is not a process. Opera on this host is **19
processes**: one browser process, a zygote, a GPU process, a network service,
and a renderer per site. Firefox is the same shape. Naively listing processes
whose name matches ``opera`` would report nineteen running Operas, and grouping
them by name substring would sooner or later sweep up something unrelated that
merely contains those letters.

The grouping evidence used here is **cgroup membership**, because it is the
answer the system already computed. When anything launches a desktop
application on a systemd session — GNOME Shell, a ``.desktop`` activation, snap,
or Cofferdam's own ``systemd-run --user`` — the application lands in its own
transient unit under ``app.slice``. Every process it forks inherits that cgroup.
Observed on this host: all 19 Opera processes sit in one
``snap.opera.opera-<uuid>.scope``. That scope *is* the instance boundary, drawn
by the system rather than guessed by us.

The rule, exactly
-----------------
A running application instance is a ``.scope`` unit under ``app.slice``, or a
``cofferdam-app-*.service`` we started ourselves. Nothing else is promoted to an
instance.

``.scope`` is the discriminating part. ``app.slice`` also contains plain
``.service`` units — ``dconf.service``, ``ssh-agent.service``,
``gnome-keyring-daemon.service``, and this service itself — which are session
infrastructure, not applications the user opened. A scope, by contrast, is what
systemd creates for a process it did not fork itself: a launched application.

Units that describe the same application
----------------------------------------
systemd's naming convention is
``app[-<launcher>]-<ApplicationID>-<discriminator>.scope``. A GNOME launch
produces **two** scopes for one application — observed here as
``app-com.anthropic.Claude-7358.scope`` and
``app-gnome-com.anthropic.Claude-7358.scope``. Both encode the same application
ID and the same launcher PID, so they are parsed and merged on that pair. This
is grammar, not substring matching: two genuinely different applications never
agree on both halves.

What is *not* an instance, and why that is the honest answer
------------------------------------------------------------
* **A browser tab.** Tabs are not processes and a renderer is not a tab.
  Discovering tabs requires a browser extension reporting real tab IDs; that is
  a later milestone, and inferring them from renderer PIDs is explicitly ruled
  out (D-2026-08-04-6).
* **A D-Bus-activated application.** On this host the terminal (``ptyxis``)
  lives in ``session.slice/dbus.service``, shared with every other
  D-Bus-activated program. A shared unit is not an instance boundary, so those
  processes stay unmapped rather than being grouped together into a fictional
  application. This is a real gap and is published as a limitation.
* **Anything ambiguous.** A group whose executable cannot be matched to an
  application definition is reported with ``application_id: null`` and a
  truthful name taken from its own executable. An unmapped instance is a fact;
  a wrong mapping is a bug that looks like a feature.

Definition versus instance
--------------------------
``applications.json`` and the adapter's allowlist say what *can* be launched.
They are consulted here only to attach an ``application_id`` to a group that was
already discovered from live processes. A definition never creates an instance:
Firefox being installed and launchable produces no entry in this collection
until a Firefox process exists.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .identity import fingerprint
from .models import (
    KIND_APPLICATIONS,
    STABILITY_BOOT,
    Evidence,
    ResourceCollection,
    collected,
    unavailable,
)
from .processes import ProcessFacts, process_resource_id, started_at

BACKEND_CGROUP = "systemd-cgroup"

# The slice systemd places launched applications in.
APP_SLICE = "app.slice"

# Cofferdam's own transient units — see ``linux_session.UNIT_PREFIX``. Matched
# as a ``.service`` because ``systemd-run --user --unit=`` creates one.
COFFERDAM_UNIT_PREFIX = "cofferdam-app-"

# ``app-<launcher>-<ApplicationID>-<discriminator>.scope`` and the launcher-less
# form. The application ID is the greedy middle; the discriminator is the final
# ``-``-separated run of digits or a UUID-ish token that systemd appends.
_APP_SCOPE = re.compile(r"^app-(?:(?P<launcher>[^-]+)-)?(?P<app>.+)-(?P<disc>[^-]+)\.scope$")

# ``snap.<package>.<app>-<uuid>.scope``
_SNAP_SCOPE = re.compile(r"^snap\.(?P<package>[^.]+)\.(?P<app>[^-]+)-(?P<disc>.+)\.scope$")

# How the launch of an instance can be attributed — on evidence, never on the
# absence of it.
#
# The earlier model was a boolean, ``launched_by_cofferdam``, and live
# validation caught it being wrong: snapd re-parents a snap launch out of our
# ``cofferdam-app-<hex>.service`` into its own ``snap.<pkg>.<app>-<uuid>.scope``
# before the first scan, so a Firefox that Cofferdam *had* just started was
# reported ``false``. "We cannot prove we started this" and "something else
# started this" are different claims, and only the first was true.
#
# Hence three states, with ``unknown`` a real answer rather than a placeholder.
LAUNCH_SOURCE_COFFERDAM = "confirmed_cofferdam"
LAUNCH_SOURCE_EXTERNAL = "confirmed_external"
LAUNCH_SOURCE_UNKNOWN = "unknown"

LAUNCH_SOURCES = (LAUNCH_SOURCE_COFFERDAM, LAUNCH_SOURCE_EXTERNAL, LAUNCH_SOURCE_UNKNOWN)

# Desktop launchers that name themselves in the scope they create:
# ``app-gnome-<ApplicationID>-<pid>.scope``. Cofferdam never produces that shape
# — ``systemd-run --user --unit=`` makes a ``.service`` — so the launcher
# segment is positive evidence that something else performed the launch, not
# merely evidence that we did not. Only shells actually observed to write this
# form are listed; an unrecognised launcher segment stays ``unknown``.
_SELF_NAMING_LAUNCHERS = frozenset({"gnome", "kde", "plasma", "xfce", "mate", "cinnamon"})

_LIMITATIONS = (
    "an application instance is a systemd app scope; a D-Bus-activated application shares "
    "dbus.service with every other one and cannot be separated into its own instance",
    "browser tabs are not application instances and are not inferred from renderer processes",
    "a process group whose executable matches no application definition is reported unmapped "
    "rather than guessed",
    "window counts are present only when window discovery is available on this host",
    "launch attribution is three-valued: snapd re-parents every snap launch into its own scope, "
    "so a snap Cofferdam started reports launch_source=unknown rather than claiming something "
    "else launched it",
)

# systemd escapes characters it cannot put in a unit name. Only the escapes that
# actually appear in application unit names are decoded, and anything else is
# left exactly as systemd wrote it rather than being partially unescaped.
_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})")


def _unescape_unit(name: str) -> str:
    return _ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), name)


class InstanceKey:
    """What makes two units the same application instance."""

    __slots__ = ("application", "discriminator", "unit_kind")

    def __init__(self, application: str, discriminator: str, unit_kind: str) -> None:
        self.application = application
        self.discriminator = discriminator
        self.unit_kind = unit_kind

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, InstanceKey)
            and self.application == other.application
            and self.discriminator == other.discriminator
        )

    def __hash__(self) -> int:
        return hash((self.application, self.discriminator))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"InstanceKey({self.application!r}, {self.discriminator!r})"


def instance_key(cgroup_path: Optional[str], unit: Optional[str]) -> Optional[InstanceKey]:
    """The instance a process belongs to, or ``None`` if it belongs to none.

    ``None`` is the common and correct answer for session infrastructure, for
    D-Bus-activated programs, and for anything systemd did not classify as a
    launched application.
    """
    if not unit or not cgroup_path:
        return None

    if unit.startswith(COFFERDAM_UNIT_PREFIX) and unit.endswith(".service"):
        # Started by us. The unit name is already unique per launch.
        return InstanceKey(unit[: -len(".service")], unit, "cofferdam")

    # Everything else must be a scope inside app.slice. A ``.service`` in
    # app.slice is session infrastructure; a scope outside it is not an
    # application launch.
    if not unit.endswith(".scope") or APP_SLICE not in cgroup_path.split("/"):
        return None

    snap = _SNAP_SCOPE.match(unit)
    if snap:
        return InstanceKey(
            "snap:" + snap.group("package") + "." + snap.group("app"),
            snap.group("disc"),
            "snap",
        )

    app = _APP_SCOPE.match(unit)
    if app:
        # ``app-gnome-com.anthropic.Claude-7358.scope`` and
        # ``app-com.anthropic.Claude-7358.scope`` differ only in the launcher
        # segment, which is not part of the key: same application, same launch.
        return InstanceKey(_unescape_unit(app.group("app")), app.group("disc"), "app-scope")

    # A scope in app.slice with an unrecognised name — ``ptyxis-spawn-<uuid>``,
    # for instance. It is still one launched thing, so the whole unit name is
    # the key; nothing is merged with it.
    return InstanceKey(_unescape_unit(unit[: -len(".scope")]), unit, "scope")


def launch_source(units: Sequence[str]) -> str:
    """Who started this instance, judged only from evidence the units carry.

    Returns one of :data:`LAUNCH_SOURCES`. The rules, in order:

    * our own transient unit is still there — ``confirmed_cofferdam``;
    * a snap scope — ``unknown``, always. Snapd re-parents *every* snap launch
      into ``snap.<pkg>.<app>-<uuid>.scope``, discarding whatever unit started
      it, so the scope is equally consistent with a Cofferdam launch and a user
      double-click. Nothing here can tell them apart, and inventing an answer is
      exactly the bug this replaced;
    * a scope whose launcher segment names a desktop shell — ``confirmed_external``;
    * anything else — ``unknown``.

    The absence of our unit is never on its own a reason to say something else
    launched it: that inference is what produced the false ``false``.
    """
    for unit in units:
        if unit.startswith(COFFERDAM_UNIT_PREFIX) and unit.endswith(".service"):
            return LAUNCH_SOURCE_COFFERDAM

    # Checked before the launcher rule: a snap scope is unattributable even when
    # some other unit sits beside it, because re-parenting is what destroyed the
    # evidence in the first place.
    if any(_SNAP_SCOPE.match(unit) for unit in units):
        return LAUNCH_SOURCE_UNKNOWN

    for unit in units:
        match = _APP_SCOPE.match(unit)
        if match and match.group("launcher") in _SELF_NAMING_LAUNCHERS:
            return LAUNCH_SOURCE_EXTERNAL

    return LAUNCH_SOURCE_UNKNOWN


def _root_of(members: Sequence[ProcessFacts]) -> ProcessFacts:
    """The member that leads the group.

    The root is the member whose parent is outside the group — the process the
    unit was created for. If ancestry cannot single one out (a racing scan can
    leave an orphan), the earliest-started member is used, which for a browser
    is the browser process rather than a renderer.
    """
    pids = {record.pid for record in members}
    roots = [record for record in members if record.ppid not in pids]
    candidates = roots or list(members)
    return min(candidates, key=lambda record: (record.start_ticks or 0, record.pid))


def _display_name(root: ProcessFacts, key: InstanceKey) -> str:
    """A name taken from the system, never invented.

    Preference order is executable basename, then ``comm``, then the unit's own
    application segment. Each is something the machine said about itself.
    """
    if root.executable_path:
        return os.path.basename(root.executable_path)
    if root.name:
        return root.name
    return key.application


def _definition_match(
    root: ProcessFacts,
    members: Sequence[ProcessFacts],
    definitions: Mapping[str, Tuple[str, ...]],
) -> Tuple[Optional[str], Optional[str]]:
    """Map a group to an application definition, or leave it unmapped.

    The only evidence used is the **exact basename of a real executable path**
    read from ``/proc/<pid>/exe``. Matching on ``comm`` would be matching on a
    15-character truncation a process can rename at will, and matching on a
    substring of anything is how unrelated processes get grouped.

    The **root's** executable decides. Consulting every member would let one
    bundled helper speak for the whole application: an Electron application that
    ships a binary called ``chromium`` would be reported as Chromium, which is
    both wrong and the kind of wrong that looks plausible. Members are consulted
    only when the root has no readable executable at all — a wrapper script, or
    a process we may not follow — and that weaker path is named in the returned
    method so a caller can tell the two apart.

    No match leaves the group unmapped, which is a complete answer: the instance
    is running and this build cannot say which definition it is.
    """
    if root.executable_path:
        matched = _lookup(os.path.basename(root.executable_path), definitions)
        return (matched, "executable-basename") if matched else (None, None)

    for record in members:
        if not record.executable_path:
            continue
        matched = _lookup(os.path.basename(record.executable_path), definitions)
        if matched:
            return matched, "member-executable-basename"
    return None, None


def _lookup(basename: str, definitions: Mapping[str, Tuple[str, ...]]) -> Optional[str]:
    for application_id, executables in definitions.items():
        if basename in executables:
            return application_id
    return None


class ApplicationInstanceDiscovery:
    """Resources owned: running application instances.

    Evidence: systemd cgroup membership from ``/proc/<pid>/cgroup``, process
    ancestry, and exact executable basenames.

    Limitations: D-Bus-activated applications share one unit and are not
    separable; browser tabs are out of scope; an unrecognised executable leaves
    the instance unmapped rather than guessed.
    """

    kind = KIND_APPLICATIONS

    def __init__(self, definitions: Optional[Mapping[str, Tuple[str, ...]]] = None) -> None:
        self._definitions = dict(definitions or {})

    def group(self, facts: Sequence[ProcessFacts]) -> Dict[InstanceKey, List[ProcessFacts]]:
        groups: Dict[InstanceKey, List[ProcessFacts]] = {}
        for record in facts:
            key = instance_key(record.cgroup_path, record.unit)
            if key is None:
                continue
            groups.setdefault(key, []).append(record)
        return groups

    def collect(
        self,
        host_id: str,
        boot,
        facts: Sequence[ProcessFacts],
        scan_warnings: Sequence[str] = (),
    ) -> Tuple[ResourceCollection, Dict[int, str]]:
        """Build the collection, and the PID → instance map processes link back with."""
        evidence = Evidence(
            backend=BACKEND_CGROUP,
            sources=("/proc/<pid>/cgroup", "/proc/<pid>/exe", "/proc/<pid>/stat"),
            limitations=_LIMITATIONS,
        )

        if not getattr(boot, "available", False):
            return (
                unavailable(
                    self.kind,
                    "this host does not publish a boot identity, so no stable instance identity "
                    "can be formed",
                    evidence,
                ),
                {},
            )

        items: List[Dict[str, Any]] = []
        instance_by_pid: Dict[int, str] = {}

        for key, members in sorted(
            self.group(facts).items(), key=lambda entry: entry[0].application
        ):
            root = _root_of(members)
            if root.start_ticks is None:
                # No start time means no identity; see ProcessDiscovery.
                continue

            resource_id = "appinstance-" + fingerprint(
                "cofferdam.appinstance", host_id, boot.boot_id, key.application, key.discriminator
            )
            application_id, match_method = _definition_match(root, members, self._definitions)

            for record in members:
                instance_by_pid[record.pid] = resource_id

            units = sorted({record.unit for record in members if record.unit})
            child_pids = sorted(record.pid for record in members if record.pid != root.pid)

            items.append(
                {
                    "resource_id": resource_id,
                    "kind": "application_instance",
                    "identity": {
                        "source": "systemd-unit+pid+start-time",
                        "stability": STABILITY_BOOT,
                        "boot_id": boot.boot_id,
                    },
                    # Null is a real answer: the instance is running, and this
                    # build cannot say which definition it is.
                    "application_id": application_id,
                    "match_method": match_method,
                    "display_name": _display_name(root, key),
                    "primary_pid": root.pid,
                    "primary_process_id": process_resource_id(
                        host_id, boot.boot_id, root.pid, root.start_ticks
                    ),
                    "start_ticks": root.start_ticks,
                    "started_at": started_at(
                        root.start_ticks, getattr(boot, "boot_epoch_seconds", None)
                    ),
                    "child_pids": child_pids,
                    "process_count": len(members),
                    "units": units,
                    "unit_kind": key.unit_kind,
                    # Three-valued on purpose. See ``launch_source``: a snap
                    # that Cofferdam started is ``unknown``, never ``external``.
                    "launch_source": launch_source(units),
                    "executable_path": root.executable_path,
                    "executable": (
                        os.path.basename(root.executable_path) if root.executable_path else None
                    ),
                    "state": "running",
                    # Absent rather than zero: "no windows" and "windows cannot
                    # be counted on this host" are different, and only window
                    # discovery may answer.
                    "window_count": None,
                    "backend": BACKEND_CGROUP,
                    "overlay": None,
                }
            )

        return collected(self.kind, items, evidence, tuple(scan_warnings)), instance_by_pid


__all__ = [
    "APP_SLICE",
    "ApplicationInstanceDiscovery",
    "BACKEND_CGROUP",
    "COFFERDAM_UNIT_PREFIX",
    "InstanceKey",
    "LAUNCH_SOURCES",
    "LAUNCH_SOURCE_COFFERDAM",
    "LAUNCH_SOURCE_EXTERNAL",
    "LAUNCH_SOURCE_UNKNOWN",
    "instance_key",
    "launch_source",
]
