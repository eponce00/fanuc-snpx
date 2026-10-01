"""The read-only survey against the fake controller."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fanuc_snpx.srtp import SrtpSession
from fanuc_snpx.survey import run_survey
from fanuc_snpx.testing import FakeSrtpServer
from helpers import fast_limiter


class FakeFiles:
    def __init__(self, host: str) -> None:
        self.files = {"numreg.va": b"[*NUMREG*]$NUMREG\n", "posreg.va": b"[*POSREG*]$POSREG\n"}
        self.closed = False

    def list(self, path: str = "") -> list[str]:
        return sorted(self.files)

    def download(self, name: str, dest_dir: str | Path) -> Path:
        if name not in self.files:
            raise FileNotFoundError(name)
        out = Path(dest_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(self.files[name])
        return out / name

    def close(self) -> None:
        self.closed = True


def test_survey_is_read_only_and_reports(tmp_path: Path) -> None:
    with FakeSrtpServer(max_reply_bytes=1024) as srv:

        def factory(host: str, port: int, **kw: Any) -> SrtpSession:
            kw["rate_limiter"] = fast_limiter()
            return SrtpSession(host, port, **kw)

        report = run_survey(
            srv.host,
            tmp_path,
            port=srv.port,
            timeout=1.0,
            max_requests_per_second=1000,
            file_source=FakeFiles,
            session_factory=factory,
        )
        frames = [f for c in srv.connections for f in c.frames[1:]]

    steps = {s.name: s for s in report.steps}
    assert all(s.ok for s in report.steps), [(s.name, s.error) for s in report.steps]
    assert steps["handshake_plain"].detail["byte51"] == 2
    assert steps["handshake_session_control"].detail["byte51"] == 4
    assert steps["size_limit"].detail["largest_ok_bytes"] == 1024
    assert "SrtpServiceError" in steps["size_limit"].detail["2048B"]
    assert steps["latency_R1_30"].detail["samples"] == 20
    assert steps["ftp_files"].detail["saved"] == ["numreg.va", "posreg.va"]
    assert len(steps["ftp_files"].detail["missing"]) == 7
    # Nothing but reads and information services went to the controller.
    assert all(f[31] == 0xC0 for f in frames)
    assert {f[42] for f in frames} <= {0x00, 0x03, 0x04, 0x38, 0x43, 0x4F}
    md = (tmp_path / "survey.md").read_text(encoding="utf-8")
    assert "| size_limit | ok |" in md
    data = json.loads((tmp_path / "survey.json").read_text(encoding="utf-8"))
    assert data["host"] == "127.0.0.1"
    assert (tmp_path / "evidence.jsonl").stat().st_size > 0
    assert (tmp_path / "files" / "numreg.va").exists()


def test_error_probe_is_opt_in(tmp_path: Path) -> None:
    with FakeSrtpServer() as srv:
        report = run_survey(srv.host, tmp_path, port=srv.port, ftp=False, error_probe=True,
                            max_requests_per_second=1000)  # fmt: skip
    names = [s.name for s in report.steps]
    assert "error_probe_R16385" in names
    assert "ftp_files" not in names
