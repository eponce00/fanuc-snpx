"""Value types returned by the client: positions, I/O families, controller status.

Units: millimetres for X/Y/Z and linear axes, degrees for W/P/R and rotary axes,
as stored by the controller. Reals are float32 values widened to Python floats.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from enum import Enum

from .errors import RepresentationError
from .memory import decode_string, encode_float32, encode_int32

# --- positions ----------------------------------------------------------------

POSITION_WORDS = 50
"""Words in the SNPX position structure (PR[], POS[], POSITION system variables)."""

# 1-based word numbers inside the 50-word position structure.
W_X = 1
W_FLIP = 19
W_TURN4 = 23
W_VALIDC = 26
W_J1 = 27
W_VALIDJ = 45
W_UF = 46
W_UT = 47


@dataclass(frozen=True)
class Configuration:
    """Arm configuration of a Cartesian position (FANUC "N U T, 0, 0, 0")."""

    flip: bool
    left: bool
    up: bool
    front: bool
    turn4: int = 0
    turn5: int = 0
    turn6: int = 0

    def __str__(self) -> str:
        return (
            f"{'F' if self.flip else 'N'} {'L' if self.left else 'R'} "
            f"{'U' if self.up else 'D'} {'T' if self.front else 'B'}, "
            f"{self.turn4}, {self.turn5}, {self.turn6}"
        )


@dataclass(frozen=True)
class Cartesian:
    """X, Y, Z (mm) and W, P, R (deg) with configuration and extended axes E1..E3.

    `configuration` and `extended` are `None` when the assignment slice
    covers only X..R.
    """

    x: float
    y: float
    z: float
    w: float
    p: float
    r: float
    configuration: Configuration | None
    extended: tuple[float, float, float] | None = (0.0, 0.0, 0.0)

    @property
    def xyzwpr(self) -> tuple[float, float, float, float, float, float]:
        return (self.x, self.y, self.z, self.w, self.p, self.r)


@dataclass(frozen=True)
class Joints:
    """Joint values J1..J9 (deg for rotary, mm for linear axes)."""

    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.values) <= 9:
            raise ValueError("between 1 and 9 joint values are required")

    def __getitem__(self, axis: int) -> float:
        """1-based axis access: ``joints[1]`` is J1."""
        if not 1 <= axis <= len(self.values):
            raise IndexError(f"J{axis} not present")
        return self.values[axis - 1]

    @property
    def j1_j6(self) -> tuple[float, ...]:
        return self.values[:6]


@dataclass(frozen=True)
class Position:
    """A decoded SNPX position structure.

    The controller supplies both a Cartesian and a joint view, converting on the
    fly. ``valid_cartesian`` / ``valid_joint`` are the VALIDC / VALIDJ words: a
    view is invalid when the position is untaught or cannot be converted. A
    field is ``None`` when the assignment slice (``@a.b``) does not include it.

    SNPX does not report which representation is *stored*; ``is_cartesian`` is
    therefore only definite when exactly one view is valid.
    """

    cartesian_view: Cartesian | None
    joint_view: Joints | None
    valid_cartesian: bool | None
    valid_joint: bool | None
    user_frame: int | None
    user_tool: int | None
    raw: bytes

    def cartesian(self) -> Cartesian:
        if self.cartesian_view is None or self.valid_cartesian is False:
            raise RepresentationError(
                "Cartesian data is not available (VALIDC = 0, untaught, or not in the assignment)"
            )
        return self.cartesian_view

    def joints(self) -> Joints:
        if self.joint_view is None or self.valid_joint is False:
            raise RepresentationError(
                "joint data is not available (VALIDJ = 0, untaught, or not in the assignment)"
            )
        return self.joint_view

    @property
    def is_cartesian(self) -> bool | None:
        if self.valid_cartesian and not self.valid_joint:
            return True
        if self.valid_joint and not self.valid_cartesian:
            return False
        return None


def decode_position(data: bytes, *, first_word: int = 1, multiply: float = 0.0) -> Position:
    """Decode a (possibly partial) 50-word position structure.

    ``data`` holds the words ``first_word .. first_word + len(data)//2 - 1`` of
    the structure (1-based). Real fields are float32 when ``multiply`` is 0,
    otherwise signed int32 values scaled by ``multiply``.
    """
    if len(data) % 2:
        raise ValueError("position data must be whole words")
    n_words = len(data) // 2
    if first_word < 1 or first_word + n_words - 1 > POSITION_WORDS:
        raise ValueError("slice exceeds the 50-word position structure")
    last_word = first_word + n_words - 1

    def has(lo: int, hi: int) -> bool:
        return first_word <= lo and hi <= last_word

    def off(word: int) -> int:
        return 2 * (word - first_word)

    def real(word: int) -> float:
        if multiply == 0:
            return float(struct.unpack_from("<f", data, off(word))[0])
        return int(struct.unpack_from("<i", data, off(word))[0]) / multiply

    def i16(word: int) -> int:
        return int(struct.unpack_from("<h", data, off(word))[0])

    cart: Cartesian | None = None
    if has(W_X, W_TURN4 + 2):
        cfg: Configuration | None = Configuration(
            flip=bool(i16(W_FLIP)),
            left=bool(i16(W_FLIP + 1)),
            up=bool(i16(W_FLIP + 2)),
            front=bool(i16(W_FLIP + 3)),
            turn4=i16(W_TURN4),
            turn5=i16(W_TURN4 + 1),
            turn6=i16(W_TURN4 + 2),
        )
        x, y, z, w, p, r, e1, e2, e3 = (real(W_X + 2 * k) for k in range(9))
        cart = Cartesian(x, y, z, w, p, r, cfg, (e1, e2, e3))
    elif has(W_X, W_X + 11):
        x, y, z, w, p, r = (real(W_X + 2 * k) for k in range(6))
        cart = Cartesian(x, y, z, w, p, r, None, None)
    joints: Joints | None = None
    if has(W_J1, W_J1 + 17):
        joints = Joints(tuple(real(W_J1 + 2 * k) for k in range(9)))
    elif has(W_J1, W_J1 + 11):
        joints = Joints(tuple(real(W_J1 + 2 * k) for k in range(6)))
    return Position(
        cartesian_view=cart,
        joint_view=joints,
        valid_cartesian=bool(i16(W_VALIDC)) if has(W_VALIDC, W_VALIDC) else None,
        valid_joint=bool(i16(W_VALIDJ)) if has(W_VALIDJ, W_VALIDJ) else None,
        user_frame=i16(W_UF) if has(W_UF, W_UF) else None,
        user_tool=i16(W_UT) if has(W_UT, W_UT) else None,
        raw=bytes(data),
    )


def _real_bytes(value: float, multiply: float) -> bytes:
    if multiply == 0:
        return encode_float32(value)
    return encode_int32(round(value * multiply))


def encode_cartesian(c: Cartesian, *, multiply: float = 0.0) -> bytes:
    """Words 1..26 of the position structure (X..TURN6 plus VALIDC = 1).

    Writing these words switches a position register to Cartesian format. UF/UT
    (words 46-47) are deliberately never written: they cannot be restored from
    the teach pendant.
    """
    if c.configuration is None:
        raise ValueError("a Cartesian write needs the arm configuration")
    out = bytearray()
    for v in (*c.xyzwpr, *(c.extended or (0.0, 0.0, 0.0))):
        out += _real_bytes(v, multiply)
    cfg = c.configuration
    for v in (int(cfg.flip), int(cfg.left), int(cfg.up), int(cfg.front)):
        out += struct.pack("<h", v)
    for t in (cfg.turn4, cfg.turn5, cfg.turn6):
        if not -128 <= t <= 127:
            raise ValueError(f"turn number {t} outside -128..127")
        out += struct.pack("<h", t)
    out += struct.pack("<h", 1)
    return bytes(out)


def encode_joints(j: Joints, *, multiply: float = 0.0) -> bytes:
    """Words 27..45 of the position structure (J1..J9 plus VALIDJ = 1)."""
    values = j.values + (0.0,) * (9 - len(j.values))
    out = bytearray()
    for v in values:
        out += _real_bytes(v, multiply)
    out += struct.pack("<h", 1)
    return bytes(out)


# --- alarms and program status (read only) ----------------------------------------

ALARM_WORDS = 100
PROGRAM_WORDS = 18

ALARM_SEVERITY_NAMES: dict[int, str] = {
    128: "NONE",
    0: "WARN",
    2: "PAUSE.L",
    34: "PAUSE.G",
    6: "STOP.L",
    38: "STOP.G",
    54: "SERVO",
    11: "ABORT.L",
    43: "ABORT.G",  # fanuc_ucl
    45: "ABORT.G",  # FANUC B-82604EN table; sources disagree, the text field is authoritative
    58: "SERVO2",
    122: "SYSTEM",  # FANUC B-82604EN table
    123: "SYSTEM",  # fanuc_ucl
}


def _str_field(data: bytes, first_word: int, word: int, n_words: int) -> str | None:
    """Decode a string field at 1-based ``word`` (length ``n_words``) if it lies in the slice."""
    lo = 2 * (word - first_word)
    hi = lo + 2 * n_words
    if word < first_word or hi > len(data):
        return None
    return decode_string(data[lo:hi])


def _i16_field(data: bytes, first_word: int, word: int) -> int | None:
    off = 2 * (word - first_word)
    if word < first_word or off + 2 > len(data):
        return None
    return int(struct.unpack_from("<h", data, off)[0])


@dataclass(frozen=True)
class Alarm:
    """One line of an alarm screen, via ``ALM[n]`` (active), ``ALM[En]`` (history) or
    ``ALM[Pn]`` (password log). Fields outside an ``@`` slice are ``None``.

    Layout (FANUC B-82604EN/01 §6.5, 100 words): 1 alarm ID, 2 number, 3 cause ID,
    4 cause number, 5 severity, 6-11 year/month/day/hour/minute/second, 12-51 message,
    52-91 cause message, 92-100 severity text.
    """

    alarm_id: int | None
    number: int | None
    cause_id: int | None
    cause_number: int | None
    severity: int | None
    time: tuple[int, int, int, int, int, int] | None
    message: str | None
    cause_message: str | None
    severity_text: str | None
    raw: bytes

    @property
    def is_empty(self) -> bool:
        return not any(self.raw)

    @property
    def is_reset(self) -> bool:
        """A RESET line: ID and number 0 with a message."""
        return self.alarm_id == 0 and self.number == 0 and bool(self.message)

    @property
    def code(self) -> str | None:
        """``"SRVO-001"`` taken from the start of the message, if present."""
        m = re.match(r"^([A-Z0-9]+-\d+)", self.message or "")
        return m[1] if m else None

    @property
    def severity_name(self) -> str | None:
        if self.severity_text:
            return self.severity_text.strip()
        return ALARM_SEVERITY_NAMES.get(self.severity) if self.severity is not None else None


def decode_alarm(data: bytes, *, first_word: int = 1) -> Alarm:
    """Decode a (possibly sliced) 100-word alarm element."""
    f = first_word
    fields = [_i16_field(data, f, w) for w in range(6, 12)]
    time: tuple[int, int, int, int, int, int] | None = None
    if all(x is not None for x in fields):
        year, month, day, hour, minute, second = (int(x or 0) for x in fields)
        time = (year, month, day, hour, minute, second)
    return Alarm(
        alarm_id=_i16_field(data, f, 1),
        number=_i16_field(data, f, 2),
        cause_id=_i16_field(data, f, 3),
        cause_number=_i16_field(data, f, 4),
        severity=_i16_field(data, f, 5),
        time=time,
        message=_str_field(data, f, 12, 40),
        cause_message=_str_field(data, f, 52, 40),
        severity_text=_str_field(data, f, 92, 9),
        raw=bytes(data),
    )


class ProgramState(Enum):
    ENDED = 0
    PAUSED = 1
    RUNNING = 2


@dataclass(frozen=True)
class ProgramStatus:
    """Execution status of one task via ``PRG[n]`` (FANUC B-82604EN/01 §6.6, 18 words).

    1-8 program name, 9 line, 10 state (0 end, 1 pause, 2 running), 11-18 the program
    started first (the caller). All zero when no program runs.
    """

    name: str | None
    line: int | None
    state_code: int | None
    caller: str | None
    raw: bytes

    @property
    def state(self) -> ProgramState | None:
        try:
            return ProgramState(self.state_code) if self.state_code is not None else None
        except ValueError:
            return None


def decode_program(data: bytes, *, first_word: int = 1) -> ProgramStatus:
    f = first_word
    return ProgramStatus(
        name=_str_field(data, f, 1, 8),
        line=_i16_field(data, f, 9),
        state_code=_i16_field(data, f, 10),
        caller=_str_field(data, f, 11, 8),
        raw=bytes(data),
    )


# --- I/O families -------------------------------------------------------------------


class IoFamily(Enum):
    """Robot I/O families, their PLC segment and address offset (FANUC SNPX mapping).

    Robot inputs appear as PLC outputs (%Q, %AQ) and robot outputs as PLC inputs
    (%I, %AI): ``DI[x]`` is ``%Qx``, ``DO[x]`` is ``%Ix``, ``GO[x]`` is ``%AIx``.
    """

    DI = ("Q", 0, "digital_inputs")
    DO = ("I", 0, "digital_outputs")
    RI = ("Q", 5000, "robot_inputs")
    RO = ("I", 5000, "robot_outputs")
    UI = ("Q", 6000, "uop_inputs")
    UO = ("I", 6000, "uop_outputs")
    SI = ("Q", 7000, "sop_inputs")
    SO = ("I", 7000, "sop_outputs")
    WI = ("Q", 8000, "weld_inputs")
    WO = ("I", 8000, "weld_outputs")
    WSI = ("Q", 8400, "wire_stick_inputs")
    WSO = ("I", 8400, "wire_stick_outputs")
    GI = ("AQ", 0, "group_inputs")
    GO = ("AI", 0, "group_outputs")
    AI = ("AQ", 1000, "analog_inputs")
    AO = ("AI", 1000, "analog_outputs")

    @property
    def plc_area(self) -> str:
        return self.value[0]

    @property
    def offset(self) -> int:
        return self.value[1]

    @property
    def policy_kind(self) -> str:
        return self.value[2]

    @property
    def is_word(self) -> bool:
        return self.plc_area in ("AI", "AQ")

    @property
    def is_input(self) -> bool:
        """True for signals the robot reads (DI, RI, UI, SI, WI, WSI, GI, AI)."""
        return self.plc_area in ("Q", "AQ")


# --- controller status ----------------------------------------------------------------


@dataclass(frozen=True)
class ShortStatus:
    """Reply to the PLC short-status service (0x00).

    Only the raw bytes are certain. ``privilege_level`` (header byte 51) and
    ``status_word`` (bytes 54-55) follow public GE SRTP descriptions and are not
    verified on a FANUC controller.
    """

    header: bytes
    privilege_level: int
    status_word: int

    @property
    def raw_bytes_42_55(self) -> str:
        return self.header[42:56].hex(" ")


@dataclass(frozen=True)
class RawServiceReply:
    """Undecoded reply to an information service whose layout is not verified yet."""

    service: int
    header: bytes
    text: bytes

    def ascii_runs(self, min_len: int = 3) -> list[str]:
        """Printable ASCII runs in the reply text (a reading aid, not a decoding)."""
        runs: list[str] = []
        cur = bytearray()
        for b in self.text:
            if 0x20 <= b < 0x7F:
                cur.append(b)
                continue
            if len(cur) >= min_len:
                runs.append(cur.decode("ascii"))
            cur.clear()
        if len(cur) >= min_len:
            runs.append(cur.decode("ascii"))
        return runs
