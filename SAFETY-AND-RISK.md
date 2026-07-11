# Safety and risk

**Status: the points below are the binding public-safe posture as of PR0. The full data-flow
diagram, invariant-to-mitigation mapping, and negative-first test results are added in PR03/PR04,
once the approval/executor/audit path exists — see "Where the full risk writeup lives" below.**

## Cofferdam is advice/tooling, not authority

Cofferdam does not make decisions for you. It is a tool that classifies and gates proposed
changes; **you** decide whether a change happens. Any model output Cofferdam ever surfaces
(from a later version onward) is advisory input to your decision, never a decision itself.

## You own the approval

**You own approvals and are responsible for reviewing every change before approving it.** Cofferdam
renders an exact dry-run of what will change; approving it without reading it is a use error, not
a safety property Cofferdam can substitute for. **Always verify the diff before you approve.**

The approval step is the interactive `cofferdam approve --file <proposal.json>` command: it shows the
complete patch on your terminal through one reversible, ASCII-only escape (a TAB as `\t`, each
trailing space as `\x20`, a literal backslash as `\\`, other non-ASCII as `\u{HEX}`, and a line
stating whether the patch ends with a final newline) so that no two different patches can look
identical, and records a single-use, five-minute approval only after you type an exact confirmation
phrase. Cofferdam renders those bytes faithfully — or refuses to approve content it cannot display
safely — but it **cannot make you read them**; that judgment is yours. Approving does **not** apply
the change (execution is a separate, later step); an approval only records that *you* authorized this
exact change, once, for the next five minutes. If the command reports that approval state is
**indeterminate** (a durability barrier failed after the record was written), an approval may already
exist — check with `cofferdam approval-status --file <proposal.json>` or wait for it to expire rather
than assuming it failed.

## AI outputs can be wrong

Any AI-generated proposal — now or once model review exists — can be wrong, incomplete, or
misleading. Cofferdam's guard does not evaluate whether a change is a *good idea*; it evaluates
whether a change is *in scope and well-formed*. Correctness review is yours.

## The guard is authoritative; advisory review cannot relax it

The deterministic guard is the sole authority over what is allowed, needs approval, or is blocked.
**Advisory review — the Review Room, from v0.2 onward — cannot relax, override, or bypass a guard
verdict.** A model saying "this looks safe" never turns a blocked or needs-approval proposal into
an allowed one.

## No auto-execution

Nothing is ever applied automatically. Every change requires an explicit, hash-bound, single-use,
expiring human approval before the executor runs.

## Secrets and keys

Secrets and API keys must never be printed or logged. Stored/displayed artifacts (dry-run output,
audit entries) pass through redaction; treat any artifact as untrusted content, not a place secrets
are safe to appear.

## No warranty

Cofferdam is provided **as is**, without warranty of any kind — see the License terms in
[`LICENSE`](LICENSE). Using an approval gate correctly is still your responsibility; Cofferdam
narrows the ways a change can go wrong, it does not eliminate the need for human judgment.

## Data flow (v0.1)

**v0.1 is zero-network: no data leaves your machine.** There is no model call, no API key, and no
network I/O anywhere in v0.1's code path — every stage (guard, dry-run, approval, execution,
audit) runs locally, on your filesystem, with no telemetry. **v0.1 does not yet ship BYOK,
provider calls, or multi-model review** — those belong to v0.2 and later, are not yet built, and
are not a committed promise.

## Data flow (v0.2+, when it exists)

From v0.2 onward, Cofferdam may send **prompts and diffs — and only prompts and diffs — to model
providers you explicitly configure (BYOK)**. Nothing is sent to any provider you have not
configured, and nothing is sent until that version exists and you opt in.

## Default posture

**Held back — fail-closed.** Nothing is ever auto-applied. A proposal that fails validation, an
approval that has expired or been replayed, or a guard re-check that disagrees at execution time
all result in *nothing happening* — never a best-effort partial write.

## Residual risk (v0.1, honest disclosure)

- v0.1 does not sandbox network access at the OS level; it relies on not invoking any
  network-capable code path. Implicit paths (the runtime itself, a `git` credential helper, DNS
  resolution as a side effect of tooling) are a documented limitation, not eliminated by a
  firewall.
- A hostile, concurrent local process could in principle race the canonicalize→approve→apply
  window; the pre-state hash and `git apply --check` narrow this but do not fully eliminate it
  under a compromised-local-environment threat model. See [`THREAT-MODEL.md`](THREAT-MODEL.md).
- Cofferdam does not protect against a compromised OS, a compromised `git` binary, or a malicious
  local insider — see [`SECURITY.md`](SECURITY.md).

## Where the full risk writeup lives

The complete data-flow diagram, the finalized invariant-to-mitigation mapping, and the
negative-first test results are published here once PR03 (approval + executor + audit) lands.
