# GE SRTP / FANUC SNPX as used by fanuc-snpx

This file records what the client sends and expects, where each fact comes from, and whether it has
been seen on real hardware. **The robot is the authority**: when a source and the robot disagree, the
robot wins and this table is updated with the evidence.

Status column:

- **robot**: observed on the project test robot (LR Mate 200iD/7L, R-30iB Plus F546175, V9.30P/17), with
  date and a reference into [VALIDATION_LOG.md](VALIDATION_LOG.md).
- **capture**: seen in published wire captures between a commercial GE Ethernet driver (Kepware) or a test
  client and *a* FANUC controller on port 18245 (Booozie-Z/Fanuc_GESRTP_Driver, `docs/srtp packets.txt`).
  Controller model and software unknown.
- **ref**: stated by public source code or documentation (see [PROVENANCE.md](PROVENANCE.md)), not seen in a
  capture.
- **hypothesis**: inferred; nothing confirms it yet.

Sources are abbreviated: **UCL** valstad-shipworks/fanuc_ucl, **BZ** Booozie-Z captures, **DIS** Palatis
Wireshark dissector, **CIMP** FANUC B-82604EN/01 "CIMPLICITY HMI for robots" chapter 6.

## 1. Transport

| Item | Value used | Sources | Status |
|---|---|---|---|
| TCP port | 18245 (HMI Device (SNPX), option R553) | BZ, CIMP, Kepware docs | robot: port open, 2026-09-30 |
| Alternative port | 60008 is the FANUC "Robot Interface" server. UCL and UnderAutomation talk SNPX-over-SRTP frames to it. **Not used**, per the project brief. | UCL `HMI_DEFAULT_PORT`, research notes | ref |
| Session | one TCP connection = one SRTP session; `TCP_NODELAY` | all | hypothesis (client choice) |
| Concurrency | one outstanding request; reply matched by sequence number | all | client choice |
| On error | close socket; next request opens a new session; failed request is never resent | brief §3.2/§6.1 | client choice |
| Max simultaneous clients | unknown; `$SNPX_PARAM.$NUM_CIMP` per CIMP (0 = multi-connection off). The test robot's guide lists `$NUM_FRIF: 4` and no `$NUM_CIMP` | CIMP, user's notes | to verify |
| Per-request size limit | client default 1024 bytes per read; larger reads are chunked. Kepware allows 32..2048 bytes. | Kepware manual | to verify (Phase 2) |

## 2. Session start

| Step | Bytes | Sources | Status |
|---|---|---|---|
| Init request | 56 × `00` | all | robot 2026-09-30 (before this repo) |
| Init reply | 56 bytes; byte 0 = `01`, byte 8 = `01`, rest `00` | UCL `INIT_ACK`; robot | robot 2026-09-30: first 16 bytes `01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00` |
| Session control | short request with service `4F`, segment `01`, byte 30 = `01`, seq 1. Reply `D4`. Kepware sends it with packet type `02`; UCL and the dissector use packet type `08`. This client uses `02` (Kepware). | BZ (Kepware), UCL, DIS | capture |
| Effect of session control | header byte 51 of replies goes from `02` to `04` afterwards (privilege level?) | BZ | capture; meaning hypothesis |
| Is it required on 18245? | unknown; the client sends it by default (`session_control=True`) | — | to verify (Phase 2: probe with and without) |

## 3. Request header (56 bytes, little-endian)

| Offset | Short read `C0` | Extended write `80` | Sources | Status |
|---|---|---|---|---|
| 0 | `02` | `02` | all | capture |
| 1 | `00` | `00` | | capture |
| 2 | sequence 1..255, wraps to 1 (0 = init) | same | UCL (u8), BZ | capture (echoed) |
| 3 | `00` (Kepware puts a 16-bit transaction number in 2-3) | same | BZ | capture |
| 4-5 | text length = 0 | payload length | all | capture |
| 9, 17 | `01` | `02` | BZ (Kepware). UCL uses the opposite (02 read / 01 write) and also works | capture |
| 26-28 | time s/m/h, sent as 0 | 0 | DIS | capture |
| 30 | `06` (Kepware constant; UCL sends the seq). Echoed by the controller. | `09` | BZ, UCL | capture |
| 31 | `C0` | `80` | all | capture |
| 32-35 | `00 00 00 00` (mailbox source) | same | all | capture |
| 36-39 | `10 0E 00 00` (mailbox destination) | same | all | capture |
| 40, 41 | packet 1 of 1 | 1 of 1 | all | capture |
| 42 | service code | payload length (low byte) | BZ | capture |
| 43 | segment selector | payload length (high byte, assumed) | BZ | capture / hypothesis |
| 44-45 | 0-based index | `00 00` | all | capture |
| 46-47 | count (words, bytes or bits by segment) | `00 00` | all | capture |
| 48, 49 | `00` | `01 01` (packet / total) | BZ | capture |
| 50 | `00` | service `07` | BZ | capture |
| 51 | `00` | segment | BZ | capture |
| 52-53 | `00` | 0-based index | BZ | capture |
| 54-55 | `00` | count | BZ | capture |
| 56.. | — | payload | BZ | capture |

