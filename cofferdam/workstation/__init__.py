"""Cofferdam workstation runtime (M1).

The personal AI workstation service: a small FastAPI application that exposes
authenticated host status, a **typed** action surface (screenshot, open
application, open URL), and a live WebSocket event channel to the phone/tablet
PWA.

Design rules that this package must keep (see ``AGENTS.md`` / ``DESIGN.md``):

* Callers submit **typed actions**, never commands. No endpoint, schema field,
  or adapter accepts a shell string, and no adapter uses ``shell=True``.
* Platform-specific behaviour lives only under :mod:`cofferdam.workstation.adapters`.
* The Trust Core package modules are not imported from here and are unaffected.
"""

from __future__ import annotations

WORKSTATION_API_VERSION = "1"

__all__ = ["WORKSTATION_API_VERSION"]
