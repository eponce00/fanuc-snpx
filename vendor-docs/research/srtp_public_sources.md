# GE SRTP / FANUC SNPX: what public sources say (as of 2026-10-01)

Conventions: offsets are 0-based into the 56-byte header. Multi-byte fields are little-endian. "Index" is a 0-based element address, so %R1 is index 0. Tags such as [S1] refer to the source list at the end. **INFERRED** marks a conclusion I drew rather than read.

---

## 1. Wireshark dissectors

- **Mainline Wireshark:** I found no GE SRTP dissector in mainline. Wireshark ships `packet-egd.c` (EGD), which is a different protocol. This is INFERRED from searches only, not an exhaustive tree check.
- **Palatis/packet-ge-srtp** (Lua, BSD-3-Clause) [S1]. The same code is at PalaIndustrial/packet-ge-srtp (BSD-3, 7 commits).
  - Registers TCP 18245 and requires at least 56 bytes. It does not reassemble multi-packet responses or parse SNPX.
  - Tested on an R-30iB.
  - Anything after byte 56 is shown as raw "payload".

### Header layout (S1 field names; other sources noted)

| Off | Len | Field | Values / notes |
|---|---|---|---|
| 0 | 2 (S1) / 1 (S3) | pkt_type | 0x00 INIT, 0x01 INIT_ACK, 0x02 REQ, 0x03 REQ_ACK, 0x08 "UNKNOWN" (this is the session-control request; see §2). [S7] sees 0x05 on errors. |
| 2 | 2 (S1 "Sequence #") / 1 (S2, S3) | seq | Repeated at byte 30. |
| 4 | 2 | text length | Number of bytes that follow the 56-byte header [S1, S2, S6]. |
| 6–25 | — | unknown | Byte 9 = 0x01 and byte 17 = 0x01 in 0xC0 requests [S3, S6]. [S2] treats 8–11 and 16–19 as u32 0x00000100 (C0) or 0x00000200 (0x80). The INIT_ACK has byte 8 = 0x0F [S6, S7, S17]. |
| 26/27/28 | 1 each | sec/min/hour | |
| 29 | 1 | unknown | |
| 30 | 1 | msg_seq | Copy of the sequence number. |
| 31 | 1 | msg_type | See the table below. |
| 32–35 | 4 | mailbox source | 0x00000000 |
| 36–39 | 4 | mailbox destination | Bytes `10 0E 00 00` (0x00000E10). Every implementation uses this value. |
| 40 / 41 | 1 / 1 | packet # / total packets | 1 / 1 |

**Bytes 42–55 depend on the message type [S1, S2]:**

- **0xC0 short request:**
  - 42 = service code; 43 = segment selector
  - 44–45 = index; 46–47 = count
  - 48–53 = up to 6 bytes of inline write data; 54–55 unknown
- **0xD4 short ACK and 0xD1 short error:**
  - 42 = major status; 43 = minor status, where 0 means OK [S3, S4, S7]. [S1] only labels these two bytes "unknown u16".
  - 44–49 = up to 6 bytes of inline read data
  - 50–53 u32 and 54–55 u16 are unknown. INFERRED: this is the PLC "piggyback status" that [S9] mentions.
- **0x80 extended request:**
  - 42–47 unknown; [S8] sends 42 = 0, 43 = 0
  - 48 / 49 = packet # / total
  - 50 = service code; 51 = segment selector
  - 52–53 = index; 54–55 = count
  - Payload starts at byte 56, and the text length equals the payload size.
- **0x94 extended ACK:**
  - 42–47 unknown. INFERRED: status is at 42–43, as in the short ACK.
  - 48 / 49 = packet # / total; 50 = service code echoed; 51 = selector echoed; 52–55 unknown
  - Data starts at byte 56 (text-length bytes). [S5] notes that the data can arrive in a separate TCP segment.

