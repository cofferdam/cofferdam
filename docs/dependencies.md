# Dependency and license inventory

Audited **2026-08-01** against the M1 branch (`feat/m1-remote-control-skeleton`) and a real
installed environment, not just the manifest. The governing rules are in
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) → *Dependency policy*; the audit conclusions are
recorded in [`../DECISIONS.md`](../DECISIONS.md) D-2026-08-01-8.

Regenerate the machine-readable form at any time:

```sh
python -m pip install -e ".[workstation,dev]"
python .github/scripts/license_report.py --json dependency-inventory.json
```

That script is a **tripwire, not an authority** — it reads installed package metadata and fails
CI on copyleft or unrecognised licenses. Anything it flags must be verified against the upstream
project itself.

## Distribution model — why this matters less than it looks

Cofferdam **vendors no third-party source code** and **ships no bundled dependencies**. It is
installed from source with `pip install -e ".[workstation]"`, so every dependency is fetched by
pip directly from PyPI, carrying its own license and notices. Cofferdam therefore does not
redistribute any of these packages, and no `THIRD_PARTY_NOTICES.md` is required.

**This changes the day Cofferdam ships a wheel, container, or installer that bundles
dependencies** — at that point notices must be collected and carried. See the trigger recorded in
`CONTRIBUTING.md`.

## Direct Python dependencies

Declared in `pyproject.toml`. "Source of license" is where the license text was actually read
from during the audit.

| Package | Version (declared / installed) | Purpose | License | Source of license | Distributed by us? | Notice required? |
|---|---|---|---|---|---|---|
| `fastapi` | `>=0.110` / 0.141.1 | HTTP + WebSocket framework; its typed request validation is the mechanism that makes "no arbitrary commands" structural | MIT | installed metadata (`License-Expression`), matches the project's `LICENSE` on GitHub | No | No |
| `uvicorn[standard]` | `>=0.27` / 0.52.0 | ASGI server that runs the app | BSD-3-Clause | installed metadata | No | No |
| `psutil` | `>=5.9` / 7.2.2 | cross-platform host metrics (CPU, memory, boot time) without per-platform `/proc` parsing | BSD-3-Clause | installed metadata | No | No |
| `httpx` | `>=0.27` / 0.28.1 | **test-only** (`dev` extra); required by Starlette's `TestClient` | BSD-3-Clause | installed metadata | No | No |

The `[standard]` extra of uvicorn pulls `httptools`, `watchfiles`, `websockets`,
`python-dotenv`, `pyyaml`, and `colorama` — all MIT or BSD.

## Transitive dependencies (installed set)

All 32 installed distributions were checked. Every one is permissive (MIT, BSD-2/3-Clause,
Apache-2.0, PSF-2.0) with a single exception:

| Package | Version | License | Assessment |
|---|---|---|---|
| `certifi` | 2026.7.22 | **MPL-2.0** (weak, file-level copyleft) | **Acceptable, and worth knowing about.** It enters only through `httpx`, which is in the **test-only `dev` extra** — it is *not* a runtime dependency of the workstation service and is never redistributed by Cofferdam. MPL-2.0 obligations attach to modified MPL files; we neither modify nor bundle it. Would need a notice only if Cofferdam ever bundles dependencies. |

Notable transitives: `starlette` (BSD-3-Clause, the ASGI toolkit under FastAPI), `pydantic` +
`pydantic-core` (MIT, schema validation), `anyio`/`sniffio` (MIT), `click` + `h11` (BSD/MIT),
`typing_extensions` (PSF-2.0), `idna`/`httpcore` (BSD-3-Clause).

> `pytest`, `pip`, and `setuptools` may appear in a developer environment but are **not**
> declared project dependencies. The test suite uses the standard library's `unittest`.

## Frontend

**None.** The PWA (`web/`) is hand-written HTML, CSS, and ES5-compatible JavaScript with **no
framework, no build step, no bundler, and no external requests** — no CDN scripts, web fonts, or
analytics. Verified: zero external URLs in `web/`. There is consequently no frontend license
surface at all.

