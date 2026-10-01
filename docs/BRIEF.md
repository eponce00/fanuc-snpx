# Original mission brief (2026-10-01)

This is the project brief given by the repository owner (Ernesto) at the start, kept verbatim so any agent
can work from the same requirements. Answers to its section 11 are recorded at the end. Where the brief's
protocol guesses turned out different, [PROTOCOL.md](PROTOCOL.md) and [TRACKER.md](TRACKER.md) say so; the
robot is the authority.

---

# MISSION: Build `fanuc-snpx` — a generic, open, pure-Python SNPX client library for FANUC robots

You are a fresh agent creating a NEW repository. Build a general-purpose library: a free substitute
for the SNPX module of the commercial .NET library "UnderAutomation.Fanuc" (which needs a paid license
after a 30-day trial). It must stand on its own: no application-specific assumptions, no knowledge of any
particular robot cell, no hard-coded register numbers. Think "a good open-source client library that anyone
with a FANUC robot with the SNPX option could use".

You have no access to any other repositories. Everything you need is in this brief and in public sources.
Work in small verified steps. Ask the user (Ernesto) whenever this brief says to.

## 1. What to build and what "done" means

1. Package `fanuc_snpx`: pure Python (>= 3.10), standard library only at runtime, Windows + Linux + macOS.
   No .NET, no pythonnet, no vendor DLLs, no third-party runtime dependencies.
2. It speaks GE SRTP / SNP-X to TCP port 18245 on a FANUC controller with the "HMI Device (SNPX)" option (R553).
3. A clean, documented, typed API (section 5), a command-line tool, a fake SRTP server for tests, and
   documentation of the protocol as you actually observed it on real hardware.