**Inline vs. trailing data.** A response that carries 6 bytes or fewer comes back as 0xD4 with the data inline at 44–49 and text length 0. Anything larger comes back as 0x94 with the data after the header [S1, S2, S4]. [S2] applies the same 6-byte rule when choosing between a 0xC0 and a 0x80 write. [S4] always writes with 0x80, so both forms are accepted (INFERRED).

**Count units** follow the selector: words for 0x08/0x0A/0x0C, bytes for byte selectors, bits for bit selectors [S2, S9].

### Message types [S1, S2]

| Code | Meaning |
|---|---|
| 0xC0 | SHORT request |
| 0xD4 | SHORT_ACK |
| 0xD1 | SHORT_ERR / "SHORT_FAILED" |
| 0x80 | EXTENDED (LONG) request |
| 0x94 | EXTENDED_ACK |

### Service request codes (byte 42 or 50)

Sources: [S1, S2, S3, S8], plus [S9] (Horner, server side).

| Code | Name |
|---|---|
| 0x00 | PLC_SHORT_STATUS |
| 0x01 | PLC_LSTAT (long status; [S9] only) |
| 0x03 | GET_PROGNAME |
| 0x04 | READ_SYS_MEM |
| 0x05 | READ_TASK_MEM |
| 0x06 | READ_PROG_MEM |
| 0x07 | WRITE_SYS_MEM |
| 0x08 | WRITE_TASK_MEM |
| 0x09 | WRITE_PROG_MEM |
| 0x20 | PROG_LOGON |
| 0x21 | CHANGE_PRIV |
| 0x22 | SET_CPU_ID |
| 0x23 | SET_PLC_RUN |
| 0x24 | SET_PLC_TIME |
| 0x25 | GET_TIME |
| 0x38 | GET_FAULT |
| 0x39 | CLR_FAULT |
| 0x3F | PROG_STORE |
| 0x40 | PROG_LOAD |
| 0x43 | GET_INFO / RETURN_CONTROLLER_TYPE |
| 0x44 | TOGGLE_FORCE_SYS_MEM |
| 0x4F | SESSION_CONTROL [S9]; also called "INIT" [S2] and "SCADA enable" [S6] |
| 0x61 | PLC_FEATURES_SUPP [S9] |

### Segment selectors (byte 43 or 51)

Sources: [S1], [S4], and decimal codes from [S9].

| Memory | Word | Byte | Bit |
|---|---|---|---|
| %R | 0x08 | — | — |
| %AI | 0x0A | — | — |
| %AQ | 0x0C | — | — |
| %I | — | 0x10 | 0x46 |
| %Q | — | 0x12 | 0x48 |
| %T | — | 0x14 | 0x4A |
| %M | — | 0x16 | 0x4C |
| %SA | — | 0x18 | 0x4E |
| %SB | — | 0x1A | 0x50 |
| %SC | — | 0x1C | 0x52 |
| %S | — | 0x1E | 0x54 |
| %G | — | 0x38 | 0x56 |

[S2] adds 0x01 as a pseudo-selector for INIT, used with service 0x4F.

### Status codes

No dissector decodes the status bytes. GE documents the COMMREQ **major** codes [S16]:

| Code | Meaning |
|---|---|
| 0x01 | Success |
| 0x02 | Insufficient privilege (the minor code is the privilege level) |
| 0x04 | Protocol sequence error |
| 0x05 | Service request error (the minor code says which) |
| 0x06 | Illegal mailbox type |
| 0x07 | Server queue full; retry after at least 10 ms |
| 0x0B | Illegal service request |
| 0x11 | SRTP error |
| 0x8x | The same conditions, reported at the client side |

INFERRED: SRTP response byte 42 uses these major values and byte 43 the minor ones. Be careful: the implementations treat 0 at 42–43 as OK, whereas the COMMREQ table uses 0x01 for success. This needs a capture to settle.

---

## 2. Other open-source SRTP / SNPX clients

