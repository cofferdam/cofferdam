# Contributing to Cofferdam

Cofferdam is an open-source, personal AI workstation for Ubuntu Desktop,
developed in the open. It is **early-stage**: the first milestone is still being
validated on real hardware, so expect rough edges and changing internals.

Read [`AGENTS.md`](AGENTS.md) before changing anything — it holds the binding
safety rules, and they apply to humans and coding agents alike.

## Development setup

```bash
git clone https://github.com/cofferdam/cofferdam.git
cd cofferdam
python3 -m venv .venv
./.venv/bin/pip install -e ".[workstation,dev]"
```

The base install has **no runtime dependencies** — the Trust Core module is
standard-library only. The `workstation` extra adds the service dependencies;
`dev` adds test-only ones.

Run the service locally (loopback by default):

```bash
COFFERDAM_HOME=~/cofferdam ./.venv/bin/python -m cofferdam.workstation
```

Full host setup, including Ubuntu specifics, is in
[`docs/host-setup.md`](docs/host-setup.md).

## Running tests

```bash
python -m unittest discover -s tests -t .
```

The workstation tests skip cleanly if the `workstation`/`dev` extras are not
installed, so the Trust Core suite still runs on a bare interpreter. **If you
changed workstation code, make sure the tests actually ran rather than skipped**
— a skipped suite is not a passing suite.

## Branches and worktrees

- `main` — integration branch. Milestone work merges here after validation.
- `feat/<milestone-or-topic>` — implementation branches.
- `pr3c2-candidate-b-execution` — preserved Trust Core work-in-progress. **Do
  not rebase, rewrite, merge, or continue it** without a task that explicitly
  scopes it.

Git worktrees are used heavily so several branches can be checked out at once
(and, later, so a candidate runtime slot can be built while the active one keeps
serving):

```bash
git worktree add ../worktrees/my-topic -b feat/my-topic main
```

## Safety rules that are not negotiable

These exist because Cofferdam can modify itself and control a live desktop.

1. **Never modify the active runtime slot directly.** Work in the inactive
   candidate slot/worktree.
2. **Never change Guardian** (`guardian/`, once it exists) without a task that
   names it explicitly. Guardian has a separate, stricter update path.
3. **Never bypass activation or rollback.** Do not flip the active slot, edit
   Guardian state, or declare a candidate healthy by hand.
4. **Preserve update records.** Keep the original user prompt and acceptance
   criteria verbatim; never rewrite them to match what was built.
5. **No secrets in git, logs, or model prompts.** The device token, browser
   profiles, and `COFFERDAM_HOME` contents stay out of the repository.
6. **No arbitrary command execution.** See the next section.

## Proposing a new action or adapter

Every capability reaches the host through a **typed action** plus an **adapter**
— never a command string. To add one:

1. Add a parameter schema in `cofferdam/workstation/actions.py`. It must set
   `extra="forbid"` and must not expose a `command`, `args`, `argv`, `shell`,
   `executable`, `path`, or `script` field. Values that name a program come from
   a closed allowlist, matched exactly.
2. Add the capability to the `HostAdapter` interface in
   `cofferdam/workstation/adapters/base.py`, then implement it per platform.
   Platform-specific code lives **only** under `adapters/`.
3. Build the argv inside the adapter from constants. Use the shared `run_fixed`
   / `spawn_fixed` helpers. `shell=True`, `os.system`, and `os.popen` are
   forbidden and enforced by `tests/test_workstation_no_shell.py`.
4. Raise `AdapterError` / `AdapterUnsupported` on failure — never a bare
   `OSError` — so the API can return a bounded, structured error.
5. Prefer semantic system commands and APIs over synthetic mouse or keyboard
   coordinates.
6. Add tests, including a negative test proving the new schema rejects a
   command-like field.

## Commits and pull requests

- Explain **why**, not just what. Note anything a reviewer could not infer.
- State the platform you tested on and whether the tests ran or skipped.
- Say plainly what you did **not** verify. Unvalidated is fine; unlabelled is not.
- Keep unrelated changes out. Documentation-only changes should say so.
- Do not commit generated artifacts, virtual environments, screenshots, or
  runtime state.

## Reporting platform-specific evidence

Cofferdam depends on real desktop behaviour that differs across sessions and
distributions, so **evidence beats assertion**. When reporting that something
works or fails, include:

- distribution and version (e.g. Ubuntu 24.04),
- session type (`echo $XDG_SESSION_TYPE` — `x11` or `wayland`),
- desktop environment, and display layout (`xrandr --listmonitors`),
- the tool actually used (which screenshot binary was found, which browser),
- the exact command run and the exact output or structured error,
- whether the stub adapter was active (`/api/status` → `adapter`, `stub`) —
  **results from the stub adapter never count as platform validation.**

The Ubuntu checklist in
[`docs/checklists/m1-ubuntu-validation.md`](docs/checklists/m1-ubuntu-validation.md)
is the template. Failures recorded there are the point of the exercise, not a
problem — write them down rather than working around them.

## Review expectations

Ordinary contributions do **not** require multi-model or council review.

| Change | Process |
|---|---|
| UI, adapters, media, actions, docs, tests | Tests + self-review. That is all. |
| Backend behaviour (task state, streaming, reconnect) | Tests + one review if it turns out subtle. |
| Guardian, A/B activation, rollback, authentication, secret handling, privileged actions, data migrations | One focused review, plus a targeted experiment first where the design is uncertain. |

Do not add process gates merely because a question exists.

## Dependency policy

1. **Prefer a normal package dependency over copied source.** Cofferdam vendors
   nothing today, and that is the default to preserve.
2. **Every new direct dependency must have its license identified** in the pull
   request: name, version, license, and where that license was read from.
   Permissive licenses (MIT, BSD, Apache-2.0, PSF, ISC) are fine.
3. **Never introduce GPL, AGPL, SSPL, or source-available dependencies
   silently.** They are not automatically disqualifying for an *optional
   external tool the user installs themselves*, but they must never be a
   distributed dependency, and the choice must be explicit and recorded.
   The CI license scan rejects these markers in tracked files.
4. **Copied or adapted code requires provenance**: record the upstream project,
   version or commit, and license, and preserve the required notices. If that
   cannot be established, do not copy it — rewrite it.
5. **Optional external tools stay behind adapters** (OpenClaw, Ollama, browser
   automation, remote-desktop fallbacks). They must never become a hard
   dependency of the UI, action schemas, task/update records, Guardian, or the
   A/B path, and Cofferdam must remain usable without them.
6. **A service's terms of use are not a source-code license.** How Cofferdam
   automates a website is an operational question for
   [`docs/host-setup.md`](docs/host-setup.md) and the relevant adapter docs, not
   a licensing question for this repository.
7. **Pinning:** declare a lower bound in `pyproject.toml` for libraries with a
   stable API. Pin exactly when a dependency lands in a security-relevant path
   or ships compiled artifacts. There is no lockfile yet — when reproducible
   host installs matter, add one for the `workstation` extra first.
8. **Updates and vulnerabilities:** re-check licenses on major version bumps
   (they do change), and review dependency advisories before a release. Keep the
   dependency surface small enough that this stays a short job.

Third-party notices: Cofferdam currently vendors no third-party code and ships
no bundled dependencies, so it carries no `THIRD_PARTY_NOTICES.md`. **If
Cofferdam ever starts distributing a wheel, container, or installer that bundles
its dependencies, that file becomes required** and must be created as part of
the same change.

## License

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the license this project uses.
