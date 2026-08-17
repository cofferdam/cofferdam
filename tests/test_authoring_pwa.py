"""M2K PR24 — the PWA as the first human author of requirements.

PR23 opened `POST /api/tasks` and `POST /api/tasks/{id}/followups` to explicit
`criteria` and `continuity`, for the device caller only, and said plainly that no
human-facing caller used it. This is that caller.

Behavioural, run through ``tests/tasks_harness.js`` against the shipped
``web/tasks.js`` — so what is asserted here is what a person operating the panel
actually causes to be sent. A test that read the source could be satisfied by a
function nothing calls.

**The property the whole file exists for: omission is never inference, at either
end.** The backend refuses to turn an absent declaration into `root` or `extend`,
and PR23 pinned that. A client that guessed on its behalf would destroy the same
property from the other side while looking like a usability improvement, and it
would be invisible to every backend test. So the requests are inspected: a form
nobody touched sends no `continuity` key at all, and there is no input to the
panel that produces one without somebody having chosen it.

**Explicit empty is not omission.** An explicit `root` with an empty composer
sends `criteria: []`, which the server records as `not_provided` against a real
declaration — and which reads afterwards as `no_structured_criteria` rather than
as nobody having declared anything. Those are different sentences about different
situations and the wire has to keep them apart.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
TASKS_HARNESS = REPO_ROOT / "tests" / "tasks_harness.js"

try:
    import fastapi
except ImportError:  # pragma: no cover - the extras are absent
    fastapi = None

from ._runtime_doubles import code_only  # noqa: E402


def panel(name: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(TASKS_HARNESS), name],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError("harness failed: " + completed.stderr[:800])
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    assert not payload.get("timerErrors"), payload.get("timerErrors")
    return payload


def tasks_code() -> str:
    return code_only((WEB_DIR / "tasks.js").read_text(encoding="utf-8"))


def only_body(name: str) -> dict:
    """The single request one scenario produced, refusing to guess if there are
    two. A scenario that sent twice is a scenario whose assertion about "the
    request" means nothing."""
    payload = panel(name)
    posts = payload["posts"]
    assert len(posts) == 1, f"expected one request, got {len(posts)}"
    return posts[0]["body"]


# -- omission -----------------------------------------------------------------


class OmissionIsNeverInference(unittest.TestCase):
    """The rule PR10 established, PR23 pinned, and a client could still break."""

    def test_an_untouched_create_form_declares_nothing(self):
        body = only_body("authoring-create-not-declared")
        self.assertNotIn("continuity", body)
        self.assertNotIn("criteria", body)

    def test_a_first_turn_is_not_silently_rooted(self):
        """The temptation this closes: turn one obviously has no predecessor, so
        `root` looks like a safe default. It is not — it is a durable claim that
        a requirement lineage starts here, and nobody made it."""
        body = only_body("authoring-create-not-declared")
        self.assertNotIn("root", json.dumps(body))

    def test_an_untouched_follow_up_form_declares_nothing(self):
        body = only_body("authoring-followup-not-declared")
        self.assertNotIn("continuity", body)
        self.assertNotIn("criteria", body)

    def test_a_follow_up_is_not_silently_extended(self):
        """And the mirror temptation: a predecessor exists, so `extend` looks
        harmless. It would silently carry every prior requirement forward."""
        body = only_body("authoring-followup-not-declared")
        self.assertNotIn("extend", json.dumps(body))

    def test_the_untouched_request_is_the_pre_pr24_request(self):
        """The compatibility claim, checked rather than asserted in prose: a
        person who never looks at the new control sends exactly the four fields
        this panel has always sent."""
        body = only_body("authoring-create-not-declared")
        self.assertEqual(
            sorted(body),
            ["adapter_id", "client_request_id", "project_id", "prompt"],
        )

    def test_no_source_branch_turns_absence_into_a_mode(self):
        """From the source as well as from behaviour, because a default that
        only fires on a path no scenario exercises is still a default."""
        code = tasks_code()
        for pattern in (
            r"mode\s*[:=]\s*[\"']root[\"']\s*[;,)]",
            r"mode\s*[:=]\s*[\"']extend[\"']\s*[;,)]",
        ):
            self.assertIsNone(
                re.search(pattern, code),
                f"a continuity mode is assigned as a literal default: {pattern}",
            )

    def test_the_form_starts_in_the_non_authoritative_state(self):
        """A visible default a person can see and change is not a hidden server
        default — but only if it is the backwards-compatible one. `not declared`
        is checked on arrival; nothing else is."""
        html = panel("authoring-create-offers-only-first-turn-modes")["html"]
        checked = re.findall(r'id="(taskAuth\w+)"[^>]*checked', html)
        self.assertEqual(checked, ["taskAuthCreateNotDeclared"])


