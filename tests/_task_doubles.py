"""Test doubles and fixtures for Agent Task Core.

Almost nothing is faked here, and that is deliberate. The real
:class:`~cofferdam.workstation.tasks.store.TaskStore` runs against a real SQLite
database in a temporary directory, the real state machine enforces the real
graph, and the real validation adapter is the one that ships. What a test
controls is the *environment* — where the home directory is, which projects are
configured, whether the validation adapter is registered — because those are the
things a deployment controls too.

The one genuine double is :class:`ScriptedAdapter`, and it exists to say things
the shipped adapters cannot: to claim a capability it does not have, to request
a state the graph forbids, to raise mid-call. Those are the cases the core's
refusals exist for, and none of them can be produced by a well-behaved adapter.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.adapters import AdapterRegistry, build_registry
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import TaskStore

PROJECT_ID = "demo"
OTHER_PROJECT_ID = "second"


def python_code_only(source: str) -> str:
    """Python source with comments and docstrings removed.

    Structural guards ask what a module can *do*, so they must scan code rather
    than the prose explaining why it does not do it. Without this, a docstring
    saying "this file never imports subprocess" would itself fail the check that
    it never imports subprocess — and the fix would be to delete the sentence,
    which is exactly backwards.

    The same technique ``tests/test_youtube_endpoint.py`` uses; kept here so the
    task guards and the player guards agree about what "code" means.
    """
    import ast
    import io
    import tokenize

    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add((body[0].lineno, body[0].col_offset))

    kept = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and token.start in docstrings:
            continue
        kept.append(token.string)
    return "\n".join(kept)

#: Real text in the two scripts this product exists to handle equally well. Used
#: wherever a prompt is needed, so the Unicode path is exercised by default
#: rather than only in the test that names it.
TURKISH_PROMPT = "Işıkları kıs ve şarkıyı değiştir — çok güzel oldu."


class ScriptedAdapter(TaskAdapter):
    """An adapter that does exactly what a test tells it to, including badly.

    Every knob here models something a real integration could do wrong: claim a
    capability it has not implemented, ask for a state the lifecycle forbids,
    raise an exception in the middle of a call. The core's job is to stay
    truthful through all of it, so the double has to be able to misbehave.
    """

    adapter_id = "scripted"
    display_name = "Scripted adapter"
    description = "A test double."

    def __init__(
        self,
        *,
        adapter_id: str = "scripted",
        capabilities: Optional[AdapterCapabilities] = None,
        start_outcome: Optional[AdapterOutcome] = None,
        followup_outcome: Optional[AdapterOutcome] = None,
        cancel_outcome: Optional[AdapterOutcome] = None,
        raise_on_start: bool = False,
        refuse_cancel: bool = False,
        available_flag: bool = True,
    ) -> None:
        self.adapter_id = adapter_id
        self._capabilities = capabilities or AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )
        self._start_outcome = start_outcome
        self._followup_outcome = followup_outcome
        self._cancel_outcome = cancel_outcome
        self._raise_on_start = raise_on_start
        self._refuse_cancel = refuse_cancel
        self._available = available_flag
        #: Every context this adapter was handed, so a test can assert what the
        #: core did and did not pass — the project root in particular.
        self.contexts: List[TaskContext] = []
        self.cancelled: List[str] = []

    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def available(self) -> bool:
        return self._available

    def start(self, context: TaskContext) -> AdapterOutcome:
        self.contexts.append(context)
        if self._raise_on_start:
            raise RuntimeError("the scripted adapter broke on purpose")
        if self._start_outcome is not None:
            return self._start_outcome
        return AdapterOutcome(
            events=(AdapterEvent(text="scripted start"),), requested_state="running"
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self.contexts.append(context)
        if self._followup_outcome is not None:
            return self._followup_outcome
        return AdapterOutcome(
            events=(AdapterEvent(text="scripted follow-up"),),
            requested_state="completed",
            final_result="done",
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        self.contexts.append(context)
        if self._refuse_cancel:
            raise AdapterRefusal("the scripted adapter refuses to cancel")
        self.cancelled.append(context.task_id)
        if self._cancel_outcome is not None:
            return self._cancel_outcome
        return AdapterOutcome(requested_state="cancelled")


class TaskTestCase(unittest.TestCase):
    """A temporary COFFERDAM_HOME, a real store, and a configured project.

    Subclasses override :attr:`enable_validation_adapter` and
    :meth:`extra_adapters` rather than reassembling the wiring, so every test
    exercises the same construction path :func:`create_app` uses.
    """

    enable_validation_adapter = True
    project_adapters: Tuple[str, ...] = ("validation", "scripted")

    def setUp(self) -> None:
        self._home_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._home_dir.cleanup)
        self.home = Path(self._home_dir.name)
        self.project_root = self.home / "projects" / PROJECT_ID
        self.project_root.mkdir(parents=True)

        self.config = load_config(self.home)
        self.config = type(self.config)(
            **{
                **self.config.__dict__,
                "enable_validation_task_adapter": self.enable_validation_adapter,
            }
        )
        self.config.ensure_dirs()
        self.write_projects()

        self.store = TaskStore(self.config)
        self.addCleanup(self.store.close)
        self.adapters = self.build_adapters()
        self.audit: List[Dict[str, Any]] = []
        self.service = TaskService(
            self.config,
            self.store,
            self.adapters,
            projects=load_projects(self.config, self.adapters.ids()),
            audit=self.record_audit,
        )

    # -- wiring --------------------------------------------------------------

    def build_adapters(self) -> AdapterRegistry:
        registry = build_registry(
            enable_validation_adapter=self.enable_validation_adapter
        )
        extra = self.extra_adapters()
        if not extra:
            return registry
        existing = tuple(
            registry.find(name) for name in registry.ids() if registry.find(name)
        )
        return AdapterRegistry(existing + tuple(extra))

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        return ()

    def write_projects(self, entries: Optional[List[Dict[str, Any]]] = None) -> None:
        if entries is None:
            entries = [
                {
                    "project_id": PROJECT_ID,
                    "display_name": "Demo project",
                    "root": str(self.project_root),
                    "adapters": list(self.project_adapters),
                }
            ]
        path = self.config.config_dir / "task-projects.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"projects": entries}), encoding="utf-8")

    def install_adapter(self, adapter: TaskAdapter) -> TaskAdapter:
        """Register a test adapter and let the demo project use it.

        Both halves are needed, and the second is not boilerplate: the project
        registry drops adapter names it does not recognise at load time, so an
        adapter registered after the projects were read is correctly refused
        until the projects are re-read. That is the shipped behaviour, so the
        test wiring works with it rather than around it.
        """
        self.adapters._adapters[adapter.adapter_id] = adapter
        self.service._adapters = self.adapters
        self.write_projects(
            [
                {
                    "project_id": PROJECT_ID,
                    "display_name": "Demo project",
                    "root": str(self.project_root),
                    "adapters": sorted(set(self.project_adapters) | {adapter.adapter_id}),
                }
            ]
        )
        self.service.reload_projects()
        return adapter

    def record_audit(
        self, operation, result, task_id, adapter_id, project_id, correlation_id
    ) -> None:
        self.audit.append(
            {
                "operation": operation,
                "result": result,
                "task_id": task_id,
                "adapter_id": adapter_id,
                "project_id": project_id,
                "correlation_id": correlation_id,
            }
        )

    # -- helpers -------------------------------------------------------------

    def create(
        self,
        prompt: str = TURKISH_PROMPT,
        *,
        adapter_id: str = "validation",
        project_id: str = PROJECT_ID,
        **kwargs: Any,
    ):
        row, _created = self.service.create_task(
            project_id=project_id, adapter_id=adapter_id, prompt=prompt, **kwargs
        )
        return row

    def restart(self) -> TaskService:
        """A new service over the *same* database, as a daemon restart is.

        The store is reopened rather than reused so the test exercises the real
        "read rows written by a process that is gone" path, and the validation
        adapter is rebuilt so it has no memory of anything — which is the actual
        situation after a restart.
        """
        self.store.close()
        self.store = TaskStore(self.config)
        self.addCleanup(self.store.close)
        self.adapters = self.build_adapters()
        self.service = TaskService(
            self.config,
            self.store,
            self.adapters,
            projects=load_projects(self.config, self.adapters.ids()),
            audit=self.record_audit,
        )
        return self.service

    def event_types(self, task_id: str) -> List[str]:
        return [event.event_type for event in self.store.events(task_id, limit=200)]

    def audit_blob(self) -> str:
        return json.dumps(self.audit, ensure_ascii=False)


__all__ = [
    "OTHER_PROJECT_ID",
    "PROJECT_ID",
    "TURKISH_PROMPT",
    "AdapterCapabilities",
    "AdapterEvent",
    "AdapterOutcome",
    "AdapterRefusal",
    "ScriptedAdapter",
    "TaskTestCase",
    "python_code_only",
]
