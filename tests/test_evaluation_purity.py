"""M2K PR7 — proof that judging evidence touches nothing.

The claim being defended: an evaluation describes what was **recorded**, not what
the repository looks like now, and not what a worker says about itself. That
claim only holds if the evaluator cannot reach the world. If it could run
``git``, an evaluation re-derived a year later would judge a repository somebody
has since edited; if it could read a claim, a worker could satisfy its own
acceptance criteria by asserting it had.

Three layers, because each alone is weak. The **static** layer reads the module's
own source and its imports: a module that cannot import ``subprocess`` cannot
shell out. The **runtime** layer poisons the dangerous callables and evaluates a
real snapshot against a real bundle. The **authority** layer builds bundles whose
claims contradict their observations and asserts the results do not move.
"""

from __future__ import annotations

import ast
import builtins
import unittest
from pathlib import Path

from cofferdam.workstation.tasks import evaluation as evaluation_module
from cofferdam.workstation.tasks.claims import ChangeClaim
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_PRESENT,
    CriteriaSnapshot,
    validate_criteria,
)
from cofferdam.workstation.tasks.evaluation import (
    RESULT_MET,
    RESULT_NOT_MET,
    evaluate,
    evaluation_fingerprint,
)
from cofferdam.workstation.tasks.evidence import (
    OBSERVATION_DOMAIN_WORKTREE,
    IngestionSummary,
)

from .test_evaluation_predicates import bundle, observation

MODULE_PATH = Path(evaluation_module.__file__)

#: Anything that could reach the world. ``hashlib`` is fine — it is arithmetic.
#: ``time`` and ``secrets`` are forbidden as well, and that is the reason
#: ``new_evaluation_id`` lives in ``identity.py``: a clock and a random source are
#: exactly the two things that would let a deterministic judgement stop being one.
FORBIDDEN_IMPORTS = frozenset(
    {
        "subprocess", "socket", "shutil", "http", "urllib", "requests", "httpx",
        "asyncio", "sqlite3", "os", "time", "datetime", "tempfile", "glob",
        "pathlib", "multiprocessing", "threading", "ssl", "ftplib", "smtplib",
        "webbrowser", "ctypes", "pickle", "random", "secrets",
    }
)

FORBIDDEN_CALLS = frozenset(
    {
        "open", "exec", "eval", "compile", "__import__", "input", "system",
        "popen", "run", "spawn", "connect", "urlopen", "now", "today", "time",
        "monotonic",
    }
)


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


