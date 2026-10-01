# Plan

Goal: a generic, open, pure-Python SNPX client (a free substitute for the SNPX part of commercial .NET
libraries) that is read-only by default and validated against the controller's own files.

| Phase | Where | Content | Exit gate |
|---|---|---|---|
| 0 | PC | Read public sources; protocol hypothesis ([PROTOCOL.md](PROTOCOL.md)); questions | done 2026-10-01 |
| 1 | PC | `srtp.py`, `memory.py`, fake server, golden-bytes tests, policy engine; client API, assignment model, CLI and FTP oracle built and tested against the fake controller | done 2026-10-01; report to user |
| 2 | robot, read-only | Handshake with/without `4F`; services `00`/`43`/`03`/`38`; a few %R/%I/%Q/%AI reads; size limit; read `$SNPX_PARAM`/`$SNPX_ASG` from the controller files over FTP → [ASG_MAP.md](ASG_MAP.md) | **GATE 1**: report the map, ask whether to create assignments |
| 3 | robot, read-only | Registers, PRs, frames/tools, current pose, I/O, strings, comments; each compared with the FTP oracle ([VALIDATION_LOG.md](VALIDATION_LOG.md)); write the `.va`/`.dg` parsers against real files; measure block-read speed | **GATE 2**: comparison table |
| 4 | robot, writes | Only with the user's local policy file and user-named scratch targets: R, PR, one system variable; read back through SRTP and FTP; restore originals; settle session vs persistent assignments | **GATE 3**: report |
| 5 | PC | Polish, docs, packaging, first release tag; keep the API stable for the user's adapter | release |

## Phase 1 deliverables (this commit)

- Framing (`srtp.py`): init, `4F` session control, `C0` reads, `80` writes, reply validation, chunking,
  timeouts, rate limiting, close-on-error with a fresh session on the next request.
- Memory (`memory.py`): segments, the single 1-based→0-based conversion, word/int/float32/string/bit
  packing, chunk planning.
- `testing.FakeSrtpServer`: scriptable memory image, independent model of `$SNPX_ASG`, faults
  (error reply, delay, split, stale sequence, wrong packet type, drop, garbage, multi-packet, short text,
  desync after a failed batch).
- Golden-bytes tests against published captures of a commercial driver talking to a FANUC controller.
- `policy.py`: local JSON policy (protected / writable / limits / controller host / assignment block).
- `client.py`: typed API (registers, PRs, strings, comments, system variables, frames, current position,
  I/O, controller info), guarded writes with read-back.
- `assignments.py`: `$VAR_NAME` parser/validator, element layouts, slot-precedence lookup, planner, and a
  session-scoped manager that refuses `CLRASG` unless multiplexing is confirmed.
- `oracle.py`: FTP client that can only list and download. File parsers wait for real files (Phase 3).
- `cli.py`: `probe`, `read-*`, `ftp-get`, guarded `write-reg` / `write-io`.

## Open questions for the user

1. Phase 2 needs network access to 10.50.160.51 (ports 18245 and 21). Who is at the pendant, and when?
2. Which %R block may this client use for its own assignments later (Phase 4), e.g. %R5001-%R6000? (Goes in
   your local policy file, not in this repo.)
3. Which scratch targets may be written in Phase 4 (one R, one PR, one frame/tool)?
4. Port 60008: public SNPX clients (fanuc_ucl, UnderAutomation) send the same SRTP frames there. The brief
   says to ignore it; confirm that stays so.
