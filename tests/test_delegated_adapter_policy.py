"""Which adapter runs a delegated task, and who is allowed to decide.

M2I.5 Gate B registers both Claude transports on one workstation at the same
time. Everything in this file exists because that makes one previously
harmless behaviour dangerous.

The behaviour: the Actions bridge had no ``adapter_id`` field — correctly — so
it took **the first adapter the project listed**. While every delegated project
permitted exactly one adapter that was a decision with one possible outcome. The
moment two coexist it becomes a real choice, made by list position.

And it was not even the position the operator wrote. ``TaskProject`` sorts the
list at load, so "first" meant *alphabetically first* — with both transports
permitted, ``claude-agent-sdk`` beats ``claude-code`` because ``a`` sorts before
``c``. Nobody would have chosen that rule, nobody would have seen it apply, and
it would have decided which agent ran on somebody's machine.

The nine cases below are the contract that replaced it. Two of them —
:meth:`DelegationPolicyTests.test_sorting_does_not_decide` and
:meth:`RegistryOrderTests.test_the_delegated_result_is_independent_of_file_order`
— exist specifically to fail if ordering ever becomes authority again, in either
layer.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.projects import (
    DELEGATION_AMBIGUOUS,
    DELEGATION_NO_ADAPTER,
    DELEGATION_OK,
    DELEGATION_STATUSES,
    DELEGATION_UNAVAILABLE,
    TaskProject,
    load_projects,
)

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

if TestClient is not None:
    from cofferdam.actions_bridge import normalize as bridge_normalize
    from cofferdam.actions_bridge.config import load_bridge_config
    from cofferdam.actions_bridge.service import create_bridge_app

    from ._actions_bridge_doubles import PROJECT_ID, FakeInternalClient, project

KEY = "bridge-test-key-not-a-real-credential-0002"

CLAUDE_CODE = "claude-code"
AGENT_SDK = "claude-agent-sdk"
VALIDATION = "validation"

#: The adapter ids a Gate B host registers. Written out rather than imported
#: from the adapter packages, so that renaming an id is a visible change here
#: too — this file is about the *words in a configuration file*, and those do not
#: move just because a class did.
BOTH_TRANSPORTS = (AGENT_SDK, CLAUDE_CODE)


def _project(**kwargs) -> TaskProject:
    fields = {
        "project_id": "p",
        "display_name": "P",
        "root": Path("/nonexistent"),
    }
    fields.update(kwargs)
    return TaskProject(**fields)


# -- 1..5, 7, 8, 9: the host's own resolution ---------------------------------


class DelegationPolicyTests(unittest.TestCase):
    """``TaskProject.delegation`` — the one place the question is answered."""

    def test_one_eligible_adapter_resolves_without_a_delegation(self) -> None:
        """Case 1. Backward compatibility, and it is not a fallback.

        There is nothing to choose between, so no existing registry has to gain
        a field to keep working. Requiring one here would have made every
        pre-Gate-B host stop accepting tasks on upgrade, for no safety gained.
        """
        for adapter in (CLAUDE_CODE, AGENT_SDK, VALIDATION):
            with self.subTest(adapter=adapter):
                self.assertEqual(
                    _project(adapters=(adapter,)).delegation(),
                    (adapter, DELEGATION_OK),
                )

    def test_an_explicit_delegation_wins_among_several(self) -> None:
        """Case 2. Both transports permitted, one named, that one runs."""
        for chosen in BOTH_TRANSPORTS:
            with self.subTest(chosen=chosen):
                self.assertEqual(
                    _project(
                        adapters=BOTH_TRANSPORTS, delegated_adapter=chosen
                    ).delegation(),
                    (chosen, DELEGATION_OK),
                )

    def test_several_adapters_and_no_delegation_fails_closed(self) -> None:
        """Case 3. The case this whole change exists for."""
        adapter, status = _project(adapters=BOTH_TRANSPORTS).delegation()
        self.assertIsNone(adapter)
        self.assertEqual(status, DELEGATION_AMBIGUOUS)

    def test_sorting_does_not_decide(self) -> None:
        """The regression guard, stated as the fact that would have decided it.

        ``claude-agent-sdk`` sorts before ``claude-code``. If ordering ever
        becomes authority again — in either direction — this fails, because the
        ambiguous case must resolve to *nothing*, not to whichever end of the
        sorted list somebody picked.
        """
        self.assertLess(AGENT_SDK, CLAUDE_CODE)
        resolved = _project(adapters=BOTH_TRANSPORTS).delegated_adapter_id
        self.assertIsNone(resolved)
        self.assertNotEqual(resolved, AGENT_SDK)
        self.assertNotEqual(resolved, CLAUDE_CODE)

    def test_a_delegation_the_project_does_not_permit_fails_closed(self) -> None:
        """Case 4/5 at the registry layer. A selection, never a grant.

        ``adapters`` drops names this build did not register, so an unregistered
        or disabled adapter reaches here as one that is simply not in the list —
        which is the same refusal as naming one the project never permitted. One
        mechanism covers both, and it fails in the direction where no task runs.
        """
        adapter, status = _project(
            adapters=(CLAUDE_CODE,), delegated_adapter=AGENT_SDK
        ).delegation()
        self.assertIsNone(adapter)
        self.assertEqual(status, DELEGATION_UNAVAILABLE)

    def test_a_delegation_with_no_permitted_adapters_at_all_fails_closed(self) -> None:
        adapter, status = _project(
            adapters=(), delegated_adapter=CLAUDE_CODE
        ).delegation()
        self.assertIsNone(adapter)
        self.assertEqual(status, DELEGATION_UNAVAILABLE)

    def test_no_adapters_is_its_own_status(self) -> None:
        """Not a misconfiguration: a Remote-Control-only project looks like this."""
        self.assertEqual(_project(adapters=()).delegation(), (None, DELEGATION_NO_ADAPTER))

    def test_every_status_is_in_the_published_vocabulary(self) -> None:
        cases = (
            _project(adapters=()),
            _project(adapters=(CLAUDE_CODE,)),
            _project(adapters=BOTH_TRANSPORTS),
            _project(adapters=BOTH_TRANSPORTS, delegated_adapter=CLAUDE_CODE),
            _project(adapters=(CLAUDE_CODE,), delegated_adapter=AGENT_SDK),
        )
        for entry in cases:
            with self.subTest(adapters=entry.adapters):
                self.assertIn(entry.delegation()[1], DELEGATION_STATUSES)

    def test_a_resolved_adapter_is_always_one_the_project_permits(self) -> None:
        """The invariant that makes ``permits`` and ``delegation`` consistent.

        Task Core re-checks ``permits`` at create. If delegation could ever
        resolve to something outside ``adapters``, the two would disagree and the
        create would fail with a confusing refusal instead of a clear one.
        """
        cases = (
            _project(adapters=()),
            _project(adapters=(CLAUDE_CODE,)),
            _project(adapters=BOTH_TRANSPORTS),
            _project(adapters=BOTH_TRANSPORTS, delegated_adapter=AGENT_SDK),
            _project(adapters=(CLAUDE_CODE,), delegated_adapter=AGENT_SDK),
        )
        for entry in cases:
            resolved = entry.delegated_adapter_id
            if resolved is not None:
                self.assertTrue(entry.permits(resolved))


# -- the registry document ----------------------------------------------------


class RegistryDocumentTests(unittest.TestCase):
    """Loading ``task-projects.json``, including the two Gate B sandboxes."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.config_dir = Path(self._dir.name)
        self.root = self.config_dir / "a-real-directory"
        self.root.mkdir()

    def load(self, entries, known=BOTH_TRANSPORTS + (VALIDATION,)):
        (self.config_dir / "task-projects.json").write_text(
            json.dumps({"projects": entries}), encoding="utf-8"
        )
        config = dataclasses.make_dataclass("C", [("config_dir", Path)])(
            config_dir=self.config_dir
        )
        return load_projects(config, known)

    def entry(self, **kwargs):
        base = {
            "project_id": "sandbox",
            "display_name": "Sandbox",
            "root": str(self.root),
            "enabled": True,
        }
        base.update(kwargs)
        return base

    def test_the_two_gate_b_sandboxes_resolve_to_different_adapters(self) -> None:
        """Case 8 and case 9, as the deployed registry actually writes them.

        Each permits one adapter, so neither needs a ``delegated_adapter`` — and
        the point is that they resolve *differently* without one, because the
        answer comes from the project rather than from a global default.
        """
        registry = self.load(
            [
                self.entry(
                    project_id="claude-sandbox",
                    display_name="Claude adapter sandbox",
                    adapters=[CLAUDE_CODE],
                ),
                self.entry(
                    project_id="agent-sdk-sandbox",
                    display_name="Agent SDK sandbox",
                    adapters=[AGENT_SDK],
                    delegated_adapter=AGENT_SDK,
                ),
            ]
        )
        self.assertEqual(registry.problems, ())
        resolved = {
            entry.project_id: entry.delegated_adapter_id for entry in registry.projects
        }
        self.assertEqual(
            resolved, {"claude-sandbox": CLAUDE_CODE, "agent-sdk-sandbox": AGENT_SDK}
        )

    def test_an_unregistered_delegated_adapter_fails_closed(self) -> None:
        """Case 4. The build knows only Claude Code; the entry asks for the SDK.

        The project survives on purpose. It stays visible with a status saying
        why it cannot take work, which is a far better diagnostic than a project
        that silently disappeared — and no task runs either way.
        """
        registry = self.load(
            [self.entry(adapters=[CLAUDE_CODE, AGENT_SDK], delegated_adapter=AGENT_SDK)],
            known=(CLAUDE_CODE,),
        )
        entry = registry.get("sandbox")
        self.assertEqual(entry.adapters, (CLAUDE_CODE,))
        self.assertEqual(entry.delegation(), (None, DELEGATION_UNAVAILABLE))

    def test_a_disabled_adapter_is_the_same_case_as_an_unregistered_one(self) -> None:
        """Case 5. There is no third state between them, and that is the design.

        ``build_registry`` constructs an adapter only when the host enabled it,
        so "disabled" is not a flag on a registered object — the object does not
        exist, and the id is not in ``known_adapters``. One mechanism, one
        refusal.
        """
        registry = self.load(
            [self.entry(adapters=[AGENT_SDK], delegated_adapter=AGENT_SDK)],
            known=(CLAUDE_CODE,),
        )
        self.assertEqual(registry.get("sandbox").delegation(), (None, DELEGATION_UNAVAILABLE))

    def test_a_malformed_delegated_adapter_rejects_the_entry(self) -> None:
        for bad in (7, [], ["claude-code"], "", "Claude Code", "x" * 200, {}):
            with self.subTest(value=bad):
                registry = self.load(
                    [self.entry(adapters=[CLAUDE_CODE], delegated_adapter=bad)]
                )
                self.assertEqual(registry.projects, ())
                self.assertEqual(len(registry.problems), 1)
                self.assertIn("delegated_adapter", registry.problems[0]["problem"])

    def test_the_projection_publishes_the_decision_and_never_the_path(self) -> None:
        registry = self.load(
            [self.entry(adapters=[CLAUDE_CODE, AGENT_SDK], delegated_adapter=CLAUDE_CODE)]
        )
        published = registry.to_dict()["projects"][0]
        self.assertEqual(published["delegated_adapter"], CLAUDE_CODE)
        self.assertEqual(published["delegation"], DELEGATION_OK)
        self.assertNotIn("root", published)
        self.assertNotIn(str(self.root), json.dumps(published))

    def test_an_ambiguous_project_is_published_with_its_reason(self) -> None:
        registry = self.load([self.entry(adapters=[CLAUDE_CODE, AGENT_SDK])])
        published = registry.to_dict()["projects"][0]
        self.assertIsNone(published["delegated_adapter"])
        self.assertEqual(published["delegation"], DELEGATION_AMBIGUOUS)


