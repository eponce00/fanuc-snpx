"""Access policy: which controller data this client may write, loaded from a local file.

A write needs all of the following, checked in this order:

1. the client was created with ``allow_writes=True`` and a policy,
2. the policy is bound to the same controller host (when it names one),
3. the target is within the policy ``limits``,
4. the target is not ``protected``,
5. the target is inside a ``writable`` range,
6. a non-empty reason.

The policy file is the user's, lives next to their application (default
``./robot-policy.local.json``), and must never be committed. See docs/POLICY.md.
"""

from __future__ import annotations

import fnmatch
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import PolicyFileError, PolicyViolation, WriteNotAllowed
from .srtp import WriteAuthorization

POLICY_ENV_VAR = "FANUC_SNPX_POLICY"
DEFAULT_POLICY_FILE = "robot-policy.local.json"
SCHEMA_VERSION = 1

INDEXED_KINDS = frozenset(
    {
        "numeric_registers",
        "position_registers",
        "string_registers",
        "flags",
        "digital_inputs",
        "digital_outputs",
        "robot_inputs",
        "robot_outputs",
        "uop_inputs",
        "uop_outputs",
        "sop_inputs",
        "sop_outputs",
        "weld_inputs",
        "weld_outputs",
        "wire_stick_inputs",
        "wire_stick_outputs",
        "group_inputs",
        "group_outputs",
        "analog_inputs",
        "analog_outputs",
        "snpx_assignments",
    }
)
"""Kinds whose targets are integer indexes (``snpx_assignments`` uses %R addresses)."""

NAMED_KINDS = frozenset({"system_variables"})
"""Kinds whose targets are names; entries may be exact names or ``fnmatch`` globs."""

COMMENT_PREFIX = "comments."

LIMIT_KEYS: Mapping[str, str] = {
    "max_numeric_register": "numeric_registers",
    "max_position_register": "position_registers",
    "max_string_register": "string_registers",
    "max_flag": "flags",
    "max_digital_input": "digital_inputs",
    "max_digital_output": "digital_outputs",
    "max_group_input": "group_inputs",
    "max_group_output": "group_outputs",
}


def is_known_kind(kind: str) -> bool:
    if kind in INDEXED_KINDS or kind in NAMED_KINDS:
        return True
    return kind.startswith(COMMENT_PREFIX) and kind[len(COMMENT_PREFIX) :] in INDEXED_KINDS


def _is_indexed(kind: str) -> bool:
    return kind in INDEXED_KINDS or kind.startswith(COMMENT_PREFIX)


@dataclass(frozen=True)
class RangeSet:
    """Inclusive integer ranges, or everything (``"*"``)."""

    ranges: tuple[tuple[int, int], ...] = ()
    everything: bool = False

    def __contains__(self, index: object) -> bool:
        if not isinstance(index, int) or isinstance(index, bool):
            return False
        return self.everything or any(lo <= index <= hi for lo, hi in self.ranges)

    @classmethod
    def parse(cls, value: object, where: str) -> RangeSet:
        if value == "*":
            return cls(everything=True)
        if not isinstance(value, list):
            raise PolicyFileError(f"{where}: expected '*' or a list of ranges, got {value!r}")
        ranges: list[tuple[int, int]] = []
        for item in value:
            if isinstance(item, int) and not isinstance(item, bool):
                lo = hi = item
            elif (
                isinstance(item, list)
                and len(item) == 2
                and all(isinstance(x, int) and not isinstance(x, bool) for x in item)
            ):
                lo, hi = item
            else:
                raise PolicyFileError(f"{where}: range must be N or [first, last], got {item!r}")
            if lo < 0 or hi < lo:
                raise PolicyFileError(f"{where}: invalid range {item!r}")
            ranges.append((lo, hi))
        return cls(tuple(ranges))


def _glob(pattern: str) -> str:
    """Make ``*`` and ``?`` the only wildcards; brackets in names match literally."""
    return "".join({"[": "[[]", "]": "[]]"}.get(c, c) for c in pattern.upper())


@dataclass(frozen=True)
class NameSet:
    """System-variable names or globs, or everything (``"*"``).

    Matching ignores case. Only ``*`` and ``?`` are wildcards, so
    ``$MNUFRAME[1,2]`` matches exactly that name.
    """

    patterns: tuple[str, ...] = ()
    everything: bool = False

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        n = name.upper()
        return self.everything or any(fnmatch.fnmatchcase(n, _glob(p)) for p in self.patterns)

    @classmethod
    def parse(cls, value: object, where: str) -> NameSet:
        if value == "*":
            return cls(everything=True)
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            raise PolicyFileError(f"{where}: expected '*' or a list of names, got {value!r}")
        for name in value:
            if not name.startswith("$"):
                raise PolicyFileError(f"{where}: system variable names start with '$': {name!r}")
            if " " in name:
                raise PolicyFileError(f"{where}: names must not contain blanks: {name!r}")
        return cls(tuple(value))


Selector = RangeSet | NameSet


