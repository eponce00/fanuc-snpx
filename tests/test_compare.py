"""SRTP-vs-file comparison against the fake controller seeded like the fixture files."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from fanuc_snpx import Assignment, AssignmentTable, SnpxClient, SysvarType, VarName
from fanuc_snpx.cli import main
from fanuc_snpx.compare import (
    compare_current_position,
    compare_frames,
    compare_numeric_registers,
    compare_position_registers,
    parse_ranges,
    summary,
    to_markdown,
)
from fanuc_snpx.srtp import SrtpSession
from fanuc_snpx.testing import FakeAssignment, FakeController, FakeSrtpServer, position_image
from helpers import fast_limiter

FIX = Path(__file__).parent / "fixtures"
LAYOUT: list[tuple[int, int, str, float, SysvarType | None]] = [
    (1, 20, "R[1]", 0.0, None),
    (21, 300, "PR[1]", 0.0, None),
    (321, 100, "$MNUFRAME[1,1]", 0.0, SysvarType.POSITION),
    (421, 100, "$MNUTOOL[1,1]", 0.0, SysvarType.POSITION),
    (521, 50, "POS[G1:0]", 0.0, None),
]


def table() -> AssignmentTable:
    return AssignmentTable(
        [Assignment(a, s, VarName.parse(v), m, slot=i + 1, sysvar_type=t)
         for i, (a, s, v, m, t) in enumerate(LAYOUT)]
    )  # fmt: skip


@pytest.fixture
def fake() -> Iterator[FakeSrtpServer]:
    ctl = FakeController()
    for i, (a, s, v, m, _t) in enumerate(LAYOUT):
        ctl.assign(i + 1, FakeAssignment(a, s, v, m))
    ctl.numeric_registers.update({1: 7, 2: -3, 3: 12.5, 4: -0.25, 5: 1234.567871, 10: 99999})
    ctl.position_registers[1] = position_image(joints=(10.0, -20.5, 0.75, -90.0, 45.0, 180.0))
    ctl.position_registers[3] = position_image(
        xyzwpr=(500.125, -250.0, 300.5, -180.0, 0.8, -0.364), config=(0, 0, 1, 1)
    )
    ctl.position_registers[4] = position_image(
        xyzwpr=(0.0,) * 6, config=(1, 0, 0, 0), turns=(1, -1, 0)
    )
    ctl.position_registers[5] = position_image(
        xyzwpr=(100.0, 200.0, 300.0, 0.0, 0.0, 0.0), ext=(1500.25, 0.0, 0.0), config=(0, 0, 1, 1)
    )
    ctl.sysvars["$MNUFRAME[1,1]"] = position_image(
        xyzwpr=(1000.5, -500.25, -12.0, 0.09, 0.03, 90.0)
    )
    ctl.sysvars["$MNUFRAME[1,2]"] = position_image(xyzwpr=(0.0,) * 6)
    ctl.sysvars["$MNUTOOL[1,1]"] = position_image(xyzwpr=(12.0, 0.0, 150.75, 0.0, -90.0, 0.0))
    ctl.current_position[(1, 0)] = position_image(
        xyzwpr=(900.1, -600.2, 238.3, -180.0, 0.0, 90.96),
        config=(0, 0, 1, 1),
        joints=(10.5, -20.25, 0.75, 0.0, -90.0, 180.0),
    )
    with FakeSrtpServer(ctl) as srv:
        yield srv


def client(fake: FakeSrtpServer) -> SnpxClient:
    s = SrtpSession(fake.host, fake.port, timeout=1.0, rate_limiter=fast_limiter())
    return SnpxClient(fake.host, session=s, assignments=table())


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="ascii")


def test_parse_ranges() -> None:
    assert parse_ranges("1-3, 7,9-10") == [1, 2, 3, 7, 9, 10]
    with pytest.raises(ValueError, match="bad range"):
        parse_ranges("5-1")


def test_everything_matches(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        results = compare_numeric_registers(c, text("numreg_va.txt"), range(1, 11))
        results += compare_position_registers(c, text("posreg_va.txt"), range(1, 7))
        results += compare_frames(c, text("sysframe_va.txt"), frames=[1, 2], tools=[1])
        results += compare_current_position(c, text("curpos_dg.txt"))
    bad = [r for r in results if not r.ok]
    assert bad == []
    assert summary(results)["compared"] > 60
    md = to_markdown(results, source="fixtures")
    assert "| R[5] | value |" in md
    assert "MISMATCH" not in md


def test_mismatches_are_reported(fake: FakeSrtpServer) -> None:
    with fake.state() as ctl:
        ctl.numeric_registers[3] = 12.51
        ctl.position_registers[3] = position_image(
            xyzwpr=(500.125, -250.0, 300.5, -180.0, 0.8, -0.364), config=(1, 0, 1, 1)
        )
        ctl.position_registers[2] = position_image(joints=(1.0,) * 6)
    with client(fake) as c:
        regs = compare_numeric_registers(c, text("numreg_va.txt"), [3])
        prs = compare_position_registers(c, text("posreg_va.txt"), [2, 3])
    assert [r.ok for r in regs] == [False]
    bad = {(r.target, r.field) for r in prs if not r.ok}
    assert bad == {("PR[2]", "valid"), ("PR[3]", "config")}


def test_missing_mapping_is_a_mismatch_not_a_crash(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        results = compare_numeric_registers(c, text("numreg_va.txt"), [30])
    assert results[0].ok is False
    assert "SnpxAssignmentError" in results[0].note


def test_cli_compare(
    fake: FakeSrtpServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = tmp_path / "files"
    files.mkdir()
    for src, dst in (
        ("numreg_va.txt", "numreg.va"),
        ("posreg_va.txt", "posreg.va"),
        ("sysframe_va.txt", "sysframe.va"),
        ("curpos_dg.txt", "curpos.dg"),
    ):
        (files / dst).write_bytes((FIX / src).read_bytes())  # fmt: skip
    amap = tmp_path / "asg.json"
    amap.write_text(table().to_json(), encoding="utf-8")
    out = tmp_path / "rows.md"
    rc = main(["compare", fake.host, "--port", str(fake.port), "--rate", "1000", "--map", str(amap),
               "--files", str(files), "--registers", "1-10", "--pr", "1-6", "--frames", "1-2",
               "--tools", "1", "--curpos", "--out", str(out)])  # fmt: skip
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mismatch"] == 0
    assert "| POS[G1:0] | J6 |" in out.read_text(encoding="utf-8")