class RegistryOrderTests(RegistryDocumentTests):
    """Case 7. The same registry, written in every order, decides the same."""

    def test_the_delegated_result_is_independent_of_file_order(self) -> None:
        for order in ([CLAUDE_CODE, AGENT_SDK], [AGENT_SDK, CLAUDE_CODE]):
            for chosen in BOTH_TRANSPORTS:
                with self.subTest(order=order, chosen=chosen):
                    registry = self.load(
                        [self.entry(adapters=list(order), delegated_adapter=chosen)]
                    )
                    self.assertEqual(
                        registry.get("sandbox").delegated_adapter_id, chosen
                    )

    def test_reversing_the_project_list_changes_nothing(self) -> None:
        entries = [
            self.entry(project_id="one", adapters=[CLAUDE_CODE]),
            self.entry(
                project_id="two", adapters=[CLAUDE_CODE, AGENT_SDK], delegated_adapter=AGENT_SDK
            ),
        ]
        forward = self.load(entries)
        backward = self.load(list(reversed(entries)))
        self.assertEqual(
            {p.project_id: p.delegated_adapter_id for p in forward.projects},
            {p.project_id: p.delegated_adapter_id for p in backward.projects},
        )


# -- 6, and the bridge's half of 1..5, 7, 8, 9 --------------------------------


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class BridgeCase(unittest.TestCase):
    """One bridge in front of one fake daemon. No test methods of its own.

    Split from the tests below so that the ``listProjects`` class can share the
    fixture without inheriting — and re-running — every create test.
    """

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.config = dataclasses.replace(
            load_bridge_config(Path(self._home.name)),
            rate_limit_per_minute=100000,
            rate_limit_burst=100000,
            mutation_rate_limit_per_minute=100000,
            mutation_rate_limit_burst=100000,
        )
        self.upstream = FakeInternalClient()
        self.app = create_bridge_app(
            self.config, external_key=KEY, internal_client=self.upstream
        )
        self.addCleanup(self.app.state.idempotency.close)
        self.client = TestClient(self.app)
        self.headers = {"Authorization": "Bearer " + KEY}
        self._n = 0

    def set_projects(self, *entries) -> None:
        self.upstream.projects_payload = {"projects": list(entries)}

    def create(self, **overrides):
        self._n += 1
        body = {
            "project_id": PROJECT_ID,
            "task_text": "Say hello.",
            "client_request_id": f"delegation-test-{self._n:04d}",
        }
        body.update(overrides)
        return self.client.post("/v1/tasks", headers=self.headers, json=body)

    def sent_adapter(self):
        _, kwargs = self.upstream.calls[-1]
        return kwargs["adapter_id"]


