"""Read-only checks against a real controller.

Skipped unless ``FANUC_SNPX_HARDWARE=1``. Host from ``FANUC_SNPX_HOST``.
Nothing here writes: no %G commands, no memory writes, FTP downloads only.
Run only when the robot owner has said the client may connect.
"""

from __future__ import annotations

import os

import pytest

from fanuc_snpx import AssignmentTable, SnpxClient

pytestmark = pytest.mark.hardware

HOST = os.environ.get("FANUC_SNPX_HOST", "")


@pytest.fixture
def robot() -> SnpxClient:
    if not HOST:
        pytest.skip("set FANUC_SNPX_HOST")
    return SnpxClient(HOST, evidence=os.environ.get("FANUC_SNPX_EVIDENCE") or None)


def test_handshake_and_short_status(robot: SnpxClient) -> None:
    with robot:
        robot.connect()
        status = robot.controller.short_status()
        assert len(status.header) == 56


def test_digital_output_read(robot: SnpxClient) -> None:
    with robot:
        values = robot.io.read("DO", 1, 8)
        assert len(values) == 8


def test_factory_default_register_view(robot: SnpxClient) -> None:
    robot._declared_table = robot._table = AssignmentTable.factory_default()
    with robot:
        values = robot.numeric_registers.read_block(1, 5)
        assert len(values) == 5