Writes always use the extended `80` form (as Kepware does), even for one bit. Writes larger than
`max_write_bytes` (default 1024) are refused, not split, because a split write is not atomic.

## 4. Reply header

| Offset | Meaning | Sources | Status |
|---|---|---|---|
| 0 | `03` (reply). Anything else: protocol error | all | capture |
| 2-3 | echoes request bytes 2-3; mismatch = stale reply, session closed | all | capture |
| 4-5 | text length after the header | all | capture |
| 30 | echoes request byte 30 | BZ | capture |
| 31 | `D4` short ack, `94` extended ack (data after header), `D1` error | all | capture |
| 32-35 | `10 0E 00 00` | BZ | capture |
| 36-39 | `30 3A 00 00` | BZ, UCL tests | capture |
| 40, 41 | packet 1 of 1 (other values rejected) | | capture |
| 42-43 | status (major, minor). `00 00` in every captured success. **Also `00 00` in the captured `D1` errors**, so `D1` itself marks failure | BZ | capture |
| 44-49 | `D4` with data: up to 6 inline data bytes (rest zero) | BZ, UCL, DIS | capture |
| 48-53 | `D4` without data and `94`: `01 01 FF pp 00 00`, pp = `02` before / `04` after session control | BZ, UCL | capture; meaning hypothesis |
| 54-55 | `7C 21` in every capture (PLC status word per UCL: state bits 12-15 = 2) | BZ, UCL | capture; meaning hypothesis |

The client raises `SrtpServiceError` (carrying bytes 42 and 43) for `D1`, and also for a `D4`/`94` whose
bytes 42-43 are not zero. It raises `SrtpProtocolError` for an unknown message type, a `D4` with text, a
length mismatch, or a multi-packet reply, and checks byte 0, the sequence and the message type before it
trusts the length field.

Inline vs extended: replies with 6 data bytes or fewer came back as `D4` inline in every capture; 80 bytes
came back as `94`. The client accepts `94` for any size and `D4` only for 6 bytes or fewer.

## 5. Services

| Code | Name | Implemented | Status |
|---|---|---|---|
| `00` | PLC short status | yes, raw + bytes 51/54-55 | ref |
| `03` | return program name | yes, raw only | ref |
| `04` | read system memory | yes | capture |
| `07` | write system memory | yes, only through the access policy | capture |
| `38` | return fault table | yes, raw only | ref (UCL; the brief guessed `0E`) |
| `43` | return controller type | yes, raw only | ref (UCL, nerva) |
| `4F` | session control | yes (handshake) | capture |
| others | run/stop, clear fault, program load, force, set time, privilege, logon | **not present in the code base** | — |

## 6. Segments

| Selector | Area | Unit | Use on a FANUC robot | Status |
|---|---|---|---|---|
| `08` | %R | word | assignments (`$SNPX_ASG`) | capture |
| `0A` | %AI | word | GO[x] at %AIx, AO[x] at %AI(1000+x) | capture (GO) |
| `0C` | %AQ | word | GI[x] at %AQx, AI[x] at %AQ(1000+x) | ref |
| `46` | %I bit | bit | DO, RO (+5000), UO (+6000), SO (+7000), WO (+8000), WSO (+8400) | capture (DO write) |
| `48` | %Q bit | bit | DI, RI (+5000), UI (+6000), SI (+7000), WI (+8000), WSI (+8400) | capture (DI write) |
| `4A`, `4C`, `4E`, `50`, `52`, `54`, `56` | %T, %M, %SA, %SB, %SC, %S, %G bit | bit | %M: PMC internal relays | ref |
| `10`, `12`, ..., `1E` | %I, %Q, %T, %M, %SA, %SB, %SC, %S byte | byte | not used; a captured byte-selector write to %I/%Q got `D1` | capture |
| `38` | %G byte | byte | SNPX command strings (`CLRASG`, `SETASG`, ...) | ref (UCL, CIMP) |

Bit reads are issued byte-aligned (start rounded down, count rounded up to multiples of 8) and sliced.
Bit writes put the first value at bit position `index % 8` of the first payload byte (captured: DO[10] →
index 9 → payload `02`).

Robot inputs are PLC outputs and vice versa (CIMP §6.1). CIMP warns that writing DI/AI makes the
input change for a moment and then fall back to the real value; avoid it.

## 7. FANUC layer: `$SNPX_ASG`

