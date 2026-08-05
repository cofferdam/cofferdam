"""Every connection path ends in a state the user can act on.

The bug these exist for
-----------------------
A tablet that had been onboarded worked. A fresh iPhone loaded the PWA shell and
sat on "Connecting…" forever, with no token form and no error.

The cause was not a wrong message — it was that no message was ever produced.
``web/app.js`` began its boot with a bare ``localStorage.getItem(...)``. On iOS
Safari, storage access *throws* rather than returning null when Private Browsing
or "Block All Cookies" is in effect. The exception escaped the module's IIFE, so
the rest of the script never ran: ``#setup`` and ``#app`` kept the ``hidden``
attribute they are served with, and ``#connText`` kept the literal
"connecting…" baked into ``index.html``. The page was showing its own initial
markup, not a state.

A structural scan cannot catch that, because nothing in the file is *wrong* to
look at — the control flow simply never arrives. So these tests run the real
``web/app.js`` inside a stubbed DOM with a fake clock (``tests/pwa_harness.js``)
and assert on where each scenario actually lands.

The invariant, stated once: **no path may leave the page on "connecting…"**.
Every scenario below is a different way of failing, and each has to arrive at
authentication-required, authentication-rejected, unreachable, or connected.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "tests" / "pwa_harness.js"
NODE = shutil.which("node")

# The literal the harness seeds and the app is never allowed to expose.
HARNESS_TOKEN = "test-token-never-in-a-url"

STUCK = "connecting…"


def run_scenario(name: str) -> dict:
    result = subprocess.run(
        [NODE, str(HARNESS), name],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        raise AssertionError(
            "harness failed for %s: %s" % (name, result.stderr[-2000:])
        )
    return json.loads(result.stdout)


@unittest.skipUnless(NODE, "node is required to run the PWA behaviour harness")
class ConnectionStateTestCase(unittest.TestCase):
    """Shared assertions: whatever happened, the page must not be stuck."""

    def assertNotStuck(self, outcome: dict) -> None:
        self.assertIsNone(
            outcome["uncaught"],
            "an exception escaped app.js: the page keeps its served markup",
        )
        self.assertNotEqual(
            outcome["connText"], STUCK,
            "the page is still on its initial 'connecting…' with no state",
        )
        # Both panels hidden is the visual signature of the original bug: a
        # header and nothing else.
        self.assertFalse(
            outcome["setupHidden"] and outcome["appHidden"],
            "neither the token form nor the app is shown — the page is a dead end",
        )


class FreshDeviceTests(ConnectionStateTestCase):
    """(1) A device that has never been onboarded."""

    def test_a_fresh_device_is_asked_for_a_token(self) -> None:
        outcome = run_scenario("fresh_no_token")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "not connected")
        self.assertFalse(outcome["setupHidden"], "the token form must be offered")
        self.assertTrue(outcome["appHidden"])

    def test_a_fresh_device_makes_no_request_before_it_has_a_token(self) -> None:
        """No token means no authenticated call to fail — and nothing to log."""
        outcome = run_scenario("fresh_no_token")
        self.assertEqual(outcome["fetchUrls"], [])
        self.assertEqual(outcome["socketUrls"], [])


class BlockedStorageTests(ConnectionStateTestCase):
    """The reported iPhone failure, reproduced and pinned.

    ``localStorage`` raises on access. Before the fix this ended the script.
    """

    def test_blocked_storage_does_not_kill_the_boot(self) -> None:
        outcome = run_scenario("fresh_no_token_storage_blocked")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "not connected")
        self.assertFalse(outcome["setupHidden"])

    def test_the_user_is_told_the_token_cannot_be_remembered(self) -> None:
        """Degraded, and honest about being degraded."""
        outcome = run_scenario("fresh_no_token_storage_blocked")
        self.assertFalse(outcome["storageWarningHidden"])
        self.assertIn("local storage", outcome["storageWarningText"].lower())

    def test_a_token_entered_on_such_a_device_still_connects(self) -> None:
        """Storage is for convenience; it is not required to use the machine."""
        outcome = run_scenario("storage_blocked_then_token_entered")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "live")
        self.assertFalse(outcome["appHidden"])
        self.assertTrue(outcome["setupHidden"])


class AlreadyAuthenticatedDeviceTests(ConnectionStateTestCase):
    """(7) The working tablet must keep working, unchanged."""

    def test_a_stored_valid_token_connects_without_a_prompt(self) -> None:
        outcome = run_scenario("stored_token_valid")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "live")
        self.assertTrue(outcome["setupHidden"], "an onboarded device is not re-prompted")
        self.assertFalse(outcome["appHidden"])
        self.assertTrue(outcome["retryHidden"])

    def test_it_opens_exactly_one_socket(self) -> None:
        outcome = run_scenario("stored_token_valid")
        self.assertEqual(len(outcome["socketUrls"]), 1)


class RejectedTokenTests(ConnectionStateTestCase):
    """Authentication failure is reported as authentication, not connectivity."""

    def test_a_rejected_token_returns_to_the_form_with_a_reason(self) -> None:
        outcome = run_scenario("stored_token_rejected")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "token rejected")
        self.assertFalse(outcome["setupHidden"])
        self.assertFalse(outcome["setupErrorHidden"])

    def test_a_socket_closed_4401_is_treated_as_authentication(self) -> None:
        """4401 is the service refusing the token before upgrade. Retrying it
        forever would be persistence dressed over a permanent answer."""
        outcome = run_scenario("stored_token_ws_rejected")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "token rejected")
        self.assertFalse(outcome["setupHidden"])


class UnreachableTests(ConnectionStateTestCase):
    """(5) Bounded: an attempt that goes nowhere has to say so."""

    def test_a_status_request_that_never_answers_times_out(self) -> None:
        outcome = run_scenario("stored_token_status_timeout")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "unreachable")
        self.assertFalse(outcome["connBannerHidden"], "the real reason must be shown")
        self.assertFalse(outcome["retryHidden"], "retry must be offered")

    def test_a_socket_that_never_opens_times_out(self) -> None:
        """A hanging socket never fires onerror on iOS, so the wait is bounded
        in our own code rather than left to the browser."""
        outcome = run_scenario("stored_token_ws_hangs")
        self.assertNotStuck(outcome)
        self.assertEqual(outcome["connText"], "unreachable")
        self.assertFalse(outcome["retryHidden"])

    def test_background_retries_do_not_reset_the_state_to_connecting(self) -> None:
        """Regression for a bug this harness found while fixing the first one.

        The reconnect loop called ``setConnState(CONNECTING)`` on every attempt.
        Against a socket that hangs rather than refusing, that produced an
        indefinite "connecting…" all over again — by loop this time instead of
        by exception. Only the first attempt may show it.
        """
        outcome = run_scenario("stored_token_ws_hangs")
        self.assertNotEqual(outcome["connText"], STUCK)
        self.assertGreater(
            len(outcome["socketUrls"]), 1,
            "the fixture should have retried, so the state is genuinely under test",
        )


class TokenIsNeverExposedTests(ConnectionStateTestCase):
    """(6) The token is a secret in every one of these paths."""

    SCENARIOS = (
        "fresh_no_token",
        "fresh_no_token_storage_blocked",
        "stored_token_valid",
        "stored_token_rejected",
        "stored_token_status_timeout",
        "stored_token_ws_hangs",
        "stored_token_ws_rejected",
        "storage_blocked_then_token_entered",
    )

    def test_the_token_never_appears_in_any_url(self) -> None:
        for name in self.SCENARIOS:
            with self.subTest(scenario=name):
                outcome = run_scenario(name)
                for url in outcome["fetchUrls"] + outcome["socketUrls"]:
                    self.assertNotIn(
                        HARNESS_TOKEN, url,
                        "a URL carrying the token reaches history and proxy logs",
                    )

    def test_the_token_is_never_written_to_the_console(self) -> None:
        for name in self.SCENARIOS:
            with self.subTest(scenario=name):
                outcome = run_scenario(name)
                for line in outcome["consoleOutput"]:
                    self.assertNotIn(HARNESS_TOKEN, line)

    def test_the_socket_carries_the_token_in_the_subprotocol(self) -> None:
        """Browsers cannot set headers on a WebSocket; the subprotocol is the
        only place a token can ride that is not the URL."""
        outcome = run_scenario("stored_token_valid")
        protocols = outcome["socketProtocols"][0]
        self.assertEqual(protocols[0], "cofferdam-token")
        self.assertEqual(protocols[1], HARNESS_TOKEN)
        self.assertNotIn(HARNESS_TOKEN, outcome["socketUrls"][0])


@unittest.skipUnless(NODE, "node is required to run the PWA behaviour harness")
class StorageAccessIsAlwaysGuardedTests(unittest.TestCase):
    """A structural backstop for the specific mistake that caused this.

    The behaviour tests above would catch a regression, but only in the stubbed
    shapes they model. This says the dangerous form is not in the file at all.
    """

    def test_no_unguarded_localstorage_access_remains(self) -> None:
        source = (REPO_ROOT / "web" / "app.js").read_text(encoding="utf-8")
        # Every real access goes through global.localStorage inside a try block
        # in the storage helpers. A bare `localStorage.` is the pre-fix shape.
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("*") and not line.strip().startswith("//")
        ]
        for line in code_lines:
            stripped = line.strip()
            if "localStorage." in stripped:
                self.assertIn(
                    "global.localStorage.", stripped,
                    "bare localStorage access can throw on iOS Safari: " + stripped,
                )

    def test_the_boot_is_wrapped(self) -> None:
        source = (REPO_ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("try {\n    boot();\n  } catch (error) {", source)
        self.assertIn("bootFailed", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
