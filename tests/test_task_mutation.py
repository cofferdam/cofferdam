"""Mutation checks: prove the Task Core guards are load-bearing.

A passing suite proves the code behaves. It does not prove the *tests* would
notice if a guard were removed — a check can be deleted and leave a suite just
as green, because nothing was ever exercising it.

So each test below deliberately breaks one guard and asserts that the property
it protects visibly fails. If a mutation ever stops producing a failure, the
corresponding guard has become decorative and this file says so.

These are the eight the milestone brief calls out by name:

1. illegal state transition acceptance
2. non-transactional snapshot/event update
3. validation adapter enabled by client input
4. arbitrary working-directory acceptance
5. idempotency bypass
6. restart leaving a task falsely running
7. cancellation affecting the wrong task
8. prompt content entering audit/log output
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from cofferdam.workstation.tasks import errors as task_errors
from cofferdam.workstation.tasks.adapters import build_registry
from cofferdam.workstation.tasks.lifecycle import IllegalTransition
from cofferdam.workstation.tasks.models import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_INTERRUPTED,
    STATE_RUNNING,
)

from ._task_doubles import (
    PROJECT_ID,
    TURKISH_PROMPT,
    ScriptedAdapter,
    TaskTestCase,
    python_code_only,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _code_lines(path: Path) -> str:
    """Source with comment lines dropped, but expressions left intact.

    The token-based stripper in ``_task_doubles`` is right for "does this module
    import subprocess", and wrong for "how many times does this file write
    ``row.prompt``" — it separates every token with a newline, so an attribute
    access stops being a substring. These guards are about *expressions*, so
    they scan lines and drop the ones that are comments.
    """
    kept = []
    for line in path.read_text("utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _service_source() -> str:
    return _code_lines(REPO_ROOT / "cofferdam" / "workstation" / "service.py")


# -- 1. illegal state transition acceptance ----------------------------------


class TransitionGuard(TaskTestCase):
    def test_the_guard_holds(self):
        row = self.create()   # completed
        with self.assertRaises(IllegalTransition):
            self.store.transition(
                row.task_id,
                STATE_RUNNING,
                event_type="task_started",
                actor="system",
                source="cofferdam",
            )

    def test_widening_the_graph_lets_a_finished_task_run_again(self):
        """Mutation: the graph gains an edge out of a terminal state.

        The failure this prevents is the worst kind of quiet: a completed task's
        result and completion time would become editable after the fact, and the
        append-only history would have a completion in the middle of it.
        """
        from cofferdam.workstation.tasks import lifecycle as module

        row = self.create()
        self.assertEqual(row.state, STATE_COMPLETED)

        original = module.ALLOWED_TRANSITIONS[STATE_COMPLETED]
        module.ALLOWED_TRANSITIONS[STATE_COMPLETED] = frozenset({STATE_RUNNING})
        try:
            mutated = self.store.transition(
                row.task_id,
                STATE_RUNNING,
                event_type="task_started",
                actor="system",
                source="cofferdam",
            )
        finally:
            module.ALLOWED_TRANSITIONS[STATE_COMPLETED] = original

        self.assertEqual(
            mutated.state,
            STATE_RUNNING,
            "the mutation changed nothing — the graph is not consulted",
        )
        # And unmutated, the same move is refused again — asserted against a
        # *fresh* completed task, because the mutated one is now running and
        # running -> completed is a legal edge.
        other = self.create()
        self.assertEqual(other.state, STATE_COMPLETED)
        with self.assertRaises(IllegalTransition):
            self.store.transition(
                other.task_id,
                STATE_RUNNING,
                event_type="task_started",
                actor="system",
                source="cofferdam",
            )

    def test_an_adapter_requesting_an_illegal_state_is_refused(self):
        scripted = self.install_adapter(
            ScriptedAdapter(
                adapter_id="jumper",
                start_outcome=type(
                    "O", (), {}
                )  # replaced below; see the concrete outcome
            )
        )
        # Built explicitly rather than through the double's defaults, so the
        # requested state is unmistakably the illegal one.
        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterEvent,
            AdapterOutcome,
        )

        scripted._start_outcome = AdapterOutcome(
            events=(AdapterEvent(text="claiming to be cancelled"),),
            requested_state=STATE_CANCELLED,
        )
        row = self.create(adapter_id="jumper")
        self.assertEqual(row.state, STATE_RUNNING)
        self.assertIn("action_rejected", self.event_types(row.task_id))


# -- 2. non-transactional snapshot/event update ------------------------------


class TransactionGuard(TaskTestCase):
    def test_the_guard_holds(self):
        """Every state a task has been in is represented in its history."""
        row = self.create(prompt="scenario: wait")
        self.service.send_followup(row.task_id, "devam")
        events = self.store.events(row.task_id, limit=200)
        final = self.store.get(row.task_id)
        self.assertEqual(events[-1].lifecycle_revision, final.lifecycle_revision)
        self.assertEqual(events[-1].state, final.state)

    def test_a_failed_event_write_rolls_the_state_back(self):
        """Mutation: the event insert fails after the row has been updated.

        Without a transaction the task would move and its history would not,
        producing a snapshot that says ``completed`` with no completion event —
        the one disagreement the whole storage design exists to prevent.
        """
        row = self.create(prompt="scenario: cancel")
        self.assertEqual(row.state, STATE_RUNNING)
        before_events = len(self.store.events(row.task_id, limit=200))

        original = self.store._append_event_locked

        def exploding(*args, **kwargs):
            raise RuntimeError("the event write failed")

        self.store._append_event_locked = exploding
        try:
            with self.assertRaises(RuntimeError):
                self.store.transition(
                    row.task_id,
                    STATE_COMPLETED,
                    event_type="task_completed",
                    actor="adapter",
                    source="adapter",
                )
        finally:
            self.store._append_event_locked = original

        after = self.store.get(row.task_id)
        self.assertEqual(
            after.state,
            STATE_RUNNING,
            "the state moved without its event — the write was not transactional",
        )
        self.assertEqual(len(self.store.events(row.task_id, limit=200)), before_events)


# -- 3. validation adapter enabled by client input ---------------------------


class ValidationAdapterGuard(unittest.TestCase):
    def test_the_guard_holds(self):
        self.assertEqual(build_registry().ids(), ())

    def test_enabling_it_is_the_only_thing_that_registers_it(self):
        """Mutation: the flag defaults to on.

        Asserted as a difference rather than as a constant, so a change to the
        default is visible here rather than only in a config file nobody reads.
        """
        self.assertEqual(build_registry(enable_validation_adapter=True).ids(), ("validation",))
        self.assertNotEqual(
            build_registry().ids(),
            build_registry(enable_validation_adapter=True).ids(),
            "the flag changes nothing — registration does not depend on it",
        )

    def test_no_request_field_reaches_the_flag(self):
        """Structural: the flag is read from config, never from a body.

        The route layer is scanned as code for any path from a request to the
        configuration field. There is exactly one reader, and it is
        ``create_app``.
        """
        source = _service_source()
        readers = source.count("enable_validation_task_adapter")
        self.assertEqual(readers, 1, "the flag is read in more than one place")
        self.assertIn("config.enable_validation_task_adapter", source)

    def test_the_cli_flag_cannot_disable_a_configured_adapter(self):
        """One direction only, so an omitted flag never silently removes it."""
        source = _code_lines(REPO_ROOT / "cofferdam" / "workstation" / "__main__.py")
        self.assertIn("enable_validation_task_adapter", source)
        # There is no `False` assignment anywhere in the entry point.
        self.assertNotIn('"enable_validation_task_adapter": False', source)


# -- 4. arbitrary working-directory acceptance -------------------------------


class ProjectRootGuard(TaskTestCase):
    def test_the_guard_holds(self):
        with self.assertRaises(task_errors.ProjectUnknown):
            self.create(project_id="/etc")

    def test_removing_the_root_verification_reaches_an_unverified_directory(self):
        """Mutation: ``verify_root`` stops checking anything.

        The guard is what turns a project whose folder has been replaced by a
        symlink into a refusal. With it removed, the task runs against whatever
        the link points at — which is the whole reason the check exists.
        """
        from cofferdam.workstation.tasks import service as module

        outside = self.home / "somewhere-else"
        outside.mkdir()
        linked = self.home / "linked-root"
        linked.symlink_to(outside, target_is_directory=True)

        # The adapter is registered *before* the projects are written, because
        # the project loader drops adapter names it does not yet recognise —
        # the shipped behaviour, which the wiring works with rather than around.
        scripted = ScriptedAdapter()
        self.adapters._adapters["scripted"] = scripted
        self.write_projects(
            [
                {
                    "project_id": "linked",
                    "root": str(linked),
                    "adapters": ["validation", "scripted"],
                }
            ]
        )
        self.service.reload_projects()

        # The guard catches it.
        with self.assertRaises(task_errors.ProjectRootInvalid):
            self.create(project_id="linked")

        original = module.verify_root
        module.verify_root = lambda root: root
        try:
            self.create(project_id="linked", adapter_id="scripted")
        finally:
            module.verify_root = original

        self.assertTrue(scripted.contexts, "the mutation did not reach the adapter")
        self.assertEqual(
            scripted.contexts[0].project_root,
            linked,
            "the mutation did not produce an unverified root — the check is decorative",
        )

    def test_there_is_no_route_field_for_a_directory(self):
        """Structural: the route's allowlist has no path-shaped key."""
        source = _service_source()
        block = source.split("async def create_task")[1][:1500]
        for forbidden in ("working_directory", "cwd", "root", "path", "executable"):
            self.assertNotIn(forbidden, block, forbidden + " is in the create allowlist")


