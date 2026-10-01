"""High-level SNPX client.

Indexing is FANUC-style 1-based everywhere in this API (``R[1]``, ``PR[1]``,
``DO[1]``, ``%R1``). Values are decoded according to the assignment that maps
them (see :mod:`fanuc_snpx.assignments`).

The client is read-only unless it is created with ``allow_writes=True`` *and* an
:class:`~fanuc_snpx.policy.AccessPolicy` that lists the target as writable and
not protected. Every write reads the old value, consults the optional
``write_guard``, writes, reads back and compares. See docs/SAFETY.md.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from .assignments import (
    Assignment,
    AssignmentManager,
    AssignmentTable,
    Located,
    SysvarType,
    canonical_sysvar,
)
from .errors import (
    ReadBackMismatch,
    SnpxAssignmentError,
    WriteNotAllowed,
    WriteVetoed,
)
from .evidence import EvidenceLog
from .memory import (
    Segment,
    decode_float32,
    decode_int16,
    decode_int32,
    decode_string,
    decode_uint16,
    encode_float32,
    encode_int16,
    encode_int32,
    encode_string,
    encode_uint16,
    f32_equal,
)
from .policy import AccessPolicy
from .srtp import DEFAULT_PORT, ServiceCode, SrtpSession, WriteAuthorization
from .types import (
    W_J1,
    W_VALIDC,
    W_VALIDJ,
    W_X,
    Cartesian,
    IoFamily,
    Joints,
    Position,
    RawServiceReply,
    ShortStatus,
    decode_position,
    encode_cartesian,
    encode_joints,
)

log = logging.getLogger(__name__)

T = TypeVar("T")
WriteGuard = Callable[[str, Any, Any, str], "bool | None"]
"""``write_guard(target, old, new, reason)``: return ``False`` or raise to veto a write."""

Number = int | float


class SnpxClient:
    """A SNPX client for one controller.

    Parameters
    ----------
    host, port:
        Controller address. Port 18245 is the "HMI Device (SNPX)" SRTP server.
    timeout:
        Seconds allowed for each request/response (and for connecting).
    policy:
        An :class:`AccessPolicy`, a path to a policy file, or ``None``. With
        ``None`` and ``allow_writes=True`` the default locations are searched
        (``$FANUC_SNPX_POLICY``, then ``./robot-policy.local.json``).
    allow_writes:
        Must be ``True`` for any write to be considered. Defaults to ``False``.
    write_guard:
        Optional ``callback(target, old, new, reason)``; return ``False`` or raise
        to veto a write.
    evidence:
        An :class:`EvidenceLog` or a path for one; logs every frame as JSONL.
    assignments:
        The controller's existing assignment table, if known (for example the
        factory default, or a table read from the controller files). Reads of
        registers and variables are resolved through it.
    max_requests_per_second:
        Rate limit for this connection (rolling one-second window).
    readback_tolerance:
        Absolute tolerance for comparing reals after a write, applied after
        rounding both values to float32. 0 means exact float32 equality.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = 2.0,
        policy: AccessPolicy | str | os.PathLike[str] | None = None,
        allow_writes: bool = False,
        write_guard: WriteGuard | None = None,
        evidence: EvidenceLog | str | os.PathLike[str] | None = None,
        assignments: AssignmentTable | None = None,
        session_control: bool = True,
        max_read_bytes: int = 1024,
        max_write_bytes: int = 1024,
        max_requests_per_second: float = 5.0,
        readback_tolerance: float = 0.0,
        session: SrtpSession | None = None,
    ) -> None:
        self.host = host
        self.allow_writes = bool(allow_writes)
        self.write_guard = write_guard
        self.readback_tolerance = readback_tolerance
        if isinstance(policy, AccessPolicy):
            self.policy: AccessPolicy | None = policy
        elif policy is not None:
            self.policy = AccessPolicy.load(policy)
        elif allow_writes:
            self.policy = AccessPolicy.discover()
        else:
            self.policy = None
        self._owns_evidence = evidence is not None and not isinstance(evidence, EvidenceLog)
        if evidence is None or isinstance(evidence, EvidenceLog):
            self.evidence = evidence
        else:
            self.evidence = EvidenceLog(Path(evidence))
        self._session = session or SrtpSession(
            host,
            port,
            timeout=timeout,
            session_control=session_control,
            max_read_bytes=max_read_bytes,
            max_write_bytes=max_write_bytes,
            max_requests_per_second=max_requests_per_second,
            evidence=self.evidence,
        )
        self._declared_table = assignments or AssignmentTable()
        self._table = self._declared_table

        self.raw = RawMemory(self)
        self.numeric_registers = NumericRegisters(self)
        self.position_registers = PositionRegisters(self)
        self.string_registers = StringRegisters(self)
        self.sysvars = SystemVariables(self)
        self.frames = Frames(self)
        self.current_position = CurrentPosition(self)
        self.io = IoAccess(self)
        self.comments = Comments(self)
        self.controller = ControllerInfo(self)
        self.assignments = AssignmentManager(self)

    # -- lifecycle -------------------------------------------------------------

    @property
    def session(self) -> SrtpSession:
        """The underlying SRTP session (for statistics; prefer the typed API)."""
        return self._session

    def connect(self) -> None:
        """Open the connection now (otherwise it opens on the first request)."""
        if not self._session.is_open:
            self._session.open()

    def reconnect(self) -> None:
        """Close and open a brand-new session; session assignments are re-created."""
        self._session.open()

    def close(self) -> None:
        self._session.close()
        if self._owns_evidence and self.evidence is not None:
            self.evidence.close()

    def __enter__(self) -> SnpxClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- assignment table ------------------------------------------------------

    @property
    def table(self) -> AssignmentTable:
        """The assignment table reads are resolved through."""
        return self._table

    def _set_table(self, table: AssignmentTable) -> None:
        self._table = table

    def _restore_declared_table(self) -> None:
        self._table = self._declared_table

    def _read_located(self, loc: Located) -> bytes:
        return self._session.read_words(Segment.R, loc.address, loc.words)

    def _read_elements(
        self, family: str, first: int, count: int, *, sub: str = "", group: int = 1
    ) -> list[tuple[Assignment, bytes]]:
        out: list[tuple[Assignment, bytes]] = []
        for run in self._table.plan_runs(family, first, count, sub=sub, group=group):
            data = self._session.read_words(Segment.R, run.address, run.words)
            step = 2 * run.assignment.words_per_element
            out.extend((run.assignment, data[i : i + step]) for i in range(0, len(data), step))
        if len(out) != count:
            raise SnpxAssignmentError(f"expected {count} elements, decoded {len(out)}")
        return out

    # -- write machinery -------------------------------------------------------------

    def _authorize(
        self, kind: str, targets: Sequence[int] | Sequence[str], reason: str
    ) -> WriteAuthorization:
        if not self.allow_writes:
            raise WriteNotAllowed("this client is read-only (create it with allow_writes=True)")
        if self.policy is None:
            raise WriteNotAllowed("no access policy is loaded, so every write is refused")
        return self.policy.authorize(kind, targets, reason=reason, host=self.host)

    def _check_limits(self, kind: str, indexes: Sequence[int]) -> None:
        if self.policy is not None:
            self.policy.check_limits(kind, indexes)

    def _guarded_write(
        self,
        *,
        kind: str,
        targets: Sequence[int] | Sequence[str],
        label: str,
        reason: str,
        new: Any,
        read: Callable[[], T],
        write: Callable[[WriteAuthorization], None],
        matches: Callable[[T], bool],
    ) -> T:
        auth = self._authorize(kind, targets, reason)
        old = read()
        if self.write_guard is not None and self.write_guard(label, old, new, reason) is False:
            raise WriteVetoed(f"write_guard vetoed the write to {label}")
        log.info("write %s: %r -> %r (reason: %s)", label, old, new, reason)
        if self.evidence is not None:
            self.evidence.record("write_begin", target=label, old=old, new=new, reason=reason)
        write(auth)
        back = read()
        ok = matches(back)
        if self.evidence is not None:
            self.evidence.record("write_end", target=label, readback=back, ok=ok)
        if not ok:
            raise ReadBackMismatch(
                f"{label}: wrote {new!r}, read back {back!r}",
                target=label,
                expected=new,
                actual=back,
            )
        return old

    def _send_commands(self, commands: Sequence[str], auth: WriteAuthorization) -> None:
        """Write SNPX command strings to %G (one command per write)."""
        for cmd in commands:
            data = cmd.encode("ascii")
            self._session.write_bytes(Segment.G_BYTE, 1, data, authorization=auth)

    def _real_equal(self, a: float, b: float) -> bool:
        return f32_equal(a, b, self.readback_tolerance)


