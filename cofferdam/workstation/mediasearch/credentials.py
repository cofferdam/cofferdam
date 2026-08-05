"""Provider credentials: local file, owner-only, never leaving the host.

Where they live
---------------

``$COFFERDAM_HOME/secrets/media_providers.json`` — the same directory as the
device token, created 0700 with the file at 0600, already excluded from Git by
the existing ``secrets/`` rules. No new mechanism was invented: the repository
already had a reviewed answer to "where does a local secret go", and inventing a
second one is how a project ends up with a secret in the place nobody audits.

Shape::

    {
      "spotify": {"client_id": "...", "client_secret": "..."},
      "youtube": {"api_key": "..."}
    }

Why there is no setup form in the PWA
-------------------------------------

The obvious convenience — type your API key into the phone — would mean the
secret travels through a request body, sits in a text input, and lands in a file
the web tier can write. The repository has no reviewed secure secret-entry
mechanism over the network, and M2B3A.1 is not the milestone to invent one. So
credentials are configured by editing a local file, and the PWA is told only a
*status word*.

What may be observed
--------------------

:meth:`CredentialStore.status` returns exactly one of
:data:`PROVIDER_CREDENTIAL_STATUSES` and nothing else. There is deliberately no
API — not even an internal one — that returns a credential value, a prefix, a
suffix, a length, or a hash. ``CredentialStore`` keeps the values private and
hands them only to the adapter that needs them, at the moment of the call.

The ``__repr__`` overrides below are not decoration. A dataclass repr is exactly
how a secret reaches a log: an exception formatter, a debugger, or a stray
f-string prints the object, and the default repr would print the value with it.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

SECRET_FILENAME = "media_providers.json"

# Bounded: this file holds a handful of short keys. Anything larger is not a
# credential file and is refused before it is parsed.
MAX_SECRET_FILE_BYTES = 16 * 1024

# A credential that is implausibly long is a paste accident or a smuggled blob.
MAX_CREDENTIAL_LENGTH = 512

# The only words that may be said about a credential, anywhere.
STATUS_CONFIGURED = "configured"
STATUS_MISSING = "missing"
STATUS_INVALID = "invalid"
STATUS_REJECTED = "provider_rejected"
STATUS_UNAVAILABLE = "temporarily_unavailable"

PROVIDER_CREDENTIAL_STATUSES = (
    STATUS_CONFIGURED,
    STATUS_MISSING,
    STATUS_INVALID,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
)


@dataclass(frozen=True)
class SpotifyCredentials:
    """Client-credentials pair for the Spotify Web API."""

    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        return "SpotifyCredentials(client_id=<redacted>, client_secret=<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True)
class YouTubeCredentials:
    """API key for the YouTube Data API."""

    api_key: str

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        return "YouTubeCredentials(api_key=<redacted>)"

    __str__ = __repr__


def _plausible(value: object) -> bool:
    """A non-empty, bounded, control-free string.

    Deliberately *not* a format check against a provider's key shape: guessing
    what a valid Spotify client id looks like would reject a legitimate one the
    day the format changes. This only rules out what cannot be a credential —
    empty, enormous, or carrying characters that would corrupt a header.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_CREDENTIAL_LENGTH:
        return False
    # A control character here would end up in an HTTP header. Refuse rather
    # than strip: a credential that needs repairing is not a credential.
    return all(0x20 <= ord(character) < 0x7F for character in candidate)


