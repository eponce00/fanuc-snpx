"""CLI against the fake server, and the read-only guarantees of the FTP oracle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fanuc_snpx.cli import main
from fanuc_snpx.oracle import ReadOnlyFtp
from fanuc_snpx.testing import FakeAssignment, FakeController, FakeSrtpServer


@pytest.fixture
def fake() -> FakeSrtpServer:
    ctl = FakeController()
    ctl.assign(1, FakeAssignment(1, 20, "R[1]", 0.0))
    ctl.numeric_registers.update({1: 2.5, 2: 4})
    ctl.bits[0x46][0] = 0b1
    return FakeSrtpServer(ctl)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> object:
    assert main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


def test_probe(fake: FakeSrtpServer, capsys: pytest.CaptureFixture[str]) -> None:
    with fake:
        out = run(capsys, "probe", fake.host, "--port", str(fake.port), "--rate", "1000",
                  "--controller-type")  # fmt: skip
    assert isinstance(out, dict)
    assert out["handshake"]["init_reply"].startswith("01 00")
    assert out["short_status"]["privilege_level(unverified)"] == 4
    assert "FAKE-SNPX" in out["controller_type"]["ascii"]


def test_read_commands(
    fake: FakeSrtpServer, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    amap = tmp_path / "map.json"
    amap.write_text(
        json.dumps({"version": 1, "assignments": [
            {"address": 1, "size": 20, "var_name": "R[1]", "multiply": 0, "slot": 1}]}),
        encoding="utf-8",
    )  # fmt: skip
    with fake:
        base = [fake.host, "--port", str(fake.port), "--rate", "1000"]
        assert run(capsys, "read-io", *base[:1], "DO", "1", "2", *base[1:]) == {
            "DO[1]": True,
            "DO[2]": False,
        }
        assert run(capsys, "read-reg", *base[:1], "R", "1", "2", *base[1:], "--map", str(amap)) == {
            "R[1]": 2.5,
            "R[2]": 4.0,
        }
        raw = run(capsys, "read-raw", *base[:1], "R", "1", "2", *base[1:])
        assert raw == {"area": "%R", "address": 1, "words": [0, 0x4020]}


def test_write_requires_policy_and_reason(fake: FakeSrtpServer) -> None:
    with fake, pytest.raises(SystemExit):
        main(["write-reg", fake.host, "--port", str(fake.port), "R", "1", "5"])


def test_write_refused_by_policy(
    fake: FakeSrtpServer, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    policy = tmp_path / "robot-policy.local.json"
    policy.write_text(json.dumps({"version": 1, "writable": {"digital_outputs": [[1, 4]]}}))
    with fake:
        args = ["write-io", fake.host, "--port", str(fake.port), "--rate", "1000",
                "--policy", str(policy), "--reason", "cli test", "--yes"]  # fmt: skip
        assert main([*args, "DO", "2", "1"]) == 0
        assert json.loads(capsys.readouterr().out)["old"] == [False]
        assert main([*args, "DO", "5", "1"]) == 2
        assert "WriteNotAllowed" in capsys.readouterr().err
    with fake.state() as ctl:
        assert ctl.bits[0x46][0] == 0b11


@pytest.mark.parametrize("cmd", ["STOR x", "DELE numreg.va", "RNFR a", "MKD d", "SITE x", "APPE y"])
def test_ftp_oracle_refuses_modifying_commands(cmd: str) -> None:
    ftp = ReadOnlyFtp()
    with pytest.raises(PermissionError, match="not allowed"):
        ftp.putcmd(cmd)


def test_parse_command(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    fix = Path(__file__).parent / "fixtures"
    target = tmp_path / "system.va"
    target.write_bytes((fix / "system_va.txt").read_bytes())
    out = run(capsys, "parse", str(target))
    assert isinstance(out, dict)
    assert out["multiplexed"] is False
    assert out["snpx"]["params"]["$NUM_CIMP"] == 0
    reg = run(capsys, "parse", str(fix / "numreg_va.txt"), "--kind", "numreg")
    assert isinstance(reg, dict)
    assert reg["3"]["value"] == 12.5


def test_plan_asg(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    fix = Path(__file__).parent / "fixtures" / "system_va.txt"
    out_map = tmp_path / "map.json"
    rc = main(["plan-asg", "--existing", str(fix), "--item", "R[1] 20", "--item", "PR[1] 10",
               "--item", "POS[G1:0]", "--out-map", str(out_map)])  # fmt: skip
    assert rc == 0
    text = capsys.readouterr().out
    assert "| 5 | 10515 | 40 | R[1] | 0 | %R10515..%R10554 |" in text
    assert "SETASG 10515 40 R[1] 0" in text
    assert "F[1]" in text  # existing slot the package cannot decode is reported
    data = json.loads(out_map.read_text(encoding="utf-8"))
    assert [e["slot"] for e in data["assignments"]] == [1, 2, 5, 6, 7]
    assert main(["plan-asg", "--item", "PR[1] 250", "--block", "10001-16384"]) == 2
    assert "whole plan needs 12500 words" in capsys.readouterr().err
