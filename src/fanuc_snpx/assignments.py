"""The SNPX assignment table (``$SNPX_ASG``): naming, layout, lookup and management.

A FANUC controller has no named-variable access over SRTP. Controller data
appears in the flat %R word area only where an assignment maps it. Each of the
80 assignment slots holds:

* ``$ADDRESS``: first %R address (1-based),
* ``$SIZE``: number of %R words,
* ``$VAR_NAME``: what is mapped, for example ``R[1]``, ``PR[G1:7]``, ``POS[0]``,
  ``$MNUFRAME[1,2]`` or ``R[C1]``, optionally sliced with ``@first.count``,
* ``$MULTIPLY``: 0 = 32-bit IEEE real; non-zero = 32-bit integer scaled by it
  (strings and comments use 1; for I/O, 1 = one word per signal, 0 = 16 signals
  per word).

When two slots overlap, the lower-numbered slot wins. Unassigned %R reads 0.

:class:`AssignmentTable` describes a table (the controller's, or one this client
created) and answers "which %R words hold R[7]?". :class:`AssignmentManager`
creates session-scoped assignments with the %G ``CLRASG``/``SETASG`` commands;
that is a controller change and goes through the access policy.

Sources: FANUC B-82604EN/01 chapter 6 (CIMPLICITY HMI for robots) and the
public clients listed in docs/PROVENANCE.md. Verification status: docs/PROTOCOL.md.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .errors import SnpxAssignmentError, WriteNotAllowed

if TYPE_CHECKING:
    from .client import SnpxClient

MAX_SLOTS = 80
MAX_VAR_NAME = 37
MAX_R_ADDRESS = 16384

IO_FAMILIES = (
    "DI",
    "DO",
    "RI",
    "RO",
    "UI",
    "UO",
    "SI",
    "SO",
    "WI",
    "WO",
    "WSI",
    "WSO",
    "GI",
    "GO",
    "AI",
    "AO",
)
WORD_IO_FAMILIES = frozenset({"GI", "GO", "AI", "AO"})
REGISTER_FAMILIES = ("R", "PR", "SR", "POS", "ALM", "PRG", "F")


class SysvarType(Enum):
    """Data type of a system variable, which fixes its size in %R words."""

    INTEGER = "INTEGER"
    SHORT = "SHORT"
    BYTE = "BYTE"
    REAL = "REAL"
    BOOLEAN = "BOOLEAN"
    STRING = "STRING"
    POSITION = "POSITION"

    @property
    def words(self) -> int:
        return {SysvarType.STRING: 40, SysvarType.POSITION: 50}.get(self, 2)


KNOWN_ARRAY_DIMS: dict[str, int] = {
    "$MNUFRAME": 2,
    "$MNUTOOL": 2,
    "$MNUFRAMENUM": 1,
    "$MNUTOOLNUM": 1,
}
"""System-variable arrays whose index count is checked.

