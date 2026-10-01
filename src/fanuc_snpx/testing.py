"""A fake SRTP/SNPX server for tests. Not for production use.

:class:`FakeSrtpServer` listens on 127.0.0.1, speaks the frame format described
in docs/PROTOCOL.md, and serves a :class:`FakeController`: a flat PLC memory
image plus a small model of the FANUC side (numeric, position and string
registers, system variables, current position and the ``$SNPX_ASG`` table).

The model encodes data with its own code, independent of the client, so client
decoding is tested against a second implementation. Both implement the same
*hypothesis* about the controller; the real robot is the authority.

Fault injection: queue :class:`Fault` objects with :meth:`FakeSrtpServer.inject`;
each one applies to one future request.
"""

from __future__ import annotations

import re
import socket
import socketserver
import struct
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Literal

HEADER = 56

FaultKind = Literal[
    "error_reply",
    "delay",
    "split",
    "stale_seq",
    "wrong_packet_type",
    "drop",
    "garbage",
    "multi_packet",
    "short_text",
]


@dataclass(frozen=True)
class Fault:
    """One injected misbehaviour, applied to the next matching request.

    * ``error_reply``: answer 0xD1 with ``major``/``minor``.
    * ``delay``: wait ``seconds`` before answering.
    * ``split``: send the reply ``chunk`` bytes at a time.
    * ``stale_seq``: echo the previous sequence number.
    * ``wrong_packet_type``: reply byte 0 = 0x05.
    * ``drop``: close the connection without answering.
    * ``garbage``: answer 56 bytes of 0xEE.
    * ``multi_packet``: mark the reply as packet 1 of 2.
    * ``short_text``: announce more text bytes than are sent, then close.
    """

    kind: FaultKind
    major: int = 0x05
    minor: int = 0x00
    seconds: float = 0.0
    chunk: int = 1


# --- controller model -----------------------------------------------------------

POSITION_BYTES = 100


def position_image(
    *,
    xyzwpr: tuple[float, float, float, float, float, float] | None = None,
    ext: tuple[float, float, float] = (0.0, 0.0, 0.0),
    config: tuple[int, int, int, int] = (0, 0, 1, 1),
    turns: tuple[int, int, int] = (0, 0, 0),
    joints: tuple[float, ...] | None = None,
    uf: int = 15,
    ut: int = 15,
) -> bytes:
    """Build the 50-word (100-byte) SNPX position structure used by the fake.

    Word map (1-based): 1-18 X..E3, 19-22 FLIP/LEFT/UP/FRONT, 23-25 TURN4-6,
    26 VALIDC, 27-44 J1..J9, 45 VALIDJ, 46 UF, 47 UT, 48-50 reserved.
    """
    b = bytearray(POSITION_BYTES)
    if xyzwpr is not None:
        struct.pack_into("<9f", b, 0, *xyzwpr, *ext)
        struct.pack_into("<4h", b, 36, *config)
        struct.pack_into("<3h", b, 44, *turns)
        struct.pack_into("<h", b, 50, 1)
    if joints is not None:
        j = tuple(joints) + (0.0,) * (9 - len(joints))
        struct.pack_into("<9f", b, 52, *j)
        struct.pack_into("<h", b, 88, 1)
    struct.pack_into("<hh", b, 90, uf, ut)
    return bytes(b)


@dataclass
class FakeAssignment:
    address: int
    size: int
    var_name: str
    multiply: float = 1.0


_VAR_RE = re.compile(
    r"^(?P<base>[A-Z]+)"
    r"(?:\[(?P<sub>[A-Z]?)(?:G(?P<group>\d+):)?(?P<index>\d+)\])?"
    r"(?:@(?P<off>\d+)\.(?P<len>\d+))?$"
)
_SLICE_RE = re.compile(r"@(?P<off>\d+)\.(?P<len>\d+)$")


