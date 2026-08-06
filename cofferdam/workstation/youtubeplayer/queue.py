"""The Cofferdam-owned play queue. Not YouTube's, and deliberately not.

Why Cofferdam owns this
-----------------------

The official IFrame Player API does have queue-shaped functions —
``loadPlaylist``, ``cuePlaylist``, ``nextVideo``, ``previousVideo``,
``playVideoAt`` — and every one of them is documented **only** in terms of a
YouTube *playlist*. ``nextVideo()`` is "loads and plays the next video in the
playlist"; there is no documented behaviour when no playlist is loaded. Handing
YouTube an array of video ids would work, and would also hand YouTube the
ordering, the advance-on-end behaviour, and the loop/shuffle state — and the
thing this milestone must never do is let a recommendation become the next
video. A queue whose contents Cofferdam cannot enumerate is not a queue this
product can honestly report.

So the queue lives here, the player is only ever told to load **one specific
verified video id**, and Next is a Cofferdam decision that happens to be
implemented with ``loadVideoById``. Nothing about a YouTube playlist is
involved at any point.

Shape
-----

An ordered list with a cursor. ``index`` is the item currently loaded in the
player; items before it are what was played, items after it are what is coming.
That single list is what makes Previous meaningful without a second structure.

* **Play now** inserts immediately after the cursor and moves the cursor onto
  it, so the thing you were watching stays reachable with Previous.
* **Add to queue** appends to the end and moves nothing.
* **Next** / **Previous** move the cursor by one and refuse at the ends. They
  never wrap, and they never invent an item.

Bounds, and what happens at the bound
-------------------------------------

:data:`~.models.MAX_QUEUE_ITEMS` items, enforced on insert. *Add to queue* at
capacity is refused outright — the user asked for something the queue cannot
hold, and silently dropping one of their earlier choices to make room would be
Cofferdam editing a list the user built.

*Play now* at capacity is different, because refusing to play a video because of
a full queue would be an unrelated failure attached to the product's primary
action. It drops the **oldest already-played entry** — strictly before the
cursor — to make room, and refuses only when there is no played history to
reclaim. Nothing upcoming is ever discarded to make room for a Play now.

Lifetime
--------

In memory, and gone on restart. Same reasoning as the search sessions this queue
is fed from: a list of what someone lined up to watch is a record of their
evening, and holding it on disk buys nothing. There is no persistence in this
milestone, and the snapshot says the queue is empty after a restart rather than
claiming a continuity it does not have.
"""

from __future__ import annotations

import secrets
import threading
from typing import List, Optional, Tuple

from .errors import NoNextItem, NoPreviousItem, QueueFull, QueueItemUnknown
from .models import MAX_QUEUE_ITEMS, QueueItem, VideoMetadata

_ID_BYTES = 8


def _new_item_id() -> str:
    """Unguessable rather than sequential.

    A queue item id is a handle a client sends back to remove something. A
    counter would let a client name an item it was never shown, which is the
    same class of mistake as accepting a video id.
    """
    return "ytq-" + secrets.token_urlsafe(_ID_BYTES)