class BridgeDelegationTests(BridgeCase):
    """The Actions bridge reads the decision and never makes one."""

    def test_the_bridge_vocabulary_matches_task_cores(self) -> None:
        """Two processes, one closed set. A drift here is invisible at runtime.

        The bridge imports no workstation code by design, so the four words are
        written down twice. This is the assertion that keeps the second copy
        honest.
        """
        self.assertEqual(bridge_normalize.DELEGATIONS, DELEGATION_STATUSES)

    def test_an_explicit_delegation_is_what_gets_sent(self) -> None:
        """Case 2, end to end."""
        for chosen in BOTH_TRANSPORTS:
            with self.subTest(chosen=chosen):
                self.set_projects(
                    project(
                        adapters=list(BOTH_TRANSPORTS),
                        delegated_adapter=chosen,
                        delegation=DELEGATION_OK,
                    )
                )
                self.assertEqual(self.create().status_code, 201)
                self.assertEqual(self.sent_adapter(), chosen)

    def test_a_single_adapter_project_still_works(self) -> None:
        """Case 1, end to end — the shape ``claude-sandbox`` has today."""
        self.set_projects(project(adapters=[CLAUDE_CODE]))
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.sent_adapter(), CLAUDE_CODE)

    def test_an_ambiguous_project_is_refused_and_reaches_no_upstream_create(self) -> None:
        """Case 3, end to end. Refused *before* anything is started."""
        self.set_projects(
            project(
                adapters=list(BOTH_TRANSPORTS),
                delegated_adapter=None,
                delegation=DELEGATION_AMBIGUOUS,
            )
        )
        response = self.create()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "project_not_eligible")
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_an_unavailable_delegation_is_refused(self) -> None:
        """Cases 4 and 5, end to end."""
        self.set_projects(
            project(
                adapters=[CLAUDE_CODE],
                delegated_adapter=None,
                delegation=DELEGATION_UNAVAILABLE,
            )
        )
        response = self.create()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "project_not_eligible")
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_project_with_no_adapter_is_not_found_rather_than_ineligible(self) -> None:
        """The two refusals are different situations and say so.

        ``no_adapter`` never appears in ``listProjects``, so a caller naming one
        guessed an id — ``not_found`` is the honest answer. ``ambiguous_adapter``
        is a real project needing an edit on the workstation, and telling the
        model "no such project" would send somebody hunting a typo that is not
        in their message.
        """
        self.set_projects(project(adapters=[]))
        response = self.create()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_payload_without_a_delegated_adapter_field_fails_closed(self) -> None:
        """Version skew: a daemon older than this bridge. Refuse, do not guess.

        The old bridge would have taken ``adapters[0]`` here. The whole point is
        that there is no such path any more, so an unexpected payload produces a
        refusal rather than a task under an adapter nobody chose.
        """
        stale = project(adapters=list(BOTH_TRANSPORTS))
        stale.pop("delegated_adapter")
        stale.pop("delegation")
        self.set_projects(stale)
        self.assertEqual(self.create().status_code, 404)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_no_external_request_can_name_an_adapter(self) -> None:
        """Case 6. A closed schema, so these are refusals rather than ignores."""
        for field, value in (
            ("adapter_id", CLAUDE_CODE),
            ("adapter", CLAUDE_CODE),
            ("delegated_adapter", CLAUDE_CODE),
            ("model", "claude-opus-4"),
            ("provider", "anthropic"),
            ("permission_mode", "bypassPermissions"),
            ("max_turns", 999),
            ("max_budget_usd", 100),
            ("tools", ["Bash"]),
            ("cwd", "/etc"),
        ):
            with self.subTest(field=field):
                self.set_projects(project(adapters=[CLAUDE_CODE]))
                response = self.create(**{field: value})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_caller_cannot_reach_a_second_permitted_adapter_by_asking(self) -> None:
        """The sharpest form of case 6: both are permitted, one is delegated.

        A caller naming the *other* permitted adapter is not a policy question —
        the field does not exist, so the request never becomes a create at all.
        """
        self.set_projects(
            project(
                adapters=list(BOTH_TRANSPORTS),
                delegated_adapter=CLAUDE_CODE,
                delegation=DELEGATION_OK,
            )
        )
        self.assertEqual(self.create(adapter_id=AGENT_SDK).status_code, 422)
        self.assertEqual(self.upstream.called("create_task"), 0)
        # And the ordinary create still runs the delegated one.
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.sent_adapter(), CLAUDE_CODE)

    def test_the_bridge_re_reads_the_registry_on_every_create(self) -> None:
        """The decision is read at use, not cached from start-up.

        An operator who fixes an ambiguous project should not have to restart
        the bridge for the fix to take effect.
        """
        self.set_projects(
            project(
                adapters=list(BOTH_TRANSPORTS),
                delegated_adapter=None,
                delegation=DELEGATION_AMBIGUOUS,
            )
        )
        self.assertEqual(self.create().status_code, 409)
        self.set_projects(
            project(
                adapters=list(BOTH_TRANSPORTS),
                delegated_adapter=AGENT_SDK,
                delegation=DELEGATION_OK,
            )
        )
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.sent_adapter(), AGENT_SDK)


