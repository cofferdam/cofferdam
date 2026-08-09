"""The complete environment an Agent SDK session is allowed to see.

This module exists because of one verified fact about the Claude Agent SDK, read
from the published 0.2.134 source and not from memory:

    ``subprocess_cli.py`` builds its child's environment as
    ``{**os.environ, "CLAUDE_CODE_ENTRYPOINT": "sdk-py", **options.env,
    "CLAUDE_AGENT_SDK_VERSION": …}``.

``ClaudeAgentOptions.env`` is therefore an **override map layered over whatever
the calling process happens to have**, never a replacement for it. There is no
option, no keyword and no seam anywhere in the supported API that hands the SDK a
complete child environment. M2I PR1 recorded that honestly and left it as a
limitation; this module is how it stops being one.

Why the helper process, and not a custom ``Transport``
------------------------------------------------------

The obvious fix is a custom transport: ``Transport`` *is* a public export and
``ClaudeSDKClient`` *does* accept one. It was rejected on evidence, and the
evidence is worth writing down because "we could have used the official
extension point" is the first question a reader will have.

**The vendor documents the ABC as removable.** Its own docstring says the class
"may change or remove" in any future release, and that custom implementations
must be updated to match. That is a poor foundation for a security boundary.

**A custom transport silently breaks the permission callback.** When
``can_use_tool`` is installed the SDK sets ``permission_prompt_tool_name="stdio"``
on a *copy* of the options and gives that copy to its own transport — and the
client's source says plainly that "the materialized options never reach a
pre-constructed transport". A custom transport must therefore reproduce that
undocumented internal detail in its own argv or the callback never fires. That
callback is where a tool is denied and where a question would be intercepted, so
the failure mode is the safety boundary going quiet without an error.

**And it would move the whole profile into copied code.** With a custom transport
the SDK applies none of ``tools``, ``permission_mode``, ``setting_sources`` or
``strict_mcp_config`` — every one of them would have to be re-derived from a
~230-line internal command builder that this project does not control.

So Cofferdam owns the *spawn* instead of the *transport*. The adapter starts a
helper process with :func:`build_child_environment` — by selection, through
Cofferdam's own ``Popen``, exactly as the Claude Code adapter has always done —
and the SDK runs inside it. Because the helper's ``os.environ`` **is** this
allowlist, the SDK's merge produces precisely this allowlist plus its own two
forced names. Every SDK API stays supported and unmodified, and the environment
guarantee rests on a mechanism Cofferdam controls and tests.

What this module will not do
----------------------------

It does not copy values it was not asked for. It has no denylist — a denylist
ships every variable somebody adds to the unit file next year, and the whole
point here is that a new name in the daemon's environment reaches Claude only
when somebody adds it *to this file* on purpose.

And it never prints, logs or returns a value. :func:`environment_key_names`
exists so a diagnostic and a test can talk about which names are present without
either of them touching what is in them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple

#: Names copied from the daemon's environment when they are present, and the
#: reason each one is here. This is the *complete* list; nothing else is copied.
#:
#: ``HOME`` is the load-bearing entry, for the same reason it is in the Claude
#: Code adapter's allowlist: the installed CLI authenticates with a ``claude.ai``
#: subscription login whose credentials live under the user's home directory.
#: Cofferdam does not read them, does not know their format and does not name
#: their path — it passes ``HOME`` and lets the CLI do its own thing. Cofferdam
#: grants *reachability*, never *possession*.
#:
#: The ``XDG_*`` entries are here for the same reason: the authenticated host CLI
#: resolves its own configuration and cache through them, and a session that
#: could not find its config would be a session that could not authenticate.
#:
#: Deliberately identical to ``claude_code.cli.ENVIRONMENT_ALLOWLIST``. Two
#: transports for one policy — a name that one transport passed and the other did
#: not would mean switching transport quietly changed what the agent can reach.
CHILD_ENVIRONMENT_ALLOWLIST: Tuple[str, ...] = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
)

#: Forced into the child regardless of what the daemon inherited, and applied
#: last so they win. Four entries and no secrets: a value here ends up in a
#: process environment, and this is the tuple a reviewer reads to confirm nothing
#: sensitive was put in one.
CHILD_ENVIRONMENT_FORCED: Dict[str, str] = {
    # UTF-8 is not negotiable: a Turkish prompt that survives the API and the
    # database only to be mangled by a child locale is a data-loss bug wearing an
    # encoding costume.
    "PYTHONIOENCODING": "utf-8",
    # Colour and cursor control in a stream something parses is noise at best.
    "NO_COLOR": "1",
    # Identifies the caller in the CLI's user agent. Documented SDK option.
    "CLAUDE_AGENT_SDK_CLIENT_APP": "cofferdam",
    "CLAUDE_CODE_ENTRYPOINT": "cofferdam-agent-sdk",
}

#: The one name the parent adds that is **not** part of the session environment.
#:
#: The helper is Cofferdam's own code and has to be able to import Cofferdam. The
#: honest way to guarantee it imports *the same* Cofferdam that launched it is to
#: derive the path from this package's own location rather than to hope an
#: install layout is on the default path. The helper removes this name from its
#: own environment once it has booted — see :func:`bootstrap_names` and
#: ``host.py`` — so the SDK's child never sees it.
BOOTSTRAP_PYTHONPATH = "PYTHONPATH"


def bootstrap_names() -> Tuple[str, ...]:
    """Names that exist only to start the helper and are dropped once it has.

    A function rather than a constant because it is asked twice — by the parent,
    which adds them, and by the helper, which removes them — and a single source
    for that pair is what keeps the two halves from drifting apart.
    """
    return (BOOTSTRAP_PYTHONPATH,)


#: Names asserted **never** to reach the child, listed for the test that proves
#: it. This is documentation and a tripwire, not the mechanism: the mechanism is
#: that :func:`build_child_environment` copies by selection, so a name absent
#: from :data:`CHILD_ENVIRONMENT_ALLOWLIST` cannot appear whether or not it is
#: named here.
#:
#: They are written down anyway because a reader's real question is "is my token
#: in there", and an answer that requires reasoning about the absence of an entry
#: is a worse answer than a list saying no.
EXCLUDED_ENVIRONMENT_NAMES: Tuple[str, ...] = (
    # Cofferdam's own device token: the one credential that would let anything
    # holding it drive this workstation.
    "COFFERDAM_TOKEN",
    "COFFERDAM_HOME",
    "COFFERDAM_STATE_DIR",
    "COFFERDAM_BIND_HOST",
    # Unrelated model providers. An agent session has no business holding a key
    # for a provider it is not talking to.
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    # Infrastructure credentials that happen to live on this host.
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_TUNNEL_TOKEN",
    "TS_AUTHKEY",
    "TAILSCALE_AUTHKEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "DATABASE_URL",
    "PGPASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    # Media provider credentials from earlier milestones.
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "YOUTUBE_API_KEY",
)


class EnvironmentPolicyError(ValueError):
    """A child environment that violates this file's own rules.

    Raised by :func:`verify_child_environment`, which runs on every build rather
    than only in tests. A guard that exists only in a test suite protects the
    test suite; this one protects the launch — a violating environment never
    reaches a process, because the process is never started.

    Its message names the offending **key** and never its value. An exception
    string ends up in a log, and a guard whose failure mode is printing the
    secret it was guarding would be worse than no guard.
    """


def package_import_root() -> Path:
    """The directory the ``cofferdam`` package is importable from.

    Derived from this module's own ``__file__`` — five parents up from
    ``cofferdam/workstation/tasks/adapters/claude_agent_sdk/hostenv.py`` — so the
    helper is guaranteed to import the same Cofferdam that launched it, whatever
    the install layout is. Nothing here reads a path from configuration, a
    request, or the environment.
    """
    return Path(__file__).resolve().parents[5]


def build_child_environment(
    inherited: Optional[Mapping[str, str]] = None,
    *,
    include_bootstrap: bool = True,
) -> Dict[str, str]:
    """The helper process's **complete** environment. Not an override map.

    Built by selection and then by forcing, in that order, so a forced name wins
    over an inherited one of the same name. The result is passed straight to
    ``Popen(env=…)``, which replaces the child's environment rather than merging
    with it — which is the whole difference between this and what the SDK's own
    ``options.env`` can offer.

    ``inherited`` is accepted so a test can pass a mapping instead of touching the
    process environment. It is never stored and never returned unchanged.

    ``include_bootstrap=False`` gives the same environment minus the names that
    exist only to start the helper — which is what the *session* environment
    should look like, and what the helper asserts about itself once it is running.
    """
    source = dict(os.environ if inherited is None else inherited)
    child: Dict[str, str] = {}
    for name in CHILD_ENVIRONMENT_ALLOWLIST:
        value = source.get(name)
        if isinstance(value, str):
            child[name] = value
    child.update(CHILD_ENVIRONMENT_FORCED)
    if include_bootstrap:
        child[BOOTSTRAP_PYTHONPATH] = str(package_import_root())
    verify_child_environment(child)
    return child


def permitted_names(*, include_bootstrap: bool = True) -> frozenset:
    """Every name that may legitimately appear in a child environment."""
    names = set(CHILD_ENVIRONMENT_ALLOWLIST) | set(CHILD_ENVIRONMENT_FORCED)
    if include_bootstrap:
        names |= set(bootstrap_names())
    return frozenset(names)


def verify_child_environment(
    environment: Mapping[str, str], *, include_bootstrap: bool = True
) -> None:
    """Check a built environment against this file's rules, before it is used.

    Three checks. Every key must be a name this file names; every key and value
    must be a string, because ``Popen`` will not accept anything else and a
    ``TypeError`` from deep inside the standard library is a worse diagnosis than
    a sentence; and no excluded name may appear even though selection already
    makes that impossible — belt and braces on the one class of mistake that
    would matter.
    """
    if not isinstance(environment, Mapping):
        raise EnvironmentPolicyError("the child environment must be a mapping")
    allowed = permitted_names(include_bootstrap=include_bootstrap)
    for name, value in environment.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise EnvironmentPolicyError("environment names and values must be text")
        if name not in allowed:
            # The name, never the value.
            raise EnvironmentPolicyError("unexpected environment name: " + name)
    forbidden = sorted(set(environment) & set(EXCLUDED_ENVIRONMENT_NAMES))
    if forbidden:  # pragma: no cover - unreachable while selection is by allowlist
        raise EnvironmentPolicyError("forbidden environment name: " + forbidden[0])


def environment_key_names(environment: Mapping[str, str]) -> Tuple[str, ...]:
    """The sorted **names** in an environment, and nothing else.

    The only function in this module a diagnostic or a test may use to describe
    an environment. There is deliberately no counterpart that returns values: a
    helper that could dump an environment is a helper somebody will one day call
    from an error path.
    """
    if not isinstance(environment, Mapping):
        return ()
    return tuple(sorted(name for name in environment if isinstance(name, str)))


def drop_bootstrap_names(environment: Optional[Iterable[str]] = None) -> None:
    """Remove the bootstrap-only names from **this** process's environment.

    Called once by the helper, immediately after it has imported Cofferdam and
    before it constructs anything from the SDK. After it returns, this process's
    ``os.environ`` is exactly the session environment — so the SDK's
    ``{**os.environ, …}`` merge produces exactly the session environment too.

    Safe here in a way it would never be in the daemon: the helper is a
    single-purpose process that Cofferdam started, owns entirely, and has not yet
    given any work to. Mutating the *daemon's* environment to achieve the same
    thing would be a data race against every other thread in it, which is
    precisely why the helper exists.
    """
    for name in bootstrap_names() if environment is None else environment:
        os.environ.pop(name, None)


__all__ = [
    "BOOTSTRAP_PYTHONPATH",
    "CHILD_ENVIRONMENT_ALLOWLIST",
    "CHILD_ENVIRONMENT_FORCED",
    "EXCLUDED_ENVIRONMENT_NAMES",
    "EnvironmentPolicyError",
    "bootstrap_names",
    "build_child_environment",
    "drop_bootstrap_names",
    "environment_key_names",
    "package_import_root",
    "permitted_names",
    "verify_child_environment",
]