# -- 5. idempotency bypass ---------------------------------------------------


class IdempotencyGuard(TaskTestCase):
    def test_the_guard_holds(self):
        for _ in range(3):
            self.service.create_task(
                project_id=PROJECT_ID,
                adapter_id="validation",
                prompt="once",
                client_request_id="key-1",
            )
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_ignoring_the_key_produces_duplicate_tasks(self):
        """Mutation: the lookup always reports "not seen before"."""
        original = self.store._lookup_idempotent
        self.store._lookup_idempotent = lambda *args, **kwargs: None
        try:
            for _ in range(3):
                self.service.create_task(
                    project_id=PROJECT_ID,
                    adapter_id="validation",
                    prompt="once",
                    client_request_id="key-2",
                )
        finally:
            self.store._lookup_idempotent = original

        self.assertEqual(
            len(self.store.list_tasks()),
            3,
            "the mutation produced no duplicates — the key is not what prevents them",
        )

    def test_the_conflict_check_is_load_bearing(self):
        """Mutation: the payload hash stops being compared.

        Without it, a key reused for a *different* request would silently return
        the earlier task — answering a question the client did not ask.
        """
        self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="first prompt",
            client_request_id="key-3",
        )
        with self.assertRaises(task_errors.IdempotencyConflict):
            self.service.create_task(
                project_id=PROJECT_ID,
                adapter_id="validation",
                prompt="a completely different prompt",
                client_request_id="key-3",
            )

        original = self.store._lookup_idempotent

        def unchecked(connection, scope, request_key, payload_hash):
            return original(connection, scope, request_key, None)

        self.store._lookup_idempotent = unchecked
        try:
            row, created = self.service.create_task(
                project_id=PROJECT_ID,
                adapter_id="validation",
                prompt="a completely different prompt",
                client_request_id="key-3",
            )
        finally:
            self.store._lookup_idempotent = original

        self.assertFalse(
            created,
            "the mutation did not silently reuse the earlier task — the hash is decorative",
        )
        self.assertEqual(self.store.get(row.task_id).prompt, "first prompt")


