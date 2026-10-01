"""Index conversion, packing and chunking: unit and property tests."""

from __future__ import annotations

import math
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fanuc_snpx.memory import (
    MAX_PLC_ADDRESS,
    Segment,
    Unit,
    align_bits,
    bytes_to_words,
    check_span,
    decode_float32,
    decode_int32,
    decode_string,
    encode_float32,
    encode_int16,
    encode_int32,
    encode_string,
    f32,
    f32_equal,
    pack_bits,
    plan_bit_chunks,
    plan_byte_chunks,
    plan_word_chunks,
    plc_address,
    srtp_index,
    unpack_bits,
    words_to_bytes,
)

# --- index conversion -----------------------------------------------------------


def test_srtp_index_is_zero_based() -> None:
    assert srtp_index(1) == 0
    assert srtp_index(10000) == 9999
    assert srtp_index(MAX_PLC_ADDRESS) == 65535


@pytest.mark.parametrize("bad", [0, -1, MAX_PLC_ADDRESS + 1])
def test_srtp_index_rejects_out_of_range(bad: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        srtp_index(bad)


@pytest.mark.parametrize("bad", [True, 1.0, "1", None])
def test_srtp_index_rejects_non_int(bad: object) -> None:
    with pytest.raises(TypeError):
        srtp_index(bad)  # type: ignore[arg-type]


@given(st.integers(min_value=1, max_value=MAX_PLC_ADDRESS))
def test_index_round_trip(address: int) -> None:
    assert plc_address(srtp_index(address)) == address


def test_check_span() -> None:
    check_span(65536, 1)
    with pytest.raises(ValueError, match="exceeds"):
        check_span(65536, 2)
    with pytest.raises(ValueError, match="count"):
        check_span(1, 0)


def test_segment_units_and_names() -> None:
    assert Segment.R.unit is Unit.WORD
    assert Segment.AQ.unit is Unit.WORD
    assert Segment.I_BIT.unit is Unit.BIT
    assert Segment.G_BYTE.unit is Unit.BYTE
    assert Segment.Q_BIT.plc_name == "%Q"
    assert Segment.R.plc_name == "%R"


# --- words and numbers --------------------------------------------------------------


@given(st.lists(st.integers(min_value=0, max_value=0xFFFF), max_size=64))
def test_words_round_trip(words: list[int]) -> None:
    assert bytes_to_words(words_to_bytes(words)) == words


def test_words_are_little_endian() -> None:
    assert words_to_bytes([0x1234]) == b"\x34\x12"


def test_int32_is_low_word_first() -> None:
    assert encode_int32(0x00010002) == b"\x02\x00\x01\x00"
    assert decode_int32(b"\x02\x00\x01\x00") == 0x00010002


@given(st.floats(width=32, allow_nan=False, allow_infinity=False))
def test_float32_round_trip_is_exact(value: float) -> None:
    assert decode_float32(encode_float32(value)) == value


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 1e39])
def test_float32_rejects_non_finite_and_overflow(bad: float) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        encode_float32(bad)


def test_int16_range() -> None:
    assert encode_int16(-1) == b"\xff\xff"
    with pytest.raises(ValueError, match="16-bit"):
        encode_int16(40000)


def test_f32_equal_compares_at_float32_precision() -> None:
    assert f32(0.1) != 0.1
    assert f32_equal(0.1, f32(0.1))
    assert f32_equal(123.456, struct.unpack("<f", struct.pack("<f", 123.456))[0])
    assert not f32_equal(1.0, 1.001)
    assert f32_equal(1.0, 1.001, tolerance=0.01)


# --- strings ---------------------------------------------------------------------------


def test_string_round_trip_and_padding() -> None:
    raw = encode_string("LD PB", 80)
    assert len(raw) == 80
    assert raw.startswith(b"LD PB\x00")
    assert decode_string(raw) == "LD PB"


def test_string_rules() -> None:
    with pytest.raises(ValueError, match="holds"):
        encode_string("x" * 81, 80)
    with pytest.raises(ValueError, match="ASCII"):
        encode_string("é", 80)
    assert decode_string(b"ABCD") == "ABCD"


# --- bits ---------------------------------------------------------------------------------


def test_align_bits() -> None:
    span = align_bits(9, 1)
    assert (span.aligned_start, span.aligned_count, span.n_bytes) == (8, 8, 1)
    span = align_bits(7, 2)
    assert (span.aligned_start, span.aligned_count) == (0, 16)


def test_pack_bits_matches_captured_single_bit_writes() -> None:
    assert pack_bits([True], 9) == b"\x02"
    assert pack_bits([True], 100) == b"\x10"
    assert pack_bits([True], 183) == b"\x80"
    assert pack_bits([False], 183) == b"\x00"


@given(
    st.integers(min_value=0, max_value=2000),
    st.lists(st.booleans(), min_size=1, max_size=100),
)
def test_pack_unpack_round_trip(start: int, values: list[bool]) -> None:
    span = align_bits(start, len(values))
    packed = pack_bits(values, start)
    # pack_bits starts at the byte containing ``start``; pad to the aligned span.
    padded = packed + bytes(span.n_bytes - len(packed))
    assert unpack_bits(padded, span) == values


# --- chunking ------------------------------------------------------------------------------


@given(
    st.integers(min_value=0, max_value=60000),
    st.integers(min_value=1, max_value=5000),
    st.integers(min_value=2, max_value=4096),
)
def test_word_chunks_cover_span_exactly(index: int, count: int, max_bytes: int) -> None:
    chunks = plan_word_chunks(index, count, max_bytes)
    expected = index
    for start, n in chunks:
        assert start == expected
        assert 1 <= n <= max_bytes // 2
        expected += n
    assert expected == index + count


@given(
    st.integers(min_value=0, max_value=60000),
    st.integers(min_value=1, max_value=5000),
    st.integers(min_value=1, max_value=512),
)
def test_bit_chunks_are_byte_aligned_and_cover(start: int, count: int, max_bytes: int) -> None:
    span = align_bits(start, count)
    chunks = plan_bit_chunks(span, max_bytes)
    expected = span.aligned_start
    for s, n in chunks:
        assert s == expected
        assert s % 8 == 0
        assert n % 8 == 0
        assert n <= max_bytes * 8
        expected += n
    assert expected == span.aligned_start + span.aligned_count


@given(
    st.integers(min_value=0, max_value=60000),
    st.integers(min_value=1, max_value=5000),
    st.integers(min_value=1, max_value=4096),
)
def test_byte_chunks_cover(index: int, count: int, max_bytes: int) -> None:
    chunks = plan_byte_chunks(index, count, max_bytes)
    assert sum(n for _, n in chunks) == count
    assert chunks[0][0] == index