# --- decoding helpers -------------------------------------------------------------


def _decode_scalar(a: Assignment, data: bytes, sysvar_type: SysvarType | None = None) -> Number:
    """Decode a numeric register or numeric system variable element."""
    first = a.slice_first_word
    words = a.words_per_element
    mult = a.multiply
    if sysvar_type is SysvarType.BOOLEAN:
        return int(decode_int32(data) != 0)
    if (first, words) == (1, 1):
        raw16 = decode_int16(data)
        return raw16 if mult in (0, 1) else raw16 / mult
    if (first, words) != (1, 2):
        raise SnpxAssignmentError(f"{a.var}: cannot decode a numeric value from this slice")
    if mult == 0 and sysvar_type not in (SysvarType.INTEGER, SysvarType.SHORT, SysvarType.BYTE):
        return decode_float32(data)
    raw = decode_int32(data)
    return raw if mult in (0, 1) else raw / mult


def _encode_scalar(a: Assignment, value: Number, sysvar_type: SysvarType | None = None) -> bytes:
    first, words, mult = a.slice_first_word, a.words_per_element, a.multiply
    if sysvar_type is SysvarType.BOOLEAN:
        return encode_int32(1 if value else 0)
    if (first, words) == (1, 1):
        return encode_int16(round(value * (mult or 1)))
    if (first, words) != (1, 2):
        raise SnpxAssignmentError(f"{a.var}: cannot write a numeric value through this slice")
    if mult == 0 and sysvar_type not in (SysvarType.INTEGER, SysvarType.SHORT, SysvarType.BYTE):
        return encode_float32(float(value))
    return encode_int32(round(value * (mult or 1)))


