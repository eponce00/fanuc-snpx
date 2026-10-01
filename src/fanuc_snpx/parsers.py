"""Parsers for controller files used as the validation oracle.

Formats were learned from public files of R-30iA/R-30iB/R-30iB Plus controllers
(V7.70 to V9.40; see docs/PROVENANCE.md) and must be re-checked against the
target controller's own files before a comparison is trusted. All parsers:

* accept CRLF or LF and strip the HTML wrapper the controller adds when a file is
  fetched over HTTP (``/md/numreg.va``),
* read numbers written without a leading zero (``.800``, ``-.364``),
* return ``None`` for values the controller prints as ``Uninitialized`` or
  ``********``.

``strreg.va`` is deliberately not parsed yet: no public example with a stored
string was found, so its value layout is unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .assignments import Assignment, AssignmentTable, SysvarType, VarName
from .errors import SnpxAssignmentError

Number = int | float

# --- common ---------------------------------------------------------------------

_HTML_TAG = re.compile(r"</?(?:HTML|HEAD|TITLE|BODY|PRE|XMP)[^>]*>", re.IGNORECASE)
_TITLE = re.compile(r"<TITLE>.*?</TITLE>", re.IGNORECASE | re.DOTALL)
_BLOCK = re.compile(
    r"^\[\*(?P<prog>[^*]*)\*\](?P<name>\$\S+)\s+Storage:\s*(?P<storage>\S+)\s+"
    r"Access:\s*(?P<access>\S+)\s+:\s*(?P<type>.*?)(?:\s*=\s*(?P<value>.*?))?\s*$"
)


def normalize(text: str) -> str:
    """LF line endings, no HTTP wrapper, no trailing whitespace at the very end."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "<PRE>" in text.upper() or "<XMP>" in text.upper():
        text = _TITLE.sub("", text)
        text = _HTML_TAG.sub("", text)
    return text


def parse_number(token: str) -> Number | None:
    """``12`` -> 12, ``-.364`` -> -0.364, ``********`` / ``Uninitialized`` -> None."""
    t = token.strip()
    if not t or set(t) == {"*"} or t.lower() == "uninitialized":
        return None
    if re.fullmatch(r"[+-]?\d+", t):
        return int(t)
    try:
        return float(t)
    except ValueError as exc:
        raise ValueError(f"not a controller number: {token!r}") from exc


@dataclass(frozen=True)
class VaBlock:
    """One ``[*PROG*]$NAME  Storage: ..  Access: ..  : TYPE [= value]`` variable block."""

    program: str
    name: str
    storage: str
    access: str
    type: str
    value: str | None
    lines: tuple[str, ...]


def iter_blocks(text: str) -> list[VaBlock]:
    """Split a ``.va`` file into variable blocks (the lines after each header)."""
    blocks: list[VaBlock] = []
    head: re.Match[str] | None = None
    body: list[str] = []

    def flush() -> None:
        if head is not None:
            blocks.append(
                VaBlock(
                    head["prog"],
                    head["name"],
                    head["storage"],
                    head["access"],
                    head["type"].strip(),
                    head["value"],
                    tuple(body),
                )
            )

    for line in normalize(text).split("\n"):
        m = _BLOCK.match(line)
        if m:
            flush()
            head, body = m, []
        elif head is not None:
            body.append(line)
    flush()
    return blocks


# --- numreg.va ------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisterEntry:
    index: int
    value: Number | None
    comment: str


_NUMREG_LINE = re.compile(r"^\s*\[(\d+)\]\s*=\s*(\S+)\s+'(.*)'\s*$")


def parse_numreg(text: str) -> dict[int, RegisterEntry]:
    """``numreg.va`` -> {index: RegisterEntry}. Integers stay ``int``, reals are ``float``."""
    out: dict[int, RegisterEntry] = {}
    for block in iter_blocks(text):
        if block.name.upper() != "$NUMREG":
            continue
        for line in block.lines:
            m = _NUMREG_LINE.match(line)
            if m:
                i = int(m[1])
                out[i] = RegisterEntry(i, parse_number(m[2]), m[3])
    if not out:
        raise ValueError("no $NUMREG entries found")
    return out


