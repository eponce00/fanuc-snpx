"""Read-only first-contact survey of a controller (Phase 2 of docs/PLAN.md).

``run_survey`` performs a fixed list of read-only steps, records every frame in
an evidence log, and writes ``survey.json`` and ``survey.md`` into an output
directory. No step writes controller memory, sends a %G command, or uploads a
file. Two optional steps can make the controller post an SNPX communication
alarm (PRIO-090) on the pendant and are therefore off by default:

* ``error_probe``: one read of an address outside the assignable %R range, to
  record the controller's real error status bytes;
* request sizes above 2048 bytes in the size-limit probe.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .errors import SnpxError, SrtpServiceError
from .evidence import EvidenceLog
from .memory import Segment
from .oracle import ControllerFiles
from .parsers import parse_numreg, parse_snpx_config
from .srtp import DEFAULT_PORT, RateLimiter, ServiceCode, SrtpSession

DEFAULT_FTP_FILES = (
    "numreg.va",
    "posreg.va",
    "strreg.va",
    "sysframe.va",
    "system.va",  # holds $SNPX_ASG and $SNPX_PARAM (there is no syssnpx.va)
    "iostate.dg",
    "curpos.dg",
    "errall.ls",
    "version.dg",
    "summary.dg",
)
DEFAULT_SIZE_STEPS_BYTES = (6, 8, 64, 256, 512, 1024, 2048)
EXTENDED_SIZE_STEPS_BYTES = (4096, 8192, 16384)
LATENCY_SAMPLES = 20


class FileSource(Protocol):
    def list(self, path: str = "") -> list[str]: ...
    def download(self, name: str, dest_dir: str | Path) -> Path: ...
    def close(self) -> None: ...


@dataclass
class Step:
    name: str
    ok: bool = True
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    seconds: float = 0.0


@dataclass
class SurveyReport:
    host: str
    port: int
    started: str
    steps: list[Step] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=repr)

    def to_markdown(self) -> str:
        lines = [
            f"# SNPX survey of {self.host}:{self.port}",
            "",
            f"Started {self.started} (UTC). Read-only. Frames are in `evidence.jsonl`.",
            "",
            "| Step | Result | Time (s) | Details |",
            "|---|---|---|---|",
        ]
        for s in self.steps:
            result = "ok" if s.ok else f"FAILED: {s.error}"
            details = "; ".join(f"{k}={_short(v)}" for k, v in s.detail.items())
            lines.append(f"| {s.name} | {result} | {s.seconds:.3f} | {details} |")
        return "\n".join(lines) + "\n"


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=repr)
    text = text.replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _record(report: SurveyReport, name: str, fn: Callable[[Step], None]) -> Step:
    step = Step(name)
    t0 = time.perf_counter()
    try:
        fn(step)
    except SnpxError as exc:
        step.ok = False
        step.error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, SrtpServiceError):
            step.detail.update(
                major=f"0x{exc.major:02X}",
                minor=f"0x{exc.minor:02X}",
                msg_type=f"0x{exc.msg_type:02X}",
                header=(exc.frame or b"").hex(" "),
            )
    except Exception as exc:  # noqa: BLE001 - a survey step must never abort the survey
        step.ok = False
        step.error = f"{type(exc).__name__}: {exc}"
    step.seconds = time.perf_counter() - t0
    report.steps.append(step)
    return step


def run_survey(
    host: str,
    out_dir: str | Path,
    *,
    port: int = DEFAULT_PORT,
    timeout: float = 2.0,
    max_requests_per_second: float = 5.0,
    error_probe: bool = False,
    large_sizes: bool = False,
    ftp: bool = True,
    ftp_files: Iterable[str] = DEFAULT_FTP_FILES,
    file_source: Callable[[str], FileSource] | None = None,
    session_factory: Callable[..., SrtpSession] | None = None,
) -> SurveyReport:
    """Run the read-only survey and write ``survey.json``/``survey.md`` into ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = SurveyReport(host, port, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    limiter = RateLimiter(max_requests_per_second)
    make = session_factory or SrtpSession

    with EvidenceLog(out / "evidence.jsonl") as evidence:
        evidence.record("survey_start", host=host, port=port, error_probe=error_probe)

        def session(**kw: Any) -> SrtpSession:
            return make(
                host,
                port,
                timeout=timeout,
                evidence=evidence,
                rate_limiter=limiter,
                **kw,
            )

        # 1-2. Handshake variants and short status.
        for label, control in (("plain", False), ("session_control", True)):

            def handshake(step: Step, control: bool = control) -> None:
                with session(session_control=control) as s:
                    s.open()
                    step.detail.update(s.stats.handshake)
                    r = s.service(ServiceCode.PLC_SHORT_STATUS)
                    step.detail["short_status_42_55"] = r.header[42:56].hex(" ")
                    step.detail["short_status_msg_type"] = f"0x{r.msg_type:02X}"
                    step.detail["short_status_text"] = r.text.hex(" ")
                    step.detail["byte51"] = r.header[51]

            _record(report, f"handshake_{label}", handshake)

        # 3. Information services (raw replies).
        for svc in (
            ServiceCode.RETURN_CONTROLLER_TYPE,
            ServiceCode.RETURN_PROGRAM_NAME,
            ServiceCode.RETURN_FAULT_TABLE,
        ):

            def info(step: Step, svc: ServiceCode = svc) -> None:
                with session() as s:
                    r = s.service(svc)
                    step.detail.update(
                        msg_type=f"0x{r.msg_type:02X}",
                        header_42_55=r.header[42:56].hex(" "),
                        text=r.text.hex(" "),
                        ascii="".join(chr(b) if 0x20 <= b < 0x7F else "." for b in r.text),
                    )

            _record(report, f"service_0x{int(svc):02X}_{svc.name.lower()}", info)

        # 4. A few harmless reads (with the default session settings).
        def small_reads(step: Step) -> None:
            with session() as s:
                step.detail["R1_10_words"] = s.read_words(Segment.R, 1, 10).hex(" ")
                step.detail["DO1_16(%I1)"] = _bits(s.read_bits(Segment.I_BIT, 1, 16))
                step.detail["DI1_16(%Q1)"] = _bits(s.read_bits(Segment.Q_BIT, 1, 16))
                step.detail["UO1_16(%I6001)"] = _bits(s.read_bits(Segment.I_BIT, 6001, 16))
                step.detail["UI1_16(%Q6001)"] = _bits(s.read_bits(Segment.Q_BIT, 6001, 16))
                step.detail["GO1_4(%AI1)"] = s.read_words(Segment.AI, 1, 4).hex(" ")
                step.detail["GI1_4(%AQ1)"] = s.read_words(Segment.AQ, 1, 4).hex(" ")

        _record(report, "small_reads", small_reads)

        # 5. Largest single read the controller answers (no chunking).
        sizes = DEFAULT_SIZE_STEPS_BYTES + (EXTENDED_SIZE_STEPS_BYTES if large_sizes else ())

        def size_limit(step: Step) -> None:
            largest = 0
            with session(max_read_bytes=max(sizes)) as s:
                for n_bytes in sizes:
                    t0 = time.perf_counter()
                    try:
                        data = s.read_words(Segment.R, 1, n_bytes // 2)
                    except SnpxError as exc:
                        step.detail[f"{n_bytes}B"] = f"{type(exc).__name__}: {exc}"
                        break
                    ms = 1000 * (time.perf_counter() - t0)
                    step.detail[f"{n_bytes}B"] = f"ok {len(data)}B {ms:.1f}ms"
                    largest = n_bytes
            step.detail["largest_ok_bytes"] = largest

        _record(report, "size_limit", size_limit)

        # 6. Latency of a typical 30-word block read.
        def latency(step: Step) -> None:
            samples: list[float] = []
            with session() as s:
                s.open()
                for _ in range(LATENCY_SAMPLES):
                    t0 = time.perf_counter()
                    s.read_words(Segment.R, 1, 30)
                    samples.append(1000 * (time.perf_counter() - t0))
            step.detail.update(
                samples=len(samples),
                min_ms=round(min(samples), 2),
                median_ms=round(statistics.median(samples), 2),
                max_ms=round(max(samples), 2),
                note="includes rate-limit waits only if the limit was reached",
            )

        _record(report, "latency_R1_30", latency)

        # 7. Optional: the controller's real error reply.
        if error_probe:

            def bad_read(step: Step) -> None:
                with session() as s:
                    s.read_words(Segment.R, 16385, 1)
                    step.detail["note"] = "read of %R16385 unexpectedly succeeded"

            _record(report, "error_probe_R16385", bad_read)

        # 8. Controller files over read-only FTP.
        if ftp:

            def files(step: Step) -> None:
                opener = file_source or (lambda h: ControllerFiles(h, timeout=10.0))
                src = opener(host)
                try:
                    listing = src.list()
                    step.detail["listing_count"] = len(listing)
                    (out / "ftp-listing.txt").write_text("\n".join(listing), encoding="utf-8")
                    saved, missing = [], []
                    for name in ftp_files:
                        try:
                            saved.append(src.download(name, out / "files").name)
                        except Exception as exc:  # noqa: BLE001 - record and continue
                            missing.append(f"{name}: {type(exc).__name__}: {exc}")
                    step.detail.update(saved=saved, missing=missing)
                finally:
                    src.close()

            _record(report, "ftp_files", files)
            _record(report, "asg_map", lambda step: _asg_map(out, step))
            _record(report, "oracle_R1_10", lambda step: _compare_r1_10(out, report, step))
        evidence.record("survey_end", steps=len(report.steps))

    (out / "survey.json").write_text(report.to_json(), encoding="utf-8")
    (out / "survey.md").write_text(report.to_markdown(), encoding="utf-8")
    return report


def _asg_map(out: Path, step: Step) -> None:
    """Parse ``system.va`` into ``asg_map.md`` / ``asg_map.json`` (read-only, offline)."""
    path = out / "files" / "system.va"
    if not path.exists():
        step.ok = False
        step.error = "system.va was not downloaded"
        return
    cfg = parse_snpx_config(path.read_text(encoding="latin-1"))
    table, problems = cfg.to_table()
    (out / "asg_map.md").write_text(cfg.to_markdown(), encoding="utf-8")
    (out / "asg_map.json").write_text(
        json.dumps(
            {
                "params": cfg.params,
                "slots": [asdict(s) for s in cfg.used_slots],
                "table": json.loads(table.to_json()),
                "problems": problems,
            },
            indent=2,
            default=repr,
        ),
        encoding="utf-8",
    )
    step.detail.update(
        used_slots=len(cfg.used_slots),
        multiplexed=cfg.multiplexed,
        NUM_CIMP=cfg.params.get("$NUM_CIMP"),
        NUM_FRIF=cfg.params.get("$NUM_FRIF"),
        VERSION=cfg.params.get("$VERSION"),
        problems=problems,
    )


def _compare_r1_10(out: Path, report: SurveyReport, step: Step) -> None:
    """Show %R1..%R10 (as signed 16-bit) next to R[1..10] from numreg.va. No verdict."""
    path = out / "files" / "numreg.va"
    reads = next((s for s in report.steps if s.name == "small_reads" and s.ok), None)
    if not path.exists() or reads is None:
        step.ok = False
        step.error = "needs numreg.va and a successful small_reads step"
        return
    raw = bytes.fromhex(str(reads.detail["R1_10_words"]))
    words = [int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, 20, 2)]
    regs = parse_numreg(path.read_text(encoding="latin-1"))
    step.detail["pairs(srtp_int16, numreg.va)"] = [
        (w, regs[i + 1].value if i + 1 in regs else None) for i, w in enumerate(words)
    ]
    step.detail["note"] = "files are snapshots; the int16 view assumes slot 1 = R[1]@1.1"


def _bits(values: list[bool]) -> str:
    return "".join("1" if v else "0" for v in values)


__all__ = ["DEFAULT_FTP_FILES", "Step", "SurveyReport", "run_survey"]