# -- explicit empty -----------------------------------------------------------


class ExplicitEmptyIsNotOmission(unittest.TestCase):
    def test_an_explicit_root_with_no_requirements_sends_an_empty_list(self):
        body = only_body("authoring-create-root-with-no-criteria")
        self.assertEqual(body["continuity"], {"mode": "root"})
        self.assertEqual(body["criteria"], [])

    def test_the_two_shapes_are_mechanically_distinguishable(self):
        """The distinction stated as the only thing that matters about it: an
        absent key and an empty list are different bytes on the wire."""
        declared = only_body("authoring-create-root-with-no-criteria")
        omitted = only_body("authoring-create-not-declared")
        self.assertIn("criteria", declared)
        self.assertNotIn("criteria", omitted)

    def test_requirements_may_be_declared_without_a_lineage(self):
        """The fields are independent on the wire and the panel does not couple
        them — no `root` is invented to carry a criteria set."""
        body = only_body("authoring-create-criteria-without-a-declaration")
        self.assertNotIn("continuity", body)
        self.assertEqual(len(body["criteria"]), 1)


# -- the modes offered --------------------------------------------------------


class ModesOffered(unittest.TestCase):
    def test_a_first_turn_offers_only_not_declared_and_root(self):
        """`extend`, `replace` and `revise` all require a
        `predecessor_snapshot_id` that a first turn does not have, so offering
        them would offer controls whose only outcome is a refusal."""
        result = panel("authoring-create-offers-only-first-turn-modes")
        self.assertTrue(result["hasNotDeclared"])
        self.assertTrue(result["hasRoot"])
        self.assertFalse(result["hasExtend"])
        self.assertFalse(result["hasReplace"])
        self.assertFalse(result["hasRevise"])

    def test_a_follow_up_offers_not_declared_extend_and_replace(self):
        result = panel("authoring-followup-offers-no-root")
        self.assertTrue(result["hasNotDeclared"])
        self.assertTrue(result["hasExtend"])
        self.assertTrue(result["hasReplace"])
        self.assertFalse(result["hasRoot"])

    def test_revise_is_absent_and_said_so(self):
        """Named rather than left as a gap somebody reads as an oversight. The
        authority for it cannot be represented without a read surface that does
        not exist — see the module docstring of the backend's `continuity`."""
        result = panel("authoring-followup-offers-no-root")
        self.assertFalse(result["hasRevise"])
        self.assertIn("task-continuity-absent", result["html"])

    def test_every_mode_says_what_it_does_before_submit(self):
        """The consequence is legible on the form, not in documentation."""
        html = panel("authoring-followup-offers-no-root")["html"]
        self.assertIn("will not infer requirement continuity", html)
        self.assertIn("Keep the requirements already active", html)
        self.assertIn("Replace the active requirement set", html)


# -- the composer -------------------------------------------------------------


