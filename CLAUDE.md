# Agent guide for fanuc-snpx

Read this first. Then read, in order: [docs/TRACKER.md](docs/TRACKER.md) (status, next steps, open
questions), [docs/BRIEF.md](docs/BRIEF.md) (the owner's original requirements), [docs/SAFETY.md](docs/SAFETY.md),
[docs/PROTOCOL.md](docs/PROTOCOL.md).

## What this is

A generic, pure-Python (stdlib-only, >= 3.10) SNPX / GE SRTP client for FANUC robot controllers with the
"HMI Device (SNPX)" option (R553), TCP 18245. Read-only by default. Owner: Ernesto (GitHub `eponce00`).
Repository: `eponce00/fanuc-snpx` (private, Apache-2.0).

## Non-negotiable rules

1. **No robot traffic unless the owner has said, in this conversation, that you are connected** and who is
   at the pendant. Then follow the phase gates in docs/TRACKER.md and stop at each gate to report.
2. **Phase 2 and 3 are read-only.** No `write_*`, no %G commands, no `CLRASG`/`SETASG`/`SETVAR`/`CLRALM`.
   FTP: list and download only (`fanuc_snpx.oracle` enforces this).
3. **Writes (Phase 4) only with the owner's local policy file and scratch targets the owner named**, someone at
   the pendant, read-back through SRTP and FTP, originals restored afterwards.
4. **Never send `CLRASG` unless the controller's own `$SNPX_PARAM` proves multiplexed (session-scoped)
   assignments are on.** Otherwise it erases the shared `$SNPX_ASG` table that other HMIs/PLCs may use.
5. Never implement run/stop, program control, alarm reset, forcing, program load, motion, or the port-60008
   protocol. Never decompile or patch vendor binaries.
6. Generic library: no cell-specific register numbers, names or layouts in code, tests or examples. The test
   robot's identity may appear in docs/VALIDATION_LOG.md, docs/ASG_MAP.md and docs/TRACKER.md only.
7. Never commit: `vendor-docs/` (FANUC/GE manuals and the owner's cell documents), `evidence/`,
   `controller-dumps/`, `*.local.json` (policy files), controller files (`*.va`, `*.dg`, `*.ls`, ...).
   `.gitignore` covers these; check `git status` before every commit.
8. Never claim "verified on the robot" without a docs/VALIDATION_LOG.md entry with raw bytes and the FTP
   oracle value. When the robot disagrees with a doc, the robot wins: update docs/PROTOCOL.md.
9. `main` is PR-only (not enforceable on this GitHub plan, so it is on you): branch, PR, CI green, merge.
   End commit messages with the attribution trailer your harness specifies.

## Gate report format (from the brief)

1. What you did (commands, files). 2. What you measured (hex + decoded). 3. What differed from the brief.
4. What you need from the owner. 5. The next gate.

## Environment (owner's PC)

- Windows 11, PowerShell. Repo: `E:\ErnestoProfile\Documents\GitHub\fanuc-snpx`. LF line endings.
- Dev venv: `.venv` (Python 3.13): `.venv\Scripts\python.exe -m pip install -e ".[dev]"`.
- Checks (all must pass before a PR):
  `.venv\Scripts\python.exe -m ruff format --check src tests`,
  `.venv\Scripts\python.exe -m ruff check src tests`, `.venv\Scripts\python.exe -m mypy`,
  `.venv\Scripts\python.exe -m pytest`.
- `gh` is logged in as `eponce00` (active).
- Local, git-ignored reference material: `vendor-docs/` (SNPX-related PDFs and markdown extracted from the
  owner's zip `E:\ErnestoProfile\Desktop\Enclosure_Robot_and_Fanuc_Documentation.zip`) and
  `vendor-docs/research/` (text extracts: FANUC B-82604EN CIMPLICITY manual = best public SNPX reference,
  GE GFK-0582D, Kepware GE Ethernet manual). If missing, re-extract from the zip or see
  docs/research/PUBLIC_SOURCES.md for URLs.
- Reference implementations (re-clone if needed, read only):
  `https://github.com/valstad-shipworks/fanuc_ucl` (Apache-2.0, `src/hmi/`),
  `https://github.com/Booozie-Z/Fanuc_GESRTP_Driver` (MIT, `docs/srtp packets.txt` = wire captures).

## Code map

`srtp.py` frames + session (one request at a time, close on any error) · `memory.py` segments, the single
`srtp_index()` conversion, packing · `assignments.py` `$SNPX_ASG` model, lookup, planner, session manager ·
`client.py` typed API and guarded writes · `types.py` Position etc. · `policy.py` access policy ·
`oracle.py` read-only FTP · `parsers.py` controller files (numreg/posreg/sysframe/system.va/curpos/errall/iostate; `fanuc-snpx parse`) · `survey.py` Phase 2 read-only survey (also writes `asg_map.md`) · `status.py` GE error-code hints ·
`testing.py` FakeSrtpServer/FakeController · `cli.py` `fanuc-snpx` command.