# --- positions in .va files ------------------------------------------------------------


@dataclass(frozen=True)
class VaConfig:
    """A configuration string such as ``N U T, 0, 0, 0`` or SCARA ``R, 0, 0, -1``.

    ``None`` means the letter is not part of the string (it differs by robot type).
    """

    raw: str
    flip: bool | None = None
    left: bool | None = None
    up: bool | None = None
    front: bool | None = None
    turns: tuple[int, ...] = ()

    @classmethod
    def parse(cls, raw: str) -> VaConfig:
        parts = [p.strip() for p in raw.split(",")]
        letters = parts[0].split() if parts and parts[0] else []
        flags: dict[str, bool] = {}
        for letter in letters:
            key, value = {
                "F": ("flip", True),
                "N": ("flip", False),
                "L": ("left", True),
                "R": ("left", False),
                "U": ("up", True),
                "D": ("up", False),
                "T": ("front", True),
                "B": ("front", False),
            }.get(letter.upper(), ("", False))
            if key:
                flags[key] = value
        turns = tuple(int(p) for p in parts[1:] if re.fullmatch(r"[+-]?\d+", p))
        return cls(raw.strip(), turns=turns, **flags)


@dataclass(frozen=True)
class VaPosition:
    """A position as printed in ``.va`` files (Cartesian or joint)."""

    group: int | None
    config: VaConfig | None
    xyzwpr: tuple[float | None, ...] | None
    joints: tuple[float | None, ...] | None
    joint_units: tuple[str, ...] = ()
    extended: dict[str, float | None] = field(default_factory=dict)

    @property
    def is_cartesian(self) -> bool:
        return self.xyzwpr is not None


_GROUP = re.compile(r"Group:\s*(\d+)")
_CONFIG = re.compile(r"Config:\s*(.*?)\s*$")
_CART = re.compile(r"(?<![A-Z$])([XYZWPR]):\s*(\S+)")
_EXT = re.compile(r"\b(EXT\d+|E\d+):\s*(\S+)")
_JOINT = re.compile(r"\bJ(\d+)\s*=\s*(\S+)\s*(deg|mm)")


def _parse_position_lines(lines: list[str], group: int | None) -> VaPosition | None:
    config: VaConfig | None = None
    cart: dict[str, float | None] = {}
    joints: dict[int, tuple[float | None, str]] = {}
    ext: dict[str, float | None] = {}
    for line in lines:
        g = _GROUP.search(line)
        if g:
            group = int(g[1])
        c = _CONFIG.search(line)
        if c:
            config = VaConfig.parse(c[1]) if c[1] else None
            continue
        for name, value in _EXT.findall(line):
            v = parse_number(value)
            ext[name] = None if v is None else float(v)
        for axis, value, unit in _JOINT.findall(line):
            v = parse_number(value)
            joints[int(axis)] = (None if v is None else float(v), unit)
        if not _EXT.search(line):
            for name, value in _CART.findall(line):
                v = parse_number(value)
                cart[name] = None if v is None else float(v)
    if not cart and not joints:
        return None
    xyzwpr = tuple(cart.get(k) for k in "XYZWPR") if cart else None
    jt = tuple(joints[k][0] for k in sorted(joints)) if joints else None
    units = tuple(joints[k][1] for k in sorted(joints)) if joints else ()
    return VaPosition(group, config, xyzwpr, jt, units, ext)


@dataclass(frozen=True)
class PositionEntry:
    """One element of a position array (``$POSREG``, ``$MNUFRAME``, ...)."""

    index: tuple[int, ...]
    comment: str | None
    uninitialized: bool
    position: VaPosition | None


_ELEMENT = re.compile(r"^\s*\[(\d+(?:,\d+)*)\]\s*=\s*(.*?)\s*$")
_COMMENT_HEAD = re.compile(r"^'([^']*)'\s*(.*)$")


