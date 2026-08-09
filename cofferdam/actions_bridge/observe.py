"""The bridge's log line: a fixed field set, and no parameter for content.

The privacy rule is enforced by the **signature**, not by discipline at each
call site. :func:`log_request` takes eight named arguments and none of them can
hold task text, a question, an answer, a result, a header, a body or a
credential — so there is no call anybody could write that logs one. That is the
same technique ``actions.py`` uses for the workstation's action records, and it
is the only version of this rule that survives a refactor.

What is logged
--------------

A request id the bridge minted, the operation, the safe display reference, the
HTTP status, the duration in milliseconds, whether it was an idempotent replay,
and a bounded error *code* from the closed vocabulary in
:mod:`~cofferdam.actions_bridge.errors`.

What is not logged, and has no parameter
----------------------------------------

The external API key · the internal token · task text · follow-up text · result
text · question text · option labels · answer selections · the canonical task id
· provider session ids · request bodies · response bodies · headers ·
environment · stack traces.

The **canonical task id** deserves its own note, because it is the one somebody
would reasonably argue for. It appears in URLs and in the daemon's own audit
records already. It is left out here because this log describes traffic from a
model provider, and correlating that traffic to specific tasks is precisely the
join a leaked log file would make possible. The display reference is a digest,
which is enough to follow one conversation through a log and not enough to
address anything.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

LOGGER_NAME = "cofferdam.actions_bridge"

_REQUEST_ID_BYTES = 6


def new_request_id() -> str:
    """A short opaque id for one bridge request. Not the caller's, and not a task's."""
    return "br_" + secrets.token_hex(_REQUEST_ID_BYTES)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_request(
    logger: logging.Logger,
    *,
    request_id: str,
    operation: str,
    status: int,
    duration_ms: int,
    display_ref: Optional[str] = None,
    replayed: bool = False,
    error_code: Optional[str] = None,
) -> None:
    """One bounded operational line. Every argument is a safe scalar.

    ``operation`` is an operationId from the bridge's own closed set, not a raw
    path — a path would carry whatever segment a caller sent, including one that
    was refused for being malformed, which is how a rejected value ends up in a
    log anyway.
    """
    logger.info(
        "%s op=%s ref=%s status=%d ms=%d replay=%s err=%s",
        request_id,
        operation,
        display_ref or "-",
        status,
        duration_ms,
        "yes" if replayed else "no",
        error_code or "-",
    )


__all__ = ["LOGGER_NAME", "get_logger", "log_request", "new_request_id"]