A one-index spelling of a two-index array (``$MNUFRAME[2]``) can be accepted by a
controller and then target nothing, so names of these arrays must carry every
index. Add entries with :func:`register_array_dims`.
"""


def register_array_dims(name: str, dims: int) -> None:
    """Declare that system-variable array ``name`` needs ``dims`` indexes."""
    if not name.startswith("$") or dims < 1:
        raise ValueError("name must start with '$' and dims must be >= 1")
    KNOWN_ARRAY_DIMS[name.upper()] = dims


_REG_RE = re.compile(
    r"^(?P<fam>WSI|WSO|POS|ALM|PRG|PR|SR|DI|DO|RI|RO|UI|UO|SI|SO|WI|WO|GI|GO|AI|AO|R|F)"
    r"\[(?P<sub>MK|[CSEPMK])?(?:G(?P<group>\d+):)?(?P<index>\d+)\]"
    r"(?:@(?P<off>\d+)\.(?P<len>\d+))?$"
)
_SYSVAR_RE = re.compile(
    r"^(?P<name>\$(?:\[[A-Z0-9_]+\])?[A-Z0-9_$]+(?:\[\d+(?:,\d+)*\])?"
    r"(?:\.\$?[A-Z0-9_]+(?:\[\d+(?:,\d+)*\])?)*)"
    r"(?:@(?P<off>\d+)\.(?P<len>\d+))?$"
)
_ARRAY_RE = re.compile(r"^(\$[A-Z0-9_]+)\[(\d+(?:,\d+)*)\]")


def canonical_sysvar(name: str) -> str:
    """Validate and upper-case a system-variable name, rejecting ambiguous spellings.

    Raises :class:`ValueError` for blanks, malformed brackets, or a known array
    spelled with the wrong number of indexes.
    """
    if not isinstance(name, str) or not name.startswith("$"):
        raise ValueError(f"system variable names start with '$': {name!r}")
    if any(c.isspace() for c in name):
        raise ValueError(f"system variable names must not contain blanks: {name!r}")
    n = name.upper()
    m = _SYSVAR_RE.match(n)
    if m is None or m["off"] is not None:
        raise ValueError(f"malformed system variable name {name!r}")
    head = re.match(r"^(\$[A-Z0-9_]+)", n)
    base = head[1] if head else n
    if base in KNOWN_ARRAY_DIMS:
        arr = _ARRAY_RE.match(n)
        got = len(arr[2].split(",")) if arr else 0
        want = KNOWN_ARRAY_DIMS[base]
        if got != want:
            raise ValueError(
                f"{name!r}: {base} needs {want} index(es), e.g. {base}[{','.join(['1'] * want)}]; "
                "a shorter spelling may be accepted by the controller and write nothing"
            )
    return n


@dataclass(frozen=True)
class VarName:
    """A parsed ``$VAR_NAME`` value.

    ``family`` is ``R``, ``PR``, ``SR``, ``POS``, ``ALM``, ``PRG``, ``F``, an I/O
    family such as ``DO``, or ``$`` for system and KAREL variables. ``sub`` is
    ``C`` (comment), ``S`` (I/O SIM status), an alarm/program qualifier, or "".
    ``index`` is the first element number (the user frame number for POS).
    ``slice`` is the ``@first.count`` suffix (1-based words inside one element).
    """

    family: str
    index: int = 0
    sub: str = ""
    group: int | None = None
    sysvar: str | None = None
    slice: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.slice is not None:
            first, count = self.slice
            if first < 1 or count < 1:
                raise ValueError(f"invalid slice @{first}.{count}")
        text = str(self)
        if len(text) > MAX_VAR_NAME:
            raise ValueError(f"$VAR_NAME {text!r} is longer than {MAX_VAR_NAME} characters")

    @classmethod
    def parse(cls, text: str) -> VarName:
        if not isinstance(text, str) or not text or any(c.isspace() for c in text):
            raise ValueError(f"$VAR_NAME must be non-empty without blanks: {text!r}")
        t = text.upper()
        if t.startswith("$"):
            m = _SYSVAR_RE.match(t)
            if m is None:
                raise ValueError(f"malformed system variable $VAR_NAME {text!r}")
            sl = (int(m["off"]), int(m["len"])) if m["off"] else None
            return cls("$", sysvar=canonical_sysvar(m["name"]), slice=sl)
        m = _REG_RE.match(t)
        if m is None:
            raise ValueError(f"unsupported $VAR_NAME {text!r}")
        fam, sub = m["fam"], m["sub"] or ""
        allowed_sub = {
            "R": {"", "C"},
            "PR": {"", "C"},
            "SR": {""},
            "POS": {""},
            "ALM": {"", "E", "P"},
            "PRG": {"", "M", "K", "MK"},
            "F": {""},
        }.get(fam, {"", "C", "S"})
        if sub not in allowed_sub:
            raise ValueError(f"{fam}[{sub}...] is not a valid $VAR_NAME form")
        if m["group"] is not None and fam not in ("PR", "POS"):
            raise ValueError(f"group prefix is only valid for PR and POS: {text!r}")
        sl = (int(m["off"]), int(m["len"])) if m["off"] else None
        group = int(m["group"]) if m["group"] is not None else None
        return cls(fam, int(m["index"]), sub, group, None, sl)

    def __str__(self) -> str:
        suffix = f"@{self.slice[0]}.{self.slice[1]}" if self.slice else ""
        if self.family == "$":
            return f"{self.sysvar}{suffix}"
        g = f"G{self.group}:" if self.group is not None else ""
        return f"{self.family}[{self.sub}{g}{self.index}]{suffix}"

    @property
    def is_comment(self) -> bool:
        return self.sub == "C"

    @property
    def is_io_value(self) -> bool:
        return self.family in IO_FAMILIES and self.sub in ("", "S")

    @property
    def effective_group(self) -> int:
        return self.group if self.group is not None else 1


def full_element_words(var: VarName, sysvar_type: SysvarType | None = None) -> int:
    """Words in one full element structure of ``var`` (before any ``@`` slice)."""
    if var.is_comment:
        return 40
    if var.family == "$":
        if sysvar_type is None:
            raise SnpxAssignmentError(f"{var}: the system variable type is needed to size it")
        return sysvar_type.words
    if var.family in IO_FAMILIES:
        return 1
    if var.family == "F":
        raise NotImplementedError("flag (F[]) assignments are not documented; not implemented")
    return {"R": 2, "PR": 50, "POS": 50, "SR": 40, "ALM": 100, "PRG": 18}[var.family]


def default_multiply(var: VarName, sysvar_type: SysvarType | None = None) -> float:
    """The multiplier this package uses when creating an assignment for ``var``."""
    if var.is_comment or var.family in ("SR", "ALM", "PRG") or var.family in IO_FAMILIES:
        return 1.0
    if var.family == "$" and sysvar_type in (SysvarType.STRING, SysvarType.BOOLEAN):
        return 1.0
    return 0.0  # reals as IEEE float32 (R, PR, POS, REAL/INTEGER/POSITION sysvars)


@dataclass(frozen=True)
class Assignment:
    """One ``$SNPX_ASG`` entry."""

    address: int
    size: int
    var: VarName
    multiply: float = 1.0
    slot: int | None = None
    sysvar_type: SysvarType | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.address <= MAX_R_ADDRESS:
            raise SnpxAssignmentError(f"$ADDRESS {self.address} outside 1..{MAX_R_ADDRESS}")
        if not 1 <= self.size <= MAX_R_ADDRESS:
            raise SnpxAssignmentError(f"$SIZE {self.size} outside 1..{MAX_R_ADDRESS}")
        if self.slot is not None and not 1 <= self.slot <= MAX_SLOTS:
            raise SnpxAssignmentError(f"slot {self.slot} outside 1..{MAX_SLOTS}")
        if self.multiply != 0 and not 0.0001 <= abs(self.multiply) <= 10000:
            raise SnpxAssignmentError(f"$MULTIPLY {self.multiply} outside 0.0001..10000 or 0")
        if self.var.family == "$" and self.sysvar_type is None:
            raise SnpxAssignmentError(f"{self.var}: give sysvar_type for system variables")
        if self.var.slice is not None:
            first, count = self.var.slice
            full = full_element_words(self.var, self.sysvar_type)
            if first + count - 1 > full:
                raise SnpxAssignmentError(f"{self.var}: slice exceeds the {full}-word element")

    @property
    def last_address(self) -> int:
        return self.address + self.size - 1

    @property
    def packed_io(self) -> bool:
        """I/O values or SIM states packed 16 per word ($MULTIPLY = 0)."""
        return (
            self.var.is_io_value and self.var.family not in WORD_IO_FAMILIES and self.multiply == 0
        )

    @property
    def words_per_element(self) -> int:
        if self.var.slice is not None:
            return self.var.slice[1]
        return full_element_words(self.var, self.sysvar_type)

    @property
    def slice_first_word(self) -> int:
        return self.var.slice[0] if self.var.slice is not None else 1

    @property
    def element_count(self) -> int:
        if self.var.family == "POS":
            return 1  # the controller never assigns consecutive POS[] elements
        if self.packed_io:
            return self.size * 16
        return self.size // self.words_per_element

    def overlaps(self, address: int, words: int) -> bool:
        return address <= self.last_address and self.address <= address + words - 1

    def command(self) -> str:
        """The ``SETASG`` command text that creates this entry."""
        return f"SETASG {self.address} {self.size} {self.var} {_fmt_multiply(self.multiply)}"

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "address": self.address,
            "size": self.size,
            "var_name": str(self.var),
            "multiply": self.multiply,
        }
        if self.slot is not None:
            out["slot"] = self.slot
        if self.sysvar_type is not None:
            out["sysvar_type"] = self.sysvar_type.value
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Assignment:
        st = data.get("sysvar_type")
        return cls(
            address=int(data["address"]),
            size=int(data["size"]),
            var=VarName.parse(str(data["var_name"])),
            multiply=float(data.get("multiply", 1.0)),
            slot=int(data["slot"]) if data.get("slot") is not None else None,
            sysvar_type=SysvarType(st) if st else None,
        )


def _fmt_multiply(m: float) -> str:
    return str(int(m)) if float(m).is_integer() else f"{m:g}"


@dataclass(frozen=True)
class Located:
    """Where one element lives: its assignment and its first %R address."""

    assignment: Assignment
    element: int
    address: int

    @property
    def words(self) -> int:
        return self.assignment.words_per_element


@dataclass(frozen=True)
class Run:
    """Consecutive elements of one assignment, readable with one ranged %R read."""

    assignment: Assignment
    first_element: int
    count: int
    address: int

    @property
    def words(self) -> int:
        return self.count * self.assignment.words_per_element


@dataclass
class AssignmentTable:
    """An ordered set of assignments (slot order decides overlaps)."""

    entries: list[Assignment] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.entries) > MAX_SLOTS:
            raise SnpxAssignmentError(f"more than {MAX_SLOTS} assignments")
        slots = [e.slot for e in self.entries if e.slot is not None]
        if len(slots) != len(set(slots)):
            raise SnpxAssignmentError("duplicate slot numbers")
        self.entries = sorted(
            self.entries,
            key=lambda e: e.slot if e.slot is not None else MAX_SLOTS + 1,
        )

    def __iter__(self) -> Iterator[Assignment]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def factory_default(cls) -> AssignmentTable:
        """The shipping default: slot 1 maps %R1..%R10000 to R[1..10000] as 16-bit ints."""
        return cls([Assignment(1, 10000, VarName.parse("R[1]@1.1"), 1.0, slot=1)])

    # -- persistence -----------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(
            {"version": 1, "assignments": [e.to_json() for e in self.entries]}, indent=2
        )

    @classmethod
    def from_json(cls, text: str) -> AssignmentTable:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise SnpxAssignmentError("assignment map must be an object with version 1")
        return cls([Assignment.from_json(x) for x in data.get("assignments", [])])

    @classmethod
    def load(cls, path: str | Path) -> AssignmentTable:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    # -- lookup --------------------------------------------------------------------

    def overlapping(self, address: int, words: int) -> list[Assignment]:
        return [e for e in self.entries if e.overlaps(address, words)]

    def _shadowed(self, entry: Assignment, address: int, words: int) -> bool:
        for other in self.entries:
            if other is entry:
                return False
            if other.overlaps(address, words):
                return True
        return False

    def _matches(self, e: Assignment, family: str, sub: str, group: int) -> bool:
        return (
            e.var.family == family
            and e.var.sub == sub
            and (family not in ("PR", "POS") or e.var.effective_group == group)
        )

    def locate(self, family: str, index: int, *, sub: str = "", group: int = 1) -> Located:
        """Find the %R words of element ``family[index]``.

        Raises :class:`SnpxAssignmentError` when no unshadowed assignment covers it.
        """
        shadowed_by: list[Assignment] = []
        for e in self.entries:
            if not self._matches(e, family, sub, group):
                continue
            k = index - e.var.index
            if e.packed_io:
                raise SnpxAssignmentError(f"{e.var} packs 16 signals per word; read I/O directly")
            if not 0 <= k < e.element_count:
                continue
            addr = e.address + k * e.words_per_element
            if self._shadowed(e, addr, e.words_per_element):
                shadowed_by.append(e)
                continue
            return Located(e, k, addr)
        what = f"{family}[{sub}{f'G{group}:' if family in ('PR', 'POS') else ''}{index}]"
        if shadowed_by:
            raise SnpxAssignmentError(
                f"{what} is assigned by {shadowed_by[0].var}, "
                "but a lower slot overlaps those %R words"
            )
        raise SnpxAssignmentError(f"no assignment covers {what}")

    def plan_runs(
        self, family: str, first: int, count: int, *, sub: str = "", group: int = 1
    ) -> list[Run]:
        """Group ``family[first .. first+count-1]`` into contiguous ranged reads."""
        if count < 1:
            raise ValueError("count must be >= 1")
        runs: list[Run] = []
        for index in range(first, first + count):
            loc = self.locate(family, index, sub=sub, group=group)
            last = runs[-1] if runs else None
            if (
                last is not None
                and last.assignment is loc.assignment
                and last.first_element + last.count == loc.element
            ):
                runs[-1] = replace(last, count=last.count + 1)
            else:
                runs.append(Run(loc.assignment, loc.element, 1, loc.address))
        return runs

    def locate_sysvar(self, name: str) -> Located:
        """Find the %R words of system variable ``name`` (full canonical name)."""
        want = canonical_sysvar(name)
        for e in self.entries:
            if e.var.family != "$" or e.var.sysvar is None:
                continue
            k = _array_distance(e.var.sysvar, want)
            if k is None or not 0 <= k < e.element_count:
                continue
            addr = e.address + k * e.words_per_element
            if self._shadowed(e, addr, e.words_per_element):
                raise SnpxAssignmentError(f"{want} is shadowed by a lower slot")
            return Located(e, k, addr)
        raise SnpxAssignmentError(f"no assignment covers {want}")


def _array_distance(base: str, want: str) -> int | None:
    """How many elements past ``base`` the array element ``want`` is, along the last index."""
    if base == want:
        return 0
    mb = re.match(r"^(.*\[(?:\d+,)*)(\d+)\]$", base)
    mw = re.match(r"^(.*\[(?:\d+,)*)(\d+)\]$", want)
    if mb is None or mw is None or mb[1] != mw[1]:
        return None
    return int(mw[2]) - int(mb[2])


# --- creating assignments ----------------------------------------------------------


class Span(Protocol):
    """Anything occupying %R words: an :class:Assignment or a raw slot read from a file."""

    @property
    def address(self) -> int: ...

    @property
    def last_address(self) -> int: ...

    def overlaps(self, address: int, words: int) -> bool: ...


@dataclass(frozen=True)
class AssignmentRequest:
    """What to map: ``count`` consecutive elements starting at ``var``."""

    var: VarName
    count: int = 1
    multiply: float | None = None
    sysvar_type: SysvarType | None = None

    @classmethod
    def of(
        cls,
        var: str | VarName,
        count: int = 1,
        *,
        multiply: float | None = None,
        sysvar_type: SysvarType | None = None,
    ) -> AssignmentRequest:
        v = VarName.parse(var) if isinstance(var, str) else var
        return cls(v, count, multiply, sysvar_type)

    @classmethod
    def parse_spec(cls, spec: str) -> AssignmentRequest:
        """Parse ``"PR[1] 300"``, ``"$MNUFRAME[1,1] 9 POSITION"``, ``"R[1] 200 mult=1"``.

        Tokens after the variable: a count, a :class:`SysvarType` name, ``mult=<x>``.
        """
        tokens = spec.split()
        if not tokens:
            raise ValueError("empty assignment spec")
        count, multiply, sysvar_type = 1, None, None
        for tok in tokens[1:]:
            if tok.isdigit():
                count = int(tok)
            elif tok.lower().startswith("mult="):
                multiply = float(tok[5:])
            elif tok.upper() in SysvarType.__members__:
                sysvar_type = SysvarType[tok.upper()]
            else:
                raise ValueError(f"unknown token {tok!r} in {spec!r}")
        return cls.of(tokens[0], count, multiply=multiply, sysvar_type=sysvar_type)


def plan_assignments(
    requests: Sequence[AssignmentRequest],
    block: tuple[int, int],
    *,
    avoid: Iterable[Span] = (),
    slots: Sequence[int] | None = None,
) -> list[Assignment]:
    """Allocate ``requests`` back to back inside the %R ``block`` (inclusive).

    ``slots`` gives the ``$SNPX_ASG`` slot numbers to use, in order (default
    1, 2, ...; use :func:`free_slots` when adding to an existing table).
    Raises if the block is too small, if more than 80 entries result, or if any
    planned entry overlaps an assignment in ``avoid`` (entries this client did
    not create).
    """
    first, last = block
    if not 1 <= first <= last <= MAX_R_ADDRESS:
        raise SnpxAssignmentError(f"invalid %R block {block}")
    if len(requests) > MAX_SLOTS:
        raise SnpxAssignmentError(f"at most {MAX_SLOTS} assignments")
    numbers = list(slots) if slots is not None else list(range(1, len(requests) + 1))
    if len(numbers) < len(requests):
        raise SnpxAssignmentError(f"{len(requests)} requests but only {len(numbers)} slots")
    foreign = list(avoid)
    out: list[Assignment] = []
    address = first
    for slot, req in zip(numbers[: len(requests)], requests, strict=True):
        if req.count < 1:
            raise SnpxAssignmentError(f"{req.var}: count must be >= 1")
        if req.var.family == "POS" and req.count != 1:
            raise SnpxAssignmentError("POS[] cannot be assigned consecutively; one per request")
        mult = (
            req.multiply if req.multiply is not None else default_multiply(req.var, req.sysvar_type)
        )
        probe = Assignment(address, 1, req.var, mult, slot, req.sysvar_type)
        size = -(-req.count // 16) if probe.packed_io else probe.words_per_element * req.count
        entry = Assignment(address, size, req.var, mult, slot, req.sysvar_type)
        if entry.last_address > last:
            total = sum(request_words(r) for r in requests)
            raise SnpxAssignmentError(
                f"{req.var} x{req.count} needs %R{address}..%R{entry.last_address}, "
                f"beyond the block end %R{last}; the whole plan needs {total} words, "
                f"the block has {last - first + 1}"
            )
        clash = [f for f in foreign if f.overlaps(entry.address, entry.size)]
        if clash:
            raise SnpxAssignmentError(
                f"{entry.var} at %R{entry.address}..%R{entry.last_address} overlaps "
                f"existing assignment {getattr(clash[0], 'var', '?')} "
                f"at %R{clash[0].address}..%R{clash[0].last_address}"
            )
        out.append(entry)
        address = entry.last_address + 1
    return out


def request_words(req: AssignmentRequest) -> int:
    """%R words a request occupies once planned."""
    mult = req.multiply if req.multiply is not None else default_multiply(req.var, req.sysvar_type)
    probe = Assignment(1, 1, req.var, mult, None, req.sysvar_type)
    return -(-req.count // 16) if probe.packed_io else probe.words_per_element * req.count


def free_slots(used: Iterable[int], n: int) -> list[int]:
    """The `n` lowest slot numbers (1..80) not in `used`."""
    taken = set(used)
    free = [s for s in range(1, MAX_SLOTS + 1) if s not in taken]
    if len(free) < n:
        raise SnpxAssignmentError(f"need {n} free $SNPX_ASG slots, only {len(free)} are free")
    return free[:n]


class AssignmentManager:
    """Creates session-scoped assignments for a client and keeps track of them.

    Only the session-scoped mode is implemented: ``CLRASG`` gives this
    connection its own private table (controllers with ``$SNPX_PARAM.$VERSION``
    >= 2 and multi-connection enabled), the entries are created with
    ``SETASG``, and the controller drops them when the connection closes. They
    are re-created automatically on every new session.

    On a controller *without* multiplexed assignments, ``CLRASG`` erases the
    shared, persistent ``$SNPX_ASG`` table instead. The caller must therefore
    confirm, from the controller's own ``$SNPX_PARAM``, that multiplexing is on
    (``multiplex_confirmed=True``). See docs/SAFETY.md.
    """

    def __init__(self, client: SnpxClient) -> None:
        self._client = client
        self._created: list[Assignment] = []
        self._hook_installed = False

    @property
    def created(self) -> tuple[Assignment, ...]:
        return tuple(self._created)

    def plan(
        self, requests: Sequence[AssignmentRequest], block: tuple[int, int] | None = None
    ) -> list[Assignment]:
        """Allocate addresses for ``requests`` without touching the controller."""
        b = block or (self._client.policy.assignment_block if self._client.policy else None)
        if b is None:
            raise SnpxAssignmentError("give a %R block, or set assignments.r_block in the policy")
        return plan_assignments(requests, b)

    def apply_session(
        self,
        plan: Sequence[Assignment],
        *,
        reason: str,
        multiplex_confirmed: bool,
    ) -> AssignmentTable:
        """Send ``CLRASG`` + ``SETASG`` for ``plan`` and make it the client's table."""
        if not multiplex_confirmed:
            raise WriteNotAllowed(
                "CLRASG would erase the controller's shared $SNPX_ASG table unless multiplexed "
                "assignments are enabled; confirm $SNPX_PARAM first (docs/SAFETY.md)"
            )
        if not plan:
            raise SnpxAssignmentError("empty assignment plan")
        addresses = sorted({a for e in plan for a in range(e.address, e.last_address + 1)})
        auth = self._client._authorize("snpx_assignments", addresses, reason)
        commands = ["CLRASG", *(e.command() for e in plan)]
        self._client._send_commands(commands, auth)
        self._created = list(plan)
        self._client._set_table(AssignmentTable(list(plan)))
        if not self._hook_installed:
            self._client._session.add_open_hook(self._reapply)
            self._hook_installed = True
        return self._client.table

    def _reapply(self, session: object) -> None:
        if not self._created:
            return
        auth = self._client._authorize(
            "snpx_assignments",
            sorted({a for e in self._created for a in range(e.address, e.last_address + 1)}),
            "re-create session-scoped assignments on a new connection",
        )
        self._client._send_commands(["CLRASG", *(e.command() for e in self._created)], auth)

    def release(self) -> None:
        """Forget the session-scoped entries and close the session (the controller drops them)."""
        if self._hook_installed:
            self._client._session.remove_open_hook(self._reapply)
            self._hook_installed = False
        self._created = []
        self._client._restore_declared_table()
        self._client._session.close()