def parse_position_array(block: VaBlock) -> dict[tuple[int, ...], PositionEntry]:
    """Elements of a position-array block. Keys are the index tuples, e.g. (1, 7)."""
    out: dict[tuple[int, ...], PositionEntry] = {}
    current: tuple[tuple[int, ...], str | None, str] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        index, comment, rest = current
        g = _GROUP.search(rest)
        uninit = "uninitialized" in rest.lower()
        pos = None if uninit else _parse_position_lines(body, int(g[1]) if g else None)
        out[index] = PositionEntry(index, comment, uninit, pos)

    for line in block.lines:
        m = _ELEMENT.match(line)
        if m:
            flush()
            index = tuple(int(x) for x in m[1].split(","))
            rest = m[2]
            cm = _COMMENT_HEAD.match(rest)
            comment = cm[1] if cm else None
            current, body = (index, comment, cm[2] if cm else rest), []
        elif current is not None and line.strip():
            body.append(line)
    flush()
    return out


def parse_posreg(text: str) -> dict[tuple[int, int], PositionEntry]:
    """``posreg.va`` -> {(group, index): PositionEntry}."""
    for block in iter_blocks(text):
        if block.name.upper() == "$POSREG":
            entries = parse_position_array(block)
            return {(k[0], k[1]): v for k, v in entries.items() if len(k) == 2}
    raise ValueError("no $POSREG block found")


def parse_sysframe(text: str) -> dict[str, dict[tuple[int, int], PositionEntry]]:
    """``sysframe.va`` -> {"$MNUFRAME": {(group, n): entry}, "$MNUTOOL": {...}}."""
    out: dict[str, dict[tuple[int, int], PositionEntry]] = {}
    for block in iter_blocks(text):
        name = block.name.upper()
        if name in ("$MNUFRAME", "$MNUTOOL"):
            entries = parse_position_array(block)
            out[name] = {(k[0], k[1]): v for k, v in entries.items() if len(k) == 2}
    if not out:
        raise ValueError("no $MNUFRAME/$MNUTOOL blocks found")
    return out


# --- system variables (Field: lines) -----------------------------------------------------

_FIELD = re.compile(
    r"^\s*Field:\s*(?P<path>\S+)\s+Access:\s*(?P<access>\S+?):\s*(?P<type>[A-Z_]+(?:\[\d+\])?)"
    r"\s*=\s*(?P<value>.*?)\s*$"
)


def parse_field_value(type_name: str, raw: str) -> Any:
    """Typed value of a ``Field:`` line (strings unquoted, ``Uninitialized`` -> None)."""
    if raw == "Uninitialized":
        return None
    t = type_name.upper()
    if t.startswith("STRING"):
        return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == "'" else raw
    if t == "BOOLEAN":
        return raw.upper() == "TRUE"
    if t in ("INTEGER", "SHORT", "BYTE", "REAL", "ULONG", "USHORT"):
        return parse_number(raw)
    return raw


def parse_fields(text: str, prefix: str = "") -> dict[str, Any]:
    """Scalar ``Field: <path> Access: ..: TYPE = value`` lines whose path starts with ``prefix``."""
    out: dict[str, Any] = {}
    for line in normalize(text).split("\n"):
        m = _FIELD.match(line)
        if m and m["path"].upper().startswith(prefix.upper()):
            out[m["path"].upper()] = parse_field_value(m["type"], m["value"])
    return out


@dataclass(frozen=True)
class SnpxSlot:
    slot: int
    address: int
    size: int
    var_name: str
    multiply: float

    @property
    def used(self) -> bool:
        return self.size > 0 and bool(self.var_name)


