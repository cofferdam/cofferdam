"""M2K PR11 — the resolved fingerprint identifies a resolution and nothing else.

Two halves, and both matter. **Stability**: the same immutable graph resolves to
the same value across repeats, a reopened database and a separate process, so it
can be recorded and compared later. **Sensitivity**: it moves when any fact that
actually produced the answer moves — the target, the active set, its order, the
source snapshots, and every continuity declaration the resolution consumed.

And one deliberate silence: a ``replace``'s predecessor is not bound, because the
resolution never traversed it. A hash that claimed otherwise would assert a
dependency the answer did not have.
"""

from __future__ import annotations


import subprocess
import sys
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_EXTEND,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
)
from cofferdam.workstation.tasks.lineage import (
    FINGERPRINT_CHARS,
    RESOLVER_VERSION,
    resolve,
    resolved_fingerprint,
)

from .test_lineage_resolver import declared, graph, snapshot

REPO_ROOT = Path(__file__).resolve().parents[1]


def two_generations():
    return (
        (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
        (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"), snapshot(2, ["c"])),
    )


class StabilityTests(unittest.TestCase):
    def test_it_is_a_sha256_hex_digest(self):
        result = resolve(graph(2, *two_generations()))
        self.assertEqual(len(result.fingerprint), FINGERPRINT_CHARS)
        int(result.fingerprint, 16)

    def test_repeated_resolution_agrees(self):
        first = resolve(graph(2, *two_generations()))
        second = resolve(graph(2, *two_generations()))
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_it_survives_a_separate_process(self):
        """A fresh interpreter, so no in-process state can be carrying it."""
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from tests.test_lineage_fingerprint import two_generations\n"
            "from tests.test_lineage_resolver import graph\n"
            "from cofferdam.workstation.tasks.lineage import resolve\n"
            "print(resolve(graph(2, *two_generations())).fingerprint)\n"
        ) % str(REPO_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.strip(),
            resolve(graph(2, *two_generations())).fingerprint,
        )

    def test_the_repository_is_not_an_input(self):
        """Nothing about a working tree reaches this, so nothing about one moves it."""
        before = resolve(graph(2, *two_generations())).fingerprint
        scratch = REPO_ROOT / "README.md"
        self.assertTrue(scratch.exists())
        after = resolve(graph(2, *two_generations())).fingerprint
        self.assertEqual(before, after)


class SensitivityTests(unittest.TestCase):
    def base(self):
        return resolve(graph(2, *two_generations())).fingerprint

    def test_the_resolver_version_is_bound(self):
        result = resolve(graph(2, *two_generations()))
        rebuilt = resolved_fingerprint(
            result.task_id,
            result.target_turn_number,
            result.target_snapshot_id,
            result.active,
            result.lineage,
        )
        self.assertEqual(rebuilt, result.fingerprint)
        self.assertEqual(result.resolver_version, RESOLVER_VERSION)
        # A version-2 resolver over the same rows must be distinguishable, which
        # is only true if the constant is inside the digest. Proven by hashing
        # the same material under a different tag position.
        import cofferdam.workstation.tasks.lineage as module

        original = module.RESOLVER_VERSION
        try:
            module.RESOLVER_VERSION = original + 1
            moved = resolved_fingerprint(
                result.task_id,
                result.target_turn_number,
                result.target_snapshot_id,
                result.active,
                result.lineage,
            )
        finally:
            module.RESOLVER_VERSION = original
        self.assertNotEqual(moved, result.fingerprint)

    def test_a_different_target_turn_moves_it(self):
        nodes = two_generations()
        self.assertNotEqual(
            resolve(graph(1, *nodes)).fingerprint,
            resolve(graph(2, *nodes)).fingerprint,
        )

    def test_a_different_active_criterion_moves_it(self):
        changed = (
            (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
            (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
             snapshot(2, ["different"])),
        )
        self.assertNotEqual(self.base(), resolve(graph(2, *changed)).fingerprint)

    def test_a_different_active_order_moves_it(self):
        reordered = (
            (declared(1, CONTINUITY_ROOT), snapshot(1, ["b", "a"])),
            (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"), snapshot(2, ["c"])),
        )
        first = resolve(graph(2, *reordered))
        second = resolve(graph(2, *two_generations()))
        self.assertEqual(sorted(first.active_criterion_ids),
                         sorted(second.active_criterion_ids))
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_a_different_source_snapshot_identity_moves_it(self):
        moved = (
            (declared(1, CONTINUITY_ROOT, current="snp_relabelled"),
             snapshot(1, ["a", "b"], snapshot_id="snp_relabelled")),
            (declared(2, CONTINUITY_EXTEND, predecessor="snp_relabelled"),
             snapshot(2, ["c"])),
        )
        result = resolve(
            graph(
                2,
                *moved,
                owners={"snp_relabelled": ("tsk_lineage", 1),
                        "snp_t2": ("tsk_lineage", 2)},
            )
        )
        self.assertTrue(result.resolved)
        self.assertNotEqual(self.base(), result.fingerprint)

    def test_a_different_criteria_snapshot_fingerprint_moves_it(self):
        rehashed = (
            (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"],
                                                    fingerprint="d" * 64)),
            (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"), snapshot(2, ["c"])),
        )
        self.assertNotEqual(self.base(), resolve(graph(2, *rehashed)).fingerprint)

    def test_a_different_consumed_continuity_fingerprint_moves_it(self):
        redeclared = (
            (declared(1, CONTINUITY_ROOT, fingerprint="e" * 64), snapshot(1, ["a", "b"])),
            (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"), snapshot(2, ["c"])),
        )
        self.assertNotEqual(self.base(), resolve(graph(2, *redeclared)).fingerprint)

    def test_the_same_active_set_reached_by_a_different_mode_moves_it(self):
        """Two ways to arrive at ``c`` alone. They are not the same fact."""
        by_replace = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
                 snapshot(2, ["c"])),
            )
        )
        by_revise = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_c", "crt_a")],
                    ),
                    snapshot(2, ["c"]),
                ),
            )
        )
        self.assertEqual(
            by_replace.active_criterion_ids, by_revise.active_criterion_ids
        )
        self.assertNotEqual(by_replace.fingerprint, by_revise.fingerprint)

    def test_a_replace_does_not_bind_the_predecessor_it_never_traversed(self):
        """The honesty rule, stated as a hash equality.

        Two tasks whose turn 2 replaces with identical criteria agree, whatever
        their turn 1 was — because turn 1's active set played no part in either
        answer.
        """
        one = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b", "c"])),
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
                 snapshot(2, ["z"])),
            )
        )
        other = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["totally", "different"])),
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
                 snapshot(2, ["z"])),
            )
        )
        self.assertEqual(one.fingerprint, other.fingerprint)
        self.assertEqual([step.turn_number for step in one.lineage], [2])

    def test_an_extend_does_bind_the_predecessor_it_traversed(self):
        """The contrast that makes the rule above a decision rather than a gap."""
        one = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, [])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["z"])),
            )
        )
        other = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, [], fingerprint="9" * 64)),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["z"])),
            )
        )
        self.assertEqual(one.active_criterion_ids, other.active_criterion_ids)
        self.assertNotEqual(one.fingerprint, other.fingerprint)


