"""The only way the bridge talks to Cofferdam. Ten calls, all of them constants.

This module is the SSRF boundary, and the shape of it is the argument.

There is no ``request(method, path)`` here. There is no ``get(url)``. Every
operation is a **named method with no URL parameter**, and the path it builds
comes from a constant in this file plus, where a path segment is needed, an
identifier that has been matched against a compiled pattern first. A caller
cannot pass a URL, a method, a header, a query string or a hostname, because
none of those is an argument anywhere in the file.

Why not a small generic helper with an allowlist
------------------------------------------------

Because an allowlist is a value and a method is a shape. A helper that took a
path and checked it against a set would be one refactor away from taking a path
and checking it against a set that had grown; ten methods cannot grow without
somebody writing an eleventh, in this file, under review.

The five things that are structurally impossible here
-----------------------------------------------------

* **A caller-chosen URL.** The base comes from validated configuration and every
  suffix is a literal.
* **A caller-chosen header.** :meth:`_headers` builds the whole dictionary from
  constants and the internal token. Nothing from a request reaches it.
* **A caller-chosen method.** Each method names ``GET`` or ``POST`` inline.
* **A path-traversal segment.** Task, project and question ids are matched
  against patterns that exclude ``/``, ``.`` and ``%`` before interpolation, and
  are refused rather than escaped — an id that needs escaping is not an id.
* **A redirect off-host.** ``follow_redirects=False``. A 3xx from the daemon is
  an upstream fault, not an instruction; following one is how a compromised or
  misconfigured upstream turns a fixed client into an open one.

What it does *not* do
---------------------

It does not touch SQLite, import a provider, resolve a project root, hold a
provider session or write task state. Everything below is an HTTP call to a
route that already exists, already authenticates, and already owns the
decision.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from .errors import (
    CODE_INTERNAL,
    CODE_UPSTREAM_TIMEOUT,
    CODE_UPSTREAM_UNAVAILABLE,
    BridgeError,
    from_upstream_code,
    status_for,
)
from .limits import MAX_RESPONSE_BYTES

# -- the allowlist, as literals -----------------------------------------------
#
# Named so a test can read them, and so the OpenAPI-versus-routes test can prove
# the bridge's external surface never grows a path that is not backed by one of
# these. The three with a placeholder are formatted with a *validated* id and
# nothing else.

ROUTE_PROJECTS = "/api/task-projects"
ROUTE_TASKS = "/api/tasks"
ROUTE_TASK = "/api/tasks/{task_id}"
ROUTE_TASK_RESULT = "/api/tasks/{task_id}/result"
ROUTE_TASK_CLARIFICATIONS = "/api/tasks/{task_id}/clarifications"
ROUTE_TASK_ANSWER = "/api/tasks/{task_id}/clarifications/{question_id}/answer"
ROUTE_TASK_FOLLOWUPS = "/api/tasks/{task_id}/followups"
ROUTE_TASK_CANCEL = "/api/tasks/{task_id}/cancel"
ROUTE_TASK_FINISH = "/api/tasks/{task_id}/finish"
#: M2J PR4. The one read whose response is shaped to leave the host. The bridge
#: knows the path and nothing about what comes back through it.
ROUTE_PROJECT_CONTEXT = "/api/projects/{project_id}/context"

#: M2M PR2. The read-only operations surface. Four GETs and nothing else --
#: there is no operations route on this client that writes, and the workstation
#: has none to call.
ROUTE_OPERATIONS = "/api/operations"
ROUTE_PROJECT_OPERATIONS = "/api/operations/{project_id}"
ROUTE_OPERATION_PROMPT = "/api/operations/{project_id}/prompt/{planner_request_id}"
ROUTE_OPERATION_RESULT = "/api/operations/{project_id}/result/{dispatch_id}"

#: Every upstream route this bridge may ever reach, as templates. A test asserts
#: that no other ``/api`` string appears in this package.
ALLOWED_UPSTREAM_ROUTES: Tuple[str, ...] = (
    ROUTE_PROJECTS,
    ROUTE_TASKS,
    ROUTE_TASK,
    ROUTE_TASK_RESULT,
    ROUTE_TASK_CLARIFICATIONS,
    ROUTE_TASK_ANSWER,
    ROUTE_TASK_FOLLOWUPS,
    ROUTE_TASK_CANCEL,
    ROUTE_TASK_FINISH,
    ROUTE_PROJECT_CONTEXT,
)

# -- identifier patterns ------------------------------------------------------
#
# Mirrors of the grammars the daemon already enforces, applied *before* an id
# becomes a path segment. Duplicated deliberately: relying on the far side to
# reject a malformed id means the malformed id has already been interpolated
# into a URL by the time anybody objects.

#: ``task_`` plus 26 Crockford base32 characters. See ``tasks/identity.py``.
_TASK_ID = re.compile(r"\A task_ [0-9a-hjkmnp-tv-z]{26} \Z", re.VERBOSE)
#: ``q_`` plus 24 lowercase hex. See ``tasks/clarifications.py``.
_QUESTION_ID = re.compile(r"\A q_ [0-9a-f]{24} \Z", re.VERBOSE)
#: Lowercase, digits, dash, underscore. See ``tasks/projects.py``.
_PROJECT_ID = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
#: Cofferdam's own option identifiers — ``opt1``, ``opt2``. See
#: ``tasks/clarifications.py``. A leading *letter* is required, which is why a
#: display number like ``"2"`` can never be one, and why the bridge can tell a
#: model it sent the wrong thing instead of forwarding it to be refused.
_OPTION_ID = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")


class UpstreamRefusal(BridgeError):
    """Cofferdam refused, in words the bridge is willing to publish.

    A ``BridgeError`` subclass rather than a separate type, so a route handler
    that forgets to catch it still produces a bounded envelope instead of a 500.
    """


def valid_task_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_TASK_ID.match(value))


def valid_question_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_QUESTION_ID.match(value))


def valid_project_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_PROJECT_ID.match(value))


def valid_option_id(value: Any) -> bool:
    """Whether this could be one of Cofferdam's option identifiers.

    A *grammar* check, not an authorization one. Cofferdam still checks the id
    against the question's own list, which is the check that matters. This one
    exists so that the single most likely model mistake — sending the display
    number the user said — is refused with a message that explains it, rather
    than travelling to the daemon to come back as "that option is not offered".
    """
    return isinstance(value, str) and bool(_OPTION_ID.match(value))


def _checked(value: Any, pattern: re.Pattern, what: str) -> str:
    """An identifier that is safe to interpolate, or a refusal.

    Refused, never quoted or escaped. ``urllib.parse.quote`` would turn
    ``../../secrets`` into a legal-looking segment and send it, and a client
    that needs its input escaped to be safe is a client whose input should not
    have been accepted.
    """
    if not isinstance(value, str) or not pattern.match(value):
        raise BridgeError(
            code=CODE_INTERNAL,
            message="internal error",
            status_code=500,
            detail="malformed " + what,
        )
    return value


class InternalTaskClient:
    """A fixed, authenticated client for ten Cofferdam task operations.

    Constructed once at startup with the internal token and never handed one
    again. The token lives in a private attribute that no method returns, no
    exception carries and no log line reads — the only place it is used is
    :meth:`_headers`, which builds a fresh dictionary per call.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout: float,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            # Not negotiable, and both halves matter. A redirect would let an
            # upstream move this client to another origin; a large response
            # would let it exhaust memory before any bound is applied.
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "cofferdam-actions-bridge"},
        )

    def close(self) -> None:
        self._client.close()

    # -- the wire ------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        """The complete header set, built from constants and the token.

        A whole dictionary rather than an update of a caller's: there is no
        parameter here for a caller to add to, so no request field can become a
        header on an internal call.
        """
        return {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
        }

    def _call(
        self, method: str, path: str, *, body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """One internal call. ``method`` and ``path`` are literals from above."""
        headers = self._headers()
        content = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            response = self._client.request(
                method, path, headers=headers, content=content
            )
        except httpx.TimeoutException:
            raise BridgeError(
                code=CODE_UPSTREAM_TIMEOUT,
                message=(
                    "Cofferdam did not answer in time. The work may still be "
                    "running — sync the task rather than sending it again."
                ),
                status_code=status_for(CODE_UPSTREAM_TIMEOUT),
            )
        except httpx.HTTPError:
            # The exception is not passed through. httpx puts the target URL in
            # its message, and that URL is this workstation's internal address.
            raise BridgeError(
                code=CODE_UPSTREAM_UNAVAILABLE,
                message="Cofferdam is not reachable from the bridge right now.",
                status_code=status_for(CODE_UPSTREAM_UNAVAILABLE),
            )

        if len(response.content) > MAX_RESPONSE_BYTES:
            # The daemon bounds its own payloads, so this is a backstop. It
            # fires before parsing, because a bound applied after `json.loads`
            # has already paid the cost it was meant to prevent.
            raise BridgeError(
                code=CODE_UPSTREAM_UNAVAILABLE,
                message="Cofferdam returned more than the bridge will carry.",
                status_code=status_for(CODE_UPSTREAM_UNAVAILABLE),
            )

        try:
            payload = response.json()
        except ValueError:
            raise BridgeError(
                code=CODE_UPSTREAM_UNAVAILABLE,
                message="Cofferdam returned something the bridge cannot read.",
                status_code=status_for(CODE_UPSTREAM_UNAVAILABLE),
            )
        if not isinstance(payload, dict):
            raise BridgeError(
                code=CODE_UPSTREAM_UNAVAILABLE,
                message="Cofferdam returned something the bridge cannot read.",
                status_code=status_for(CODE_UPSTREAM_UNAVAILABLE),
            )

        if response.status_code >= 400:
            raise self._refusal(payload)
        # Carried alongside the body so `create_task` can tell 201 from 200 —
        # which is how Task Core says "this key had already been used".
        payload["_status"] = response.status_code
        return payload

    @staticmethod
    def _refusal(payload: Dict[str, Any]) -> BridgeError:
        """Translate Cofferdam's error envelope into the bridge's.

        The upstream ``detail`` is dropped rather than forwarded. Task Core's
        details are written for the person who owns the machine and mention
        states, adapters and reasons that describe the workstation's insides;
        the bridge publishes its own sentence and its own code.
        """
        envelope = payload.get("error")
        upstream_code = None
        upstream_message = None
        if isinstance(envelope, dict):
            upstream_code = envelope.get("code")
            upstream_message = envelope.get("message")
        code = from_upstream_code(upstream_code)
        message = (
            upstream_message
            if isinstance(upstream_message, str) and upstream_message
            else "Cofferdam refused that."
        )
        return UpstreamRefusal(
            code=code,
            message=message,
            status_code=status_for(code),
            # The upstream code, and only the code. It is a closed vocabulary of
            # Cofferdam's own words with no content in it, and it is the single
            # most useful thing for diagnosing a refusal from a phone.
            detail=str(upstream_code) if upstream_code else None,
        )

    # -- the ten operations --------------------------------------------------

    def list_projects(self) -> Dict[str, Any]:
        return self._call("GET", ROUTE_PROJECTS)

    def get_project_context(self, project_id: str) -> Dict[str, Any]:
        """One GET. The bridge builds no context and inspects none (M2J PR4).

        Everything that makes this safe happens on the other side of the call:
        the workstation resolves the project to its active workspace, builds the
        `LocalContextPack`, applies `project_context_external_v1` and serializes
        the `CloudContextProjection`. This method forwards a validated id and
        returns whatever bounded payload came back.

        The id is **refused rather than escaped** upstream in the route, for the
        reason `_task_id` gives: percent-encoding a hostile id turns a rejection
        into a request for something else.
        """
        return self._call(
            "GET", ROUTE_PROJECT_CONTEXT.format(project_id=project_id)
        )

    def read_operations(self) -> Dict[str, Any]:
        """What Cofferdam is doing, across every enabled project. One GET."""
        return self._call("GET", ROUTE_OPERATIONS)

    def read_project_operations(self, project_id: str) -> Dict[str, Any]:
        return self._call(
            "GET", ROUTE_PROJECT_OPERATIONS.format(project_id=project_id)
        )

    def read_operation_prompt(
        self, project_id: str, planner_request_id: str
    ) -> Dict[str, Any]:
        """The exact approved prompt, addressed by project *and* durable id.

        Both ids are validated by the bridge route before they arrive here, and
        both are refused rather than escaped upstream -- percent-encoding a
        hostile id turns a rejection into a request for something else, which is
        the reasoning `_task_id` already gives for task ids.
        """
        return self._call(
            "GET",
            ROUTE_OPERATION_PROMPT.format(
                project_id=project_id, planner_request_id=planner_request_id
            ),
        )

    def read_operation_result(
        self, project_id: str, dispatch_id: str
    ) -> Dict[str, Any]:
        return self._call(
            "GET",
            ROUTE_OPERATION_RESULT.format(
                project_id=project_id, dispatch_id=dispatch_id
            ),
        )

    def list_tasks(self, *, limit: int) -> Dict[str, Any]:
        # The only query string in the file, and its one value is an integer
        # this method clamps. It is not interpolated from a caller's string.
        bounded = max(1, min(int(limit), 100))
        return self._call("GET", f"{ROUTE_TASKS}?limit={bounded:d}")

    def create_task(
        self,
        *,
        project_id: str,
        adapter_id: Optional[str],
        prompt: str,
        client_request_id: str,
        title: Optional[str],
    ) -> Dict[str, Any]:
        """Create one task.

        The body is assembled here from five named parameters. It is not a
        caller's dictionary forwarded on, so a field the bridge does not know
        about cannot reach Task Core even if something upstream of this method
        started accepting one.
        """
        body: Dict[str, Any] = {
            "project_id": _checked(project_id, _PROJECT_ID, "project id"),
            "prompt": prompt,
            "client_request_id": client_request_id,
        }
        if adapter_id is not None:
            body["adapter_id"] = adapter_id
        if title is not None:
            body["title"] = title
        return self._call("POST", ROUTE_TASKS, body=body)

    def get_task(self, task_id: str) -> Dict[str, Any]:
        return self._call(
            "GET", ROUTE_TASK.format(task_id=_checked(task_id, _TASK_ID, "task id"))
        )

    def get_result(self, task_id: str) -> Dict[str, Any]:
        return self._call(
            "GET",
            ROUTE_TASK_RESULT.format(
                task_id=_checked(task_id, _TASK_ID, "task id")
            ),
        )

    def list_clarifications(self, task_id: str) -> Dict[str, Any]:
        return self._call(
            "GET",
            ROUTE_TASK_CLARIFICATIONS.format(
                task_id=_checked(task_id, _TASK_ID, "task id")
            ),
        )

    def answer_clarification(
        self, *, task_id: str, question_id: str, option_id: str
    ) -> Dict[str, Any]:
        """Submit exactly one option id.

        ``option_ids`` is a one-element list built here, and there is **no
        ``answer`` key in this body at all**. Task Core would accept free text
        on this route from the PWA, where a person typed it; the bridge does not
        offer that channel and therefore cannot forward prose a model composed
        into a question's answer slot.
        """
        return self._call(
            "POST",
            ROUTE_TASK_ANSWER.format(
                task_id=_checked(task_id, _TASK_ID, "task id"),
                question_id=_checked(question_id, _QUESTION_ID, "question id"),
            ),
            body={"option_ids": [option_id]},
        )

    def send_followup(
        self, *, task_id: str, followup: str, client_request_id: str
    ) -> Dict[str, Any]:
        return self._call(
            "POST",
            ROUTE_TASK_FOLLOWUPS.format(
                task_id=_checked(task_id, _TASK_ID, "task id")
            ),
            body={"followup": followup, "client_request_id": client_request_id},
        )

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        # An empty body, because the upstream route accepts no fields. Nothing
        # here signals a process, names a pid or chooses a stop mode — see
        # ``TaskService.cancel_task``.
        return self._call(
            "POST",
            ROUTE_TASK_CANCEL.format(
                task_id=_checked(task_id, _TASK_ID, "task id")
            ),
            body={},
        )

    def finish_task(self, task_id: str) -> Dict[str, Any]:
        return self._call(
            "POST",
            ROUTE_TASK_FINISH.format(
                task_id=_checked(task_id, _TASK_ID, "task id")
            ),
            body={},
        )


__all__ = [
    "ALLOWED_UPSTREAM_ROUTES",
    "InternalTaskClient",
    "UpstreamRefusal",
    "valid_option_id",
    "valid_project_id",
    "valid_question_id",
    "valid_task_id",
]
