"""SnpxClient end to end against the fake controller: reads, gated writes, assignments."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from fanuc_snpx import (
    AccessPolicy,
    Assignment,
    AssignmentRequest,
    AssignmentTable,
    Cartesian,
    Configuration,
    Joints,
    SnpxClient,
    SysvarType,
    VarName,
)
from fanuc_snpx.errors import (
    PolicyViolation,
    ReadBackMismatch,
    RepresentationError,
    SnpxAssignmentError,
    WriteNotAllowed,
    WriteVetoed,
)
from fanuc_snpx.memory import f32
from fanuc_snpx.srtp import SrtpSession
from fanuc_snpx.testing import FakeAssignment, FakeController, FakeSrtpServer, position_image
from helpers import fast_limiter

# (address, size, var, multiply, sysvar_type)
LAYOUT: list[tuple[int, int, str, float, SysvarType | None]] = [
    (1, 60, "R[1]", 0.0, None),
    (61, 500, "PR[1]", 0.0, None),
    (561, 50, "POS[G1:0]", 0.0, None),
    (611, 100, "$MNUFRAME[1,1]", 0.0, SysvarType.POSITION),
    (711, 80, "SR[1]", 1.0, None),
    (791, 40, "R[C5]", 1.0, None),
    (831, 2, "$WAITTMOUT", 1.0, SysvarType.INTEGER),
    (833, 4, "R[41]", 100.0, None),
]

POLICY = {
    "version": 1,
    "controller": {"host": "127.0.0.1"},
    "protected": {"numeric_registers": [[1, 9]], "position_registers": [10]},
    "writable": {
        "numeric_registers": [[1, 30], 41],
        "position_registers": [[5, 10]],
        "string_registers": [2],
        "system_variables": ["$MNUFRAME[1,2]", "$WAITTMOUT"],
        "digital_outputs": [[1, 8]],
        "group_outputs": [3],
        "comments.numeric_registers": [5],
        "snpx_assignments": [[5001, 6000]],
    },
    "limits": {"max_position_register": 10},
    "assignments": {"r_block": [5001, 6000]},
}


def table() -> AssignmentTable:
    return AssignmentTable(
        [
            Assignment(a, s, VarName.parse(v), m, slot=i + 1, sysvar_type=t)
            for i, (a, s, v, m, t) in enumerate(LAYOUT)
        ]
    )


@pytest.fixture
def fake() -> Iterator[FakeSrtpServer]:
    ctl = FakeController()
    for i, (a, s, v, m, _t) in enumerate(LAYOUT):
        ctl.assign(i + 1, FakeAssignment(a, s, v, m))
    ctl.numeric_registers.update({1: 7, 2: 1.5, 3: -2.25, 41: 12.3456})
    ctl.position_registers[3] = position_image(
        xyzwpr=(100.0, -200.5, 300.25, 180.0, 0.0, 90.0),
        config=(0, 0, 1, 1),
        turns=(0, 1, -1),
        joints=(10.0, 20.0, -30.0, 0.0, 45.0, 90.0),
        uf=1,
        ut=2,
    )
    ctl.position_registers[4] = position_image(joints=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
    ctl.position_registers[6] = position_image()  # untaught, UF/UT = 15 ("F")
    ctl.position_registers[7] = position_image()
    ctl.current_position[(1, 0)] = position_image(
        xyzwpr=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0), joints=(0.5,) * 6, uf=0
    )
    ctl.sysvars["$MNUFRAME[1,1]"] = position_image(xyzwpr=(10.0, 20.0, 30.0, 0.0, 0.0, 0.0))
    ctl.sysvars["$MNUFRAME[1,2]"] = position_image(xyzwpr=(1.0, 1.0, 1.0, 0.0, 0.0, 90.0))
    ctl.sysvars["$WAITTMOUT"] = 3000
    ctl.string_registers[1] = "HELLO SNPX"
    with FakeSrtpServer(ctl) as srv:
        yield srv


def client(fake: FakeSrtpServer, **kw: Any) -> SnpxClient:
    session = SrtpSession(fake.host, fake.port, timeout=1.0, rate_limiter=fast_limiter())
    kw.setdefault("assignments", table())
    return SnpxClient(fake.host, session=session, **kw)


def writer(fake: FakeSrtpServer, **kw: Any) -> SnpxClient:
    return client(fake, allow_writes=True, policy=AccessPolicy.from_dict(POLICY), **kw)


# --- reads -------------------------------------------------------------------------------


def test_numeric_reads(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        assert c.numeric_registers.read_block(1, 4) == [7.0, 1.5, -2.25, 0.0]
        assert c.numeric_registers.read_int(1) == 7
        assert c.numeric_registers.read_real(2) == 1.5
        assert c.numeric_registers.read(41) == pytest.approx(12.35)  # int32 x100 mapping
        with pytest.raises(SnpxAssignmentError, match="MULTIPLY"):
            c.numeric_registers.read_real(41)
        with pytest.raises(ValueError, match="non-integer"):
            c.numeric_registers.read_int(2)
        with pytest.raises(SnpxAssignmentError, match="no assignment"):
            c.numeric_registers.read(31)


def test_factory_default_int16_view(fake: FakeSrtpServer) -> None:
    with fake.state() as ctl:
        ctl.set_default_table()
    with client(fake, assignments=AssignmentTable.factory_default()) as c:
        assert c.numeric_registers.read_block(1, 3) == [7, 2, -2]  # rounded 16-bit view


def test_position_register_reads(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        p = c.position_registers.read(3)
        cart = p.cartesian()
        assert cart.xyzwpr == (100.0, -200.5, 300.25, 180.0, 0.0, 90.0)
        assert cart.configuration == Configuration(False, False, True, True, 0, 1, -1)
        assert str(cart.configuration) == "N R U T, 0, 1, -1"
        assert p.joints().j1_j6 == (10.0, 20.0, -30.0, 0.0, 45.0, 90.0)
        assert (p.user_frame, p.user_tool) == (1, 2)
        assert p.is_cartesian is None  # both views valid
        joint_only = c.position_registers.read(4)
        assert joint_only.is_cartesian is False
        with pytest.raises(RepresentationError):
            joint_only.cartesian()
        untaught = c.position_registers.read(5)
        with pytest.raises(RepresentationError):
            untaught.joints()
        block = c.position_registers.read_block(1, 10)
        assert len(block) == 10
        assert block[2] == p


def test_read_limits_from_policy(fake: FakeSrtpServer) -> None:
    c = client(fake, policy=AccessPolicy.from_dict(POLICY))
    with c, pytest.raises(PolicyViolation, match="limit"):
        c.position_registers.read_block(5, 7)


def test_block_read_is_one_request(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        c.connect()
        before = c.session.stats.requests
        c.position_registers.read_block(1, 10)  # 1000 bytes, under max_read_bytes
        assert c.session.stats.requests - before == 1


def test_current_position(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        p = c.current_position.world()
        assert p.cartesian().xyzwpr == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        assert p.joints().j1_j6 == (0.5,) * 6
        with pytest.raises(SnpxAssignmentError):
            c.current_position.user()


def test_sysvar_reads(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        assert c.frames.read_user_frame(1, 2).xyzwpr == (1.0, 1.0, 1.0, 0.0, 0.0, 90.0)
        assert c.sysvars.read_number("$waittmout") == 3000
        with pytest.raises(ValueError, match="needs 2 index"):
            c.sysvars.read("$MNUFRAME[2]")


def test_strings_and_comments(fake: FakeSrtpServer) -> None:
    with fake.state() as ctl:
        ctl.sysvars.clear()
    with client(fake) as c:
        assert c.string_registers.read(1) == "HELLO SNPX"
        assert c.string_registers.read(2) == ""
        assert c.comments.read("R", 5) == ""


def test_io_reads(fake: FakeSrtpServer) -> None:
    with fake.state() as ctl:
        ctl.bits[0x46][0] = 0b0000_0101  # %I1, %I3 -> DO[1], DO[3]
        ctl.bits[0x48][(6000 + 1 - 1) // 8] |= 1 << ((6000 + 1 - 1) % 8)  # %Q6001 -> UI[1]
        ctl.words[0x0A][2 * 2 : 2 * 3] = (560).to_bytes(2, "little")  # %AI3 -> GO[3]
    with client(fake) as c:
        assert c.io.read("DO", 1, 4) == [True, False, True, False]
        assert c.io.read_one("UI", 1) is True
        assert c.io.read("GO", 3) == [560]
        with pytest.raises(ValueError, match="valid"):
            c.io.read("DO", 0)
        with pytest.raises(ValueError, match="next I/O family"):
            c.io.read("RI", 999, 2)


def test_controller_info(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        assert "FAKE-SNPX" in c.controller.controller_type().ascii_runs()
        st = c.controller.short_status()
        assert st.privilege_level == 4
        assert c.raw.read_words("%R", 1, 2) == [0, 0x40E0]  # 7.0f low word, high word


# --- write gating --------------------------------------------------------------------------------


def test_read_only_by_default(fake: FakeSrtpServer) -> None:
    with client(fake) as c, pytest.raises(WriteNotAllowed, match="read-only"):
        c.numeric_registers.write(20, 1, reason="test")


def test_no_policy_means_no_writes(fake: FakeSrtpServer, tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANUC_SNPX_POLICY", raising=False)
    with client(fake, allow_writes=True) as c:
        assert c.policy is None
        with pytest.raises(WriteNotAllowed, match="no access policy"):
            c.numeric_registers.write(20, 1, reason="test")


def test_protected_and_not_writable(fake: FakeSrtpServer) -> None:
    with writer(fake) as c:
        with pytest.raises(PolicyViolation):
            c.numeric_registers.write(1, 5, reason="test")
        with pytest.raises(PolicyViolation):
            c.position_registers.write(10, Joints((0.0,) * 6), reason="test")
        with pytest.raises(WriteNotAllowed, match="writable"):
            c.numeric_registers.write(31, 1, reason="test")  # refused before any lookup
        with pytest.raises(WriteNotAllowed, match="reason"):
            c.numeric_registers.write(20, 1, reason=" ")
        with pytest.raises(WriteNotAllowed, match="writable"):
            c.frames.write_user_frame(
                1, 1, Cartesian(0, 0, 0, 0, 0, 0, None), reason="frame 1 is not writable"
            )
    with fake.state() as ctl:
        assert ctl.commands == []


def test_numeric_write_reads_back(fake: FakeSrtpServer) -> None:
    with writer(fake) as c:
        old = c.numeric_registers.write(20, 0.1, reason="scratch test")
        assert old == 0.0
        assert c.numeric_registers.read(20) == f32(0.1)
        c.numeric_registers.write(41, 1.23456, reason="int32 x100 mapping")
        assert c.numeric_registers.read(41) == pytest.approx(1.23)
    with fake.state() as ctl:
        assert ctl.numeric_registers[20] == pytest.approx(0.1)


def test_write_guard_veto_and_approval(fake: FakeSrtpServer) -> None:
    seen: list[tuple[str, Any, Any, str]] = []

    def guard(target: str, old: Any, new: Any, reason: str) -> bool:
        seen.append((target, old, new, reason))
        return new != 13

    with writer(fake, write_guard=guard) as c:
        c.numeric_registers.write(20, 12, reason="ok")
        with pytest.raises(WriteVetoed):
            c.numeric_registers.write(20, 13, reason="vetoed")
        assert c.numeric_registers.read(20) == 12.0
    assert seen[0] == ("R[20]", 0.0, 12, "ok")


def test_readback_mismatch_is_raised(fake: FakeSrtpServer) -> None:
    with writer(fake) as c:
        # The controller rounds through the x100 int32 mapping; 1.234 reads back as 1.23,
        # which the client predicts, so this passes. Corrupt the model to force a mismatch.
        c.numeric_registers.write(41, 1.234, reason="ok")
        fake.controller.write_r = lambda *a, **k: None  # type: ignore[method-assign]
        with pytest.raises(ReadBackMismatch):
            c.numeric_registers.write(41, 5.0, reason="ignored by the fake")


def test_position_write_cartesian_and_joint(fake: FakeSrtpServer) -> None:
    target = Cartesian(
        1.5, 2.5, 3.5, 0.0, 90.0, 180.0, Configuration(True, False, True, False, 0, 0, 1)
    )
    with writer(fake) as c:
        c.position_registers.write(6, target, reason="scratch PR")
        p = c.position_registers.read(6)
        assert p.cartesian().xyzwpr == target.xyzwpr
        assert p.cartesian().configuration == target.configuration
        assert p.user_frame == 15  # UF/UT untouched
        c.position_registers.write(7, Joints((1.0, 2.0, 3.0, 4.0, 5.0, 6.0)), reason="scratch PR")
        assert c.position_registers.read(7).joints().j1_j6 == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_sysvar_and_string_writes(fake: FakeSrtpServer) -> None:
    new = Cartesian(5.0, 6.0, 7.0, 0.0, 0.0, 45.0, None)
    with writer(fake) as c:
        c.frames.write_user_frame(1, 2, new, reason="scratch frame")
        assert c.frames.read_user_frame(1, 2).xyzwpr == new.xyzwpr
        c.sysvars.write("$WAITTMOUT", 2500, reason="scratch sysvar")
        assert c.sysvars.read("$WAITTMOUT") == 2500
        c.string_registers.write(2, "ABC", reason="scratch SR")
        assert c.string_registers.read(2) == "ABC"
        c.comments.write("R", 5, "scratch", reason="comment test")
        assert c.comments.read("R", 5) == "scratch"


def test_io_writes(fake: FakeSrtpServer) -> None:
    with writer(fake) as c:
        old = c.io.write("DO", 2, [True, True], reason="scratch DO")
        assert old == [False, False]
        assert c.io.read("DO", 1, 4) == [False, True, True, False]
        c.io.write("GO", 3, [77], reason="scratch GO")
        assert c.io.read("GO", 3) == [77]
        with pytest.raises(WriteNotAllowed):
            c.io.write("DI", 1, [True], reason="inputs are not writable here")


# --- assignment manager -------------------------------------------------------------


def test_session_assignments_need_confirmation(fake: FakeSrtpServer) -> None:
    with writer(fake) as c:
        plan = c.assignments.plan([AssignmentRequest.of("R[1]", 10)])
        with pytest.raises(WriteNotAllowed, match="CLRASG"):
            c.assignments.apply_session(plan, reason="test", multiplex_confirmed=False)
    with fake.state() as ctl:
        assert ctl.commands == []


def test_session_assignments_apply_read_and_reapply(fake: FakeSrtpServer) -> None:
    with writer(fake, assignments=AssignmentTable()) as c:
        plan = c.assignments.plan(
            [
                AssignmentRequest.of("R[1]", 4),
                AssignmentRequest.of("PR[3]"),
                AssignmentRequest.of("$MNUFRAME[1,1]", 2, sysvar_type=SysvarType.POSITION),
            ]
        )
        assert plan[0].address == 5001
        c.assignments.apply_session(plan, reason="session map", multiplex_confirmed=True)
        assert c.numeric_registers.read_block(1, 3) == [7.0, 1.5, -2.25]
        assert c.position_registers.read(3).cartesian().x == 100.0
        assert c.frames.read_user_frame(1, 2).r == 90.0
        c.reconnect()
        assert c.numeric_registers.read(2) == 1.5
    with fake.state() as ctl:
        assert ctl.commands[:2] == ["CLRASG", "SETASG 5001 8 R[1] 0"]
        assert ctl.commands.count("CLRASG") == 2
        assert ctl.table[0] is not None  # the shared table was not erased (multiplex on)


def test_assignment_block_must_be_writable(fake: FakeSrtpServer) -> None:
    with writer(fake) as c:
        plan = c.assignments.plan([AssignmentRequest.of("R[1]")], block=(100, 200))
        with pytest.raises(WriteNotAllowed, match="writable"):
            c.assignments.apply_session(plan, reason="outside block", multiplex_confirmed=True)
