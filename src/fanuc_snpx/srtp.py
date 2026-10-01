"""GE SRTP framing and a strict request/response session over TCP.

Frame layout (see docs/PROTOCOL.md for sources and verification status):

* Every frame starts with a 56-byte header. Bytes 4-5 give the number of bytes
  that follow it ("text length").
* A session starts with a 56-byte all-zero init packet; the controller answers
  56 bytes with byte 0 = 0x01.
* Requests use packet type 0x02 and message type 0xC0 (short, parameters in the
  header) or 0x80 (extended, data after the header). Replies use packet type
  0x03 and message type 0xD4 (short ack, up to 6 data bytes inline at 44..49),
  0x94 (extended ack, data after the header) or 0xD1 (error).

The session is deliberately simple: one request at a time, every reply matched
by its sequence number, exact-length reads, a deadline on every operation. Any
error closes the socket; the next request opens a brand-new session. The
session never retries a request by itself, so a write is never sent twice.
"""

from __future__ import annotations

import logging
import math
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .errors import (
    SrtpConnectionError,
    SrtpProtocolError,
    SrtpServiceError,
    SrtpTimeout,
    WriteNotAllowed,
)
from .evidence import EvidenceLog
from .memory import (
    Segment,
    Unit,
    align_bits,
    check_span,
    pack_bits,
    plan_bit_chunks,
    plan_byte_chunks,
    plan_word_chunks,
    srtp_index,
    unpack_bits,
)

log = logging.getLogger(__name__)

DEFAULT_PORT = 18245
HEADER_LEN = 56
INLINE_MAX = 6
MAX_TEXT_LEN = 65535
MAILBOX_DESTINATION = bytes((0x10, 0x0E, 0x00, 0x00))

# Header byte 30. Meaning unknown; these are the values a commercial driver sends
# to FANUC controllers on port 18245 (captures in Booozie-Z/Fanuc_GESRTP_Driver).
# The controller echoes the byte unchanged.
_TAG_SHORT = 0x06
_TAG_EXTENDED = 0x09
_TAG_SESSION = 0x01


class PacketType(IntEnum):
    """Header byte 0."""

    INIT_REQUEST = 0x00
    INIT_REPLY = 0x01
    REQUEST = 0x02
    RESPONSE = 0x03


class MessageType(IntEnum):
    """Header byte 31."""

    SHORT_REQUEST = 0xC0
    SHORT_REPLY = 0xD4
    ERROR_REPLY = 0xD1
    EXTENDED_REQUEST = 0x80
    EXTENDED_REPLY = 0x94


class ServiceCode(IntEnum):
    """Service request codes this package can send. Nothing else is implemented."""

    PLC_SHORT_STATUS = 0x00
    RETURN_PROGRAM_NAME = 0x03
    READ_SYSTEM_MEMORY = 0x04
    WRITE_SYSTEM_MEMORY = 0x07
    RETURN_FAULT_TABLE = 0x38
    RETURN_CONTROLLER_TYPE = 0x43
    SESSION_CONTROL = 0x4F


INFO_SERVICES = frozenset(
    {
        ServiceCode.PLC_SHORT_STATUS,
        ServiceCode.RETURN_PROGRAM_NAME,
        ServiceCode.RETURN_FAULT_TABLE,
        ServiceCode.RETURN_CONTROLLER_TYPE,
    }
)
"""Read-only information services that :meth:`SrtpSession.service` may send."""


# --- frame builders -----------------------------------------------------------


def _base_header(seq: int, msg_type: MessageType, tag: int, op: int, text_len: int) -> bytearray:
    if not 0 <= seq <= 0xFF:
        raise ValueError(f"sequence number {seq} outside 0..255")
    if not 0 <= text_len <= MAX_TEXT_LEN:
        raise ValueError(f"text length {text_len} outside 0..{MAX_TEXT_LEN}")
    h = bytearray(HEADER_LEN)
    h[0] = PacketType.REQUEST
    h[2] = seq
    h[4:6] = text_len.to_bytes(2, "little")
    h[9] = op
    h[17] = op
    h[30] = tag
    h[31] = msg_type
    h[36:40] = MAILBOX_DESTINATION
    h[40] = 1
    h[41] = 1
    return h


