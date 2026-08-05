"""Just enough EDID to identify a panel — and nothing more.

EDID is the block of bytes a monitor hands the graphics card describing itself.
Cofferdam reads it for exactly two reasons:

1. **Identity.** The SHA-256 of the block is a hardware fingerprint that
   survives reboots, cable swaps, and connector renumbering. ``HDMI-1`` becomes
   ``HDMI-2`` when a cable moves; the digest does not move.
2. **Physical size.** Millimetres are the one property no compositor API on this
   host reports through its current (non-deprecated) interface.

Everything else in EDID — timings, chromaticity, colour primaries, CEA
extensions — is deliberately not parsed. Parsing bytes from a device is exactly
where a decoder gets to be wrong, so the surface is kept to the smallest thing
that answers those two questions, every field is bounds-checked, and a block
that does not parse yields ``None`` rather than a guess.

The manufacturer/model/serial parsing here exists to **match** a sysfs EDID
block against the same panel as reported over D-Bus, not to be the published
answer: :mod:`~cofferdam.workstation.runtime.displays` prefers the compositor's
own strings. Reproducing the compositor's exact fallback spellings
(``0x53ab`` for a panel with no model descriptor) is what makes that match
reliable, so those spellings are followed to the byte.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple

# An EDID base block is 128 bytes; extensions add further 128-byte blocks.
EDID_BLOCK_BYTES = 128
EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"

# Descriptor tags in the four 18-byte descriptor slots of the base block.
DESCRIPTOR_MONITOR_NAME = 0xFC
DESCRIPTOR_MONITOR_SERIAL = 0xFF

_DESCRIPTOR_OFFSETS = (54, 72, 90, 108)
_DESCRIPTOR_LENGTH = 18

# A plausible panel is somewhere between a small tablet and the largest
# consumer television (a 115" set is about 2.55 m wide). Anything outside this
# range is a misparse, and the failure mode of refusing it is benign — the card
# shows no physical size — while the failure mode of publishing it is a
# measurement that is simply wrong.
_MIN_DIMENSION_MM = 10
_MAX_DIMENSION_MM = 3000


SOURCE_DESCRIPTOR = "edid-descriptor"
SOURCE_PRODUCT_CODE = "edid-product-code"
SOURCE_SERIAL_NUMBER = "edid-serial-number"


@dataclass(frozen=True)
class EdidInfo:
    """What one EDID block says about the panel it came from.

    ``model_source`` matters to anything that displays the model. A panel that
    ships a monitor-name descriptor gives a real name (``VA1650-FHD``); one that
    does not gives its numeric product code rendered as ``0x53ab``. Both are
    truthful reports of what the hardware said, but only the first is a name,
    and a caller that puts a product code where a user expects a name has made
    the inventory harder to read without making it more honest.
    """

    sha256: str
    manufacturer: Optional[str]
    model: Optional[str]
    serial: Optional[str]
    width_mm: Optional[int]
    height_mm: Optional[int]
    model_source: Optional[str] = None
    serial_source: Optional[str] = None

    @property
    def match_key(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """The triple a compositor reports for the same panel."""
        return (self.manufacturer, self.model, self.serial)


def _manufacturer(block: bytes) -> Optional[str]:
    """The three-letter PnP ID packed into bytes 8-9 as five-bit letters."""
    packed = (block[8] << 8) | block[9]
    letters = []
    for shift in (10, 5, 0):
        value = (packed >> shift) & 0x1F
        if not 1 <= value <= 26:
            return None
        letters.append(chr(ord("A") + value - 1))
    return "".join(letters)


def _descriptor_string(block: bytes, tag: int) -> Optional[str]:
    """The text of the first descriptor carrying ``tag``, if any."""
    for offset in _DESCRIPTOR_OFFSETS:
        chunk = block[offset : offset + _DESCRIPTOR_LENGTH]
        if len(chunk) < _DESCRIPTOR_LENGTH:
            return None
        # A text descriptor starts 00 00 00 <tag> 00; anything else in the
        # first two bytes is a detailed timing block.
        if chunk[0] or chunk[1] or chunk[2] or chunk[4]:
            continue
        if chunk[3] != tag:
            continue
        text = chunk[5:].split(b"\n", 1)[0]
        decoded = text.decode("ascii", "ignore").strip()
        if decoded:
            return decoded
    return None


def _physical_size_mm(block: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Physical size, preferring the detailed-timing millimetres.

    Bytes 21/22 hold whole centimetres, which rounds a 344 mm panel to 340 mm.
    The first detailed timing descriptor carries the same measurement in
    millimetres, so it is tried first and the centimetre pair is the fallback.
    A projector reports 0/0 for both, which stays ``None`` rather than becoming
    a zero-sized display.
    """
    for offset in _DESCRIPTOR_OFFSETS:
        chunk = block[offset : offset + _DESCRIPTOR_LENGTH]
        if len(chunk) < _DESCRIPTOR_LENGTH:
            break
        if chunk[0] == 0 and chunk[1] == 0:
            continue  # not a detailed timing descriptor
        width = ((chunk[14] & 0xF0) << 4) | chunk[12]
        height = ((chunk[14] & 0x0F) << 8) | chunk[13]
        if _plausible(width) and _plausible(height):
            return width, height
        break  # only the first detailed timing describes the preferred mode

    width_cm, height_cm = block[21], block[22]
    if width_cm and height_cm:
        width, height = width_cm * 10, height_cm * 10
        if _plausible(width) and _plausible(height):
            return width, height
    return None, None


def _plausible(value: int) -> bool:
    return _MIN_DIMENSION_MM <= value <= _MAX_DIMENSION_MM


def parse_edid(data: bytes) -> Optional[EdidInfo]:
    """Parse an EDID block, or return ``None`` if it is not one.

    The header check is not a formality: ``/sys/class/drm/*/edid`` is an empty
    file for a disconnected connector and can be a short read for a flaky
    cable, and treating either as a panel would invent a display.
    """
    if len(data) < EDID_BLOCK_BYTES or not data.startswith(EDID_HEADER):
        return None
    block = data[:EDID_BLOCK_BYTES]

    manufacturer = _manufacturer(block)

    # The fallback spellings below mirror Mutter's, so a sysfs block and a
    # compositor report of the same panel compare equal.
    model = _descriptor_string(block, DESCRIPTOR_MONITOR_NAME)
    model_source = SOURCE_DESCRIPTOR
    if model is None:
        product_code = block[10] | (block[11] << 8)
        model = "0x%04x" % product_code
        model_source = SOURCE_PRODUCT_CODE

    serial = _descriptor_string(block, DESCRIPTOR_MONITOR_SERIAL)
    serial_source = SOURCE_DESCRIPTOR
    if serial is None:
        serial_number = int.from_bytes(block[12:16], "little")
        serial = "0x%08x" % serial_number
        serial_source = SOURCE_SERIAL_NUMBER

    width_mm, height_mm = _physical_size_mm(block)

    return EdidInfo(
        # The digest covers everything that was read, extension blocks
        # included: two panels differing only in an extension are two panels.
        sha256=hashlib.sha256(data).hexdigest(),
        manufacturer=manufacturer,
        model=model,
        serial=serial,
        width_mm=width_mm,
        height_mm=height_mm,
        model_source=model_source,
        serial_source=serial_source,
    )


__all__ = [
    "EDID_BLOCK_BYTES",
    "EDID_HEADER",
    "EdidInfo",
    "SOURCE_DESCRIPTOR",
    "SOURCE_PRODUCT_CODE",
    "SOURCE_SERIAL_NUMBER",
    "parse_edid",
]