def _expected_scalar(a: Assignment, value: Number, sysvar_type: SysvarType | None = None) -> Number:
    """What reading back ``value`` through assignment ``a`` should give."""
    return _decode_scalar(a, _encode_scalar(a, value, sysvar_type), sysvar_type)


def _same_number(client: SnpxClient, a: Number, b: Number) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return client._real_equal(float(a), float(b))
    return a == b


class _Api:
    def __init__(self, client: SnpxClient) -> None:
        self._c = client


# --- raw memory (read only) ---------------------------------------------------------


class RawMemory(_Api):
    """Read-only access to PLC memory as the controller presents it.

    There are deliberately no raw write methods; writes go through the typed APIs
    so that the access policy can name what is being changed.
    """

    _WORDS: ClassVar[dict[str, Segment]] = {"R": Segment.R, "AI": Segment.AI, "AQ": Segment.AQ}
    _BITS: ClassVar[dict[str, Segment]] = {
        "I": Segment.I_BIT,
        "Q": Segment.Q_BIT,
        "M": Segment.M_BIT,
        "T": Segment.T_BIT,
        "G": Segment.G_BIT,
        "SA": Segment.SA_BIT,
        "SB": Segment.SB_BIT,
        "SC": Segment.SC_BIT,
        "S": Segment.S_BIT,
    }

    def read_words(self, area: str, address: int, count: int = 1) -> list[int]:
        """Unsigned 16-bit words from ``%R``, ``%AI`` or ``%AQ`` (1-based address)."""
        seg = self._WORDS[area.upper().lstrip("%")]
        data = self._c._session.read_words(seg, address, count)
        return [decode_uint16(data, i) for i in range(0, len(data), 2)]

    def read_bytes(self, area: str, address: int, count: int = 1) -> bytes:
        """Words from a word area as raw little-endian bytes."""
        seg = self._WORDS[area.upper().lstrip("%")]
        return self._c._session.read_words(seg, address, count)

    def read_bits(self, area: str, address: int, count: int = 1) -> list[bool]:
        """Bits from ``%I``, ``%Q``, ``%M``, ``%T``, ``%G`` or ``%S*`` (1-based address)."""
        seg = self._BITS[area.upper().lstrip("%")]
        return self._c._session.read_bits(seg, address, count)