def build_init() -> bytes:
    """The 56-byte all-zero packet that opens an SRTP session."""
    return bytes(HEADER_LEN)


def build_short_request(
    seq: int, service: int, segment: int = 0, index: int = 0, count: int = 0
) -> bytes:
    """A 0xC0 request: service code, segment, 0-based index and count in the header."""
    for name, value, hi in (("service", service, 0xFF), ("segment", segment, 0xFF)):
        if not 0 <= value <= hi:
            raise ValueError(f"{name} {value} outside 0..{hi}")
    for name, value in (("index", index), ("count", count)):
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"{name} {value} outside 0..65535")
    h = _base_header(seq, MessageType.SHORT_REQUEST, _TAG_SHORT, 0x01, 0)
    h[42] = service
    h[43] = segment
    h[44:46] = index.to_bytes(2, "little")
    h[46:48] = count.to_bytes(2, "little")
    return bytes(h)


def build_session_control(seq: int) -> bytes:
    """The 0x4F packet that commercial drivers send right after the init packet."""
    h = bytearray(build_short_request(seq, ServiceCode.SESSION_CONTROL, 0x01))
    h[30] = _TAG_SESSION
    return bytes(h)


def build_write_request(seq: int, segment: int, index: int, count: int, payload: bytes) -> bytes:
    """A 0x80 extended request writing ``payload`` with service 0x07.

    Writes always use the extended form, like the captured commercial driver,
    even when the data would fit inline.
    """
    if not payload:
        raise ValueError("write payload is empty")
    if not 0 <= index <= 0xFFFF or not 1 <= count <= 0xFFFF:
        raise ValueError(f"invalid index/count {index}/{count}")
    h = _base_header(seq, MessageType.EXTENDED_REQUEST, _TAG_EXTENDED, 0x02, len(payload))
    h[42:44] = len(payload).to_bytes(2, "little")
    h[48] = 1
    h[49] = 1
    h[50] = ServiceCode.WRITE_SYSTEM_MEMORY
    h[51] = segment
    h[52:54] = index.to_bytes(2, "little")
    h[54:56] = count.to_bytes(2, "little")
    return bytes(h) + payload


# --- reply parsing ------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    """A complete reply: the 56-byte header plus the text that followed it."""

    header: bytes
    text: bytes

    @property
    def packet_type(self) -> int:
        return self.header[0]

    @property
    def seq(self) -> int:
        return int.from_bytes(self.header[2:4], "little")

    @property
    def text_len(self) -> int:
        return int.from_bytes(self.header[4:6], "little")

    @property
    def msg_type(self) -> int:
        return self.header[31]

    @property
    def major_status(self) -> int:
        return self.header[42]

    @property
    def minor_status(self) -> int:
        return self.header[43]

    @property
    def inline(self) -> bytes:
        """Header bytes 44..49, where short acks carry up to 6 data bytes."""
        return self.header[44:50]

    @property
    def status_word(self) -> int:
        """Header bytes 54-55 (little-endian). Believed to be the PLC status word."""
        return int.from_bytes(self.header[54:56], "little")

    def summary(self) -> dict[str, Any]:
        """Decoded fields for logs. Unverified fields are labelled as such."""
        h = self.header
        return {
            "packet_type": f"0x{h[0]:02X}",
            "seq": self.seq,
            "text_len": self.text_len,
            "tag30": f"0x{h[30]:02X}",
            "msg_type": f"0x{h[31]:02X}",
            "status": f"{h[42]:02X} {h[43]:02X}",
            "inline": h[44:50].hex(" "),
            "bytes48_55": h[48:56].hex(" "),
        }


def describe_request(frame: bytes) -> dict[str, Any]:
    """Decoded summary of a request frame built by this module (for evidence logs)."""
    h = frame[:HEADER_LEN]
    out: dict[str, Any] = {
        "packet_type": f"0x{h[0]:02X}",
        "seq": h[2],
        "msg_type": f"0x{h[31]:02X}",
    }
    if h[31] == MessageType.SHORT_REQUEST:
        out.update(
            service=f"0x{h[42]:02X}",
            segment=f"0x{h[43]:02X}",
            index=int.from_bytes(h[44:46], "little"),
            count=int.from_bytes(h[46:48], "little"),
        )
    elif h[31] == MessageType.EXTENDED_REQUEST:
        out.update(
            service=f"0x{h[50]:02X}",
            segment=f"0x{h[51]:02X}",
            index=int.from_bytes(h[52:54], "little"),
            count=int.from_bytes(h[54:56], "little"),
            payload_len=len(frame) - HEADER_LEN,
        )
    return out