| Repo | Lang / License | Notable details |
|---|---|---|
| [S2] Palatis/Fanuc.RobotInterface | C#, **no LICENSE file (all rights reserved)** | Covered in detail below. |
| [S3] TheMadHatt3r/ge-ethernet-SRTP | Python, MIT | Sends 56 zero bytes and expects byte 0 of the reply to be 0x01. Read only; count is hardcoded to 1 word; reads with `recv(1024)`. Status at 42/43, data at 44–45. |
| [S4] hoilung/go-gesrtp | Go, MIT | Treats byte 0 = 0x03 as success. On 0xD4 it returns 44–49 when 42–43 are 0; on 0x94 it reads text-length bytes. Writes always use 0x80, with the payload at 56 and 52–53 = address − 1. No chunking. |
| [S5] kkuba91/uGESRTP | C#, MIT | Same 56-byte template. %R as WORD/DWORD/FLOAT; %I/%Q/%M as bit or byte. Addresses are uint16. |
| [S6] praetorian-inc/nerva `gesrtp` | Go, Apache-2.0 | Scanner. Exact bytes are listed below. |
| [S7] TakkoTheBoss/gesrtp-fuzzer | Python, MIT | Byte 0 is 0x01 for the init reply, 0x03 for success and 0x05 for errors. Example error reply: `05 00 32 f3 00 00 00 00 04 …`. |
| [S8] BiasedControls/snpx-client | Python, **no license stated** | FANUC-specific; default port **60008**. Details below. |
| Campajroska/jGESRTP | Java, no license | %R/%AI/%AQ only. |
| libOpenSRTP (sourceforge.net/projects/libopensrtp) | C, LGPLv2, alpha, 2013 | Not inspected. |

**Session-control packet (service 0x4F), byte for byte from [S6]** (the same bytes appear in [S2]'s second connect step and [S8]'s `INIT_MSG`):
```
08 00 01 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00
00 00 00 00 00 00 01 C0 00 00 00 00 10 0E 00 00 01 01 4F 01 00 00 00 00
00 00 00 00 00 00 00 00
```
The reply has byte 0 = 0x03.

**Return-controller-type packet (0x43) [S6]:** the same template with byte 0 = 0x02, seq = 2 and byte 43 = 0x00. The reply payload (after byte 56) has:
- +8: service code echoed
- +9: device indicator
- +12..19: PLC name (8 bytes)

**Connect sequence:**
- FANUC clients [S2, S8]: 56 zero bytes (reply byte 0 = 0x01), then the 0x4F packet.
- Plain GE clients [S3, S4]: only the zero init.

**[S2] Palatis C# (tested on an R-30iB):**
- I/O selectors:
  - DI → BIT_Q 0x48; DO → BIT_I 0x46
  - GI → WORD_AQ 0x0C; GO → WORD_AI 0x0A
- Bit reads are aligned to byte boundaries; bits come back LSB-first in each byte.
- Bit writes use the exact start index, but the payload is packed from the byte boundary.
- Index offsets added to those selectors:
  - RDIO 5000, UOP 6000, SOP 7000, WIO 8000, WSI 8400
  - PMC_K 10000, PMC_R 11000
  - AIO 1000, PMC_D 10000
- `WriteCommand(str)` sends ASCII to **%G, BYTE_G 0x38, index 0, count = byte length**. It is used for `SETASG <addr> <size> <var> <mult>` and `CLRALM`.
- Assignments start at %R1 and are allocated sequentially. Templates and sizes:

  | Template | %R words | Bytes |
  |---|---|---|
  | `R[n] 0` | 2 | 4 (float) |
  | `PR[n] 0.0` | 50 | 100 |
  | `SR[n] 1` | 40 | 80 ASCII |
  | `POS[Gg:uf] 0.0` | 50 | 100 |
  | `PRG[n]` / `PRG[Mn]` / `PRG[Kn]` / `PRG[MKn]` ` 1` | 18 | 36 |
  | `ALM[n]` or `ALM[En]` ` 1` | 100 | 200 |
  | sysvar int | 2 | 4 |
  | sysvar string | 40 | 80 |
  | sysvar position | 50 | 100 |

