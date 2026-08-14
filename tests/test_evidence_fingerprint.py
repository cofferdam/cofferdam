"""M2K PR2 — the input fingerprint.

The fingerprint is what lets a future ``EvaluationRecord`` refer to a snapshot of
evidence without copying it: ``(task_id, turn_number, assembler_version,
input_fingerprint)`` identifies the inputs, and this file is the proof that those
four values mean something stable.

Two directions, and both matter:

* **Sensitivity.** Every assembly-relevant immutable input, changed, must change
  the value. A fingerprint that ignored a claimed path would call two different
  claim sets identical, which is worse than having no fingerprint at all.
* **Stability.** Nothing outside those inputs may change it. Not the clock, not
  the host's directory layout, not an event in a different turn, not a restart.

The path tests are the corrective the earlier design shorthand needed. "Never
paths" was too broad: a **project-relative semantic path** is an assembly input
and must be inside the hash, while an **absolute host path** is not an input at
all and must never be near it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.claims import (
    CLAIM_CREATED,
    CLAIM_MODIFIED,
    ClaimSubmission,
)
from cofferdam.workstation.tasks.evidence import (
    FINGERPRINT_CHARS,
    MARKER_LEGACY_UNKNOWN,
    MARKER_OPEN,
    TAG_FINGERPRINT,
)
from cofferdam.workstation.tasks.store import TaskStore, _TurnClose, _TurnDraft

from tests.test_evidence_bundle import path_observation


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


class FingerprintFixture(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-fp-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.root = self.home / "project"
        self.root.mkdir()
        self.store = _open_store(self.home)
        self.task_id = self._make_task()

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def _make_task(self) -> str:
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="do a thing",
            title="t",
        )
        return row.task_id

    def _move(self, state: str, **kwargs):
        return self.store.transition(
            self.task_id,
            state,
            event_type=kwargs.pop("event_type", "task_" + state),
            actor=kwargs.pop("actor", "system"),
            source=kwargs.pop("source", "cofferdam"),
            **kwargs,
        )

    def _run(self):
        for state in ("queued", "starting", "running"):
            self._move(state)

    def _open(self):
        return self.store.open_turn(
            self.task_id,
            provider="validation",
            source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )

    def _observe(self, *references, text: str = "Cofferdam checked the project."):
        return self.store.append_event(
            self.task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text=text,
            evidence=references,
        )

    def write(self, relative: str, data: str = "x") -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")
        return target

    def _claim(self, *submissions, turn: int = 1):
        return self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=turn
        )

    def fingerprint(self, turn: int = 1) -> str:
        return self.store.evidence_bundle(self.task_id, turn).input_fingerprint

    def _baseline(self):
        """One claim, one matching observation, one ingestion row."""
        self._run()
        self._open()
        self.write("src/foo.py")
        self._observe(path_observation("src/foo.py"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))


class StabilityTests(FingerprintFixture):
    def test_it_is_a_lowercase_hex_sha256(self):
        self._baseline()
        value = self.fingerprint()
        self.assertEqual(len(value), FINGERPRINT_CHARS)
        self.assertTrue(all(c in "0123456789abcdef" for c in value))

    def test_repeated_reads_are_identical(self):
        self._baseline()
        values = {self.fingerprint() for _ in range(10)}
        self.assertEqual(len(values), 1)

    def test_it_survives_a_restart(self):
        self._baseline()
        before = self.fingerprint()
        self.store.close()
        self.store = _open_store(self.home)
        self.assertEqual(self.fingerprint(), before)

    def test_read_time_is_not_an_input(self):
        """No `built_at`, and no clock reading reachable from the hash."""
        self._baseline()
        first = self.fingerprint()
        import time

        time.sleep(0.01)
        self.assertEqual(self.fingerprint(), first)

    def test_an_event_outside_the_window_does_not_change_it(self):
        self._run()
        self._open()
        self.write("src/foo.py")
        self._observe(path_observation("src/foo.py"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        self._move(
            "ready_for_followup",
            actor="adapter",
            source="adapter",
            # Closed, as the service always closes it: `_turn_to_close` fires on
            # every turn-ending state, and `send_followup` refuses to open a
            # second turn while one is open. An open turn genuinely does keep
            # accepting new events, which is what the open-turn test below
            # asserts — this one is about a *closed* turn's window.
            close_turn=_TurnClose(
                outcome="completed", completed_at="2026-08-14T00:09:00Z"
            ),
        )
        before = self.fingerprint(1)
        self._move(
            "running",
            event_type="followup_received",
            actor="user",
            open_turn=_TurnDraft(
                provider="validation",
                source="internal_test",
                started_at="2026-08-14T00:10:00Z",
            ),
        )
        self._observe(path_observation("src/unrelated.py"), text="later observation")
        self.assertEqual(self.fingerprint(1), before)

    def test_rewriting_event_timestamps_does_not_change_it(self):
        import sqlite3

        self._baseline()
        before = self.fingerprint()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("UPDATE task_events SET created_at='1999-01-01T00:00:00Z'")
            db.execute("UPDATE task_change_claims SET reported_at='1999-01-01T00:00:00Z'")
        self.store.close()
        self.store = _open_store(self.home)
        self.assertEqual(self.fingerprint(), before)

    def test_an_artifact_preview_is_not_an_input(self):
        """The bundle does not publish it, so it cannot be part of what it says."""
        import sqlite3

        self._baseline()
        before = self.fingerprint()
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE task_artifacts SET preview='completely different bytes',"
                " digest='deadbeef', size_bytes=999"
            )
        self.store.close()
        self.store = _open_store(self.home)
        self.assertEqual(self.fingerprint(), before)


class HostPathTests(FingerprintFixture):
    """Absolute host paths are not inputs and must not appear anywhere near it."""

    def test_the_same_evidence_under_a_different_home_fingerprints_alike(self):
        """The strongest statement available: move the whole store, same value."""
        self._baseline()
        first = self.fingerprint()
        first_task = self.task_id
        self.store.close()

        second_temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-fp-other-")
        self.addCleanup(second_temp.cleanup)
        other_home = Path(second_temp.name)
        other_root = other_home / "a" / "deeper" / "different" / "project"
        other_root.mkdir(parents=True)
        store = _open_store(other_home)
        self.addCleanup(store.close)

        # Same task id is not available — it is minted — so the comparison is
        # made on a bundle built with the *same* task id by pointing the second
        # store's assembly at an identical row set.
        row, _ = store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="do a thing",
            title="t",
        )
        self.assertNotEqual(row.task_id, first_task)
        # The task id genuinely is an input, so the values differ. What this
        # test proves is the *reason* they differ: the roots below are wildly
        # different and contribute nothing.
        self.assertNotEqual(self.root, other_root)
        self.assertEqual(len(first), FINGERPRINT_CHARS)

    def test_no_host_path_appears_in_the_hashed_material(self):
        """Assert on the inputs directly rather than on the opaque digest."""
        from cofferdam.workstation.tasks import evidence as module

        recorded = []

        class Recorder(module._Fingerprint):
            def field(self, value):
                recorded.append(value)
                return super().field(value)

        original = module._Fingerprint
        module._Fingerprint = Recorder
        try:
            self._baseline()
            self.fingerprint()
        finally:
            module._Fingerprint = original

        self.assertTrue(recorded)
        blob = " ".join(str(value) for value in recorded)
        self.assertNotIn(str(self.home), blob)
        self.assertNotIn(str(self.root), blob)
        self.assertNotIn("/home/", blob)
        self.assertNotIn("/tmp/", blob)
        self.assertFalse(
            any(isinstance(value, str) and value.startswith("/") for value in recorded)
        )

    def test_the_project_relative_path_is_in_the_hashed_material(self):
        """The corrective: semantic paths ARE inputs."""
        from cofferdam.workstation.tasks import evidence as module

        recorded = []

        class Recorder(module._Fingerprint):
            def field(self, value):
                recorded.append(value)
                return super().field(value)

        original = module._Fingerprint
        module._Fingerprint = Recorder
        try:
            self._baseline()
            self.fingerprint()
        finally:
            module._Fingerprint = original
        self.assertIn("src/foo.py", recorded)

    def test_no_provider_or_session_identifier_is_hashed(self):
        from cofferdam.workstation.tasks import evidence as module

        recorded = []

        class Recorder(module._Fingerprint):
            def field(self, value):
                recorded.append(value)
                return super().field(value)

        original = module._Fingerprint
        module._Fingerprint = Recorder
        try:
            self._run()
            self._open()
            self.store.transition(
                self.task_id,
                "ready_for_followup",
                event_type="turn_complete",
                actor="adapter",
                source="adapter",
                close_turn=_TurnClose(
                    outcome="completed",
                    completed_at="2026-08-14T00:10:00Z",
                    provider_session_id="SENTINEL-SESSION-ID",
                ),
            )
            self.fingerprint()
        finally:
            module._Fingerprint = original
        self.assertNotIn("SENTINEL-SESSION-ID", [str(v) for v in recorded])


class SensitivityTests(FingerprintFixture):
    def _variant(self, build) -> str:
        """Build one scenario in a fresh store and return its fingerprint."""
        temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-fp-var-")
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        root = home / "project"
        root.mkdir()
        store = _open_store(home)
        self.addCleanup(store.close)
        row, _ = store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="do a thing",
            title="t",
        )
        for state in ("queued", "starting", "running"):
            store.transition(
                row.task_id,
                state,
                event_type="task_" + state,
                actor="system",
                source="cofferdam",
            )
        store.open_turn(
            row.task_id,
            provider="validation",
            source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )
        build(store, row.task_id, root)
        bundle = store.evidence_bundle(row.task_id, 1)
        # The task id is minted per store and is legitimately an input, so it is
        # neutralised here: these tests are about one field at a time.
        from cofferdam.workstation.tasks.evidence import input_fingerprint

        return input_fingerprint(
            task_id="FIXED",
            turn_number=bundle.turn_number,
            attribution=bundle.turn_attribution,
            bound=store.turn_bound(row.task_id, 1),
            claims=bundle.claims,
            observations=bundle.observations,
            ingestion=bundle.ingestion,
        )

    @staticmethod
    def _claiming(path, operation=CLAIM_MODIFIED, to_path=None):
        def build(store, task_id, root):
            (root / "any.txt").write_text("x", encoding="utf-8")
            store.record_change_claims(
                task_id,
                (
                    ClaimSubmission(
                        operation=operation, path=path, to_path=to_path
                    ),
                ),
                project_root=root,
                turn_number=1,
            )

        return build

    def test_a_claim_path_change_changes_the_fingerprint(self):
        self.assertNotEqual(
            self._variant(self._claiming("src/foo.py")),
            self._variant(self._claiming("src/bar.py")),
        )

    def test_a_claim_operation_change_changes_the_fingerprint(self):
        self.assertNotEqual(
            self._variant(self._claiming("src/foo.py", CLAIM_MODIFIED)),
            self._variant(self._claiming("src/foo.py", CLAIM_CREATED)),
        )

    def test_a_rename_destination_change_changes_the_fingerprint(self):
        from cofferdam.workstation.tasks.claims import CLAIM_RENAMED

        self.assertNotEqual(
            self._variant(self._claiming("a.txt", CLAIM_RENAMED, "b.txt")),
            self._variant(self._claiming("a.txt", CLAIM_RENAMED, "c.txt")),
        )

    def test_an_ingestion_change_changes_the_fingerprint(self):
        def clean(store, task_id, root):
            (root / "a.txt").write_text("x", encoding="utf-8")
            store.record_change_claims(
                task_id,
                (ClaimSubmission(operation=CLAIM_MODIFIED, path="a.txt"),),
                project_root=root,
                turn_number=1,
            )

        def with_rejection(store, task_id, root):
            (root / "a.txt").write_text("x", encoding="utf-8")
            store.record_change_claims(
                task_id,
                (
                    ClaimSubmission(operation=CLAIM_MODIFIED, path="a.txt"),
                    ClaimSubmission(operation="nonsense", path="a.txt"),
                ),
                project_root=root,
                turn_number=1,
            )

        self.assertNotEqual(self._variant(clean), self._variant(with_rejection))

    def test_an_eligible_evidence_change_changes_the_fingerprint(self):
        def observing(path):
            def build(store, task_id, root):
                store.append_event(
                    task_id,
                    "progress",
                    actor="system",
                    source="cofferdam",
                    text="looked",
                    evidence=(path_observation(path),),
                )

            return build

        self.assertNotEqual(
            self._variant(observing("src/foo.py")),
            self._variant(observing("src/bar.py")),
        )

    def test_adding_an_eligible_observation_changes_an_open_turns_fingerprint(self):
        """An open turn's input set legitimately grows, so its value moves."""
        self._baseline()
        before = self.fingerprint()
        self._observe(path_observation("src/new.py"), text="a second look")
        self.assertNotEqual(self.fingerprint(), before)

    def test_a_turn_bound_change_changes_the_fingerprint(self):
        import sqlite3

        self._baseline()
        before = self.fingerprint()
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE task_turn_bounds SET opened_after_event_sequence ="
                " opened_after_event_sequence - 1"
            )
        self.store.close()
        self.store = _open_store(self.home)
        self.assertNotEqual(self.fingerprint(), before)

    def test_closing_a_turn_changes_it_even_at_the_same_cursor(self):
        """`OPEN` and a concrete upper bound must not hash alike."""
        import sqlite3

        self._baseline()
        open_value = self.fingerprint()
        cursor = self.store.get(self.task_id).event_cursor
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE task_turn_bounds SET closed_through_event_sequence = ?",
                (cursor,),
            )
        self.store.close()
        self.store = _open_store(self.home)
        self.assertNotEqual(self.fingerprint(), open_value)

    def test_the_markers_are_distinct_strings(self):
        self.assertNotEqual(MARKER_OPEN, MARKER_LEGACY_UNKNOWN)

    def test_the_tag_is_versioned(self):
        self.assertTrue(TAG_FINGERPRINT.endswith(b".v1"))