class CriteriaComposer(unittest.TestCase):
    def test_all_six_shapes_can_be_authored(self):
        body = only_body("authoring-every-predicate")
        self.assertEqual(
            [item.get("predicate") for item in body["criteria"]],
            [
                "path_changed", "path_operation", "rename",
                None, "path_exists", "path_absent", "path_operation",
            ],
        )
        self.assertEqual(
            [item["kind"] for item in body["criteria"]],
            ["evidence"] * 3 + ["manual"] + ["evidence"] * 3,
        )

    def test_each_shape_carries_exactly_its_own_fields(self):
        body = only_body("authoring-every-predicate")
        by_predicate = {
            item.get("predicate"): item for item in body["criteria"]
            if item["kind"] == "evidence"
        }
        self.assertEqual(
            sorted(by_predicate["path_changed"]), ["kind", "path", "predicate"]
        )
        self.assertEqual(
            sorted(by_predicate["rename"]),
            ["kind", "path", "predicate", "to_path"],
        )
        self.assertEqual(
            sorted(by_predicate["path_exists"]), ["kind", "path", "predicate"]
        )
        self.assertEqual(
            sorted(by_predicate["path_absent"]), ["kind", "path", "predicate"]
        )
        manual = [i for i in body["criteria"] if i["kind"] == "manual"][0]
        self.assertEqual(sorted(manual), ["description", "kind"])

    def test_a_manual_criterion_carries_no_structured_field(self):
        """The backend refuses one that does, by name. Nothing here can build
        one: the manual row draws no path, destination or operation control."""
        seen = panel("authoring-predicate-fields-are-bounded-by-predicate")["seen"]
        self.assertEqual(
            seen["manual"],
            {"path": False, "to": False, "operation": False, "description": True},
        )

    def test_invalid_field_combinations_cannot_be_produced(self):
        """A row draws only the fields its predicate owns, so a destination on a
        `path_exists` or an operation on a `rename` is not something a person can
        type — a stronger guarantee than validating one away afterwards."""
        seen = panel("authoring-predicate-fields-are-bounded-by-predicate")["seen"]
        self.assertFalse(seen["evidence:path_exists"]["to"])
        self.assertFalse(seen["evidence:path_exists"]["operation"])
        self.assertFalse(seen["evidence:path_absent"]["operation"])
        self.assertFalse(seen["evidence:rename"]["operation"])
        self.assertFalse(seen["evidence:path_changed"]["to"])
        self.assertTrue(seen["evidence:rename"]["to"])
        self.assertTrue(seen["evidence:path_operation"]["operation"])

    def test_there_is_no_free_form_criterion_control(self):
        """No expression box, no JSON field, and nothing that could carry a
        command — the criteria vocabulary is closed and so is this composer.

        Scoped to the composer itself. Scanning the whole panel would sweep in
        the prompt box's own "never run as a command" sentence and turn a real
        guard into one that can only pass by deleting an honest explanation."""
        html = panel("authoring-every-predicate")["html"]
        composer = html[html.index("task-criteria"):html.index("task-new-actions")]
        for forbidden in ("command", "argv", "script", "shell", "check_id", "JSON"):
            self.assertNotIn(forbidden, composer)
        self.assertNotIn("textarea", composer)

    def test_order_is_insertion_order_and_survives_a_removal(self):
        """Ordinal is positional and part of the stored snapshot fingerprint, so
        the order sent is the order on screen. Nothing sorts by path or
        predicate on the way out — `zebra` before `mango` is the proof."""
        body = only_body("authoring-rows-keep-their-order")
        self.assertEqual(
            [item["path"] for item in body["criteria"]], ["zebra.txt", "mango.txt"]
        )


class NoSemanticConversion(unittest.TestCase):
    """An action is not a state, and the panel never quietly turns one into the
    other. `created` stays `path_operation`; a person who wants `path_exists`
    chooses `path_exists`."""

    def test_a_path_operation_stays_a_path_operation(self):
        body = only_body("authoring-every-predicate")
        operations = [
            item for item in body["criteria"]
            if item.get("predicate") == "path_operation"
        ]
        self.assertEqual(
            [item["operation"] for item in operations], ["created", "deleted"]
        )

    def test_created_is_not_turned_into_path_exists(self):
        body = only_body("authoring-every-predicate")
        created = [
            item for item in body["criteria"]
            if item.get("operation") == "created"
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["predicate"], "path_operation")

    def test_deleted_is_not_turned_into_path_absent(self):
        body = only_body("authoring-every-predicate")
        absent = [
            item for item in body["criteria"]
            if item.get("predicate") == "path_absent"
        ]
        self.assertEqual(len(absent), 1)
        self.assertNotIn("operation", absent[0])

    def test_no_conversion_table_exists_in_the_source(self):
        code = tasks_code()
        self.assertIsNone(re.search(r"created[\"']?\s*[:=>]+\s*[\"']path_exists", code))
        self.assertIsNone(re.search(r"deleted[\"']?\s*[:=>]+\s*[\"']path_absent", code))