@dataclass
class FakeController:
    """State behind a :class:`FakeSrtpServer`. Access it only through the server lock."""

    words: dict[int, bytearray] = field(default_factory=dict)
    bits: dict[int, bytearray] = field(default_factory=dict)
    numeric_registers: dict[int, int | float] = field(default_factory=dict)
    position_registers: dict[int, bytes] = field(default_factory=dict)
    string_registers: dict[int, str] = field(default_factory=dict)
    comments: dict[tuple[str, int], str] = field(default_factory=dict)
    sysvars: dict[str, int | float | str | bytes] = field(default_factory=dict)
    current_position: dict[tuple[int, int], bytes] = field(default_factory=dict)
    table: list[FakeAssignment | None] = field(default_factory=lambda: [None] * 80)
    multiplex: bool = True
    commands: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for seg in (0x08, 0x0A, 0x0C):
            self.words.setdefault(seg, bytearray(2 * 65536))
        for seg in (0x46, 0x48, 0x4A, 0x4C, 0x4E, 0x50, 0x52, 0x54, 0x56):
            self.bits.setdefault(seg, bytearray(8192))

    # -- assignment table ---------------------------------------------------

    def set_default_table(self) -> None:
        """Factory default: %R1..%R10000 -> R[1]@1.1, multiply 1 (16-bit integers)."""
        self.table = [None] * 80
        self.table[0] = FakeAssignment(1, 10000, "R[1]@1.1", 1.0)

    def assign(self, slot: int, entry: FakeAssignment) -> None:
        self.table[slot - 1] = entry

    @staticmethod
    def _parse(var: str) -> tuple[str, str, int, int, int | None, int | None]:
        if var.startswith("$"):
            s = _SLICE_RE.search(var)
            name = var[: s.start()] if s else var
            return (
                name.upper(),
                "",
                1,
                0,
                int(s["off"]) if s else None,
                int(s["len"]) if s else None,
            )
        m = _VAR_RE.match(var.upper())
        if m is None:
            raise ValueError(f"fake cannot parse var name {var!r}")
        base = m["base"]
        sub = m["sub"] or ""
        group = int(m["group"]) if m["group"] else 1
        index = int(m["index"]) if m["index"] else 0
        off = int(m["off"]) if m["off"] else None
        length = int(m["len"]) if m["len"] else None
        return base, sub, group, index, off, length

    def _element(self, var: str, element: int, mult: float) -> bytes:
        """Full structure bytes of element ``element`` (0 = first) of assignment ``var``."""
        base, sub, group, index, _off, _len = self._parse(var)
        if base.startswith("$"):
            value = self.sysvars.get(_nth_array_element(base, element), 0)
            if isinstance(value, bytes):
                return _scale_position(value, mult)
            if isinstance(value, str):
                return value.encode("ascii")[:80].ljust(80, b"\x00")
            return _scale_scalar(value, mult)
        n = index + element
        if sub == "C":
            return self.comments.get((base, n), "").encode("ascii")[:80].ljust(80, b"\x00")
        if base == "R" and sub == "":
            return _scale_scalar(self.numeric_registers.get(n, 0), mult)
        if base == "PR" and sub == "":
            return _scale_position(self.position_registers.get(n, bytes(POSITION_BYTES)), mult)
        if base == "SR":
            return self.string_registers.get(n, "").encode("ascii")[:80].ljust(80, b"\x00")
        if base == "POS":
            if element:
                return bytes(POSITION_BYTES)  # POS[] cannot be assigned consecutively
            img = self.current_position.get((group, index), bytes(POSITION_BYTES))
            return _scale_position(img, mult)
        raise ValueError(f"fake does not model {var!r}")

    @staticmethod
    def _element_words(var: str, sysvars: dict[str, int | float | str | bytes]) -> int:
        base, sub = FakeController._parse(var)[:2]
        if sub == "C":
            return 40
        if base.startswith("$"):
            value = sysvars.get(base, 0)
            if isinstance(value, bytes):
                return 50
            return 40 if isinstance(value, str) else 2
        return {"R": 2, "PR": 50, "SR": 40, "POS": 50}.get(base, 2)

    def _table_for(self, local: list[FakeAssignment | None] | None) -> list[FakeAssignment | None]:
        return local if local is not None else self.table

    def _resolve(
        self, word: int, local: list[FakeAssignment | None] | None
    ) -> tuple[FakeAssignment, int, int, int, int] | None:
        """Locate %R ``word``.

        Returns (assignment, element, word in slice, slice start, words per element).
        """
        for entry in self._table_for(local):
            if entry is None or not entry.address <= word < entry.address + entry.size:
                continue
            _b, _s, _g, _i, off, length = self._parse(entry.var_name)
            full = self._element_words(entry.var_name, self.sysvars)
            start = (off or 1) - 1
            per = length if length is not None else full
            rel = word - entry.address
            return entry, rel // per, rel % per, start, per
        return None

    def read_r(self, address: int, count: int, local: list[FakeAssignment | None] | None) -> bytes:
        out = bytearray()
        for w in range(address, address + count):
            hit = self._resolve(w, local)
            if hit is None:
                out += self.words[0x08][2 * (w - 1) : 2 * w]
                continue
            entry, element, word_in_slice, start, _per = hit
            img = self._element(entry.var_name, element, entry.multiply)
            pos = 2 * (start + word_in_slice)
            out += img[pos : pos + 2].ljust(2, b"\x00")
        return bytes(out)

    def write_r(self, address: int, data: bytes, local: list[FakeAssignment | None] | None) -> None:
        for k in range(len(data) // 2):
            w = address + k
            word = data[2 * k : 2 * k + 2]
            hit = self._resolve(w, local)
            if hit is None:
                self.words[0x08][2 * (w - 1) : 2 * w] = word
                continue
            entry, element, word_in_slice, start, _per = hit
            img = bytearray(self._element(entry.var_name, element, entry.multiply))
            pos = 2 * (start + word_in_slice)
            img[pos : pos + 2] = word
            self._store(entry, element, bytes(img))

    def _store(self, entry: FakeAssignment, element: int, img: bytes) -> None:
        base, sub, _group, index, _off, length = self._parse(entry.var_name)
        n = index + element
        if base.startswith("$"):
            name = _nth_array_element(base, element)
            old = self.sysvars.get(name, 0)
            if isinstance(old, bytes):
                self.sysvars[name] = _unscale_position(img, entry.multiply)
            elif isinstance(old, str):
                self.sysvars[name] = img.split(b"\x00")[0].decode("ascii")
            else:
                self.sysvars[name] = _unscale_scalar(img, entry.multiply, isinstance(old, float))
        elif sub == "C":
            self.comments[(base, n)] = img.split(b"\x00")[0].decode("ascii")
        elif base == "R" and sub == "":
            if length == 1:
                self.numeric_registers[n] = struct.unpack_from("<h", img, 0)[0]
            else:
                self.numeric_registers[n] = _unscale_scalar(
                    img, entry.multiply, entry.multiply != 1
                )
        elif base == "PR" and sub == "":
            self.position_registers[n] = _unscale_position(img, entry.multiply)
        elif base == "SR":
            self.string_registers[n] = img.split(b"\x00")[0].decode("ascii")
        # POS[] is read-only on the controller: writes are ignored.

    # -- %G commands ----------------------------------------------------------

    def command(self, text: str, conn: _ConnState) -> None:
        for raw in re.split(r"[\r\n]+", text):
            cmd = raw.strip("\x00 ").strip()
            if not cmd:
                continue
            self.commands.append(cmd)
            parts = cmd.split()
            verb = parts[0].upper()
            if verb == "CLRASG":
                if self.multiplex:
                    conn.local_table = [None] * 80
                else:
                    self.table = [None] * 80
            elif verb == "SETASG":
                mult = float(parts[4]) if len(parts) > 4 else 1.0
                entry = FakeAssignment(int(parts[1]), int(parts[2]), parts[3], mult)
                table = self._table_for(conn.local_table)
                slot = table.index(None)
                table[slot] = entry
            elif verb == "SETVAR":
                old = self.sysvars.get(parts[1].upper(), 0)
                value = parts[2].strip('"')
                if isinstance(old, float):
                    self.sysvars[parts[1].upper()] = float(value)
                elif isinstance(old, int):
                    self.sysvars[parts[1].upper()] = int(value)
                else:
                    self.sysvars[parts[1].upper()] = value


def _nth_array_element(name: str, element: int) -> str:
    """``$A[1,2]`` with element 1 -> ``$A[1,3]`` (consecutive assignment of array elements)."""
    if element == 0:
        return name
    m = re.search(r"(\d+)\]$", name)
    if m is None:
        raise ValueError(f"fake cannot assign consecutive elements of non-array {name!r}")
    return f"{name[: m.start(1)]}{int(m[1]) + element}]"


def _scale_scalar(value: int | float, mult: float) -> bytes:
    if mult == 0:
        return struct.pack("<f", float(value))
    return struct.pack("<i", round(value * mult))


def _unscale_scalar(img: bytes, mult: float, as_float: bool) -> int | float:
    if mult == 0:
        v = struct.unpack_from("<f", img, 0)[0]
        return float(v) if as_float else round(v)
    raw = int(struct.unpack_from("<i", img, 0)[0])
    return raw / mult if as_float else round(raw / mult)


def _scale_position(img: bytes, mult: float) -> bytes:
    if mult == 0:
        return img
    b = bytearray(img)
    for off in (*range(0, 36, 4), *range(52, 88, 4)):
        struct.pack_into("<i", b, off, round(struct.unpack_from("<f", img, off)[0] * mult))
    return bytes(b)


def _unscale_position(img: bytes, mult: float) -> bytes:
    if mult == 0:
        return img
    b = bytearray(img)
    for off in (*range(0, 36, 4), *range(52, 88, 4)):
        struct.pack_into("<f", b, off, struct.unpack_from("<i", img, off)[0] / mult)
    return bytes(b)


# --- server -------------------------------------------------------------------------


@dataclass
class _ConnState:
    conn_id: int
    privilege: int = 2
    local_table: list[FakeAssignment | None] | None = None
    desynced: bool = False
    frames: list[bytes] = field(default_factory=list)


class FakeSrtpServer:
    """Threaded fake controller on ``127.0.0.1:<ephemeral port>``.

    Use as a context manager. ``controller`` is the shared state; hold ``lock``
    while touching it from a test.
    """

    def __init__(
        self,
        controller: FakeController | None = None,
        *,
        desync_after_error: bool = False,
        controller_type: bytes = b"FAKE-SNPX",
    ) -> None:
        self.controller = controller or FakeController()
        self.lock = threading.RLock()
        self.desync_after_error = desync_after_error
        self.controller_type = controller_type
        self.faults: deque[Fault] = deque()
        self.connections: list[_ConnState] = []
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> FakeSrtpServer:
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                outer._serve(self.request)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> FakeSrtpServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("server is not running")
        return int(self._server.server_address[1])

    def inject(self, *faults: Fault) -> None:
        with self.lock:
            self.faults.extend(faults)

    @contextmanager
    def state(self) -> Iterator[FakeController]:
        with self.lock:
            yield self.controller

    # -- protocol ----------------------------------------------------------------

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _serve(self, sock: socket.socket) -> None:
        with self.lock:
            conn = _ConnState(conn_id=len(self.connections) + 1)
            self.connections.append(conn)
        init = self._recv_exact(sock, HEADER)
        if init is None:
            return
        conn.frames.append(init)
        reply = bytearray(HEADER)
        reply[0] = 0x01
        reply[8] = 0x01
        sock.sendall(bytes(reply))
        while True:
            header = self._recv_exact(sock, HEADER)
            if header is None:
                return
            text_len = int.from_bytes(header[4:6], "little")
            text = self._recv_exact(sock, text_len) if text_len else b""
            if text is None:
                return
            conn.frames.append(header + text)
            with self.lock:
                fault = self.faults.popleft() if self.faults else None
                if conn.desynced:
                    out = self._reply(header, 0xD1, major=0x05, minor=0x10, conn=conn)
                else:
                    out = self._handle(header, text, conn)
                if fault is not None and fault.kind == "error_reply":
                    out = self._reply(header, 0xD1, major=fault.major, minor=fault.minor, conn=conn)
                if out[31] == 0xD1 and self.desync_after_error:
                    conn.desynced = True
            if not self._send(sock, out, fault):
                return

    def _send(self, sock: socket.socket, out: bytes, fault: Fault | None) -> bool:
        if fault is None or fault.kind == "error_reply":
            sock.sendall(out)
            return True
        b = bytearray(out)
        if fault.kind == "delay":
            time.sleep(fault.seconds)
        elif fault.kind == "stale_seq":
            b[2] = (b[2] - 1) % 256
        elif fault.kind == "wrong_packet_type":
            b[0] = 0x05
        elif fault.kind == "garbage":
            b = bytearray(b"\xee" * HEADER)
        elif fault.kind == "multi_packet":
            b[41] = 2
        elif fault.kind == "drop":
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            return False
        elif fault.kind == "short_text":
            b[4:6] = (int.from_bytes(b[4:6], "little") + 10).to_bytes(2, "little")
            sock.sendall(bytes(b))
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            return False
        if fault.kind == "split":
            for i in range(0, len(b), max(1, fault.chunk)):
                sock.sendall(bytes(b[i : i + fault.chunk]))
                time.sleep(0.001)
            return True
        sock.sendall(bytes(b))
        return True

    def _reply(
        self,
        req: bytes,
        msg_type: int,
        *,
        conn: _ConnState,
        inline: bytes = b"",
        text: bytes = b"",
        major: int = 0,
        minor: int = 0,
    ) -> bytes:
        h = bytearray(HEADER)
        h[0] = 0x03
        h[2:4] = req[2:4]
        h[4:6] = len(text).to_bytes(2, "little")
        h[9] = 0x01
        h[17] = 0x01
        h[30] = req[30]
        h[31] = msg_type
        h[32:36] = b"\x10\x0e\x00\x00"
        h[36:40] = b"\x30\x3a\x00\x00"
        h[40] = 1
        h[41] = 1
        h[42] = major
        h[43] = minor
        if inline:
            h[44 : 44 + len(inline)] = inline
        else:
            h[48] = 1
            h[49] = 1
            h[50] = 0xFF
            h[51] = conn.privilege
        h[54:56] = b"\x7c\x21"
        return bytes(h) + text

    def _data_reply(self, req: bytes, data: bytes, conn: _ConnState) -> bytes:
        if len(data) <= 6:
            return self._reply(req, 0xD4, conn=conn, inline=data)
        return self._reply(req, 0x94, conn=conn, text=data)

    def _handle(self, h: bytes, text: bytes, conn: _ConnState) -> bytes:
        ctl = self.controller
        if h[0] != 0x02 or h[31] not in (0xC0, 0x80):
            return self._reply(h, 0xD1, major=0x05, minor=0x01, conn=conn)
        if h[31] == 0xC0:
            service, segment = h[42], h[43]
            index = int.from_bytes(h[44:46], "little")
            count = int.from_bytes(h[46:48], "little")
            if service == 0x4F:
                conn.privilege = 4
                return self._reply(h, 0xD4, conn=conn)
            if service == 0x00:
                return self._reply(h, 0xD4, conn=conn)
            if service == 0x43:
                body = bytearray(40)
                body[12 : 12 + len(self.controller_type)] = self.controller_type
                return self._reply(h, 0x94, conn=conn, text=bytes(body))
            if service in (0x03, 0x38):
                return self._reply(h, 0x94, conn=conn, text=bytes(16))
            if service != 0x04 or count == 0:
                return self._reply(h, 0xD1, major=0x05, minor=0x02, conn=conn)
            if segment == 0x08:
                return self._data_reply(h, ctl.read_r(index + 1, count, conn.local_table), conn)
            if segment in ctl.words:
                mem = ctl.words[segment]
                return self._data_reply(h, bytes(mem[2 * index : 2 * (index + count)]), conn)
            if segment in ctl.bits:
                if index % 8 or count % 8:
                    return self._reply(h, 0xD1, major=0x05, minor=0x03, conn=conn)
                mem = ctl.bits[segment]
                return self._data_reply(h, bytes(mem[index // 8 : (index + count) // 8]), conn)
            return self._reply(h, 0xD1, major=0x05, minor=0x04, conn=conn)
        # extended request
        service, segment = h[50], h[51]
        index = int.from_bytes(h[52:54], "little")
        count = int.from_bytes(h[54:56], "little")
        if service != 0x07:
            return self._reply(h, 0xD1, major=0x05, minor=0x05, conn=conn)
        if segment == 0x38:
            ctl.command(text.decode("ascii", errors="replace"), conn)
        elif segment == 0x08:
            if len(text) != 2 * count:
                return self._reply(h, 0xD1, major=0x05, minor=0x06, conn=conn)
            ctl.write_r(index + 1, text, conn.local_table)
        elif segment in ctl.words:
            if len(text) != 2 * count:
                return self._reply(h, 0xD1, major=0x05, minor=0x06, conn=conn)
            ctl.words[segment][2 * index : 2 * (index + count)] = text
        elif segment in ctl.bits:
            mem = ctl.bits[segment]
            for i in range(count):
                pos = index % 8 + i
                bit = bool(text[pos // 8] >> (pos % 8) & 1)
                b = index + i
                if bit:
                    mem[b // 8] |= 1 << (b % 8)
                else:
                    mem[b // 8] &= ~(1 << (b % 8)) & 0xFF
        else:
            return self._reply(h, 0xD1, major=0x05, minor=0x07, conn=conn)
        return self._reply(h, 0xD4, conn=conn)
