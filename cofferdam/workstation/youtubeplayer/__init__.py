"""The YouTube dedicated player (M2E).

One persistent, Cofferdam-owned player document in Opera on the workstation,
driven from the phone through typed actions. It replaces the old behaviour where
selecting a YouTube search result opened a new watch tab every time — *Open in
YouTube* still does exactly that, explicitly, and stays the user's choice.

Reading order, if you are new to this package:

``errors``     the refusals, and why ``autoplay_blocked`` is not one of them
``models``     the published shapes, the state vocabularies, and the bounds
``queue``      the Cofferdam-owned queue, and why YouTube's is not used
``channel``    the closed message vocabulary and the heartbeat that defines
               "connected"
``endpoint``   the loopback-only listener, and what its trust boundary is worth
``launcher``   one bounded Opera launch through the existing allowlisted path
``service``    the lifecycle: one launch, bounded waits, honest snapshots
``actions``    the typed operations, each of which reports what it observed

The two properties worth stating up front, because everything else follows
from them:

* **A player is a document that is currently saying so.** Connection state comes
  from a heartbeat, never from Opera's process list. A running browser is not a
  connected player and is never reported as one.
* **No client names a video.** A video reaches the player from a verified search
  session or from the Cofferdam queue. There is no request field anywhere in
  this package for a URL, a video id, a player command or a script.
"""

from __future__ import annotations

from .actions import YouTubeActionExecutor
from .channel import PlayerChannel
from .endpoint import LOOPBACK_HOST, PlayerEndpoint
from .errors import YouTubePlayerError
from .launcher import PlayerLauncher
from .models import MAX_QUEUE_ITEMS, YOUTUBE_PLAYER_VERSION, PlayerSnapshot
from .progress import ActivityRecorder
from .queue import PlayQueue
from .service import PlayerService

__all__ = [
    "LOOPBACK_HOST",
    "MAX_QUEUE_ITEMS",
    "YOUTUBE_PLAYER_VERSION",
    "ActivityRecorder",
    "PlayQueue",
    "PlayerChannel",
    "PlayerEndpoint",
    "PlayerLauncher",
    "PlayerService",
    "PlayerSnapshot",
    "YouTubeActionExecutor",
    "YouTubePlayerError",
]
