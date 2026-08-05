"""Refusals at the Spotify playback boundary.

Every state below is one Cofferdam can *justify*. That constraint did real work
here, because the official error model changed: the current API-calls concept
page documents the regular error object (``status``, ``message``, optional
``reason``) and lists exactly one reason value — ``QUOTA_EXCEEDED``. The
historical "Player error object" reasons (``PREMIUM_REQUIRED``,
``NO_ACTIVE_DEVICE``, ``DEVICE_NOT_CONTROLLABLE``, ``VOLUME_CONTROL_DISALLOWED``)
are no longer documented.

So this module treats any ``reason`` string as a **hint**, never as a
requirement, and derives what it can from documented facts instead:

* "no active device" comes from the **devices list**, which is documented, not
  from guessing at a 404.
* "restricted" and "volume unsupported" come from the documented device fields
  ``is_restricted`` and ``supports_volume``.
* a 403 with no recognised hint becomes :data:`CODE_PROVIDER_REJECTED` and says
  what the two documented causes are — the account is not Premium, or the app is
  in development mode and this user is not on its allowlist — rather than
  asserting either one.
"""

from __future__ import annotations

from typing import Optional

# -- connection status vocabulary -------------------------------------------
#
# Bounded and closed. Nothing here is derived from a token, and no state
# carries provider text.

STATUS_DISCONNECTED = "disconnected"
STATUS_AUTHORIZATION_PENDING = "authorization_pending"
STATUS_CONNECTED = "connected"
STATUS_MISSING_SCOPES = "missing_required_scopes"
STATUS_REFRESH_FAILED = "refresh_failed"
STATUS_PROVIDER_REJECTED = "provider_rejected"
STATUS_TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
STATUS_PREMIUM_REQUIRED = "premium_required"

CONNECTION_STATUSES = (
    STATUS_DISCONNECTED,
    STATUS_AUTHORIZATION_PENDING,
    STATUS_CONNECTED,
    STATUS_MISSING_SCOPES,
    STATUS_REFRESH_FAILED,
    STATUS_PROVIDER_REJECTED,
    STATUS_TEMPORARILY_UNAVAILABLE,
    STATUS_PREMIUM_REQUIRED,
)

# -- action refusal codes ----------------------------------------------------

CODE_NOT_CONNECTED = "spotify_not_connected"
CODE_MISSING_SCOPES = "spotify_missing_scopes"
CODE_PREMIUM_REQUIRED = "spotify_premium_required"
CODE_NO_ACTIVE_DEVICE = "spotify_no_active_device"
CODE_DEVICE_UNKNOWN = "spotify_device_unknown"
CODE_DEVICE_RESTRICTED = "spotify_device_restricted"
CODE_VOLUME_UNSUPPORTED = "spotify_volume_unsupported"
CODE_INVALID_VOLUME = "spotify_volume_invalid"
CODE_UNMUTE_UNKNOWN = "spotify_unmute_restore_unknown"
CODE_RESULT_NOT_PLAYABLE = "spotify_result_not_playable"
CODE_RATE_LIMITED = "spotify_rate_limited"
CODE_PROVIDER_REJECTED = "spotify_provider_rejected"
CODE_PROVIDER_UNAVAILABLE = "spotify_provider_unavailable"
CODE_AUTHORIZATION_FAILED = "spotify_authorization_failed"
CODE_OBSERVATION_FAILED = "spotify_observation_failed"


