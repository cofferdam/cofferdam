"""The one place a project id becomes a systemd unit name.

A templated unit turns a string into part of a unit name, and a unit name is a
namespace `systemctl` will happily act on. So the interesting question is not
"what does the name look like" but "what is the set of strings that can reach
here", and the answer is: exactly the ids the host's own project configuration
already accepted.

Why nothing is escaped
----------------------

The instinct is to add ``systemd-escape``. This module deliberately does not,
because escaping is what you do when the input grammar is wide. Here it is not:
:func:`..tasks.projects.valid_project_id` admits only lowercase letters, digits,
dash and underscore, bounded to 64 characters and non-empty. Every dangerous
character is already outside that set — ``/``, ``.``, ``..``, ``@``, whitespace,
newlines, shell metacharacters, and ``%``, which is systemd's own specifier
introducer.

Escaping a string that cannot contain anything needing escape adds a
transformation nobody can predict from the id, which is worse: the unit name in
`systemctl` output would stop matching the project id in the configuration file,
and every debugging session would start with a decoding step. Validation is
re-run here rather than assumed, so this module is safe on its own terms even if
somebody later calls it with a value that did not come from the registry.

The dash caveat, which is real
------------------------------

systemd treats ``-`` in an instance name as an encoded ``/`` when a unit expands
``%I`` or ``%f``. A project id like ``my-project`` would unescape to
``my/project``. That is why the shipped template uses ``%i`` — the *raw*
instance — and never ``%I`` or ``%f``, and why a test asserts it.
"""

from __future__ import annotations

from typing import Optional

from ..tasks.projects import valid_project_id
from .errors import SessionProjectUnknown

#: The fixed user-service namespace for native Remote Control hosts. One
#: template, owned by Cofferdam, with no second family and no way to select one.
UNIT_TEMPLATE = "cofferdam-rc@"
UNIT_SUFFIX = ".service"

#: The template file as it is shipped in ``deploy/``.
TEMPLATE_FILENAME = UNIT_TEMPLATE + UNIT_SUFFIX

#: systemd's own limit is 255 bytes for a unit name. With a 64-character id the
#: longest name this module can produce is 85, so this is a guard against a
#: future prefix change rather than against any id the registry would accept.
MAX_UNIT_NAME_BYTES = 255


def unit_name(project_id: str) -> str:
    """``cofferdam-rc@<project_id>.service``, or refuse.

    Deterministic: the same id always produces the same name, and the id is
    visible verbatim inside it. Raises :class:`~.errors.SessionProjectUnknown`
    for anything the project-id grammar does not accept — including the empty
    string, a path, a traversal, whitespace and shell metacharacters — because a
    caller holding an id that cannot name a project is indistinguishable from a
    caller naming a project that does not exist.
    """
    if not valid_project_id(project_id):
        raise SessionProjectUnknown()
    name = UNIT_TEMPLATE + project_id + UNIT_SUFFIX
    if len(name.encode("utf-8")) > MAX_UNIT_NAME_BYTES:
        raise SessionProjectUnknown()
    return name


def project_id_from_unit(unit: str) -> Optional[str]:
    """The inverse, for reading systemd output back. ``None`` if it is not ours.

    Used only to confirm that an answer is about the unit that was asked about.
    A value that does not round-trip through :func:`unit_name` is rejected rather
    than repaired.
    """
    if not isinstance(unit, str):
        return None
    if not unit.startswith(UNIT_TEMPLATE) or not unit.endswith(UNIT_SUFFIX):
        return None
    candidate = unit[len(UNIT_TEMPLATE) : -len(UNIT_SUFFIX)]
    if not valid_project_id(candidate):
        return None
    return candidate


__all__ = [
    "MAX_UNIT_NAME_BYTES",
    "TEMPLATE_FILENAME",
    "UNIT_SUFFIX",
    "UNIT_TEMPLATE",
    "project_id_from_unit",
    "unit_name",
]
