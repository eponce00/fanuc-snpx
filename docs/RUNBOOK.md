# Runbook

## Development (no robot)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows; use .venv/bin/python on Linux/macOS
.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m mypy
```

## Phase 2: first read-only contact (only after the robot owner says go)

Everything below is read-only. Keep an evidence log for every run.

```bash
# 1. Handshake + short status, with and without the 0x4F session control packet
fanuc-snpx probe 10.50.160.51 --evidence evidence/phase2.jsonl --controller-type
fanuc-snpx probe 10.50.160.51 --evidence evidence/phase2.jsonl --no-session-control

# 2. A few harmless reads
fanuc-snpx read-raw 10.50.160.51 R 1 10 --evidence evidence/phase2.jsonl
fanuc-snpx read-io  10.50.160.51 DO 1 16 --evidence evidence/phase2.jsonl
fanuc-snpx read-io  10.50.160.51 GO 1 4  --evidence evidence/phase2.jsonl

# 3. Controller files (read-only FTP) for the oracle and the assignment map
fanuc-snpx ftp-get 10.50.160.51 --list
fanuc-snpx ftp-get 10.50.160.51 numreg.va posreg.va sysframe.va curpos.dg errall.ls --dest controller-dumps
```

`controller-dumps/` and `evidence/` are git-ignored. Copy the relevant hex and values into
`docs/VALIDATION_LOG.md` by hand, with timestamps.

## Hardware tests

```bash
FANUC_SNPX_HARDWARE=1 FANUC_SNPX_HOST=10.50.160.51 pytest tests/hardware
```

## If something goes wrong

- `SrtpTimeout` / `SrtpConnectionError`: the session is already closed; the next call reconnects.
  Check the network, then the controller's SNPX settings (option R553, `$SNPX_PARAM`).
- `SrtpServiceError`: the controller rejected the request. The exception carries the raw status bytes and
  the header; record them in the validation log.
- `PRIO-096 "SNPX Connection is full"` on the pendant (reported in forums): too many clients; close others.
- Never "fix" a problem by sending `CLRASG`, `CLRALM` or by writing `$SNPX_ASG` from a script.
