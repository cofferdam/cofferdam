"""Getting Spotify to exist, when the user pressed Play and it does not.

Real validation on the phone found the gap this module fills. With the Spotify
desktop application closed, Play now reported *"Spotify has no active device"* and
stopped. That is a true statement about the device list and a useless product:
the machine the phone is controlling has Spotify installed, and Cofferdam already
knows how to launch it — the Media panel does exactly that.

A second, quieter case was found beside it. With Spotify **open but idle**, the
device is present and `is_active` is false, and the old code refused that too.
That is why "Open in Spotify, then Play now" worked: opening the app made the
device active. The workaround was the diagnosis.

What recovery is allowed to do
------------------------------
Recovery escalates in one direction and never loops:

1. an **active** controllable device — use it, nothing else happens;
2. exactly **one** eligible device that is merely inactive — adopt it, and
   transfer to it if the action needs it playing;
3. **several** eligible devices and none active — **ask**. Picking the first of
   three speakers because it sorts first would start music in a room nobody
   named, and there is no evidence in a device list about which room somebody is
   standing in;
4. **none at all** — launch Spotify, once, through the same allowlisted
   application launcher the Media panel uses, then wait a bounded time for a
   device to register.

**One launch attempt, one transfer attempt, bounded polling, no retry loop.** The
launcher is called at most once per recovery, and a recovery that ends without a
device says so rather than starting again.

What it deliberately does not do
--------------------------------
* **No shell.** `adapter.open_application("spotify")` is the existing fixed-argv,
  allowlisted path; this module builds no command line of its own.
* **No search page as a substitute.** Opening a web page is not launching a
  player, and reporting it as recovery would be a false success.
* **No matching by device name.** Handles are opaque and resolved against a
  freshly read list, exactly as everywhere else in this milestone — a device that
  appears after a launch is not trusted because it is called "Workstation".
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Tuple

from .confirm import ACTIVATION_CONFIRM, DEVICE_APPEARANCE, ConfirmWindow, confirm
from .errors import (
    DeviceAmbiguous,
    DeviceRestricted,
    LaunchFailed,
    NoActiveDevice,
    NoDeviceAfterLaunch,
)
from .models import PlaybackSnapshot, SpotifyDevice
from .progress import (
    PHASE_ACTIVATING,
    PHASE_LAUNCHING,
    PHASE_WAITING_FOR_DEVICE,
    OperationProgress,
)

# The logical application key, not a program name. The adapter owns the mapping
# from this to an executable, and refuses anything not on its allowlist.
SPOTIFY_APPLICATION_KEY = "spotify"


class SpotifyLauncher:
    """One bounded launch of the installed Spotify desktop application.

    A thin wrapper, and the thinness is the point: it exists so the playback code
    has no reason to reach for a subprocess, and so "did we already launch?" is a
    question with one owner.
    """

    def __init__(self, adapter, application_key: str = SPOTIFY_APPLICATION_KEY) -> None:
        self._adapter = adapter
        self._key = application_key

    def launch(self) -> None:
        """Launch, or raise a refusal a person can act on.

        An adapter that reports the application is not installed, or that this
        host cannot launch anything, is not a bug to be retried — it is a fact,
        and the message says what to do instead.
        """
        try:
            self._adapter.open_application(self._key)
        except Exception as exc:  # adapter refusals are already bounded and safe
            detail = getattr(exc, "detail", None) or getattr(exc, "message", None)
            raise LaunchFailed(detail if isinstance(detail, str) else None) from None


def eligible_devices(snapshot: PlaybackSnapshot) -> List[SpotifyDevice]:
    """Devices Spotify will actually accept commands for.

    ``is_restricted`` is documented as "no Web API commands will be accepted by
    this device", so a restricted device is not a candidate for anything —
    including for being the single unambiguous one.
    """
    return [device for device in snapshot.devices if device.controllable]


class DeviceRecovery:
    """Resolves *something to play on*, launching Spotify if it has to."""

    def __init__(
        self,
        service,
        launcher: Optional[SpotifyLauncher] = None,
        sleeper: Callable[[float], None] = time.sleep,
        appearance_window: ConfirmWindow = DEVICE_APPEARANCE,
        activation_window: ConfirmWindow = ACTIVATION_CONFIRM,
    ) -> None:
        self._service = service
        self._launcher = launcher
        self._sleeper = sleeper
        self._appearance = appearance_window
        self._activation = activation_window

    # -- choosing --------------------------------------------------------

    def _choose(self, snapshot: PlaybackSnapshot) -> Optional[SpotifyDevice]:
        """The unambiguous target in this snapshot, or ``None``.

        Active wins outright: if Spotify is already playing somewhere, that is
        where the user is listening, and no other device is a candidate.
        """
        active = snapshot.active_device()
        if active is not None:
            if not active.controllable:
                raise DeviceRestricted()
            return active
        candidates = eligible_devices(snapshot)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise DeviceAmbiguous([device.name for device in candidates])
        return None

    def resolve(
        self,
        snapshot: PlaybackSnapshot,
        resource_id: object,
        progress: OperationProgress,
        *,
        allow_launch: bool = True,
    ) -> Tuple[SpotifyDevice, PlaybackSnapshot]:
        """A device to act on, and the snapshot it was resolved against.

        An explicitly chosen device short-circuits everything: the user named it,
        so it is resolved through the ordinary opaque-handle path and a handle
        that no longer resolves is still refused. Recovery only decides when
        nobody decided.
        """
        if resource_id:
            return self._service.resolve_device(snapshot, resource_id), snapshot

        chosen = self._choose(snapshot)
        if chosen is not None:
            return chosen, snapshot

        if not allow_launch or self._launcher is None:
            raise NoActiveDevice()

        # Nothing to play on. Launch once, then wait a bounded time.
        progress.enter(PHASE_LAUNCHING)
        self._launcher.launch()

        progress.enter(PHASE_WAITING_FOR_DEVICE)
        devices, appeared = confirm(
            self._service.observe_devices,
            lambda found: any(device.controllable for device in found),
            self._appearance,
            self._sleeper,
        )
        if not appeared:
            raise NoDeviceAfterLaunch(int(self._appearance.timeout_seconds))

        # Re-resolve through a full snapshot rather than trusting the cheap read:
        # the handle, the capabilities and the active flag all come from one
        # place, and that place is the same one every other action reads.
        snapshot = self._service.snapshot(refresh=True)
        chosen = self._choose(snapshot)
        if chosen is None:
            raise NoDeviceAfterLaunch(int(self._appearance.timeout_seconds))
        return chosen, snapshot

    # -- activating ------------------------------------------------------

    def activate(
        self, device: SpotifyDevice, progress: OperationProgress
    ) -> Tuple[SpotifyDevice, PlaybackSnapshot]:
        """Make ``device`` the one Spotify is on, if it is not already.

        One transfer attempt, then a bounded wait for the device list to agree.
        Transferring with ``play=False`` on purpose: this is "put Spotify here",
        and starting whatever happened to be loaded would be an action nobody
        asked for. The track the user chose is started by the caller, afterwards.
        """
        if device.is_active:
            return device, self._service.snapshot(refresh=True)

        progress.enter(PHASE_ACTIVATING)
        tokens = self._service.authorized_tokens()
        self._service.client.transfer(tokens, device.provider_device_id, play=False)

        def read() -> Tuple[SpotifyDevice, ...]:
            return self._service.observe_devices()

        def is_active(found) -> bool:
            for candidate in found:
                if candidate.resource_id == device.resource_id:
                    return candidate.is_active
            return False

        confirm(read, is_active, self._activation, self._sleeper)

        # Whatever the transfer did, the snapshot is the authority on what is
        # true now — including "it did not take", which the caller reports.
        snapshot = self._service.snapshot(refresh=True)
        current = snapshot.device_by_resource_id(device.resource_id)
        return (current or device), snapshot


__all__ = [
    "DeviceRecovery",
    "SPOTIFY_APPLICATION_KEY",
    "SpotifyLauncher",
    "eligible_devices",
]
