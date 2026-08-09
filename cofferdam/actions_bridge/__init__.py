"""The private Custom GPT Actions bridge (M2I.5).

A separate, narrow process in front of Cofferdam's task API. It is deliberately
none of the following, and each absence is load-bearing:

* not the Project Workstation PWA,
* not the main Cofferdam API,
* not a reverse proxy or a generic HTTP proxy,
* not a mirror of the private task routes,
* not a provider adapter, a shell runner, a filesystem API or a transcript API.

It publishes eight bounded operations under ``/v1`` and reaches Cofferdam
through ten fixed, allowlisted internal calls. Run it with::

    python -m cofferdam.actions_bridge

It binds to loopback unless told otherwise, and PR1 ships no public transport:
no tunnel, no DNS, no certificate, and no Custom GPT configured against it. See
``docs/ACTIONS_BRIDGE.md`` for the trust boundaries and
``docs/custom-gpt/`` for the schema and operator instructions.
"""

from __future__ import annotations

from .limits import BRIDGE_API_VERSION

__all__ = ["BRIDGE_API_VERSION"]
