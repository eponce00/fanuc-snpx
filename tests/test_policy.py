"""Access policy: parsing, protected ranges, writable ranges, limits, host binding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fanuc_snpx.errors import PolicyFileError, PolicyViolation, WriteNotAllowed
from fanuc_snpx.policy import AccessPolicy

HOST = "192.0.2.10"

EXAMPLE = {
    "version": 1,
    "description": "generic example",
    "controller": {"host": HOST},
    "protected": {
        "numeric_registers": [[1, 99]],
        "position_registers": [[1, 100], 250],
        "system_variables": ["$SNPX_*", "$MNUFRAME[1,1]"],
        "group_outputs": "*",
    },
    "writable": {
        "numeric_registers": [[100, 120]],
        "position_registers": [[200, 300]],
        "system_variables": ["$MNUFRAME[1,2]", "$MNUFRAME[1,1]"],
    },
    "limits": {"max_numeric_register": 200, "max_position_register": 500},
    "assignments": {"r_block": [5001, 6000]},
}


@pytest.fixture
def policy() -> AccessPolicy:
    return AccessPolicy.from_dict(EXAMPLE, source="example")


def ok(p: AccessPolicy, kind: str, targets: list[int] | list[str]) -> str:
    return p.authorize(kind, targets, reason="unit test", host=HOST).target


def test_writable_target_is_authorized(policy: AccessPolicy) -> None:
    assert ok(policy, "numeric_registers", [100]) == "numeric_registers[100]"
    assert ok(policy, "position_registers", [200, 201]) == "position_registers[200..201]"
    assert ok(policy, "system_variables", ["$mnuframe[1,2]"]) == "system_variables[$mnuframe[1,2]]"


@given(st.integers(min_value=1, max_value=99))
def test_every_protected_index_is_rejected(i: int) -> None:
    p = AccessPolicy.from_dict(
        {
            "version": 1,
            "protected": {"numeric_registers": [[1, 99]]},
            "writable": {"numeric_registers": [[1, 200]]},
        }
    )
    with pytest.raises(PolicyViolation):
        p.authorize("numeric_registers", [i], reason="x", host=None)


def test_protected_wins_over_writable(policy: AccessPolicy) -> None:
    with pytest.raises(PolicyViolation, match="protected"):
        ok(policy, "position_registers", [250])
    with pytest.raises(PolicyViolation, match="protected"):
        ok(policy, "system_variables", ["$MNUFRAME[1,1]"])


def test_protected_glob(policy: AccessPolicy) -> None:
    with pytest.raises(PolicyViolation):
        ok(policy, "system_variables", ["$SNPX_ASG[1].$ADDRESS"])


def test_star_protects_everything(policy: AccessPolicy) -> None:
    with pytest.raises(PolicyViolation):
        ok(policy, "group_outputs", [1])


def test_not_writable_is_refused(policy: AccessPolicy) -> None:
    with pytest.raises(WriteNotAllowed, match="writable"):
        ok(policy, "numeric_registers", [150])
    with pytest.raises(WriteNotAllowed, match="writable"):
        ok(policy, "digital_outputs", [1])


def test_any_element_outside_range_rejects_whole_write(policy: AccessPolicy) -> None:
    with pytest.raises(WriteNotAllowed):
        ok(policy, "numeric_registers", [119, 120, 121])


def test_limits(policy: AccessPolicy) -> None:
    with pytest.raises(PolicyViolation, match="limit"):
        ok(policy, "position_registers", [501])
    with pytest.raises(PolicyViolation, match="limit"):
        policy.check_limits("numeric_registers", [201])
    policy.check_limits("numeric_registers", [200])
    policy.check_limits("string_registers", [10_000])  # no limit configured


def test_host_binding(policy: AccessPolicy) -> None:
    with pytest.raises(PolicyViolation, match="bound"):
        policy.authorize("numeric_registers", [100], reason="x", host="192.0.2.99")


@pytest.mark.parametrize("reason", ["", "   "])
def test_reason_required(policy: AccessPolicy, reason: str) -> None:
    with pytest.raises(WriteNotAllowed, match="reason"):
        policy.authorize("numeric_registers", [100], reason=reason, host=HOST)


def test_assignment_block(policy: AccessPolicy) -> None:
    assert policy.assignment_block == (5001, 6000)


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"version": 2},
        {"version": 1, "extra": 1},
        {"version": 1, "writable": {"numeric_registers": "*"}},
        {"version": 1, "protected": {"bogus_kind": "*"}},
        {"version": 1, "protected": {"numeric_registers": [[5, 1]]}},
        {"version": 1, "protected": {"numeric_registers": [[1, 2, 3]]}},
        {"version": 1, "protected": {"numeric_registers": [True]}},
        {"version": 1, "protected": {"system_variables": ["NO_DOLLAR"]}},
        {"version": 1, "protected": {"system_variables": ["$A B"]}},
        {"version": 1, "limits": {"max_numeric_register": -1}},
        {"version": 1, "limits": {"max_unknown": 1}},
        {"version": 1, "assignments": {"r_block": [10, 5]}},
        {"version": 1, "controller": {"host": ""}},
    ],
)
def test_invalid_policies_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(PolicyFileError):
        AccessPolicy.from_dict(bad)


def test_comment_kinds() -> None:
    p = AccessPolicy.from_dict({"version": 1, "writable": {"comments.numeric_registers": [[1, 5]]}})
    assert p.authorize("comments.numeric_registers", [3], reason="x", host=None)
    with pytest.raises(PolicyFileError):
        AccessPolicy.from_dict({"version": 1, "writable": {"comments.bogus": [1]}})


def test_load_and_discover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "p.json"
    f.write_text(json.dumps(EXAMPLE), encoding="utf-8")
    assert AccessPolicy.load(f).source == str(f)
    with pytest.raises(PolicyFileError, match="not found"):
        AccessPolicy.load(tmp_path / "missing.json")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(PolicyFileError, match="JSON"):
        AccessPolicy.load(tmp_path / "broken.json")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANUC_SNPX_POLICY", raising=False)
    assert AccessPolicy.discover() is None
    monkeypatch.setenv("FANUC_SNPX_POLICY", str(f))
    discovered = AccessPolicy.discover()
    assert discovered is not None
    assert discovered.controller_host == HOST