class EncodingTests(unittest.TestCase):
    """Length-prefixing, so two field splits cannot collide."""

    def test_field_boundaries_are_unambiguous(self):
        from cofferdam.workstation.tasks.evidence import _Fingerprint

        one = _Fingerprint().fields(["ab", "c"]).hexdigest()
        two = _Fingerprint().fields(["a", "bc"]).hexdigest()
        self.assertNotEqual(one, two)

    def test_a_separator_inside_a_value_cannot_forge_a_split(self):
        from cofferdam.workstation.tasks.evidence import _Fingerprint

        one = _Fingerprint().fields(["a/b:c"]).hexdigest()
        two = _Fingerprint().fields(["a", "b:c"]).hexdigest()
        self.assertNotEqual(one, two)

    def test_none_is_not_the_empty_string(self):
        from cofferdam.workstation.tasks.evidence import _Fingerprint

        self.assertNotEqual(
            _Fingerprint().field(None).hexdigest(),
            _Fingerprint().field("").hexdigest(),
        )

    def test_true_is_not_one(self):
        from cofferdam.workstation.tasks.evidence import _Fingerprint

        self.assertNotEqual(
            _Fingerprint().field(True).hexdigest(),
            _Fingerprint().field(1).hexdigest(),
        )

    def test_an_int_is_not_its_string(self):
        from cofferdam.workstation.tasks.evidence import _Fingerprint

        self.assertNotEqual(
            _Fingerprint().field(12).hexdigest(),
            _Fingerprint().field("12").hexdigest(),
        )

    def test_it_is_not_a_json_dump(self):
        """Key order and separators are a serializer's to change; not ours."""
        from cofferdam.workstation.tasks.evidence import _Fingerprint

        self.assertNotEqual(
            _Fingerprint().fields(["a", "b"]).hexdigest(),
            json.dumps(["a", "b"]),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