# -- 6. restart leaving a task falsely running -------------------------------


class RestartGuard(TaskTestCase):
    def test_the_guard_holds(self):
        row = self.create(prompt="scenario: interrupt")
        self.restart().recover_after_restart()
        self.assertEqual(self.store.get(row.task_id).state, STATE_INTERRUPTED)

    def test_skipping_recovery_leaves_a_task_falsely_running(self):
        """Mutation: start-up stops settling non-terminal tasks.

        This is the single most important guard in the milestone. Without it the
        database's word is taken as evidence, and the phone shows "running" for a
        task whose process died with the daemon — for as long as somebody keeps
        looking at it.
        """
        row = self.create(prompt="scenario: interrupt")
        service = self.restart()
        # The mutation: recovery simply is not run.
        self.assertEqual(
            self.store.get(row.task_id).state,
            STATE_RUNNING,
            "the task was not left running — this test proves nothing",
        )
        # And running it produces the truthful state.
        service.recover_after_restart()
        self.assertEqual(self.store.get(row.task_id).state, STATE_INTERRUPTED)

    def test_recovery_is_wired_into_application_start_up(self):
        """The guard only helps if something calls it. Asserted structurally."""
        source = python_code_only(
            (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text("utf-8")
        )
        self.assertIn("recover_after_restart", source)

    def test_an_adapter_claiming_recovery_gets_the_other_state(self):
        """The one branch: a genuinely recoverable adapter is not lied about either."""
        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterCapabilities,
            AdapterOutcome,
        )

        scripted = self.install_adapter(
            ScriptedAdapter(
                adapter_id="recoverable",
                capabilities=AdapterCapabilities(
                    start=True, cancel=True, recover_after_restart=True
                ),
                start_outcome=AdapterOutcome(requested_state=STATE_RUNNING),
            )
        )
        row = self.create(adapter_id="recoverable")
        service = self.restart()
        service._adapters._adapters["recoverable"] = scripted
        service.recover_after_restart()
        self.assertEqual(self.store.get(row.task_id).state, "recovery_required")


