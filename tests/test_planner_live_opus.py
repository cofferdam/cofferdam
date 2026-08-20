"""The whole vertical slice, against a real model. Skipped unless asked for.

Every other planner test fakes the provider, which is right: a suite that needs
a network and a subscription is a suite that fails for reasons that have nothing
to do with the change under review. But *nothing* faking the provider means
nothing ever proves the connected path — real workspace, real Context Builder,
real ContextProjector, real egress policy, real Opus, real validation, real
persistence — actually holds end to end.

So this runs only when ``COFFERDAM_LIVE_PLANNER=1``. It is the test that would
have caught the gap PR1c-b originally shipped with: live smokes that built their
own ``CloudContextProjection`` and therefore never exercised the projector.

It costs a real model call and about a minute. It never executes the prompt it
gets back.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from cofferdam.workstation.planner import (
    ACTION_PREPARE_WORKER_PROMPT,
    STATUS_SUCCEEDED,
    PlannerService,
    PlannerStore,
)
from cofferdam.workstation.planner.providers import ClaudeCodePlanner

from .test_context_builder import FROZEN, ContextHarness

LIVE = os.environ.get("COFFERDAM_LIVE_PLANNER") == "1"

INTENT = (
    "planner sonucu kalıcı olarak kaydedilsin ve sonradan okunabilsin istiyorum, "
    "ama planner kendi başına worker çalıştırmasın; önce bana göstersin. "
    "context olarak da sadece cloud'a çıkmasına izin verilenler gitsin."
)

GUIDANCE = (
    "Prompt-writing rules: state the objective, the exact files in scope, what must "
    "NOT change, the authority/escalation rule, acceptance criteria, the exact test "
    "command, stop conditions, and what to report back. Do not dump project history."
)

NOTES = (
    "Research from the Custom GPT: writing the request row before invoking the "
    "provider makes an interrupted call visible rather than invisible; a single "
    "UPDATE for result plus provenance avoids a torn succeeded-with-no-result row."
)


@unittest.skipUnless(LIVE, "set COFFERDAM_LIVE_PLANNER=1 to run the real Opus slice")
class LiveVerticalSlice(ContextHarness):
    """Real workspace → real projection → real Opus → durable record."""

    def write_project_documents(self) -> None:
        """Real project documents, so the step is genuinely derivable.

        The harness's defaults describe a fictional project in three sentences.
        Against those, Opus correctly answered ``ASK_USER`` — it had no way to
        write an implementation prompt without inventing requirements, which is
        the restraint the contract asks for. Proving the
        ``PREPARE_WORKER_PROMPT`` slice therefore needs a workspace whose
        documents actually determine the step, so they are written here and go
        through the same builder and the same projector as any other content.
        """
        (self.project_root / "STATUS.md").write_text(
            "# Status\n\nM2L PR1c-a merged: the planner contracts and the Claude "
            "Code provider. There is no planner UI, no Actions Bridge route and no "
            "worker dispatch anywhere in the system.\n",
            encoding="utf-8",
        )
        (self.project_root / "ROADMAP.md").write_text(
            "# Roadmap\n\n## M2L PR1c-b\n\nDurable planner persistence and an "
            "internal read surface. Worker dispatch is NOT in scope and requires "
            "later explicit user confirmation. No new public ingress, no "
            "deployment.\n",
            encoding="utf-8",
        )
        (self.project_root / "DECISIONS.md").write_text(
            "# Decisions\n\n## D-2026-08-20-1\n\nCofferdam is local-first in "
            "authority, state, memory, evidence and execution control. Intelligence "
            "may be cloud. The planner is a provider-neutral role.\n\n"
            "## D-2026-08-20-2\n\nCofferdam is the central orchestrator. Planner "
            "output is DATA, never EXECUTION. A PlannerResult must not contain "
            "command, argv, shell, cwd, env or tool_name.\n",
            encoding="utf-8",
        )
        (self.project_root / "DESIGN.md").write_text(
            "# Design\n\nA cloud-backed planner is external: it receives only a "
            "CloudContextProjection, never a LocalContextPack. It runs with no "
            "tools, no MCP and a controlled working directory. The planner package "
            "is cofferdam/workstation/planner/ with models.py, protocol.py, "
            "store.py, service.py and providers/claude_code.py. Planner state lives "
            "in planner.sqlite3, its own database. Tests run with "
            "`python -m unittest discover -s tests -t .`\n",
            encoding="utf-8",
        )

    def setUp(self) -> None:
        super().setUp()
        # An inactive workspace yields a pack holding only the user message,
        # which the policy excludes — an empty projection, and the reason the
        # first live run correctly answered ASK_USER.
        self.activate()
        from cofferdam.workstation.context.projection import (
            ContextProjector,
            HostRedactionEnvironment,
        )

        self.projector = ContextProjector(
            redaction=HostRedactionEnvironment(
                cofferdam_home=str(self.home),
                project_roots=(str(self.project_root),),
                vault_roots=(str(self.vault_root),),
                slot_roots=(
                    str(self.home / "slots" / "a"),
                    str(self.home / "slots" / "b"),
                ),
                home_directories=(str(self.home),),
            ),
            clock=lambda: FROZEN,
        )
        self.store = PlannerStore(self.home / "planner-state")
        executable = os.environ.get(
            "COFFERDAM_PLANNER_EXECUTABLE", str(Path.home() / ".local/bin/claude")
        )
        self.planner = ClaudeCodePlanner(
            executable=executable,
            model="opus",
            runtime_dir=self.home / "planner-runtime",
            timeout_seconds=900,
        )
        self.service = PlannerService(
            store=self.store,
            planner=self.planner,
            context=self.builder,
            projector=self.projector,
        )

    def test_the_whole_slice_prepares_a_prompt_and_starts_nothing(self):
        from cofferdam.workstation.tasks.adapters import build_registry

        self.assertTrue(self.planner.available(), "no authenticated CLI on this host")

        outcome = self.service.prepare_development_step(
            user_intent=INTENT,
            research_notes=NOTES,
            prompt_writing_guidance=GUIDANCE,
            authority_boundary=(
                "This step may modify only cofferdam/workstation/planner/ and its "
                "tests. It may not deploy, add a public route, or dispatch a worker."
            ),
        )

        record = outcome.record
        self.assertTrue(outcome.ok, f"planner failed: {record.failure_message}")
        self.assertEqual(record.status, STATUS_SUCCEEDED)
        self.assertEqual(record.action, ACTION_PREPARE_WORKER_PROMPT)

        # A real model answered, through the real provider.
        self.assertEqual(record.requested_model, "opus")
        self.assertTrue(record.actual_model, "no canonical model was reported")
        self.assertIn("opus", (record.actual_model or "").lower())
        self.assertGreater(record.duration_ms or 0, 0)

        # A prompt worth handing to a worker.
        self.assertTrue(record.has_prepared_prompt)
        self.assertGreater(len(record.worker_prompt), 800)
        self.assertIsNone(record.user_question)

        # Durable, and the read surface agrees with what was returned.
        self.assertEqual(self.store.get(record.planner_request_id).to_dict(),
                         record.to_dict())

        # The packet that left the host went through the real projector, and the
        # host's own paths did not survive it.
        payload = json.dumps(
            self.store.request_payload(record.planner_request_id), ensure_ascii=False
        )
        self.assertIn("project_context", payload)
        self.assertNotIn(str(self.project_root), payload)
        self.assertNotIn(str(self.vault_root), payload)
        self.assertNotIn(str(self.home), payload)

        # Nothing was started. The prompt is a string in a column.
        self.assertEqual(build_registry().ids(), ())
        self.assertFalse((self.home / "planner-state" / "tasks.sqlite3").exists())

        if os.environ.get("COFFERDAM_LIVE_PLANNER_DUMP"):  # pragma: no cover
            Path(os.environ["COFFERDAM_LIVE_PLANNER_DUMP"]).write_text(
                record.worker_prompt, encoding="utf-8"
            )