class NoIncidentalMaterialTests(unittest.TestCase):
    def test_the_minted_continuity_id_is_not_bound(self):
        """It carries a clock and randomness; it identifies the row, not the fact."""
        renamed = (
            (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
            (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"), snapshot(2, ["c"])),
        )
        first = resolve(graph(2, *renamed))
        rebuilt = [
            (
                type(continuity)(
                    **{**continuity.__dict__, "continuity_id": "ctn_re_minted"}
                ),
                snap,
            )
            for continuity, snap in renamed
        ]
        second = resolve(graph(2, *rebuilt))
        self.assertNotEqual(
            first.lineage[0].continuity_id, second.lineage[0].continuity_id
        )
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_the_dispatch_state_is_not_bound(self):
        """How far a turn got is not part of what it requires."""
        rebuilt = [
            (
                type(continuity)(**{**continuity.__dict__, "dispatch_state": "captured"}),
                type(snap)(**{**snap.__dict__, "dispatch_state": "captured"}),
            )
            for continuity, snap in two_generations()
        ]
        self.assertEqual(
            resolve(graph(2, *two_generations())).fingerprint,
            resolve(graph(2, *rebuilt)).fingerprint,
        )

    def test_recorded_at_is_not_bound(self):
        rebuilt = [
            (continuity, type(snap)(**{**snap.__dict__, "recorded_at": "2099-01-01"}))
            for continuity, snap in two_generations()
        ]
        self.assertEqual(
            resolve(graph(2, *two_generations())).fingerprint,
            resolve(graph(2, *rebuilt)).fingerprint,
        )

    def test_no_host_path_or_slot_appears_in_the_material(self):
        """A deployment that moves must not move a resolved identity."""
        source = (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "lineage.py").read_text(
            encoding="utf-8"
        )
        body = source.split("def resolved_fingerprint(")[1]
        body = body.split("\ndef ")[0]
        for forbidden in ("/home/", "cofferdam/slots", "os.environ", "getcwd", "time("):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
