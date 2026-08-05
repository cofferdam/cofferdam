"""Typed audio actions: resolve, verify, act, then look again.

Every action here follows the same five steps, and the order is the point:

1. **Refresh.** Take a new snapshot. A queued or retried request must never act
   on the picture the client was holding.
2. **Resolve.** Turn the submitted ``resource_id`` into a live object. The id is
   the *only* thing a client may send to name a device. There is no fallback to
   a display name, a node id, or a nearest match — a resource that is gone is
   gone, and saying so is the correct outcome.
3. **Re-verify.** Read the graph once more, immediately before acting, and
   confirm the node id still carries the same node name and the same PipeWire
   serial. This is what closes the window between "resolved" and "acted", and it
   is the check that makes a *reused* node id harmless: node 58 being present is
   not enough, node 58 must still be the same object.
4. **Act**, through a fixed argv, with a backend-derived node id and an
   already-validated integer.
5. **Observe.** Take another snapshot and report what the host actually shows.
   The requested value is echoed back beside it as ``requested``, clearly
   separate from ``observed``, and it is never the evidence for success.

Why step 5 is not optional
--------------------------
``wpctl`` exits zero for a command it accepted. Accepted is not applied: a route
can clamp a volume, a device can vanish between the command and its effect, and
a card can refuse a mute. An action that reported success from an exit code
would be the same false-success shape M1 found in the launch path — a green tick
on the phone with silence in the room. So the outcome is computed by comparing
the *observed* state against what was asked for, and an action that did not
reach its target is reported as ``not_applied`` even though the command
succeeded.

What is deliberately absent
---------------------------
There is no ``move_audio_stream``. WirePlumber on the target host exposes no
command for it — ``wpctl`` has ``set-default``, ``set-volume``, ``set-mute``,
``set-profile`` and ``set-route``, and nothing that relocates a playing stream.
It could be done by writing PipeWire metadata keyed by the stream's transient
node id, but that is exactly the identity this codebase refuses to act on, and
WirePlumber's ``node.stream.restore-target`` would then persist the choice and
pin that application to that output for future sessions. The capability is
published as ``unavailable`` with that reason rather than implemented because
two numeric ids happen to fit a command line.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

from ..errors import AdapterError
from .discovery import graph_identity_value
from .models import KIND_OUTPUTS, STATUS_OK, STATUS_PARTIAL
from .service import AudioInventoryService

# -- outcomes ----------------------------------------------------------------

OUTCOME_APPLIED = "applied"
"""Observed state matches what was asked for."""

OUTCOME_PARTIAL = "partially_applied"
"""Something changed, but not everything the request implied. Selecting a
default output where an existing stream did not follow lands here."""

OUTCOME_NOT_APPLIED = "not_applied"
"""The command was accepted and the host did not end up in the requested
state. A truthful failure, not an error."""

# -- refusals ----------------------------------------------------------------
#
# Stable codes the PWA branches on. Each is a fail-closed outcome that never
# degrades into acting on something else.

REJECT_UNKNOWN_RESOURCE = "audio_resource_unknown"
REJECT_RESOURCE_CHANGED = "audio_resource_changed"
REJECT_GRAPH_CHANGED = "audio_graph_changed"
REJECT_UNAVAILABLE = "audio_unavailable"
REJECT_INVALID_VOLUME = "audio_volume_invalid"
REJECT_INVALID_MUTE = "audio_mute_invalid"
REJECT_UNSUPPORTED = "audio_action_unsupported"

MIN_VOLUME_PERCENT = 0
MAX_VOLUME_PERCENT = 100


class AudioActionRejected(Exception):
    """An action refused for a reason the user should see.

    Carries a stable ``code`` plus prose, and stays free of HTTP concerns so the
    executor is testable without a client — the same shape display-overlay
    writes already use.
    """

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# -- input validation --------------------------------------------------------


def clean_volume_percent(raw: Any) -> int:
    """Validate a requested volume, or refuse it. Never clamps.

    Clamping is the tempting behaviour and the wrong one: a client that asks for
    150 has a bug, and quietly giving it 100 hides that bug while teaching it
    the request was fine. The range is refused with its bounds named instead.

    Booleans are rejected before anything else because ``True`` is an ``int`` in
    Python and would otherwise sail through as 100%... which is precisely the
    kind of accident this milestone is meant to make impossible.
    """
    if isinstance(raw, bool):
        raise AudioActionRejected(REJECT_INVALID_VOLUME, "the volume must be a number")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw):
            raise AudioActionRejected(REJECT_INVALID_VOLUME, "the volume must be a real number")
        if not float(raw).is_integer():
            raise AudioActionRejected(
                REJECT_INVALID_VOLUME,
                "the volume must be a whole percentage",
                detail="this control works in whole percent, so a fractional value is refused "
                "rather than rounded to something you did not ask for",
            )
        value = int(raw)
    else:
        # Strings included, deliberately. "50" is a client that has not decided
        # whether it is sending a number, and coercing it would make the schema
        # a suggestion.
        raise AudioActionRejected(REJECT_INVALID_VOLUME, "the volume must be a number")

    if value < MIN_VOLUME_PERCENT or value > MAX_VOLUME_PERCENT:
        raise AudioActionRejected(
            REJECT_INVALID_VOLUME,
            f"the volume must be between {MIN_VOLUME_PERCENT} and {MAX_VOLUME_PERCENT} percent",
            detail="Cofferdam does not offer amplification above 100%, which on this hardware "
            "distorts rather than getting louder",
        )
    return value


def clean_muted(raw: Any) -> bool:
    """Validate a mute flag. Only a real boolean is accepted."""
    if not isinstance(raw, bool):
        raise AudioActionRejected(
            REJECT_INVALID_MUTE, "muted must be true or false"
        )
    return raw


# -- the executor ------------------------------------------------------------


class AudioActionExecutor:
    """Performs the three supported actions, verifying each one afterwards."""

    def __init__(self, service: AudioInventoryService, backend=None) -> None:
        self._service = service
        # The service already owns a backend; sharing it keeps one process
        # boundary rather than two that could drift apart.
        self._backend = backend if backend is not None else service.backend

    # -- resolution --------------------------------------------------------

    def _resolve_output(self, resource_id: Any) -> Tuple[Mapping[str, Any], Any]:
        """A fresh snapshot and the live output that ``resource_id`` names."""
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise AudioActionRejected(
                REJECT_UNKNOWN_RESOURCE, "no output was named in this request"
            )

        snapshot = self._service.snapshot(refresh=True)
        outputs = snapshot.collections.get(KIND_OUTPUTS)
        if outputs is None or outputs.status not in (STATUS_OK, STATUS_PARTIAL):
            raise AudioActionRejected(
                REJECT_UNAVAILABLE,
                "this host's audio outputs cannot be read right now",
                detail=outputs.reason if outputs is not None else None,
            )

        output = snapshot.output_by_resource_id(resource_id)
        if output is None:
            # The id may be from a previous audio graph, or the device may have
            # been unplugged. Both are "not here now", and neither justifies
            # picking the closest-looking output instead.
            raise AudioActionRejected(
                REJECT_UNKNOWN_RESOURCE,
                "that output is not available on this machine right now",
                detail="it may have been disconnected, or the audio server may have restarted "
                "since this page last loaded — refresh to see what is connected now",
            )
        return output, snapshot

    def _verify_still_live(self, output: Mapping[str, Any], snapshot) -> int:
        """Re-read the graph and confirm the node is still the same object.

        Returns the node id to act on. The id comes from *this* verification,
        never from the client and never from a cached snapshot.
        """
        try:
            graph = self._backend.read_graph()
        except AdapterError as exc:
            raise AudioActionRejected(
                REJECT_UNAVAILABLE, "this host's audio server could not be reached", exc.message
            ) from exc

        # The graph context first: if the audio server restarted between the
        # snapshot and now, every node id from that snapshot names a different
        # object, and no per-node check below would be trustworthy.
        expected_graph = (snapshot.graph or {}).get("graph_id")
        host_id = (snapshot.host or {}).get("host_id")
        if expected_graph and host_id and graph.cookie is not None:
            if graph_identity_value(host_id, graph.cookie) != expected_graph:
                raise AudioActionRejected(
                    REJECT_GRAPH_CHANGED,
                    "the audio server restarted while the request was being handled",
                    detail="every device reference from before the restart is stale, so the "
                    "request was refused — refresh to see the outputs that exist now",
                )

        node_id = output.get("node_id")
        node = graph.nodes.get(node_id) if isinstance(node_id, int) else None
        if node is None:
            raise AudioActionRejected(
                REJECT_RESOURCE_CHANGED,
                "that output disappeared while the request was being handled",
            )
        # The two checks that make a reused id safe. A node id being present
        # proves nothing; it must still be *this* object.
        if node.node_name != output.get("node_name"):
            raise AudioActionRejected(
                REJECT_RESOURCE_CHANGED,
                "that output changed while the request was being handled",
                detail="the audio server has reassigned this device slot to different hardware, "
                "so the request was refused rather than applied to the wrong device",
            )
        if (
            output.get("object_serial") is not None
            and node.object_serial != output.get("object_serial")
        ):
            raise AudioActionRejected(
                REJECT_RESOURCE_CHANGED,
                "that output was replaced while the request was being handled",
                detail="a new device object is now using the same slot, so the request was "
                "refused rather than applied to the wrong device",
            )
        return node.node_id

    def _reobserve(self, resource_id: str):
        """A snapshot taken after acting, plus this output as it now reads."""
        self._service.invalidate()
        after = self._service.snapshot(refresh=True)
        return after, after.output_by_resource_id(resource_id)

    # -- actions -----------------------------------------------------------

    def set_default_output(self, resource_id: Any) -> Dict[str, Any]:
        """Make one output the default, and report what actually moved.

        Whether an *already playing* stream follows is a WirePlumber policy
        decision, not ours: a stream that connected to "the default" follows the
        default, and a stream pinned to a specific target does not. Rather than
        assert either, this records where every stream was before and reads
        where each one is afterwards.
        """
        output, before = self._resolve_output(resource_id)
        streams_before = {
            item["resource_id"]: item.get("current_output_resource_id")
            for item in before.streams()
        }
        node_id = self._verify_still_live(output, before)

        self._backend.set_default_sink(node_id)

        after, observed = self._reobserve(output["resource_id"])
        if observed is None:
            return self._result(
                "set_default_audio_output",
                output,
                OUTCOME_NOT_APPLIED,
                requested={"default": True},
                observed=None,
                message="that output disappeared immediately after being selected",
                snapshot=after,
            )

        became_default = bool(observed.get("is_default"))
        movement = self._stream_movement(streams_before, after, output["resource_id"])

        if not became_default:
            message = "the audio server did not switch to that output"
            outcome = OUTCOME_NOT_APPLIED
        elif movement["already_playing"] and not movement["moved"]:
            # The honest partial. New sound will use the new output; what is
            # already playing did not follow, and the user needs to know that
            # rather than wonder why the room is quiet.
            outcome = OUTCOME_PARTIAL
            message = (
                "new sound will now play through this output, but audio that was already "
                "playing stayed where it was"
            )
        else:
            outcome = OUTCOME_APPLIED
            message = "this is now the default output"

        result = self._result(
            "set_default_audio_output",
            output,
            outcome,
            requested={"default": True},
            observed={"is_default": became_default},
            message=message,
            snapshot=after,
        )
        result["streams"] = movement
        return result

    def set_output_volume(self, resource_id: Any, volume_percent: Any) -> Dict[str, Any]:
        """Set one output's volume, then read it back and report what it is."""
        percent = clean_volume_percent(volume_percent)
        output, before = self._resolve_output(resource_id)
        node_id = self._verify_still_live(output, before)

        self._backend.set_volume_percent(node_id, percent)

        after, observed = self._reobserve(output["resource_id"])
        observed_percent = observed.get("volume_percent") if observed else None
        if observed_percent is None:
            outcome = OUTCOME_NOT_APPLIED
            message = "the volume could not be read back, so the change is not confirmed"
        elif observed_percent == percent:
            outcome = OUTCOME_APPLIED
            message = f"the volume is now {observed_percent}%"
        else:
            outcome = OUTCOME_NOT_APPLIED
            message = (
                f"the volume was set to {percent}% but this output reports {observed_percent}%"
            )
        return self._result(
            "set_output_volume",
            output,
            outcome,
            requested={"volume_percent": percent},
            observed={"volume_percent": observed_percent},
            message=message,
            snapshot=after,
        )

    def set_output_mute(self, resource_id: Any, muted: Any) -> Dict[str, Any]:
        """Mute or unmute one output, then read the flag back independently.

        Mute and unmute are not one toggle with two labels: each is executed and
        then verified on its own, so an unmute that silently failed cannot be
        reported as a successful mute-toggle.
        """
        wanted = clean_muted(muted)
        output, before = self._resolve_output(resource_id)
        node_id = self._verify_still_live(output, before)

        self._backend.set_mute(node_id, wanted)

        after, observed = self._reobserve(output["resource_id"])
        observed_muted = observed.get("muted") if observed else None
        if observed_muted is None:
            outcome = OUTCOME_NOT_APPLIED
            message = "the mute state could not be read back, so the change is not confirmed"
        elif observed_muted == wanted:
            outcome = OUTCOME_APPLIED
            message = "this output is muted" if wanted else "this output is unmuted"
        else:
            outcome = OUTCOME_NOT_APPLIED
            message = "the audio server did not change the mute state"
        return self._result(
            "set_output_mute",
            output,
            outcome,
            requested={"muted": wanted},
            observed={"muted": observed_muted},
            message=message,
            snapshot=after,
        )

    def move_stream(self, stream_resource_id: Any, output_resource_id: Any) -> Dict[str, Any]:
        """Always refused on this backend. See this module's docstring."""
        from .service import MOVE_STREAM_UNAVAILABLE_REASON

        raise AudioActionRejected(
            REJECT_UNSUPPORTED,
            "moving a playing stream to another output is not supported on this host",
            detail=MOVE_STREAM_UNAVAILABLE_REASON,
        )

    # -- shared shaping ----------------------------------------------------

    def _stream_movement(self, before: Mapping[str, Any], after, target_resource_id: str):
        """What the streams did, observed rather than assumed."""
        moved = []
        stayed = []
        for item in after.streams():
            resource = item.get("resource_id")
            if resource not in before:
                continue  # started during the action; it has no "before" to compare
            now = item.get("current_output_resource_id")
            was = before[resource]
            if was is None and now is None:
                continue
            entry = {
                "resource_id": resource,
                "application": (item.get("association") or {}).get("application"),
                "current_output_resource_id": now,
            }
            if now == target_resource_id:
                moved.append(entry)
            else:
                stayed.append(entry)
        return {
            # "Already playing" means it had an output before the switch.
            "already_playing": bool(moved or stayed),
            "moved": moved,
            "stayed": stayed,
            "verified": True,
        }

    def _result(
        self,
        operation: str,
        output: Mapping[str, Any],
        outcome: str,
        requested: Mapping[str, Any],
        observed: Optional[Mapping[str, Any]],
        message: str,
        snapshot,
    ) -> Dict[str, Any]:
        """The envelope every action returns.

        ``requested`` and ``observed`` are separate keys on purpose. A client
        rendering the result reads ``observed``; ``requested`` is there so the
        two can be shown side by side when they disagree, and so nothing can
        accidentally present the request as the outcome.
        """
        return {
            "operation": operation,
            "resource_id": output.get("resource_id"),
            "outcome": outcome,
            "requested": dict(requested),
            "observed": dict(observed) if observed is not None else None,
            "message": message,
            "output": dict(snapshot.output_by_resource_id(output["resource_id"]) or {}) or None,
            "observed_at": snapshot.observed_at,
        }


__all__ = [
    "AudioActionExecutor",
    "AudioActionRejected",
    "MAX_VOLUME_PERCENT",
    "MIN_VOLUME_PERCENT",
    "OUTCOME_APPLIED",
    "OUTCOME_NOT_APPLIED",
    "OUTCOME_PARTIAL",
    "REJECT_GRAPH_CHANGED",
    "REJECT_INVALID_MUTE",
    "REJECT_INVALID_VOLUME",
    "REJECT_RESOURCE_CHANGED",
    "REJECT_UNAVAILABLE",
    "REJECT_UNKNOWN_RESOURCE",
    "REJECT_UNSUPPORTED",
    "clean_muted",
    "clean_volume_percent",
]
