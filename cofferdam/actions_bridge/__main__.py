"""``python -m cofferdam.actions_bridge`` — start the bridge, or make its key.

Two things this entry point does that the daemon's does not.

**It refuses to bind off-loopback without being told twice.** ``--host`` alone is
not enough; ``--allow-public-bind`` must be passed as well. The reason is the
milestone: PR1's whole safety argument is that this process is not reachable
from anywhere, and a single mistyped flag should not be able to end that.

**It prints a configuration summary with no values in it.** Paths, ports and
numbers. Neither credential is read by the summary code, and neither is printed
by anything here — not on success, not on failure, and not with ``--check``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional, Sequence

from .config import (
    BridgeConfigError,
    generate_external_key,
    load_bridge_config,
    read_secret_file,
)
from .observe import LOGGER_NAME


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cofferdam-actions-bridge",
        description=(
            "The private Custom GPT Actions bridge. A narrow process in front of "
            "Cofferdam's task API. Binds to loopback by default."
        ),
    )
    parser.add_argument("--host", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="bind port (default: 7108)")
    parser.add_argument(
        "--internal-base-url",
        help="the Cofferdam daemon origin (default: http://127.0.0.1:7101)",
    )
    parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        help=(
            "required in addition to --host before the bridge will bind to "
            "anything other than loopback. Exposing this process is an approval "
            "gate in its own right; see docs/ACTIONS_BRIDGE.md."
        ),
    )
    parser.add_argument(
        "--generate-key",
        action="store_true",
        help=(
            "write a new external Actions key to secrets/actions-bridge-key "
            "(0600) and exit. The value is never printed; read the file to copy "
            "it into the GPT editor."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --generate-key, replace an existing key.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "validate configuration and both credential files, print the safe "
            "summary, and exit without binding anything."
        ),
    )
    args = parser.parse_args(argv)

    try:
        config = load_bridge_config(
            bind_host=args.host,
            bind_port=args.port,
            internal_base_url=args.internal_base_url,
        )
    except BridgeConfigError as failure:
        print(f"[cofferdam-bridge] {failure}", file=sys.stderr)
        return 2

    if args.generate_key:
        try:
            path = generate_external_key(config, force=args.force)
        except BridgeConfigError as failure:
            print(f"[cofferdam-bridge] {failure}", file=sys.stderr)
            return 2
        print(f"[cofferdam-bridge] external Actions key written to {path}")
        print("[cofferdam-bridge] the value is not printed. Read the file to copy it.")
        return 0

    if not _is_loopback(config.bind_host) and not args.allow_public_bind:
        print(
            f"[cofferdam-bridge] refusing to bind to {config.bind_host}: pass "
            "--allow-public-bind as well. Binding this process off loopback is a "
            "separate decision from running it.",
            file=sys.stderr,
        )
        return 2

    # Both credentials are read here, before anything binds, so a missing or
    # world-readable file is a startup failure rather than a 500 on the first
    # real request. The values are discarded immediately; `create_bridge_app`
    # reads them again into its own closure.
    try:
        read_secret_file(config.external_key_path, what="Actions bridge external key")
        read_secret_file(
            config.internal_token_path, what="Cofferdam internal bridge token"
        )
    except BridgeConfigError as failure:
        print(f"[cofferdam-bridge] {failure}", file=sys.stderr)
        return 2

    print(
        "[cofferdam-bridge] configuration: "
        + json.dumps(config.summary(), indent=2, sort_keys=True),
        file=sys.stderr,
    )

    if args.check:
        print("[cofferdam-bridge] configuration and credentials are usable.")
        return 0

    if not config.loopback_only:
        print(
            f"[cofferdam-bridge] binding to {config.bind_host} — this process is "
            "now reachable from outside this machine. Ensure the only thing in "
            "front of it is the dedicated bridge origin, and that the Cofferdam "
            "daemon and PWA are NOT exposed.",
            file=sys.stderr,
        )

    try:
        import uvicorn
    except ImportError:  # pragma: no cover - the extras are absent
        print(
            "[cofferdam-bridge] the 'workstation' extra is required: "
            "pip install -e '.[workstation]'",
            file=sys.stderr,
        )
        return 2

    from .service import create_bridge_app

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger(LOGGER_NAME).setLevel(logging.INFO)

    app = create_bridge_app(config)
    uvicorn.run(
        app,
        host=config.bind_host,
        port=config.bind_port,
        # The bridge writes its own bounded line per request. uvicorn's access
        # log would add a second one carrying the full path — which includes a
        # task id, the one identifier `observe.py` deliberately keeps out.
        access_log=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