# -- continuity on a follow-up ------------------------------------------------


class FollowUpContinuity(unittest.TestCase):
    def test_extend_sends_extend_and_an_anchor(self):
        body = only_body("authoring-followup-extend")
        self.assertEqual(body["continuity"]["mode"], "extend")
        self.assertTrue(
            body["continuity"]["predecessor_snapshot_id"].startswith("acs_")
        )

    def test_extend_sends_only_this_turns_new_requirements(self):
        """The inherited set survives *through continuity*. Copying it into the
        new snapshot would mint fresh criterion identities for requirements that
        already have them, and the lineage would be destroyed while looking
        complete."""
        body = only_body("authoring-followup-extend")
        self.assertEqual(len(body["criteria"]), 1)
        self.assertEqual(body["criteria"][0]["path"], "b.txt")

    def test_no_inherited_criterion_is_copied_into_the_composer(self):
        """The predecessor's four criteria are on screen in the assessment
        section and in none of them is a composer row."""
        result = panel("authoring-followup-extend")
        body = result["posts"][0]["body"]
        sent = {item.get("path") for item in body["criteria"]}
        for inherited in ("src/app.py", "src/gone.py", "src/old.py"):
            self.assertNotIn(inherited, sent)

    def test_replace_sends_replace_and_an_anchor(self):
        body = only_body("authoring-followup-replace")
        self.assertEqual(body["continuity"]["mode"], "replace")
        self.assertTrue(
            body["continuity"]["predecessor_snapshot_id"].startswith("acs_")
        )

    def test_replace_carries_the_replacement_set_only(self):
        body = only_body("authoring-followup-replace")
        self.assertEqual(
            [item["path"] for item in body["criteria"]], ["replacement.txt"]
        )


class TheAnchorIsReadNeverTyped(unittest.TestCase):
    """The property that decides whether this is a user interface at all.

    `extend` and `replace` name a `predecessor_snapshot_id`. It reaches the
    request from two routes that already existed and that the device token
    already reached — the result route for how many turns there have been, the
    assessment route for that turn's snapshot id. No new backend surface, and no
    identifier typed by a person.
    """

    def test_the_snapshot_id_reaches_the_request_without_a_control_for_it(self):
        result = panel("authoring-anchor-is-read-not-typed")
        self.assertEqual(result["identityInputs"], [])
        body = result["posts"][0]["body"]
        self.assertEqual(
            body["continuity"]["predecessor_snapshot_id"],
            "acs_" + "a" * 26,
        )

    def test_the_anchor_is_read_from_the_existing_private_routes(self):
        paths = [
            request["path"] for request in
            panel("authoring-anchor-is-read-not-typed")["requests"]
        ]
        self.assertTrue(any(path.endswith("/result") for path in paths))
        self.assertTrue(any("/assessment" in path for path in paths))

    def test_no_new_route_is_asked_for(self):
        """PR24 adds no backend read surface. Every path the panel touches is one
        that already existed before it."""
        known = (
            "/api/task-adapters", "/api/task-projects", "/api/tasks",
        )
        for request in panel("authoring-anchor-is-read-not-typed")["requests"]:
            path = request["path"]
            self.assertTrue(
                path.startswith(known),
                f"the panel reached an unexpected path: {path}",
            )

    def test_the_anchor_is_visible_before_submit(self):
        html = panel("authoring-anchor-is-read-not-typed")["htmlBeforeSubmit"]
        self.assertIn("task-continuity-anchor", html)
        self.assertIn("Continuing from turn", html)

    def test_a_turn_with_no_snapshot_cannot_be_continued_from(self):
        """A `legacy_unknown` turn publishes no snapshot id. There is nothing to
        anchor to, so nothing is sent — rather than a declaration with a missing
        field, or one pointed at some other turn."""
        result = panel("authoring-legacy-turn-cannot-be-continued")
        self.assertEqual(result["posts"], [])
        self.assertIn("no recorded requirements to continue from", result["html"])

    def test_a_declaration_does_not_cross_tasks(self):
        result = panel("authoring-declaration-does-not-cross-tasks")
        self.assertFalse(result["rowsOnSecondTask"])


