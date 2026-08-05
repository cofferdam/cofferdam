"""Host, boot, and session identity — the three scopes every resource sits in.

These are short tests for a short module, but the properties are the ones every
other identity in the milestone is built on. If two different boots could
produce the same boot fingerprint, every process identity in the product would
silently become unsafe to act on.

The published values are *derived*: a domain-separated SHA-256 prefix rather
than the raw ``machine-id`` or ``boot_id``. Neither raw value is a secret, but
both are stable global identifiers, and an authenticated client needs the
comparison properties, not the identifier itself.
"""

from __future__ import annotations

import unittest

from cofferdam.workstation.runtime.identity import (
    SOURCE_HOSTNAME,
    SOURCE_MACHINE_ID,
    SOURCE_UNAVAILABLE,
    BootIdentity,
    detect_boot_identity,
    detect_host_identity,
    fingerprint,
    now_iso,
    session_identity_from,
)

from ._runtime_doubles import FakeSession


class FingerprintTests(unittest.TestCase):
    def test_the_same_input_gives_the_same_fingerprint(self) -> None:
        self.assertEqual(fingerprint("d", "a", "b"), fingerprint("d", "a", "b"))

    def test_different_inputs_give_different_fingerprints(self) -> None:
        self.assertNotEqual(fingerprint("d", "a"), fingerprint("d", "b"))

    def test_the_domain_separates_identical_inputs(self) -> None:
        """A host fingerprint and a boot fingerprint of the same bytes must differ."""
        self.assertNotEqual(fingerprint("cofferdam.host", "x"), fingerprint("cofferdam.boot", "x"))

    def test_field_boundaries_cannot_be_shifted(self) -> None:
        """Mutation check: ``("ab", "c")`` must not collide with ``("a", "bc")``.

        A naive concatenation would make them equal, which would let two
        different (pid, start-time) pairs share one process identity.
        """
        self.assertNotEqual(fingerprint("d", "ab", "c"), fingerprint("d", "a", "bc"))

    def test_the_raw_value_is_not_recoverable_from_the_published_one(self) -> None:
        raw = "9f4a1c2e-0000-4000-8000-000000000001"
        self.assertNotIn(raw, fingerprint("cofferdam.boot", raw))


class HostIdentityTests(unittest.TestCase):
    def test_this_host_yields_a_hostname_and_a_derived_id(self) -> None:
        identity = detect_host_identity()

        self.assertTrue(identity.hostname)
        self.assertTrue(identity.host_id.startswith("host-"))
        self.assertIn(identity.source, (SOURCE_MACHINE_ID, SOURCE_HOSTNAME))

    def test_the_source_is_published_so_a_weaker_identity_is_visible(self) -> None:
        """A hostname can be changed at any time; a machine-id cannot.

        Publishing which was used lets a caller weigh the identity instead of
        assuming the strong case.
        """
        self.assertIn("source", detect_host_identity().to_dict())

    def test_detection_is_stable_across_calls(self) -> None:
        self.assertEqual(detect_host_identity().host_id, detect_host_identity().host_id)


class BootIdentityTests(unittest.TestCase):
    def test_this_boot_yields_a_derived_id_and_a_boot_time(self) -> None:
        identity = detect_boot_identity()
        if not identity.available:  # pragma: no cover - non-Linux
            self.skipTest("this host publishes no boot id")

        self.assertTrue(identity.boot_id.startswith("boot-"))
        self.assertIsNotNone(identity.boot_epoch_seconds)
        self.assertTrue(identity.booted_at.endswith("Z"))

    def test_an_absent_boot_id_reports_unavailable_rather_than_a_placeholder(self) -> None:
        identity = BootIdentity(boot_id=None, source=SOURCE_UNAVAILABLE)
        self.assertFalse(identity.available)
        self.assertIsNone(identity.to_dict()["boot_id"])


class SessionIdentityTests(unittest.TestCase):
    def test_an_active_session_yields_a_derived_generation_id(self) -> None:
        identity = session_identity_from(FakeSession(session_id="12345"))

        self.assertTrue(identity.available)
        self.assertTrue(identity.session_id.startswith("gsession-"))
        self.assertEqual(identity.session_type, "wayland")

    def test_a_different_activation_stamp_is_a_different_session(self) -> None:
        """This is what makes a stale snapshot detectable after a re-login."""
        first = session_identity_from(FakeSession(session_id="12345"))
        second = session_identity_from(FakeSession(session_id="67890"))
        self.assertNotEqual(first.session_id, second.session_id)

    def test_the_same_activation_stamp_is_the_same_session(self) -> None:
        self.assertEqual(
            session_identity_from(FakeSession(session_id="12345")).session_id,
            session_identity_from(FakeSession(session_id="12345")).session_id,
        )

    def test_no_session_yields_no_id_and_carries_the_reason(self) -> None:
        identity = session_identity_from(
            FakeSession(available=False, session_id=None, reason="nobody has logged in yet")
        )
        self.assertFalse(identity.available)
        self.assertIsNone(identity.session_id)
        self.assertEqual(identity.reason, "nobody has logged in yet")

    def test_an_active_session_without_a_stamp_is_available_but_unidentified(self) -> None:
        """Available and not identifiable is a real state, and is not faked."""
        identity = session_identity_from(FakeSession(session_id=None))
        self.assertTrue(identity.available)
        self.assertIsNone(identity.session_id)


class TimestampTests(unittest.TestCase):
    def test_observed_at_is_utc_with_millisecond_precision(self) -> None:
        stamp = now_iso()
        self.assertTrue(stamp.endswith("Z"))
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