# --- authorization token ------------------------------------------------------


@dataclass(frozen=True)
class WriteAuthorization:
    """Proof that a write was checked against the access policy.

    The session refuses every write that does not carry one. Instances are made
    by :meth:`fanuc_snpx.policy.AccessPolicy.authorize`; constructing one by hand
    defeats the purpose and is not supported.
    """

    kind: str
    target: str
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise WriteNotAllowed("every write needs a non-empty reason")


# --- rate limiting --------------------------------------------------------------


class RateLimiter:
    """At most ``max_per_second`` requests in any rolling one-second window.

    Waiting uses ``time.sleep``; there is no busy loop.
    """

    def __init__(
        self,
        max_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (math.isfinite(max_per_second) and max_per_second > 0):
            raise ValueError("max_per_second must be a positive number")
        self.max_per_second = max_per_second
        self._limit = max(1, int(max_per_second))
        self._window = 1.0 if max_per_second >= 1 else 1.0 / max_per_second
        self._stamps: deque[float] = deque()
        self._clock = clock
        self._sleep = sleep

    def acquire(self) -> None:
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= self._window:
            self._stamps.popleft()
        if len(self._stamps) >= self._limit:
            wait = self._window - (now - self._stamps[0])
            if wait > 0:
                self._sleep(wait)
            now = self._clock()
            self._stamps.popleft()
        self._stamps.append(now)


# --- session ------------------------------------------------------------------


SocketFactory = Callable[[tuple[str, int], float], socket.socket]


def _default_socket_factory(address: tuple[str, int], timeout: float) -> socket.socket:
    return socket.create_connection(address, timeout=timeout)


@dataclass
class SessionStats:
    sessions_opened: int = 0
    requests: int = 0
    errors: int = 0
    last_error: str | None = None
    handshake: dict[str, Any] = field(default_factory=dict)


class SrtpSession:
    """One SRTP conversation with a controller, re-opened on demand.

    Addresses passed to the ``read_*``/``write_*`` methods are 1-based PLC
    addresses (``%R1`` is ``address=1``). Counts are in the segment's unit:
    words for %R/%AI/%AQ, bits for bit segments, bytes for byte segments.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = 2.0,
        connect_timeout: float | None = None,
        session_control: bool = True,
        max_read_bytes: int = 1024,
        max_write_bytes: int = 1024,
        max_requests_per_second: float = 5.0,
        evidence: EvidenceLog | None = None,
        socket_factory: SocketFactory | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        if max_read_bytes < 2 or max_write_bytes < 1:
            raise ValueError("max_read_bytes must be >= 2 and max_write_bytes >= 1")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connect_timeout = connect_timeout if connect_timeout is not None else timeout
        self.session_control = session_control
        self.max_read_bytes = max_read_bytes
        self.max_write_bytes = max_write_bytes
        self.evidence = evidence
        self.stats = SessionStats()
        self._socket_factory = socket_factory or _default_socket_factory
        self._limiter = rate_limiter or RateLimiter(max_requests_per_second)
        self._sock: socket.socket | None = None
        self._seq = 0
        self._lock = threading.RLock()
        self._session_id = 0
        self._on_open: list[Callable[[SrtpSession], None]] = []
        self._opening = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def add_open_hook(self, hook: Callable[[SrtpSession], None]) -> None:
        """Run ``hook(session)`` after every new handshake (used to re-apply assignments)."""
        self._on_open.append(hook)

    def remove_open_hook(self, hook: Callable[[SrtpSession], None]) -> None:
        self._on_open.remove(hook)

    def open(self) -> None:
        """Open a new TCP connection and perform the handshake. Closes any old one first."""
        with self._lock:
            self.close()
            self._opening = True
            try:
                self._connect_and_handshake()
                for hook in list(self._on_open):
                    hook(self)
            except BaseException:
                self.close()
                raise
            finally:
                self._opening = False

    def close(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            if sock is not None:
                try:
                    sock.close()
                finally:
                    self._event("close", session=self._session_id)

    def __enter__(self) -> SrtpSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _connect_and_handshake(self) -> None:
        self._session_id += 1
        self._seq = 0
        self._event("connect", session=self._session_id, host=self.host, port=self.port)
        try:
            sock = self._socket_factory((self.host, self.port), self.connect_timeout)
        except TimeoutError as exc:
            raise SrtpTimeout(f"connect to {self.host}:{self.port} timed out") from exc
        except OSError as exc:
            raise SrtpConnectionError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            log.debug("TCP_NODELAY not supported by this socket")
        self._sock = sock
        self.stats.sessions_opened += 1

        deadline = time.monotonic() + self.timeout
        init = build_init()
        self._send(init)
        reply = self._recv_exact(HEADER_LEN, deadline)
        self._frame("rx", reply, kind="init_reply")
        if reply[0] != PacketType.INIT_REPLY:
            raise SrtpProtocolError(
                f"init reply byte 0 is 0x{reply[0]:02X}, expected 0x01", frame=reply
            )
        self.stats.handshake = {"init_reply": reply.hex(" ")}
        if self.session_control:
            seq = self._next_seq()
            r = self._exchange(build_session_control(seq), seq, ServiceCode.SESSION_CONTROL, None)
            if r.msg_type != MessageType.SHORT_REPLY:
                raise SrtpProtocolError(
                    f"session control answered with msg type 0x{r.msg_type:02X}",
                    frame=r.header,
                )
            self.stats.handshake["session_control_reply"] = r.header.hex(" ")

    def _ensure_open(self) -> None:
        if self._sock is None and not self._opening:
            self.open()

    # -- low-level I/O -------------------------------------------------------

    def _next_seq(self) -> int:
        # 0 is used by the init packet; data requests cycle 1..255.
        self._seq = self._seq % 255 + 1
        return self._seq

    def _socket(self) -> socket.socket:
        if self._sock is None:
            raise SrtpConnectionError("session is not open")
        return self._sock

    def _send(self, frame: bytes) -> None:
        sock = self._socket()
        self._frame("tx", frame, **describe_request(frame))
        try:
            sock.settimeout(self.timeout)
            sock.sendall(frame)
        except TimeoutError as exc:
            raise SrtpTimeout("timed out sending a request") from exc
        except OSError as exc:
            raise SrtpConnectionError(f"send failed: {exc}") from exc

    def _recv_exact(self, n: int, deadline: float) -> bytes:
        sock = self._socket()
        buf = bytearray()
        while len(buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SrtpTimeout(f"timed out after {len(buf)} of {n} bytes")
            try:
                sock.settimeout(remaining)
                chunk = sock.recv(n - len(buf))
            except TimeoutError as exc:
                raise SrtpTimeout(f"timed out after {len(buf)} of {n} bytes") from exc
            except OSError as exc:
                raise SrtpConnectionError(f"receive failed: {exc}") from exc
            if not chunk:
                raise SrtpConnectionError(
                    f"controller closed the connection after {len(buf)} of {n} bytes"
                )
            buf += chunk
        return bytes(buf)

    def _exchange(self, frame: bytes, seq: int, service: int, segment: int | None) -> Reply:
        """Send one request and return its validated reply. Raises on any problem."""
        self._socket()
        self._limiter.acquire()
        self.stats.requests += 1
        deadline = time.monotonic() + self.timeout
        self._send(frame)
        header = self._recv_exact(HEADER_LEN, deadline)
        # Validate what we can before trusting the length field, so a corrupt
        # header fails fast instead of waiting for up to 64 KiB that never come.
        if header[0] != PacketType.RESPONSE:
            self._frame("rx", header, **Reply(header, b"").summary())
            raise SrtpProtocolError(
                f"reply packet type 0x{header[0]:02X}, expected 0x03", frame=header
            )
        if header[2:4] != bytes((seq, 0)):
            self._frame("rx", header, **Reply(header, b"").summary())
            raise SrtpProtocolError(
                f"reply sequence {int.from_bytes(header[2:4], 'little')} does not match "
                f"request {seq} (stale or out of order)",
                frame=header,
            )
        text_len = int.from_bytes(header[4:6], "little")
        known = (MessageType.SHORT_REPLY, MessageType.EXTENDED_REPLY, MessageType.ERROR_REPLY)
        if header[31] not in known or (header[31] == MessageType.SHORT_REPLY and text_len):
            self._frame("rx", header, **Reply(header, b"").summary())
            raise SrtpProtocolError(
                f"unexpected reply msg type 0x{header[31]:02X} with {text_len} text bytes",
                frame=header,
            )
        text = self._recv_exact(text_len, deadline) if text_len else b""
        reply = Reply(header, text)
        self._frame("rx", header + text, **reply.summary())

        if reply.msg_type == MessageType.ERROR_REPLY:
            raise SrtpServiceError(
                f"controller rejected service 0x{service:02X}"
                + (f" segment 0x{segment:02X}" if segment is not None else "")
                + f": status {reply.major_status:02X} {reply.minor_status:02X}",
                major=reply.major_status,
                minor=reply.minor_status,
                msg_type=reply.msg_type,
                service=service,
                segment=segment,
                frame=header,
            )
        if reply.msg_type not in (MessageType.SHORT_REPLY, MessageType.EXTENDED_REPLY):
            raise SrtpProtocolError(f"unknown reply msg type 0x{reply.msg_type:02X}", frame=header)
        if reply.major_status or reply.minor_status:
            raise SrtpServiceError(
                f"reply to service 0x{service:02X} carries non-zero status "
                f"{reply.major_status:02X} {reply.minor_status:02X}",
                major=reply.major_status,
                minor=reply.minor_status,
                msg_type=reply.msg_type,
                service=service,
                segment=segment,
                frame=header,
            )
        if header[40] != 1 or header[41] != 1:
            raise SrtpProtocolError(
                f"multi-packet reply ({header[40]}/{header[41]}) is not supported; "
                "lower max_read_bytes",
                frame=header,
            )
        if reply.msg_type == MessageType.EXTENDED_REPLY and (header[48], header[49]) != (1, 1):
            raise SrtpProtocolError(
                f"multi-packet extended reply ({header[48]}/{header[49]}) is not supported",
                frame=header,
            )
        return reply

    def _request(
        self, frame_for_seq: Callable[[int], bytes], service: int, segment: int | None
    ) -> Reply:
        """Run one request on a healthy session; close the session on any failure."""
        with self._lock:
            self._ensure_open()
            seq = self._next_seq()
            try:
                return self._exchange(frame_for_seq(seq), seq, service, segment)
            except BaseException as exc:
                self.stats.errors += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                self._event("error", session=self._session_id, error=self.stats.last_error)
                self.close()
                raise

    # -- reads ---------------------------------------------------------------

    def _read_chunk(self, segment: Segment, index: int, count: int, n_bytes: int) -> bytes:
        reply = self._request(
            lambda seq: build_short_request(
                seq, ServiceCode.READ_SYSTEM_MEMORY, segment, index, count
            ),
            ServiceCode.READ_SYSTEM_MEMORY,
            segment,
        )
        if reply.msg_type == MessageType.SHORT_REPLY:
            if n_bytes > INLINE_MAX:
                self.close()
                raise SrtpProtocolError(
                    f"expected {n_bytes} bytes but got a short (inline) reply", frame=reply.header
                )
            if reply.text:
                self.close()
                raise SrtpProtocolError("short reply carried trailing text", frame=reply.header)
            return reply.inline[:n_bytes]
        if len(reply.text) != n_bytes:
            self.close()
            raise SrtpProtocolError(
                f"expected {n_bytes} data bytes, got {len(reply.text)}", frame=reply.header
            )
        return reply.text

    def read_words(self, segment: Segment, address: int, count: int) -> bytes:
        """Read ``count`` words from a word segment starting at 1-based ``address``."""
        if segment.unit is not Unit.WORD:
            raise ValueError(f"{segment.plc_name} is not a word segment")
        check_span(address, count)
        out = bytearray()
        with self._lock:
            for index, n in plan_word_chunks(srtp_index(address), count, self.max_read_bytes):
                out += self._read_chunk(segment, index, n, 2 * n)
        return bytes(out)

    def read_bits(self, segment: Segment, address: int, count: int) -> list[bool]:
        """Read ``count`` bits from a bit segment starting at 1-based ``address``."""
        if segment.unit is not Unit.BIT:
            raise ValueError(f"{segment.plc_name} bit access needs a bit segment")
        check_span(address, count)
        span = align_bits(srtp_index(address), count)
        data = bytearray()
        with self._lock:
            for index, n_bits in plan_bit_chunks(span, self.max_read_bytes):
                data += self._read_chunk(segment, index, n_bits, n_bits // 8)
        return unpack_bits(bytes(data), span)

    def read_bytes(self, segment: Segment, address: int, count: int) -> bytes:
        """Read ``count`` bytes from a byte segment starting at 1-based byte ``address``."""
        if segment.unit is not Unit.BYTE:
            raise ValueError(f"{segment.plc_name} is not a byte segment")
        check_span(address, count)
        out = bytearray()
        with self._lock:
            for index, n in plan_byte_chunks(srtp_index(address), count, self.max_read_bytes):
                out += self._read_chunk(segment, index, n, n)
        return bytes(out)

    def service(self, service: ServiceCode) -> Reply:
        """Send a read-only information service (status, controller type, ...)."""
        if service not in INFO_SERVICES:
            raise ValueError(f"service 0x{int(service):02X} is not a read-only information service")
        return self._request(lambda seq: build_short_request(seq, service), service, None)

    # -- writes --------------------------------------------------------------

    def _write(
        self, segment: Segment, index: int, count: int, payload: bytes, auth: WriteAuthorization
    ) -> None:
        if not isinstance(auth, WriteAuthorization):
            raise WriteNotAllowed("writes need a WriteAuthorization from the access policy")
        if len(payload) > self.max_write_bytes:
            raise ValueError(
                f"write of {len(payload)} bytes exceeds max_write_bytes={self.max_write_bytes}; "
                "large writes are refused rather than split, because a split write is not atomic"
            )
        self._event(
            "write",
            kind=auth.kind,
            target=auth.target,
            reason=auth.reason,
            segment=f"0x{int(segment):02X}",
            index=index,
            count=count,
        )
        reply = self._request(
            lambda seq: build_write_request(seq, segment, index, count, payload),
            ServiceCode.WRITE_SYSTEM_MEMORY,
            segment,
        )
        if reply.msg_type != MessageType.SHORT_REPLY or reply.text:
            self.close()
            raise SrtpProtocolError("unexpected reply to a write", frame=reply.header)

    def write_words(
        self, segment: Segment, address: int, data: bytes, *, authorization: WriteAuthorization
    ) -> None:
        """Write whole words (``data`` length must be even) at 1-based ``address``."""
        if segment.unit is not Unit.WORD:
            raise ValueError(f"{segment.plc_name} is not a word segment")
        if not data or len(data) % 2:
            raise ValueError("word data must be a non-empty even number of bytes")
        check_span(address, len(data) // 2)
        self._write(segment, srtp_index(address), len(data) // 2, data, authorization)

    def write_bits(
        self,
        segment: Segment,
        address: int,
        values: Sequence[bool],
        *,
        authorization: WriteAuthorization,
    ) -> None:
        """Write ``values`` to consecutive bits starting at 1-based ``address``."""
        if segment.unit is not Unit.BIT:
            raise ValueError(f"{segment.plc_name} bit access needs a bit segment")
        check_span(address, len(values))
        index = srtp_index(address)
        self._write(segment, index, len(values), pack_bits(values, index), authorization)

    def write_bytes(
        self, segment: Segment, address: int, data: bytes, *, authorization: WriteAuthorization
    ) -> None:
        """Write raw bytes to a byte segment (used for %G command strings)."""
        if segment.unit is not Unit.BYTE:
            raise ValueError(f"{segment.plc_name} is not a byte segment")
        check_span(address, len(data))
        self._write(segment, srtp_index(address), len(data), data, authorization)

    # -- evidence ------------------------------------------------------------

    def _frame(self, direction: str, data: bytes, **fields: Any) -> None:
        if self.evidence is not None:
            self.evidence.frame(direction, data, session=self._session_id, **fields)

    def _event(self, event: str, **fields: Any) -> None:
        if self.evidence is not None:
            self.evidence.record(event, **fields)
