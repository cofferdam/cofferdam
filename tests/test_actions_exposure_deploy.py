"""Structural guards on the M2I.5 Gate A deployment assets.

Gate A is the milestone where Cofferdam gains a public origin for the first
time. Everything that makes that safe is a *shape* — one hostname, one loopback
service, a 404 for everything else, two credentials in two 0600 files, and no
route to the private API — and every one of those shapes lives in a file that a
person edits by hand at three in the morning.

So they are asserted here, against the shipped files rather than against a
running system, and standard-library only so they run on the stdlib-only path
alongside ``test_service_unit_lifecycle.py``.

The rule these tests encode, stated once: **the tunnel can only reach what the
ingress file names.** Not "denies the rest" — cannot reach it. If a future
change adds a second ingress rule, the property that keeps the PWA private stops
being true, and it stops being true silently. That is what a test is for.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = REPO_ROOT / "deploy"
DROPIN_ROOT = DEPLOY_ROOT / "dropins"
DOCS_ROOT = REPO_ROOT / "docs"

BRIDGE_UNIT = DEPLOY_ROOT / "cofferdam-actions-bridge.service"
TUNNEL_UNIT = DEPLOY_ROOT / "cofferdam-actions-tunnel.service"
BRIDGE_ENV = DEPLOY_ROOT / "actions-bridge.env.example"
TUNNEL_CONFIG = DEPLOY_ROOT / "actions-tunnel.yml.example"
CALLER_DROPIN = DROPIN_ROOT / "20-actions-bridge-caller.conf"
SLOT_DROPIN = DROPIN_ROOT / "10-actions-bridge-slot.conf.example"
RENDERER = DEPLOY_ROOT / "render-actions-openapi.py"
VERIFIER = DEPLOY_ROOT / "verify-actions-exposure.sh"
EXPOSURE_DOC = DOCS_ROOT / "ACTIONS_EXPOSURE.md"

GRAPHICAL_TARGET = "graphical-session.target"

#: The eight Actions plus the health check, as the code implements them. A
#: literal list rather than an import, because these tests must keep working on
#: the stdlib-only path where the package's extras are absent — and because a
#: renamed operation should fail a test rather than silently agree with itself.
OPERATION_IDS = (
    "bridgeHealth",
    "listProjects",
    "listRecentTasks",
    "createTask",
    "syncTask",
    "submitChoiceAnswer",
    "sendFollowup",
    "cancelTask",
    "finishTask",
)


def _sections(path: Path) -> Dict[str, List[str]]:
    """Parse a systemd unit into {section: [lines]}, comments dropped."""
    sections: Dict[str, List[str]] = {}
    current = ""
    # Continuation lines end in a backslash; join them so a multi-line
    # ExecStart is asserted as the single directive systemd sees.
    text = re.sub(r"\\\n\s*", " ", path.read_text(encoding="utf-8"))
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _directive(path: Path, section: str, key: str) -> List[str]:
    return [
        line.split("=", 1)[1].strip()
        for line in _sections(path).get(section, [])
        if line.split("=", 1)[0].strip() == key
    ]


class UnitsExistAndParseTests(unittest.TestCase):
    def test_every_gate_a_asset_is_present(self) -> None:
        for path in (
            BRIDGE_UNIT,
            TUNNEL_UNIT,
            BRIDGE_ENV,
            TUNNEL_CONFIG,
            CALLER_DROPIN,
            SLOT_DROPIN,
            RENDERER,
            VERIFIER,
            EXPOSURE_DOC,
        ):
            with self.subTest(asset=path.name):
                self.assertTrue(path.is_file(), f"{path} is missing")

    def test_the_units_declare_the_sections_systemd_needs(self) -> None:
        for unit in (BRIDGE_UNIT, TUNNEL_UNIT):
            with self.subTest(unit=unit.name):
                sections = _sections(unit)
                for required in ("Unit", "Service", "Install"):
                    self.assertIn(required, sections, f"{unit.name} has no [{required}]")

    def test_every_directive_line_is_a_key_value_pair(self) -> None:
        """A stray line without `=` is a unit systemd refuses to load."""
        for unit in (BRIDGE_UNIT, TUNNEL_UNIT, CALLER_DROPIN, SLOT_DROPIN):
            with self.subTest(unit=unit.name):
                for section, lines in _sections(unit).items():
                    for line in lines:
                        self.assertIn("=", line, f"{unit.name} [{section}]: {line!r}")

    def test_the_shipped_units_verify_under_systemd_analyze(self) -> None:
        """The real parser's opinion, when it is available to ask."""
        try:
            probe = subprocess.run(
                ["systemd-analyze", "--version"],
                capture_output=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("systemd-analyze is unavailable")
        if probe.returncode != 0:
            self.skipTest("systemd-analyze is unavailable")
        for unit in (BRIDGE_UNIT, TUNNEL_UNIT):
            with self.subTest(unit=unit.name):
                result = subprocess.run(
                    ["systemd-analyze", "--user", "verify", str(unit)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                combined = result.stdout + result.stderr
                # `verify` reports missing units it merely orders against, which
                # is expected here: the templates are checked outside an install.
                real = [
                    line
                    for line in combined.splitlines()
                    if line.strip()
                    and "not found" not in line
                    and "Unknown key" not in line
                ]
                self.assertEqual(
                    [], real, f"{unit.name} failed verification:\n{combined}"
                )


class BootSafetyTests(unittest.TestCase):
    """The M1.1 login-loop rule, applied to the two new always-on units."""

    ACTIVATING = (
        "Wants",
        "Requires",
        "Requisite",
        "BindsTo",
        "PartOf",
        "Upholds",
        "WantedBy",
        "RequiredBy",
        "UpheldBy",
    )

    def test_no_new_unit_names_the_graphical_target(self) -> None:
        for unit in (BRIDGE_UNIT, TUNNEL_UNIT, CALLER_DROPIN, SLOT_DROPIN):
            text = unit.read_text(encoding="utf-8")
            # Comments may discuss it; directives may not name it.
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=", 1)[0].strip()
                if key in self.ACTIVATING or key in ("After", "Before"):
                    with self.subTest(unit=unit.name, directive=line):
                        self.assertNotIn(GRAPHICAL_TARGET, line)

    def test_both_units_install_under_default_target_only(self) -> None:
        for unit in (BRIDGE_UNIT, TUNNEL_UNIT):
            with self.subTest(unit=unit.name):
                self.assertEqual(["default.target"], _directive(unit, "Install", "WantedBy"))

    def test_the_restart_limiter_is_declared_where_systemd_reads_it(self) -> None:
        """StartLimit* under [Service] is ignored with only a warning."""
        for unit in (BRIDGE_UNIT, TUNNEL_UNIT):
            with self.subTest(unit=unit.name):
                unit_section = _sections(unit)["Unit"]
                service_section = _sections(unit)["Service"]
                self.assertTrue(
                    any(line.startswith("StartLimitIntervalSec=") for line in unit_section)
                )
                self.assertTrue(
                    any(line.startswith("StartLimitBurst=") for line in unit_section)
                )
                for line in service_section:
                    self.assertFalse(
                        line.startswith("StartLimit"),
                        f"{unit.name} puts a StartLimit key under [Service], where "
                        "systemd ignores it",
                    )

    def test_both_units_restart_on_failure(self) -> None:
        for unit in (BRIDGE_UNIT, TUNNEL_UNIT):
            with self.subTest(unit=unit.name):
                self.assertEqual(["on-failure"], _directive(unit, "Service", "Restart"))


class BridgeUnitTests(unittest.TestCase):
    def test_the_bridge_runs_the_bridge_module_from_a_slot(self) -> None:
        exec_starts = _directive(BRIDGE_UNIT, "Service", "ExecStart")
        self.assertEqual(1, len(exec_starts))
        command = exec_starts[0]
        self.assertIn("-m cofferdam.actions_bridge", command)
        self.assertRegex(command, r"%h/cofferdam/slots/[ab]/\.venv/bin/python")

    def test_the_bridge_is_never_started_with_a_public_bind_flag(self) -> None:
        """One flag is not enough to bind off loopback; zero flags is the point.

        `--allow-public-bind` is the second of the two things the bridge
        requires before it will listen on an interface. It has no place in a
        unit: the tunnel connects *outward* to Cloudflare, so the listener never
        needs to leave loopback, and a unit carrying the flag would make a
        one-word `--host` edit sufficient to publish the process.
        """
        text = BRIDGE_UNIT.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("ExecStart"):
                self.assertNotIn("--allow-public-bind", line)
                self.assertNotIn("--host", line)

    def test_the_bridge_unit_does_not_start_a_provider_or_the_daemon(self) -> None:
        text = BRIDGE_UNIT.read_text(encoding="utf-8")
        for forbidden in (
            "cofferdam.workstation",
            "claude_agent_sdk",
            "claude-agent-sdk",
            "--enable-claude-agent-sdk-adapter",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_bridge_only_orders_against_the_daemon_never_requires_it(self) -> None:
        """A bridge whose daemon is down must still answer /v1/health."""
        self.assertEqual(
            ["cofferdam-workstation.service"], _directive(BRIDGE_UNIT, "Unit", "After")
        )
        self.assertEqual([], _directive(BRIDGE_UNIT, "Unit", "Requires"))
        self.assertEqual([], _directive(BRIDGE_UNIT, "Unit", "BindsTo"))

    def test_the_bridge_may_write_only_its_own_state_directory(self) -> None:
        self.assertEqual(
            ["%h/cofferdam/state/actions-bridge"],
            _directive(BRIDGE_UNIT, "Service", "ReadWritePaths"),
        )
        self.assertEqual(["read-only"], _directive(BRIDGE_UNIT, "Service", "ProtectHome"))


class TunnelUnitTests(unittest.TestCase):
    def test_the_tunnel_runs_cloudflared_with_the_dedicated_config(self) -> None:
        exec_starts = _directive(TUNNEL_UNIT, "Service", "ExecStart")
        self.assertEqual(1, len(exec_starts))
        command = exec_starts[0]
        self.assertIn("cloudflared", command)
        self.assertIn("--config %h/cofferdam/config/actions-tunnel.yml", command)
        self.assertIn("tunnel run", command)

    def test_autoupdate_is_disabled(self) -> None:
        """An unattended binary swap under the only external surface Cofferdam has."""
        self.assertIn("--no-autoupdate", _directive(TUNNEL_UNIT, "Service", "ExecStart")[0])

    def test_the_metrics_endpoint_is_pinned_to_loopback(self) -> None:
        command = _directive(TUNNEL_UNIT, "Service", "ExecStart")[0]
        match = re.search(r"--metrics\s+(\S+)", command)
        self.assertIsNotNone(match, "the tunnel unit does not pin --metrics")
        self.assertTrue(
            match.group(1).startswith("127.0.0.1:"),
            f"metrics bound to {match.group(1)}, which is not loopback",
        )

    def test_the_tunnel_carries_no_token_on_its_command_line(self) -> None:
        """`cloudflared tunnel run --token <T>` would put a secret in /proc."""
        text = TUNNEL_UNIT.read_text(encoding="utf-8")
        self.assertNotIn("--token", text)
        self.assertNotIn("--token-file", text)


class NoSecretInAnyDeploymentFileTests(unittest.TestCase):
    """Nothing shipped may carry a credential, an account id or a UUID."""

    FILES = (
        BRIDGE_UNIT,
        TUNNEL_UNIT,
        BRIDGE_ENV,
        TUNNEL_CONFIG,
        CALLER_DROPIN,
        SLOT_DROPIN,
        RENDERER,
        VERIFIER,
    )

    def test_no_token_shaped_string_is_committed(self) -> None:
        for path in self.FILES:
            text = path.read_text(encoding="utf-8")
            for pattern in (
                r"Bearer\s+[A-Za-z0-9_\-]{20,}",
                r"\b[A-Za-z0-9_\-]{40,}\b",
                r"sk-[A-Za-z0-9]{16,}",
            ):
                with self.subTest(file=path.name, pattern=pattern):
                    found = re.search(pattern, text)
                    self.assertIsNone(
                        found, f"{path.name} contains {found.group(0)!r}" if found else ""
                    )

    def test_no_tunnel_uuid_is_committed(self) -> None:
        """A tunnel UUID is an account-scoped identifier, not a public fact."""
        uuid_shape = re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        )
        for path in self.FILES:
            with self.subTest(file=path.name):
                self.assertIsNone(uuid_shape.search(path.read_text(encoding="utf-8")))

    def test_no_machine_specific_value_is_committed(self) -> None:
        """Read this machine's own values at run time, never hard-code them."""
        import os

        machine_values = [
            str(Path.home()),
            os.environ.get("USER") or "",
            os.environ.get("HOSTNAME") or "",
        ]
        shapes = [
            (r"/home/(?!CHANGE_ME)[A-Za-z0-9._-]+", "a real absolute home path"),
            (r"/Users/(?!CHANGE_ME)[A-Za-z0-9._-]+", "a real macOS home path"),
            (
                r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
                "a tailnet address",
            ),
            (r"[A-Za-z0-9-]+\.ts\.net", "a tailnet hostname"),
            (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "an email address"),
        ]
        for path in self.FILES:
            text = path.read_text(encoding="utf-8")
            for pattern, what in shapes:
                with self.subTest(file=path.name, shape=what):
                    found = re.search(pattern, text)
                    self.assertIsNone(found, f"{what}: {found.group(0)!r}" if found else "")
            for value in machine_values:
                if len(value) < 4:
                    continue
                with self.subTest(file=path.name, value="<this machine>"):
                    self.assertNotIn(value, text)

    def test_the_caller_dropin_carries_only_the_switch(self) -> None:
        """The scoped credential is enabled by a boolean, never by a value."""
        lines = _sections(CALLER_DROPIN)["Service"]
        self.assertEqual(["Environment=COFFERDAM_ENABLE_ACTIONS_BRIDGE_CALLER=1"], lines)

    def test_the_caller_dropin_does_not_move_production(self) -> None:
        text = CALLER_DROPIN.read_text(encoding="utf-8")
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#"):
                continue
            self.assertFalse(line.startswith("ExecStart"))
            self.assertFalse(line.startswith("WorkingDirectory"))


class TunnelIngressTests(unittest.TestCase):
    """The single most important file in this milestone.

    Cloudflare can reach exactly what these rules name. Not "is allowed to" —
    can. Every guard below is about keeping that list at one entry.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = TUNNEL_CONFIG.read_text(encoding="utf-8")
        cls.rules: List[Dict[str, str]] = []
        in_ingress = False
        current: Dict[str, str] = {}
        for raw in cls.text.splitlines():
            if raw.startswith("ingress:"):
                in_ingress = True
                continue
            if not in_ingress:
                continue
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- "):
                if current:
                    cls.rules.append(current)
                current = {}
                stripped = stripped[2:]
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                current[key.strip()] = value.strip()
        if current:
            cls.rules.append(current)

    def test_exactly_one_hostname_is_routed(self) -> None:
        hostnames = [rule["hostname"] for rule in self.rules if "hostname" in rule]
        self.assertEqual(
            1, len(hostnames), f"the ingress routes {len(hostnames)} hostnames: {hostnames}"
        )

    def test_the_single_hostname_points_at_the_loopback_bridge(self) -> None:
        routed = [rule for rule in self.rules if "hostname" in rule]
        self.assertEqual("http://127.0.0.1:7210", routed[0]["service"])

    def test_the_last_rule_is_the_404_catch_all(self) -> None:
        last = self.rules[-1]
        self.assertNotIn("hostname", last)
        self.assertEqual("http_status:404", last["service"])

    def test_no_rule_targets_the_workstation_daemon(self) -> None:
        """7101 is the private API. It must not appear anywhere in this file."""
        for forbidden in (":7101", "7101"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)

    def test_no_rule_targets_a_tailnet_address_or_another_protocol(self) -> None:
        for rule in self.rules:
            service = rule.get("service", "")
            with self.subTest(service=service):
                self.assertFalse(service.startswith("ssh://"))
                self.assertFalse(service.startswith("rdp://"))
                self.assertFalse(service.startswith("tcp://"))
                self.assertFalse(service.startswith("unix:"))
                self.assertIsNone(
                    re.search(
                        r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}",
                        service,
                    )
                )

    def test_no_wildcard_hostname(self) -> None:
        for rule in self.rules:
            with self.subTest(rule=rule):
                self.assertNotIn("*", rule.get("hostname", ""))

    def test_no_path_only_rule_can_widen_the_surface(self) -> None:
        """A rule with a path but no hostname matches every hostname."""
        for rule in self.rules[:-1]:
            with self.subTest(rule=rule):
                self.assertIn("hostname", rule)

    def test_warp_routing_and_private_networks_are_absent(self) -> None:
        for forbidden in ("warp-routing", "route ip", "vnet"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)

    def test_metrics_stay_on_loopback(self) -> None:
        match = re.search(r"^metrics:\s*(\S+)", self.text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertTrue(match.group(1).startswith("127.0.0.1:"))


class BridgeEnvTemplateTests(unittest.TestCase):
    def test_the_template_binds_loopback(self) -> None:
        text = BRIDGE_ENV.read_text(encoding="utf-8")
        self.assertIn("COFFERDAM_BRIDGE_BIND_HOST=127.0.0.1", text)
        self.assertIn("COFFERDAM_BRIDGE_BIND_PORT=7210", text)

    def test_the_template_offers_no_credential_variable(self) -> None:
        """There is no env var for either credential, and the template proves it."""
        text = BRIDGE_ENV.read_text(encoding="utf-8")
        for forbidden in (
            "COFFERDAM_BRIDGE_KEY=",
            "COFFERDAM_BRIDGE_TOKEN=",
            "COFFERDAM_TOKEN=",
            "COFFERDAM_ENABLE_ACTIONS_BRIDGE_CALLER=",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_template_names_both_credential_files(self) -> None:
        text = BRIDGE_ENV.read_text(encoding="utf-8")
        self.assertIn("secrets/actions-bridge-key", text)
        self.assertIn("secrets/actions-bridge-internal-token", text)


class RendererTests(unittest.TestCase):
    """The production schema is rendered, verified, and never committed."""

    def _render(self, hostname: str):
        return subprocess.run(
            [sys.executable, str(RENDERER), "--hostname", hostname, "--stdout"],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_the_committed_schema_stays_a_placeholder(self) -> None:
        schema = (DOCS_ROOT / "custom-gpt" / "openapi.yaml").read_text(encoding="utf-8")
        self.assertIn("https://REPLACE-ME.example.invalid", schema)

    def test_rendering_substitutes_the_server_url(self) -> None:
        result = self._render("actions.example.com")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("  - url: https://actions.example.com", result.stdout)
        self.assertNotIn("REPLACE-ME", result.stdout)

    def test_rendering_preserves_every_operation_id(self) -> None:
        result = self._render("actions.example.com")
        for operation in OPERATION_IDS:
            with self.subTest(operation=operation):
                self.assertIn(f"operationId: {operation}", result.stdout)

    def test_rendering_preserves_the_consequential_markings(self) -> None:
        source = (DOCS_ROOT / "custom-gpt" / "openapi.yaml").read_text(encoding="utf-8")
        result = self._render("actions.example.com")
        self.assertEqual(
            re.findall(r"x-openai-isConsequential: (\w+)", source),
            re.findall(r"x-openai-isConsequential: (\w+)", result.stdout),
        )

    def test_the_rendered_schema_declares_exactly_one_server(self) -> None:
        result = self._render("actions.example.com")
        self.assertEqual(1, result.stdout.count("  - url: "))

    def test_an_ip_address_is_refused(self) -> None:
        """A certificate cannot be issued to an address; a schema naming one fails late."""
        result = self._render("127.0.0.1")
        self.assertEqual(2, result.returncode)
        self.assertIn("IP address", result.stderr)

    def test_a_reserved_namespace_is_refused(self) -> None:
        for hostname in ("bridge.local", "bridge.internal", "bridge.example.invalid"):
            with self.subTest(hostname=hostname):
                result = self._render(hostname)
                self.assertEqual(2, result.returncode)

    def test_an_uppercase_or_dotted_hostname_is_refused(self) -> None:
        for hostname in ("Actions.Example.Com", "actions.example.com."):
            with self.subTest(hostname=hostname):
                self.assertEqual(2, self._render(hostname).returncode)

    def test_rendering_is_deterministic(self) -> None:
        first = self._render("actions.example.com").stdout
        second = self._render("actions.example.com").stdout
        self.assertEqual(first, second)


class VerifierTests(unittest.TestCase):
    def test_the_verifier_is_executable_and_parses(self) -> None:
        self.assertTrue(VERIFIER.stat().st_mode & 0o111, "verify script is not executable")
        result = subprocess.run(
            ["bash", "-n", str(VERIFIER)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_the_verifier_never_prints_the_key(self) -> None:
        """The value is read into a variable and only ever handed to curl."""
        text = VERIFIER.read_text(encoding="utf-8")
        for forbidden in ('echo "$KEY"', "echo $KEY", 'printf "%s" "$KEY"\n', "cat $KEY_FILE"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_verifier_never_puts_the_key_on_a_command_line(self) -> None:
        """`curl -H "Authorization: Bearer $KEY"` is visible in /proc/<pid>/cmdline."""
        text = VERIFIER.read_text(encoding="utf-8")
        self.assertNotIn('-H "Authorization: Bearer $KEY"', text)
        self.assertNotIn("-H 'Authorization: Bearer $KEY'", text)

    def test_the_verifier_checks_the_private_surface_is_absent(self) -> None:
        text = VERIFIER.read_text(encoding="utf-8")
        for path in ("/api/remote-control", "/api/tasks", "/ws", "/api/registries"):
            with self.subTest(path=path):
                self.assertIn(path, text)

    def test_the_verifier_changes_nothing(self) -> None:
        """Read-only by construction: no mutation Action is ever invoked."""
        text = VERIFIER.read_text(encoding="utf-8")
        for forbidden in ("/cancel", "/finish", "/followup", "/answer"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


class PasteableInstructionsTests(unittest.TestCase):
    """The block that actually goes into the GPT builder.

    `docs/custom-gpt/INSTRUCTIONS.md` was written before anything had been pasted
    into a real editor, and its instruction block is ~11,200 characters. The
    Instructions box holds **8,000** and refuses to save anything longer — so the
    file the operator was told to copy could not be copied, and the only place
    that would have surfaced is the one step Gate A exists to perform.

    `gpt-instructions.md` is the bounded version. Being shorter is worthless if
    it dropped a safety sentence on the way, so both properties are asserted
    together: it fits, *and* it still says every load-bearing thing.

    Comparisons run against whitespace-flattened text. The file is wrapped prose,
    and a phrase that happens to straddle a line break is still present — an
    assertion that missed it would push somebody toward reflowing paragraphs to
    satisfy a test rather than to say something.
    """

    #: OpenAI's Instructions box limit, in characters rather than bytes: the
    #: block contains typographic punctuation, and the two counts differ by
    #: enough to matter at this size.
    INSTRUCTIONS_LIMIT = 8000

    @classmethod
    def setUpClass(cls) -> None:
        cls.path = DOCS_ROOT / "custom-gpt" / "gpt-instructions.md"
        cls.text = cls.path.read_text(encoding="utf-8")
        cls.flat = " ".join(cls.text.split())
        cls.flatlow = cls.flat.lower()
        cls.plain = " ".join(cls.text.replace("**", "").split())

    def test_it_fits_in_the_instructions_box(self) -> None:
        self.assertLessEqual(
            len(self.text),
            self.INSTRUCTIONS_LIMIT,
            f"the pasteable block is {len(self.text)} characters; the GPT "
            f"builder accepts {self.INSTRUCTIONS_LIMIT} and silently refuses to "
            "save anything longer.",
        )

    def test_the_operator_document_is_the_one_that_does_not_fit(self) -> None:
        """Guards the direction of the fix: shrink the paste, keep the reasoning.

        If somebody ever trims INSTRUCTIONS.md to fit instead, the worked
        examples and the rationale go with it — and this file stops having a
        reason to exist.
        """
        operator = (DOCS_ROOT / "custom-gpt" / "INSTRUCTIONS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("gpt-instructions.md", operator)
        self.assertIn("8,000 characters", operator)

    def test_every_action_is_named(self) -> None:
        for operation in OPERATION_IDS:
            if operation == "bridgeHealth":
                continue  # a tunnel probe, never something the model calls
            with self.subTest(operation=operation):
                self.assertIn(operation, self.text)

    def test_approvals_stay_separated_from_clarifications(self) -> None:
        self.assertIn("Tool approvals are not clarifications", self.flat)
        self.assertIn("no Action that can approve anything", self.plain)
        self.assertIn("there is no endpoint", self.plain)
        self.assertIn("permission to act", self.flatlow)
        self.assertIn("asking for *information*", self.plain)

    def test_guessing_a_task_id_is_forbidden(self) -> None:
        self.assertIn("Never guess or construct a `task_id`", self.flat)
        self.assertIn("listRecentTasks", self.text)
        self.assertIn("ambiguous", self.flatlow)

    def test_high_impact_decisions_are_never_automatic(self) -> None:
        for phrase in (
            "architecture direction",
            "new dependency",
            "data migration",
            "deletion",
            "production change",
            "public exposure",
            "authentication",
            "permissions",
            "security boundary",
            "irreversible",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.flatlow)
        self.assertIn("recommend, then wait", self.flatlow)

    def test_the_other_option_case_survives_the_condensation(self) -> None:
        self.assertIn('"Other"', self.text)
        self.assertIn("cannot carry", self.flatlow)
        self.assertIn("appended", self.flatlow)
        self.assertIn("close enough", self.flatlow)

    def test_no_background_push_is_promised(self) -> None:
        self.assertIn("cannot push", self.flatlow)
        self.assertIn("I'll let you know when it's done", self.flat)
        self.assertIn("never promise otherwise", self.flatlow)

    def test_secrets_are_never_requested(self) -> None:
        self.assertIn("never ask the user for a password", self.flatlow)

    def test_the_conventions_are_described_as_conventions(self) -> None:
        self.assertIn("not syntax", self.flatlow)
        for convention in (
            "@cf projects",
            "@cf send",
            "@cf sync",
            "@cf recent",
            "@cf answer",
            "@cf followup",
            "@cf cancel",
            "@cf finish",
        ):
            with self.subTest(convention=convention):
                self.assertIn(convention, self.text)
        self.assertIn("must work identically", self.flatlow)

    def test_the_artifact_absence_is_stated(self) -> None:
        self.assertIn("artifacts_supported", self.text)
        self.assertIn("always false", self.flatlow)

    def test_it_is_paste_content_and_nothing_else(self) -> None:
        """No title, no rules, no commentary — the whole file goes in the box."""
        self.assertFalse(self.text.startswith("#"), "the file opens with a title")
        self.assertNotIn("\n---\n", self.text, "the file contains a rule to split on")

    def test_it_carries_no_hostname_secret_or_machine_value(self) -> None:
        import os

        for pattern, what in (
            (r"/home/[A-Za-z0-9._-]+", "an absolute home path"),
            (r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b", "a tailnet address"),
            (r"[A-Za-z0-9-]+\.ts\.net", "a tailnet hostname"),
            (r"Bearer\s+[A-Za-z0-9_\-]{20,}", "a token"),
            (r"https?://", "a URL"),
        ):
            with self.subTest(shape=what):
                found = re.search(pattern, self.text)
                self.assertIsNone(found, f"{what}: {found.group(0)!r}" if found else "")
        for value in (str(Path.home()), os.environ.get("USER") or ""):
            if len(value) >= 4:
                self.assertNotIn(value, self.text)


class IdempotencyStorePermissionTests(unittest.TestCase):
    """The bridge's own table is owner-only, like the daemon's task store.

    It stores no request body, but it does store
    `(operation, scope, request_id) -> task_id`. Anybody who can read the file
    can therefore enumerate which tasks arrived through the Custom GPT and when
    — the same provider-traffic-to-task correlation `observe.py` keeps out of
    the journal. Created at the process umask it was 0755/0644, which published
    through the filesystem what the logging is careful not to publish through
    the log.
    """

    def test_the_store_creates_owner_only_paths(self) -> None:
        try:
            from cofferdam.actions_bridge.idempotency import IdempotencyStore
        except ImportError:
            self.skipTest("the actions-bridge extra is not installed")

        import stat as _stat
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "actions-bridge" / "idempotency.db"
            store = IdempotencyStore(path)
            try:
                self.assertEqual(
                    0o700,
                    _stat.S_IMODE(path.parent.stat().st_mode),
                    "the bridge state directory must be owner-only",
                )
                self.assertEqual(
                    0o600,
                    _stat.S_IMODE(path.stat().st_mode),
                    "the idempotency database must be owner-only",
                )
            finally:
                store.close()

    def test_an_existing_loose_database_is_tightened(self) -> None:
        """A host that already ran a build from before this fix gets corrected.

        Tightened rather than refused, unlike a credential file: this table is
        not a secret whose exposure cannot be undone, it is derived state whose
        worst case is a task id somebody could also read from the daemon's own
        database. Refusing to start over it would strand a working deployment.
        """
        try:
            from cofferdam.actions_bridge.idempotency import IdempotencyStore
        except ImportError:
            self.skipTest("the actions-bridge extra is not installed")

        import stat as _stat
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "actions-bridge" / "idempotency.db"
            path.parent.mkdir(parents=True)
            path.parent.chmod(0o755)
            path.touch(mode=0o644)
            store = IdempotencyStore(path)
            try:
                self.assertEqual(0o700, _stat.S_IMODE(path.parent.stat().st_mode))
                self.assertEqual(0o600, _stat.S_IMODE(path.stat().st_mode))
            finally:
                store.close()


class ExposureDocTests(unittest.TestCase):
    """The document is part of the boundary: it is what a person follows."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = EXPOSURE_DOC.read_text(encoding="utf-8")
        cls.lowered = cls.text.lower()

    def test_the_rollback_sequence_is_documented(self) -> None:
        for step in (
            "cloudflared tunnel delete",
            "systemctl --user disable",
            "actions-bridge-key",
            "actions-bridge-internal-token",
            "20-actions-bridge-caller.conf",
        ):
            with self.subTest(step=step):
                self.assertIn(step, self.text)

    def test_external_and_internal_keys_are_distinguished(self) -> None:
        self.assertIn("secrets/actions-bridge-key", self.text)
        self.assertIn("secrets/actions-bridge-internal-token", self.text)

    def test_key_rotation_is_documented(self) -> None:
        self.assertIn("--generate-key", self.text)
        self.assertIn("--force", self.text)

    def test_the_private_surface_is_stated_as_staying_private(self) -> None:
        for claim in ("pwa", "tailscale", "loopback"):
            with self.subTest(claim=claim):
                self.assertIn(claim, self.lowered)

    def test_cloudflare_access_is_documented_as_deliberately_off(self) -> None:
        self.assertIn("Cloudflare Access", self.text)

    def test_no_gate_b_capability_is_claimed(self) -> None:
        """Gate A must not imply the Agent SDK is enabled or a question shipped."""
        self.assertNotIn("Agent SDK is enabled", self.text)
        self.assertNotIn("AskUserQuestion works", self.text)
        self.assertIn("Gate B", self.text)

    def test_the_provider_usage_gate_is_stated(self) -> None:
        self.assertIn("provider", self.lowered)
        self.assertIn("approval", self.lowered)

    def test_the_document_carries_no_machine_specific_value(self) -> None:
        import os

        shapes = [
            r"/home/(?!CHANGE_ME|<)[A-Za-z0-9._-]+",
            r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
            r"[A-Za-z0-9-]+\.ts\.net",
        ]
        for pattern in shapes:
            with self.subTest(shape=pattern):
                found = re.search(pattern, self.text)
                self.assertIsNone(found, f"{found.group(0)!r}" if found else "")
        for value in (str(Path.home()), os.environ.get("USER") or ""):
            if len(value) < 4:
                continue
            with self.subTest(value="<this machine>"):
                self.assertNotIn(value, self.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
