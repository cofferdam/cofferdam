"""Refusals at the YouTube dedicated-player boundary.

Every code below names a state Cofferdam can *justify* from something it
observed — a heartbeat that stopped, a player that reported an error code, a
queue that is full. None of them is inferred from "we sent the command, so it
must have worked".

Two groups are worth reading together.

**Lifecycle codes** describe the player *tab*: there is none, one is starting,
one was asked for and never registered, one went away. These are the states
that made the old behaviour (a new Opera tab per video) look like it worked —
a tab always appeared, so nothing ever had to report that playback had not.

**Observation codes** describe a command that was delivered and did not produce
the state it asked for. They exist because the product's rule is that a
requested value is never echoed as an observed one: a volume that was set and
never confirmed is ``youtube_volume_not_observed``, not success.

``autoplay_blocked`` is deliberately **not** an error class here. The browser
refusing to start audio without a gesture is a normal, documented outcome of a
correct command — the video is loaded and cued, and one click on the workstation
resolves it. It travels as a player *state*, so the phone can render the
instruction, rather than as a failure that would suggest something is broken.
"""

from __future__ import annotations

from typing import Optional, Sequence

# -- lifecycle ---------------------------------------------------------------

CODE_UNAVAILABLE = "youtube_player_unavailable"
CODE_NO_PLAYER = "youtube_player_not_connected"
CODE_LAUNCH_FAILED = "youtube_player_launch_failed"
CODE_REGISTRATION_TIMEOUT = "youtube_player_registration_timeout"
CODE_PLAYER_GONE = "youtube_player_gone"

# -- authority ---------------------------------------------------------------

CODE_RESULT_NOT_PLAYABLE = "youtube_result_not_playable"
CODE_WRONG_PROVIDER = "youtube_result_wrong_provider"

# -- queue -------------------------------------------------------------------

CODE_QUEUE_FULL = "youtube_queue_full"
CODE_QUEUE_EMPTY = "youtube_queue_empty"
CODE_QUEUE_ITEM_UNKNOWN = "youtube_queue_item_unknown"
CODE_NO_NEXT_ITEM = "youtube_queue_no_next_item"
CODE_NO_PREVIOUS_ITEM = "youtube_queue_no_previous_item"

# -- commands and observation ------------------------------------------------

CODE_INVALID_VOLUME = "youtube_volume_invalid"
CODE_INVALID_MUTE = "youtube_mute_invalid"
CODE_COMMAND_NOT_ACKNOWLEDGED = "youtube_command_not_acknowledged"
CODE_VIDEO_NOT_OBSERVED = "youtube_video_not_observed"
CODE_VOLUME_NOT_OBSERVED = "youtube_volume_not_observed"
CODE_MUTE_NOT_OBSERVED = "youtube_mute_not_observed"
CODE_TRANSPORT_NOT_OBSERVED = "youtube_transport_not_observed"
CODE_BUSY = "youtube_player_busy"

# -- what YouTube itself refused ---------------------------------------------
#
# Mapped from the documented ``onError`` codes. The player page forwards the
# numeric code from a closed set and nothing else; the sentences are written
# here, in Cofferdam's own words, because provider text is not something to put
# on a phone screen.

CODE_VIDEO_UNAVAILABLE = "youtube_video_unavailable"
CODE_EMBEDDING_REFUSED = "youtube_embedding_refused"
CODE_PLAYER_ERROR = "youtube_player_error"


class YouTubePlayerError(Exception):
    """A refusal a person should see, with a stable code to branch on.

    Deliberately not an HTTP concern — the service layer maps codes to statuses,
    the same shape the audio, overlay and Spotify write paths already use.
    """

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class PlayerUnavailable(YouTubePlayerError):
    """This host cannot run a dedicated player at all."""

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_UNAVAILABLE,
            "the YouTube player is not available on this host",
            detail or "a browser Cofferdam can launch is required",
        )


class NoPlayerConnected(YouTubePlayerError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_NO_PLAYER,
            "no Cofferdam YouTube player is open",
            detail or "open the player on the workstation, then try again",
        )


class LaunchFailed(YouTubePlayerError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_LAUNCH_FAILED,
            "the YouTube player could not be opened on the workstation",
            detail or "Opera could not be launched from this session",
        )