# -- 7. cancellation affecting the wrong task --------------------------------


class CancellationGuard(TaskTestCase):
    def test_the_guard_holds(self):
        first = self.create(prompt="scenario: cancel")
        second = self.create(prompt="scenario: cancel")
        self.service.cancel_task(first.task_id)
        self.assertEqual(self.store.get(second.task_id).state, STATE_RUNNING)

    def test_cancel_only_ever_names_one_task(self):
        """Mutation: an adapter is asked to cancel everything it knows.

        The shipped path hands the adapter exactly one context. This proves the
        difference is observable — a "cancel" that reached more than one task
        would leave the second one cancelled too.
        """
        scripted = self.install_adapter(ScriptedAdapter(adapter_id="broad"))
        first = self.create(adapter_id="broad")
        second = self.create(adapter_id="broad")

        self.service.cancel_task(first.task_id)
        self.assertEqual(scripted.cancelled, [first.task_id])
        self.assertNotIn(second.task_id, scripted.cancelled)
        self.assertNotEqual(self.store.get(second.task_id).state, STATE_CANCELLED)

    #: The files allowed to know a process exists, and nothing else is.
    #:
    #: ``claude_code`` because that adapter *is* a process launcher, for the
    #: reason given on the matching guard in ``tests/test_task_core.py``.
    #: ``hostclient.py`` because M2I PR2 runs the Agent SDK inside a helper
    #: Cofferdam launches with a bounded environment — the SDK offers no way to
    #: replace an inherited one, so owning the spawn is how an agent stops
    #: inheriting the daemon's secrets.
    #:
    #: Both are *adapters*. The point of this guard was never "one file": it is
    #: that Task Core's lifecycle, store, models, errors and service cannot reach
    #: a process, and that is still exactly true.
    #: ``hostclient.py`` starts and owns the Agent SDK helper child.
    #: ``gitbaseline.py`` (M2K PR4) is the host's pre-work Git probe and
    #: ``gitrange.py`` (M2K PR5) its post-work one: each runs a process and owns
    #: none — constant argv tuples, ``shell=False``, a closed environment, a
    #: timeout and an output cap, all read-only, nothing outliving the call. The
    #: breadth rules below still apply to both in full, and neither contains any
    #: of that vocabulary.
    PROCESS_AWARE_FILES = ("hostclient.py", "gitbaseline.py", "gitrange.py")

    def test_no_broad_process_vocabulary_exists_in_task_core(self):
        """Structural: there is nothing here that *could* kill by name.

        The two words that make this guard about *breadth* rather than about
        processes — ``pkill`` and ``killall`` — are forbidden **everywhere**,
        including in the excepted files, and are checked separately below.
        Nothing in this repository may kill by name, in any directory.
        """
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.rglob("*.py")):
            source = python_code_only(path.read_text("utf-8"))
            # No exception, in any file, ever. This is the breadth rule.
            for never in ("pkill", "killall"):
                self.assertNotIn(never, source, str(path) + " uses " + never)
            if "claude_code" in path.parts or path.name in self.PROCESS_AWARE_FILES:
                continue
            for forbidden in ("os.kill", "signal", "subprocess"):
                self.assertNotIn(forbidden, source, str(path) + " uses " + forbidden)

    def test_the_agent_sdk_launcher_signals_only_its_own_child(self):
        """The excepted file stops a group it created, never one it looked up.

        **This rule changed in M2I PR4, and the reason is worth stating.** The
        file used to be held to "call ``terminate`` and ``kill`` as methods on
        the object ``Popen`` returned, and never name a pid or a group". That
        sounds stricter and was, about the wrong thing: ``Popen.terminate``
        signals the helper alone, and the helper is not the only process in the
        picture. The SDK starts a Claude CLI of its own, inside the helper's
        process group, so a terminated helper could leave that CLI running with a
        live subscription session. Refusing to name a group did not prevent an
        orphan; it prevented cleaning one up.

        So the file now signals a group, under the ownership rule the Claude Code
        adapter has enforced since M2G: pid, ``/proc`` start time and group id
        recorded at launch, **all three** re-verified immediately before every
        signal. What stays forbidden is everything that makes a stop broad — a
        bare ``os.kill`` on a pid, process enumeration, and any match on a
        process *name*.
        """
        launcher = (
            REPO_ROOT
            / "cofferdam"
            / "workstation"
            / "tasks"
            / "adapters"
            / "claude_agent_sdk"
            / "hostclient.py"
        )
        source = python_code_only(launcher.read_text("utf-8"))
        tokens = source.split("\n")
        # Broad stops, still absent. `pidof` and the name matchers are what turn
        # "stop my child" into "stop anything that looks like Claude".
        for forbidden in ("psutil", "pkill", "killall", "pidof"):
            self.assertNotIn(forbidden, tokens, "hostclient.py uses " + forbidden)
        # A bare `os.kill(pid, …)` bypasses the group check entirely.
        self.assertNotIn("os\n.\nkill\n(", source.replace("\n\n", "\n"))
        # And the identity rule is present rather than assumed: a `killpg` that
        # were not gated behind `still_ours` would be exactly the mistake the
        # previous version of this test was trying to prevent.
        self.assertIn("killpg", tokens)
        self.assertIn("still_ours", tokens)
        signal_site = source.index("killpg")
        self.assertLess(
            source.index("still_ours"),
            signal_site,
            "hostclient.py signals a group before it verifies it owns one",
        )


