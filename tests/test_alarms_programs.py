"""Alarm screens (ALM[]) and program status (PRG[]) through the fake controller."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from fanuc_snpx import Assignment, AssignmentTable, SnpxClient, VarName
from fanuc_snpx.srtp import SrtpSession
from fanuc_snpx.testing import (
    FakeAssignment,
    FakeController,
    FakeSrtpServer,
    alarm_image,
    program_image,
)
from fanuc_snpx.types import ProgramState, decode_alarm
from helpers import fast_limiter

LAYOUT = [
    (1, 200, "ALM[1]", 1.0),
    (201, 300, "ALM[E1]", 1.0),
    (501, 12, "ALM[E1]@1.4", 1.0),
    (513, 18, "PRG[1]", 1.0),
    (531, 4, "PRG[1]@9.2", 1.0),
]


@pytest.fixture
def fake() -> Iterator[FakeSrtpServer]:
    ctl = FakeController()
    for i, (a, s, v, m) in enumerate(LAYOUT):
        ctl.assign(i + 1, FakeAssignment(a, s, v, m))
    ctl.alarms[("", 1)] = alarm_image(
        alarm_id=11, number=3, message="SRVO-003 Deadman switch released",
        severity=54, severity_text="SERVO", time=(2026, 10, 1, 8, 59, 50),
    )  # fmt: skip
    ctl.alarms[("E", 1)] = ctl.alarms[("", 1)]
    ctl.alarms[("E", 2)] = alarm_image(
        alarm_id=0, number=0, message="RESET", severity=128, severity_text=""
    )
    ctl.alarms[("E", 3)] = alarm_image(
        alarm_id=43, number=685, message="INTP-685 (PROG1, 10) TIMER[1] already started",
        cause_id=7, cause_number=2, cause_message="SYST-002 cause text",
    )  # fmt: skip
    ctl.programs[1] = program_image("MAIN_SUB", 42, 2, caller="MAIN")
    with FakeSrtpServer(ctl) as srv:
        yield srv


def client(fake: FakeSrtpServer, layout: list[tuple[int, int, str, float]] = LAYOUT) -> SnpxClient:
    table = AssignmentTable(
        [Assignment(a, s, VarName.parse(v), m, slot=i + 1) for i, (a, s, v, m) in enumerate(layout)]
    )
    s = SrtpSession(fake.host, fake.port, timeout=1.0, rate_limiter=fast_limiter())
    return SnpxClient(fake.host, session=s, assignments=table)


def test_active_and_history(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        first, second = c.alarms.active(1, 2)
        hist = c.alarms.history(1, 3)
    assert first.code == "SRVO-003"
    assert (first.alarm_id, first.number, first.severity_name) == (11, 3, "SERVO")
    assert first.time == (2026, 10, 1, 8, 59, 50)
    assert second.is_empty
    assert hist[1].is_reset
    assert hist[2].cause_message == "SYST-002 cause text"
    assert (hist[2].cause_id, hist[2].cause_number) == (7, 2)


def test_sliced_history_has_only_ids(fake: FakeSrtpServer) -> None:
    sliced = [(501, 12, "ALM[E1]@1.4", 1.0)]
    with client(fake, sliced) as c:
        lines = c.alarms.history(1, 3)
    assert [(a.alarm_id, a.number) for a in lines] == [(11, 3), (0, 0), (43, 685)]
    assert lines[0].message is None
    assert lines[0].time is None
    assert lines[2].severity is None


def test_program_status(fake: FakeSrtpServer) -> None:
    with client(fake) as c:
        st = c.programs.status(1)
    assert (st.name, st.line, st.state, st.caller) == ("MAIN_SUB", 42, ProgramState.RUNNING, "MAIN")
    with client(fake, [(531, 4, "PRG[1]@9.2", 1.0)]) as c:
        part = c.programs.status(1)
    assert (part.name, part.line, part.state) == (None, 42, ProgramState.RUNNING)


def test_severity_names_from_both_sources() -> None:
    raw = alarm_image(alarm_id=1, number=1, message="X-1", severity=45, severity_text="")
    assert decode_alarm(raw).severity_name == "ABORT.G"
    raw = alarm_image(alarm_id=1, number=1, message="X-1", severity=43, severity_text="")
    assert decode_alarm(raw).severity_name == "ABORT.G"
    raw = alarm_image(alarm_id=1, number=1, message="X-1", severity=43, severity_text="STOP.G")
    assert decode_alarm(raw).severity_name == "STOP.G"  # the text field wins
