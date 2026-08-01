"""Entry point: ``python -m cofferdam.workstation``.

Binds to ``COFFERDAM_BIND_HOST`` (default ``127.0.0.1``). For phone access the
host-setup runbook binds to the machine's **Tailscale** address — never to
``0.0.0.0`` on a public interface. If a non-loopback bind is requested, the
service says so explicitly on stderr so an accidental public bind is visible.

The device token is printed **once, to stderr, only when it was just
generated**, so the maintainer can copy it into the phone. It is never printed
on subsequent starts and never written to logs.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from typing import Optional, Sequence

from .config import load_config, load_or_create_token
from .service import create_app


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cofferdam-workstation", description="Cofferdam workstation service (M1)")
    parser.add_argument("--host", help="bind address (default: COFFERDAM_BIND_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="bind port (default: COFFERDAM_BIND_PORT or 7101)")
    parser.add_argument("--print-token", action="store_true", help="print the device token to stderr and exit")
    args = parser.parse_args(argv)

    config = load_config()
    if args.host:
        config = type(config)(**{**config.__dict__, "bind_host": args.host})
    if args.port:
        config = type(config)(**{**config.__dict__, "bind_port": args.port})
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

    if not _is_loopback(config.bind_host):
        print(
            f"[cofferdam] binding to {config.bind_host} — ensure this is a private "
            "(e.g. Tailscale) interface, not a public one.",
            file=sys.stderr,
        )

    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        print("[cofferdam] uvicorn is not installed: pip install -e '.[workstation]'", file=sys.stderr)
        return 2

    app = create_app(config=config, token=token)
    uvicorn.run(app, host=config.bind_host, port=config.bind_port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