# -- 8. prompt content entering audit or log output --------------------------


class ContentLeakGuard(TaskTestCase):
    def test_the_guard_holds(self):
        self.create(prompt=TURKISH_PROMPT)
        self.assertNotIn(TURKISH_PROMPT, self.audit_blob())

    def test_adding_content_to_the_audit_call_would_be_visible(self):
        """Mutation: the audit hook is called with the prompt.

        Proves the assertion above is not vacuous — if content *did* reach the
        audit path, this suite would notice.
        """
        self.record_audit(
            "task_create", "ok", "task_x", "validation", PROJECT_ID, TURKISH_PROMPT
        )
        self.assertIn(
            TURKISH_PROMPT,
            self.audit_blob(),
            "the audit blob does not include what is passed to it — the check is blind",
        )

    def test_the_audit_signature_has_no_content_parameter(self):
        """The structural half: content cannot arrive by accident."""
        import inspect

        from cofferdam.workstation.store import ActionStore

        parameters = list(inspect.signature(ActionStore.record_task_event).parameters)
        # "result" is deliberately absent: the parameter of that name carries
        # an *outcome word* from a closed vocabulary ("ok", "rejected"), which
        # is exactly what an audit record is for.
        for forbidden in ("prompt", "followup", "text", "content", "payload", "final_result"):
            self.assertNotIn(forbidden, parameters)

    def test_task_core_writes_no_log_line_at_all(self):
        """Nothing to leak into, which is stronger than filtering what does."""
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.rglob("*.py")):
            source = python_code_only(path.read_text("utf-8"))
            # `stdout` and `stderr` are forbidden everywhere except the adapter
            # that launches a process, where they are the names of that
            # process's pipes rather than anywhere output could be written.
            # `logging`, `logger` and `print(` stay forbidden in every file,
            # including the adapter — those are the words that would actually
            # put a prompt or a result somewhere it must never appear, and the
            # property this test is named for is untouched.
            if path.name == "observe.py":
                # The local observer is a command a person runs in their own
                # terminal, so printing is its entire purpose. The property this
                # test protects is that task content never reaches a *log* — a
                # journal line, an operator's aggregator, something that outlives
                # the task and is read by somebody who was not there. Writing to
                # the stdout of a program somebody just typed is the opposite of
                # that, and it is why the observer exists at all.
                #
                # `logging` and `logger` stay forbidden here, which is the half
                # that matters: those are the words that would put a prompt
                # somewhere nobody chose.
                forbidden_here = ("logging", "logger", "journal", "syslog")
            else:
                forbidden_here = ("logging", "logger", "print(")
                # The Agent SDK helper and its launcher name a *pipe* when they
                # say `stdout`, exactly as the Claude Code adapter does. For the
                # helper it is the protocol channel to its parent, and it is the
                # reason there is no `print(` anywhere in that file: anything
                # printed would corrupt a frame. `logging` and `logger` stay
                # forbidden in both, which is the half this test is named for.
                # `gitbaseline.py` (M2K PR4) and `gitrange.py` (M2K PR5) join
                # them for the same reason and read even less: each names
                # `stdout` once, to take the bounded bytes a finished
                # `git rev-parse`, `git status` or `git diff` produced, and
                # neither touches stderr at all — a Git error message carries an
                # absolute host path, so the probes record a closed reason code
                # and drop the text entirely.
                pipe_owners = (
                    "host.py", "hostclient.py", "gitbaseline.py", "gitrange.py",
                )
                if (
                    "claude_code" not in path.parts
                    and path.name not in pipe_owners
                ):
                    forbidden_here += ("stdout", "stderr")
            for forbidden in forbidden_here:
                self.assertNotIn(forbidden, source, str(path) + " uses " + forbidden)

    def test_no_api_response_carries_the_prompt_except_the_detail_view(self):
        """The single publication point, asserted against every other route."""
        source = _service_source()
        # `row.prompt` appears exactly once in the whole route layer.
        self.assertEqual(source.count("row.prompt"), 1, "the prompt is published twice")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