@dataclass(frozen=True)
class AccessPolicy:
    """Parsed, immutable access policy (schema version 1)."""

    protected: Mapping[str, Selector] = field(default_factory=dict)
    writable: Mapping[str, Selector] = field(default_factory=dict)
    limits: Mapping[str, int] = field(default_factory=dict)
    controller_host: str | None = None
    assignment_block: tuple[int, int] | None = None
    source: str | None = None

    # -- loading -----------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str | None = None) -> AccessPolicy:
        if not isinstance(data, Mapping):
            raise PolicyFileError("policy must be a JSON object")
        unknown = set(data) - {
            "version",
            "description",
            "controller",
            "protected",
            "writable",
            "limits",
            "assignments",
        }
        if unknown:
            raise PolicyFileError(f"unknown top-level keys: {sorted(unknown)}")
        if data.get("version") != SCHEMA_VERSION:
            raise PolicyFileError(f"'version' must be {SCHEMA_VERSION}")

        def selectors(section: str) -> dict[str, Selector]:
            raw = data.get(section, {})
            if not isinstance(raw, Mapping):
                raise PolicyFileError(f"'{section}' must be an object")
            out: dict[str, Selector] = {}
            for kind, value in raw.items():
                if not is_known_kind(kind):
                    raise PolicyFileError(f"'{section}': unknown kind {kind!r}")
                where = f"{section}.{kind}"
                if section == "writable" and value == "*":
                    raise PolicyFileError(f"{where}: '*' is not allowed in 'writable'; list ranges")
                out[kind] = (
                    NameSet.parse(value, where)
                    if kind in NAMED_KINDS
                    else RangeSet.parse(value, where)
                )
            return out

        limits_raw = data.get("limits", {})
        if not isinstance(limits_raw, Mapping):
            raise PolicyFileError("'limits' must be an object")
        limits: dict[str, int] = {}
        for key, value in limits_raw.items():
            if key not in LIMIT_KEYS:
                raise PolicyFileError(f"'limits': unknown key {key!r}")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PolicyFileError(f"'limits.{key}' must be a non-negative integer")
            limits[key] = value

        host: str | None = None
        controller = data.get("controller", {})
        if not isinstance(controller, Mapping):
            raise PolicyFileError("'controller' must be an object")
        if "host" in controller:
            if not isinstance(controller["host"], str) or not controller["host"]:
                raise PolicyFileError("'controller.host' must be a non-empty string")
            host = controller["host"]

        block: tuple[int, int] | None = None
        assignments = data.get("assignments", {})
        if not isinstance(assignments, Mapping):
            raise PolicyFileError("'assignments' must be an object")
        if "r_block" in assignments:
            rb = assignments["r_block"]
            if (
                not isinstance(rb, list)
                or len(rb) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in rb)
                or not 1 <= rb[0] <= rb[1] <= 65536
            ):
                raise PolicyFileError("'assignments.r_block' must be [first, last] %R addresses")
            block = (rb[0], rb[1])

        return cls(
            protected=selectors("protected"),
            writable=selectors("writable"),
            limits=limits,
            controller_host=host,
            assignment_block=block,
            source=source,
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> AccessPolicy:
        """Load a policy file. Raises :class:`PolicyFileError` if it is missing or invalid."""
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise PolicyFileError(f"policy file not found: {p}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyFileError(f"{p}: invalid JSON: {exc}") from exc
        return cls.from_dict(data, source=str(p))

    @classmethod
    def discover(cls, path: str | os.PathLike[str] | None = None) -> AccessPolicy | None:
        """Find the policy: explicit ``path``, then ``$FANUC_SNPX_POLICY``, then the default file.

        Returns ``None`` when no path was given and no file exists. In that case
        every write raises :class:`WriteNotAllowed`.
        """
        if path is not None:
            return cls.load(path)
        env = os.environ.get(POLICY_ENV_VAR)
        if env:
            return cls.load(env)
        default = Path(DEFAULT_POLICY_FILE)
        return cls.load(default) if default.is_file() else None

    # -- checks ------------------------------------------------------------

    def limit_for(self, kind: str) -> int | None:
        base = kind[len(COMMENT_PREFIX) :] if kind.startswith(COMMENT_PREFIX) else kind
        for key, k in LIMIT_KEYS.items():
            if k == base and key in self.limits:
                return self.limits[key]
        return None

    def check_limits(self, kind: str, indexes: Iterable[int]) -> None:
        """Raise :class:`PolicyViolation` if any index exceeds the user's limit for ``kind``."""
        limit = self.limit_for(kind)
        if limit is None:
            return
        for i in indexes:
            if i > limit:
                raise PolicyViolation(f"{kind}[{i}] exceeds policy limit {limit}")

    def authorize(
        self,
        kind: str,
        targets: Iterable[int] | Iterable[str],
        *,
        reason: str,
        host: str | None,
    ) -> WriteAuthorization:
        """Check a write of every element in ``targets`` and return the authorization token."""
        if not is_known_kind(kind):
            raise ValueError(f"unknown policy kind {kind!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise WriteNotAllowed("every write needs a non-empty reason")
        if self.controller_host is not None and host != self.controller_host:
            raise PolicyViolation(
                f"policy {self.source or ''} is bound to controller {self.controller_host!r}, "
                f"not {host!r}"
            )
        items = list(targets)
        if not items:
            raise ValueError("no write targets")
        if _is_indexed(kind):
            if not all(isinstance(t, int) and not isinstance(t, bool) for t in items):
                raise TypeError(f"{kind} targets must be integer indexes")
            self.check_limits(kind, [t for t in items if isinstance(t, int)])
        elif not all(isinstance(t, str) for t in items):
            raise TypeError(f"{kind} targets must be names")
        protected = self.protected.get(kind)
        writable = self.writable.get(kind)
        for t in items:
            if protected is not None and t in protected:
                raise PolicyViolation(f"{kind} {t} is protected by policy {self.source or ''}")
            if writable is None or t not in writable:
                raise WriteNotAllowed(
                    f"{kind} {t} is not inside a 'writable' range of policy {self.source or ''}"
                )
        label = f"{kind}[{items[0]}]" if len(items) == 1 else f"{kind}[{items[0]}..{items[-1]}]"
        return WriteAuthorization(kind=kind, target=label, reason=reason.strip())