# --- numeric registers ----------------------------------------------------------------


class NumericRegisters(_Api):
    """``R[i]``. Values are ints or floats depending on the assignment.

    A ``$MULTIPLY`` of 0 gives float32 reals; 1 gives integers (fractions are
    rounded by the controller); ``@1.1`` gives 16-bit integers.
    """

    KIND = "numeric_registers"

    def read(self, index: int) -> Number:
        return self.read_block(index, 1)[0]

    def read_block(self, first: int, count: int) -> list[Number]:
        self._c._check_limits(self.KIND, [first + count - 1])
        return [_decode_scalar(a, d) for a, d in self._c._read_elements("R", first, count)]

    def read_real(self, index: int) -> float:
        """Read as a real; requires an assignment with ``$MULTIPLY`` = 0."""
        loc = self._c.table.locate("R", index)
        if loc.assignment.multiply != 0 or loc.assignment.words_per_element != 2:
            raise SnpxAssignmentError(
                f"R[{index}] is mapped by {loc.assignment.var} with $MULTIPLY "
                f"{loc.assignment.multiply}; a real needs a full 2-word mapping with $MULTIPLY 0"
            )
        return float(self.read(index))

    def read_int(self, index: int) -> int:
        """Read an integer value; raises if the register holds a fraction."""
        v = self.read(index)
        if isinstance(v, float):
            if not v.is_integer():
                raise ValueError(f"R[{index}] holds the non-integer {v}")
            return int(v)
        return v

    def write(self, index: int, value: Number, *, reason: str) -> Number:
        """Write ``R[index]``; returns the previous value."""
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("numeric register values must be int or float")
        self._c._authorize(self.KIND, [index], reason)  # policy errors before any lookup
        loc = self._c.table.locate("R", index)
        expected = _expected_scalar(loc.assignment, value)

        def do_write(auth: WriteAuthorization) -> None:
            self._c._session.write_words(
                Segment.R, loc.address, _encode_scalar(loc.assignment, value), authorization=auth
            )

        return self._c._guarded_write(
            kind=self.KIND,
            targets=[index],
            label=f"R[{index}]",
            reason=reason,
            new=value,
            read=lambda: self.read(index),
            write=do_write,
            matches=lambda back: _same_number(self._c, back, expected),
        )


# --- positions --------------------------------------------------------------------------------


def _position_matches(client: SnpxClient, back: Position, new: Cartesian | Joints) -> bool:
    if isinstance(new, Cartesian):
        try:
            c = back.cartesian()
        except Exception:  # noqa: BLE001 - any failure to decode means "does not match"
            return False
        if not all(client._real_equal(a, b) for a, b in zip(c.xyzwpr, new.xyzwpr, strict=True)):
            return False
        if new.configuration is not None and c.configuration != new.configuration:
            return False
        return (
            new.extended is None
            or c.extended is None
            or all(client._real_equal(a, b) for a, b in zip(c.extended, new.extended, strict=True))
        )
    try:
        j = back.joints()
    except Exception:  # noqa: BLE001
        return False
    return all(client._real_equal(a, b) for a, b in zip(j.values, new.values, strict=False))


