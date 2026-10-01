"""PLC memory segments, address conversion and value packing.

Conventions used throughout the package:

* PLC addresses are 1-based, as written on HMIs and in FANUC manuals: ``%R1`` is
  the first register word, ``%I1`` the first input bit.
* SRTP wire indexes are 0-based. :func:`srtp_index` is the only place that
  converts one into the other.
* Words are 16-bit little-endian. 32-bit values occupy two consecutive words,
  low word first, so their four bytes are plain little-endian.
* Reals are IEEE-754 float32. Compare them with :func:`f32_equal`.
* Strings are packed two characters per word, first character in the low byte.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, IntEnum

MAX_PLC_ADDRESS = 65536
"""Highest 1-based address that fits the 16-bit wire index."""


class Unit(Enum):
    """Addressing unit of a segment. Counts on the wire use the same unit."""

    WORD = "word"
    BYTE = "byte"
    BIT = "bit"


class Segment(IntEnum):
    """GE SRTP segment selectors (request byte 43, or byte 51 for extended frames)."""

    R = 0x08
    AI = 0x0A
    AQ = 0x0C
    I_BYTE = 0x10
    Q_BYTE = 0x12
    T_BYTE = 0x14
    M_BYTE = 0x16
    SA_BYTE = 0x18
    SB_BYTE = 0x1A
    SC_BYTE = 0x1C
    S_BYTE = 0x1E
    G_BYTE = 0x38
    I_BIT = 0x46
    Q_BIT = 0x48
    T_BIT = 0x4A
    M_BIT = 0x4C
    SA_BIT = 0x4E
    SB_BIT = 0x50
    SC_BIT = 0x52
    S_BIT = 0x54
    G_BIT = 0x56

    @property
    def unit(self) -> Unit:
        if self in (Segment.R, Segment.AI, Segment.AQ):
            return Unit.WORD
        if self.value >= 0x46:
            return Unit.BIT
        return Unit.BYTE

    @property
    def plc_name(self) -> str:
        """Name as written in PLC addresses, e.g. ``%R`` or ``%Q``."""
        return "%" + self.name.split("_")[0]


def srtp_index(address: int) -> int:
    """Convert a 1-based PLC address (``%R1`` -> 1) to a 0-based SRTP wire index.

    This is the single place in the package where the conversion happens.
    """
    if isinstance(address, bool) or not isinstance(address, int):
        raise TypeError(f"PLC address must be an int, got {type(address).__name__}")
    if not 1 <= address <= MAX_PLC_ADDRESS:
        raise ValueError(f"PLC address {address} outside 1..{MAX_PLC_ADDRESS}")
    return address - 1


def plc_address(index: int) -> int:
    """Inverse of :func:`srtp_index`, used only for messages and logs."""
    if not 0 <= index < MAX_PLC_ADDRESS:
        raise ValueError(f"SRTP index {index} outside 0..{MAX_PLC_ADDRESS - 1}")
    return index + 1


def check_span(address: int, count: int) -> None:
    """Validate that ``count`` units starting at 1-based ``address`` fit the address space."""
    srtp_index(address)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"count must be a positive int, got {count!r}")
    if address + count - 1 > MAX_PLC_ADDRESS:
        raise ValueError(f"span {address}..{address + count - 1} exceeds {MAX_PLC_ADDRESS}")


# --- words ------------------------------------------------------------------


def words_to_bytes(words: Iterable[int]) -> bytes:
    """Pack unsigned 16-bit words little-endian."""
    out = bytearray()
    for w in words:
        if not 0 <= w <= 0xFFFF:
            raise ValueError(f"word {w} outside 0..65535")
        out += w.to_bytes(2, "little")
    return bytes(out)


def bytes_to_words(data: bytes) -> list[int]:
    """Unpack little-endian unsigned 16-bit words. ``data`` must have even length."""
    if len(data) % 2:
        raise ValueError(f"odd byte count {len(data)} cannot form 16-bit words")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


def decode_int16(data: bytes, offset: int = 0) -> int:
    return int(struct.unpack_from("<h", data, offset)[0])


def decode_uint16(data: bytes, offset: int = 0) -> int:
    return int(struct.unpack_from("<H", data, offset)[0])


def decode_int32(data: bytes, offset: int = 0) -> int:
    return int(struct.unpack_from("<i", data, offset)[0])


def decode_float32(data: bytes, offset: int = 0) -> float:
    return float(struct.unpack_from("<f", data, offset)[0])


def encode_int16(value: int) -> bytes:
    if not -0x8000 <= value <= 0x7FFF:
        raise ValueError(f"{value} does not fit a signed 16-bit word")
    return struct.pack("<h", value)


def encode_uint16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{value} does not fit an unsigned 16-bit word")
    return struct.pack("<H", value)


def encode_int32(value: int) -> bytes:
    if not -0x80000000 <= value <= 0x7FFFFFFF:
        raise ValueError(f"{value} does not fit a signed 32-bit integer")
    return struct.pack("<i", value)


def encode_float32(value: float) -> bytes:
    """Encode as IEEE-754 float32. Rejects NaN, infinities and out-of-range values."""
    if not math.isfinite(value):
        raise ValueError(f"{value} is not a finite number")
    try:
        return struct.pack("<f", value)
    except OverflowError as exc:
        raise ValueError(f"{value} does not fit float32") from exc


def f32(value: float) -> float:
    """Round ``value`` to the nearest float32, as the controller stores it."""
    return decode_float32(encode_float32(value))


def f32_equal(a: float, b: float, tolerance: float = 0.0) -> bool:
    """Compare two reals at float32 precision.

    Both values are rounded to float32 first, then compared with an absolute
    ``tolerance`` (0 means bit-for-bit equal after rounding).
    """
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    return abs(f32(a) - f32(b)) <= tolerance


# --- strings ----------------------------------------------------------------


def encode_string(text: str, n_bytes: int) -> bytes:
    """Encode ``text`` into exactly ``n_bytes`` bytes, NUL padded.

    Only 7-bit ASCII is accepted, because the controller's other character sets
    (for example Katakana) are not verified. Raises if ``text`` does not fit.
    """
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"only ASCII text can be written, got {text!r}") from exc
    if b"\x00" in raw:
        raise ValueError("text must not contain NUL characters")
    if len(raw) > n_bytes:
        raise ValueError(f"text is {len(raw)} bytes, the target holds {n_bytes}")
    return raw + bytes(n_bytes - len(raw))


def decode_string(data: bytes) -> str:
    """Decode a NUL-terminated (or full-length) controller string.

    Bytes are decoded as Latin-1 so no byte is lost; ASCII text decodes as-is.
    """
    end = data.find(b"\x00")
    if end >= 0:
        data = data[:end]
    return data.decode("latin-1")


# --- bits -------------------------------------------------------------------


@dataclass(frozen=True)
class BitSpan:
    """A byte-aligned bit read covering ``start``..``start + count - 1`` (0-based bits)."""

    start: int
    count: int
    aligned_start: int
    aligned_count: int

    @property
    def n_bytes(self) -> int:
        return self.aligned_count // 8


def align_bits(start: int, count: int) -> BitSpan:
    """Round a 0-based bit span out to whole bytes.

    The controller returns bit data byte-aligned, so reads are always issued for
    whole bytes and the requested bits are sliced out afterwards.
    """
    if start < 0 or count < 1:
        raise ValueError(f"invalid bit span start={start} count={count}")
    aligned_start = start - start % 8
    end = start + count
    aligned_end = end + (-end % 8)
    return BitSpan(start, count, aligned_start, aligned_end - aligned_start)


def unpack_bits(data: bytes, span: BitSpan) -> list[bool]:
    """Extract ``span.count`` bits from byte-aligned ``data`` (LSB first in each byte)."""
    if len(data) != span.n_bytes:
        raise ValueError(f"expected {span.n_bytes} bytes of bit data, got {len(data)}")
    offset = span.start - span.aligned_start
    return [bool(data[(offset + i) // 8] >> ((offset + i) % 8) & 1) for i in range(span.count)]


def pack_bits(values: Sequence[bool], start: int) -> bytes:
    """Pack bits for a write starting at 0-based bit ``start``.

    The first value lands at bit position ``start % 8`` of the first byte; bits
    before it are zero. This matches captured writes from a commercial driver
    (e.g. one bit at index 9 is sent as byte 0x02).
    """
    if not values:
        raise ValueError("no bits to pack")
    offset = start % 8
    n_bytes = (offset + len(values) + 7) // 8
    out = bytearray(n_bytes)
    for i, value in enumerate(values):
        if value:
            pos = offset + i
            out[pos // 8] |= 1 << (pos % 8)
    return bytes(out)


# --- chunking ---------------------------------------------------------------


def plan_word_chunks(index: int, count: int, max_bytes: int) -> list[tuple[int, int]]:
    """Split a word read of ``count`` words at 0-based ``index`` into requests.

    Each chunk is at most ``max_bytes`` bytes (rounded down to whole words).
    Returns ``[(index, count), ...]`` covering the span exactly once, in order.
    """
    per = max_bytes // 2
    if per < 1:
        raise ValueError("max_bytes must allow at least one word")
    return [(index + off, min(per, count - off)) for off in range(0, count, per)]


def plan_bit_chunks(span: BitSpan, max_bytes: int) -> list[tuple[int, int]]:
    """Split a byte-aligned bit read into requests of at most ``max_bytes`` bytes.

    Returns ``[(aligned_bit_index, bit_count), ...]``; every chunk is byte aligned.
    """
    per_bits = (max_bytes // 1) * 8
    if per_bits < 8:
        raise ValueError("max_bytes must allow at least one byte")
    return [
        (span.aligned_start + off, min(per_bits, span.aligned_count - off))
        for off in range(0, span.aligned_count, per_bits)
    ]


def plan_byte_chunks(index: int, count: int, max_bytes: int) -> list[tuple[int, int]]:
    """Split a byte-unit read into requests of at most ``max_bytes`` bytes."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")
    return [(index + off, min(max_bytes, count - off)) for off in range(0, count, max_bytes)]
