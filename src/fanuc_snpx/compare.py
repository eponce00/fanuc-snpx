"""Compare SRTP reads with the controller's own files (the validation oracle).

Every function here is read-only: it reads through an :class:`SnpxClient` and
compares with text already downloaded from the controller (see
:mod:`fanuc_snpx.oracle` and :mod:`fanuc_snpx.parsers`).

Tolerances follow what each file can express, not what the controller stores:
``numreg.va`` prints 6 decimals, ``posreg.va``/``sysframe.va`` 3, ``curpos.dg``
2. A difference within half the printed resolution (plus float32 rounding) is a
match. Files are snapshots: compare only data that is not changing, twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .client import SnpxClient
from .errors import RepresentationError, SnpxError
from .parsers import VaConfig, parse_curpos, parse_numreg, parse_posreg, parse_sysframe
from .types import Configuration, Position

F32_REL = 1.2e-7  # float32 relative precision (2**-23)


@dataclass(frozen=True)
class Comparison:
    target: str
    field: str
    srtp: Any
    oracle: Any
    ok: bool
    note: str = ""


def _tol(decimals: int, value: float) -> float:
    return 0.5 * 10.0**-decimals + F32_REL * abs(value) + 1e-12


def _close(a: float | None, b: float | None, decimals: int) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= _tol(decimals, b)


def parse_ranges(text: str) -> list[int]:
    """``"1-5,9"`` -> [1, 2, 3, 4, 5, 9]."""
    out: list[int] = []
    for raw in text.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            if hi < lo:
                raise ValueError(f"bad range {part!r}")
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part))
    return out


def _runs(indexes: Iterable[int]) -> list[tuple[int, int]]:
    """Sorted unique indexes -> [(first, count), ...] of consecutive runs."""
    runs: list[tuple[int, int]] = []
    for i in sorted(set(indexes)):
        if runs and runs[-1][0] + runs[-1][1] == i:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((i, 1))
    return runs


# --- numeric registers -------------------------------------------------------------


def compare_numeric_registers(
    client: SnpxClient, numreg_text: str, indexes: Iterable[int]
) -> list[Comparison]:
    """R[i] via SRTP vs ``numreg.va``; 16-bit (``@1.1``) views compare with the rounded value."""
    regs = parse_numreg(numreg_text)
    out: list[Comparison] = []
    for first, count in _runs(indexes):
        try:
            values = client.numeric_registers.read_block(first, count)
        except SnpxError as exc:
            out.extend(
                Comparison(f"R[{i}]", "value", None, None, False, f"{type(exc).__name__}: {exc}")
                for i in range(first, first + count)
            )
            continue
        for k, v in enumerate(values):
            i = first + k
            ref = regs[i].value if i in regs else None
            target = f"R[{i}]"
            if i not in regs:
                out.append(Comparison(target, "value", v, None, False, "not in numreg.va"))
            elif ref is None:
                out.append(Comparison(target, "value", v, None, v == 0, "uninitialized in file"))
            elif isinstance(v, int) and isinstance(ref, float) and not ref.is_integer():
                out.append(
                    Comparison(target, "value", v, ref, v == round(ref), "integer view of a real")
                )
            else:
                out.append(Comparison(target, "value", v, ref, _close(float(v), float(ref), 6)))
    return out


# --- positions -----------------------------------------------------------------------------


def _config_matches(snpx: Configuration | None, file_cfg: VaConfig | None) -> tuple[bool, str]:
    if file_cfg is None or snpx is None:
        return True, "configuration not compared"
    for name in ("flip", "left", "up", "front"):
        want = getattr(file_cfg, name)
        if want is not None and getattr(snpx, name) != want:
            return False, f"{name}: srtp {getattr(snpx, name)} file {want}"
    turns = (snpx.turn4, snpx.turn5, snpx.turn6)[: len(file_cfg.turns)]
    if turns != file_cfg.turns[: len(turns)]:
        return False, f"turns: srtp {turns} file {file_cfg.turns}"
    return True, ""


def _compare_cartesian(
    target: str, pos: Position, xyzwpr: Sequence[float | None], cfg: VaConfig | None, decimals: int
) -> list[Comparison]:
    try:
        cart = pos.cartesian()
    except RepresentationError as exc:
        return [Comparison(target, "cartesian", None, tuple(xyzwpr), False, str(exc))]
    out = [
        Comparison(target, axis, got, want, _close(got, want, decimals))
        for axis, got, want in zip("XYZWPR", cart.xyzwpr, xyzwpr, strict=True)
    ]
    ok, note = _config_matches(cart.configuration, cfg)
    out.append(
        Comparison(target, "config", str(cart.configuration), cfg.raw if cfg else None, ok, note)
    )
    return out


def _compare_joints(
    target: str, pos: Position, joints: Sequence[float | None], decimals: int
) -> list[Comparison]:
    try:
        got = pos.joints().values
    except RepresentationError as exc:
        return [Comparison(target, "joints", None, tuple(joints), False, str(exc))]
    return [
        Comparison(target, f"J{n}", got[n - 1], want, _close(got[n - 1], want, decimals))
        for n, want in enumerate(joints, start=1)
        if n <= len(got)
    ]


def compare_position_registers(
    client: SnpxClient, posreg_text: str, indexes: Iterable[int], *, group: int = 1
) -> list[Comparison]:
    """PR[i] via SRTP vs ``posreg.va`` (Cartesian, configuration, joints, extended axes)."""
    file_prs = parse_posreg(posreg_text)
    out: list[Comparison] = []
    for first, count in _runs(indexes):
        try:
            positions = client.position_registers.read_block(first, count, group=group)
        except SnpxError as exc:
            out.extend(
                Comparison(f"PR[{i}]", "read", None, None, False, f"{type(exc).__name__}: {exc}")
                for i in range(first, first + count)
            )
            continue
        for k, pos in enumerate(positions):
            i = first + k
            target = f"PR[{'' if group == 1 else f'G{group}:'}{i}]"
            entry = file_prs.get((group, i))
            if entry is None:
                out.append(Comparison(target, "entry", None, None, False, "not in posreg.va"))
                continue
            if entry.uninitialized or entry.position is None:
                untaught = not pos.valid_cartesian and not pos.valid_joint
                out.append(
                    Comparison(
                        target,
                        "valid",
                        (pos.valid_cartesian, pos.valid_joint),
                        "Uninitialized",
                        untaught,
                        "file says untaught; SRTP VALIDC/VALIDJ should both be 0",
                    )
                )
                continue
            fp = entry.position
            if fp.xyzwpr is not None:
                out.extend(_compare_cartesian(target, pos, fp.xyzwpr, fp.config, 3))
                ext = pos.cartesian_view.extended if pos.cartesian_view else None
                for n, name in enumerate(sorted(fp.extended)):
                    want = fp.extended[name]
                    if ext is not None and want is not None and n < len(ext):
                        out.append(Comparison(target, name, ext[n], want, _close(ext[n], want, 3)))
            if fp.joints is not None:
                out.extend(_compare_joints(target, pos, fp.joints, 3))
    return out


def compare_frames(
    client: SnpxClient,
    sysframe_text: str,
    *,
    group: int = 1,
    frames: Iterable[int] = (),
    tools: Iterable[int] = (),
) -> list[Comparison]:
    """``$MNUFRAME[g,i]`` / ``$MNUTOOL[g,i]`` via SRTP vs ``sysframe.va`` (X..R)."""
    file_frames = parse_sysframe(sysframe_text)
    out: list[Comparison] = []
    for var, numbers in (("$MNUFRAME", frames), ("$MNUTOOL", tools)):
        for n in numbers:
            name = f"{var}[{group},{n}]"
            entry = file_frames.get(var, {}).get((group, n))
            if entry is None or entry.position is None or entry.position.xyzwpr is None:
                out.append(Comparison(name, "entry", None, None, False, "not in sysframe.va"))
                continue
            try:
                pos = client.sysvars.read_position(name)
            except SnpxError as exc:
                out.append(
                    Comparison(name, "read", None, None, False, f"{type(exc).__name__}: {exc}")
                )
                continue
            cmp = _compare_cartesian(name, pos, entry.position.xyzwpr, None, 3)
            out.extend(c for c in cmp if c.field != "config")
    return out


def compare_current_position(
    client: SnpxClient, curpos_text: str, *, group: int = 1
) -> list[Comparison]:
    """``POS[Gg:0]`` (world + joints, one SRTP read) vs ``curpos.dg``. The robot must be still."""
    file_pos = parse_curpos(curpos_text).get(group)
    target = f"POS[G{group}:0]"
    if file_pos is None or file_pos.world is None:
        return [Comparison(target, "entry", None, None, False, "group not in curpos.dg")]
    try:
        pos = client.current_position.world(group=group)
    except SnpxError as exc:
        return [Comparison(target, "read", None, None, False, f"{type(exc).__name__}: {exc}")]
    out = _compare_cartesian(target, pos, file_pos.world.xyzwpr, file_pos.world.config, 2)
    out.extend(_compare_joints(target, pos, file_pos.joints, 2))
    return out


# --- reporting ------------------------------------------------------------------------------


def summary(comparisons: Sequence[Comparison]) -> dict[str, int]:
    return {
        "compared": len(comparisons),
        "ok": sum(c.ok for c in comparisons),
        "mismatch": sum(not c.ok for c in comparisons),
    }


def to_markdown(comparisons: Sequence[Comparison], *, source: str) -> str:
    """Rows ready to paste into docs/VALIDATION_LOG.md."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        f"Comparison at {ts} against {source}: {summary(comparisons)}",
        "",
        "| Target | Field | SRTP | File | Result | Note |",
        "|---|---|---|---|---|---|",
    ]
    for c in comparisons:
        lines.append(
            f"| {c.target} | {c.field} | {c.srtp!r} | {c.oracle!r} | "
            f"{'ok' if c.ok else 'MISMATCH'} | {c.note} |"
        )
    return "\n".join(lines) + "\n"
