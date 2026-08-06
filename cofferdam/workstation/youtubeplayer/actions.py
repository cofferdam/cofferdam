"""The typed YouTube player actions, and what each one is allowed to claim.

Every public method here follows the same five steps, in this order, with no
shortcuts for the fast cases:

1. **Validate the request** against a strict schema. A value that is out of
   range is refused, never clamped.
2. **Resolve authority** from Cofferdam's own state — a search session for a
   video, the Cofferdam queue for Next and Previous. Nothing a client sent is
   ever a destination.
3. **Make a player exist**, launching at most once.
4. **Send the command** and wait, bounded, for the player to acknowledge it.
5. **Re-read what the player reports** and answer with that.

Step 5 is the one that matters. The response's ``outcome`` is derived from the
player's own report and never from the fact that a command was delivered. A
volume set to 80 that the player still reports as 50 is
``youtube_volume_not_observed``, not success. A video that was loaded and is not
playing is ``autoplay_blocked`` or ``partially_applied``, not "playing".

Where a video comes from
------------------------

Exactly two places, and both are Cofferdam's:

* a **verified search result** — resolved through the existing search session
  store, checked for provider, checked for result type, and re-validated for id
  shape before anything is sent;
* a **queue item** — which is a video that already passed that check when it was
  added.

There is no third path, and in particular there is no path from a request body
to a video id. The routes accept a search id, a result id, a queue item id, an
integer and a boolean; a body carrying a URL, a video id, a player command or a
script has no field to arrive in.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..mediasearch.results import RESULT_TYPE_VIDEO
from ..mediasearch.youtube import PROVIDER_ID as YOUTUBE_PROVIDER_ID
from ..mediasearch.youtube import valid_video_id
from .errors import (
    CODE_EMBED_IDENTITY_REJECTED,
    EmbedIdentityRejected,
    InvalidMute,
    InvalidVolume,
    MuteNotObserved,
    NoPlayerConnected,
    PlayerBusy,
    ResultNotPlayable,
    TransportNotObserved,
    VideoNotObserved,
    VolumeNotObserved,
    WrongProvider,
)
from .models import (
    PLAYBACK_AUTOPLAY_BLOCKED,
    PLAYBACK_BUFFERING,
    PLAYBACK_ENDED,
    PLAYBACK_PAUSED,
    PLAYBACK_PLAYING,
    QueueItem,
)
from .progress import (
    PHASE_CONFIRMING_VOLUME,
    PHASE_LOADING,
    PHASE_STARTING,
    PHASE_VERIFYING,
    ActivityRecorder,
    OperationProgress,
)
from .service import (
    LOAD_WINDOW,
    TRANSPORT_WINDOW,
    VOLUME_WINDOW,
    PlayerService,
)

# -- outcome vocabulary ------------------------------------------------------
#
# Closed, and deliberately not just "ok". Each of these is a different sentence
# on a phone and a different thing for a person to do next.

OUTCOME_APPLIED = "applied"
OUTCOME_QUEUED = "queued"
OUTCOME_AUTOPLAY_BLOCKED = "autoplay_blocked"
OUTCOME_PARTIALLY_APPLIED = "partially_applied"

OUTCOMES = (
    OUTCOME_APPLIED,
    OUTCOME_QUEUED,
    OUTCOME_AUTOPLAY_BLOCKED,
    OUTCOME_PARTIALLY_APPLIED,
)

#: The outcomes the audit path records as a success. ``partially_applied`` is
#: deliberately absent: something did not do what was asked, and the log should
#: not say it did.
SUCCESS_OUTCOMES = frozenset({OUTCOME_APPLIED, OUTCOME_QUEUED})


class YouTubeActionExecutor:
    """One player, one queue, one command at a time."""

    def __init__(
        self,
        service: PlayerService,
        sessions,
        activity: Optional[ActivityRecorder] = None,
    ) -> None:
        self._service = service
        self._sessions = sessions
        self.activity = activity or ActivityRecorder()

    # -- shared plumbing -----------------------------------------------------

    def _exclusive(self, operation: str, work: Callable[[OperationProgress], Dict[str, Any]]):
        """Run one write with the player lock held, or refuse as busy.

        Non-blocking acquisition on purpose. Queueing a second Play now behind
        the first would load two videos in sequence and confirm neither, and a
        user who tapped twice would watch the wrong one start. A refusal the
        client can retry is the honest answer, and it is also what makes
        duplicate submissions harmless rather than merely unlikely.
        """
        lock = self._service.write_lock
        if not lock.acquire(blocking=False):
            raise PlayerBusy()
        progress = OperationProgress()
        self.activity.begin(progress, operation)
        try:
            return work(progress)
        finally:
            self.activity.finish(progress)
            lock.release()

    def _envelope(
        self, progress: OperationProgress, outcome: str, note: str, **extra: Any
    ) -> Dict[str, Any]:
        """The observed answer. Assembled *after* the work, never before.

        ``player`` is a fresh snapshot read at the end of the operation, so a
        client that renders it is rendering what the player reported and not
        what the request asked for.
        """
        payload = {
            "outcome": outcome,
            "note": note,
            "correlation_id": progress.correlation_id,
            "progress": progress.to_dict(),
            "player": self._service.snapshot().to_dict(),
        }
        payload.update(extra)
        return payload

    # -- authority -----------------------------------------------------------

    def _resolve_video(self, search_id: object, result_id: object):
        """A verified YouTube video from a live search session, or a refusal.

        Four independent checks, none of which trusts the one before it:

        1. the session must exist and not have expired — the store raises
           otherwise, and an expired session is removed rather than reused;
        2. it must belong to **YouTube** — this is what stops a Spotify result
           being routed into the YouTube player;
        3. the *result* must be a video — a channel or playlist card has no
           video id and could only produce a broken load;
        4. the private provider item's id must still match the documented video
           id shape. It was validated when the search normalized it; it is
           validated again here because the value is about to become the thing
           the player loads.
        """
        session, result, item = self._sessions.resolve(
            search_id, result_id, provider_id=YOUTUBE_PROVIDER_ID
        )
        if item.provider_id != YOUTUBE_PROVIDER_ID:  # pragma: no cover - store invariant
            raise WrongProvider()
        if result.result_type != RESULT_TYPE_VIDEO or item.item_type != RESULT_TYPE_VIDEO:
            raise ResultNotPlayable(
                "only video results can be played in the YouTube player"
            )
        if not valid_video_id(item.item_id):
            raise ResultNotPlayable("that result has no usable video id")
        return session, result, item

    # -- playing -------------------------------------------------------------

    def play_search_result(self, search_id: object, result_id: object) -> Dict[str, Any]:
        """Play the exact video behind a verified result, in the one player.

        One deliberate press is enough: if no player is open this launches one,
        waits for it, and then continues the original request rather than asking
        the user to press again. It never opens a normal watch tab as a fallback
        — that is the explicit *Open in YouTube* action and stays the user's
        choice.
        """
        session, result, item = self._resolve_video(search_id, result_id)

        def work(progress: OperationProgress) -> Dict[str, Any]:
            self._service.ensure_player(progress)

            metadata = self._service.describe(item.item_id, result)
            queued = self._service.queue.play_now(item.item_id, metadata)

            progress.enter(PHASE_LOADING)
            self._service.load_video(queued, autoplay=True)
            self._service.set_result_handle(result.result_id)

            progress.enter(PHASE_VERIFYING)
            return self._confirm_playing(progress, queued)

        return self._exclusive("youtube_play_search_result", work)

    def _confirm_playing(
        self, progress: OperationProgress, item: QueueItem
    ) -> Dict[str, Any]:
        """Wait for the player to report *this* video, then say what it is doing.

        Two separate confirmations, in order, because they fail differently.

        The first is identity: did the player load the video that was asked for?
        A player showing something else is :class:`~.errors.VideoNotObserved` —
        never a success — because reporting otherwise would tell someone their
        video is playing while a different one is on screen.

        The second is transport: given the right video, is it *playing*? A
        browser that refused to start audio without a gesture is a normal,
        documented outcome, and it is reported as ``autoplay_blocked`` with the
        video still cued, so one click on the workstation resolves it.

        A reported *error* outranks both, and is checked twice: before the
        transport wait and again after it. Twice, because the two failures arrive
        at different moments — an embed YouTube refuses outright never loads at
        all, while one it refuses a moment later can satisfy the identity check
        first and then produce nothing to play. Either way the answer is the
        error the player reported, not "loaded but not playing", which would read
        as a slow video rather than a broken one.
        """
        loaded = self._service.confirm(
            lambda: self._service.observed_video_matches(item.video_id), LOAD_WINDOW
        )
        self._refuse_reported_error()
        if not loaded:
            raise VideoNotObserved()

        progress.enter(PHASE_STARTING)
        playing = self._service.confirm(
            lambda: self._playback_state() == PLAYBACK_PLAYING, TRANSPORT_WINDOW
        )
        state = self._playback_state()

        if playing:
            return self._envelope(
                progress, OUTCOME_APPLIED, "Playing on the workstation player."
            )
        self._refuse_reported_error()
        if state == PLAYBACK_AUTOPLAY_BLOCKED:
            return self._envelope(
                progress,
                OUTCOME_AUTOPLAY_BLOCKED,
                "The video is loaded, and the browser will not start sound until "
                "the player window on the workstation is clicked once. Press Play "
                "there, then this player works from your phone for the rest of the "
                "session.",
            )
        if state == PLAYBACK_BUFFERING:
            return self._envelope(
                progress,
                OUTCOME_PARTIALLY_APPLIED,
                "The video you chose is loaded and still buffering. Refresh to see "
                "whether it started.",
            )
        return self._envelope(
            progress,
            OUTCOME_PARTIALLY_APPLIED,
            "The video you chose is loaded but the player has not reported it as "
            "playing.",
        )

    def _playback_state(self) -> Optional[str]:
        observation = self._service.channel.observation()
        return observation.playback_state if observation is not None else None

    def _refuse_reported_error(self) -> None:
        """Raise the refusal the *player* reported, if it reported one.

        The mapping from YouTube's numeric code to a Cofferdam state already
        happened in :mod:`.errors`; this is only about which refusal a caller
        gets, and the distinction that matters is between "that video will not
        play here" and "this player could not identify itself".

        **Error 153 is the second kind**, and is the reason this is a branch and
        not a single ``VideoNotObserved``. Nothing is wrong with the video, no
        click on the workstation will help, and telling someone their video is
        unavailable — or that autoplay was blocked — would send them looking in
        the wrong place entirely. It gets its own code so the phone can say what
        actually happened and offer the two things that help.
        """
        observation = self._service.channel.observation()
        error = observation.error if observation is not None else None
        if not error:
            return
        self._service.note_error(error)
        if error.get("code") == CODE_EMBED_IDENTITY_REJECTED:
            raise EmbedIdentityRejected(error.get("detail"))
        # Everything else YouTube refuses is about the video itself — removed,
        # private, or not embeddable — and the player genuinely did not load what
        # was asked for. Its own words, from the documented code.
        raise VideoNotObserved(error.get("detail"))

    # -- queueing ------------------------------------------------------------

    def queue_search_result(self, search_id: object, result_id: object) -> Dict[str, Any]:
        """Add a verified video to the Cofferdam queue without interrupting anything.

        Deliberately does **not** touch the player: no load, no play, no pause.
        Adding something to watch later must never stop what is playing now, so
        this path sends no command at all — which is also why it does not require
        a connected player.
        """
        session, result, item = self._resolve_video(search_id, result_id)

        def work(progress: OperationProgress) -> Dict[str, Any]:
            metadata = self._service.describe(item.item_id, result)
            queued = self._service.queue.add(item.item_id, metadata)
            return self._envelope(
                progress,
                OUTCOME_QUEUED,
                "Added to the Cofferdam queue. Nothing that is playing was changed.",
                queue_item_id=queued.queue_item_id,
            )

        return self._exclusive("youtube_queue_search_result", work)

    def remove_queue_item(self, queue_item_id: object) -> Dict[str, Any]:
        def work(progress: OperationProgress) -> Dict[str, Any]:
            self._service.queue.remove(queue_item_id)
            return self._envelope(
                progress,
                OUTCOME_APPLIED,
                "Removed from the queue. Playback was not changed.",
            )

        return self._exclusive("youtube_remove_queue_item", work)

    def clear_queue(self) -> Dict[str, Any]:
        def work(progress: OperationProgress) -> Dict[str, Any]:
            dropped = self._service.queue.clear()
            return self._envelope(
                progress,
                OUTCOME_APPLIED,
                "Queue cleared ("
                + str(dropped)
                + " removed). Playback was not changed.",
            )

        return self._exclusive("youtube_clear_queue", work)

    # -- moving through the queue -------------------------------------------

    def skip(self, forward: bool) -> Dict[str, Any]:
        """Next or Previous — always a *Cofferdam* queue item, never a suggestion.

        This is the reason the queue exists. The IFrame API's ``nextVideo()`` is
        documented only against a YouTube playlist, and a YouTube playlist would
        decide for itself what comes next. Here, "next" is an item Cofferdam can
        name, and if there is not one the answer is a refusal rather than
        whatever YouTube would have played.
        """
        def work(progress: OperationProgress) -> Dict[str, Any]:
            if not self._service.channel.connected():
                raise NoPlayerConnected()
            item = self._service.queue.advance() if forward else self._service.queue.retreat()

            progress.enter(PHASE_LOADING)
            self._service.load_video(item, autoplay=True)
            self._service.set_result_handle(None)

            progress.enter(PHASE_VERIFYING)
            return self._confirm_playing(progress, item)

        return self._exclusive("youtube_next" if forward else "youtube_previous", work)

    # -- transport -----------------------------------------------------------

    def pause(self) -> Dict[str, Any]:
        def work(progress: OperationProgress) -> Dict[str, Any]:
            if not self._service.channel.connected():
                raise NoPlayerConnected()
            self._service.request_pause()
            progress.enter(PHASE_VERIFYING)
            settled = self._service.confirm(
                lambda: self._playback_state() in (PLAYBACK_PAUSED, PLAYBACK_ENDED),
                TRANSPORT_WINDOW,
            )
            if not settled:
                raise TransportNotObserved("pause")
            return self._envelope(progress, OUTCOME_APPLIED, "Paused.")

        return self._exclusive("youtube_pause", work)

    def resume(self) -> Dict[str, Any]:
        def work(progress: OperationProgress) -> Dict[str, Any]:
            if not self._service.channel.connected():
                raise NoPlayerConnected()
            self._service.request_play()
            progress.enter(PHASE_VERIFYING)
            settled = self._service.confirm(
                lambda: self._playback_state() == PLAYBACK_PLAYING, TRANSPORT_WINDOW
            )
            if settled:
                return self._envelope(progress, OUTCOME_APPLIED, "Playing.")
            # Before the autoplay branch, because a player YouTube refused to
            # load reports neither playing nor blocked, and "press play on the
            # workstation" is useless advice when there is no player to press.
            self._refuse_reported_error()
            if self._playback_state() == PLAYBACK_AUTOPLAY_BLOCKED:
                # One play request, one truthful answer. There is deliberately no
                # retry loop here: the browser is not going to change its mind
                # without a gesture, and hammering playVideo would only bury that.
                return self._envelope(
                    progress,
                    OUTCOME_AUTOPLAY_BLOCKED,
                    "The browser will not start sound until the player window on "
                    "the workstation is clicked once.",
                )
            raise TransportNotObserved("resume")

        return self._exclusive("youtube_resume", work)

    # -- volume and mute -----------------------------------------------------

    def set_volume(self, volume_percent: object) -> Dict[str, Any]:
        """Set the **YouTube player's own** volume. Not this computer's.

        Refuses anything that is not a whole number from 0 to 100. Not clamped:
        a request for 150 is not a request for 100, and treating it as one would
        teach a client that the range is advisory. Booleans are refused too —
        ``True`` is an ``int`` in Python, and a client sending it means something
        went wrong upstream.
        """
        wanted = _whole_percent(volume_percent)

        def work(progress: OperationProgress) -> Dict[str, Any]:
            if not self._service.channel.connected():
                raise NoPlayerConnected()
            self._service.request_volume(wanted)
            progress.enter(PHASE_CONFIRMING_VOLUME)
            settled = self._service.confirm(
                lambda: self._observed_volume() == wanted, VOLUME_WINDOW
            )
            if not settled:
                # The observed value, not the requested one. This is the exact
                # failure the Spotify milestone found in real validation and the
                # reason confirmation is bounded rather than single-shot.
                raise VolumeNotObserved(self._observed_volume())
            return self._envelope(
                progress,
                OUTCOME_APPLIED,
                "YouTube player volume is now "
                + str(self._observed_volume())
                + "%. This computer's speaker volume was not changed.",
            )

        return self._exclusive("youtube_set_volume", work)

    def _observed_volume(self) -> Optional[int]:
        observation = self._service.channel.observation()
        return observation.volume_percent if observation is not None else None

    def set_muted(self, muted: object) -> Dict[str, Any]:
        """Mute or unmute the YouTube player through the official player API.

        Unlike Spotify — which publishes no mute operation, so Cofferdam has to
        implement it as volume zero and says so — the IFrame Player API has real
        ``mute()`` and ``unMute()``, and documents that the volume is preserved
        across them. So this is a genuine mute, the published field is plainly
        ``muted``, and unmuting needs no remembered level.

        It never touches Computer Audio. Muting a video is not muting a machine.
        """
        if not isinstance(muted, bool):
            raise InvalidMute()

        def work(progress: OperationProgress) -> Dict[str, Any]:
            if not self._service.channel.connected():
                raise NoPlayerConnected()
            self._service.request_mute(muted)
            progress.enter(PHASE_VERIFYING)
            settled = self._service.confirm(
                lambda: self._observed_muted() is muted, TRANSPORT_WINDOW
            )
            if not settled:
                raise MuteNotObserved(muted)
            return self._envelope(
                progress,
                OUTCOME_APPLIED,
                ("YouTube player muted." if muted else "YouTube player unmuted.")
                + " This computer's speaker was not changed.",
            )

        return self._exclusive("youtube_set_mute", work)

    def _observed_muted(self) -> Optional[bool]:
        observation = self._service.channel.observation()
        return observation.muted if observation is not None else None

    # -- opening the player on purpose --------------------------------------

    def open_player(self) -> Dict[str, Any]:
        """The explicit "Open player on workstation" button.

        Same single-launch guarantee as Play now: if a player is already
        connected this opens nothing and says so, rather than adding a tab.
        """
        def work(progress: OperationProgress) -> Dict[str, Any]:
            if self._service.channel.connected():
                return self._envelope(
                    progress,
                    OUTCOME_APPLIED,
                    "The player is already open on the workstation.",
                )
            self._service.ensure_player(progress)
            return self._envelope(
                progress, OUTCOME_APPLIED, "The player is open on the workstation."
            )

        return self._exclusive("youtube_open_player", work)


def _whole_percent(value: object) -> int:
    """0–100 inclusive, as a whole number, or a refusal naming what was wrong.

    ``NaN`` is caught by the ``value != value`` identity rather than by a
    library call, and floats carrying a fraction are refused rather than
    truncated: 61.5 is not a volume this API accepts, and silently making it 61
    would be inventing the user's intent.
    """
    if isinstance(value, bool):
        raise InvalidVolume("true and false are not volumes")
    if isinstance(value, float):
        if value != value:
            raise InvalidVolume("not a number")
        if value in (float("inf"), float("-inf")):
            raise InvalidVolume("not a finite number")
        if value != int(value):
            raise InvalidVolume("whole numbers only")
        value = int(value)
    if not isinstance(value, int):
        raise InvalidVolume("a whole number from 0 to 100 is required")
    if value < 0:
        raise InvalidVolume("below the minimum of 0")
    if value > 100:
        raise InvalidVolume("above the maximum of 100")
    return value


__all__ = [
    "OUTCOMES",
    "OUTCOME_APPLIED",
    "OUTCOME_AUTOPLAY_BLOCKED",
    "OUTCOME_PARTIALLY_APPLIED",
    "OUTCOME_QUEUED",
    "SUCCESS_OUTCOMES",
    "YouTubeActionExecutor",
]