def _decode_pos(a: Assignment, data: bytes) -> Position:
    return decode_position(data, first_word=a.slice_first_word, multiply=a.multiply)


def _position_payload(a: Assignment, value: Cartesian | Joints) -> tuple[int, bytes]:
    """(first word to write, payload) for a Cartesian or joint write through ``a``."""
    first, n = a.slice_first_word, a.words_per_element
    if isinstance(value, Cartesian):
        word, payload = W_X, encode_cartesian(value, multiply=a.multiply)
        last = W_VALIDC
    else:
        word, payload = W_J1, encode_joints(value, multiply=a.multiply)
        last = W_VALIDJ
    if first > word or first + n - 1 < last:
        raise SnpxAssignmentError(f"{a.var} does not include words {word}..{last} needed to write")
    return word - first, payload


class PositionRegisters(_Api):
    """``PR[i]`` (optionally of motion group ``group``)."""

    KIND = "position_registers"

    def read(self, index: int, *, group: int = 1) -> Position:
        return self.read_block(index, 1, group=group)[0]

    def read_block(self, first: int, count: int, *, group: int = 1) -> list[Position]:
        self._c._check_limits(self.KIND, [first + count - 1])
        return [
            _decode_pos(a, d) for a, d in self._c._read_elements("PR", first, count, group=group)
        ]

    def write(
        self, index: int, value: Cartesian | Joints, *, reason: str, group: int = 1
    ) -> Position:
        """Write a Cartesian or joint position; returns the previous value.

        Only words 1-26 (Cartesian) or 27-45 (joint) are written; the user
        frame and tool numbers are never changed.
        """
        if not isinstance(value, Cartesian | Joints):
            raise TypeError("value must be a Cartesian or Joints")
        self._c._authorize(self.KIND, [index], reason)
        loc = self._c.table.locate("PR", index, group=group)
        offset, payload = _position_payload(loc.assignment, value)

        def do_write(auth: WriteAuthorization) -> None:
            self._c._session.write_words(
                Segment.R, loc.address + offset, payload, authorization=auth
            )

        return self._c._guarded_write(
            kind=self.KIND,
            targets=[index],
            label=f"PR[{'' if group == 1 else f'G{group}:'}{index}]",
            reason=reason,
            new=value,
            read=lambda: self.read(index, group=group),
            write=do_write,
            matches=lambda back: _position_matches(self._c, back, value),
        )


class CurrentPosition(_Api):
    """The robot's current position, via a ``POS[Gg:uf]`` assignment (read only).

    ``frame`` 0 is WORLD, 1..9 a specific user frame, 15 the currently selected
    user frame. Cartesian and joint values come from one 50-word read, i.e. one
    controller snapshot.
    """

    def read(self, *, group: int = 1, frame: int = 0) -> Position:
        loc = self._c.table.locate("POS", frame, group=group)
        return _decode_pos(loc.assignment, self._c._read_located(loc))

    def world(self, *, group: int = 1) -> Position:
        return self.read(group=group, frame=0)

    def user(self, *, group: int = 1) -> Position:
        return self.read(group=group, frame=15)


# --- strings ---------------------------------------------------------------------------------


class StringRegisters(_Api):
    """``SR[i]``: up to 80 ASCII characters (fewer with an ``@`` slice)."""

    KIND = "string_registers"

    def read(self, index: int) -> str:
        return self.read_block(index, 1)[0]

    def read_block(self, first: int, count: int) -> list[str]:
        self._c._check_limits(self.KIND, [first + count - 1])
        return [decode_string(d) for _a, d in self._c._read_elements("SR", first, count)]

    def write(self, index: int, text: str, *, reason: str) -> str:
        self._c._authorize(self.KIND, [index], reason)
        loc = self._c.table.locate("SR", index)
        payload = encode_string(text, 2 * loc.words)

        def do_write(auth: WriteAuthorization) -> None:
            self._c._session.write_words(Segment.R, loc.address, payload, authorization=auth)

        return self._c._guarded_write(
            kind=self.KIND,
            targets=[index],
            label=f"SR[{index}]",
            reason=reason,
            new=text,
            read=lambda: self.read(index),
            write=do_write,
            matches=lambda back: back == text,
        )


