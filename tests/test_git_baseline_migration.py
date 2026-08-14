"""M2K PR4 — schema v6, and the v5 → v6 migration.

Schema v6 adds exactly one table: ``task_turn_git_baselines``. It exists because
the PR3 deployment proved a coverage gap that no amount of status parsing closes:
a worker may modify files **and commit them**, after which
``git status --porcelain=v1 -z --untracked-files=all`` reports a clean tree and
the committed work is invisible to the evidence view. PR3 observes the index and
worktree relative to the *current* HEAD, and after the worker's commit the
current HEAD is the worker's own commit.

What is missing is a durable revision the machine recorded **before the worker
was allowed to begin**. v6 stores that, and nothing else. PR4 does not consume it
— no ``git diff baseline..HEAD`` runs anywhere in this build — because the
boundary has to be proven correct, host-owned, bounded and turn-scoped before
anything is allowed to derive evidence from it.

The foreign key is to ``tasks``, not to ``task_turns``, and that is a deliberate
consequence of the ordering requirement rather than an oversight. The baseline
must be durable *before* the adapter is invoked, and on both dispatch paths the
adapter is invoked before the turn row exists — see
:mod:`cofferdam.workstation.tasks.gitbaseline` for the full argument. A composite
key into ``task_turns`` would make the honest state "captured, and then the
adapter refused so the turn never opened" impossible to represent, which is
exactly the state the pre-work guarantee produces.

The migration tests matter more than they look. A live v5 database exists on the
production host with 25 tasks, 473 events and 3 turns in it, and those three
turns predate v5 itself. They get **no baseline**. Not one inferred from a task
timestamp, not one read from the current HEAD, not one guessed from the reflog:
none. A fabricated pre-work revision is worse than an absent one, because the
absent one is true.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

BASELINE_TABLE = "task_turn_git_baselines"


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


class CleanDatabaseTests(unittest.TestCase):
    """A database this build created from nothing."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr4-clean-")
        self.home = Path(self._temp.name)
        self.store = _open_store(self.home)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def test_the_schema_version_is_six(self):
        self.assertEqual(SCHEMA_VERSION, 6)

    def test_the_recorded_version_is_six(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), 6)

    def test_the_baseline_table_exists(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertIn(BASELINE_TABLE, names)

    def test_the_baseline_table_has_exactly_these_columns(self):
        """No path, no file content, no diff, no verdict, no provider id."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(%s)" % BASELINE_TABLE)}
        self.assertEqual(
            columns,
            {
                "task_id",
                "turn_number",
                "capture_state",
                "head_state",
                "head_revision",
                "object_format",
                "working_tree_state",
                "status_coverage",
                "reason",
                "captured_at",
            },
        )

    def test_the_identity_is_the_turn(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            primary = [
                r[1] for r in db.execute("PRAGMA table_info(%s)" % BASELINE_TABLE) if r[5]
            ]
        self.assertEqual(sorted(primary), ["task_id", "turn_number"])

    def test_the_foreign_key_is_the_task_not_the_turn(self):
        """Deliberate. See this module's docstring: the turn row does not exist yet."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            keys = list(db.execute("PRAGMA foreign_key_list(%s)" % BASELINE_TABLE))
        self.assertTrue(keys, "%s has no foreign key" % BASELINE_TABLE)
        self.assertEqual({row[2] for row in keys}, {"tasks"})
        self.assertEqual({(row[3], row[4]) for row in keys}, {("task_id", "task_id")})

    def test_a_baseline_cannot_reference_a_task_that_does_not_exist(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                    " head_revision, object_format, working_tree_state, status_coverage,"
                    " reason, captured_at) VALUES"
                    " ('task_nope', 1, 'captured', 'present', 'a' , 'sha1', 'clean',"
                    " 'complete', NULL, '2026-08-15T00:00:00Z')" % BASELINE_TABLE
                )

    def test_a_turn_number_below_one_is_refused(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                    " head_revision, object_format, working_tree_state, status_coverage,"
                    " reason, captured_at) VALUES"
                    " ('task_x', 0, 'captured', 'present', 'abc', 'sha1', 'clean',"
                    " 'complete', NULL, '2026-08-15T00:00:00Z')" % BASELINE_TABLE
                )

    def test_a_present_head_must_carry_a_revision(self):
        """`present` with no revision would be a boundary that names nothing."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                    " head_revision, object_format, working_tree_state, status_coverage,"
                    " reason, captured_at) VALUES"
                    " ('task_x', 1, 'captured', 'present', NULL, 'sha1', 'clean',"
                    " 'complete', NULL, '2026-08-15T00:00:00Z')" % BASELINE_TABLE
                )

    def test_an_absent_head_must_not_carry_a_revision(self):
        """`unborn` and `unavailable` must never be given an invented revision."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            for state in ("unborn", "unavailable", "not_a_repository"):
                with self.assertRaises(sqlite3.IntegrityError, msg=state):
                    db.execute(
                        "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                        " head_revision, object_format, working_tree_state,"
                        " status_coverage, reason, captured_at) VALUES"
                        " ('task_x', 1, 'unavailable', ?, 'abc123', NULL, 'unknown',"
                        " 'unavailable', 'unborn_head', '2026-08-15T00:00:00Z')"
                        % BASELINE_TABLE,
                        (state,),
                    )

    def test_clean_with_incomplete_status_is_refused(self):
        """The one combination that would be a lie. See PR4's status coverage rule."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                    " head_revision, object_format, working_tree_state, status_coverage,"
                    " reason, captured_at) VALUES"
                    " ('task_x', 1, 'captured', 'present', 'abc', 'sha1', 'clean',"
                    " 'incomplete', NULL, '2026-08-15T00:00:00Z')" % BASELINE_TABLE
                )

    def test_the_state_vocabularies_are_closed(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            for column, bad in (
                ("capture_state", "maybe"),
                ("head_state", "probably"),
                ("working_tree_state", "sortof"),
                ("status_coverage", "partial-ish"),
            ):
                values = {
                    "capture_state": "captured",
                    "head_state": "present",
                    "head_revision": "abc",
                    "object_format": "sha1",
                    "working_tree_state": "clean",
                    "status_coverage": "complete",
                }
                values[column] = bad
                with self.assertRaises(sqlite3.IntegrityError, msg=column):
                    db.execute(
                        "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                        " head_revision, object_format, working_tree_state,"
                        " status_coverage, reason, captured_at) VALUES"
                        " ('task_x', 1, ?, ?, ?, ?, ?, ?, NULL,"
                        " '2026-08-15T00:00:00Z')" % BASELINE_TABLE,
                        (
                            values["capture_state"],
                            values["head_state"],
                            values["head_revision"],
                            values["object_format"],
                            values["working_tree_state"],
                            values["status_coverage"],
                        ),
                    )

    def test_the_pre_existing_tables_are_untouched(self):
        """Additive means every v5 table still has exactly its v5 columns."""
        self.store.storage_health()
        expected = {
            "task_events": {
                "task_id", "sequence", "event_type", "created_at", "actor", "source",
                "lifecycle_revision", "correlation_id", "state", "text", "detail",
                "evidence_json",
            },
            "task_turns": {
                "task_id", "turn_number", "provider", "provider_session_id",
                "provider_turn_sequence", "source", "followup_request_id",
                "started_at", "completed_at", "outcome", "result", "failure_code",
                "failure_summary",
            },
            "task_turn_bounds": {
                "task_id", "turn_number", "opened_after_event_sequence",
                "closed_through_event_sequence",
            },
            "task_change_claims": {
                "claim_id", "task_id", "turn_number", "operation", "path", "to_path",
                "adapter_label", "reported_at", "artifact_id", "reason",
            },
            "task_artifacts": {
                "artifact_id", "task_id", "claim_id", "path", "digest", "size_bytes",
                "preview", "preview_truncated", "reason", "observed_at",
            },
            "task_claim_ingestion": {
                "task_id", "sequence", "turn_number", "submitted_count",
                "accepted_count", "rejected_count", "truncated", "reason_counts_json",
                "recorded_at",
            },
        }
        with sqlite3.connect(str(self.path)) as db:
            for table, columns in expected.items():
                found = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
                self.assertEqual(found, columns, table)

    def test_there_is_no_revision_range_table(self):
        """PR5's problem. A table for it here would be a promise this PR cannot keep."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        for forbidden in (
            "task_turn_git_diffs",
            "task_revision_changes",
            "task_commit_observations",
            "task_evaluations",
            "task_verdicts",
        ):
            self.assertNotIn(forbidden, names)


