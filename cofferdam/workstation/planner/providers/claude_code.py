"""The first planner provider: the installed CLI, invoked with nothing enabled.

Every vendor-specific fact in the planner layer lives in this file — the
executable, the flags, the envelope shape, the model alias. ``protocol.py`` and
``models.py`` name none of them, which is what makes the role provider-neutral
in the sense D-2026-08-20-1 requires.

Three properties this module exists to guarantee, each verified by
``tests/test_planner_provider.py`` against the constructed argv rather than
against a docstring:

**No tools.** ``--tools ""`` is documented by the installed CLI as disabling all
tools, and the flag is a constant here. No request field reaches it.

**No MCP.** ``--strict-mcp-config`` with no ``--mcp-config`` yields a session
with no servers, including none inherited from a directory.

**A controlled working directory.** The M2L capability audit found that a
``-p`` session adopts its working directory's hooks and ``.mcp.json`` without an
approval prompt, because a print-mode session shows no trust dialog. The cure is
not to run in a directory that has any. ``--bare`` would also isolate it, but
the same audit found bare mode does not use the subscription login, so isolation
comes from the directory instead.

**Its own credentials, and no way to reach anybody else's.** Until M2M PR4 this
module passed no ``env`` to ``subprocess.run``, so the CLI inherited the daemon's
environment — ``HOME`` included — and authenticated as *the operator*. That was
already wrong and became untenable when a remote Custom GPT request could trigger
the invocation. The environment is now built by selection from
:mod:`~..session`, which is the planner's own namespace under
:mod:`cofferdam.workstation.claudeauth.session`: ``HOME`` points inside it, so
``~/.claude`` cannot resolve to the operator's session; ``CLAUDE_CONFIG_DIR``
points at the planner's own config root; and no API-key variable can be present
because nothing copies one in.

The session is also checked *before* the process starts — see
:meth:`ClaudeCodePlanner.session_status`. A planner that has never been logged in
refuses rather than falling back to whatever credential happens to be reachable,
which is the property the whole namespace exists for.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..contract import PLANNER_CONTRACT
from ..errors import (
    PlannerEnvelopeInvalid,
    PlannerInvocationFailed,
    PlannerResultMissing,
    PlannerTimeout,
    PlannerUnavailable,
)
from ..models import (
    PLANNER_RESULT_SCHEMA,
    DevelopmentRequest,
    validate_planner_result,
)
from ..protocol import (
    DevelopmentPlanner,
    PlannerCapabilities,
    PlanningTurn,
    ProviderExecution,
)

PROVIDER_ID = "claude-code"

DEFAULT_EXECUTABLE = "/usr/bin/claude"
#: An alias, resolved by the provider to whatever is current. Deliberately not a
#: dated model name: a marketing string baked into source is a thing that goes
#: stale silently. The alias is configuration; core logic never sees it.
DEFAULT_MODEL = "opus"
DEFAULT_TIMEOUT_SECONDS = 600

#: The short directive that goes in argv. The request itself travels on stdin,
#: so user- and Custom-GPT-authored text never appears in a process command line
#: or in anything that reads one.
STDIN_DIRECTIVE = (
    "The JSON object on stdin is the bounded development request. "
    "Return one JSON object matching the schema."
)

#: Files whose presence means the directory would inject configuration into a
#: print-mode session.
_CONTAMINANTS = (".mcp.json", "CLAUDE.md", ".claude")


def default_runtime_dir() -> Path:
    """Code-owned, never caller-selectable."""
    override = os.environ.get("COFFERDAM_PLANNER_RUNTIME_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "cofferdam" / "planner-runtime"


def prepare_runtime_dir(path: Optional[Path] = None) -> Path:
    """Create the planner's working directory and prove it is inert.

    Verified rather than assumed: a directory that has acquired a ``.mcp.json``
    or a ``CLAUDE.md`` since it was created is refused, because running there
    would silently re-enable exactly what this boundary removes.
    """
    directory = Path(path) if path is not None else default_runtime_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for name in _CONTAMINANTS:
        if (directory / name).exists():
            raise PlannerUnavailable(
                "planner runtime directory is contaminated",
                detail=f"{directory / name} exists and would be inherited",
            )
    return directory


def build_argv(
    *,
    executable: str,
    model: str,
    schema: Mapping[str, Any],
) -> List[str]:
    """The whole command line. A pure function, so a test can read it.

    Nothing from a :class:`DevelopmentRequest` appears here. The request travels
    on stdin; this is constants and configuration only.
    """
    return [
        executable,
        "-p",
        STDIN_DIRECTIVE,
        "--model",
        model,
        # Documented by the installed CLI as: use "" to disable all tools.
        "--tools",
        "",
        # No MCP servers, and none adopted from the working directory.
        "--strict-mcp-config",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--append-system-prompt",
        PLANNER_CONTRACT,
    ]


def _as_int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_float(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_envelope(raw: str, *, requested_model: str) -> Tuple[Any, ProviderExecution]:
    """Split the provider's process report from the model's output.

    Returns the untrusted ``structured_output`` payload and a
    :class:`ProviderExecution`. It does **not** validate the payload — that is
    the host's job, in :func:`~..models.validate_planner_result`, and keeping the
    two apart is what stops provenance and content being confused.
    """
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlannerEnvelopeInvalid(
            "provider did not return a JSON envelope", detail=str(exc)[:300]
        ) from exc
    if not isinstance(envelope, Mapping):
        raise PlannerEnvelopeInvalid("provider envelope is not an object")

    usage = envelope.get("usage") if isinstance(envelope.get("usage"), Mapping) else {}
    model_usage = (
        envelope.get("modelUsage")
        if isinstance(envelope.get("modelUsage"), Mapping)
        else {}
    )
    canonical: List[str] = []
    for entry in model_usage.values():
        if isinstance(entry, Mapping) and entry.get("canonicalModel"):
            canonical.append(str(entry["canonicalModel"]))

    # The primary model is the one whose name the requested alias resolved to,
    # when we can tell. Providers may make side calls with a smaller model, and
    # attributing the result to whichever arrived first would be a guess.
    actual = None
    for name in canonical:
        if requested_model.lower() in name.lower():
            actual = name
            break
    if actual is None and len(canonical) == 1:
        actual = canonical[0]

    execution = ProviderExecution(
        provider_id=PROVIDER_ID,
        requested_model=requested_model,
        actual_model=actual,
        models_used=tuple(sorted(canonical)),
        session_id=envelope.get("session_id") if isinstance(envelope.get("session_id"), str) else None,
        duration_ms=_as_int(envelope.get("duration_ms")),
        ttft_ms=_as_int(envelope.get("ttft_ms")),
        input_tokens=_as_int(usage.get("input_tokens")),
        output_tokens=_as_int(usage.get("output_tokens")),
        provider_reported_cost_estimate_usd=_as_float(envelope.get("total_cost_usd")),
    )

    if envelope.get("is_error"):
        raise PlannerInvocationFailed(
            "provider reported an error result",
            detail=str(envelope.get("api_error_status") or envelope.get("subtype"))[:300],
        )

    if "structured_output" not in envelope:
        # The model answered in prose. That is a failed planning turn; it is not
        # something to salvage by reading the free-text `result` field.
        raise PlannerResultMissing(
            "provider envelope carried no structured_output",
            detail="the model did not produce a schema-conforming object",
        )

    return envelope["structured_output"], execution


class ClaudeCodePlanner(DevelopmentPlanner):
    """Subscription-authenticated CLI, prompt-only."""

    planner_id = PROVIDER_ID

    def __init__(
        self,
        *,
        executable: str = DEFAULT_EXECUTABLE,
        model: str = DEFAULT_MODEL,
        runtime_dir: Optional[Path] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        runner: Optional[Any] = None,
        state_dir: Optional[Path] = None,
    ) -> None:
        self._executable = executable
        self._model = model
        self._runtime_dir = runtime_dir
        self._timeout = timeout_seconds
        # Injected only by tests. Production always uses subprocess.run.
        self._runner = runner or self._run_subprocess
        # Where the planner's own Claude session lives (M2M PR4). ``None`` means
        # this host has not told the provider where its state directory is, and
        # that is treated as *no session* rather than as permission to fall back
        # to whatever credential the process happens to have inherited.
        self._state_dir = Path(state_dir) if state_dir is not None else None

    # -- the credential boundary
    def session_status(self):
        """What can be said about the planner's own session, without opening it.

        Returns a :class:`~...claudeauth.session.SessionStatus`. ``cli_present`` is
        this provider's own executable check, so "the CLI is missing" and "the
        session is not signed in" stay two different answers.
        """
        from .. import session as planner_session

        if self._state_dir is None:
            return planner_session.status(
                Path("/nonexistent"), cli_present=Path(self._executable).exists()
            )
        return planner_session.status(
            self._state_dir, cli_present=Path(self._executable).exists()
        )

    def require_session(self) -> Path:
        """The planner's config root, or a typed refusal. **No fallback.**

        Raises :class:`~..session.PlannerSessionUnavailable`, which the ingress
        turns into ``planner_auth_required`` or ``planner_session_expired``. It
        never returns the operator's directory, the worker's directory, or
        ``None`` — there is no third outcome in which the run proceeds against
        some other credential.
        """
        from .. import session as planner_session

        if self._state_dir is None:
            raise planner_session.PlannerSessionUnavailable(
                planner_session.NAMESPACE.sentence(planner_session.STATUS_UNPREPARED),
                status=planner_session.STATUS_UNPREPARED,
                detail="this host has no planner session directory configured",
            )
        return planner_session.require_usable(
            self._state_dir, cli_present=Path(self._executable).exists()
        )

    # -- declarations
    def capabilities(self) -> PlannerCapabilities:
        return PlannerCapabilities(
            prepare_development_step=True,
            provider_schema_enforcement=True,
            enforced_no_tools=True,
        )

    def available(self) -> bool:
        return Path(self._executable).exists()

    def unavailable_reason(self) -> Optional[str]:
        if self.available():
            return None
        return f"planner executable not found at {self._executable}"

    def describe(self) -> Dict[str, Any]:
        """Non-secret. Reports the session's *status*, never its location.

        The config root is not here on purpose: a describe line naming the
        directory would be the one place this feature prints where the planner's
        credentials live.
        """
        described = super().describe()
        described["requested_model"] = self._model
        found = self.session_status()
        described["session"] = {
            "status": found.status,
            "usable": found.usable,
            "needs_login": found.needs_login,
        }
        return described

    # -- the one operation
    def prepare_development_step(self, request: DevelopmentRequest) -> PlanningTurn:
        if not isinstance(request, DevelopmentRequest):
            raise TypeError("prepare_development_step requires a DevelopmentRequest")
        if not self.available():
            raise PlannerUnavailable(self.unavailable_reason() or "planner unavailable")

        # Before anything is built or spawned. A planner with no session of its
        # own refuses here rather than starting a process that would authenticate
        # as somebody else.
        self.require_session()

        cwd = prepare_runtime_dir(self._runtime_dir)
        argv = build_argv(
            executable=self._executable,
            model=self._model,
            schema=PLANNER_RESULT_SCHEMA,
        )
        payload = json.dumps(request.to_prompt_payload(), ensure_ascii=False)

        raw = self._runner(argv, payload, cwd, self._timeout, self.environment())
        structured, execution = parse_envelope(raw, requested_model=self._model)
        result = validate_planner_result(structured)
        return PlanningTurn(result=result, execution=execution)

    def environment(self) -> Dict[str, str]:
        """The complete environment the CLI runs under. Built, never inherited.

        Five keys, all of them derived from this host's configuration and the
        planner's own namespace. The absences are the point and they are
        structural rather than filtered: there is no ``ANTHROPIC_API_KEY`` and no
        ``CLAUDE_CODE_OAUTH_TOKEN`` because nothing copies one in, and ``HOME``
        cannot reach the operator's home, so ``~/.claude`` has nowhere to resolve
        to but this namespace.
        """
        from .. import session as planner_session

        if self._state_dir is None:  # pragma: no cover - require_session refuses first
            raise planner_session.PlannerSessionUnavailable(
                "the planner has no session directory",
                status=planner_session.STATUS_UNPREPARED,
            )
        return planner_session.environment(self._state_dir)

    # -- transport
    def _run_subprocess(
        self,
        argv: List[str],
        stdin_text: str,
        cwd: Path,
        timeout: int,
        env: Dict[str, str],
    ) -> str:
        try:
            proc = subprocess.run(
                argv,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd),
                shell=False,
                # The whole environment, from `environment()`. Never
                # `os.environ`, never an overlay on it -- an inherited
                # `CLAUDE_CONFIG_DIR` or `HOME` would silently point this run at
                # the operator's session, which is the defect M2M PR4 exists to
                # close.
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PlannerTimeout(
                f"planner did not answer within {timeout}s", detail=str(exc)[:200]
            ) from exc
        except OSError as exc:
            raise PlannerUnavailable(
                "planner executable could not be run", detail=str(exc)[:200]
            ) from exc
        if proc.returncode != 0:
            # An auth failure is not a code failure. The CLI's own words are the
            # only way to tell them apart, and mislabelling in either direction
            # sends somebody to the wrong screen -- so classification happens
            # here and produces a session refusal rather than an invocation one.
            from .. import session as planner_session

            combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
            condition = planner_session.classify_auth_failure(combined)
            if condition is not None:
                raise planner_session.PlannerSessionUnavailable(
                    planner_session.NAMESPACE.sentence(condition),
                    status=condition,
                    # The CLI's stderr is not forwarded: it names the config root
                    # it was pointed at, which is a host path.
                    detail="the planner session was refused by the provider",
                )
            raise PlannerInvocationFailed(
                f"planner exited {proc.returncode}",
                detail=(proc.stderr or "").strip()[:500],
            )
        return proc.stdout


__all__ = [
    "PROVIDER_ID",
    "DEFAULT_EXECUTABLE",
    "DEFAULT_MODEL",
    "ClaudeCodePlanner",
    "build_argv",
    "parse_envelope",
    "prepare_runtime_dir",
    "default_runtime_dir",
]
