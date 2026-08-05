"""Reading and safely controlling this workstation's audio.

A focused module rather than another wing of the general Linux adapter: audio
has its own identity problem (PipeWire reuses node ids), its own truthfulness
problem (a command that was accepted is not a change that happened), and its own
privacy problem (a stream's properties carry the title of whatever is playing).
Those are not variations on launching an application, and mixing them into the
session adapter would have buried all three.

Layering, outermost first:

``service.AudioInventoryService``
    One graph read per snapshot, short-cached, invalidated by graph identity.
``actions.AudioActionExecutor``
    The typed actions. Resolves, re-verifies, acts, then observes the result.
``discovery``
    Graph objects to published outputs and streams, on an allowlist of fields.
``wireplumber.WirePlumberBackend``
    The only place that runs a program.
``models``
    The published shapes, sharing the runtime milestone's status vocabulary.

The same models serve the PWA and the future desktop companion; neither gets a
private shape, and neither is trusted to send a PipeWire node id.
"""

from .actions import AudioActionExecutor, AudioActionRejected
from .models import AUDIO_SNAPSHOT_VERSION, KIND_OUTPUTS, KIND_STREAMS, AudioSnapshot
from .service import AudioInventoryService
from .wireplumber import WirePlumberBackend

__all__ = [
    "AUDIO_SNAPSHOT_VERSION",
    "AudioActionExecutor",
    "AudioActionRejected",
    "AudioInventoryService",
    "AudioSnapshot",
    "KIND_OUTPUTS",
    "KIND_STREAMS",
    "WirePlumberBackend",
]