# -- refusals -----------------------------------------------------------------


class RefusalsAreDistinguishable(unittest.TestCase):
    """Four refusals, four different things for a person to do next. Collapsing
    them into one "that was refused" is what makes a form feel arbitrary."""

    def kind(self, name: str) -> str:
        html = panel(name)["html"]
        match = re.search(r'data-refusal="(\w+)"', html)
        assert match, "no authoring refusal was rendered"
        return match.group(1)

    def test_invalid_criteria_read_as_a_requirement_problem(self):
        self.assertEqual(self.kind("authoring-criteria-refusal"), "criteria")

    def test_invalid_continuity_reads_as_a_tracking_problem(self):
        self.assertEqual(self.kind("authoring-continuity-refusal"), "continuity")

    def test_a_stale_anchor_reads_as_the_task_having_moved(self):
        """Not as the person having got something wrong: they did not. Another
        caller created a turn while the form was open."""
        self.assertEqual(self.kind("authoring-stale-anchor-refusal"), "stale")

    def test_the_closed_reason_code_is_preserved(self):
        html = panel("authoring-criteria-refusal")["html"]
        self.assertIn("criterion_path_invalid", html)

    def test_the_code_is_behind_the_advanced_disclosure(self):
        """A closed reason code is what makes a report actionable and is not the
        headline. The panel's existing Advanced convention holds it."""
        html = panel("authoring-criteria-refusal")["html"]
        block = re.search(
            r'<div class="media-note err task-authoring-error".*?</div>', html, re.S
        ).group(0)
        self.assertIn("task-advanced", block)
        self.assertLess(
            block.index("</strong>"), block.index("criterion_path_invalid")
        )

    def test_nothing_leaks_through_a_refusal(self):
        for name in (
            "authoring-criteria-refusal",
            "authoring-continuity-refusal",
            "authoring-stale-anchor-refusal",
        ):
            html = panel(name)["html"]
            for forbidden in (
                "Traceback", "SELECT ", "sqlite", "/home/", "File \"",
                ".py\", line", "Exception(",
            ):
                self.assertNotIn(forbidden, html, f"{forbidden!r} leaked in {name}")


class NoAutoRetryWithModifiedAuthority(unittest.TestCase):
    """A refused declaration is never re-sent as a weaker one. `revise` does not
    fall back to `extend`, and `root` does not fall back to omitting continuity —
    an authority change requires another human action."""

    def test_a_refusal_produces_exactly_one_request(self):
        for name in (
            "authoring-criteria-refusal",
            "authoring-continuity-refusal",
            "authoring-stale-anchor-refusal",
        ):
            self.assertEqual(len(panel(name)["posts"]), 1, name)

    def test_the_declaration_is_not_rewritten_after_a_refusal(self):
        result = panel("authoring-stale-anchor-refusal")
        self.assertEqual(result["posts"][0]["body"]["continuity"]["mode"], "root")
        self.assertTrue(result["html"].count('id="taskAuthCreateRoot"'))

    def test_the_composer_survives_so_it_can_be_corrected(self):
        result = panel("authoring-refusal-keeps-the-composer")
        self.assertTrue(result["rowStillThere"])
        self.assertEqual(result["pathStillThere"], "a.txt")
        self.assertTrue(result["modeStillRoot"])


# -- idempotency --------------------------------------------------------------


class DoubleSubmitSafety(unittest.TestCase):
    def test_a_double_tap_with_a_declaration_sends_one_request(self):
        self.assertEqual(len(panel("authoring-double-tap-sends-one-request")["posts"]), 1)

    def test_a_retry_of_the_same_declaration_reuses_one_key(self):
        """The key is a function of what was authored, never a fresh value per
        attempt — a new key on every press is how a timeout becomes two turns."""
        ids = panel("authoring-retry-reuses-its-request-id")["requestIds"]
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])

    def test_an_edited_declaration_gets_a_new_key(self):
        """And the other half: the server binds a key to a payload hash, so a
        changed requirement under the old key would be answered as a conflict
        rather than as the different request it is."""
        ids = panel("authoring-an-edited-declaration-gets-a-new-request-id")["requestIds"]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])