_COMMENT_KINDS = {
    "R": "numeric_registers",
    "PR": "position_registers",
    **{f.name: f.policy_kind for f in IoFamily},
}


class Comments(_Api):
    """Comments of ``R``, ``PR`` and I/O, via ``X[Cn]`` assignments (40 words = 80 chars)."""

    def read(self, family: str, index: int) -> str:
        fam = family.upper()
        if fam not in _COMMENT_KINDS:
            raise ValueError(f"comments exist for {sorted(_COMMENT_KINDS)}, not {family!r}")
        loc = self._c.table.locate(fam, index, sub="C")
        return decode_string(self._c._read_located(loc))

    def write(self, family: str, index: int, text: str, *, reason: str) -> str:
        fam = family.upper()
        if fam not in _COMMENT_KINDS:
            raise ValueError(f"comments exist for {sorted(_COMMENT_KINDS)}, not {family!r}")
        self._c._authorize(f"comments.{_COMMENT_KINDS[fam]}", [index], reason)
        loc = self._c.table.locate(fam, index, sub="C")
        payload = encode_string(text, 2 * loc.words)

        def do_write(auth: WriteAuthorization) -> None:
            self._c._session.write_words(Segment.R, loc.address, payload, authorization=auth)

        return self._c._guarded_write(
            kind=f"comments.{_COMMENT_KINDS[fam]}",
            targets=[index],
            label=f"{fam}[C{index}]",
            reason=reason,
            new=text,
            read=lambda: self.read(fam, index),
            write=do_write,
            matches=lambda back: back == text,
        )


# --- system variables ------------------------------------------------------------------------

SysvarValue = int | float | str | Position