- Joint data is written at word offset +26.
- Alarm reset uses HTTP `http://host/KCL/reset`, not SRTP.

**[S8] BiasedControls quirks:**
- Puts the byte count into the seq fields (bytes 2–3 and 30). INFERRED: the robot does not check sequence numbers.
- Command packet: 0x80, 42–43 = 00 00, 48/49 = 01/01, 50 = 0x07, 51 = 0x38, 54–55 = length, byte 9 = 0x02, byte 17 = 0x00.
- Labels 0x48 as "I" and 0x46 as "Q". That is the robot's point of view, and both are bit selectors.

---

## 3. FANUC SNPX facts

The main source is FANUC's own **B-82604EN/01 "CIMPLICITY HMI for Robots" Operator's Manual** [S10], which covers R-J3, R-J3iB and R-30iA. It is quoted only briefly here.

### Option and port

- R-30iA and later need option **R553 "HMI Device (SNPX)"** when FRA params are used [S10, S12].
- R651 FRL needs no option [S12].
- CIMPLICITY treats the robot as a "GE Fanuc Series 90-30" device [S10].
- **Ports:**
  - Real HMI drivers use **18245** (C-more [S14], Kepware [S15]).
  - UnderAutomation, [S8] and realvirtual use **60008**, the "Robot IF server" [S12, S18]. ROBOGUIDE gives robot n port 60008 + (n − 1).

### Fixed I/O mapping [S10 §6.1, confirmed by S2, S14]

Robot inputs map to PLC outputs, and robot outputs to PLC inputs.

| Robot signal | PLC address |
|---|---|
| DI[x] | %Qx |
| DO[x] | %Ix |
| RI / RO | %Q / %I (5000+x) |
| UI / UO | %Q / %I (6000+x) |
| SI / SO | %Q / %I (7000+x) |
| WI / WO | %Q / %I (8000+x) |
| WSI / WSO | %Q / %I (8400+x) |
| GI[x] / GO[x] | %AQx / %AIx |
| AI / AO | %AQ / %AI (1000+x) |
| PMC keep relay Ka.b (DO[10001–10144]) | %I((a·8)+b+10001) |
| PMC internal relay Ra.b (DO[11001–23000]) | %M(x − 11000) = %M((a·8)+b+1) |
| PMC data table (GO[10001–12000]) | %AI(x − 6000) = %AI(a+4001) |

- %R is defined by `$SNPX_ASG`; %G accepts command strings.
- Writing to DI or AI causes a brief glitch and should be avoided.

### `$SNPX_ASG[1..80]` [S10]

There are **80 slots**, each with these fields:

- `$ADDRESS`: start %R, 1–16384
- `$SIZE`: number of %R words, 1–16384
- `$VAR_NAME`: STRING[37] [S11d]
- `$MULTIPLY`: REAL, 0.0001–10000, or 0

**Default slot 1:** `1 / 10000 / R[1]@1.1 / 1`. In the field it has also been seen with size 299 [S11b].

**`$MULTIPLY` semantics:**

| Value | Effect |
|---|---|
| 0 | 32-bit IEEE REAL (2 %R) |
| Non-zero | 32-bit signed int = value × mult, rounded (2 %R) |
| 1 on I/O | 1 %R per signal (0/1) |
| 0 on I/O | 16 signals per %R, lowest index in the LSB (DI[16] alone reads 32768) |
| On strings, alarms, program status | "Data format of character string"; set 1 |

**`@a.b` suffix:** take b words starting at word a of each element's structure. `R[1]@1.1` gives 16-bit integers; `PR[3]@27.12` gives J1–J6 only. No blanks are allowed inside `$VAR_NAME`.

