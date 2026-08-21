"""What is Cofferdam doing right now? One answer, projected from durable facts.

Five components already own pieces of that answer: the planner owns whether a
plan is being made, the authority gate owns whether a person has decided, Task
Core owns whether a worker is running, worker recovery owns what a restart found,
and the publisher owns whether anything reached GitHub. Each is right about its
own piece and none can answer the whole question.

This package is the projection that joins them, and the discipline that makes it
safe is stated once here: **it owns no state.** Every value it reports is read
live from whoever owns it, joined on ids that are already durable. There is no
operations table, no status column, no cache and no writer — which is what
guarantees this view can never disagree with the systems it describes.
"""

from __future__ import annotations

from . import phases, view
from .phases import PHASES, Phase
from .view import OperationsService, ProjectOperations

__all__ = [
    "PHASES",
    "OperationsService",
    "Phase",
    "ProjectOperations",
    "phases",
    "view",
]
