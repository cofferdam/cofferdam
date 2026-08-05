"""Typed Spotify playback actions: act, then look, then say what you saw.

Every player write returns ``204 No Content``. That is Spotify saying "I have
your request", not "the speaker changed". Spotify also warns that "the order of
execution is not guaranteed when you use this API with other Player API
endpoints", so even a request that lands may be observed out of order.

So each action here re-reads playback afterwards and reports what it *observed*,
with ``requested`` and ``observed`` as separate keys — the same shape the audio
milestone uses, for the same reason. An action whose effect cannot be seen is
``partially_applied`` with an explanation, never a success.

Once was not enough
-------------------
Real validation on the phone found that reading **once** was the bug. Setting the
volume to 80% reported *"set to 80% but the device reports 50%"*, and the first
Play now reported *"playing something other than the track you chose"* — both
while Spotify was doing exactly what it had been asked. Spotify's player
endpoints are eventually consistent, so the read that happens microseconds after
a write frequently still describes the world before it, and the honest-looking
report was wrong in the user's favour's opposite direction: it denied a success
that had happened.

So every observation here now goes through :mod:`.confirm` — the same immediate
first read, followed by a **bounded** number of further reads, and then a truthful
give-up. Nothing loops, and nothing reports the requested value as though it were
observed.

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
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from ..mediasearch.spotify import PROVIDER_ID as SPOTIFY_PROVIDER_ID
from ..mediasearch.spotify import build_uri
from .coldstart import DeviceRecovery
from .confirm import (
    PLAYBACK_CONFIRM,
    TRANSPORT_CONFIRM,
    VOLUME_CONFIRM,
    ConfirmWindow,
    confirm,
)
from .errors import (
    CODE_INVALID_VOLUME,
    CODE_RESULT_NOT_PLAYABLE,
    CODE_UNMUTE_UNKNOWN,
    SpotifyPlayerError,
)
from .models import PlaybackSnapshot, SpotifyDevice
from .progress import (
    PHASE_CONFIRMING_VOLUME,
    PHASE_STARTING,
    PHASE_VERIFYING,
    ActivityRecorder,
    OperationProgress,
)
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

    def __init__(
        self,
        service: SpotifyPlayerService,
        sessions=None,
        recovery: Optional[DeviceRecovery] = None,
        activity: Optional[ActivityRecorder] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._service = service
        self._sessions = sessions
        # Absent on a host with no launcher wired: recovery is then simply not
        # offered, and the old truthful "no active device" stands.
        self._recovery = recovery
        self._activity = activity or ActivityRecorder()
        self._sleeper = sleeper

    @property
    def activity(self) -> ActivityRecorder:
        return self._activity

    # -- helpers -----------------------------------------------------------

    def _begin(self, operation: str) -> OperationProgress:
        """Start a correlated operation and publish it as the current activity.

        The correlation id is random hex with nothing of the account in it, which
        is what makes it the only part of a playback operation that is safe to
        carry into an audit record.
        """
        progress = OperationProgress(operation=operation)
        self._activity.begin(progress, operation)
        return progress

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

    def _confirm_snapshot(
        self, matches: Callable[[PlaybackSnapshot], bool], window: ConfirmWindow
    ) -> Tuple[PlaybackSnapshot, bool]:
        """Re-read until the snapshot agrees, or until the window is spent.

        Every caller uses the snapshot this returns as the response's observed
        state, matched or not — so a confirmation that timed out reports the last
        thing actually seen rather than the thing that was asked for.
        """
        return confirm(self._reobserve, matches, window, self._sleeper)

    def _tokens(self):
        return self._service.authorized_tokens()

    def _result(
        self,
        operation: str,
        outcome: str,
        requested: Mapping[str, Any],
        observed: Optional[Mapping[str, Any]],
        message: str,
        snapshot: PlaybackSnapshot,
        progress: Optional[OperationProgress] = None,
    ) -> Dict[str, Any]:
        payload = {
            "operation": operation,
            "outcome": outcome,
            "requested": dict(requested),
            "observed": dict(observed) if observed is not None else None,
            "message": message,
            "observed_at": snapshot.observed_at,
            "playback": snapshot.to_dict(),
        }
        if progress is not None:
            self._activity.finish(progress)
            payload["correlation_id"] = progress.correlation_id
            payload["progress"] = progress.to_dict()["steps"]
        return payload

    # -- transport ---------------------------------------------------------

    def pause(self) -> Dict[str, Any]:
        before = self._fresh()
        device = self._service.target_device(before)
        tokens = self._tokens()

        self._service.client.pause(tokens, device.provider_device_id)
        # Bounded confirmation rather than one immediate read: the state endpoint
        # can still describe the moment before the write. "Nothing playing" also
        # satisfies a pause, so it ends the wait too.
        after, _matched = self._confirm_snapshot(
            lambda snapshot: not snapshot.is_playing or not snapshot.playback_available,
            TRANSPORT_CONFIRM,
        )

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
        after, _matched = self._confirm_snapshot(
            lambda snapshot: snapshot.is_playing, TRANSPORT_CONFIRM
        )

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

        # A skip is confirmed by the item *changing*. When it legitimately does
        # not — "previous" inside the first seconds restarts the current track —
        # the window is spent and the honest partial result below is reported,
        # which is the same answer the single read used to give, only after
        # having actually looked more than once.
        after, _matched = self._confirm_snapshot(
            lambda snapshot: (
                (snapshot.now_playing.track_id if snapshot.now_playing else None)
                != previous_track
            ),
            TRANSPORT_CONFIRM,
        )

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
        progress = self._begin("spotify_set_volume")
        before = self._fresh()
        device = self._service.target_device(before, device_resource_id)
        self._require_volume(device)
        tokens = self._tokens()

        self._service.client.set_volume(tokens, percent, device.provider_device_id)

        # The defect this replaced: one immediate read of the devices endpoint,
        # which is eventually consistent, so 50 → 80 reported "set to 80% but the
        # device reports 50%" while the speaker was already at 80. The volume is
        # now re-read on a bounded schedule until the device agrees.
        progress.enter(PHASE_CONFIRMING_VOLUME)

        def at_target(snapshot: PlaybackSnapshot) -> bool:
            seen = snapshot.device_by_resource_id(device.resource_id)
            return bool(seen and seen.volume_percent == percent)

        after, matched = self._confirm_snapshot(at_target, VOLUME_CONFIRM)

        observed_device = after.device_by_resource_id(device.resource_id)
        observed_volume = observed_device.volume_percent if observed_device else None

        # A non-zero volume set by hand ends a Cofferdam mute, so the stored
        # restore level would be describing a mute that no longer exists.
        if percent > 0:
            self._service.mute_state.forget(device.resource_id)

        if matched:
            outcome, message = OUTCOME_APPLIED, f"Spotify volume is now {observed_volume}%"
        elif observed_volume is None:
            outcome, message = (
                OUTCOME_PARTIAL,
                "Spotify accepted the volume but did not report it back, so the change is not "
                "confirmed",
            )
        else:
            # Still not the requested level after the whole window. Reported as
            # what was actually seen — never as the number that was asked for.
            outcome, message = (
                OUTCOME_PARTIAL,
                f"Spotify accepted {percent}% but still reports {observed_volume}% after "
                f"{int(VOLUME_CONFIRM.timeout_seconds)}s — refresh to see where it settled",
            )
        return self._result(
            "spotify_set_volume", outcome, {"volume_percent": percent},
            {"volume_percent": observed_volume, "confirmed": matched}, message, after, progress,
        )

    def set_muted(self, muted: Any, device_resource_id: object = None) -> Dict[str, Any]:
        """Mute or unmute — which on Spotify means volume zero and back.

        Spotify publishes no mute operation. This is named ``muted_by_cofferdam``
        everywhere it surfaces so nobody can mistake it for one.
        """
        if not isinstance(muted, bool):
            raise SpotifyPlayerError(CODE_INVALID_VOLUME, "muted must be true or false")

        progress = self._begin("spotify_set_mute")
        before = self._fresh()
        device = self._service.target_device(before, device_resource_id)
        self._require_volume(device)
        tokens = self._tokens()

        restore_level = None
        if muted:
            current = device.volume_percent
            if current is not None and current > 0:
                restore_level = current
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

        # Same bounded confirmation as an ordinary volume change, because it is
        # one: mute *is* a volume write, and it lags in exactly the same way.
        def at_target(snapshot: PlaybackSnapshot) -> bool:
            seen = snapshot.device_by_resource_id(device.resource_id)
            return bool(seen and seen.volume_percent == target)

        after, matched = self._confirm_snapshot(at_target, VOLUME_CONFIRM)

        if muted and matched and restore_level is not None:
            # Confirmation polling and the stale-record cleanup collided here,
            # and the collision only appears when the provider lags. Each read in
            # the window builds a snapshot, and a snapshot that still shows the
            # *old* non-zero volume looks exactly like "the user turned it back
            # up in the Spotify app" — so the level to restore was dropped
            # mid-mute, and the following unmute refused as though Cofferdam had
            # never muted anything. Re-recording after the mute is confirmed is
            # idempotent and repairs that. The cleanup itself is still right for
            # ordinary reads; it just cannot tell a lagging read from a real one.
            self._service.mute_state.remember(device.resource_id, restore_level)
            after = self._reobserve()
        if not muted and matched:
            self._service.mute_state.forget(device.resource_id)

        observed_device = after.device_by_resource_id(device.resource_id)
        observed_volume = observed_device.volume_percent if observed_device else None

        observed = {
            "volume_percent": observed_volume,
            "muted_by_cofferdam": after.muted_by_cofferdam,
            "confirmed": matched,
        }
        if matched:
            outcome = OUTCOME_APPLIED
            message = (
                "Spotify is muted — Cofferdam set its volume to zero"
                if muted
                else f"Spotify volume restored to {observed_volume}%"
            )
        elif observed_volume is None:
            outcome, message = (
                OUTCOME_PARTIAL,
                "Spotify accepted the change but did not report the volume back",
            )
        else:
            outcome, message = (
                OUTCOME_PARTIAL,
                f"Spotify accepted the change but still reports {observed_volume}% rather than "
                f"{target}% — refresh to see where it settled",
            )
        return self._result(
            "spotify_set_mute", outcome, {"muted": muted, "volume_percent": target},
            observed, message, after, progress,
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
        """Play one verified track, opening Spotify first if it has to.

        The full sequence, and every step of it earned by a real failure on the
        phone: resolve the private session result, read devices, launch Spotify
        if there is nowhere to play, wait for its device to register, transfer to
        that device if it is present but idle, send exact-track playback, then
        **poll** for confirmation rather than reading once.

        The two defects this replaces were separate and both looked like "Play
        now does not work the first time". With Spotify open but idle the device
        existed and was inactive, and the old code refused outright — which is
        why "Open in Spotify, then Play now" was a working workaround. And even
        when playback did start, a single immediate read often still described
        the previous track, so a successful play was reported as *"playing
        something other than the track you chose"*.
        """
        uri, result = self._resolve_track(search_id, result_id)
        expected_track_id = uri.rsplit(":", 1)[-1]

        progress = self._begin("spotify_play_search_result")
        before = self._fresh()

        if self._recovery is not None:
            device, before = self._recovery.resolve(before, device_resource_id, progress)
            # A device that is present but not the one Spotify is on will accept
            # `play?device_id=` in principle, but validation showed the first
            # play against a cold device is the one that goes missing. Making it
            # active first is the documented operation for exactly this, and it
            # is one bounded attempt.
            if not device.is_active:
                device, before = self._recovery.activate(device, progress)
        else:  # pragma: no cover - only when no launcher is wired
            device = self._service.target_device(before, device_resource_id)

        tokens = self._tokens()
        progress.enter(PHASE_STARTING)
        self._service.client.play_uris(tokens, [uri], device.provider_device_id)

        progress.enter(PHASE_VERIFYING)
        after, matched = self._confirm_snapshot(
            lambda snapshot: bool(
                snapshot.now_playing
                and snapshot.now_playing.track_id == expected_track_id
                and snapshot.is_playing
            ),
            PLAYBACK_CONFIRM,
        )

        observed_id = after.now_playing.track_id if after.now_playing else None
        observed = {
            "track_id": observed_id,
            "title": after.now_playing.title if after.now_playing else None,
            "is_playing": after.is_playing,
            "confirmed": matched,
        }
        requested = {"result_id": getattr(result, "result_id", None), "track_id": expected_track_id}

        if matched:
            outcome, message = OUTCOME_APPLIED, "Spotify is playing the track you chose"
        elif observed_id == expected_track_id:
            outcome = OUTCOME_PARTIAL
            message = "Spotify loaded the track you chose but reports it as not playing"
        elif observed_id is None:
            outcome = OUTCOME_PARTIAL
            message = (
                "Spotify accepted the request but never reported a track — check Spotify on the "
                "workstation, or try again"
            )
        else:
            # The strongest check available, now made after looking more than
            # once: the item playing is not the one that was asked for.
            outcome = OUTCOME_NOT_APPLIED
            message = "Spotify is playing something other than the track you chose — try again"
        return self._result(
            "spotify_play_search_result", outcome, requested, observed, message, after, progress
        )

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
