"""Example application adapter on top of fanuc_snpx.

An application usually wants a handful of named operations rather than the whole
library. This adapter shows one way to provide them. It holds no robot-specific
numbers: register ranges, frames and the assignment map come from the caller.

Every write goes through the library's guarded path (policy, reason, write
guard, read-back). The adapter adds an example ``write_guard`` that refuses
frame/tool changes larger than a limit unless the caller says they are expected.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fanuc_snpx import (
    AccessPolicy,
    AssignmentTable,
    Cartesian,
    Joints,
    Position,
    SnpxClient,
    SrtpSession,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pose:
    """World pose and joints from one controller snapshot."""

    xyzwpr: tuple[float, float, float, float, float, float]
    joints: tuple[float, ...]


class FrameChangeGuard:
    """``write_guard`` that vetoes frame/tool writes moving more than ``max_mm`` / ``max_deg``.

    Set ``allow_large_change`` for one write when a large change is intended.
    """

    def __init__(self, max_mm: float = 5.0, max_deg: float = 2.0) -> None:
        self.max_mm = max_mm
        self.max_deg = max_deg
        self.allow_large_change = False

    def __call__(self, target: str, old: Any, new: Any, reason: str) -> bool:
        if not target.startswith(("$MNUFRAME", "$MNUTOOL")):
            return True
        if not isinstance(old, Position) or old.cartesian_view is None:
            return True  # nothing to compare with (e.g. untaught)
        if not isinstance(new, Cartesian):
            return False
        before = old.cartesian_view.xyzwpr
        dist = math.dist(before[:3], new.xyzwpr[:3])
        turn = max(abs(a - b) for a, b in zip(before[3:], new.xyzwpr[3:], strict=True))
        ok = self.allow_large_change or (dist <= self.max_mm and turn <= self.max_deg)
        log.info("%s change: %.3f mm, %.3f deg -> %s (%s)", target, dist, turn, ok, reason)
        self.allow_large_change = False
        return ok


class RobotAdapter:
    """Named operations for an application. Read-only unless created with a policy."""

    def __init__(
        self,
        host: str,
        assignments: AssignmentTable,
        *,
        policy: AccessPolicy | str | None = None,
        group: int = 1,
        session: SrtpSession | None = None,
    ) -> None:
        self.guard = FrameChangeGuard()
        self.group = group
        self.robot = SnpxClient(
            host,
            assignments=assignments,
            policy=policy,
            allow_writes=policy is not None,
            write_guard=self.guard,
            session=session,
        )

    def close(self) -> None:
        self.robot.close()

    def __enter__(self) -> RobotAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # numeric registers
    def read_numeric(self, index: int) -> float:
        return float(self.robot.numeric_registers.read(index))

    def read_numerics(self, first: int, count: int) -> list[float]:
        return [float(v) for v in self.robot.numeric_registers.read_block(first, count)]

    def write_numeric(self, index: int, value: float, *, reason: str) -> float:
        return float(self.robot.numeric_registers.write(index, value, reason=reason))

    # position registers
    def read_position(self, index: int) -> Position:
        return self.robot.position_registers.read(index, group=self.group)

    def read_positions(self, first: int, count: int) -> list[Position]:
        return self.robot.position_registers.read_block(first, count, group=self.group)

    def write_position(self, index: int, value: Cartesian | Joints, *, reason: str) -> Position:
        return self.robot.position_registers.write(index, value, reason=reason, group=self.group)

    # frames and tools
    def read_user_frame(self, number: int) -> Cartesian:
        return self.robot.frames.read_user_frame(self.group, number)

    def write_user_frame(
        self, number: int, value: Cartesian, *, reason: str, large_change: bool = False
    ) -> None:
        self.guard.allow_large_change = large_change
        self.robot.frames.write_user_frame(self.group, number, value, reason=reason)

    def read_user_tool(self, number: int) -> Cartesian:
        return self.robot.frames.read_user_tool(self.group, number)

    def write_user_tool(
        self, number: int, value: Cartesian, *, reason: str, large_change: bool = False
    ) -> None:
        self.guard.allow_large_change = large_change
        self.robot.frames.write_user_tool(self.group, number, value, reason=reason)

    # current position
    def read_current_world_position_with_joints(self) -> Pose:
        p = self.robot.current_position.world(group=self.group)
        c = p.cartesian()
        return Pose(c.xyzwpr, p.joints().values)

    # group I/O
    def read_group_input(self, index: int) -> int:
        return int(self.robot.io.read_one("GI", index))

    def read_group_output(self, index: int) -> int:
        return int(self.robot.io.read_one("GO", index))

    def write_group_input(self, index: int, value: int, *, reason: str) -> int:
        return int(self.robot.io.write("GI", index, [value], reason=reason)[0])

    # comments
    def read_comment(self, index: int) -> str:
        return self.robot.comments.read("R", index)

    def write_comment(self, index: int, text: str, *, reason: str) -> str:
        return self.robot.comments.write("R", index, text, reason=reason)

    def read_position_comment(self, index: int) -> str:
        return self.robot.comments.read("PR", index)

    def write_position_comment(self, index: int, text: str, *, reason: str) -> str:
        return self.robot.comments.write("PR", index, text, reason=reason)


def summarize(positions: Sequence[Position]) -> list[str]:
    """One line per position: Cartesian if available, else joints, else 'untaught'."""
    out = []
    for p in positions:
        if p.cartesian_view is not None and p.valid_cartesian:
            out.append("X/Y/Z/W/P/R " + " ".join(f"{v:.3f}" for v in p.cartesian_view.xyzwpr))
        elif p.joint_view is not None and p.valid_joint:
            out.append("J " + " ".join(f"{v:.3f}" for v in p.joint_view.j1_j6))
        else:
            out.append("untaught")
    return out