**Accepted `$VAR_NAME` forms:**
- `R[n]`
- `PR[n]`, `PR[Gg:n]`
- `POS[uf]`, `POS[Gg:uf]`
- `ALM[n]`, `ALM[En]`, `ALM[Pn]`
- `PRG[n]`
- `$sysvar`, `$[KarelProg]var`
- `XX[n]`, `XX[Sn]` (SIM status) and `XX[Cn]` (comment), where XX is DI, DO, RI, RO, UI, UO, SI, SO, WI, WO, WSI, WSO, GI, GO, AI or AO
- `R[Cn]` and `PR[Cn]` (comments)

`SR[n]` is used in the field (40 words = 80 chars) [S2, S11b]. `F[n]` (flags) is offered by UnderAutomation, but its `$VAR_NAME` syntax is not documented (unknown).

**Sizes per element:**

| Element | %R words | Layout |
|---|---|---|
| R | 2 | |
| PR / POS / sysvar POSITION | 50 | See below. |
| ALM | 100 | 1 id, 2 number, 3 cause id, 4 cause number, 5 severity, 6–11 Y/M/D/h/m/s, 12–51 message (80 chars), 52–91 cause message, 92–100 severity text (18 chars) |
| PRG | 18 | 1–8 program name (16 chars), 9 line, 10 state (0 end, 1 pause, 2 running), 11–18 caller |
| Comment | 40 | |
| sysvar INTEGER / SHORT / BYTE / REAL / BOOLEAN | 2 | |
| sysvar STRING | 40 | |

**The 50-word position block** (1-based %R; [S2] byte offsets agree):

| %R | Content |
|---|---|
| 1–18 | X Y Z W P R E1 E2 E3, each a REAL or int32 |
| 19 | FLIP |
| 20 | LEFT |
| 21 | UP |
| 22 | FRONT |
| 23–25 | TURN4–6 |
| 26 | VALIDC |
| 27–44 | J1–J9 |
| 45 | VALIDJ |
| 46 | UF (0–15; 15 = "currently selected") |
| 47 | UT |
| 48–50 | Reserved |

- Writing to VALIDC or VALIDJ switches the PR's representation.
- POS[], ALM[] and PRG[] are read-only; writes are silently ignored.
- POS[0] is in world coordinates and POS[15] in the current user frame. A POS assignment cannot cover more than one element.

**Encoding:**
- %R words are little-endian.
- 32-bit values are stored low word first (INFERRED from [S2], which uses `BitConverter` on a little-endian host).
- Strings are ASCII with 2 chars per %R, first char in the low byte [S2, S10]. Unused bytes are 0. Katakana is Shift-JIS.

**Other rules:**
- An unassigned %R reads 0.
- When assignments overlap, the lower-numbered slot wins.
- I/O access is faster than %R. %R slows down while programs are running, and converting a PR between Cartesian and joint form costs milliseconds.

### %G commands [S10 §6.10, S11a]

Write ASCII to **any** %G address:

- `CLRASG`
- `SETASG addr size var [mult]` (multiplier defaults to 1)
- `SETVAR $var value` (for INTEGER, SHORT, BYTE, REAL, BOOLEAN or STRING; quote strings that contain spaces)
- `CLRALM` (clears the alarm history)

Several commands can go in one write, separated by CR or LF. A trailing terminator makes it slightly faster.

### `$SNPX_PARAM` fields

- `$VERSION`: version 1 if absent. Version ≥ 2 adds comments, SIM status, multiple commands per write, CLRALM, I/O mapped to %R, KAREL variables and multi-connection.
- `$NUM_CIMP`: number of simultaneous TCP connections; **default 0** means multi-connection is off [S10, S14].
  - When the limit is exceeded, the manual says the connection idle the longest is dropped [S10].
  - The field reports alarm PRIO-096 "SNPX Connection is full" [S11c].
  - Values that are too large can halt the controller because of memory.
- `$NUM_ASG`, `$TIMEOUT` (10000 in the field), `$SNP_ID`, `$NUM_MODBUS`, `$MODBUS_ADR` [S11c, S11e].