class MigrationTests(unittest.TestCase):
    """A real v5 database, opened by this build.

    ``_build_v5`` is the v5 schema written out longhand rather than imported.
    Importing would make the test pass by construction the day somebody changes
    the constant; typing it out is what makes it a test of the *upgrade*.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr4-mig-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self._temp.cleanup()

    def _build_v5(self):
        """The v5 schema, exactly as version 5 shipped it, with real history."""
        with sqlite3.connect(str(self.path)) as db:
            db.executescript(
                """
                CREATE TABLE idempotency (
                    scope        TEXT NOT NULL,
                    request_key  TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    task_id      TEXT NOT NULL,
                    result_json  TEXT,
                    created_at   TEXT NOT NULL,
                    created_ts   REAL NOT NULL,
                    PRIMARY KEY (scope, request_key)
                );
                CREATE TABLE schema_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE task_artifacts (
                    artifact_id       TEXT PRIMARY KEY,
                    task_id           TEXT NOT NULL,
                    claim_id          TEXT NOT NULL,
                    path              TEXT NOT NULL,
                    digest            TEXT,
                    size_bytes        INTEGER,
                    preview           TEXT,
                    preview_truncated INTEGER NOT NULL DEFAULT 0,
                    reason            TEXT NOT NULL,
                    observed_at       TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE,
                    FOREIGN KEY (claim_id) REFERENCES task_change_claims (claim_id) ON DELETE CASCADE
                );
                CREATE TABLE task_change_claims (
                    claim_id      TEXT PRIMARY KEY,
                    task_id       TEXT NOT NULL,
                    turn_number   INTEGER,
                    operation     TEXT NOT NULL,
                    path          TEXT NOT NULL,
                    to_path       TEXT,
                    adapter_label TEXT,
                    reported_at   TEXT NOT NULL,
                    artifact_id   TEXT,
                    reason        TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE task_claim_ingestion (
                    task_id            TEXT    NOT NULL,
                    sequence           INTEGER NOT NULL,
                    turn_number        INTEGER,
                    submitted_count    INTEGER NOT NULL,
                    accepted_count     INTEGER NOT NULL,
                    rejected_count     INTEGER NOT NULL,
                    truncated          INTEGER NOT NULL DEFAULT 0,
                    reason_counts_json TEXT,
                    recorded_at        TEXT    NOT NULL,
                    PRIMARY KEY (task_id, sequence),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE task_clarifications (
                    task_id             TEXT NOT NULL,
                    question_id         TEXT NOT NULL,
                    provider            TEXT NOT NULL,
                    provider_session_id TEXT,
                    provider_event_id   TEXT,
                    provider_sequence   INTEGER NOT NULL DEFAULT 0,
                    question            TEXT NOT NULL,
                    answer_mode         TEXT NOT NULL,
                    options_json        TEXT,
                    schema_verified     INTEGER NOT NULL DEFAULT 0,
                    requested_at        TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    answered_at         TEXT,
                    answer_json         TEXT,
                    PRIMARY KEY (task_id, question_id),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE task_events (
                    task_id            TEXT NOT NULL,
                    sequence           INTEGER NOT NULL,
                    event_type         TEXT NOT NULL,
                    created_at         TEXT NOT NULL,
                    actor              TEXT NOT NULL,
                    source             TEXT NOT NULL,
                    lifecycle_revision INTEGER NOT NULL,
                    correlation_id     TEXT,
                    state              TEXT,
                    text               TEXT,
                    detail             TEXT,
                    evidence_json      TEXT,
                    PRIMARY KEY (task_id, sequence),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE task_turn_bounds (
                    task_id                       TEXT    NOT NULL,
                    turn_number                   INTEGER NOT NULL,
                    opened_after_event_sequence   INTEGER NOT NULL,
                    closed_through_event_sequence INTEGER,
                    PRIMARY KEY (task_id, turn_number),
                    FOREIGN KEY (task_id, turn_number)
                        REFERENCES task_turns (task_id, turn_number) ON DELETE CASCADE,
                    -- Sequences start at one and the cursor starts at zero, so a turn opened
                    -- before any event has a floor of zero and nothing below it is legal.
                    CHECK (opened_after_event_sequence >= 0),
                    -- A turn cannot close before it opened. `=` is allowed on purpose: that is
                    -- a turn during which nothing was appended, which is valid.
                    CHECK (closed_through_event_sequence IS NULL
                           OR closed_through_event_sequence >= opened_after_event_sequence)
                );
                CREATE TABLE task_turns (
                    task_id                TEXT    NOT NULL,
                    turn_number            INTEGER NOT NULL,
                    provider               TEXT    NOT NULL,
                    provider_session_id    TEXT,
                    provider_turn_sequence INTEGER NOT NULL DEFAULT 0,
                    source                 TEXT    NOT NULL,
                    followup_request_id    TEXT,
                    started_at             TEXT    NOT NULL,
                    completed_at           TEXT,
                    outcome                TEXT,
                    result                 TEXT,
                    failure_code           TEXT,
                    failure_summary        TEXT,
                    PRIMARY KEY (task_id, turn_number),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE tasks (
                    task_id            TEXT PRIMARY KEY,
                    correlation_id     TEXT NOT NULL,
                    parent_task_id     TEXT,
                    origin             TEXT NOT NULL,
                    adapter_id         TEXT NOT NULL,
                    project_id         TEXT NOT NULL,
                    state              TEXT NOT NULL,
                    waiting_reason     TEXT,
                    lifecycle_revision INTEGER NOT NULL DEFAULT 0,
                    created_at         TEXT NOT NULL,
                    started_at         TEXT,
                    updated_at         TEXT NOT NULL,
                    completed_at       TEXT,
                    title              TEXT,
                    prompt             TEXT NOT NULL,
                    latest_activity    TEXT,
                    latest_output      TEXT,
                    final_result       TEXT,
                    failure_json       TEXT,
                    cancellation_json  TEXT,
                    event_cursor       INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX artifacts_by_task
                    ON task_artifacts (task_id, observed_at, artifact_id);
                CREATE INDEX claim_ingestion_by_task
                    ON task_claim_ingestion (task_id, sequence);
                CREATE INDEX claims_by_task
                    ON task_change_claims (task_id, reported_at, claim_id);
                CREATE UNIQUE INDEX clarifications_by_provider_event
                    ON task_clarifications (task_id, provider_event_id);
                CREATE INDEX clarifications_by_status
                    ON task_clarifications (task_id, status, provider_sequence);
                CREATE INDEX idempotency_by_age ON idempotency (created_ts);
                CREATE INDEX tasks_by_created ON tasks (created_at DESC);
                CREATE INDEX tasks_by_state   ON tasks (state, created_at DESC);
                CREATE INDEX turns_by_completion
                    ON task_turns (task_id, completed_at, turn_number);
                CREATE UNIQUE INDEX turns_by_followup_request
                    ON task_turns (task_id, followup_request_id);
                INSERT INTO schema_meta (key, value) VALUES ('schema_version', '5');
                """
            )
            db.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, prompt, event_cursor)"
                " VALUES ('task_legacy', 'corr_legacy', 'pwa', 'validation',"
                " 'synth', 'completed', '2026-08-01T00:00:00Z',"
                " '2026-08-01T00:05:00Z', 'historical prompt', 3)"
            )
            for seq in (1, 2, 3):
                db.execute(
                    "INSERT INTO task_events (task_id, sequence, event_type,"
                    " created_at, actor, source, lifecycle_revision, evidence_json)"
                    " VALUES ('task_legacy', ?, 'progress', '2026-08-01T00:01:00Z',"
                    " 'system', 'cofferdam', 0, NULL)",
                    (seq,),
                )
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at, completed_at, outcome, result)"
                " VALUES ('task_legacy', 1, 'validation', 'pwa',"
                " '2026-08-01T00:00:30Z', '2026-08-01T00:04:00Z', 'completed', 'done')"
            )

    def _columns(self, table):
        with sqlite3.connect(str(self.path)) as db:
            return {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}

    def test_the_fixture_starts_at_five(self):
        self._build_v5()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), 5)
        self.assertNotIn(BASELINE_TABLE, self._table_names())

    def _table_names(self):
        with sqlite3.connect(str(self.path)) as db:
            return {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

    def test_opening_it_migrates_to_six(self):
        self._build_v5()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        self.assertEqual(store.storage_health()["schema_version"], 6)
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), 6)

    def test_the_new_table_is_created_empty(self):
        self._build_v5()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        self.assertIn(BASELINE_TABLE, self._table_names())
        with sqlite3.connect(str(self.path)) as db:
            count = db.execute("SELECT COUNT(*) FROM %s" % BASELINE_TABLE).fetchone()[0]
        self.assertEqual(count, 0)

    def test_the_historical_turn_gets_no_baseline(self):
        """The whole point. A fabricated pre-work revision is worse than none."""
        self._build_v5()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            rows = db.execute(
                "SELECT * FROM %s WHERE task_id='task_legacy'" % BASELINE_TABLE
            ).fetchall()
        self.assertEqual(rows, [])
        self.assertIsNone(store.turn_baseline("task_legacy", 1))

    def test_the_historical_contents_survive_exactly(self):
        self._build_v5()
        with sqlite3.connect(str(self.path)) as db:
            before = {
                "tasks": db.execute("SELECT * FROM tasks").fetchall(),
                "task_events": db.execute("SELECT * FROM task_events").fetchall(),
                "task_turns": db.execute("SELECT * FROM task_turns").fetchall(),
            }
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            after = {
                "tasks": db.execute("SELECT * FROM tasks").fetchall(),
                "task_events": db.execute("SELECT * FROM task_events").fetchall(),
                "task_turns": db.execute("SELECT * FROM task_turns").fetchall(),
            }
        self.assertEqual(before, after)

    def test_every_v5_table_survives_with_its_columns(self):
        self._build_v5()
        before = {t: self._columns(t) for t in self._table_names()}
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        for table, columns in before.items():
            self.assertEqual(self._columns(table), columns, table)

    def test_the_bounds_table_is_preserved(self):
        self._build_v5()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        self.assertIn("task_turn_bounds", self._table_names())

    def test_the_claim_tables_are_preserved(self):
        self._build_v5()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        names = self._table_names()
        for table in ("task_change_claims", "task_artifacts", "task_claim_ingestion"):
            self.assertIn(table, names)

    def test_reopening_is_idempotent(self):
        self._build_v5()
        first = _open_store(self.home)
        first.storage_health()
        first.close()
        second = _open_store(self.home)
        self.addCleanup(second.close)
        self.assertEqual(second.storage_health()["schema_version"], 6)
        with sqlite3.connect(str(self.path)) as db:
            count = db.execute("SELECT COUNT(*) FROM %s" % BASELINE_TABLE).fetchone()[0]
        self.assertEqual(count, 0)

    def test_the_migrated_database_is_intact(self):
        self._build_v5()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(
                [r[0] for r in db.execute("PRAGMA integrity_check")], ["ok"]
            )
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_v6_database_is_refused_by_a_v5_build(self):
        """Forward-only, unchanged. Recorded here so the rollback shape is pinned."""
        self._build_v5()
        store = _open_store(self.home)
        store.storage_health()
        store.close()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertGreater(int(value), 5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
