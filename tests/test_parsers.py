"""Parsers for controller files. Fixtures use invented values in the controllers' real layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from fanuc_snpx.assignments import SysvarType
from fanuc_snpx.parsers import (
    VaConfig,
    iter_blocks,
    normalize,
    parse_curpos,
    parse_errall,
    parse_iostate,
    parse_number,
    parse_numreg,
    parse_posreg,
    parse_snpx_config,
    parse_strreg,
    parse_sysframe,
)

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str, *, crlf: bool = False) -> str:
    text = (FIX / name).read_text(encoding="ascii")
    return text.replace("\n", "\r\n") if crlf else text


@pytest.mark.parametrize(
    ("token", "value"),
    [("12", 12), ("-3", -3), ("12.500000", 12.5), (".800", 0.8), ("-.364", -0.364),
     ("0.000", 0.0), ("********", None), ("Uninitialized", None)],
)  # fmt: skip
def test_parse_number(token: str, value: object) -> None:
    assert parse_number(token) == value


def test_normalize_strips_http_wrapper() -> None:
    wrapped = (
        "<HTML>\n<HEAD><TITLE>numreg</TITLE></HEAD>\n<BODY><PRE>\nA\nB\n</PRE></BODY></HTML>\n"
    )
    assert normalize(wrapped).split() == ["A", "B"]


@pytest.mark.parametrize("crlf", [False, True])
def test_numreg(crlf: bool) -> None:
    regs = parse_numreg(fixture("numreg_va.txt", crlf=crlf))
    assert len(regs) == 10
    assert (regs[1].value, regs[1].comment) == (7, "COUNT A")
    assert isinstance(regs[1].value, int)
    assert regs[3].value == 12.5
    assert regs[4].value == -0.25
    assert regs[5].comment == "Pad  "
    assert regs[7].value is None
    assert regs[10].value == 99999


def test_block_headers() -> None:
    blocks = iter_blocks(fixture("numreg_va.txt"))
    assert [b.name for b in blocks] == ["$NUMREG", "$MAXREGNUM"]
    assert blocks[1].value == "10"
    assert blocks[0].type == "ARRAY[10] OF Numeric Reg"


def test_posreg() -> None:
    prs = parse_posreg(fixture("posreg_va.txt", crlf=True))
    park = prs[(1, 1)]
    assert park.comment == "PARK"
    assert park.position is not None
    assert park.position.group == 1
    assert park.position.joints == (10.0, -20.5, 0.75, -90.0, 45.0, 180.0)
    assert not park.position.is_cartesian
    assert prs[(1, 2)].uninitialized
    assert prs[(1, 2)].position is None
    app = prs[(1, 3)].position
    assert app is not None
    assert app.xyzwpr == (500.125, -250.0, 300.5, -180.0, 0.8, -0.364)
    assert app.config == VaConfig("N U T, 0, 0, 0", False, None, True, True, (0, 0, 0))
    odd = prs[(1, 4)]
    assert odd.comment == "       "
    assert odd.position is not None
    assert odd.position.config is not None
    assert odd.position.config.turns == (1, -1, 0)
    assert odd.position.extended == {"EXT1": None}
    assert prs[(1, 5)].position is not None
    assert prs[(1, 5)].position.extended == {"EXT1": 1500.25}
    slide = prs[(2, 1)].position
    assert slide is not None
    assert (slide.group, slide.joints, slide.joint_units) == (2, (-1200.5,), ("mm",))


def test_scara_config() -> None:
    cfg = VaConfig.parse("R, 0, 0, -1")
    assert (cfg.left, cfg.flip, cfg.turns) == (False, None, (0, 0, -1))


def test_sysframe() -> None:
    frames = parse_sysframe(fixture("sysframe_va.txt"))
    uf1 = frames["$MNUFRAME"][(1, 1)].position
    assert uf1 is not None
    assert uf1.xyzwpr == (1000.5, -500.25, -12.0, 0.09, 0.03, 90.0)
    assert frames["$MNUFRAME"][(1, 3)].uninitialized
    tool = frames["$MNUTOOL"][(1, 1)].position
    assert tool is not None
    assert tool.xyzwpr == (12.0, 0.0, 150.75, 0.0, -90.0, 0.0)


def test_snpx_config_from_system_va() -> None:
    cfg = parse_snpx_config(fixture("system_va.txt", crlf=True))
    assert cfg.params["$NUM_CIMP"] == 0
    assert cfg.params["$NUM_FRIF"] == 4
    assert cfg.params["$VERSION"] == 2
    assert cfg.params["$SNP_ID"] is None
    assert cfg.multiplexed is False  # CLRASG would erase the shared table
    assert [s.slot for s in cfg.used_slots] == [1, 2, 3, 4]
    assert cfg.slots[1].var_name == "PR[G1:1]"
    assert cfg.slots[1].multiply == 0.0

    table, problems = cfg.to_table({"$MNUFRAME[1,1]": SysvarType.POSITION})
    assert [e.slot for e in table] == [1, 2, 3]
    assert len(problems) == 1
    assert "F[1]" in problems[0]
    assert table.locate("PR", 3).address == 10001 + 100
    assert table.locate_sysvar("$MNUFRAME[1,2]").address == 10507

    _, problems = cfg.to_table()
    assert any("$MNUFRAME" in p for p in problems)  # type is required, never guessed
    md = cfg.to_markdown()
    assert "| 2 | 10001 | 500 | `PR[G1:1]` | 0 | %R10001..%R10500 |" in md
    assert "NUM_CIMP=0" in md


def test_curpos_v9() -> None:
    cur = parse_curpos(fixture("curpos_dg.txt", crlf=True))
    g1 = cur[1]
    assert g1.joints == (10.5, -20.25, 0.75, 0.0, -90.0, 180.0)
    assert (g1.user_frame, g1.tool) == (3, 2)
    assert g1.user is not None
    assert g1.world is not None
    assert g1.user.xyzwpr == (400.1, -100.2, 250.3, -180.0, 0.0, 0.96)
    assert g1.world.xyzwpr[5] == 90.96
    assert g1.world.config is not None
    assert g1.world.config.raw == "N U T, 0, 0, 0"


def test_curpos_v770_in_summary() -> None:
    cur = parse_curpos(fixture("curpos_v770_dg.txt"))
    assert cur[1].user is not None
    assert cur[1].user.config is not None  # CFG after R in older versions
    assert cur[1].world is not None
    assert cur[1].world.xyzwpr == (415.0, -125.0, -331.0, 56.0, 90.0, 0.0)


def test_errall() -> None:
    alarms = parse_errall(fixture("errall_ls.txt", crlf=True))
    assert [a.sequence for a in alarms] == [1003, 1002, 1001, 1000]
    first = alarms[0]
    assert first.message == "SRVO-003 Deadman switch released"
    assert (first.severity, first.mask, first.active) == ("SERVO", "00110110", True)
    assert alarms[1].cause.startswith("HOST-223")
    assert alarms[2].severity == ""
    assert alarms[3].time == "30-SEP-26 17:00"  # older format without seconds


def test_iostate() -> None:
    io = parse_iostate(fixture("iostate_dg.txt"))
    assert io[("DIN", 1)] is True
    assert io[("DIN", 2)] is False
    assert io[("GOUT", 3)] == 560
    assert io[("FLG", 2)] is True


def test_strreg_not_guessed() -> None:
    with pytest.raises(NotImplementedError, match="unverified"):
        parse_strreg("")