See `src/fanuc_snpx/assignments.py` for the implementation. Facts from CIMP §6.2-6.11 (status **ref**
unless noted):

- 80 slots; each has `$ADDRESS` (1..16384), `$SIZE` (1..16384 words), `$VAR_NAME` (no blanks; STRING[37]
  per field reports), `$MULTIPLY` (0, or 0.0001..10000).
- Factory default slot 1: `1 / 10000 / R[1]@1.1 / 1`, i.e. %R1..%R10000 are R[1..10000] as 16-bit
  integers. The user's notes report exactly this on the test robot, with slots 2..80 empty (source of that
  capture not recorded; **to verify** with the controller's own file in Phase 2).
- Overlapping slots: the lower slot number wins. Unassigned %R reads 0.
- `$MULTIPLY`: 0 = IEEE float32 over 2 words; non-zero = int32 = round(value × multiply). Integer system
  variables: 0 behaves like 1. Strings, comments, alarms, program status: 1. I/O: 1 = one word per
  signal; 0 = 16 signals per word, lowest index in bit 0 (GI/GO/AI/AO always one word).
- `@a.b` takes `b` words starting at word `a` of each element (1-based).
- Element sizes (words): R 2, PR/POS/POSITION 50, SR 40, comment 40, ALM 100, PRG 18, scalar sysvar 2,
  STRING sysvar 40. `F[]` (flags) is not documented: **not implemented**.
- 32-bit values are low word first (UCL decodes 4 consecutive little-endian bytes): **to verify**.
- Strings: 2 characters per word, first character in the low byte, NUL padded (BZ capture of "adb").

### 50-word position structure

| Words | Content |
|---|---|
| 1-18 | X, Y, Z, W, P, R, E1, E2, E3 (real or scaled int32) |
| 19-22 | FLIP, LEFT, UP, FRONT (int16, 1/0) |
| 23-25 | TURN4, TURN5, TURN6 (int16, -128..127) |
| 26 | VALIDC (int16; 0 = Cartesian view not available) |
| 27-44 | J1..J9 |
| 45 | VALIDJ |
| 46 | UF (0 world, 15 = "current") |
| 47 | UT (0 = faceplate, 15 = "current") |
| 48-50 | reserved |

Notes:

- Reading converts between Cartesian and joint on the fly (milliseconds per PR). Elements of the view
  that cannot be converted read as 0, and that view's VALID word is 0.
- Writing any Cartesian element (or VALIDC) switches the PR to Cartesian; writing joint elements (or
  VALIDJ) switches it to joint. The client writes words 1-26 or 27-45 and **never UF/UT**, which cannot be
  restored from the pendant.
- **Discrepancy:** UCL decodes VALIDC/VALIDJ from the *high* byte of words 26/45, whereas CIMP calls them
  16-bit integers. This client reads the whole word and treats non-zero as valid. **To verify.**
- `POS[uf]` / `POS[Gg:uf]` is the current position (read only): uf 0 world, 1..9 that frame, 15 the
  selected frame; one element per slot. UT always reads 15.
- SNPX never says which representation a PR *stores*; `Position.is_cartesian` is only definite when exactly
  one view is valid.

### %G commands

Written as ASCII to %G (selector `38`, index 0, count = byte length):

| Command | Effect | Status |
|---|---|---|
| `CLRASG` | `$SNPX_PARAM.$VERSION` >= 2 **and** multi-connection on: creates a private, connection-scoped table. Otherwise: **erases the shared `$SNPX_ASG`**. | ref (CIMP §6.11.4); to verify |
| `SETASG a s var [m]` | adds an entry in the first free slot of the active table; multiply defaults to 1 | ref |
| `SETVAR $var value` | sets a system variable | ref; not used by this client |
| `CLRALM` | clears alarm history | ref; **not used by this client** |

The client sends `CLRASG` only through `AssignmentManager.apply_session(..., multiplex_confirmed=True)`.
UCL sends `CLRASG` on every connect to port 60008; doing that on a controller without multiplexing would
wipe the shared table, which is why this client never does it implicitly.

## 8. Open questions for Phase 2 (read-only)

1. Does 18245 need the `4F` session control? Does privilege (byte 51) matter for reads?
2. Largest read the controller answers in one `94` reply (try 64, 256, 1024, 2048, 4096 bytes).
3. Bytes 42-43 on a genuine read error (e.g. read beyond %R16384).
4. Contents of `$SNPX_PARAM` (`$VERSION`, `$NUM_CIMP` or equivalent) and `$SNPX_ASG` from the
   controller's own files over FTP, to decide whether `CLRASG` is safe.
5. Services `00`, `43`, `03`, `38`: raw replies.
6. %R1..%R10 (factory int16 view) against `numreg.va`.