class CredentialStore:
    """Reads provider credentials, and says as little as possible about them.

    Not cached: the file is tiny, and re-reading means fixing a credential takes
    effect without restarting the service — the same reasoning the registry
    loader records. It also means a *removed* credential stops working at once,
    rather than lingering in a process that started before the removal.
    """

    def __init__(self, config) -> None:
        self._config = config

    @property
    def path(self) -> Path:
        return Path(self._config.secrets_dir) / SECRET_FILENAME

    # -- reading -------------------------------------------------------------

    def _document(self) -> Optional[Dict[str, object]]:
        """The parsed file, ``None`` when absent, or raise for unusable content.

        Raising ``ValueError`` carries no file content and no path — only the
        fact that it could not be read. The caller turns that into
        :data:`STATUS_INVALID`.
        """
        path = self.path
        try:
            if path.stat().st_size > MAX_SECRET_FILE_BYTES:
                raise ValueError("credential file is too large")
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except NotADirectoryError:
            return None
        except OSError:
            raise ValueError("credential file could not be read") from None
        except UnicodeDecodeError:
            raise ValueError("credential file is not valid UTF-8") from None

        try:
            parsed = json.loads(raw)
        except ValueError:
            # The decoder's message can quote the offending line. Replaced with
            # a constant so a malformed file cannot echo its own contents.
            raise ValueError("credential file is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise ValueError("credential file must be a JSON object")
        return parsed

    def _section(self, provider_id: str) -> Optional[Dict[str, object]]:
        document = self._document()
        if document is None:
            return None
        section = document.get(provider_id)
        if section is None:
            return None
        if not isinstance(section, dict):
            raise ValueError("provider section must be a JSON object")
        return section

    # -- the only thing anyone outside may learn -----------------------------

    def status(self, provider_id: str) -> str:
        """One word from :data:`PROVIDER_CREDENTIAL_STATUSES`. Never a value."""
        try:
            section = self._section(provider_id)
        except ValueError:
            return STATUS_INVALID
        if section is None:
            return STATUS_MISSING
        try:
            self._build(provider_id, section)
        except ValueError:
            return STATUS_INVALID
        return STATUS_CONFIGURED

    def configured(self, provider_id: str) -> bool:
        return self.status(provider_id) == STATUS_CONFIGURED

    # -- handing values to an adapter, and nowhere else ----------------------

    @staticmethod
    def _build(provider_id: str, section: Dict[str, object]):
        if provider_id == "spotify":
            client_id = section.get("client_id")
            client_secret = section.get("client_secret")
            if not _plausible(client_id) or not _plausible(client_secret):
                raise ValueError("spotify credentials are incomplete")
            return SpotifyCredentials(
                client_id=str(client_id).strip(), client_secret=str(client_secret).strip()
            )
        if provider_id == "youtube":
            api_key = section.get("api_key")
            if not _plausible(api_key):
                raise ValueError("youtube credentials are incomplete")
            return YouTubeCredentials(api_key=str(api_key).strip())
        raise ValueError("no credential shape is defined for this provider")

    def load(self, provider_id: str):
        """Credentials for one provider, or ``None`` when unusable.

        Returns ``None`` rather than raising for both "missing" and "invalid":
        the caller's next move is identical (report unconfigured), and the
        difference is already available through :meth:`status` without a value
        ever being involved.
        """
        try:
            section = self._section(provider_id)
            if section is None:
                return None
            return self._build(provider_id, section)
        except ValueError:
            return None

    # -- diagnostics ---------------------------------------------------------

    def describe(self, provider_ids) -> Dict[str, str]:
        """``{provider_id: status_word}``. The whole diagnostic surface."""
        return {provider_id: self.status(provider_id) for provider_id in provider_ids}

    def permissions_note(self) -> Optional[str]:
        """A warning when the credential file is readable by others.

        Says that the file is too permissive; never says what is in it, and
        never returns the path — the setup documentation already names it, and
        an API response is not the place to publish a filesystem layout.
        """
        try:
            mode = self.path.stat().st_mode
        except OSError:
            return None
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            return (
                "the media provider credential file is readable by other users on this host; "
                "tighten it to owner-only (chmod 600)"
            )
        return None

    def restrict(self) -> None:
        """Best-effort owner-only permissions, matching the token's handling."""
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - platform dependent
            pass


def redact_environment_note() -> Optional[str]:
    """Warn if credentials were put in the environment instead of the file.

    Environment variables leak in ways a 0600 file does not — ``/proc/<pid>/environ``,
    crash handlers, and anything that dumps ``os.environ``. This detects only the
    *presence* of such a variable by name, and never reads its value.
    """
    leaked = sorted(
        name
        for name in os.environ
        if name.startswith(("SPOTIFY_", "YOUTUBE_"))
        and any(part in name for part in ("SECRET", "KEY", "TOKEN", "PASSWORD"))
    )
    if not leaked:
        return None
    return (
        "provider credentials appear to be set in this service's environment ("
        + ", ".join(leaked)
        + "); Cofferdam does not read them from there — use the credential file instead"
    )


__all__ = [
    "MAX_CREDENTIAL_LENGTH",
    "MAX_SECRET_FILE_BYTES",
    "PROVIDER_CREDENTIAL_STATUSES",
    "SECRET_FILENAME",
    "STATUS_CONFIGURED",
    "STATUS_INVALID",
    "STATUS_MISSING",
    "STATUS_REJECTED",
    "STATUS_UNAVAILABLE",
    "CredentialStore",
    "SpotifyCredentials",
    "YouTubeCredentials",
    "redact_environment_note",
]
