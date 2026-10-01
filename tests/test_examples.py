"""Keep the examples working: run the adapter demo against the fake controller."""

from __future__ import annotations

import runpy
from pathlib import Path

from fanuc_snpx import AccessPolicy, AssignmentTable

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_example_files_are_valid() -> None:
    table = AssignmentTable.load(EXAMPLES / "asg-map.example.json")
    assert len(table) == 6
    policy = AccessPolicy.load(EXAMPLES / "robot-policy.example.json")
    assert policy.controller_host == "192.0.2.10"


def test_demo_runs() -> None:
    demo = runpy.run_path(str(EXAMPLES / "demo_fake.py"))
    out = demo["main"]()
    assert out["R[1..2]"] == [3.0, 12.5]
    assert out["PR[1..2]"][0].startswith("X/Y/Z/W/P/R 400.000")
    assert out["PR[1..2]"][1] == "untaught"
    assert out["GO[1]"] == 7
    assert out["UFRAME 1 after"][0] == 501.0
    assert str(out["large change"]).startswith("vetoed")
    assert out["R[20]"] == 1.5
