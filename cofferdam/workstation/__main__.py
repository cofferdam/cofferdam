"""Entry point: ``python -m cofferdam.workstation``.

Binds to ``COFFERDAM_BIND_HOST`` (default ``127.0.0.1``). For phone access the
host-setup runbook binds to the machine's **Tailscale** address — never to
``0.0.0.0`` on a public interface. If a non-loopback bind is requested, the
service says so explicitly on stderr so an accidental public bind is visible.

The device token is printed **once, to stderr, only when it was just
generated**, so the maintainer can copy it into the phone. It is never printed
on subsequent starts and never written to logs.

Waiting for the bind address — an M1.1 finding
---------------------------------------------
The daemon starts at boot through lingering, and ``tailscaled`` often has no
address assigned yet at that moment. Binding straight to the configured
Tailscale IP then failed with ``EADDRNOTAVAIL`` and the process exited, which
under the old unit's ``Restart=on-failure`` plus ``StartLimitIntervalSec=0``
meant a respawn every three seconds, forever.

The fix is a *bounded* wait: poll until the address becomes assignable, up to
``COFFERDAM_BIND_WAIT_SECONDS`` (default 120), then give up cleanly with a
diagnostic. Falling back to a wildcard bind would turn a private service into a
public one, so it is never done. Together with the unit's ``StartLimitBurst``,
a permanently missing address now produces a bounded degraded state instead of
a restart storm.
"""

from __future__ import annotations

import argparse
import errno
import ipaddress
import os
import socket as socket_module
import sys
import time
from typing import Optional, Sequence

from .config import load_config, load_or_create_token

# ``create_app`` is imported inside main() rather than here: it pulls in
# FastAPI, and keeping this module importable with the standard library alone
# lets the boot-safety logic below (bind waiting, loopback detection) be tested
# on the stdlib-only CI path, where a missing extra would otherwise be an
# import error rather than a test result.

# How long to wait for a configured non-loopback address (e.g. Tailscale) to be
# assigned to a local interface before giving up.
ENV_BIND_WAIT = "COFFERDAM_BIND_WAIT_SECONDS"
DEFAULT_BIND_WAIT_SECONDS = 120.0
BIND_POLL_SECONDS = 2.0


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "")


def _address_assignable(host: str) -> bool:
    """True if ``host`` is an address this machine can currently bind to.

    Binds to port 0 and closes immediately: that answers "does the interface
    carrying this address exist yet" without reserving the real port or racing
    the server about to take it.
    """
    for family in (socket_module.AF_INET, socket_module.AF_INET6):
        try:
            info = socket_module.getaddrinfo(host, 0, family, socket_module.SOCK_STREAM)
        except socket_module.gaierror:
            continue
        for *_, sockaddr in info:
            probe = socket_module.socket(family, socket_module.SOCK_STREAM)
            try:
                probe.bind(sockaddr)
                return True
            except OSError as exc:
                # EADDRNOTAVAIL is the "interface is not up yet" case worth
                # waiting on. Anything else (EACCES, EADDRINUSE) is a real
                # condition that waiting will not resolve, so stop waiting and
                # let the bind itself report it.
                if exc.errno != errno.EADDRNOTAVAIL:
                    return True
            finally:
                probe.close()
    return False


def _bind_wait_seconds() -> float:
    raw = os.environ.get(ENV_BIND_WAIT)
    if raw is None:
        return DEFAULT_BIND_WAIT_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_BIND_WAIT_SECONDS


