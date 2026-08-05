"""Typed Spotify playback actions: act, then look, then say what you saw.

Every player write returns ``204 No Content``. That is Spotify saying "I have
your request", not "the speaker changed". Spotify also warns that "the order of
execution is not guaranteed when you use this API with other Player API
endpoints", so even a request that lands may be observed out of order.

So each action here re-reads playback afterwards and reports what it *observed*,
with ``requested`` and ``observed`` as separate keys — the same shape the audio
milestone uses, for the same reason. An action whose effect cannot be seen is
``partially_applied`` with an explanation, never a success.

Where verification is genuinely impossible
------------------------------------------
Two operations cannot be fully verified, and both say so rather than pretending:

* **Add to queue.** The queue-reading endpoint exists, but a track added to the
  end of a long queue is not a state a re-read can confirm quickly, and the
  milestone asks only that the track be added. So queueing reports
  ``accepted_by_provider`` and explicitly does **not** claim playback started.
* **Next/previous.** The track that follows is Spotify's choice, not ours. The
  action reports the item observed afterwards without claiming it is the "right"
  one, and notes when the item did not change at all.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

from ..mediasearch.spotify import PROVIDER_ID as SPOTIFY_PROVIDER_ID
from ..mediasearch.spotify import build_uri
from .errors import (
    CODE_INVALID_VOLUME,
    CODE_RESULT_NOT_PLAYABLE,
    CODE_UNMUTE_UNKNOWN,
    NoActiveDevice,
    SpotifyPlayerError,
)
from .models import PlaybackSnapshot, SpotifyDevice
from .service import SpotifyPlayerService

# -- outcomes ----------------------------------------------------------------

OUTCOME_APPLIED = "applied"
OUTCOME_PARTIAL = "partially_applied"
OUTCOME_NOT_APPLIED = "not_applied"
OUTCOME_ACCEPTED = "accepted_by_provider"
"""Spotify accepted the request and it is not observable from playback state.
Used only by queueing, which is a statement about a list we do not re-read."""

MIN_VOLUME_PERCENT = 0
MAX_VOLUME_PERCENT = 100

# Only a track can be played or queued. Spotify's queue endpoint documents
# "must be a track or an episode uri", and an album or artist is a *context*,
# which is a different endpoint with different semantics that this milestone
# deliberately does not model.
PLAYABLE_ITEM_TYPES = ("track",)


def clean_volume_percent(raw: Any) -> int:
    """Validate a Spotify volume, or refuse it. Never clamps.

    Identical in spirit to the system-volume rule in the audio milestone, and
    separate from it on purpose: this is Spotify's own device level, and the two
    must never share a value or a control.
    """
    if isinstance(raw, bool):
        raise SpotifyPlayerError(CODE_INVALID_VOLUME, "the volume must be a number")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw):
            raise SpotifyPlayerError(CODE_INVALID_VOLUME, "the volume must be a real number")
        if not float(raw).is_integer():
            raise SpotifyPlayerError(
                CODE_INVALID_VOLUME,
                "the volume must be a whole percentage",
                "Spotify takes whole percentages, so a fractional value is refused rather than "
                "rounded to something you did not ask for",
            )
        value = int(raw)
    else:
        raise SpotifyPlayerError(CODE_INVALID_VOLUME, "the volume must be a number")

    if value < MIN_VOLUME_PERCENT or value > MAX_VOLUME_PERCENT:
        raise SpotifyPlayerError(
            CODE_INVALID_VOLUME,
            f"the volume must be between {MIN_VOLUME_PERCENT} and {MAX_VOLUME_PERCENT} percent",
        )
    return value


class SpotifyActionExecutor:
    """The typed actions, each verified against a re-read of playback state."""

    def __init__(self, service: SpotifyPlayerService, sessions=None) -> None:
        self._service = service
        self._sessions = sessions

    # -- helpers -----------------------------------------------------------

    def _fresh(self) -> PlaybackSnapshot:
        """The pre-action observation, or the refusal that explains why not.

        The connection check happens *here*, before any caller looks at a device
        list, so it cannot be forgotten by an action added later. See
        :meth:`SpotifyPlayerService.require_playable` for why the ordering
        matters.
        """
        snapshot = self._service.snapshot(refresh=True)
        self._service.require_playable(snapshot)
        return snapshot

    def _reobserve(self) -> PlaybackSnapshot:
        self._service.invalidate()
        return self._service.snapshot(refresh=True)

    def _tokens(self):
        return self._service._authorized_tokens()

    def _result(
        self,
        operation: str,
        outcome: str,
        requested: Mapping[str, Any],
        observed: Optional[Mapping[str, Any]],
        message: str,
        snapshot: PlaybackSnapshot,
    ) -> Dict[str, Any]:
        return {
            "operation": operation,
            "outcome": outcome,
            "requested": dict(requested),
            "observed": dict(observed) if observed is not None else None,
            "message": message,
            "observed_at": snapshot.observed_at,
            "playback": snapshot.to_dict(),
        }

    # -- transport ---------------------------------------------------------

    def pause(self) -> Dict[str, Any]:
        before = self._fresh()
        device = self._service.target_device(before)
        tokens = self._tokens()

        self._service.client.pause(tokens, device.provider_device_id)
        after = self._reobserve()

        if not after.playback_available:
            # Spotify answers 204 when nothing is playing; after a pause that is
            # a plausible and honest end state, not a failure.
            return self._result(
                "spotify_pause", OUTCOME_APPLIED, {"is_playing": False},
                {"is_playing": False, "playback_available": False},
                "Spotify is paused", after,
            )
        outcome = OUTCOME_APPLIED if not after.is_playing else OUTCOME_NOT_APPLIED
        message = "Spotify is paused" if not after.is_playing else "Spotify is still playing"
        return self._result(
            "spotify_pause", outcome, {"is_playing": False}, {"is_playing": after.is_playing},
            message, after,
        )

    def resume(self) -> Dict[str, Any]:
        before = self._fresh()
        device = self._service.target_device(before)
        tokens = self._tokens()

        self._service.client.resume(tokens, device.provider_device_id)
        after = self._reobserve()

        if not after.playback_available:
            return self._result(
                "spotify_resume", OUTCOME_NOT_APPLIED, {"is_playing": True},
                {"is_playing": False, "playback_available": False},
                "Spotify reports nothing playing, so there was nothing to resume", after,
            )
        outcome = OUTCOME_APPLIED if after.is_playing else OUTCOME_NOT_APPLIED
        message = "Spotify is playing" if after.is_playing else "Spotify did not start playing"
        return self._result(
            "spotify_resume", outcome, {"is_playing": True}, {"is_playing": after.is_playing},
            message, after,
        )

    def skip(self, forward: bool) -> Dict[str, Any]:
        """Next or previous, reporting the item observed afterwards.

        What *should* come next is Spotify's decision — queue, shuffle, radio —
        so this reports the item it saw rather than claiming a specific track.
        """
        operation = "spotify_next" if forward else "spotify_previous"
        before = self._fresh()
        device = self._service.target_device(before)
        tokens = self._tokens()
        previous_track = before.now_playing.track_id if before.now_playing else None

        if forward:
            self._service.client.next_track(tokens, device.provider_device_id)
        else:
            self._service.client.previous_track(tokens, device.provider_device_id)
        after = self._reobserve()

        current = after.now_playing.track_id if after.now_playing else None
        observed = {
            "track_id": current,
            "title": after.now_playing.title if after.now_playing else None,
            "is_playing": after.is_playing,
        }
        if not after.playback_available:
            return self._result(
                operation, OUTCOME_PARTIAL, {"direction": "next" if forward else "previous"},
                observed,
                "Spotify accepted the request but reports nothing playing now", after,
            )
        if previous_track is not None and current == previous_track:
            # Legitimately common: "previous" within the first seconds restarts
            # the current track. Reported as observed rather than as a failure.
            return self._result(
                operation, OUTCOME_PARTIAL, {"direction": "next" if forward else "previous"},
                observed,
                "Spotify is still on the same track — previous restarts a track that has only "
                "just begun" if not forward else "Spotify is still on the same track",
                after,
            )
        return self._result(
            operation, OUTCOME_APPLIED, {"direction": "next" if forward else "previous"},
            observed, "Spotify moved to another track", after,
        )

    # -- volume and mute ---------------------------------------------------

    def set_volume(self, volume_percent: Any, device_resource_id: object = None) -> Dict[str, Any]:
        percent = clean_volume_percent(volume_percent)
        before = self._fresh()
        device = self._service.target_device(before, device_resource_id)
        self._require_volume(device)
        tokens = self._tokens()

        self._service.client.set_volume(tokens, percent, device.provider_device_id)
        after = self._reobserve()

        observed_device = after.device_by_resource_id(device.resource_id)
        observed_volume = observed_device.volume_percent if observed_device else None

        # A non-zero volume set by hand ends a Cofferdam mute, so the stored
        # restore level would be describing a mute that no longer exists.
        if percent > 0:
            self._service.mute_state.forget(device.resource_id)

        if observed_volume is None:
            outcome, message = (
                OUTCOME_PARTIAL,
                "Spotify accepted the volume but did not report it back, so the change is not "
                "confirmed",
            )
        elif observed_volume == percent:
            outcome, message = OUTCOME_APPLIED, f"Spotify volume is now {observed_volume}%"
        else:
            outcome, message = (
                OUTCOME_NOT_APPLIED,
                f"Spotify volume was set to {percent}% but the device reports {observed_volume}%",
            )
        return self._result(
            "spotify_set_volume", outcome, {"volume_percent": percent},
            {"volume_percent": observed_volume}, message, after,
        )

    def set_muted(self, muted: Any, device_resource_id: object = None) -> Dict[str, Any]:
        """Mute or unmute — which on Spotify means volume zero and back.

        Spotify publishes no mute operation. This is named ``muted_by_cofferdam``
        everywhere it surfaces so nobody can mistake it for one.
        """
        if not isinstance(muted, bool):
            raise SpotifyPlayerError(CODE_INVALID_VOLUME, "muted must be true or false")

        before = self._fresh()
        device = self._service.target_device(before, device_resource_id)
        self._require_volume(device)
        tokens = self._tokens()

        if muted:
            current = device.volume_percent
            if current is not None and current > 0:
                self._service.mute_state.remember(device.resource_id, current)
            target = 0
        else:
            restore = self._service.mute_state.restore_value(device.resource_id)
            if restore is None:
                # The rule this milestone is explicit about: never invent a
                # level. After a restart, or a mute performed in the Spotify app
                # itself, Cofferdam does not know what "unmuted" sounded like.
                raise SpotifyPlayerError(
                    CODE_UNMUTE_UNKNOWN,
                    "Cofferdam does not know what volume to restore",
                    "it did not perform the mute it is being asked to undo, so it will not pick "
                    "a level for you — set a volume directly instead",
                )
            target = restore

        self._service.client.set_volume(tokens, target, device.provider_device_id)
        after = self._reobserve()

        observed_device = after.device_by_resource_id(device.resource_id)
        observed_volume = observed_device.volume_percent if observed_device else None
        if not muted and observed_volume == target:
            self._service.mute_state.forget(device.resource_id)

        observed = {
            "volume_percent": observed_volume,
            "muted_by_cofferdam": after.muted_by_cofferdam,
        }
        if observed_volume is None:
            outcome, message = (
                OUTCOME_PARTIAL,
                "Spotify accepted the change but did not report the volume back",
            )
        elif observed_volume == target:
            outcome = OUTCOME_APPLIED
            message = (
                "Spotify is muted — Cofferdam set its volume to zero"
                if muted
                else f"Spotify volume restored to {observed_volume}%"
            )
        else:
            outcome, message = (
                OUTCOME_NOT_APPLIED,
                f"the device reports {observed_volume}% rather than {target}%",
            )
        return self._result(
            "spotify_set_mute", outcome, {"muted": muted, "volume_percent": target},
            observed, message, after,
        )

    def _require_volume(self, device: SpotifyDevice) -> None:
        from .errors import VolumeUnsupported

        if not device.supports_volume:
            raise VolumeUnsupported()

    # -- devices -----------------------------------------------------------

    def transfer(self, device_resource_id: object, play: bool = False) -> Dict[str, Any]:
        """Move Spotify playback to another Connect device.

        This changes where **Spotify** plays. It does not touch this computer's
        audio output — that is the Computer Audio panel, a different subsystem
        with a different backend — and the message says so, because a user
        looking at two "output" controls deserves to know which one moved.
        """
        before = self._fresh()
        device = self._service.resolve_device(before, device_resource_id)
        tokens = self._tokens()

        self._service.client.transfer(tokens, device.provider_device_id, play=bool(play))
        after = self._reobserve()

        observed_active = after.active_device_resource_id
        observed = {
            "active_device_resource_id": observed_active,
            "is_playing": after.is_playing,
        }
        if observed_active == device.resource_id:
            outcome = OUTCOME_APPLIED
            message = f"Spotify is now playing through {device.name or 'the selected device'}"
        elif observed_active is None:
            outcome = OUTCOME_PARTIAL
            message = (
                "Spotify accepted the transfer but reports no active device yet — it can take a "
                "moment for a device to take over"
            )
        else:
            outcome = OUTCOME_NOT_APPLIED
            message = "Spotify is still using a different device"
        result = self._result(
            "spotify_transfer_playback", outcome,
            {"device_resource_id": device.resource_id, "play": bool(play)},
            observed, message, after,
        )
        result["system_audio_unchanged"] = True
        return result

    # -- search-result playback -------------------------------------------

    def _resolve_track(self, search_id: object, result_id: object) -> Tuple[str, Any]:
        """The verified track behind a search result, as a Spotify URI.

        The client sends a search id and a result id and nothing else. The URI is
        rebuilt here from the session's private ``ProviderItem``, so there is no
        request field for a URI, a track id, or a URL to validate — they are
        absent from the schema rather than rejected by it. ``provider_id``
        pins the lookup to Spotify, which is what stops a YouTube result being
        routed into this path.
        """
        if self._sessions is None:  # pragma: no cover - wiring error
            raise SpotifyPlayerError(
                CODE_RESULT_NOT_PLAYABLE, "search results are not available on this host"
            )
        _session, result, item = self._sessions.resolve(
            search_id, result_id, provider_id=SPOTIFY_PROVIDER_ID
        )
        if item.item_type not in PLAYABLE_ITEM_TYPES:
            raise SpotifyPlayerError(
                CODE_RESULT_NOT_PLAYABLE,
                "only a track can be played or queued",
                "albums, artists and playlists are contexts rather than tracks; open them in "
                "Spotify instead",
            )
        return build_uri(item.item_type, item.item_id), result

    def play_search_result(
        self, search_id: object, result_id: object, device_resource_id: object = None
    ) -> Dict[str, Any]:
        uri, result = self._resolve_track(search_id, result_id)
        expected_track_id = uri.rsplit(":", 1)[-1]

        before = self._fresh()
        device = self._service.target_device(before, device_resource_id)
        tokens = self._tokens()

        self._service.client.play_uris(tokens, [uri], device.provider_device_id)
        after = self._reobserve()

        observed_id = after.now_playing.track_id if after.now_playing else None
        observed = {
            "track_id": observed_id,
            "title": after.now_playing.title if after.now_playing else None,
            "is_playing": after.is_playing,
        }
        requested = {"result_id": getattr(result, "result_id", None), "track_id": expected_track_id}

        if observed_id == expected_track_id and after.is_playing:
            outcome, message = OUTCOME_APPLIED, "Spotify is playing the track you chose"
        elif observed_id == expected_track_id:
            outcome = OUTCOME_PARTIAL
            message = "Spotify loaded the track you chose but reports it as not playing"
        elif observed_id is None:
            outcome = OUTCOME_PARTIAL
            message = (
                "Spotify accepted the request but has not reported a track yet — it can take a "
                "moment to start"
            )
        else:
            # The strongest check available: the item now playing is not the one
            # that was asked for. Never reported as success.
            outcome = OUTCOME_NOT_APPLIED
            message = "Spotify is playing something other than the track you chose"
        return self._result("spotify_play_search_result", outcome, requested, observed, message, after)

    def queue_search_result(
        self, search_id: object, result_id: object, device_resource_id: object = None
    ) -> Dict[str, Any]:
        uri, result = self._resolve_track(search_id, result_id)

        before = self._fresh()
        device = self._service.target_device(before, device_resource_id)
        tokens = self._tokens()

        self._service.client.queue(tokens, uri, device.provider_device_id)
        after = self._reobserve()

        # Deliberately *not* claiming anything about playback. Queueing adds to
        # a list; the current track is expected to keep playing, and saying
        # otherwise would be the exact false success this codebase avoids.
        return self._result(
            "spotify_queue_search_result",
            OUTCOME_ACCEPTED,
            {"result_id": getattr(result, "result_id", None)},
            {
                "is_playing": after.is_playing,
                "track_id": after.now_playing.track_id if after.now_playing else None,
            },
            "Spotify accepted the track into the queue — what is playing now has not changed",
            after,
        )

    # -- disconnect --------------------------------------------------------

    def disconnect(self) -> Dict[str, Any]:
        """Remove Cofferdam's local authorization.

        This does **not** revoke anything at Spotify: the API publishes no
        revocation endpoint for this flow. Saying "revoked" would leave someone
        believing an app no longer has access when the grant is still listed in
        their account. The message says what actually happened and where to do
        the other half.
        """
        removed = self._service.tokens.clear()
        self._service.mute_state.clear()
        self._service.invalidate()
        after = self._service.snapshot(refresh=True)
        return {
            "operation": "spotify_disconnect",
            "outcome": OUTCOME_APPLIED if removed else OUTCOME_NOT_APPLIED,
            "requested": {"disconnect": True},
            "observed": {"status": after.connection.get("status")},
            "message": (
                "Spotify is disconnected from Cofferdam. This removed the authorization stored "
                "on this machine; it did not revoke Cofferdam's access in your Spotify account."
                if removed
                else "no Spotify account was connected"
            ),
            "revoked_at_provider": False,
            "observed_at": after.observed_at,
            "playback": after.to_dict(),
        }


__all__ = [
    "MAX_VOLUME_PERCENT",
    "MIN_VOLUME_PERCENT",
    "OUTCOME_ACCEPTED",
    "OUTCOME_APPLIED",
    "OUTCOME_NOT_APPLIED",
    "OUTCOME_PARTIAL",
    "PLAYABLE_ITEM_TYPES",
    "SpotifyActionExecutor",
    "clean_volume_percent",
]