### Persistence

- With `$NUM_CIMP` = 0 or version 1, `CLRASG`/`SETASG` edit the system variable `$SNPX_ASG` itself. The changes persist and are shared.
- With version ≥ 2 and multiplexing on, `CLRASG` creates a **per-connection local table** that is deleted on disconnect. Its contents are not visible on the teach pendant [S10 §6.11.4].
- [S13] maps `$SNPX_ASG[80]`'s own fields into %R16300–16345 using slots 76–79:

  | Field | %R words | Multiplier |
  |---|---|---|
  | `$ADDRESS` | 2 | 1 |
  | `$SIZE` | 2 | 1 |
  | `$VAR_NAME` | 40 | **−1** |
  | `$MULTIPLY` | 2 | 0 |

  An HMI can therefore rewrite slot 80 through %R writes.

### UnderAutomation SNPX API surface [S12] (feature names only)

- **Registers:** NumericRegisters (float), NumericRegistersInt16, NumericRegistersInt32, PositionRegisters, StringRegisters (StringLength defaults to 80 and must be even and ≥ 2), Flags.
- **Digital I/O (13 types):** SDI/SDO, RDI/RDO, UI/UO, SI/SO, WI/WO, WSI, PMC_K, PMC_R.
- **Numeric I/O (5 types, ushort):** GI/GO, AI/AO, PMC_D.
- **System variables:** Integer, Real, Position and String, plus `SetVariable(name, value)` (auto-typed) and `$[prog]var`.
- **Position:** CurrentPosition (world or user frame, with a group number; frame 15 = current).
- **Status and alarms:** CurrentTaskStatus, ActiveAlarm, AlarmHistory, ClearAlarms(), SimulationStatus.
- **Comments:** 21 comment types.
- **Assignments:** GetAssignments, ClearAssignments, `CreateBatchAssignment(start, count)`.
- **Performance claim:** about 2 ms per command, and 80 PRs read in one batch take the same time.

---

## 4. DFRWS 2017 paper (Denton et al.)

It is available only as PDF:
- https://dfrws.org/wp-content/uploads/2019/06/paper_leveraging_the_srtp_protocol_for_over-the-network_memory_acquisition_of_a_ge_fanuc_series_90-30.pdf
- https://www.researchgate.net/publication/318925679

ScienceDirect returned 403. **Skipped** as instructed. The search snippets confirm that the sequence number appears at offsets 2 and 30, and that 0x43 means "return controller type and ID".

---

## CONFLICTS between sources

1. **Byte S/SA/SB/SC selectors.**
   - [S1], [S4] and [S9] give SA 0x18, SB 0x1A, SC 0x1C, S 0x1E.
   - [S2] has them shifted (SA 0x1A, SB 0x1C, SC 0x1E) and no BYTE_S.
   - Trust [S1], [S4] and [S9].
2. **Bit I/Q labels.** [S8] calls 0x48 "I" and 0x46 "Q", but [S1] and [S4] have I = 0x46 and Q = 0x48. [S8] names them from the robot's side and also mislabels them as "Byte".
3. **Width of the sequence field.** [S1] treats bytes 2–3 as u16; [S2] and [S3] treat it as a single byte at 2 with byte 3 reserved. [S8] writes a byte count into it.
4. **Response status.** [S3], [S4] and [S7] read status at bytes 42–43, with 0 = OK. [S1] marks those bytes unknown. [S2] only checks msg_type 0xD1. GE COMMREQ uses 0x01 for success [S16].
5. **Error indication.** [S7] sees 0x05 at byte 0; [S2] relies on msg_type 0xD1; [S4] and [S6] check byte 0 = 0x03.
6. **Bytes 9 and 17 in extended requests.** [S2] sets both to 0x02; [S8] sets byte 9 = 0x02 and byte 17 = 0x00.
7. **Write form.** [S2] inlines writes of 6 bytes or fewer in a 0xC0 packet; [S4] always uses 0x80.
8. **Default port.** 18245 [S10, S14, S15, S11f] versus 60008 [S8, S12, S18].
9. **PMC mapping.**
   - [S10]: PMC_R maps to %M and PMC_D to %AI(4001+).
   - [S2]: PMC_R uses offset 11000 on the DO (%I) path and PMC_D uses offset 10000 on the GO (%AI) path.