@dataclass(frozen=True)
class SnpxConfig:
    """``$SNPX_PARAM`` and ``$SNPX_ASG`` as read from ``system.va``."""

    params: dict[str, Any]
    slots: tuple[SnpxSlot, ...]

    @property
    def used_slots(self) -> list[SnpxSlot]:
        return [s for s in self.slots if s.used]

    @property
    def multiplexed(self) -> bool | None:
        """Whether ``CLRASG`` creates a per-connection table (CIMPLICITY manual §6.11.4).

        True only when ``$VERSION`` >= 2 and ``$NUM_CIMP`` > 0; ``None`` if either
        field is missing. When False, ``CLRASG`` erases the shared table.
        """
        version, cimp = self.params.get("$VERSION"), self.params.get("$NUM_CIMP")
        if not isinstance(version, int) or not isinstance(cimp, int):
            return None
        return version >= 2 and cimp > 0

    def to_table(
        self, sysvar_types: dict[str, SysvarType] | None = None
    ) -> tuple[AssignmentTable, list[str]]:
        """Build an :class:`AssignmentTable`; returns (table, problems).

        System-variable entries need their type (``sysvar_types`` maps the
        ``$VAR_NAME`` base, e.g. ``$MNUFRAME[1,1]``, to a :class:`SysvarType`);
        entries that cannot be represented are listed in ``problems``, never dropped silently.
        """
        types = {k.upper(): v for k, v in (sysvar_types or {}).items()}
        entries: list[Assignment] = []
        problems: list[str] = []
        for s in self.used_slots:
            try:
                var = VarName.parse(s.var_name)
                st = types.get(var.sysvar or "") if var.family == "$" else None
                entry = Assignment(s.address, s.size, var, s.multiply, s.slot, st)
                _ = entry.words_per_element  # raises for forms this package cannot decode
                entries.append(entry)
            except (ValueError, SnpxAssignmentError, NotImplementedError) as exc:
                problems.append(f"slot {s.slot} {s.var_name!r}: {exc}")
        return AssignmentTable(entries), problems

    def to_markdown(self) -> str:
        lines = [
            "| Slot | $ADDRESS | $SIZE | $VAR_NAME | $MULTIPLY | %R range |",
            "|---|---|---|---|---|---|",
        ]
        for s in self.used_slots:
            lines.append(
                f"| {s.slot} | {s.address} | {s.size} | `{s.var_name}` | {s.multiply:g} | "
                f"%R{s.address}..%R{s.address + s.size - 1} |"
            )
        if len(lines) == 2:
            lines.append("| (none) | | | | | |")
        params = ", ".join(f"{k.split('.')[-1]}={v!r}" for k, v in self.params.items())
        return f"$SNPX_PARAM: {params}\n\n" + "\n".join(lines) + "\n"


def parse_snpx_config(text: str) -> SnpxConfig:
    """Read ``$SNPX_PARAM`` and every ``$SNPX_ASG[n]`` slot from ``system.va``."""
    fields = parse_fields(text, "$SNPX_")
    params = {
        k.removeprefix("$SNPX_PARAM."): v for k, v in fields.items() if k.startswith("$SNPX_PARAM.")
    }
    slots: dict[int, dict[str, Any]] = {}
    for path, value in fields.items():
        m = re.match(r"^\$SNPX_ASG\[(\d+)\]\.\$(ADDRESS|SIZE|VAR_NAME|MULTIPLY)$", path)
        if m:
            slots.setdefault(int(m[1]), {})[m[2]] = value
    if not slots and not params:
        raise ValueError("no $SNPX_ASG / $SNPX_PARAM fields found (they are in system.va)")
    out = []
    for n in sorted(slots):
        f = slots[n]
        out.append(
            SnpxSlot(
                slot=n,
                address=int(f.get("ADDRESS") or 0),
                size=int(f.get("SIZE") or 0),
                var_name=str(f.get("VAR_NAME") or ""),
                multiply=float(f["MULTIPLY"]) if f.get("MULTIPLY") is not None else 1.0,
            )
        )
    return SnpxConfig(params, tuple(out))


# --- curpos.dg -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DgCartesian:
    config: VaConfig | None
    xyzwpr: tuple[float | None, ...]


@dataclass(frozen=True)
class CurrentPositionDg:
    group: int
    joints: tuple[float | None, ...]
    user_frame: int | None
    tool: int | None
    user: DgCartesian | None
    world: DgCartesian | None