class SpotifyPlayerError(Exception):
    """A refusal a person should see, with a stable code to branch on.

    Deliberately not an HTTP concern: the service layer maps codes to statuses,
    which keeps every module below it testable without a client — the same shape
    the audio and overlay write paths already use.

    ``retry_after_seconds`` is carried only for rate limiting, and only because
    the provider supplied it. Nothing in Cofferdam sleeps on it.
    """

    def __init__(
        self,
        code: str,
        message: str,
        detail: Optional[str] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


class NotConnected(SpotifyPlayerError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_NOT_CONNECTED,
            "no Spotify account is connected",
            detail or "authorize Spotify on the workstation first",
        )


class MissingScopes(SpotifyPlayerError):
    def __init__(self, missing) -> None:
        super().__init__(
            CODE_MISSING_SCOPES,
            "this Spotify authorization is missing permissions Cofferdam needs",
            "reconnect to grant: " + ", ".join(sorted(missing)),
        )


class PremiumRequired(SpotifyPlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_PREMIUM_REQUIRED,
            "Spotify playback control requires a Premium account",
            "every Spotify player endpoint is documented as Premium-only",
        )


class NoActiveDevice(SpotifyPlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_NO_ACTIVE_DEVICE,
            "Spotify has no active device to play on",
            "open Spotify on this computer, your phone, or a speaker, then try again — "
            "Spotify needs somewhere to send the audio",
        )


class DeviceUnknown(SpotifyPlayerError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_DEVICE_UNKNOWN,
            "that Spotify device is not available right now",
            detail or "the device list has changed since this page loaded — refresh and retry",
        )


class DeviceRestricted(SpotifyPlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_DEVICE_RESTRICTED,
            "Spotify does not allow remote control of that device",
            "the device reports itself as restricted, which Spotify documents as accepting no "
            "Web API commands at all",
        )


class VolumeUnsupported(SpotifyPlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_VOLUME_UNSUPPORTED,
            "that Spotify device does not support volume control",
            "the device reports supports_volume as false; use the device's own controls",
        )


class RateLimited(SpotifyPlayerError):
    def __init__(self, retry_after_seconds: Optional[int]) -> None:
        detail = "Spotify limits requests over a rolling 30-second window"
        if retry_after_seconds is not None:
            detail += f"; it asked for about {retry_after_seconds}s"
        super().__init__(
            CODE_RATE_LIMITED,
            "Spotify is rate limiting Cofferdam",
            detail,
            retry_after_seconds=retry_after_seconds,
        )


class ProviderRejected(SpotifyPlayerError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROVIDER_REJECTED,
            "Spotify refused that request",
            detail
            or "the two documented causes are an account without Premium, and an app in "
            "development mode whose allowlist does not include this Spotify user",
        )


class ProviderUnavailable(SpotifyPlayerError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROVIDER_UNAVAILABLE,
            "Spotify could not be reached",
            detail or "this is a network or provider problem, not a configuration one",
        )


__all__ = [
    "CODE_AUTHORIZATION_FAILED",
    "CODE_DEVICE_RESTRICTED",
    "CODE_DEVICE_UNKNOWN",
    "CODE_INVALID_VOLUME",
    "CODE_MISSING_SCOPES",
    "CODE_NOT_CONNECTED",
    "CODE_NO_ACTIVE_DEVICE",
    "CODE_OBSERVATION_FAILED",
    "CODE_PREMIUM_REQUIRED",
    "CODE_PROVIDER_REJECTED",
    "CODE_PROVIDER_UNAVAILABLE",
    "CODE_RATE_LIMITED",
    "CODE_RESULT_NOT_PLAYABLE",
    "CODE_UNMUTE_UNKNOWN",
    "CODE_VOLUME_UNSUPPORTED",
    "CONNECTION_STATUSES",
    "DeviceRestricted",
    "DeviceUnknown",
    "MissingScopes",
    "NoActiveDevice",
    "NotConnected",
    "PremiumRequired",
    "ProviderRejected",
    "ProviderUnavailable",
    "RateLimited",
    "STATUS_AUTHORIZATION_PENDING",
    "STATUS_CONNECTED",
    "STATUS_DISCONNECTED",
    "STATUS_MISSING_SCOPES",
    "STATUS_PREMIUM_REQUIRED",
    "STATUS_PROVIDER_REJECTED",
    "STATUS_REFRESH_FAILED",
    "STATUS_TEMPORARILY_UNAVAILABLE",
    "SpotifyPlayerError",
]
