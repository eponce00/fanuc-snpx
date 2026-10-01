# Owning our robot data link: replacing the UnderAutomation SNPX SDK

Status: investigation and plan, 2026-09-30. No client code has been written yet.
Decision (Ernesto, 2026-09-30): do not pay for or depend on a third-party
license; develop our own client.

## 1. Why this exists

`amperesand-fanuc[snpx]` depends on **UnderAutomation.Fanuc**, a commercial
.NET library (called from Python through `pythonnet`). It is not FANUC
software. It runs in a 30-day trial mode and the code never registers a
license key.

On 2026-09-30 every Python install on the commissioning PC reported:

- Edge Trace venv: `Your trial version has expired since 9/18/2026 12:58:13 PM`
- System Python 3.12: expired `9/19/2026 6:19:13 PM`

The same venv still connected at about 09:20 the same morning (UFRAME[2] and
UTOOL[6] were applied). Why it worked then is unexplained; do not rely on it.

Effect: everything that uses the adapter below stops working until a license
is bought or the dependency is replaced. The FTP path (`FtpController`) does
not use the SDK and keeps working.

## 2. What we use it for

All use goes through one adapter class, `UnderAutomationSnpxClient` in
`src/fanuc_devamp/snpx.py`, plus `SnpxCalibrationValueClient` and
`SnpxCameraValueClient` in `station_deploy.py`. Callers depend on the
`SnpxPathClient` protocol and on duck-typed `read_*`/`write_*` methods, so a
replacement is a drop-in class.

Operations used (the complete list a replacement must cover):

| Operation | Adapter method | Used by |
| --- | --- | --- |
| Numeric register R[i] read/write, block read | `read_numeric`, `write_numeric`, `read_numerics` | PR stream handshake, readiness audit |
| Position register PR[i] read/write, block read | `read_position`, `write_position`, `read_positions` | path publication (PR[101]..PR[350]), station poses |
| UFRAME/UTOOL read/write with read-back | `read_user_frame`, `write_user_frame`, `read_user_tool`, `write_user_tool` (`$MNUFRAME[g,i]`, `$MNUTOOL[g,i]`) | guarded frame apply, camera/UTOOL 3/4/6 audit, rollback |
| Current WORLD pose and joints | `read_current_world_position[_with_joints]` | hand-eye capture, `tip_in_frame.py`, frame touch checks |
| Group input/output | `read_group_input`, `write_group_input`, `read_group_output` | idle gate (GI3/GO3/GO1), station handshake audit |
| Comments on R/PR/F/SR | `read_comment`, `write_comment`, `read_position_comment`, `write_position_comment` | station identity and PR buffer naming |

Not used: motion, program control, alarms, string registers (today).

## 3. What the robot offers (F546175, measured 2026-09-30)

Two network data servers are open on 10.50.160.51:

| Port | Protocol | Option | Public spec? |
| --- | --- | --- | --- |
| 60008 | FANUC "Robot Interface" / PC Interface (what `FRRJIF.DLL` talks; vendor docs say this is what the UnderAutomation SNPX client uses) | PC Interface | No. Only FANUC's COM DLL and the vendor library implement it. |
| 18245 | GE **SRTP** / SNP-X ("HMI Device (SNPX)", R553) | HMI Device | Yes. Public research papers, Wireshark dissector, and open-source clients. |

Evidence that 18245 is alive and speaking SRTP: sending the 56-byte all-zero
SRTP init packet returned a 56-byte reply with packet type `0x01` (InitRx):
`0100000000000000 0100000000000000...`. This was a read-only handshake; no data
was read or written.

I did not confirm which of the two ports the vendor SDK actually uses (it
refuses to connect before any traffic). The vendor documentation states port
60008.

### Prior art we can study (licensed for reuse)

- `valstad-shipworks/fanuc_ucl` (Apache-2.0, Rust with Python bindings). Its
  `hmi` module is a complete SRTP client with service-request codes, segment
  selectors, and "ASG" registration of named system variables. It targets
  exactly our option.
- `Booozie-Z/Fanuc_GESRTP_Driver` (MIT, pure Python). Reads and writes DI, DO,
  numeric registers, string registers, and group outputs.
- `EERichardji/RJi.FanucRobot.Interface` (README MIT; no source published). It
  documents a .NET re-implementation of the port-60008 protocol covering
  exactly our operation list, which shows it is feasible, but gives us no
  wire format.

## 4. Options