class OneSubmissionCarriesEverything(unittest.TestCase):
    """No create-then-patch sequence. A turn whose requirements arrive after it
    started is a turn judged against a moving target, which is the whole reason
    criteria are a pre-dispatch fact."""

    def test_the_declaration_travels_with_the_dispatch(self):
        result = panel("authoring-create-root-with-state-criterion")
        self.assertEqual(len(result["posts"]), 1)
        body = result["posts"][0]["body"]
        self.assertIn("prompt", body)
        self.assertIn("criteria", body)
        self.assertIn("continuity", body)

    def test_no_patch_route_is_ever_called(self):
        code = tasks_code()
        for verb in ("PATCH", "PUT", "DELETE"):
            self.assertNotIn(f'method: "{verb}"', code)
        self.assertNotIn("/criteria", code)
        self.assertNotIn("/continuity", code)


class NoPostDispatchEditing(unittest.TestCase):
    """Historical requirements are immutable. A follow-up turn changes them
    through continuity, and there is no other affordance."""

    def test_the_assessment_view_offers_no_edit_control(self):
        html = panel("authoring-reaches-acceptance")["html"]
        section = html[html.index("task-assessment"):]
        for word in ("Edit", "Change requirement", "Update criteria", "Re-run"):
            self.assertNotIn(word, section)

    def test_the_composer_is_never_rendered_for_a_completed_turn(self):
        """The authoring block belongs to a form that dispatches something. A
        completed task has no such form, so there is nothing to edit through."""
        html = panel("authoring-reaches-acceptance")["html"]
        self.assertNotIn("task-authoring", html)


# -- the loop -----------------------------------------------------------------


class HumanReachesAcceptance(unittest.TestCase):
    """The milestone's claim, end to end through the shipped request layer:
    author, dispatch, and read the answer — with no curl, no raw JSON, no raw id
    and no terminal anywhere in the path."""

    def test_a_declared_root_reaches_the_acceptance_section(self):
        result = panel("authoring-reaches-acceptance")
        body = result["posts"][0]["body"]
        self.assertEqual(body["continuity"], {"mode": "root"})
        self.assertEqual(
            body["criteria"],
            [{"kind": "evidence", "predicate": "path_exists", "path": "a.txt"}],
        )
        self.assertIn("task-acceptance", result["html"])

    def test_the_acceptance_section_is_the_existing_one(self):
        """No second acceptance viewer was built. What answers is PR22's
        section, reached through PR22's button."""
        html = panel("authoring-reaches-acceptance")["html"]
        self.assertIn("Acceptance at this turn", html)
        self.assertEqual(len(re.findall(r'class="task-acceptance"', html)), 1)

    def test_no_global_verdict_appears(self):
        html = panel("authoring-reaches-acceptance")["html"]
        for forbidden in (
            "task passed", "project passed", "ready to merge", "ready to deploy",
        ):
            self.assertNotIn(forbidden, html.lower())


# -- narrow layout ------------------------------------------------------------


class NarrowLayout(unittest.TestCase):
    """The panel is used on a phone. A requirement row that overflows sideways
    hides the control on its right, which here is Remove — and a control you
    cannot reach is worse than one that is not there."""

    def setUp(self):
        self.css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    def test_the_authoring_controls_have_a_narrow_screen_layout(self):
        narrow = self.css.split("@media (max-width: 480px)")[-1]
        self.assertIn(".task-criterion-head", narrow)
        self.assertIn(".task-criterion-remove", narrow)
        self.assertIn(".task-continuity-mode", narrow)

    def test_every_authoring_container_can_shrink(self):
        """`min-width: 0` on each, because a grid or flex child defaults to
        `min-width: auto` and a 512-character path would widen the page rather
        than wrap inside it."""
        for selector in (
            ".task-authoring", ".task-continuity", ".task-criteria",
            ".task-criterion", ".task-criterion-head", ".task-criteria-list",
        ):
            block = self.css.split(selector + " {")[1].split("}")[0]
            self.assertIn("min-width: 0", block, selector)

    def test_a_path_input_cannot_exceed_its_container(self):
        block = self.css.split(".task-criterion-field input {")[1].split("}")[0]
        self.assertIn("max-width: 100%", block)
        self.assertIn("box-sizing: border-box", block)

    def test_the_touch_targets_are_reachable(self):
        for selector in (".task-criterion-remove", ".task-criteria-actions button"):
            block = self.css.split(selector + " {")[1].split("}")[0]
            self.assertIn("min-height: 44px", block)


