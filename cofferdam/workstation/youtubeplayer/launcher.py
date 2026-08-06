"""Opening the dedicated player, exactly once, through the existing safe path.

A thin wrapper, and the thinness is the point — the same reasoning that shaped
:class:`~..spotifyplayer.coldstart.SpotifyLauncher`. It exists so the playback
code has no reason to reach for a subprocess, and so "have we already launched?"
is a question with exactly one owner.

What it does
------------

Calls ``adapter.open_url(player_url, application="opera")``: the existing
allowlisted, fixed-argv browser launch that the Media panel's Open button
already uses. Passing a URL to an already-running Opera hands it to that
instance, which opens one tab in the user's ordinary session — this never starts
a second isolated browser, never touches a profile directory, and never reads
one.

What it deliberately does not do
--------------------------------

* **No shell**, and no command line built here.
* **No arbitrary URL.** The only URL it can be given is the one
  :meth:`~.endpoint.PlayerEndpoint.player_url` constructs from a module constant
  and a bound port. Nothing from a request reaches it.
* **No retry.** One call, one outcome. A launch that did not produce a player is
  reported by the caller as a timeout, not chased with a second tab — chasing is
  how the old behaviour ended up with a tab per video.
* **No claim of success.** Returning from :meth:`launch` means Opera was asked.
  Whether a *player* exists is answered by the heartbeat, and only by that.
"""

from __future__ import annotations

from typing import Optional

from ..media import DEFAULT_BROWSER_KEY
from .errors import LaunchFailed, PlayerUnavailable


class PlayerLauncher:
    """One bounded launch of the player document in the product's browser."""

    def __init__(self, adapter, browser_key: str = DEFAULT_BROWSER_KEY) -> None:
        self._adapter = adapter
        self._browser_key = browser_key

    def available(self) -> bool:
        """Whether this host could open a player at all.

        Asked before anything is attempted so the PWA can disable the control
        with a reason, rather than offering a button whose only outcome is a
        failure. A host with no Opera is a fact about the host, not an error.
        """
        try:
            applications = self._adapter.available_applications()
        except Exception:  # pragma: no cover - an adapter that cannot answer
            return False
        return self._browser_key in (applications or ())

    def launch(self, player_url: str) -> Optional[int]:
        """Open the player document. Returns the launch pid, if the adapter has one.

        The pid is returned for diagnostics only and is **never** used as player
        identity — see :mod:`.channel` for why a process id is the wrong answer
        to "is a player connected".
        """
        if not self.available():
            raise PlayerUnavailable(
                self._browser_key + " is not installed on this host, or cannot be "
                "launched from this session"
            )
        try:
            launch = self._adapter.open_url(player_url, application=self._browser_key)
        except Exception as exc:  # adapter refusals are already bounded and safe
            detail = getattr(exc, "detail", None) or getattr(exc, "message", None)
            raise LaunchFailed(detail if isinstance(detail, str) else None) from None
        return getattr(launch, "pid", None)


__all__ = ["PlayerLauncher"]
