"""Command-line tool: ``fanuc-snpx <command> ...``.

Read commands are always available. Write commands need ``--policy``,
``--reason`` and a confirmation (interactive, or ``--yes``), and are refused
otherwise. Output is JSON so it can be logged or piped.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__, parsers
from .assignments import AssignmentTable
from .client import SnpxClient
from .errors import SnpxError
from .oracle import ControllerFiles
from .srtp import DEFAULT_PORT
from .survey import run_survey
from .types import IoFamily


def _jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, bytes | bytearray):
        return bytes(obj).hex(" ")
    if isinstance(obj, list | tuple):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {
            (k if isinstance(k, str) else str(list(k) if isinstance(k, tuple) else k)): _jsonable(v)
            for k, v in obj.items()
        }
    return obj


def _print(obj: Any) -> None:
    print(json.dumps(_jsonable(obj), indent=2, default=repr))


def _table(args: argparse.Namespace) -> AssignmentTable | None:
    if getattr(args, "map", None):
        return AssignmentTable.load(args.map)
    if getattr(args, "factory_default", False):
        return AssignmentTable.factory_default()
    return None


def _client(args: argparse.Namespace, *, writes: bool = False) -> SnpxClient:
    return SnpxClient(
        args.host,
        args.port,
        timeout=args.timeout,
        evidence=args.evidence,
        assignments=_table(args),
        session_control=not args.no_session_control,
        max_requests_per_second=args.rate,
        policy=getattr(args, "policy", None) if writes else None,
        allow_writes=writes,
    )


def _confirm(args: argparse.Namespace, what: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit("refusing to write without --yes in a non-interactive shell")
    answer = input(f"About to write {what} on {args.host} (reason: {args.reason}). Type 'yes': ")
    if answer.strip().lower() != "yes":
        raise SystemExit("write cancelled")


# --- commands ---------------------------------------------------------------------


def cmd_probe(args: argparse.Namespace) -> None:
    with _client(args) as c:
        c.connect()
        out: dict[str, Any] = {"handshake": c.session.stats.handshake}
        status = c.controller.short_status()
        out["short_status"] = {
            "bytes_42_55": status.raw_bytes_42_55,
            "privilege_level(unverified)": status.privilege_level,
            "status_word(unverified)": f"0x{status.status_word:04X}",
        }
        if args.controller_type:
            ct = c.controller.controller_type()
            out["controller_type"] = {"text_hex": ct.text.hex(" "), "ascii": ct.ascii_runs()}
        _print(out)


def cmd_read_raw(args: argparse.Namespace) -> None:
    with _client(args) as c:
        area = args.area.upper().lstrip("%")
        if area in ("R", "AI", "AQ"):
            _print(
                {
                    "area": f"%{area}",
                    "address": args.address,
                    "words": c.raw.read_words(area, args.address, args.count),
                }
            )
        else:
            _print(
                {
                    "area": f"%{area}",
                    "address": args.address,
                    "bits": c.raw.read_bits(area, args.address, args.count),
                }
            )


def cmd_read_io(args: argparse.Namespace) -> None:
    with _client(args) as c:
        values = c.io.read(args.family, args.index, args.count)
        _print({f"{args.family.upper()}[{args.index + i}]": v for i, v in enumerate(values)})


def cmd_read_reg(args: argparse.Namespace) -> None:
    with _client(args) as c:
        kind = args.kind.upper()
        if kind == "R":
            values: list[Any] = list(c.numeric_registers.read_block(args.index, args.count))
        elif kind == "PR":
            values = list(c.position_registers.read_block(args.index, args.count, group=args.group))
        elif kind == "SR":
            values = list(c.string_registers.read_block(args.index, args.count))
        else:
            raise SystemExit("kind must be R, PR or SR")
        _print({f"{kind}[{args.index + i}]": v for i, v in enumerate(values)})


def cmd_read_sysvar(args: argparse.Namespace) -> None:
    with _client(args) as c:
        _print({args.name: c.sysvars.read(args.name)})


def cmd_read_pos(args: argparse.Namespace) -> None:
    with _client(args) as c:
        _print(c.current_position.read(group=args.group, frame=args.frame))


def cmd_ftp_get(args: argparse.Namespace) -> None:
    with ControllerFiles(args.host, timeout=args.timeout) as files:
        if args.list:
            _print(files.list(args.list_path))
            return
        saved = [str(files.download(name, args.dest)) for name in args.names]
        _print({"saved": saved})


_PARSERS: dict[str, Any] = {
    "numreg": parsers.parse_numreg,
    "posreg": parsers.parse_posreg,
    "sysframe": parsers.parse_sysframe,
    "system": lambda text: {
        "snpx": parsers.parse_snpx_config(text),
        "multiplexed": parsers.parse_snpx_config(text).multiplexed,
    },
    "curpos": parsers.parse_curpos,
    "summary": parsers.parse_curpos,
    "errall": parsers.parse_errall,
    "iostate": parsers.parse_iostate,
}


def cmd_parse(args: argparse.Namespace) -> None:
    kind = args.kind or Path(args.file).name.lower().split(".")[0]
    if kind not in _PARSERS:
        raise SystemExit(f"unknown file kind {kind!r}; use --kind {'/'.join(_PARSERS)}")
    text = Path(args.file).read_text(encoding="latin-1")
    _print(_PARSERS[kind](text))


def cmd_survey(args: argparse.Namespace) -> None:
    report = run_survey(
        args.host,
        args.out,
        port=args.port,
        timeout=args.timeout,
        max_requests_per_second=args.rate,
        error_probe=args.error_probe,
        large_sizes=args.large_sizes,
        ftp=not args.no_ftp,
    )
    _print(
        {
            "out": str(args.out),
            "steps": {s.name: ("ok" if s.ok else s.error) for s in report.steps},
        }
    )


def cmd_write_reg(args: argparse.Namespace) -> None:
    kind = args.kind.upper()
    _confirm(args, f"{kind}[{args.index}] = {args.value!r}")
    with _client(args, writes=True) as c:
        if kind == "R":
            value: float | int = float(args.value) if "." in args.value else int(args.value)
            old: Any = c.numeric_registers.write(args.index, value, reason=args.reason)
        elif kind == "SR":
            old = c.string_registers.write(args.index, args.value, reason=args.reason)
        else:
            raise SystemExit("write-reg supports R and SR")
        _print({"target": f"{kind}[{args.index}]", "old": old, "new": args.value, "verified": True})


def cmd_write_io(args: argparse.Namespace) -> None:
    fam = IoFamily[args.family.upper()]
    raw = [v.strip() for v in args.values.split(",")]
    values: list[Any] = (
        [int(v) for v in raw] if fam.is_word else [v.lower() in ("1", "on", "true") for v in raw]
    )
    _confirm(args, f"{fam.name}[{args.index}..] = {values}")
    with _client(args, writes=True) as c:
        old = c.io.write(fam, args.index, values, reason=args.reason)
        _print({"target": f"{fam.name}[{args.index}]", "old": old, "new": values, "verified": True})


# --- parser -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fanuc-snpx", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"fanuc-snpx {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, *, assignments: bool = False) -> None:
        sp.add_argument("host")
        sp.add_argument("--port", type=int, default=DEFAULT_PORT)
        sp.add_argument("--timeout", type=float, default=2.0)
        sp.add_argument("--evidence", type=Path, help="append every frame to this JSONL file")
        sp.add_argument("--rate", type=float, default=5.0, help="max requests per second")
        sp.add_argument(
            "--no-session-control", action="store_true", help="skip the 0x4F packet after init"
        )
        if assignments:
            g = sp.add_mutually_exclusive_group()
            g.add_argument(
                "--map", type=Path, help="assignment table JSON describing the controller"
            )
            g.add_argument(
                "--factory-default",
                action="store_true",
                help="assume the factory table (R[1..10000] as 16-bit ints at %%R1)",
            )

    def writes(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--policy", type=Path, required=True, help="local access-policy JSON file")
        sp.add_argument("--reason", required=True, help="why this write is made (logged)")
        sp.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    sp = sub.add_parser("probe", help="handshake and short status (read only)")
    common(sp)
    sp.add_argument("--controller-type", action="store_true", help="also send service 0x43")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("read-raw", help="read %%R/%%AI/%%AQ words or %%I/%%Q/%%M/... bits")
    common(sp)
    sp.add_argument("area")
    sp.add_argument("address", type=int)
    sp.add_argument("count", type=int, nargs="?", default=1)
    sp.set_defaults(func=cmd_read_raw)

    sp = sub.add_parser("read-io", help="read DI/DO/RI/RO/UI/UO/SI/SO/WI/WO/WSI/WSO/GI/GO/AI/AO")
    common(sp)
    sp.add_argument("family")
    sp.add_argument("index", type=int)
    sp.add_argument("count", type=int, nargs="?", default=1)
    sp.set_defaults(func=cmd_read_io)

    sp = sub.add_parser("read-reg", help="read R/PR/SR through an assignment table")
    common(sp, assignments=True)
    sp.add_argument("kind")
    sp.add_argument("index", type=int)
    sp.add_argument("count", type=int, nargs="?", default=1)
    sp.add_argument("--group", type=int, default=1)
    sp.set_defaults(func=cmd_read_reg)

    sp = sub.add_parser("read-sysvar", help="read a mapped system variable")
    common(sp, assignments=True)
    sp.add_argument("name")
    sp.set_defaults(func=cmd_read_sysvar)

    sp = sub.add_parser("read-pos", help="read the current position (POS[] assignment)")
    common(sp, assignments=True)
    sp.add_argument("--group", type=int, default=1)
    sp.add_argument("--frame", type=int, default=0, help="0 world, 1-9 user frame, 15 current")
    sp.set_defaults(func=cmd_read_pos)

    sp = sub.add_parser("parse", help="parse a downloaded controller file to JSON (offline)")
    sp.add_argument("file", type=Path)
    sp.add_argument("--kind", choices=sorted(_PARSERS), help="default: from the file name")
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("survey", help="read-only first-contact survey (Phase 2) with a report")
    sp.add_argument("host")
    sp.add_argument("--out", type=Path, default=Path("evidence/survey"))
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--timeout", type=float, default=2.0)
    sp.add_argument("--rate", type=float, default=5.0, help="max requests per second")
    sp.add_argument("--no-ftp", action="store_true", help="skip the controller file downloads")
    sp.add_argument(
        "--error-probe",
        action="store_true",
        help="also read %%R16385 once to record the real error reply (may post PRIO-090)",
    )
    sp.add_argument(
        "--large-sizes",
        action="store_true",
        help="probe reads above 2048 bytes (may post PRIO-090)",
    )
    sp.set_defaults(func=cmd_survey)

    sp = sub.add_parser("ftp-get", help="download controller files (read-only FTP)")
    sp.add_argument("host")
    sp.add_argument("names", nargs="*")
    sp.add_argument("--dest", type=Path, default=Path("controller-dumps"))
    sp.add_argument("--list", action="store_true", help="list files instead of downloading")
    sp.add_argument("--list-path", default="")
    sp.add_argument("--timeout", type=float, default=10.0)
    sp.set_defaults(func=cmd_ftp_get)

    sp = sub.add_parser("write-reg", help="write R or SR (needs --policy, --reason, confirmation)")
    common(sp, assignments=True)
    writes(sp)
    sp.add_argument("kind")
    sp.add_argument("index", type=int)
    sp.add_argument("value")
    sp.set_defaults(func=cmd_write_reg)

    sp = sub.add_parser("write-io", help="write I/O (needs --policy, --reason, confirmation)")
    common(sp)
    writes(sp)
    sp.add_argument("family")
    sp.add_argument("index", type=int)
    sp.add_argument("values", help="comma separated: 1,0,1 or 560")
    sp.set_defaults(func=cmd_write_io)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SnpxError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
