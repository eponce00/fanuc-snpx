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

The quickest way is the survey, which runs the whole checklist and writes `survey.md`, `survey.json`,
`evidence.jsonl` and the downloaded files into one folder (about 10-20 s at 5 requests/s):

```bash
fanuc-snpx survey 10.50.160.51 --out evidence/survey-2026-10-xx
```

Steps: handshake without and with the `0x4F` packet (plus short status), services `0x43`/`0x03`/`0x38`,
small reads (%R1-10, DO/DI/UO/UI 1-16, GO/GI 1-4), the largest single read up to 2048 bytes, latency of
20 reads of %R1-30, and FTP downloads of `numreg.va`, `posreg.va`, `strreg.va`, `sysframe.va`,
`syssnpx.va`, `curpos.dg`, `errall.ls`, `version.dg`, `summary.dg`. Two optional steps may make
the controller post an SNPX communication alarm (PRIO-090) and only run when asked: `--error-probe`
(one read of %R16385) and `--large-sizes` (reads of 4-16 KiB).

The same steps by hand:

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