class ListProjectsDelegationTests(BridgeCase):
    """What ``listProjects`` says, and what it deliberately does not add."""

    def projects(self):
        response = self.client.get("/v1/projects", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()["projects"]

    def test_the_published_fields_are_unchanged(self) -> None:
        """No new field, so no OpenAPI change, so no Custom GPT edit.

        ``Project`` is ``additionalProperties: false`` in the published schema.
        A ``delegated_adapter`` key here would be a contract break for a
        cosmetic gain — the resolved adapter is already visible as
        ``task_adapters`` on a project that permits one, and a model has nothing
        to do with the answer on a project that permits several.
        """
        self.set_projects(project(adapters=[CLAUDE_CODE]))
        self.assertEqual(
            sorted(self.projects()[0]),
            ["accepts_tasks", "display_name", "enabled", "project_id", "task_adapters"],
        )

    def test_the_gate_b_sandboxes_appear_with_their_own_adapters(self) -> None:
        self.set_projects(
            project(project_id="claude-sandbox", adapters=[CLAUDE_CODE]),
            project(project_id="agent-sdk-sandbox", adapters=[AGENT_SDK]),
        )
        published = {p["project_id"]: p for p in self.projects()}
        self.assertEqual(published["claude-sandbox"]["task_adapters"], [CLAUDE_CODE])
        self.assertEqual(published["agent-sdk-sandbox"]["task_adapters"], [AGENT_SDK])
        self.assertTrue(all(p["accepts_tasks"] for p in published.values()))

    def test_an_ambiguous_project_is_not_offered_at_all(self) -> None:
        """``accepts_tasks`` is resolvability now, not merely eligibility.

        Before Gate B it asked "does this project permit any adapter", which was
        the same question only while every project permitted one. Offering a
        model a project whose every create fails is offering it a loop.
        """
        self.set_projects(
            project(
                project_id="ambiguous",
                adapters=list(BOTH_TRANSPORTS),
                delegated_adapter=None,
                delegation=DELEGATION_AMBIGUOUS,
            ),
            project(project_id="fine", adapters=[CLAUDE_CODE]),
        )
        self.assertEqual([p["project_id"] for p in self.projects()], ["fine"])

    def test_no_delegation_status_or_path_leaks_into_the_response(self) -> None:
        self.set_projects(
            project(
                adapters=list(BOTH_TRANSPORTS),
                delegated_adapter=AGENT_SDK,
                delegation=DELEGATION_OK,
            )
        )
        body = self.client.get("/v1/projects", headers=self.headers).text
        for forbidden in (
            "delegated_adapter",
            "delegation",
            DELEGATION_AMBIGUOUS,
            "/home/",
            "internal note",
        ):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