class SystemVariables(_Api):
    """System and KAREL variables mapped by ``$...`` assignments.

    Names must be complete: ``$MNUFRAME[1,2]``, never ``$MNUFRAME[2]``.
    """

    KIND = "system_variables"

    def _locate(self, name: str) -> tuple[str, Located, SysvarType]:
        canon = canonical_sysvar(name)
        loc = self._c.table.locate_sysvar(canon)
        st = loc.assignment.sysvar_type
        if st is None:
            raise SnpxAssignmentError(f"{loc.assignment.var} has no sysvar_type")
        return canon, loc, st

    def read(self, name: str) -> SysvarValue:
        _canon, loc, st = self._locate(name)
        data = self._c._read_located(loc)
        if st is SysvarType.STRING:
            return decode_string(data)
        if st is SysvarType.POSITION:
            return _decode_pos(loc.assignment, data)
        value = _decode_scalar(loc.assignment, data, st)
        if st is SysvarType.BOOLEAN:
            return bool(value)
        return value

    def read_position(self, name: str) -> Position:
        v = self.read(name)
        if not isinstance(v, Position):
            raise SnpxAssignmentError(f"{name} is not mapped as a POSITION")
        return v

    def read_number(self, name: str) -> Number:
        v = self.read(name)
        if isinstance(v, str | Position):
            raise SnpxAssignmentError(f"{name} is not mapped as a number")
        return v

    def read_string(self, name: str) -> str:
        v = self.read(name)
        if not isinstance(v, str):
            raise SnpxAssignmentError(f"{name} is not mapped as a STRING")
        return v

    def write(self, name: str, value: SysvarValue | Cartesian, *, reason: str) -> SysvarValue:
        """Write a mapped system variable and verify it by reading back.

        For POSITION variables pass a :class:`Cartesian`; only X..R (words 1-12)
        are written, so the stored configuration and frame numbers stay as they are.
        """
        self._c._authorize(self.KIND, [canonical_sysvar(name)], reason)
        canon, loc, st = self._locate(name)
        a = loc.assignment
        if st is SysvarType.POSITION:
            if not isinstance(value, Cartesian):
                raise TypeError("POSITION system variables take a Cartesian value")
            if a.slice_first_word != 1 or a.words_per_element < 12:
                raise SnpxAssignmentError(f"{a.var} does not include X..R")
            payload = b"".join(
                encode_float32(v) if a.multiply == 0 else encode_int32(round(v * a.multiply))
                for v in value.xyzwpr
            )

            def matches(back: SysvarValue) -> bool:
                if not isinstance(back, Position) or back.cartesian_view is None:
                    return False
                return all(
                    self._c._real_equal(x, y)
                    for x, y in zip(back.cartesian_view.xyzwpr, value.xyzwpr, strict=True)
                )
        elif st is SysvarType.STRING:
            if not isinstance(value, str):
                raise TypeError(f"{canon} is a STRING")
            payload = encode_string(value, 2 * loc.words)

            def matches(back: SysvarValue) -> bool:
                return back == value
        else:
            if isinstance(value, str | Position | Cartesian):
                raise TypeError(f"{canon} is numeric")
            payload = _encode_scalar(a, value, st)
            expected = _expected_scalar(a, value, st)

            def matches(back: SysvarValue) -> bool:
                return isinstance(back, int | float) and _same_number(self._c, back, expected)

        def do_write(auth: WriteAuthorization) -> None:
            self._c._session.write_words(Segment.R, loc.address, payload, authorization=auth)

        return self._c._guarded_write(
            kind=self.KIND,
            targets=[canon],
            label=canon,
            reason=reason,
            new=value,
            read=lambda: self.read(canon),
            write=do_write,
            matches=matches,
        )


class Frames(_Api):
    """User frames ``$MNUFRAME[g,i]`` and user tools ``$MNUTOOL[g,i]`` (POSITION variables)."""

    def read_user_frame(self, group: int, number: int) -> Cartesian:
        return self._c.sysvars.read_position(f"$MNUFRAME[{group},{number}]").cartesian()

    def read_user_tool(self, group: int, number: int) -> Cartesian:
        return self._c.sysvars.read_position(f"$MNUTOOL[{group},{number}]").cartesian()

    def write_user_frame(self, group: int, number: int, value: Cartesian, *, reason: str) -> None:
        self._c.sysvars.write(f"$MNUFRAME[{group},{number}]", value, reason=reason)

    def write_user_tool(self, group: int, number: int, value: Cartesian, *, reason: str) -> None:
        self._c.sysvars.write(f"$MNUTOOL[{group},{number}]", value, reason=reason)


# --- I/O ---------------------------------------------------------------------------------------

_Q_OFFSETS = sorted({f.offset for f in IoFamily if f.plc_area == "Q"})
_AQ_OFFSETS = sorted({f.offset for f in IoFamily if f.plc_area == "AQ"})


def _io_address(family: IoFamily, index: int, count: int) -> int:
    offsets = _AQ_OFFSETS if family.is_word else _Q_OFFSETS
    nxt = [o for o in offsets if o > family.offset]
    lowest = 1 if family.offset == 0 else 0
    if index < lowest or count < 1:
        raise ValueError(f"{family.name}[{index}] x{count} is not a valid I/O span")
    last = index + count - 1
    if nxt and family.offset + last >= nxt[0]:
        raise ValueError(f"{family.name}[{last}] would run into the next I/O family")
    return family.offset + index