class RegistrationTimeout(YouTubePlayerError):
    """Opera was launched and no player reported in inside the window.

    Truthful rather than optimistic: the tab may well be opening. Saying so and
    letting the user press Play now again is better than reporting a success
    that has not happened, and better than launching a second tab to chase it.
    """

    def __init__(self, seconds: int) -> None:
        super().__init__(
            CODE_REGISTRATION_TIMEOUT,
            "the YouTube player did not finish opening in time",
            "waited " + str(seconds) + "s for the player tab to report in; "
            "if it has now opened on the workstation, press Play now again",
        )


class PlayerGone(YouTubePlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_PLAYER_GONE,
            "the YouTube player tab closed",
            "press Play now again to open it once more",
        )


class ResultNotPlayable(YouTubePlayerError):
    """The chosen search result is not a video this player can load."""

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_RESULT_NOT_PLAYABLE,
            "that result cannot be played in the YouTube player",
            detail or "only YouTube video results can be played here",
        )


class WrongProvider(YouTubePlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_WRONG_PROVIDER,
            "that result did not come from YouTube",
            "a result can only be played through the provider that produced it",
        )


class QueueFull(YouTubePlayerError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            CODE_QUEUE_FULL,
            "the YouTube queue is full",
            "the queue holds " + str(limit) + " videos; remove one or clear it",
        )


class QueueItemUnknown(YouTubePlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_QUEUE_ITEM_UNKNOWN,
            "that queued video is no longer in the queue",
            "the queue may have changed; refresh the player",
        )


class NoNextItem(YouTubePlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_NO_NEXT_ITEM,
            "there is nothing after this in the Cofferdam queue",
            "add a video to the queue first — Cofferdam never picks a YouTube "
            "recommendation for you",
        )


class NoPreviousItem(YouTubePlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_NO_PREVIOUS_ITEM,
            "there is nothing before this in the Cofferdam queue",
            "this is the first video in the queue",
        )


class InvalidVolume(YouTubePlayerError):
    """Out of range, wrong type, or not a number at all.

    Not clamped. A request for 150 is not a request for 100, and quietly
    treating it as one would teach a client that the range is advisory.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_INVALID_VOLUME,
            "the YouTube player volume must be a whole number from 0 to 100",
            detail,
        )


class InvalidMute(YouTubePlayerError):
    def __init__(self) -> None:
        super().__init__(
            CODE_INVALID_MUTE, "muted must be true or false", None
        )


class CommandNotAcknowledged(YouTubePlayerError):
    """Delivered to the channel, never acknowledged by the player.

    Distinct from "the player is gone": the tab may be alive and wedged behind a
    frozen iframe. The distinction matters because the answers differ — one is
    "press again to reopen", the other is "reload the player tab".
    """

    def __init__(self, seconds: int) -> None:
        super().__init__(
            CODE_COMMAND_NOT_ACKNOWLEDGED,
            "the YouTube player did not answer that command",
            "waited " + str(seconds) + "s; the player tab may need reloading",
        )


class VideoNotObserved(YouTubePlayerError):
    """The command went through and the player is showing something else.

    The most important refusal in this module. Reporting success here would be
    the exact failure the milestone exists to prevent: telling someone their
    video is playing while a different one is on screen.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_VIDEO_NOT_OBSERVED,
            "the player did not confirm it loaded the video you chose",
            detail or "nothing was reported as playing; try again",
        )


class VolumeNotObserved(YouTubePlayerError):
    def __init__(self, observed: Optional[int]) -> None:
        super().__init__(
            CODE_VOLUME_NOT_OBSERVED,
            "the player did not confirm the new volume",
            "the player still reports "
            + (str(observed) + "%" if observed is not None else "no volume")
            + "; try again",
        )


class MuteNotObserved(YouTubePlayerError):
    def __init__(self, wanted: bool) -> None:
        super().__init__(
            CODE_MUTE_NOT_OBSERVED,
            "the player did not confirm " + ("mute" if wanted else "unmute"),
            "the player still reports the previous state; try again",
        )


class TransportNotObserved(YouTubePlayerError):
    def __init__(self, operation: str) -> None:
        super().__init__(
            CODE_TRANSPORT_NOT_OBSERVED,
            "the player did not confirm " + operation,
            "the command was delivered and the player state did not change",
        )


