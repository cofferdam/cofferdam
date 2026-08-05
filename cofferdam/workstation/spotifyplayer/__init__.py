"""Controlling the user's own Spotify playback, with their explicit consent.

Separate from :mod:`cofferdam.workstation.mediasearch` on purpose. That package
holds an *application* credential and can only read a public catalogue; this one
holds a *user* authorization and can change what somebody is listening to. Two
different kinds of power, two different modules, two different secret files.

It is equally separate from :mod:`cofferdam.workstation.audio`. That is this
computer's speaker — PipeWire, a system volume, a physical output. This is
Spotify's own player and its Connect devices. Transferring Spotify to a kitchen
speaker does not change this machine's audio output, and turning this machine
down does not change Spotify's level. The product keeps them in two panels and
the code keeps them in two packages, because a user with two sliders labelled
"volume" cannot tell which one is at fault when the room goes quiet.

Layering, outermost first:

``authorize.AuthorizationRunner``
    One PKCE attempt: open Opera on the workstation, listen on loopback,
    exchange once.
``actions.SpotifyActionExecutor``
    The typed actions. Act, re-read, report what was observed.
``service.SpotifyPlayerService``
    Connection status, playback snapshots, device resolution.
``client.SpotifyPlayerClient``
    The only thing that talks to Spotify, over the existing bounded transport.
``tokens`` / ``mutestate``
    What is written down, and nothing more.
"""

from .actions import SpotifyActionExecutor
from .authorize import AuthorizationRunner
from .errors import CONNECTION_STATUSES, SpotifyPlayerError
from .models import SPOTIFY_PLAYBACK_VERSION, PlaybackSnapshot
from .oauth import REDIRECT_URI, REQUIRED_SCOPES
from .service import SpotifyPlayerService
from .tokens import TokenStore

__all__ = [
    "AuthorizationRunner",
    "CONNECTION_STATUSES",
    "PlaybackSnapshot",
    "REDIRECT_URI",
    "REQUIRED_SCOPES",
    "SPOTIFY_PLAYBACK_VERSION",
    "SpotifyActionExecutor",
    "SpotifyPlayerError",
    "SpotifyPlayerService",
    "TokenStore",
]