def wait_for_bind_address(host: str, timeout: float, *, poll: float = BIND_POLL_SECONDS) -> bool:
    """Block until ``host`` is assignable, or ``timeout`` elapses.

    Returns True if the address became available. Never falls back to another
    address: a private service that cannot reach its private interface has to
    stay down rather than move to a public one.
    """
    if _address_assignable(host):
        return True
    deadline = time.monotonic() + timeout
    print(
        f"[cofferdam] {host} is not assigned to any interface yet — waiting up to "
        f"{timeout:.0f}s for it (is tailscaled still coming up?).",
        file=sys.stderr,
    )
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll, remaining))
        if _address_assignable(host):
            return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cofferdam-workstation", description="Cofferdam workstation service (M1)")
    parser.add_argument("--host", help="bind address (default: COFFERDAM_BIND_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="bind port (default: COFFERDAM_BIND_PORT or 7101)")
    parser.add_argument("--print-token", action="store_true", help="print the device token to stderr and exit")
    parser.add_argument(
        "--enable-validation-task-adapter",
        action="store_true",
        help=(
            "register the deterministic validation task adapter. It runs no "
            "program and calls no model; it exists to exercise the task "
            "lifecycle on a validation runtime. Off unless this flag is given."
        ),
    )
    parser.add_argument(
        "--enable-claude-code-adapter",
        action="store_true",
        help=(
            "register the Claude Code task adapter. It launches the installed "
            "Claude Code CLI inside a project from the host's project registry, "
            "where it can read and edit files. It has no shell and cannot leave "
            "that folder. Off unless this flag is given."
        ),
    )
    parser.add_argument(
        "--enable-claude-agent-sdk-adapter",
        action="store_true",
        help=(
            "register the Claude Agent SDK task adapter. It runs Claude through "
            "the official Agent SDK inside a project from the host's project "
            "registry, with the same tool profile and no shell. It does not "
            "replace the Claude Code adapter; both may be enabled. Needs the "
            "'agent-sdk' extra installed. Off unless this flag is given."
        ),
    )
    args = parser.parse_args(argv)

    config = load_config()
    if args.host:
        config = type(config)(**{**config.__dict__, "bind_host": args.host})
    if args.port:
        config = type(config)(**{**config.__dict__, "bind_port": args.port})
    if args.enable_validation_task_adapter:
        # One direction only. The flag can turn the validation adapter *on*; its
        # absence never turns off something the host's own config.json or
        # environment enabled, because a switch that silently disabled a
        # configured surface would be as surprising as one that enabled an
        # unconfigured one.
        config = type(config)(
            **{**config.__dict__, "enable_validation_task_adapter": True}
        )
    if args.enable_claude_code_adapter:
        # Same one-directional rule as above, for the same reason.
        config = type(config)(
            **{**config.__dict__, "enable_claude_code_adapter": True}
        )
    if args.enable_claude_agent_sdk_adapter:
        # Same one-directional rule again, and note what it does *not* do: it
        # never clears `enable_claude_code_adapter`. Two Claude transports can
        # be registered at once, and choosing the newer one is a per-project
        # decision in the host's registry, not a side effect of a flag.
        config = type(config)(
            **{**config.__dict__, "enable_claude_agent_sdk_adapter": True}
        )
    config.ensure_dirs()

    token_existed = config.token_path.is_file()
    token = load_or_create_token(config)

    if args.print_token:
        print(token)
        return 0

    if not token_existed:
        print(f"[cofferdam] device token generated at {config.token_path}", file=sys.stderr)
        print(f"[cofferdam] token: {token}", file=sys.stderr)
        print("[cofferdam] enter this token once in the phone UI; it is not shown again.", file=sys.stderr)

    if config.enable_validation_task_adapter:
        # Said out loud on every start, not only when the flag was passed on the
        # command line. A validation surface that is on because of a config file
        # written months ago is exactly the one worth announcing.
        print(
            "[cofferdam] the validation task adapter is enabled. It runs no program "
            "and calls no model; it exists to exercise the task lifecycle. Do not "
            "leave it enabled on a normal runtime.",
            file=sys.stderr,
        )

    if config.enable_claude_code_adapter:
        # Announced on every start, for the same reason and with more at stake:
        # this one starts a real program that can change files.
        print(
            "[cofferdam] the Claude Code task adapter is enabled. Tasks can run "
            "Claude Code inside projects listed in task-projects.json, where it "
            "can read and edit files. It has no shell.",
            file=sys.stderr,
        )

    if config.enable_claude_agent_sdk_adapter:
        # Announced on every start, like the two above. This one also says what
        # it did *not* do, because "I enabled the new adapter" and "the old one
        # is gone" are easy to conflate and only one of them is true.
        print(
            "[cofferdam] the Claude Agent SDK task adapter is enabled. It runs "
            "Claude through the official Agent SDK in projects listed in "
            "task-projects.json, with no shell. The Claude Code adapter is "
            "unaffected by this flag.",
            file=sys.stderr,
        )

    if not _is_loopback(config.bind_host):
        print(
            f"[cofferdam] binding to {config.bind_host} — ensure this is a private "
            "(e.g. Tailscale) interface, not a public one.",
            file=sys.stderr,
        )
        # Started at boot through lingering, this can easily be running before
        # tailscaled has an address. Wait, bounded, rather than failing into a
        # restart loop.
        if not wait_for_bind_address(config.bind_host, _bind_wait_seconds()):
            print(
                f"[cofferdam] {config.bind_host} never became available. Not starting: "
                "the service binds only to its configured private address and will not "
                "fall back to a public one. Check `tailscale status` and "
                "COFFERDAM_BIND_HOST, then start the service again.",
                file=sys.stderr,
            )
            return 4

    try:
        import uvicorn

        from .service import create_app
    except ImportError:  # pragma: no cover
        print(
            "[cofferdam] the workstation extras are not installed: "
            "pip install -e '.[workstation]'",
            file=sys.stderr,
        )
        return 2

    app = create_app(config=config, token=token)
    uvicorn.run(app, host=config.bind_host, port=config.bind_port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
