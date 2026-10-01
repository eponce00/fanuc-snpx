"""SrtpSession against the fake server: normal traffic, chunking and every injected fault."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fanuc_snpx.errors import (
    SrtpConnectionError,
    SrtpProtocolError,
    SrtpServiceError,
    SrtpTimeout,
    WriteNotAllowed,
)
from fanuc_snpx.memory import Segment
from fanuc_snpx.srtp import RateLimiter, ServiceCode, SrtpSession, WriteAuthorization
from fanuc_snpx.testing import FakeSrtpServer, Fault

AUTH = WriteAuthorization(kind="test", target="test", reason="unit test")
Factory = Callable[..., SrtpSession]


def put_words(server: FakeSrtpServer, segment: int, address: int, data: bytes) -> None:
    with server.state() as ctl:
        ctl.words[segment][2 * (address - 1) : 2 * (address - 1) + len(data)] = data


def test_handshake_sends_init_then_session_control(
    server: FakeSrtpServer, make_session: Factory
) -> None:
    s = make_session()
    s.open()
    conn = server.connections[-1]
    assert conn.frames[0] == bytes(56)
    assert conn.frames[1][42] == ServiceCode.SESSION_CONTROL
    assert conn.privilege == 4


def test_handshake_without_session_control(server: FakeSrtpServer, make_session: Factory) -> None:
    s = make_session(session_control=False)
    s.open()
    assert len(server.connections[-1].frames) == 1


def test_lazy_open_on_first_request(server: FakeSrtpServer, make_session: Factory) -> None:
    put_words(server, Segment.R, 1, b"\x01\x00\x02\x00")
    s = make_session()
    assert not s.is_open
    assert s.read_words(Segment.R, 1, 2) == b"\x01\x00\x02\x00"
    assert s.is_open


def test_inline_and_extended_reads(server: FakeSrtpServer, make_session: Factory) -> None:
    data = bytes(range(200))
    put_words(server, Segment.AI, 1, data)
    s = make_session()
    assert s.read_words(Segment.AI, 1, 3) == data[:6]  # inline (D4)
    assert s.read_words(Segment.AI, 1, 100) == data  # extended (0x94)


def test_word_read_is_chunked(server: FakeSrtpServer, make_session: Factory) -> None:
    data = bytes(i % 251 for i in range(6000))
    put_words(server, Segment.R, 100, data)
    s = make_session(max_read_bytes=1024)
    s.open()
    before = s.stats.requests
    assert s.read_words(Segment.R, 100, 3000) == data
    assert s.stats.requests - before == 6  # ceil(6000 / 1024)


def test_bit_read_and_write(server: FakeSrtpServer, make_session: Factory) -> None:
    s = make_session()
    s.write_bits(Segment.I_BIT, 10, [True, False, True], authorization=AUTH)
    assert s.read_bits(Segment.I_BIT, 9, 5) == [False, True, False, True, False]
    with server.state() as ctl:
        assert ctl.bits[Segment.I_BIT][1] == 0b0000_0101 << 1


def test_word_write(server: FakeSrtpServer, make_session: Factory) -> None:
    s = make_session()
    s.write_words(Segment.AQ, 5, b"\x34\x12", authorization=AUTH)
    assert s.read_words(Segment.AQ, 5, 1) == b"\x34\x12"


def test_writes_require_authorization(make_session: Factory) -> None:
    s = make_session()
    with pytest.raises(WriteNotAllowed):
        s.write_words(Segment.R, 1, b"\x00\x00", authorization=None)  # type: ignore[arg-type]
    with pytest.raises(WriteNotAllowed):
        WriteAuthorization(kind="x", target="y", reason="  ")


def test_large_write_is_refused_not_split(make_session: Factory) -> None:
    s = make_session(max_write_bytes=100)
    with pytest.raises(ValueError, match="not atomic"):
        s.write_words(Segment.R, 1, bytes(102), authorization=AUTH)


def test_info_services_only(make_session: Factory) -> None:
    s = make_session()
    reply = s.service(ServiceCode.RETURN_CONTROLLER_TYPE)
    assert b"FAKE-SNPX" in reply.text
    with pytest.raises(ValueError, match="read-only"):
        s.service(ServiceCode.WRITE_SYSTEM_MEMORY)


# --- faults: every error closes the session; the next request opens a new one ---------


def test_error_reply_raises_typed_error_and_reconnects(
    server: FakeSrtpServer, make_session: Factory
) -> None:
    s = make_session()
    s.read_words(Segment.R, 1, 1)
    server.inject(Fault("error_reply", major=0x05, minor=0x02))
    with pytest.raises(SrtpServiceError) as info:
        s.read_words(Segment.R, 1, 1)
    assert (info.value.major, info.value.minor) == (0x05, 0x02)
    assert not s.is_open
    n = len(server.connections)
    s.read_words(Segment.R, 1, 1)
    assert len(server.connections) == n + 1


def test_desync_after_failed_batch_is_cured_by_new_session(make_session: Factory) -> None:
    # Reproduces the behaviour seen with another library: after one failed block
    # read, every request on that connection fails; a fresh connection works.
    with FakeSrtpServer(desync_after_error=True) as srv:
        s = SrtpSession(srv.host, srv.port, timeout=1.0, rate_limiter=RateLimiter(10_000))
        try:
            srv.inject(Fault("error_reply"))
            with pytest.raises(SrtpServiceError):
                s.read_words(Segment.R, 1, 200)
            assert not s.is_open
            assert s.read_words(Segment.R, 1, 1) == b"\x00\x00"
            assert len(srv.connections) == 2
            assert srv.connections[0].desynced
        finally:
            s.close()


def test_timeout_closes_session(server: FakeSrtpServer, make_session: Factory) -> None:
    s = make_session(timeout=0.2)
    s.open()
    server.inject(Fault("delay", seconds=0.5))
    with pytest.raises(SrtpTimeout):
        s.read_words(Segment.R, 1, 1)
    assert not s.is_open


def test_split_reply_is_reassembled(server: FakeSrtpServer, make_session: Factory) -> None:
    put_words(server, Segment.R, 1, bytes(range(40)))
    s = make_session()
    server.inject(Fault("split", chunk=3))
    assert s.read_words(Segment.R, 1, 20) == bytes(range(40))
    assert s.is_open


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("stale_seq", SrtpProtocolError),
        ("wrong_packet_type", SrtpProtocolError),
        ("garbage", SrtpProtocolError),
        ("multi_packet", SrtpProtocolError),
        ("drop", SrtpConnectionError),
        ("short_text", SrtpConnectionError),
    ],
)
def test_malformed_replies_close_session(
    server: FakeSrtpServer, make_session: Factory, kind: str, error: type[Exception]
) -> None:
    s = make_session()
    s.open()
    server.inject(Fault(kind))  # type: ignore[arg-type]
    with pytest.raises(error):
        s.read_words(Segment.R, 1, 10)
    assert not s.is_open
    assert s.stats.errors == 1


def test_failed_chunk_never_returns_partial_data(
    server: FakeSrtpServer, make_session: Factory
) -> None:
    s = make_session(max_read_bytes=64)
    s.open()
    server.inject(Fault("split", chunk=16), Fault("error_reply"))
    with pytest.raises(SrtpServiceError):
        s.read_words(Segment.R, 1, 100)
    assert not s.is_open


def test_connect_refused() -> None:
    srv = FakeSrtpServer().start()
    port = srv.port
    srv.stop()
    s = SrtpSession("127.0.0.1", port, timeout=0.5, rate_limiter=RateLimiter(10_000))
    # Linux refuses at once; Windows retries the SYN and may hit the timeout first.
    with pytest.raises((SrtpConnectionError, SrtpTimeout)):
        s.open()
    assert not s.is_open


def test_sequence_numbers_increment_and_wrap(server: FakeSrtpServer, make_session: Factory) -> None:
    s = make_session(session_control=False)
    for _ in range(260):
        s.read_words(Segment.R, 1, 1)
    seqs = [f[2] for f in server.connections[-1].frames[1:]]
    assert seqs[:3] == [1, 2, 3]
    assert seqs[254] == 255
    assert seqs[255] == 1
    assert all(f[3] == 0 for f in server.connections[-1].frames[1:])


def test_open_hooks_run_on_every_new_session(server: FakeSrtpServer, make_session: Factory) -> None:
    s = make_session()
    calls: list[int] = []
    s.add_open_hook(lambda sess: calls.append(sess.stats.sessions_opened))
    s.open()
    server.inject(Fault("error_reply"))
    with pytest.raises(SrtpServiceError):
        s.read_words(Segment.R, 1, 1)
    s.read_words(Segment.R, 1, 1)
    assert calls == [1, 2]


def test_evidence_log_records_frames(
    server: FakeSrtpServer, make_session: Factory, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    import json

    from fanuc_snpx.evidence import EvidenceLog

    path = tmp_path / "ev.jsonl"
    with EvidenceLog(path) as ev:
        s = make_session(evidence=ev)
        s.read_words(Segment.R, 1, 2)
        s.close()
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    frames = [x for x in lines if x["event"] == "frame"]
    assert frames[0]["direction"] == "tx"
    assert frames[0]["hex"] == " ".join(["00"] * 56)
    assert any(x.get("service") == "0x04" for x in frames)
    assert lines[-1]["event"] == "close"


# --- rate limiter ------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_allows_n_per_rolling_second() -> None:
    clock = FakeClock()
    rl = RateLimiter(5, clock=clock.time, sleep=clock.sleep)
    for _ in range(5):
        rl.acquire()
    assert clock.sleeps == []
    rl.acquire()
    assert clock.sleeps == [pytest.approx(1.0)]
    # 10 requests take at least one second of waiting, 15 at least two.
    for _ in range(9):
        rl.acquire()
    assert clock.now >= 2.0


def test_rate_limiter_rejects_unbounded() -> None:
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(0)
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(float("nan"))
