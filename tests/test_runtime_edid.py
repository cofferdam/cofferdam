"""EDID parsing: a fingerprint and a size, and nothing made up.

The parser is small on purpose, so its tests are about the edges rather than
about coverage: a block that is not an EDID at all, a panel that publishes no
model name, a projector reporting a zero physical size, and the fallback
spellings that must match the compositor's byte for byte — because the join
between the kernel's EDID and the compositor's monitor list depends on them.
"""

from __future__ import annotations

import hashlib
import unittest

from cofferdam.workstation.runtime.edid import (
    SOURCE_DESCRIPTOR,
    SOURCE_PRODUCT_CODE,
    SOURCE_SERIAL_NUMBER,
    parse_edid,
)

from ._runtime_doubles import build_edid


class ValidBlockTests(unittest.TestCase):
    def test_a_full_block_yields_every_field(self) -> None:
        block = build_edid(
            manufacturer="VSC",
            product_code=0x6943,
            model_name="VA1650-FHD",
            serial_text="Y39252000375",
            width_mm=344,
            height_mm=194,
        )
        info = parse_edid(block)

        self.assertEqual(info.manufacturer, "VSC")
        self.assertEqual(info.model, "VA1650-FHD")
        self.assertEqual(info.serial, "Y39252000375")
        self.assertEqual((info.width_mm, info.height_mm), (344, 194))
        self.assertEqual(info.model_source, SOURCE_DESCRIPTOR)
        self.assertEqual(info.serial_source, SOURCE_DESCRIPTOR)

    def test_the_digest_covers_every_byte_that_was_read(self) -> None:
        block = build_edid(manufacturer="AUO")
        self.assertEqual(parse_edid(block).sha256, hashlib.sha256(block).hexdigest())

    def test_two_panels_differing_only_in_serial_have_different_digests(self) -> None:
        first = build_edid(manufacturer="VSC", model_name="VA1650", serial_text="AAA1")
        second = build_edid(manufacturer="VSC", model_name="VA1650", serial_text="AAA2")
        self.assertNotEqual(parse_edid(first).sha256, parse_edid(second).sha256)

    def test_the_same_panel_read_twice_has_the_same_digest(self) -> None:
        block = build_edid(manufacturer="VSC", model_name="VA1650", serial_text="AAA1")
        self.assertEqual(parse_edid(block).sha256, parse_edid(block).sha256)


class FallbackSpellingTests(unittest.TestCase):
    """A panel with no descriptors is described by numbers — the same numbers."""

    def test_a_missing_model_name_becomes_the_product_code_in_hex(self) -> None:
        info = parse_edid(build_edid(manufacturer="AUO", product_code=0x53AB))
        self.assertEqual(info.model, "0x53ab")
        self.assertEqual(info.model_source, SOURCE_PRODUCT_CODE)

    def test_a_missing_serial_string_becomes_the_serial_number_in_hex(self) -> None:
        info = parse_edid(build_edid(manufacturer="AUO", serial_number=0))
        self.assertEqual(info.serial, "0x00000000")
        self.assertEqual(info.serial_source, SOURCE_SERIAL_NUMBER)

    def test_the_fallback_matches_what_the_compositor_reports(self) -> None:
        """The join key. Observed on the validation host: Mutter reported the
        built-in panel as ``('eDP-1', 'AUO', '0x53ab', '0x00000000')`` for an
        EDID with no name and no serial descriptor. If these spellings drift,
        the EDID/compositor join silently falls back to connector names.
        """
        info = parse_edid(build_edid(manufacturer="AUO", product_code=0x53AB, serial_number=0))
        self.assertEqual(info.match_key, ("AUO", "0x53ab", "0x00000000"))


class RejectedBlockTests(unittest.TestCase):
    """Not-an-EDID must not become a display."""

    def test_an_empty_block_is_rejected(self) -> None:
        """A disconnected connector's ``edid`` file is empty."""
        self.assertIsNone(parse_edid(b""))

    def test_a_block_without_the_edid_header_is_rejected(self) -> None:
        self.assertIsNone(parse_edid(b"\x01" * 128))

    def test_a_truncated_block_is_rejected(self) -> None:
        """A flaky cable produces a short read, not a small monitor."""
        self.assertIsNone(parse_edid(build_edid()[:64]))

    def test_a_block_with_a_valid_header_but_junk_body_does_not_crash(self) -> None:
        block = bytearray(build_edid())
        block[8:128] = bytes(range(120))
        info = parse_edid(bytes(block))
        self.assertIsNotNone(info, "a parseable header should still yield a digest")


class PhysicalSizeTests(unittest.TestCase):
    def test_millimetres_from_the_detailed_timing_are_preferred_over_centimetres(self) -> None:
        """Bytes 21/22 round 344 mm down to 340. The detailed timing does not."""
        info = parse_edid(build_edid(width_mm=344, height_mm=194, width_cm=34, height_cm=19))
        self.assertEqual((info.width_mm, info.height_mm), (344, 194))

    def test_centimetres_are_used_when_no_detailed_timing_carries_the_size(self) -> None:
        info = parse_edid(build_edid(width_cm=34, height_cm=19))
        self.assertEqual((info.width_mm, info.height_mm), (340, 190))

    def test_a_projector_reporting_no_size_stays_absent(self) -> None:
        """Zeroes are "not stated", and must not become a zero-sized display."""
        info = parse_edid(build_edid(width_cm=0, height_cm=0))
        self.assertIsNone(info.width_mm)
        self.assertIsNone(info.height_mm)

    def test_an_implausible_size_is_refused_rather_than_reported(self) -> None:
        """Mutation check: a misparse must not be published as a measurement."""
        info = parse_edid(build_edid(width_mm=4095, height_mm=4095))
        self.assertIsNone(info.width_mm, "a 4 m panel is a misparse, not a monitor")
        self.assertIsNone(info.height_mm)

    def test_a_large_but_real_television_is_still_accepted(self) -> None:
        """The other side of the ceiling: it must not reject real hardware."""
        info = parse_edid(build_edid(width_mm=2500, height_mm=1400))
        self.assertEqual((info.width_mm, info.height_mm), (2500, 1400))


class ManufacturerTests(unittest.TestCase):
    def test_the_three_letter_pnp_code_round_trips(self) -> None:
        for code in ("AUO", "VSC", "DEL", "SAM"):
            with self.subTest(code=code):
                self.assertEqual(parse_edid(build_edid(manufacturer=code)).manufacturer, code)

    def test_an_out_of_range_manufacturer_encoding_yields_none(self) -> None:
        block = bytearray(build_edid())
        block[8:10] = b"\x00\x00"  # all five-bit letters are zero: invalid
        self.assertIsNone(parse_edid(bytes(block)).manufacturer)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