## System tools invoked by the Linux adapter

These are **not dependencies of Cofferdam** — they are programs the user installs on their own
host, which the adapter locates on `PATH` and invokes with a fixed argv. Cofferdam neither
distributes nor links against them, so their licenses do not affect Cofferdam's license. They are
listed because the host-setup runbook tells the user to install them.

| Tool | Used for | Typical license | Required? |
|---|---|---|---|
| `gnome-screenshot` / `maim` / `scrot` / `spectacle` / ImageMagick `import` | screen capture (first one found wins) | GPL-family | At least one, for screenshots |
| `xdg-open` (xdg-utils) | open a URL in the user's default browser | MIT | Yes, for `open_url` |
| `xrandr` (x11-xserver-utils) | display enumeration | MIT | Optional (display count) |
| `firefox` / `chromium` | the browsers being launched | MPL-2.0 / BSD-3-Clause | At least one |
| `pactl` (PulseAudio/PipeWire) | volume control (M3) | LGPL-2.1+ | Later milestone |
| `wmctrl` / `xdotool` | window placement (M2) | GPL-2.0 | Later milestone |

**Invoking a GPL program as a separate process does not make Cofferdam GPL.** Cofferdam runs
these as ordinary subprocesses with fixed arguments; it does not link them, embed them, or
derive from their source. This is the same relationship any script has with the commands it runs.
The rule that keeps it that way: *never vendor or copy source from these tools.*

## Optional external components (documented, not yet integrated)

| Component | Role | Status | License / terms | Classification |
|---|---|---|---|---|
| **Claude Code** | first development worker; drives candidate-slot implementation (M4/M6) | Not yet integrated | Commercial service under Anthropic's terms; invoked as an installed CLI, not linked | Replaceable adapter; practically central for now |
| **Tailscale** | private network boundary the service binds to | Documented in host setup; used operationally | BSD-3-Clause client; service terms apply to the coordination service | Operational dependency, entirely outside Cofferdam's code path |
| **Ollama** | optional local model for intent classification (M7) | Not integrated | MIT (models carry their own separate licenses — check each) | Optional; the product degrades to buttons + delegation without it |
| **OpenClaw** | optional acceleration for agent sessions / streaming | **Not integrated. No OpenClaw code is present in this repository.** | To be established **before** any adoption | Optional; must stay behind an adapter and out of the Guardian/activation path |
| **Playwright** | browser and media automation (M3) | Not yet added | Apache-2.0 (the Python library) | Replaceable adapter |
| Browsers downloaded by Playwright | rendering engines | n/a | **Separate from Playwright's own license** — Chromium is BSD-3-Clause plus components; a branded Chrome/Edge build carries proprietary terms and is what Widevine DRM requires | User-installed; never redistributed by Cofferdam |
| Remote-desktop fallback (e.g. Sunshine, RustDesk) | raw screen access escape hatch | Not integrated | Varies (often GPL-family) | Optional, installed beside Cofferdam, never integrated into its code |

## Service terms are not source licenses

Automating YouTube or Netflix through a logged-in browser profile raises **operational and
service-compatibility** questions — each service's terms of use govern what a user may do with
their own account, and DRM playback requires a browser build with Widevine. Those are questions
for the user and for the adapter's operational documentation. **They have no bearing on the
license of Cofferdam's source code**, which is Apache-2.0 regardless.

Related rule from [`../DESIGN.md`](../DESIGN.md): media services are accessed through a dedicated
persistent browser profile the user logs into manually, once. Account passwords never enter the
repository, any config file, or a model prompt.

## Compatibility conclusion

No dependency conflicts with Apache-2.0, and none imposes an obligation Cofferdam does not meet.
The only items worth re-checking on future changes are: (1) `certifi`'s MPL-2.0 status if
dependencies ever get bundled, (2) any new dependency's license at the time it is added, and
(3) licenses at major version bumps — projects do relicense.