class PlayQueue:
    """Bounded, ordered, cursor-carrying, thread-safe. One per player service.

    Thread-safe because actions run in a worker thread pool and the loopback
    channel's own threads read the queue to answer a state request; two of those
    can land at once.
    """

    def __init__(self, limit: int = MAX_QUEUE_ITEMS) -> None:
        self._limit = limit
        self._items: List[QueueItem] = []
        self._index: Optional[int] = None
        self._lock = threading.RLock()

    # -- reading -------------------------------------------------------------

    @property
    def limit(self) -> int:
        return self._limit

    def snapshot(self) -> Tuple[Tuple[QueueItem, ...], Optional[int]]:
        """The items and the cursor, read together under one lock.

        Returned as a pair on purpose: an index read separately from the list it
        indexes can point at a different item by the time both are rendered.
        """
        with self._lock:
            return tuple(self._items), self._index

    def current(self) -> Optional[QueueItem]:
        with self._lock:
            if self._index is None or not (0 <= self._index < len(self._items)):
                return None
            return self._items[self._index]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    # -- writing -------------------------------------------------------------

    def play_now(self, video_id: str, metadata: VideoMetadata) -> QueueItem:
        """Insert after the cursor and select it. Returns the new current item.

        Reclaims one already-played entry at capacity; see the module docstring
        for why this differs from :meth:`add`.
        """
        with self._lock:
            if len(self._items) >= self._limit:
                self._reclaim_played()
            if len(self._items) >= self._limit:
                raise QueueFull(self._limit)
            item = QueueItem(
                queue_item_id=_new_item_id(), video_id=video_id, metadata=metadata
            )
            position = 0 if self._index is None else self._index + 1
            self._items.insert(position, item)
            self._index = position
            return item

    def _reclaim_played(self) -> None:
        """Drop the oldest entry before the cursor, if there is one.

        Only ever reached from :meth:`play_now`. The cursor moves back by one so
        it keeps pointing at the same item it pointed at before.
        """
        if self._index is None or self._index <= 0:
            return
        self._items.pop(0)
        self._index -= 1

    def add(self, video_id: str, metadata: VideoMetadata) -> QueueItem:
        """Append to the end. The cursor does not move, so nothing is interrupted."""
        with self._lock:
            if len(self._items) >= self._limit:
                raise QueueFull(self._limit)
            item = QueueItem(
                queue_item_id=_new_item_id(), video_id=video_id, metadata=metadata
            )
            self._items.append(item)
            return item

    def advance(self) -> QueueItem:
        """Move the cursor forward one and return that item, or refuse."""
        with self._lock:
            if self._index is None:
                # Nothing has played yet: "next" from a standing start is the
                # first queued item, which is what a user means by pressing it.
                if not self._items:
                    raise NoNextItem()
                self._index = 0
                return self._items[0]
            if self._index + 1 >= len(self._items):
                raise NoNextItem()
            self._index += 1
            return self._items[self._index]

    def retreat(self) -> QueueItem:
        """Move the cursor back one and return that item, or refuse."""
        with self._lock:
            if self._index is None or self._index <= 0:
                raise NoPreviousItem()
            self._index -= 1
            return self._items[self._index]

    def peek_next(self) -> Optional[QueueItem]:
        """The item Next would load, without moving anything.

        Used to answer "is automatic continuation possible" without committing
        to it — the answer must not change the queue.
        """
        with self._lock:
            if self._index is None:
                return self._items[0] if self._items else None
            following = self._index + 1
            return self._items[following] if following < len(self._items) else None

    def remove(self, queue_item_id: object) -> QueueItem:
        """Remove one item by its handle, keeping the cursor on the same item.

        Removing the *current* item leaves the cursor where it is, which now
        points at whatever followed. That is deliberate: the video already
        loaded in the player keeps playing, and the queue simply no longer lists
        it. Removing it does not stop playback, because the user asked to edit a
        list and not to stop a video.
        """
        if not isinstance(queue_item_id, str) or not queue_item_id:
            raise QueueItemUnknown()
        with self._lock:
            for position, item in enumerate(self._items):
                if item.queue_item_id != queue_item_id:
                    continue
                self._items.pop(position)
                if self._index is not None:
                    if position < self._index:
                        self._index -= 1
                    elif position == self._index and self._index >= len(self._items):
                        # The cursor fell off the end; park it on the last item,
                        # or nowhere if the queue is now empty.
                        self._index = len(self._items) - 1 if self._items else None
                return item
            raise QueueItemUnknown()

    def clear(self) -> int:
        """Empty the queue and forget the cursor. Returns how many were dropped.

        Does **not** stop the player. Clearing a list of what is coming next is
        not a request to stop what is playing, and doing both from one button
        would be one of those small inventions this codebase does not make.
        """
        with self._lock:
            dropped = len(self._items)
            self._items = []
            self._index = None
            return dropped


__all__ = ["MAX_QUEUE_ITEMS", "PlayQueue"]