class IoAccess(_Api):
    """Robot I/O through the fixed SNPX mapping (no assignment needed).

    Digital signals read as ``bool``; GI/GO/AI/AO read as unsigned 16-bit ints.
    Writing I/O can move equipment or confuse a PLC that owns the signal: list
    every writable signal explicitly in the policy and protect the rest.
    """

    def _family(self, family: IoFamily | str) -> IoFamily:
        return family if isinstance(family, IoFamily) else IoFamily[family.upper()]

    def read(self, family: IoFamily | str, index: int, count: int = 1) -> list[bool] | list[int]:
        fam = self._family(family)
        address = _io_address(fam, index, count)
        if fam.is_word:
            seg = Segment.AQ if fam.plc_area == "AQ" else Segment.AI
            data = self._c._session.read_words(seg, address, count)
            return [decode_uint16(data, i) for i in range(0, len(data), 2)]
        seg = Segment.Q_BIT if fam.plc_area == "Q" else Segment.I_BIT
        return self._c._session.read_bits(seg, address, count)

    def read_one(self, family: IoFamily | str, index: int) -> bool | int:
        return self.read(family, index, 1)[0]

    def write(
        self,
        family: IoFamily | str,
        index: int,
        values: Sequence[bool] | Sequence[int],
        *,
        reason: str,
    ) -> list[bool] | list[int]:
        """Write consecutive signals; returns the previous values."""
        fam = self._family(family)
        vals = list(values)
        address = _io_address(fam, index, len(vals))
        targets = list(range(index, index + len(vals)))
        self._c._authorize(fam.policy_kind, targets, reason)

        def do_write(auth: WriteAuthorization) -> None:
            if fam.is_word:
                seg = Segment.AQ if fam.plc_area == "AQ" else Segment.AI
                payload = b"".join(encode_uint16(int(v)) for v in vals)
                self._c._session.write_words(seg, address, payload, authorization=auth)
            else:
                seg = Segment.Q_BIT if fam.plc_area == "Q" else Segment.I_BIT
                self._c._session.write_bits(
                    seg, address, [bool(v) for v in vals], authorization=auth
                )

        expected = [int(v) for v in vals] if fam.is_word else [bool(v) for v in vals]
        return self._c._guarded_write(
            kind=fam.policy_kind,
            targets=targets,
            label=f"{fam.name}[{index}]" + (f"..[{targets[-1]}]" if len(vals) > 1 else ""),
            reason=reason,
            new=vals,
            read=lambda: self.read(fam, index, len(vals)),
            write=do_write,
            matches=lambda back: list(back) == expected,
        )


# --- controller information ----------------------------------------------------------------


class ControllerInfo(_Api):
    """Read-only SRTP information services.

    Only the short status has a (partly) decoded form; the other replies are
    returned raw until their layout is verified on a FANUC controller.
    """

    def short_status(self) -> ShortStatus:
        r = self._c._session.service(ServiceCode.PLC_SHORT_STATUS)
        return ShortStatus(header=r.header, privilege_level=r.header[51], status_word=r.status_word)

    def controller_type(self) -> RawServiceReply:
        r = self._c._session.service(ServiceCode.RETURN_CONTROLLER_TYPE)
        return RawServiceReply(int(ServiceCode.RETURN_CONTROLLER_TYPE), r.header, r.text)

    def program_name(self) -> RawServiceReply:
        r = self._c._session.service(ServiceCode.RETURN_PROGRAM_NAME)
        return RawServiceReply(int(ServiceCode.RETURN_PROGRAM_NAME), r.header, r.text)

    def fault_table(self) -> RawServiceReply:
        r = self._c._session.service(ServiceCode.RETURN_FAULT_TABLE)
        return RawServiceReply(int(ServiceCode.RETURN_FAULT_TABLE), r.header, r.text)


__all__ = [
    "Comments",
    "ControllerInfo",
    "CurrentPosition",
    "Frames",
    "IoAccess",
    "NumericRegisters",
    "PositionRegisters",
    "RawMemory",
    "SnpxClient",
    "StringRegisters",
    "SystemVariables",
    "WriteGuard",
]
