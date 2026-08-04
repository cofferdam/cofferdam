"""Bounded waiting for the private bind address (an M1.1 finding).

The daemon starts at boot through lingering, often before ``tailscaled`` has an
address. Binding straight to the configured Tailscale IP then failed with
``EADDRNOTAVAIL``, the process exited, and the old unit's
``Restart=on-failure`` + ``StartLimitIntervalSec=0`` respawned it every three
seconds forever.

These tests pin the two properties that make that safe:

* the wait is **bounded** — it gives up and returns rather than blocking or
  looping forever;
* it never widens the bind. A private service that cannot reach its private
  interface stays down; it does not fall back to a public wildcard.

Standard-library only, so this runs on the stdlib-only CI path.
"""

from __future__ import annotations

import time
import unittest

from cofferdam.workstation.__main__ import (
    DEFAULT_BIND_WAIT_SECONDS,
    _address_assignable,
    _bind_wait_seconds,
    _is_loopback,
    wait_for_bind_address,
)
from unittest.mock import patch

# TEST-NET-3 (RFC 5737). Reserved for documentation, so it is never assigned to
# a real interface — the stand-in for "Tailscale has not come up yet".
UNASSIGNED_ADDRESS = "203.0.113.1"


class AddressAssignabilityTests(unittest.TestCase):
    def test_loopback_is_assignable(self) -> None:
        self.assertTrue(_address_assignable("127.0.0.1"))

    def test_an_unassigned_address_is_not_assignable(self) -> None:
        self.assertFalse(_address_assignable(UNASSIGNED_ADDRESS))

    def test_loopback_detection(self) -> None:
        self.assertTrue(_is_loopback("127.0.0.1"))
        self.assertTrue(_is_loopback("localhost"))
        self.assertFalse(_is_loopback("100.116.199.35"))


class BindWaitTests(unittest.TestCase):
    def test_an_available_address_returns_at_once(self) -> None:
        started = time.monotonic()
        self.assertTrue(wait_for_bind_address("127.0.0.1", timeout=30.0))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_a_missing_address_gives_up_within_the_timeout(self) -> None:
        """Bounded: this is what stops the restart storm."""
        started = time.monotonic()
        result = wait_for_bind_address(UNASSIGNED_ADDRESS, timeout=0.3, poll=0.05)
        elapsed = time.monotonic() - started
        self.assertFalse(result)
        self.assertLess(elapsed, 5.0, "the wait must be bounded by its timeout")

    def test_a_zero_timeout_does_not_block(self) -> None:
        started = time.monotonic()
        self.assertFalse(wait_for_bind_address(UNASSIGNED_ADDRESS, timeout=0.0))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_an_address_that_appears_later_is_picked_up(self) -> None:
        answers = [False, False, True]

        def assignable(_host: str) -> bool:
            return answers.pop(0) if answers else True

        with patch(
            "cofferdam.workstation.__main__._address_assignable", side_effect=assignable
        ):
            self.assertTrue(wait_for_bind_address("100.64.0.1", timeout=5.0, poll=0.01))


class BindWaitConfigTests(unittest.TestCase):
    def test_default_is_used_when_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_bind_wait_seconds(), DEFAULT_BIND_WAIT_SECONDS)

    def test_environment_override_is_honoured(self) -> None:
        with patch.dict("os.environ", {"COFFERDAM_BIND_WAIT_SECONDS": "7"}, clear=True):
            self.assertEqual(_bind_wait_seconds(), 7.0)

    def test_a_malformed_override_falls_back_to_the_default(self) -> None:
        with patch.dict("os.environ", {"COFFERDAM_BIND_WAIT_SECONDS": "soon"}, clear=True):
            self.assertEqual(_bind_wait_seconds(), DEFAULT_BIND_WAIT_SECONDS)

    def test_a_negative_override_is_clamped(self) -> None:
        with patch.dict("os.environ", {"COFFERDAM_BIND_WAIT_SECONDS": "-5"}, clear=True):
            self.assertEqual(_bind_wait_seconds(), 0.0)


if __name__ == "__main__":
    unittest.main()