# -- negative space -----------------------------------------------------------


class NegativeSpace(unittest.TestCase):
    """What PR24 did not do, asserted from the artefacts rather than from prose."""

    def test_the_schema_version_is_unchanged(self):
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, 11)

    def test_no_semantic_version_moved(self):
        from cofferdam.workstation.tasks.acceptance import AGGREGATOR_VERSION
        from cofferdam.workstation.tasks.assessment import ASSESSMENT_API_VERSION
        from cofferdam.workstation.tasks.criteria import CRITERIA_MODEL_VERSION
        from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION

        self.assertEqual(EVALUATOR_VERSION, 1)
        self.assertEqual(AGGREGATOR_VERSION, 1)
        self.assertEqual(CRITERIA_MODEL_VERSION, 1)
        self.assertEqual(ASSESSMENT_API_VERSION, 1)

    def test_no_backend_file_changed(self):
        """PR24 is a client. If this ever fails, the authority boundary moved and
        the change belongs in a PR that says so."""
        import subprocess as sp

        changed = sp.run(
            ["git", "diff", "--name-only", "a1dfd23b", "--"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        ).stdout.split()
        if not changed:  # pragma: no cover - depends on the checkout
            raise unittest.SkipTest("not a git checkout with the base commit")
        for name in changed:
            self.assertFalse(
                name.startswith("cofferdam/"),
                f"a production backend file changed: {name}",
            )

    def test_a_requirement_row_can_only_come_from_the_add_control(self):
        """No model-generated criteria, no suggested continuity, no requirement
        extracted from prose. Those are later host-planner capabilities and each
        needs its own authority decision.

        Asserted structurally rather than by scanning for words: the *only*
        thing that ever appends to a composer is `newRow()`, and the only thing
        that ever builds a row list is an empty one. A response payload has
        nowhere to become a requirement."""
        code = tasks_code()
        appends = re.findall(r"\.rows\.push\(([^)]*\)?)\)", code)
        self.assertEqual(appends, ["newRow()"])
        assignments = re.findall(r"rows:\s*([^,\n]+)", code)
        self.assertEqual(assignments, ["[]"])

    def test_no_continuity_mode_is_ever_read_from_a_response(self):
        """The mirror property for the declaration: a mode reaches state only
        through `selectMode`, which is reached only from a change handler."""
        code = tasks_code()
        writers = re.findall(r"(\w+)\.mode\s*=", code)
        self.assertEqual(sorted(set(writers)), ["state"])

    def test_the_panel_names_no_named_check_or_runner(self):
        """A criterion that names a check is a later milestone's decision and a
        criterion that carries a command is one the model refuses outright."""
        code = tasks_code().lower()
        for forbidden in (
            "check_id", "named_check", "runner", "spawn", "argv", "shell",
        ):
            self.assertNotIn(forbidden, code)


class BridgeNegativeSpace(unittest.TestCase):
    """The Actions Bridge gained nothing. PR23 made the authoring field list
    per-caller precisely so this stays true without the bridge being touched."""

    def test_no_bridge_source_mentions_authoring(self):
        root = REPO_ROOT / "cofferdam" / "actions_bridge"
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("criteria", "continuity", "supersede", "predecessor"):
                self.assertNotIn(
                    forbidden, source,
                    f"{path.name} mentions {forbidden}",
                )

    @unittest.skipIf(fastapi is None, "FastAPI is not installed (workstation extra)")
    def test_the_bridge_operation_set_is_unchanged(self):
        from cofferdam.actions_bridge.service import OPERATION_IDS

        self.assertNotIn("authorCriteria", OPERATION_IDS)
        for name in OPERATION_IDS:
            self.assertNotIn("criteri", name.lower())
            self.assertNotIn("continuit", name.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