10. **Multiplier for strings.** [S10] and [S2] use 1; [S13] uses −1 for a STRING sysvar. The meaning of −1 is unknown.
11. **String density.** [S10] and [S2] give 2 chars per %R. [S11b] reports "16 HMI registers for 64 chars", probably because that HMI uses 32-bit registers (INFERRED).
12. **What happens over the connection limit.** [S10] says the oldest idle connection is dropped; [S11c] reports PRIO-096 "connection is full".
13. **Max request size.** Kepware allows 32–2048 bytes per request, default 2048 [S15]. UnderAutomation claims 80 PRs (8000 bytes) in "a single command". Whether FANUC needs multi-packet transfers (bytes 40–41 and 48–49) is unverified.
14. **CR/LF hex values.** [S10] writes "CR(0x0A) or LF(0x0D)", which swaps the usual values.
15. **Serial vs. Ethernet.** [S11f] describes a "24-byte attach telegram". That is serial SNP-X; SRTP uses 56 bytes.

---

## Sources

- **S1** https://github.com/Palatis/packet-ge-srtp (raw `packet-ge-srtp.lua`)
- **S2** https://github.com/Palatis/Fanuc.RobotInterface (`SRTP/*.cs`, `RobotIF.cs`, `ExRobotIF.*.cs`, `IPosition.cs`, `RobotAlarm.cs`, `RobotTaskStatus.cs`, `IRobotIFExtension.cs`)
- **S3** https://github.com/TheMadHatt3r/ge-ethernet-SRTP (`lib/GE_SRTP.py`, `GE_SRTP_Messages.py`)
- **S4** https://github.com/hoilung/go-gesrtp (`gogesrtp.go`, `datatype.go`)
- **S5** https://github.com/kkuba91/uGESRTP
- **S6** https://github.com/praetorian-inc/nerva/blob/main/pkg/plugins/services/gesrtp/gesrtp.go
- **S7** https://github.com/TakkoTheBoss/gesrtp-fuzzer
- **S8** https://github.com/BiasedControls/snpx-client
- **S9** https://cscapehelp.hornerautomation.com/Content/HW_Config/ETN-SRTP.htm
- **S10** FANUC B-82604EN/01, https://icdn.tradew.com/file/201606/1569362/pdf/7066339.pdf (text extract in this folder: `cimplicity_b82604.txt`)
- **S11** robot-forum.com threads:
  - a: /thread/40240
  - b: /thread/15582 and /thread/21949
  - c: /thread/44707 and /thread/13484
  - d: /thread/15546
  - e: /thread/42087
  - f: /thread/19084
- **S12** https://underautomation.com/fanuc/documentation/snpx (and `-registers`, `-io`, `-variables`, `-position`, `-alarms-tasks`, `-batch`)
- **S13** https://github.com/MisoRobotics/fanuc-hmi-jointstreaming (BSD-3)
- **S14** https://cdn.automationdirect.com/static/helpfiles/c-more/cm5/Content/121.htm and `.../370.htm`
- **S15** Kepware GE Ethernet Driver Help v5, https://www.opcturkey.com/uploads/v5-ge-ethernet-manual.pdf (text extract: `kepware_ge_ethernet.txt`)
- **S16** GE RX3i manual GFK-2224Q, p. 234: https://www.manualslib.com/manual/1258748/Ge-Rx3i.html?page=234
- **S17** https://github.com/automayt/ICS-pcap/blob/master/GE-SRTP/Notes.txt
- **S18** https://doc.realvirtual.io/components-and-scripts/interfaces/fanuc-pro
