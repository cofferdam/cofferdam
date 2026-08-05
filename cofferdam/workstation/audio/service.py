"""One graph read, one audio snapshot — the orchestration the API and PWA read.

The shape mirrors :mod:`cofferdam.workstation.runtime.service` deliberately:
one collection pass behind a short lock, a very small cache that is really a
rate limit, and identity-based invalidation on top of time-based expiry.

The identity rule matters more here than it does for the runtime inventory. A
cached runtime snapshot that is a few seconds stale describes processes that
probably still exist. A cached *audio* snapshot taken before PipeWire restarted
describes node ids that have since been handed to different devices — so the
cache is dropped whenever the graph cookie changes, however fresh it is. Actions
never read the cache at all; see :mod:`cofferdam.workstation.audio.actions`.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..errors import AdapterError, AdapterUnsupported
from ..runtime.identity import detect_boot_identity, detect_host_identity, now_iso
from .discovery import (
    AudioOutputDiscovery,
    AudioStreamDiscovery,
    graph_identity_value,
    streams_unavailable,
)
from .models import (
    AUDIO_RESOURCE_KINDS,
    AudioSnapshot,
    CAPABILITY_SUPPORTED,
    CAPABILITY_UNAVAILABLE,
    Capability,
    Evidence,
    GraphIdentity,
    KIND_OUTPUTS,
    KIND_STREAMS,
    failed,
    unavailable,
)
from .wireplumber import BACKEND_NAME, WirePlumberBackend

# Short enough that "what is my volume" stays a true answer, long enough that a
# phone polling every couple of seconds does not re-dump the graph each time.
DEFAULT_CACHE_SECONDS = 2.0

CAPABILITY_SET_DEFAULT = "set_default_audio_output"
CAPABILITY_SET_VOLUME = "set_output_volume"
CAPABILITY_SET_MUTE = "set_output_mute"
CAPABILITY_MOVE_STREAM = "move_audio_stream"

# Why the optional fourth action is not offered. This text is published to the
# client so the UI can explain the absence rather than just omit a button.
MOVE_STREAM_UNAVAILABLE_REASON = (
    "this host's WirePlumber offers no command that moves a playing stream to another output. "
    "Doing it by hand would mean writing PipeWire metadata addressed by a stream's transient "
    "node id, and WirePlumber would then remember that target and pin the application to that "
    "output for future sessions — a lasting change nobody asked for. Choosing a different "
    "default output is offered instead."
)


def _monotonic() -> float:
    return time.monotonic()


class AudioInventoryService:
    """Collects one :class:`AudioSnapshot` per scan, with a short cache."""

    def __init__(
        self,
        backend: Optional[WirePlumberBackend] = None,
        adapter=None,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        clock=_monotonic,
        stream_discovery: Optional[AudioStreamDiscovery] = None,
    ) -> None:
        self._backend = backend or WirePlumberBackend()
        self._adapter = adapter
        self._cache_seconds = max(0.0, cache_seconds)
        self._clock = clock
        self._stream_discovery = stream_discovery

        self._lock = threading.Lock()
        self._cached: Optional[AudioSnapshot] = None
        self._cached_at: float = 0.0

    # -- public API ----------------------------------------------------------

    @property
    def backend(self) -> WirePlumberBackend:
        """The backend this service reads through.

        Exposed so the action executor acts through the *same* process boundary
        the snapshot was taken through, rather than constructing a second one
        that could be configured differently.
        """
        return self._backend

    def snapshot(self, refresh: bool = False) -> AudioSnapshot:
        with self._lock:
            if not refresh:
                cached = self._usable_cached()
                if cached is not None:
                    return cached
            snapshot = self._collect()
            self._cached = snapshot
            self._cached_at = self._clock()
            return snapshot

    def collection(self, kind: str, refresh: bool = False):
        """One collection plus the snapshot header it belongs to."""
        if kind not in AUDIO_RESOURCE_KINDS:  # pragma: no cover - callers validate first
            raise KeyError(kind)
        snapshot = self.snapshot(refresh=refresh)
        return snapshot, snapshot.collection(kind)

    def invalidate(self) -> None:
        """Drop the cache. Called after any action changes the host's state."""
        with self._lock:
            self._cached = None
            self._cached_at = 0.0

    # -- collection ----------------------------------------------------------

    def _usable_cached(self) -> Optional[AudioSnapshot]:
        if self._cached is None:
            return None
        if self._clock() - self._cached_at >= self._cache_seconds:
            return None
        return self._cached

    def _collect(self) -> AudioSnapshot:
        host = detect_host_identity()
        boot = detect_boot_identity()

        if getattr(self._adapter, "stub", False):
            # A stub adapter means no real host is being controlled. Publishing
            # this machine's actual speakers under it would be worse than
            # publishing nothing: real hardware presented as belonging to a host
            # the rest of the UI calls simulated.
            return self._nothing_observed(
                host,
                boot,
                "the stub adapter is active: no real host is being observed, so this machine's "
                "audio cannot be reported",
            )

        try:
            graph = self._backend.read_graph()
        except AdapterUnsupported as exc:
            return self._nothing_observed(host, boot, exc.message)
        except AdapterError as exc:
            return self._nothing_observed(host, boot, exc.message, kind="error")

        if graph.cookie is None:
            # Without a cookie there is no way to notice a graph restart, so
            # every resource id would risk outliving the objects it names.
            return self._nothing_observed(
                host,
                boot,
                "this host's audio server published no graph identity, so audio resources "
                "cannot be addressed safely",
            )

        graph_id = graph_identity_value(host.host_id, graph.cookie)
        identity = GraphIdentity(
            available=True,
            graph_id=graph_id,
            backend=BACKEND_NAME,
            server_version=graph.server_version,
            session_manager=None,
        )

        warnings: List[str] = list(graph.warnings)

        outputs, output_warnings = AudioOutputDiscovery(self._backend.read_volume).collect(
            host.host_id, graph_id, graph
        )
        warnings.extend(w for w in output_warnings if w not in warnings)

        output_id_by_node = {
            item["node_id"]: item["resource_id"]
            for item in outputs.items
            if isinstance(item.get("node_id"), int)
        }

        default_resource_id: Optional[str] = None
        for item in outputs.items:
            if item.get("is_default"):
                default_resource_id = item.get("resource_id")
                break
        if default_resource_id is None and outputs.items:
            warnings.append(
                "this host has audio outputs but reports no default one, so new sound may go "
                "somewhere unexpected"
            )

        streams = self._collect_streams(host.host_id, graph_id, graph, boot, output_id_by_node)

        return AudioSnapshot(
            observed_at=now_iso(),
            host=host.to_dict(),
            boot=boot.to_dict(),
            graph=identity.to_dict(),
            backend=BACKEND_NAME,
            collections={KIND_OUTPUTS: outputs, KIND_STREAMS: streams},
            default_output_resource_id=default_resource_id,
            capabilities=self._capabilities(),
            warnings=tuple(warnings),
        )

    def _collect_streams(self, host_id, graph_id, graph, boot, output_id_by_node):
        """Streams, or an honest ``unavailable`` when they cannot be identified.

        A host with no boot identity cannot form a process identity, but it can
        still see that *something* is playing. That is reported rather than
        withheld — the association simply stays unclassified, which the stream
        discovery already handles.
        """
        discovery = self._stream_discovery or AudioStreamDiscovery(self._application_executables())
        try:
            return discovery.collect(host_id, graph_id, graph, boot, output_id_by_node)
        except Exception:
            # A fault in stream association must not cost the user their volume
            # control. Outputs are the primary capability; streams degrade.
            return streams_unavailable(
                "this host's playback streams could not be examined, so nothing is claimed "
                "about what is currently playing"
            )

    def _capabilities(self) -> Tuple[Capability, ...]:
        return (
            Capability(CAPABILITY_SET_DEFAULT, CAPABILITY_SUPPORTED),
            Capability(CAPABILITY_SET_VOLUME, CAPABILITY_SUPPORTED),
            Capability(CAPABILITY_SET_MUTE, CAPABILITY_SUPPORTED),
            Capability(
                CAPABILITY_MOVE_STREAM,
                CAPABILITY_UNAVAILABLE,
                MOVE_STREAM_UNAVAILABLE_REASON,
            ),
        )

    def _nothing_observed(self, host, boot, reason: str, kind: str = "unavailable") -> AudioSnapshot:
        """Every collection empty *and* labelled, never empty and ``ok``.

        This is the distinction the whole status vocabulary exists for: "there
        are no outputs" and "this host cannot be asked about outputs" must not
        render as the same silent empty list.
        """
        make = failed if kind == "error" else unavailable
        evidence = Evidence(backend=BACKEND_NAME)
        return AudioSnapshot(
            observed_at=now_iso(),
            host=host.to_dict(),
            boot=boot.to_dict(),
            graph=GraphIdentity(available=False, reason=reason).to_dict(),
            backend=BACKEND_NAME,
            collections={
                KIND_OUTPUTS: make(KIND_OUTPUTS, reason, evidence),
                KIND_STREAMS: make(KIND_STREAMS, reason, evidence),
            },
            default_output_resource_id=None,
            capabilities=(
                Capability(CAPABILITY_SET_DEFAULT, CAPABILITY_UNAVAILABLE, reason),
                Capability(CAPABILITY_SET_VOLUME, CAPABILITY_UNAVAILABLE, reason),
                Capability(CAPABILITY_SET_MUTE, CAPABILITY_UNAVAILABLE, reason),
                Capability(CAPABILITY_MOVE_STREAM, CAPABILITY_UNAVAILABLE, reason),
            ),
            warnings=(reason,),
        )

    # -- injected knowledge --------------------------------------------------

    def _application_executables(self) -> Mapping[str, Any]:
        """The adapter's launch table, or nothing.

        Nothing is a valid answer: every stream is then unclassified, which is
        the honest degradation rather than a reason to fall back to matching
        application names.
        """
        if self._adapter is None:
            return {}
        try:
            return dict(self._adapter.application_executables() or {})
        except Exception:
            return {}


__all__ = [
    "AudioInventoryService",
    "CAPABILITY_MOVE_STREAM",
    "CAPABILITY_SET_DEFAULT",
    "CAPABILITY_SET_MUTE",
    "CAPABILITY_SET_VOLUME",
    "DEFAULT_CACHE_SECONDS",
    "MOVE_STREAM_UNAVAILABLE_REASON",
]