class PlayerBusy(YouTubePlayerError):
    """Another write is already in flight for this player.

    One player, one command at a time. Two Play now presses that raced would
    load two videos and confirm neither, so the second is refused rather than
    queued — a refusal a client can retry is safer than a queue a user cannot see.
    """

    def __init__(self) -> None:
        super().__init__(
            CODE_BUSY,
            "the YouTube player is already handling another command",
            "wait for the current action to finish, then try again",
        )


# -- YouTube's own documented error codes ------------------------------------
#
# From the official IFrame Player API ``onError`` reference. The player page
# forwards the number; the mapping and every sentence live here.

ERROR_INVALID_PARAMETER = 2
ERROR_HTML5 = 5
ERROR_NOT_FOUND = 100
ERROR_NOT_EMBEDDABLE = 101
ERROR_NOT_EMBEDDABLE_ALT = 150

#: The complete set the player page is allowed to forward. Anything else is
#: dropped at the channel rather than carried into a snapshot.
PLAYER_ERROR_CODES: Sequence[int] = (
    ERROR_INVALID_PARAMETER,
    ERROR_HTML5,
    ERROR_NOT_FOUND,
    ERROR_NOT_EMBEDDABLE,
    ERROR_NOT_EMBEDDABLE_ALT,
)


def describe_player_error(code: object) -> Optional[dict]:
    """One documented ``onError`` code as a bounded, Cofferdam-worded state.

    Returns ``None`` for anything outside the documented set, so an unexpected
    number becomes "no error reported" rather than an unbounded value rendered
    on a phone.
    """
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    if code not in PLAYER_ERROR_CODES:
        return None
    if code == ERROR_NOT_FOUND:
        return {
            "code": CODE_VIDEO_UNAVAILABLE,
            "message": "that video is unavailable",
            "detail": "it may have been removed or made private",
        }
    if code in (ERROR_NOT_EMBEDDABLE, ERROR_NOT_EMBEDDABLE_ALT):
        return {
            "code": CODE_EMBEDDING_REFUSED,
            "message": "the video's owner does not allow it to play in an embedded player",
            "detail": "use Open in YouTube to watch it on the normal page",
        }
    if code == ERROR_HTML5:
        return {
            "code": CODE_PLAYER_ERROR,
            "message": "that video could not be played in this player",
            "detail": "use Open in YouTube to watch it on the normal page",
        }
    return {
        "code": CODE_PLAYER_ERROR,
        "message": "the player refused that video",
        "detail": "use Open in YouTube to watch it on the normal page",
    }


__all__ = [
    "CODE_BUSY",
    "CODE_COMMAND_NOT_ACKNOWLEDGED",
    "CODE_EMBEDDING_REFUSED",
    "CODE_INVALID_MUTE",
    "CODE_INVALID_VOLUME",
    "CODE_LAUNCH_FAILED",
    "CODE_MUTE_NOT_OBSERVED",
    "CODE_NO_NEXT_ITEM",
    "CODE_NO_PLAYER",
    "CODE_NO_PREVIOUS_ITEM",
    "CODE_PLAYER_ERROR",
    "CODE_PLAYER_GONE",
    "CODE_QUEUE_EMPTY",
    "CODE_QUEUE_FULL",
    "CODE_QUEUE_ITEM_UNKNOWN",
    "CODE_REGISTRATION_TIMEOUT",
    "CODE_RESULT_NOT_PLAYABLE",
    "CODE_TRANSPORT_NOT_OBSERVED",
    "CODE_UNAVAILABLE",
    "CODE_VIDEO_NOT_OBSERVED",
    "CODE_VIDEO_UNAVAILABLE",
    "CODE_VOLUME_NOT_OBSERVED",
    "CODE_WRONG_PROVIDER",
    "PLAYER_ERROR_CODES",
    "CommandNotAcknowledged",
    "InvalidMute",
    "InvalidVolume",
    "LaunchFailed",
    "MuteNotObserved",
    "NoNextItem",
    "NoPlayerConnected",
    "NoPreviousItem",
    "PlayerBusy",
    "PlayerGone",
    "PlayerUnavailable",
    "QueueFull",
    "QueueItemUnknown",
    "RegistrationTimeout",
    "ResultNotPlayable",
    "TransportNotObserved",
    "VideoNotObserved",
    "VolumeNotObserved",
    "WrongProvider",
    "YouTubePlayerError",
    "describe_player_error",
]