def _dg_cartesian(lines: list[str]) -> DgCartesian | None:
    values: dict[str, float | None] = {}
    config: VaConfig | None = None
    for line in lines:
        s = line.strip()
        if s.startswith("CFG:"):
            config = VaConfig.parse(s[4:])
            continue
        m = re.match(r"^([XYZWPR]):\s*(\S+)", s)
        if m:
            v = parse_number(m[2])
            values[m[1]] = None if v is None else float(v)
    if not values:
        return None
    return DgCartesian(config, tuple(values.get(k) for k in "XYZWPR"))


def parse_curpos(text: str) -> dict[int, CurrentPositionDg]:
    """``curpos.dg`` (or section 7 of ``summary.dg``) -> {group: CurrentPositionDg}."""
    lines = normalize(text).split("\n")
    starts = [i for i, ln in enumerate(lines) if re.match(r"^\s*Group #:\s*\d+", ln)]
    if not starts:
        raise ValueError("no 'Group #:' section found")
    out: dict[int, CurrentPositionDg] = {}
    for n, start in enumerate(starts):
        chunk = lines[start : starts[n + 1] if n + 1 < len(starts) else len(lines)]
        group = int(re.findall(r"\d+", chunk[0])[0])
        joints: dict[int, float | None] = {}
        frame = tool = None
        section = ""
        user_lines: list[str] = []
        world_lines: list[str] = []
        for ln in chunk[1:]:
            s = ln.strip()
            jm = re.match(r"^Joint\s+(\d+):\s*(\S+)", s)
            if jm:
                v = parse_number(jm[2])
                joints[int(jm[1])] = None if v is None else float(v)
                continue
            fm = re.match(r"^Frame #:\s*(\d+)\s+Tool #:\s*(\d+)", s)
            if fm:
                frame, tool = int(fm[1]), int(fm[2])
                continue
            if s.startswith("CURRENT USER FRAME POSITION"):
                section = "user"
            elif s.startswith("CURRENT WORLD POSITION"):
                section = "world"
            elif s.startswith("CURRENT") or s.startswith("<"):
                section = ""
            elif section == "user":
                user_lines.append(s)
            elif section == "world":
                world_lines.append(s)
        out[group] = CurrentPositionDg(
            group,
            tuple(joints[k] for k in sorted(joints)),
            frame,
            tool,
            _dg_cartesian(user_lines),
            _dg_cartesian(world_lines),
        )
    return out


# --- errall.ls ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AlarmRecord:
    sequence: int
    time: str
    message: str
    cause: str
    severity: str
    mask: str
    active: bool


def parse_errall(text: str) -> list[AlarmRecord]:
    """``errall.ls`` -> alarm history, newest first (as the file lists it)."""
    out: list[AlarmRecord] = []
    for line in normalize(text).split("\n"):
        if not line.strip() or line.startswith("ERRALL.LS"):
            continue
        parts = line.split('"')
        if len(parts) < 6 or not parts[0].strip().isdigit():
            continue
        sev_mask = parts[4].rstrip()
        mask = sev_mask[-8:] if re.search(r"[01]{8}$", sev_mask) else ""
        out.append(
            AlarmRecord(
                sequence=int(parts[0].strip()),
                time=parts[1].strip(),
                message=parts[2].strip(),
                cause=parts[3].strip(),
                severity=sev_mask[: len(sev_mask) - len(mask)].strip(),
                mask=mask,
                active=parts[5].strip() == "act",
            )
        )
    return out


# --- iostate.dg --------------------------------------------------------------------------------

_IO_ITEM = re.compile(r"\b([A-Z]+)\[\s*(\d+)\]\s+(ON|OFF|-?\d+)\b")


def parse_iostate(text: str) -> dict[tuple[str, int], bool | int]:
    """``iostate.dg`` -> {("DIN", 81): True, ("GIN", 1): 0, ...} using the file's own names."""
    out: dict[tuple[str, int], bool | int] = {}
    for line in normalize(text).split("\n"):
        for name, index, value in _IO_ITEM.findall(line):
            out[(name, int(index))] = (value == "ON") if value in ("ON", "OFF") else int(value)
    return out


def parse_strreg(text: str) -> dict[int, str]:
    raise NotImplementedError(
        "strreg.va is not parsed yet: the layout of a stored string value is unverified; "
        "add a parser from a real file of the target controller"
    )