A. **SRTP client over port 18245 (recommended).** Public protocol, open
   reference code, option already present on the robot. Pure Python, no .NET
   dependency. Controller-side data is exposed through `$SNPX_ASG` assignments
   (map a controller variable or register to a `%R` address); named system
   variables (`$MNUFRAME[1,2]`, `$MNUTOOL[1,3]`) are supported through the same
   mechanism in `fanuc_ucl`.

B. **Reimplement port 60008.** Would match the old behaviour one-to-one but has
   no public spec. We would have to capture FANUC's own client traffic and
   infer the format. Do not decompile or patch the vendor DLL: that breaks its
   license terms and is unnecessary if A works.

C. **Stay on FTP plus TP programs.** Reads work today (see section 7). Live
   writes of frames/registers would need a TP program the operator runs, so
   this is only a fallback, not a replacement.

## 5. What has to be verified before building (unknowns)

1. **Current `$SNPX_ASG` configuration on F546175.** Which registers and
   system variables are already mapped, and at which `%R` addresses. The
   station contract (SNPX limited to R[120]..R[150] and PR[101]..PR[350])
   suggests an existing mapping, but it has not been read. Do not write
   `$SNPX_ASG` without an explicit, recorded decision: it is a controller
   system-variable change.
2. **Can ASG expose the things we need?** Position registers with UF/UT and
   configuration, `$MNUFRAME`/`$MNUTOOL`, and the current pose/joints. Confirm
   types and sizes on this controller and software version (V9.30146).
3. **Group I/O.** SRTP exposes `%I`/`%Q` segments; confirm GI/GO mapping.
4. **Comments.** Register and PR comments may have no SRTP equivalent. If not,
   read them from FTP (`numreg.va`, `posreg.va`) and drop comment writes or
   do them with a TP program.
5. **Concurrency and session limits** on the SRTP server, and whether
   controller-side assignments are per-connection (the old batch assignments
   were session-scoped).

Each item gets a short result note appended to this document.

## 6. Design

- New module `fanuc_devamp/srtp.py`: a minimal SRTP client (init, read/write
  memory, close), no robot semantics.
- New class `SrtpSnpxClient` implementing the same methods as
  `UnderAutomationSnpxClient`. Keep the vendor adapter available behind the
  existing optional extra so either can be selected; do not remove it until
  the replacement has passed hardware validation.
- **Read-only by default.** Every write path keeps today's gating (guarded
  apply, read-back tolerance, ownership rules from the CORE runtime contract).
  The new client adds no new write capability and invents no register, GI/GO
  or PR allocations.
- Float32 exactness: FANUC stores reals as float32; keep the existing
  read-back comparison at controller precision.
- Fake SRTP server for unit tests (scripted request/response), so the suite
  runs with no robot.

## 7. Validation (no vendor library needed)

The controller publishes its own state over FTP, which gives an independent
oracle for read checks:

| Data | FTP file |
| --- | --- |
| R[i] | `numreg.va` |
| PR[i] | `posreg.va` |
| UFRAME/UTOOL | `sysframe.va` |
| current pose and joints | `curpos.dg` |
| alarm history | `errall.ls` |

Acceptance for each operation: the SRTP value equals the FTP value. Writes are
validated only on a scratch target that the user names (for example one
register inside R[120]..R[150] or one PR inside PR[101]..PR[350]), with
read-back through both paths, and never as part of an automated test.

## 8. Plan

1. Survey (read-only): read `$SNPX_ASG` via FTP/SRTP, record the existing
   mapping here.
2. Port the SRTP framing from the public references (clean implementation,
   attribute Apache-2.0/MIT sources if any code is reused), with a fake server
   and tests.
3. Read paths first: numeric registers, PR, frames/tools, current pose, GI/GO.
   Compare to the FTP oracle.
4. Write paths: R, PR, then UFRAME/UTOOL with the existing read-back check,
   on user-approved scratch targets.
5. Switch the default in `station_deploy.py`, Edge Trace, and `tools/` to the
   new client; keep the vendor extra optional for one release.
6. Update `AGENTS.md`, `FANUC_FTP_TRANSFER.md` and this document with the
   validated result and the final `$SNPX_ASG` map.

## 9. Until the replacement exists

- Program upload, read-back, and all controller file reads continue to work
  (`FtpController`). The 2026-09-30 `P_TCP_BOOT` work used only this path.
- Blocked without a license: guarded UFRAME/UTOOL writes, the Edge Trace
  "WRITE UFRAME TO ROBOT" button, live PR path streaming, and live pose reads.
- Do not try to reset the trial. If a blocked operation is urgent, use the
  pendant or a TP program.
