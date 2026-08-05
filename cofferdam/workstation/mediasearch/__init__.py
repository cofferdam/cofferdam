"""Official-provider catalogue search, and the bounded sessions that hold it.

What this package is
--------------------

M2B3A gave the phone a *launch* surface: open Spotify, open a service's own
search page. It could not answer "which of these is the one I meant?", because
opening a search page hands that question back to the user on a screen they are
not looking at.

This package answers it, through **official provider interfaces only** — the
Spotify Web API and the YouTube Data API. Nothing here scrapes a website, reads
a browser profile, or automates a page.

The shape of the boundary
-------------------------

Three rules decide almost every design choice below.

**The client never names a destination.** It sends a provider id from the
allowlist and a bounded phrase; it gets back opaque ``result_id`` values. When
it asks to open one, it names the search session and the result — never a URL,
never a ``spotify:`` URI, never a video id. The server re-resolves the result
from its own verified session and builds the launch target itself. A client that
could post a URL would have turned this into an open-redirect with a browser
attached.

**Credentials never leave the host.** They are read from an owner-only file
under ``$COFFERDAM_HOME/secrets/``, exactly where the device token already
lives. They are never returned by an endpoint, never logged, never placed in an
argv, never stored in a registry, and never embedded in an error. Diagnostics
report a *status word* and nothing else.

**Absence is a truthful state, not a broken one.** A machine with no provider
credentials is a normal machine: structured search reports itself unconfigured
and the M2B3A open/search-page actions keep working untouched. Every provider
failure below degrades to that same honest place.

Stdlib only
-----------

Every module here uses ``http.client`` and ``ssl`` rather than a third-party
HTTP library. That is not asceticism: it keeps the dependency surface of the
one component that talks to the public internet at zero, and it gives direct
control over the things that actually matter here — **redirects are never
followed**, response bodies are read to a hard byte cap, and certificate
verification is the default rather than a flag someone can turn off.
"""

from __future__ import annotations

from .credentials import (
    PROVIDER_CREDENTIAL_STATUSES,
    STATUS_CONFIGURED,
    STATUS_INVALID,
    STATUS_MISSING,
    CredentialStore,
)
from .errors import (
    ProviderRateLimited,
    ProviderRejected,
    ProviderSearchError,
    ProviderTemporarilyUnavailable,
    ProviderUnconfigured,
)
from .results import (
    MEDIA_RESULT_MODEL_VERSION,
    RESULT_TYPES,
    MediaResult,
    MediaSearchOutcome,
)
from .sessions import (
    SEARCH_SESSION_TTL_SECONDS,
    SearchSession,
    SearchSessionStore,
)

__all__ = [
    "MEDIA_RESULT_MODEL_VERSION",
    "PROVIDER_CREDENTIAL_STATUSES",
    "RESULT_TYPES",
    "SEARCH_SESSION_TTL_SECONDS",
    "STATUS_CONFIGURED",
    "STATUS_INVALID",
    "STATUS_MISSING",
    "CredentialStore",
    "MediaResult",
    "MediaSearchOutcome",
    "ProviderRateLimited",
    "ProviderRejected",
    "ProviderSearchError",
    "ProviderTemporarilyUnavailable",
    "ProviderUnconfigured",
    "SearchSession",
    "SearchSessionStore",
]
