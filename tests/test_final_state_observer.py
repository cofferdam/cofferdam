"""M2K PR14 — the observer itself: what is there, and what it refuses to look at.

Two halves. The **semantic** half pins what each filesystem shape is recorded as,
including the two that are easy to get wrong: a broken symlink is a *present*
symlink rather than an absent path, and a path blocked by an intermediate symlink
is *unavailable* rather than absent. The **containment** half proves the observer
cannot be walked out of the project, and that it says so instead of quietly
answering about somewhere else.

``absent`` is a positive machine observation — the safe anchored lookup completed
and found nothing. Every test here that expects ``unavailable`` exists because
the alternative would let a future layer read "we could not look" as "it is not
there".
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.criteria import AcceptanceCriterion
from cofferdam.workstation.tasks.finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    FINGERPRINT_CHARS,
    KIND_DIRECTORY,
    KIND_FILE,
    KIND_OTHER,
    KIND_SYMLINK,
    MAX_STABILITY_ATTEMPTS,
    PATH_ABSENT,
    PATH_PRESENT,
    PATH_UNAVAILABLE,
    REASON_OBSERVATION_UNSTABLE,
    REASON_SYMLINK_TRAVERSAL_REFUSED,
    REASON_UNSAFE_PATH,
    PathObservation,
    final_state_fingerprint,
    observe_path,
    observe_paths,
    target_paths,
)
from cofferdam.workstation.tasks.lineage import ActiveCriterion


class ObserverCase(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.root = Path(self._home.name) / "project"
        self.root.mkdir()
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside.cleanup)
        self.outside = Path(self._outside.name)

    def observe(self, relative):
        return observe_path(self.root, relative)


class PathStateTests(ObserverCase):
    def test_an_ordinary_file(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.observe("a.txt"), (PATH_PRESENT, KIND_FILE, None))

    def test_a_directory(self):
        (self.root / "pkg").mkdir()
        self.assertEqual(self.observe("pkg"), (PATH_PRESENT, KIND_DIRECTORY, None))

    def test_a_nested_file(self):
        (self.root / "pkg" / "deep").mkdir(parents=True)
        (self.root / "pkg" / "deep" / "f.py").write_text("x", encoding="utf-8")
        self.assertEqual(
            self.observe("pkg/deep/f.py"), (PATH_PRESENT, KIND_FILE, None)
        )

    def test_a_missing_path(self):
        self.assertEqual(self.observe("nope.txt"), (PATH_ABSENT, None, None))

    def test_a_missing_path_under_a_real_directory(self):
        (self.root / "pkg").mkdir()
        self.assertEqual(self.observe("pkg/nope.txt"), (PATH_ABSENT, None, None))

    def test_a_missing_path_under_a_missing_directory(self):
        self.assertEqual(self.observe("nodir/nope.txt"), (PATH_ABSENT, None, None))

    def test_a_path_under_a_regular_file_is_absent(self):
        """`a/b` where `a` is a file. The target genuinely cannot exist."""
        (self.root / "a").write_text("x", encoding="utf-8")
        self.assertEqual(self.observe("a/b"), (PATH_ABSENT, None, None))

    def test_a_unicode_filename(self):
        (self.root / "café.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.observe("café.txt"), (PATH_PRESENT, KIND_FILE, None))

    def test_a_fifo_is_present_and_other(self):
        """Recorded, not refused — and never opened, which would block forever."""
        try:
            os.mkfifo(self.root / "pipe")
        except (AttributeError, OSError):  # pragma: no cover - platform dependent
            self.skipTest("this platform has no FIFOs")
        self.assertEqual(self.observe("pipe"), (PATH_PRESENT, KIND_OTHER, None))


class SymlinkTests(ObserverCase):
    def test_a_final_symlink_is_present_as_itself(self):
        (self.root / "real.txt").write_text("x", encoding="utf-8")
        os.symlink("real.txt", self.root / "link.txt")
        self.assertEqual(self.observe("link.txt"), (PATH_PRESENT, KIND_SYMLINK, None))

    def test_a_broken_final_symlink_is_present_not_absent(self):
        """The path exists. What it points at is a question this build does not ask."""
        os.symlink("nowhere.txt", self.root / "broken.txt")
        self.assertEqual(self.observe("broken.txt"), (PATH_PRESENT, KIND_SYMLINK, None))

    def test_a_symlink_pointing_outside_is_present_as_a_symlink(self):
        """Observed as a link object. Nothing about its target is read."""
        (self.outside / "secret.txt").write_text("s", encoding="utf-8")
        os.symlink(str(self.outside / "secret.txt"), self.root / "escape.txt")
        self.assertEqual(self.observe("escape.txt"), (PATH_PRESENT, KIND_SYMLINK, None))

    def test_an_intermediate_symlink_to_outside_is_refused(self):
        """The load-bearing one. `repo/external -> /outside`, target `external/x`."""
        (self.outside / "data.txt").write_text("s", encoding="utf-8")
        os.symlink(str(self.outside), self.root / "external")
        self.assertEqual(
            self.observe("external/data.txt"),
            (PATH_UNAVAILABLE, None, REASON_SYMLINK_TRAVERSAL_REFUSED),
        )

    def test_the_deny_gate_runs_before_any_traversal(self):
        """A credential name is refused lexically, before a descriptor is opened.

        Ordering matters: the cheap gate first means a denied path never reaches
        the filesystem at all, so it cannot be probed for existence by naming it.
        """
        (self.outside / "secret.txt").write_text("s", encoding="utf-8")
        os.symlink(str(self.outside), self.root / "external")
        self.assertEqual(
            self.observe("external/secret.txt"),
            (PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH),
        )

    def test_an_intermediate_symlink_inside_the_project_is_also_refused(self):
        """The rule is about traversal, not about where the link happens to land.

        A link that points somewhere safe today can be repointed tomorrow, and a
        rule that depended on the target would have to be re-checked every time.
        """
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "f.txt").write_text("x", encoding="utf-8")
        os.symlink("pkg", self.root / "alias")
        self.assertEqual(
            self.observe("alias/f.txt"),
            (PATH_UNAVAILABLE, None, REASON_SYMLINK_TRAVERSAL_REFUSED),
        )

    def test_a_refused_traversal_never_reports_absent(self):
        """The failure mode this whole design exists to prevent.

        The file *does* exist behind the link. Reporting `absent` would be a
        false negative that a future acceptance layer would read as proof.
        """
        (self.outside / "present.txt").write_text("s", encoding="utf-8")
        os.symlink(str(self.outside), self.root / "external")
        state, _, _ = self.observe("external/present.txt")
        self.assertNotEqual(state, PATH_ABSENT)
        self.assertEqual(state, PATH_UNAVAILABLE)

    def test_nothing_outside_the_project_is_described(self):
        """A refusal carries a reason code and no information about the target."""
        (self.outside / "data.txt").write_text("s", encoding="utf-8")
        os.symlink(str(self.outside), self.root / "external")
        rendered = repr(self.observe("external/data.txt"))
        self.assertNotIn(str(self.outside), rendered)
        self.assertNotIn(self._outside.name, rendered)


class UnsafePathTests(ObserverCase):
    def test_a_parent_traversal(self):
        self.assertEqual(
            self.observe("../escape"), (PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH)
        )

    def test_an_absolute_path(self):
        self.assertEqual(
            self.observe("/etc/passwd"), (PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH)
        )

    def test_a_home_relative_path(self):
        self.assertEqual(
            self.observe("~/secrets"), (PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH)
        )

    def test_an_embedded_nul(self):
        self.assertEqual(
            self.observe("a\x00b"), (PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH)
        )

    def test_a_sensitive_path(self):
        """The claim deny list applies here too — a new door to the same room."""
        state, _, reason = self.observe("secrets/id_rsa")
        self.assertEqual((state, reason), (PATH_UNAVAILABLE, REASON_UNSAFE_PATH))

    def test_a_dot_segment(self):
        self.assertEqual(
            self.observe("a/./b"), (PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH)
        )

    def test_an_unsafe_path_is_never_absent(self):
        for path in ("../escape", "/etc/passwd", "a\x00b"):
            self.assertNotEqual(self.observe(path)[0], PATH_ABSENT, path)

    def test_a_missing_project_root(self):
        missing = Path(self._home.name) / "gone"
        state, _, reason = observe_path(missing, "a.txt")
        self.assertEqual(state, PATH_UNAVAILABLE)
        self.assertIsNotNone(reason)


class StabilityTests(ObserverCase):
    def test_a_settled_tree_observes_cleanly(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        results, limitation = observe_paths(self.root, ("a.txt", "b.txt"))
        self.assertIsNone(limitation)
        self.assertEqual(results[0][0], PATH_PRESENT)
        self.assertEqual(results[1][0], PATH_ABSENT)

    def test_an_empty_target_set_is_stable_and_empty(self):
        results, limitation = observe_paths(self.root, ())
        self.assertEqual(results, ())
        self.assertIsNone(limitation)

    def test_a_path_changing_under_the_observation_is_refused(self):
        """Bounded retries, then `unavailable` — never an optimistic answer."""
        import cofferdam.workstation.tasks.finalstate as module

        toggle = {"n": 0}
        real = module.observe_path

        def flapping(root, relative):
            toggle["n"] += 1
            if toggle["n"] % 2:
                return (PATH_PRESENT, KIND_FILE, None)
            return (PATH_ABSENT, None, None)

        module.observe_path = flapping
        try:
            results, limitation = observe_paths(self.root, ("a.txt",))
        finally:
            module.observe_path = real
        self.assertEqual(results, ())
        self.assertEqual(limitation, REASON_OBSERVATION_UNSTABLE)

    def test_instability_terminates(self):
        """No infinite retry: the number of passes is bounded by the constant."""
        import cofferdam.workstation.tasks.finalstate as module

        calls = {"n": 0}
        real = module.observe_path

        def counting(root, relative):
            calls["n"] += 1
            return (PATH_PRESENT, KIND_FILE, None) if calls["n"] % 2 else (
                PATH_ABSENT,
                None,
                None,
            )

        module.observe_path = counting
        try:
            observe_paths(self.root, ("a.txt",))
        finally:
            module.observe_path = real
        self.assertLessEqual(calls["n"], MAX_STABILITY_ATTEMPTS)
        self.assertGreaterEqual(MAX_STABILITY_ATTEMPTS, 2)

    def test_a_settling_tree_is_accepted(self):
        """One change, then quiet. The retry is what makes this answerable."""
        import cofferdam.workstation.tasks.finalstate as module

        state = {"n": 0}
        real = module.observe_path

        def settling(root, relative):
            state["n"] += 1
            if state["n"] == 1:
                return (PATH_ABSENT, None, None)
            return (PATH_PRESENT, KIND_FILE, None)

        module.observe_path = settling
        try:
            results, limitation = observe_paths(self.root, ("a.txt",))
        finally:
            module.observe_path = real
        self.assertIsNone(limitation)
        self.assertEqual(results, ((PATH_PRESENT, KIND_FILE, None),))


class TargetSelectionTests(unittest.TestCase):
    def criterion(self, label, ordinal, path, to_path=None, predicate="path_changed"):
        return ActiveCriterion(
            criterion_id="crt_%s" % label,
            source_snapshot_id="acs_%s" % label,
            source_turn_number=ordinal,
            source_ordinal=ordinal,
            criterion=AcceptanceCriterion(
                ordinal=ordinal,
                kind="evidence",
                predicate=predicate,
                path=path,
                to_path=to_path,
                criterion_id="crt_%s" % label,
            ),
        )

    def test_paths_are_taken_in_resolver_order(self):
        active = [
            self.criterion("z", 1, "zulu.py"),
            self.criterion("a", 2, "alpha.py"),
        ]
        self.assertEqual(target_paths(active), ("zulu.py", "alpha.py"))

    def test_a_rename_contributes_both_endpoints(self):
        active = [
            self.criterion("r", 1, "old.py", to_path="new.py", predicate="rename")
        ]
        self.assertEqual(target_paths(active), ("old.py", "new.py"))

    def test_exact_duplicates_collapse_once(self):
        active = [self.criterion("a", 1, "same.py"), self.criterion("b", 2, "same.py")]
        self.assertEqual(target_paths(active), ("same.py",))

    def test_similar_paths_are_not_collapsed(self):
        """Only exact equality dedupes. Similarity is never authority."""
        active = [
            self.criterion("a", 1, "src/app.py"),
            self.criterion("b", 2, "app.py"),
            self.criterion("c", 3, "src/App.py"),
        ]
        self.assertEqual(
            target_paths(active), ("src/app.py", "app.py", "src/App.py")
        )

    def test_a_manual_criterion_contributes_nothing(self):
        active = [
            ActiveCriterion(
                criterion_id="crt_m",
                source_snapshot_id="acs_m",
                source_turn_number=1,
                source_ordinal=1,
                criterion=AcceptanceCriterion(
                    ordinal=1,
                    kind="manual",
                    description="a person checks",
                    criterion_id="crt_m",
                ),
            )
        ]
        self.assertEqual(target_paths(active), ())

    def test_an_empty_active_set_yields_no_targets(self):
        self.assertEqual(target_paths([]), ())


class FingerprintTests(unittest.TestCase):
    def observation(self, path="a.txt", state=PATH_PRESENT, kind=KIND_FILE, reason=None):
        return PathObservation(ordinal=1, path=path, state=state, kind=kind, reason=reason)

    def hash(self, **overrides):
        arguments = {
            "task_id": "tsk_1",
            "turn_number": 1,
            "state": "complete",
            "limitation_reason": None,
            "lineage_fingerprint": "l" * 64,
            "head_revision": "h" * 40,
            "paths": (self.observation(),),
        }
        arguments.update(overrides)
        return final_state_fingerprint(**arguments)

    def test_it_is_a_sha256_hex_digest(self):
        value = self.hash()
        self.assertEqual(len(value), FINGERPRINT_CHARS)
        int(value, 16)

    def test_it_is_deterministic(self):
        self.assertEqual(self.hash(), self.hash())

    def test_the_observer_version_is_bound(self):
        import cofferdam.workstation.tasks.finalstate as module

        before = self.hash()
        original = module.FINAL_STATE_OBSERVER_VERSION
        try:
            module.FINAL_STATE_OBSERVER_VERSION = original + 1
            moved = self.hash()
        finally:
            module.FINAL_STATE_OBSERVER_VERSION = original
        self.assertNotEqual(before, moved)
        self.assertEqual(FINAL_STATE_OBSERVER_VERSION, 1)

    def test_the_path_moves_it(self):
        self.assertNotEqual(
            self.hash(), self.hash(paths=(self.observation(path="b.txt"),))
        )

    def test_the_state_moves_it(self):
        self.assertNotEqual(
            self.hash(),
            self.hash(paths=(self.observation(state=PATH_ABSENT, kind=None),)),
        )

    def test_the_kind_moves_it(self):
        self.assertNotEqual(
            self.hash(), self.hash(paths=(self.observation(kind=KIND_SYMLINK),))
        )

    def test_a_path_reason_moves_it(self):
        self.assertNotEqual(
            self.hash(),
            self.hash(
                paths=(
                    self.observation(
                        state=PATH_UNAVAILABLE,
                        kind=None,
                        reason=REASON_SYMLINK_TRAVERSAL_REFUSED,
                    ),
                )
            ),
        )

    def test_target_ordering_moves_it(self):
        forward = (
            PathObservation(1, "a.txt", PATH_PRESENT, KIND_FILE),
            PathObservation(2, "b.txt", PATH_PRESENT, KIND_FILE),
        )
        reversed_order = (
            PathObservation(1, "b.txt", PATH_PRESENT, KIND_FILE),
            PathObservation(2, "a.txt", PATH_PRESENT, KIND_FILE),
        )
        self.assertNotEqual(self.hash(paths=forward), self.hash(paths=reversed_order))

    def test_the_lineage_identity_moves_it(self):
        """Which paths were looked at is half of what an observation asserts."""
        self.assertNotEqual(self.hash(), self.hash(lineage_fingerprint="9" * 64))

    def test_the_head_anchor_moves_it(self):
        self.assertNotEqual(self.hash(), self.hash(head_revision="0" * 40))

    def test_the_observation_state_and_limitation_move_it(self):
        self.assertNotEqual(
            self.hash(),
            self.hash(state="incomplete", limitation_reason=REASON_UNSAFE_PATH),
        )

    def test_no_host_path_or_clock_reaches_the_material(self):
        from pathlib import Path as _Path

        source = (
            _Path(__file__).resolve().parents[1]
            / "cofferdam"
            / "workstation"
            / "tasks"
            / "finalstate.py"
        ).read_text(encoding="utf-8")
        body = source.split("def final_state_fingerprint(")[1].split("\ndef ")[0]
        # The docstring legitimately *names* what is excluded, so the executable
        # body is what gets scanned.
        code = body.split('"""')[2]
        for forbidden in ("recorded_at", "observation_id", "time(", "os.environ", "/home/"):
            self.assertNotIn(forbidden, code, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
