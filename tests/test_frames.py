"""Golden-bytes tests: frames this client builds vs frames captured from real traffic."""

from __future__ import annotations

import pytest

import golden
from fanuc_snpx.errors import SrtpServiceError
from fanuc_snpx.memory import Segment, pack_bits
from fanuc_snpx.srtp import (
    HEADER_LEN,
    MessageType,
    ServiceCode,
    SrtpSession,
    build_init,
    build_session_control,
    build_short_request,
    build_write_request,
    describe_request,
)
from helpers import ScriptedSocket, fast_limiter, scripted_factory


def mask_byte3(frame: bytes) -> bytes:
    b = bytearray(frame)
    b[3] = 0
    return bytes(b)


def test_init_is_56_zero_bytes() -> None:
    assert build_init() == bytes(56)


def test_session_control_matches_capture() -> None:
    assert build_session_control(1) == golden.CAPTURED_SESSION_CONTROL


def test_short_read_matches_capture() -> None:
    frame = build_short_request(0xAE, ServiceCode.READ_SYSTEM_MEMORY, Segment.AI, 10, 1)
    assert frame == golden.CAPTURED_READ_AI11


def test_word_read_40_matches_capture() -> None:
    frame = build_short_request(0x06, ServiceCode.READ_SYSTEM_MEMORY, Segment.R, 2042, 40)
    assert frame == golden.CAPTURED_READ_R2043_X40


def test_string_write_matches_capture() -> None:
    frame = build_write_request(0xC1, Segment.R, 0x2AF8, 2, b"adb\x00")
    assert frame == golden.CAPTURED_WRITE_STRING


def test_register_write_matches_capture_except_kepware_id_byte() -> None:
    frame = build_write_request(0xFD, Segment.R, 0, 1, (1234).to_bytes(2, "little"))
    assert frame == mask_byte3(golden.CAPTURED_WRITE_R1_1234)


@pytest.mark.parametrize(
    ("capture", "seq", "segment", "bit_index"),
    [
        (golden.CAPTURED_WRITE_DO10_ON, 0x5B, Segment.I_BIT, 9),
        (golden.CAPTURED_WRITE_DI101_ON, 0xCB, Segment.Q_BIT, 100),
    ],
)
def test_bit_write_matches_capture(
    capture: bytes, seq: int, segment: Segment, bit_index: int
) -> None:
    frame = build_write_request(seq, segment, bit_index, 1, pack_bits([True], bit_index))
    assert frame == mask_byte3(capture)


def test_frame_lengths() -> None:
    assert len(build_short_request(1, 0x04, 0x08, 0, 1)) == HEADER_LEN
    assert len(build_write_request(1, 0x08, 0, 3, bytes(6))) == HEADER_LEN + 6


def test_describe_request_decodes_fields() -> None:
    d = describe_request(golden.CAPTURED_WRITE_STRING)
    assert d["service"] == "0x07"
    assert d["segment"] == "0x08"
    assert d["index"] == 0x2AF8
    assert d["count"] == 2
    assert d["payload_len"] == 4


@pytest.mark.parametrize(
    "bad",
    [
        {"seq": 256},
        {"seq": -1},
        {"index": 70000},
        {"count": -1},
    ],
)
def test_builder_rejects_out_of_range(bad: dict[str, int]) -> None:
    args = {"seq": 1, "service": 4, "segment": 8, "index": 0, "count": 1} | bad
    with pytest.raises(ValueError):  # noqa: PT011
        build_short_request(**args)


# --- replaying captured replies through the real session code ---------------


def scripted_session(
    *replies: bytes, session_control: bool = False
) -> tuple[SrtpSession, ScriptedSocket]:
    sock = ScriptedSocket([golden.OBSERVED_INIT_REPLY, *replies])
    s = SrtpSession(
        "scripted",
        socket_factory=scripted_factory(sock),
        session_control=session_control,
        rate_limiter=fast_limiter(),
    )
    return s, sock


def test_handshake_with_session_control_capture() -> None:
    s, sock = scripted_session(golden.CAPTURED_SESSION_CONTROL_REPLY, session_control=True)
    s.open()
    assert sock.sent[0] == bytes(56)
    assert sock.sent[1] == golden.CAPTURED_SESSION_CONTROL
    assert s.is_open


def test_inline_reply_capture_decodes() -> None:
    s, sock = scripted_session(golden.CAPTURED_READ_AI11_REPLY)
    assert s.read_words(Segment.AI, 11, 1) == bytes.fromhex("30 02")
    assert sock.sent[1][42:48] == bytes.fromhex("04 0a 0a 00 01 00")


def test_extended_reply_capture_decodes() -> None:
    s, _ = scripted_session(golden.CAPTURED_READ_R2043_X40_REPLY)
    data = s.read_words(Segment.R, 2043, 40)
    assert data[:5] == b"LD PB"
    assert len(data) == 80


def test_error_reply_capture_raises_and_closes() -> None:
    s, sock = scripted_session(golden.CAPTURED_ERROR_REPLY)
    with pytest.raises(SrtpServiceError) as info:
        s.read_words(Segment.R, 1, 1)
    assert info.value.msg_type == MessageType.ERROR_REPLY
    assert (info.value.major, info.value.minor) == (0, 0)
    assert not s.is_open
    assert sock.closed
