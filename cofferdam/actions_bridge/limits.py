"""Every bound the bridge enforces, in one file, with the reason beside it.

Two rules govern this module and they point in opposite directions on purpose.

**On the way in, a request that exceeds a bound is refused, never truncated.**
A person can ask for a shorter task. Silently sending a clipped instruction to
an agent is a different instruction from the one somebody meant, and the failure
would show up as strange work rather than as an error.

**On the way out, a value that exceeds a bound is truncated, never refused.**
Nobody can retype an agent's result. Refusing to publish a long one would lose
it entirely, so it is cut at a stated length and the payload says it was cut.

This is Task Core's own rule (``tasks/models.py``), restated here because the
bridge's bounds are *smaller* than Task Core's rather than equal to them, and a
reader should be able to see both numbers without holding two files open.

Why smaller
-----------

The bridge sits between a model provider and a workstation, and it is sized for
that seat rather than for the surface behind it:

* GPT Actions cap a request or response payload at 100,000 characters. Every
  bound here is chosen so a maximal response cannot approach that ceiling — a
  truncated-by-ChatGPT payload is a corrupted one, and the failure mode is a
  model reading half a JSON document.
* GPT Actions time out at 45 seconds round trip. The upstream timeout below is
  well inside it, so a slow daemon produces a bounded bridge error rather than a
  ChatGPT-side timeout with an unknown server-side outcome.
* A conversation is not a task brief. A prompt field sized like Task Core's
  8,000 characters invites a model to paste a chat transcript into it, and the
  smaller number is the cheapest way to make summarising the default.
"""

from __future__ import annotations

#: Bumped when a published bridge response shape changes in a way a caller could
#: notice. Independent of ``TASK_API_VERSION``: the bridge normalizes rather than
#: mirrors, so the two can and should move separately.
BRIDGE_API_VERSION = 1

# -- what the Actions contract itself allows ----------------------------------
#
# Recorded as constants rather than prose so a test can assert the bridge stays
# inside them. Verified 2026-08-09 against OpenAI's "Production notes on GPT
# Actions"; see docs/ACTIONS_BRIDGE.md for the evidence record.

#: OpenAI's stated payload ceiling, in characters, each direction.
ACTIONS_PAYLOAD_CHARS = 100_000
#: OpenAI's stated round-trip timeout, in seconds.
ACTIONS_ROUND_TRIP_SECONDS = 45

# -- request bounds (refused when exceeded) -----------------------------------

#: The whole JSON body of any mutation. Generous for eight small objects and far
#: below anything that would need streaming to parse.
MAX_BODY_BYTES = 16 * 1024

#: The most header *fields* one request may carry, and the most bytes any one of
#: them may be. ChatGPT does not send custom headers — the Actions contract does
#: not support them — so anything unusual here is not the caller this bridge was
#: built for.
MAX_HEADER_COUNT = 64
MAX_HEADER_BYTES = 8 * 1024

#: A task instruction. Task Core allows 8,000; the bridge allows this, and the
#: gap is deliberate — see the module docstring.
MAX_TASK_TEXT_CHARS = 6_000
#: The optional expected-output summary, composed into the task text under a
#: fixed code-owned heading. Counted against :data:`MAX_TASK_TEXT_CHARS` too, so
#: the two together can never exceed what Task Core will accept.
MAX_EXPECTED_OUTPUT_CHARS = 1_000
#: A follow-up. Task Core allows 4,000.
MAX_FOLLOWUP_TEXT_CHARS = 3_000
#: A display title, matching Task Core's ``MAX_LABEL_CHARS``.
MAX_TITLE_CHARS = 120

#: A remote development instruction (M2M PR4). The planner contract allows 8,000
#: characters of intent; the workstation's ingress allows 4,000 and this matches
#: it exactly rather than approximating, so a caller is never told a different
#: number by the two sides of one request.
#:
#: Smaller than ``MAX_TASK_TEXT_CHARS`` on purpose, and the gap is the same
#: argument the module docstring makes about transcripts — more so here, because
#: the *host* supplies the project context. Anything the caller adds beyond a
#: bounded intent is duplicate context at best and somebody's chat log at worst.
MAX_DEVELOPMENT_INSTRUCTION_CHARS = 4_000
#: Optional bounded research the Custom GPT may have done. Reuses the planner
#: contract's existing ``research_notes`` field, which the contract prompt already
#: tells the model to treat as advisory rather than as project authority.
MAX_DEVELOPMENT_NOTES_CHARS = 4_000

#: The idempotency key. Bounded and pattern-checked — see
#: :func:`~cofferdam.actions_bridge.idempotency.valid_request_id`.
MAX_CLIENT_REQUEST_ID_CHARS = 64
MIN_CLIENT_REQUEST_ID_CHARS = 8

# -- response bounds (truncated when exceeded) --------------------------------

#: The longest result text one ``sync_task`` response carries. A result longer
#: than this is cut and the response says ``result_truncated: true``; the whole
#: text stays readable on the local surface, which is where a long one belongs.
MAX_RESULT_CHARS = 6_000
#: The bounded activity line.
MAX_ACTIVITY_CHARS = 400
#: A clarification question, matching what the provider layer already bounds it
#: to, so a question is never cut twice by two different numbers.
MAX_QUESTION_CHARS = 1_000
MAX_OPTION_LABEL_CHARS = 120
MAX_OPTION_DESCRIPTION_CHARS = 240
#: The most options one normalized question publishes.
MAX_OPTIONS = 8
#: A failure summary. Short on purpose: a bridge error is a sentence, and a
#: longer one usually means a stack trace found a way out.
MAX_ERROR_SUMMARY_CHARS = 200

#: Recent-task listing. ``MAX`` is a hard ceiling a caller cannot raise; asking
#: for more returns this many rather than an error, because a list is a list.
MAX_RECENT_TASKS = 20
DEFAULT_RECENT_TASKS = 10

#: The assembled response body cap. Well under :data:`ACTIONS_PAYLOAD_CHARS`.
#: Every field above is bounded, so this is a backstop rather than the mechanism
#: — and a backstop that fires is a bug, which is why a test asserts a maximal
#: response stays under it.
MAX_RESPONSE_BYTES = 60 * 1024

# -- time and rate ------------------------------------------------------------

#: How long the bridge waits on the Cofferdam daemon for one internal call.
#: Inside the Actions round-trip budget with room for the bridge's own work, so
#: a slow upstream becomes a bridge 504 rather than a ChatGPT-side timeout.
UPSTREAM_TIMEOUT_SECONDS = 20.0
#: The deadline for a whole bridge request, including every internal call it
#: makes. ``sync_task`` makes up to three.
REQUEST_DEADLINE_SECONDS = 30.0

#: Requests per minute per key, and the burst one may arrive in. One person with
#: one phone cannot reach these; a retry storm can.
RATE_LIMIT_PER_MINUTE = 60
RATE_LIMIT_BURST = 20
#: A second, tighter bucket for mutations only. Reads are cheap and idempotent;
#: a create, a follow-up or an answer reaches an agent.
MUTATION_RATE_LIMIT_PER_MINUTE = 20
MUTATION_RATE_LIMIT_BURST = 6

#: The most requests the bridge will process at once. Beyond this it refuses
#: with 429 rather than queueing: a queued request that ChatGPT has already
#: timed out on is work nobody will read.
MAX_CONCURRENT_REQUESTS = 4

__all__ = [name for name in dir() if name.isupper()]