4. Everything READ-capable is validated against an independent oracle (the controller's own files over FTP).
5. Writing is possible but gated: see section 4 (access policy). The library never writes by default.

Non-goals: motion control, program control (select/run/pause/abort), alarm reset, controller run/stop,
forcing I/O, KAREL/TP program loading, the FANUC port-60008 "PC Interface" protocol. Do not implement them,
and do not decompile or patch any vendor binary. Public information and your own captures of open-source
clients only.

## 2. Test robot (for development and validation)
- LR Mate 200iD/7L, controller F546175, software V9.30P/17, IP 10.50.160.51.
- Port 18245: GE SRTP / SNP-X (the target). Port 60008: FANUC PC Interface (ignore). Port 21: FTP,
  anonymous login works (used only to READ files for validation). Port 80: web server.
- A 56-byte all-zero SRTP init packet to 18245 was answered with a 56-byte reply whose byte 0 is 0x01.
  Nothing was read or written. That is the only traffic ever exchanged so far.
- The user's PC: Windows 11, PowerShell. No heredocs in the shell tool; edit files with real file edits.
- You do NOT have the FANUC SNPX (R553) manual. Ask the user for: "HMI Device (SNPX) R553 manual",
  "Ethernet Control Interface" PDFs, and the controller web "SNPX" help. Use them when provided.
- Until the user says you are connected to the robot, work on your own PC only (fake server, public sources).

## 3. Protocol: GE SRTP / SNP-X (a HYPOTHESIS — verify every byte)

The byte layout below is reconstructed from public references and memory. Treat it as a starting point.
Confirm against (a) the sources in 3.1 and (b) read-only exchanges with the real robot. Where sources and robot
disagree, the robot wins; document it in `docs/PROTOCOL.md` with a "verified on <controller/version> on <date>" column.

### 3.1 Public prior art (read, learn, keep license headers, cite in docs/PROVENANCE.md)
- `valstad-shipworks/fanuc_ucl` (Apache-2.0, Rust + Python): its `hmi` module is a complete FANUC SRTP client
  (service codes, segment selectors, registration of named system variables as assignments). PRIMARY reference.
- `Booozie-Z/Fanuc_GESRTP_Driver` (MIT, pure Python): digital I/O, numeric and string registers, group outputs.
- Wireshark's GE SRTP dissector (`packet-ge-srtp`) and public GE SRTP research write-ups.
- FANUC's own SNPX documentation (R553) if the user provides it.
If a license is unclear, re-implement from the protocol description rather than copying code.

### 3.2 Transport
- TCP port 18245; one socket = one SRTP session; `TCP_NODELAY`; per-operation timeouts (default 2 s).
- Session start: send a 56-byte all-zero init packet; the server answers 56 bytes with byte 0 = 0x01.
- Strict request/response, one outstanding request, matched by sequence number. On timeout, malformed frame, or
  stale sequence, close the socket and open a NEW session. Never try to resynchronise mid-stream.
- Always read exactly 56 header bytes first, then the payload length the header indicates.

### 3.3 Frame layout (little-endian; verify)
Data frame = 56-byte header + optional payload.
- byte 0: type (0x02 request, 0x03 response). Init: 0x00 request / 0x01 reply.
- byte 2: sequence number (+1 per request, wrap at 255); the response echoes it.
- bytes 4-5: payload length that follows the header (writes), LE16.
- byte 17: 0x02. byte 30: 0x06. byte 31: message type (0xC0 read request, 0x80 write request; success
  responses 0xD4 / 0xD1 — verify). bytes 36-37: mailbox constants (0x10 0x0E in public drivers).
  bytes 40-41: packet number / total (0x01 0x01).
- byte 42: service request code. Believed: 0x00 PLC short status, 0x03 controller type/ID, 0x04 read system
  memory, 0x07 write system memory, 0x0E fault table. IMPLEMENT ONLY read codes (0x00, 0x03, 0x04, 0x0E)
  and write-memory (0x07); the last is reachable only through the access policy (section 4).
- byte 43: segment selector. Believed: %R=0x08, %AI=0x0A, %AQ=0x0C, %I=0x10, %Q=0x12, %T=0x14, %M=0x16,
  %SA/%SB/%SC=0x18/0x1A/0x1C, %S=0x1E, %G=0x38; bit variants for %I/%Q (0x46/0x48...). Discover which exist.
- bytes 44-45: start address, 0-BASED (%R number - 1). bytes 46-47: element count (words for %R/%AI/%AQ).
- Responses: small reads (<= ~6 bytes) come back INLINE in the header (~bytes 44-49); larger reads append
  data after the 56-byte header. Error status in the major/minor bytes near the end of the header
  (~50-51); non-zero = failure -> raise a typed exception carrying both bytes.
- Per-request payload limit: discover on the real controller with growing read-only sizes; chunk transparently.

### 3.4 FANUC layer on top of SRTP
- The robot has no named register access over SRTP. It exposes a flat %R word area. Controller variables
  (registers, position registers, system variables, I/O, comments...) appear in %R through ASSIGNMENTS stored
  in the system variables `$SNPX_ASG[1..N]` (and `$SNPX_PARAM`). Each assignment names a variable
  (e.g. `R[1]`, `PR[7]`, `$MNUFRAME[1,2]`), a start %R address, a size/count, and a type/multiplier.
  Confirm exact field names, limits and semantics from the R553 manual and by reading the table that already exists.
- FIRST DELIVERABLE after connecting: a read-only map of the existing assignments (`docs/ASG_MAP.md`).
  Creating or changing assignments is a controller system-variable change: only with explicit user approval.
- Provide an assignment manager: it allocates a documented, fixed block of %R addresses for your own
  assignments (user-approved layout), tracks them, and can release them. It must detect and avoid overlapping
  with assignments it did not create.
- Encodings to confirm on hardware (expect little-endian; reals are IEEE-754 float32):
  numeric registers (int vs real view), position registers (Cartesian vs joint representation, configuration
  flags, user-frame/tool numbers, extended axes), position-typed system variables (6 floats), current pose and
  joints, group/digital I/O, string registers, flags, comments (likely no SRTP path — FTP read-only fallback).
- Concurrency: find the maximum simultaneous SRTP clients; never hog connections.

## 4. Safety architecture (built into the library, not just policy)

The library will eventually be pointed at production robots. It must be safe by construction.
1. READ-ONLY by default. A client is read-only unless constructed with `allow_writes=True` AND an
   `AccessPolicy` that explicitly permits the target.
2. `AccessPolicy` is loaded from a LOCAL file the user provides (default `./robot-policy.local.json`, or a path/
   env var). It is NEVER committed (gitignore it). Schema (version 1):
   - `protected`: per data kind (numeric_registers, position_registers, string_registers, system_variables,
     group_inputs, group_outputs, digital_io, flags, comments...), a list of index ranges or "*" that can
     never be written, even with `allow_writes=True`.
   - `writable`: ranges explicitly allowed for writes (the "scratch" targets). A write needs: not protected
     AND inside `writable`.
   - `limits`: robot limits the user knows (e.g. `max_position_register`) that the client enforces on reads and writes.
   - If no policy file exists, every write raises `WriteNotAllowed`.
3. Every write requires a caller-supplied `reason` string and goes through an optional `write_guard(target, old,
   new, reason)` callback; return False/raise to veto. Writes always read the old value first, write, read back
   and compare (float32 precision, configurable tolerance), and log old/new. Mismatch raises.
4. Never expose services for run/stop/reset/force/program-load. They don't exist in the code base.
5. Rate limits: default max 5 requests/second per connection, configurable, never unbounded loops.
6. Evidence logging (opt-in): every request/response frame as hex + decoded, JSONL with timestamps.
7. CLI: read commands always available; `write` commands exist but require `--policy`, `--reason`, and an
   interactive or flag confirmation, and refuse otherwise.

## 5. Public API (design it cleanly; document units, indexing, byte order, failure modes)

Indexing: FANUC is 1-based; SRTP addresses are 0-based. Do the conversion in exactly one helper with tests.
Suggested shape (use your judgment, keep it small and consistent):
- `SnpxClient(host, port=18245, timeout=2.0, policy=None, allow_writes=False, write_guard=None, evidence=None)`;
  context manager; `close()`; `reconnect()` = brand-new socket + session + assignments (see 6.2).
- Numeric registers: `read(i)`, `read_block(first, count)`, `write(i, value, reason=...)`. Int and real views.
- Position registers: `read(i)`, `read_block(first, count)`, `write(i, position, reason=...)`. A `Position`
  dataclass with Cartesian (x,y,z,w,p,r), joint (j1..j6) variants, configuration (turn counts, flip/up/top flags),
  user-frame and user-tool numbers, extended axes, and `is_cartesian`. Raising a clear error when the stored
  representation is not the one requested.
- System variables: typed reads/writes for int, real, string, and position-typed variables (`$MNUFRAME[g,i]`,
  `$MNUTOOL[g,i]` style). Require full index lists for multi-dimensional names (a missing group index can be
  silently accepted by a controller but target nothing — reject ambiguous spellings). Writes read back and compare.
- Current position: world/user/joint pose of a motion group, joints and Cartesian from ONE atomic controller
  read when the controller allows it.
- I/O: digital inputs/outputs, group inputs/outputs, analog, robot and UOP signals — READ by default. Writes of
  any I/O are protected by policy; document that PLC- or robot-owned signals must be protected by the user.
- String registers, flags, comments (with controller-imposed lengths).
- Controller info: model, software version, serial, short status, fault table (read only).
- Block reads must be fast: after assignments exist, reading a 30-register block should take a few ms and a
  few hundred position registers well under a second on a LAN. Measure and document.
Errors: `SrtpProtocolError`, `SrtpTimeout`, `SnpxAssignmentError`, `WriteNotAllowed`, `PolicyViolation`,
`ReadBackMismatch`. Never swallow errors. Never return a partial list as success.

## 6. Hard-won requirements (observed with another SNPX library on this same robot)
1. A failed block read can leave the session's protocol state desynchronised: subsequent single reads on the
   same connection also fail while a fresh connection works. Therefore errors in a batch must close the socket,
   and `reconnect()` must create a new session; test this with the fake server.
2. Some controllers accept a one-index spelling of a two-index system variable without error but the write hits
   nothing. Require the complete index list and always verify with a read-back.
3. FANUC reals are float32: compare read-backs at float32 precision.
4. Assignments created by a client may be session-scoped (released with the connection) or persistent —
   determine which and document it. Clean up what you create.
5. Unsupported operations must raise `NotImplementedError` with a clear message, not return guesses.

## 7. Validation: the robot's own files over FTP are the oracle (READ ONLY)
| Data | File |
|---|---|
| Numeric registers | `numreg.va` |
| Position registers | `posreg.va` |
| User frames/tools | `sysframe.va` |
| Current pose and joints | `curpos.dg` |
| Alarm history | `errall.ls` |
| I/O configuration | `diocfgsv.io` |
For each operation: SRTP value == FTP value (floats at float32 precision). Files are snapshots: compare when
values are not changing, re-read both twice. Log each comparison in `docs/VALIDATION_LOG.md` with timestamps
and raw bytes. Writes are validated only on scratch targets the user names, with read-back through both paths.
You may use FTP only to READ and download files. Never upload, delete or rename on the controller.

## 8. Repository layout (ask the user to confirm owner/name before `gh repo create`; private is fine)
- `pyproject.toml` (stdlib-only runtime; dev: pytest, ruff, mypy), `LICENSE` (ask the user; MIT or Apache-2.0)
- `src/fanuc_snpx/`: `srtp.py` (framing/init/sequence/chunking), `memory.py` (segments, word/bit packing,
  float32/int32/string), `assignments.py`, `client.py`, `types.py` (Position etc.), `errors.py`, `policy.py`
  (AccessPolicy), `oracle.py` (FTP READ-ONLY parsers), `evidence.py`, `cli.py`, `py.typed`
- `tests/`: `FakeSrtpServer` (scriptable %R image, error injection, short/split reads, delayed replies, stale
  sequence numbers, desync after a failed batch), golden-bytes fixtures for every frame, property tests for
  packing/chunking/index conversion, policy tests (every protected range rejected even with `allow_writes=True`;
  no policy file => no writes). `tests/hardware/` marked `@pytest.mark.hardware`, skipped unless
  `FANUC_SNPX_HARDWARE=1`, READ-ONLY.
- `docs/`: `PROTOCOL.md`, `ASG_MAP.md` (what was found on the test robot), `ARCHITECTURE.md`, `SAFETY.md`,
  `RUNBOOK.md`, `PROVENANCE.md`, `VALIDATION_LOG.md`, `POLICY.md` (schema + example with generic values)
- `.gitignore`: `evidence/`, `robot-policy.local.json`, `*.local.json`, controller dumps.
- CI (GitHub Actions): ruff, mypy, pytest (no hardware). `main` is PR-only: branch, PR, self-merge allowed.
- Engineering rules: boring, deterministic, typed code; sockets closed in `finally`; timeouts everywhere;
  no busy loops; no secrets in the repo; LF line endings in source.

## 9. Work plan (stop and report at each gate)
Phase 0 (PC only): read public sources; write `docs/PLAN.md` and `PROTOCOL.md` (hypothesis); list questions.
Phase 1 (PC only): `srtp.py`, `memory.py`, `FakeSrtpServer`, golden tests, policy engine.
Phase 2 (robot, read-only; only when the user says you are connected): init handshake, controller info,
a few harmless %R reads; reconcile the frame layout; read the existing assignments -> `ASG_MAP.md`.
  GATE 1: report the map; ask whether to create assignments.
Phase 3 (read-only): registers, position registers, system variables, current pose, I/O, each compared
  with the FTP oracle. GATE 2: report the comparison table.
Phase 4 (writes; ONLY with a user-provided policy file and user-named scratch targets): numeric register,
  position register, system variable; read-back through both paths; restore original values.
  GATE 3: report.
Phase 5: polish, docs, CLI, packaging, first release tag. The user will write their own application adapter
  against your public API; keep the API stable and versioned.

## 10. Report format at every gate
1. What you did (commands, files). 2. What you measured (hex + decoded). 3. What differed from this brief
(protocol deviations are expected). 4. What you need from the user. 5. The next gate. Never claim "verified on
the robot" without a log entry showing the FTP-oracle comparison.

## 11. Ask the user before starting
- Repo owner/name/visibility and license?
- Can you provide the R553 SNPX manual and the Ethernet Control Interface PDFs?
- When will you connect me to the robot, and who is at the pendant?

---

## Answers to section 11 (2026-10-01)

| Question | Answer |
|---|---|
| Repo | `eponce00/fanuc-snpx`, private (created 2026-10-01) |
| License | Apache-2.0 |
| Manuals | No R553 manual. The user supplied `Enclosure_Robot_and_Fanuc_Documentation.zip` (on their Desktop, 349 MB, mostly their own cell's documents). The SNPX-relevant subset is extracted to the git-ignored `vendor-docs/` folder. "You can always find more stuff online." The best public SNPX reference found is FANUC B-82604EN/01 chapter 6 (see PROVENANCE.md). |
| Robot | Not yet. PC-only until the user says "connected". A different agent may take over at that point: start from `CLAUDE.md` and `docs/TRACKER.md`. |
