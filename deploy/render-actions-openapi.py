#!/usr/bin/env python3
"""Render the production GPT Actions schema from the committed placeholder one.

``docs/custom-gpt/openapi.yaml`` is the authority and stays a placeholder: its
``servers[0].url`` is ``https://REPLACE-ME.example.invalid`` and a contract test
asserts it. That is not an oversight to be fixed at Gate A — a repository that
committed the real origin would publish the one fact an attacker cannot derive
from the code, and would do it in a file whose whole purpose is being copied
into somebody else's product.

So the production document is *rendered*, deterministically, onto the host that
owns the origin, and is never committed:

    python3 deploy/render-actions-openapi.py --hostname actions.example.com

Substitution, not generation
----------------------------
This script replaces exactly two scalars — the server URL and its description —
and copies every other byte of meaning through unchanged. It does not reorder,
reformat, prune or "improve" the schema, because the value of the committed file
is that it was reviewed, and a renderer that rewrites it hands that review to a
program nobody read.

It verifies the result rather than trusting itself: the rendered document must
parse, must still declare every operationId the code implements, must carry the
same consequential markings, and must contain no second server, no loopback URL,
no tailnet address and no filesystem path. Any failure is a non-zero exit and no
output file.

The output is a document to paste into the GPT editor. It contains no credential
— the key is entered separately, in the editor's authentication panel, and is
never part of a schema.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "docs" / "custom-gpt" / "openapi.yaml"

#: The placeholder line the committed schema ships with, matched whole. A loose
#: match on "REPLACE-ME" would also hit prose elsewhere in the file; this one
#: cannot, because it is anchored to the ``url:`` key at its known indent.
PLACEHOLDER_URL = "https://REPLACE-ME.example.invalid"

#: Hostname grammar, deliberately stricter than DNS allows. Labels are
#: alphanumeric with internal hyphens, at least two of them, and the whole thing
#: is lowercase — an uppercase or trailing-dot hostname would render a schema
#: that works and a set of verification commands that do not.
_HOSTNAME = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

#: Shapes that must never reach a document destined for a model provider.
_FORBIDDEN = (
    (r"/home/[A-Za-z0-9._-]+", "an absolute home path"),
    (r"/Users/[A-Za-z0-9._-]+", "an absolute macOS home path"),
    (r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b", "a tailnet address"),
    (r"\b127\.0\.0\.1\b", "a loopback address"),
    (r"\blocalhost\b", "localhost"),
    (r"[A-Za-z0-9-]+\.ts\.net", "a tailnet hostname"),
    (r"REPLACE-ME", "the placeholder"),
    (r"Bearer\s+[A-Za-z0-9_\-]{20,}", "a token-shaped string"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "an email address"),
)


class RenderError(RuntimeError):
    """Rendering cannot proceed, or the result failed its own checks."""


def validate_hostname(hostname: str) -> str:
    """A public DNS hostname, or a precise refusal.

    An IP address is refused even though it would resolve: GPT Actions require a
    valid public certificate, and a certificate cannot be issued to a private
    address — so an IP here produces a schema that fails at the first call, in
    the GPT editor, with an error that says nothing about the cause.
    """
    candidate = hostname.strip().rstrip(".")
    if candidate != hostname.strip():
        raise RenderError(f"the hostname must not end in a dot: {hostname!r}")
    if candidate != candidate.lower():
        raise RenderError(f"the hostname must be lowercase: {hostname!r}")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise RenderError(
            f"{candidate} is an IP address. GPT Actions require TLS on port 443 "
            "with a valid public certificate, which an address cannot carry."
        )
    if not _HOSTNAME.match(candidate):
        raise RenderError(f"{candidate!r} is not a public DNS hostname")
    if candidate.endswith((".invalid", ".local", ".internal", ".test", ".localhost")):
        raise RenderError(
            f"{candidate} is in a reserved namespace that no public certificate "
            "authority will issue for"
        )
    return candidate


def render(source_text: str, hostname: str) -> str:
    """Replace the two server scalars and nothing else."""
    url_line = f"  - url: {PLACEHOLDER_URL}"
    if url_line not in source_text:
        raise RenderError(
            f"the source schema no longer contains the expected placeholder line "
            f"({url_line!r}). Refusing to guess where the server URL is."
        )
    rendered = source_text.replace(url_line, f"  - url: https://{hostname}", 1)

    # The description below it is three placeholder lines in a folded block. They
    # are replaced as a unit so the rendered file does not claim "no public
    # origin exists yet" while naming one.
    old_description = (
        "    description: >-\n"
        "      PLACEHOLDER. No public origin exists yet. Replace with the dedicated\n"
        "      Cofferdam bridge hostname once external exposure is approved.\n"
    )
    if old_description not in rendered:
        raise RenderError(
            "the source schema's server description is not the expected "
            "placeholder block. Refusing to render a document whose provenance "
            "cannot be checked."
        )
    new_description = (
        "    description: >-\n"
        "      The dedicated Cofferdam Actions bridge origin. It serves the eight\n"
        "      operations below and nothing else: no PWA, no Cofferdam API, no\n"
        "      filesystem and no generic path. Rendered by\n"
        "      deploy/render-actions-openapi.py; do not edit by hand.\n"
    )
    return rendered.replace(old_description, new_description, 1)


def check(rendered: str, hostname: str, source_text: str) -> None:
    """Verify the rendered document rather than trusting the substitution."""
    for pattern, what in _FORBIDDEN:
        found = re.search(pattern, rendered)
        if found is not None:
            raise RenderError(f"the rendered schema contains {what}: {found.group(0)!r}")

    if rendered.count("  - url: ") != 1:
        raise RenderError("the rendered schema must declare exactly one server")
    if f"  - url: https://{hostname}" not in rendered:
        raise RenderError("the rendered server URL is not the requested hostname")

    # Every operationId that survived, and no new one. Compared against the
    # source rather than against a list in this file: a list here would be a
    # second place to update, and the day somebody forgets is the day the
    # renderer silently drops an operation.
    source_ops = re.findall(r"^\s*operationId: (\w+)$", source_text, re.MULTILINE)
    rendered_ops = re.findall(r"^\s*operationId: (\w+)$", rendered, re.MULTILINE)
    if source_ops != rendered_ops:
        raise RenderError(
            f"operationIds changed during rendering: {source_ops} -> {rendered_ops}"
        )

    source_flags = re.findall(r"x-openai-isConsequential: (\w+)", source_text)
    rendered_flags = re.findall(r"x-openai-isConsequential: (\w+)", rendered)
    if source_flags != rendered_flags:
        raise RenderError(
            "the consequential markings changed during rendering. That is a "
            "safety-posture change and this script does not make them."
        )

    # Length, against OpenAI's stated 100,000-character payload cap. The schema
    # is not a payload, but a document near that size is a signal something was
    # duplicated rather than substituted.
    if len(rendered) > 100_000:
        raise RenderError(f"the rendered schema is {len(rendered)} characters")

    try:
        import yaml
    except ImportError:
        print(
            "[render-actions-openapi] PyYAML absent; skipped the parse check. "
            "Install the dev extra to enable it.",
            file=sys.stderr,
        )
        return
    parsed = yaml.safe_load(rendered)
    if not isinstance(parsed, dict):
        raise RenderError("the rendered schema is not a YAML mapping")

    # A parameter given as {"$ref": ...} is valid OpenAPI and unusable in the GPT
    # Actions editor: it does not resolve the reference, reads the parameter as
    # nameless, and skips the entire operation with
    #   "parameter {...} is missing or has a non-string name; operation skipped"
    # Five of nine Actions vanished that way, reported as a note rather than an
    # error. Checked here as well as in the tests because this function produces
    # the document somebody pastes, and it should not be able to produce one that
    # imports as four operations.
    for path, item in (parsed.get("paths") or {}).items():
        entries = list(item.get("parameters") or [])
        for method, operation in item.items():
            if isinstance(operation, dict):
                entries.extend(operation.get("parameters") or [])
        for parameter in entries:
            if isinstance(parameter, dict) and "$ref" in parameter:
                raise RenderError(
                    f"{path} declares a parameter by $ref. The GPT Actions editor "
                    "skips any operation whose parameter it cannot read a name "
                    "from. Inline the parameter into each operation."
                )
    if parsed.get("openapi") != "3.1.0":
        raise RenderError(f"unexpected OpenAPI version {parsed.get('openapi')!r}")
    servers = parsed.get("servers")
    if not isinstance(servers, list) or len(servers) != 1:
        raise RenderError("the rendered schema must declare exactly one server")
    if servers[0].get("url") != f"https://{hostname}":
        raise RenderError("the parsed server URL is not the requested hostname")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="render-actions-openapi",
        description=(
            "Render the production GPT Actions schema from the committed "
            "placeholder. Substitutes the server URL and nothing else."
        ),
    )
    parser.add_argument(
        "--hostname",
        required=True,
        help="the dedicated public Actions hostname, without a scheme or a path",
    )
    parser.add_argument(
        "--output",
        help=(
            "where to write it. Default: "
            "$COFFERDAM_HOME/state/actions-bridge/openapi.production.yaml — "
            "outside the repository, because the real origin is not committed."
        ),
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write to stdout instead of a file (still fully verified).",
    )
    args = parser.parse_args(argv)

    try:
        hostname = validate_hostname(args.hostname)
        source_text = SOURCE.read_text(encoding="utf-8")
        rendered = render(source_text, hostname)
        check(rendered, hostname, source_text)
    except (RenderError, OSError) as failure:
        print(f"[render-actions-openapi] {failure}", file=sys.stderr)
        return 2

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.output:
        destination = Path(args.output).expanduser()
    else:
        import os

        home = Path(os.environ.get("COFFERDAM_HOME") or (Path.home() / "cofferdam"))
        destination = home / "state" / "actions-bridge" / "openapi.production.yaml"

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    print(f"[render-actions-openapi] wrote {destination}")
    print(f"[render-actions-openapi] server URL: https://{hostname}")
    print(
        "[render-actions-openapi] paste this file into the GPT editor's Actions "
        "schema box. The Actions key is entered separately and is not in it."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