class StaticPurityTests(unittest.TestCase):
    def test_the_evaluator_imports_nothing_that_reaches_the_world(self):
        found = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        offenders = found & FORBIDDEN_IMPORTS
        self.assertEqual(offenders, set(), "forbidden imports: %s" % sorted(offenders))

    def test_the_evaluator_calls_nothing_that_reaches_the_world(self):
        offenders = set()
        for node in ast.walk(_tree()):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute) else None
            )
            if name in FORBIDDEN_CALLS:
                offenders.add(name)
        self.assertEqual(offenders, set(), "forbidden calls: %s" % sorted(offenders))

    def test_the_evaluator_holds_no_command_to_run(self):
        """Asked of the string constants via the AST, not of the file's text.

        The module's prose discusses Git and commands by necessity; a text search
        would either fail on the docstrings or be tuned until it meant nothing.
        """
        tree = _tree()
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                text = ast.get_docstring(node, clean=False)
                if text is not None:
                    docstrings.add(text)
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value not in docstrings
        ]
        for literal in literals:
            lowered = literal.lower()
            for forbidden in ("git ", "--", "/usr/", "/bin/", "sh -c", "pytest"):
                self.assertNotIn(forbidden, lowered, literal)

    def test_the_evaluator_writes_nothing(self):
        """No INSERT, UPDATE, DELETE or connection anywhere in the pure module."""
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        tree = _tree()
        literals = " ".join(
            node.value.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        for forbidden in ("insert into", "update ", "delete from", "commit()"):
            self.assertNotIn(forbidden, literals, forbidden)
        self.assertNotIn("def record_", source.split('"""', 2)[-1])

    def test_there_is_no_aggregate_or_verdict_function(self):
        names = {
            node.name
            for node in ast.walk(_tree())
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for forbidden in (
            "aggregate", "task_verdict", "verdict", "summarise", "summarize",
            "score", "confidence", "risk",
        ):
            self.assertNotIn(forbidden, names)
            for name in names:
                self.assertNotIn(forbidden, name, name)


class RuntimePurityTests(unittest.TestCase):
    """Poison the world, then evaluate for real."""

    def setUp(self):
        criteria = tuple(
            c.with_id("acr_%d" % c.ordinal)
            for c in validate_criteria(
                [
                    {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
                    {"kind": "evidence", "predicate": "path_changed", "path": "src/gone.py"},
                    {"kind": "manual", "description": "a person looks"},
                ]
            )
        )
        self.snapshot = CriteriaSnapshot(
            task_id="task_x",
            turn_number=1,
            state=CRITERIA_PRESENT,
            snapshot_id="acs_1",
            fingerprint="c" * 64,
            criterion_count=3,
            criteria=criteria,
        )
        self.bundle = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )

    def test_evaluation_runs_with_every_dangerous_callable_poisoned(self):
        import socket
        import subprocess
        import time

        def poison(*args, **kwargs):
            raise AssertionError("the evaluator reached the world")

        patches = [
            (builtins, "open", builtins.open),
            (subprocess, "run", subprocess.run),
            (subprocess, "Popen", subprocess.Popen),
            (socket, "socket", socket.socket),
            (time, "time", time.time),
        ]
        for owner, name, _ in patches:
            setattr(owner, name, poison)
        try:
            results = evaluate(self.snapshot, self.bundle)
            digest = evaluation_fingerprint(
                snapshot=self.snapshot, bundle=self.bundle, results=results
            )
        finally:
            for owner, name, original in patches:
                setattr(owner, name, original)
        self.assertEqual([r.result for r in results][:2], [RESULT_MET, RESULT_NOT_MET])
        self.assertEqual(len(digest), 64)

    def test_the_same_inputs_give_the_same_answer_every_time(self):
        answers = {
            tuple((r.criterion_id, r.result, r.reason) for r in evaluate(self.snapshot, self.bundle))
            for _ in range(25)
        }
        self.assertEqual(len(answers), 1)

    def test_the_fingerprint_is_stable_across_repeated_calls(self):
        results = evaluate(self.snapshot, self.bundle)
        digests = {
            evaluation_fingerprint(
                snapshot=self.snapshot, bundle=self.bundle, results=results
            )
            for _ in range(25)
        }
        self.assertEqual(len(digests), 1)

    def test_deleting_the_repository_cannot_change_anything(self):
        """There is no repository in the inputs — that is the whole point."""
        import inspect

        parameters = set(inspect.signature(evaluate).parameters)
        self.assertEqual(parameters, {"snapshot", "bundle"})
        for name in ("root", "path", "repository", "project_root", "cwd"):
            self.assertNotIn(name, parameters)


class MachineAuthorityTests(unittest.TestCase):
    """Claims cannot satisfy, cannot fail, and cannot break a tie."""

    def setUp(self):
        criteria = tuple(
            c.with_id("acr_%d" % c.ordinal)
            for c in validate_criteria(
                [{"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"}]
            )
        )
        self.snapshot = CriteriaSnapshot(
            task_id="task_x",
            turn_number=1,
            state=CRITERIA_PRESENT,
            snapshot_id="acs_1",
            fingerprint="c" * 64,
            criterion_count=1,
            criteria=criteria,
        )

    def claim(self, path, operation="modified"):
        return ChangeClaim(
            claim_id="chg_1", task_id="task_x", turn_number=1, operation=operation,
            path=path, to_path=None, adapter_label=None, reported_at="x",
            artifact_id=None, reason="ok",
        )

    def test_claims_do_not_change_any_result(self):
        seen = observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE)
        without = evaluate(self.snapshot, bundle(observations=(seen,)))
        with_claims = evaluate(
            self.snapshot,
            bundle(observations=(seen,), claims=(self.claim("src/elsewhere.py", "deleted"),)),
        )
        self.assertEqual(
            [(r.result, r.reason) for r in without],
            [(r.result, r.reason) for r in with_claims],
        )

    def test_a_claim_cannot_turn_a_not_met_into_a_met(self):
        empty = evaluate(self.snapshot, bundle())
        claimed = evaluate(self.snapshot, bundle(claims=(self.claim("src/a.py"),)))
        self.assertEqual(empty[0].result, RESULT_NOT_MET)
        self.assertEqual(claimed[0].result, RESULT_NOT_MET)

    def test_incomplete_claim_ingestion_does_not_downgrade_a_decided_criterion(self):
        """The PR6 readiness audit floated this; it is deliberately NOT the rule.

        Claims are not the truth source for these predicates, so their
        completeness cannot gate them. Only the evidence dimensions a predicate
        actually rests on may.
        """
        seen = observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE)
        complete = bundle(observations=(seen,))
        truncated = bundle(observations=(seen,))
        truncated = type(truncated)(
            **{
                **{f: getattr(truncated, f) for f in truncated.__dataclass_fields__},
                "ingestion": IngestionSummary(
                    state="incomplete", submitted=40, accepted=32, rejected=0, truncated=True
                ),
                "limitations": ("claim_set_incomplete", "claims_truncated"),
            }
        )
        self.assertEqual(
            [(r.result, r.reason) for r in evaluate(self.snapshot, complete)],
            [(r.result, r.reason) for r in evaluate(self.snapshot, truncated)],
        )
        self.assertEqual(evaluate(self.snapshot, truncated)[0].result, RESULT_MET)

    def test_the_evaluator_never_reads_the_claim_fields(self):
        """Structural: no attribute access to claims, ingestion or relationships."""
        attributes = {
            node.attr
            for node in ast.walk(_tree())
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("claims", "ingestion", "relationships"):
            self.assertNotIn(forbidden, attributes, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
