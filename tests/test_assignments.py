"""Assignment naming, sizing, lookup (including slot precedence) and planning."""

from __future__ import annotations

import pytest

from fanuc_snpx.assignments import (
    Assignment,
    AssignmentRequest,
    AssignmentTable,
    SysvarType,
    VarName,
    canonical_sysvar,
    plan_assignments,
)
from fanuc_snpx.errors import SnpxAssignmentError


def A(address: int, size: int, var: str, mult: float = 0.0, slot: int | None = None,
      st: SysvarType | None = None) -> Assignment:  # fmt: skip
    return Assignment(address, size, VarName.parse(var), mult, slot, st)


# --- names ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "R[1]",
        "R[1]@1.1",
        "PR[7]",
        "PR[G2:5]",
        "PR[3]@27.12",
        "POS[0]",
        "POS[G1:15]",
        "SR[3]",
        "R[C1]",
        "PR[C10]@1.5",
        "DO[S1]",
        "WSI[C4]",
        "ALM[E1]",
        "PRG[MK1]",
        "$MNUFRAME[1,2]",
        "$MNUFRAME[1,1]@1.6",
        "$SNPX_ASG[1].$ADDRESS",
        "$[MYPROG]MYVAR",
    ],
)
def test_var_names_round_trip(text: str) -> None:
    assert str(VarName.parse(text)) == text


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "R[ 1]",
        "R 1",
        "R[1]@1",
        "XX[1]",
        "SR[C1]",
        "R[G1:1]",
        "DO[E1]",
        "$MNUFRAME[2]",
        "$MNUFRAME",
        "$MNUTOOL[1]",
        "$A B",
        "$" + "X" * 40,
    ],
)
def test_bad_var_names_rejected(bad: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        VarName.parse(bad)


def test_sysvar_canonicalisation() -> None:
    assert canonical_sysvar("$mnuframe[1,2]") == "$MNUFRAME[1,2]"
    with pytest.raises(ValueError, match="needs 2 index"):
        canonical_sysvar("$MNUFRAME[2]")
    with pytest.raises(ValueError, match="blanks"):
        canonical_sysvar("$MNUFRAME[1, 2]")


# --- sizes -------------------------------------------------------------------------------


def test_element_sizes() -> None:
    assert A(1, 20, "R[1]").words_per_element == 2
    assert A(1, 20, "R[1]@1.1", 1).words_per_element == 1
    assert A(1, 500, "PR[1]").element_count == 10
    assert A(1, 18, "PR[1]@1.6").element_count == 3
    assert A(1, 80, "SR[1]", 1).element_count == 2
    assert A(1, 40, "R[C1]", 1).element_count == 1
    assert A(1, 100, "POS[0]").element_count == 1
    assert A(1, 2, "DI[1]", 0).element_count == 32  # packed 16 per word
    assert A(1, 2, "GI[1]", 0).element_count == 2  # GI/GO/AI/AO never packed
    assert A(1, 100, "$MNUFRAME[1,1]", 0, st=SysvarType.POSITION).element_count == 2


def test_assignment_validation() -> None:
    with pytest.raises(SnpxAssignmentError, match="sysvar_type"):
        A(1, 50, "$MNUFRAME[1,1]")
    with pytest.raises(SnpxAssignmentError, match="slice exceeds"):
        A(1, 10, "R[1]@2.2")
    with pytest.raises(SnpxAssignmentError, match="MULTIPLY"):
        A(1, 2, "R[1]", 20000)
    with pytest.raises(SnpxAssignmentError, match="ADDRESS"):
        A(0, 2, "R[1]")
    with pytest.raises(NotImplementedError):
        _ = A(1, 2, "F[1]").words_per_element


def test_setasg_command_text() -> None:
    assert A(5001, 100, "PR[G1:101]", 0).command() == "SETASG 5001 100 PR[G1:101] 0"
    assert A(1, 2, "R[1]", 0.1).command() == "SETASG 1 2 R[1] 0.1"
    assert A(1, 2, "R[1]", 100).command() == "SETASG 1 2 R[1] 100"


# --- lookup ----------------------------------------------------------------------------------


def test_factory_default_maps_r_as_int16() -> None:
    t = AssignmentTable.factory_default()
    loc = t.locate("R", 7)
    assert loc.address == 7
    assert loc.words == 1


def test_locate_and_runs() -> None:
    t = AssignmentTable([A(101, 200, "PR[1]", slot=1), A(1, 40, "R[1]", slot=2)])
    loc = t.locate("PR", 3)
    assert loc.address == 101 + 2 * 50
    runs = t.plan_runs("PR", 2, 3)
    assert len(runs) == 1
    assert (runs[0].address, runs[0].words) == (151, 150)
    with pytest.raises(SnpxAssignmentError, match="no assignment covers PR"):
        t.locate("PR", 5)
    with pytest.raises(SnpxAssignmentError, match="no assignment covers PR"):
        t.locate("PR", 1, group=2)


def test_runs_split_across_assignments() -> None:
    t = AssignmentTable([A(1, 4, "R[1]", slot=1), A(101, 4, "R[3]", slot=2)])
    runs = t.plan_runs("R", 1, 4)
    assert [(r.address, r.count) for r in runs] == [(1, 2), (101, 2)]


def test_lower_slot_shadows_higher_slot() -> None:
    # Manual example: slot 1 maps R[1..1000] 16-bit over %R1..%R1000, slot 2 maps PR[1]
    # at %R101..%R150; PR[1] is unreachable because slot 1 wins.
    t = AssignmentTable([A(1, 1000, "R[1]@1.1", 1, slot=1), A(101, 50, "PR[1]", 100, slot=2)])
    with pytest.raises(SnpxAssignmentError, match="lower slot"):
        t.locate("PR", 1)


def test_unshadowed_alternative_is_used() -> None:
    t = AssignmentTable(
        [A(1, 2, "R[1]", slot=1), A(1, 4, "R[5]", slot=2), A(11, 4, "R[5]", slot=3)]
    )
    assert t.locate("R", 5).address == 11  # slot 2 is shadowed by slot 1 at %R1..%R2
    assert t.locate("R", 6).address == 3  # slot 2's second element is not shadowed


def test_sysvar_lookup_along_last_index() -> None:
    t = AssignmentTable([A(1, 150, "$MNUFRAME[1,1]", 0, slot=1, st=SysvarType.POSITION)])
    assert t.locate_sysvar("$mnuframe[1,3]").address == 101
    with pytest.raises(SnpxAssignmentError):
        t.locate_sysvar("$MNUFRAME[1,4]")
    with pytest.raises(SnpxAssignmentError):
        t.locate_sysvar("$MNUFRAME[2,1]")


def test_json_round_trip() -> None:
    t = AssignmentTable(
        [A(1, 20, "R[1]", slot=1), A(21, 50, "$MNUTOOL[1,1]", 0, slot=2, st=SysvarType.POSITION)]
    )
    back = AssignmentTable.from_json(t.to_json())
    assert back.entries == t.entries


def test_duplicate_slots_rejected() -> None:
    with pytest.raises(SnpxAssignmentError, match="duplicate"):
        AssignmentTable([A(1, 2, "R[1]", slot=1), A(3, 2, "R[2]", slot=1)])


# --- planning ----------------------------------------------------------------------------------


def test_plan_allocates_back_to_back() -> None:
    plan = plan_assignments(
        [
            AssignmentRequest.of("R[1]", 30),
            AssignmentRequest.of("PR[101]", 250),
            AssignmentRequest.of("POS[G1:0]"),
            AssignmentRequest.of("$MNUFRAME[1,1]", 9, sysvar_type=SysvarType.POSITION),
            AssignmentRequest.of("SR[1]", 2),
        ],
        (1, 16384),
    )
    assert [(e.address, e.size, e.multiply, e.slot) for e in plan] == [
        (1, 60, 0.0, 1),
        (61, 12500, 0.0, 2),
        (12561, 50, 0.0, 3),
        (12611, 450, 0.0, 4),
        (13061, 80, 1.0, 5),
    ]


def test_plan_respects_block_and_foreign_entries() -> None:
    with pytest.raises(SnpxAssignmentError, match="beyond the block"):
        plan_assignments([AssignmentRequest.of("PR[1]", 3)], (1000, 1100))
    foreign = [A(1050, 10, "R[1]", slot=1)]
    with pytest.raises(SnpxAssignmentError, match="overlaps existing"):
        plan_assignments([AssignmentRequest.of("PR[1]")], (1001, 2000), avoid=foreign)
    with pytest.raises(SnpxAssignmentError, match="consecutively"):
        plan_assignments([AssignmentRequest.of("POS[0]", 2)], (1, 1000))
