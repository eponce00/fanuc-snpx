# Tracker

Single source of truth for status, next steps and open items. Update it in the same PR as the work.
Legend: ✅ done · ⏳ in progress · ⬜ to do · ❓ needs the owner · 🚫 do not do

Last updated: 2026-10-01 (end of PC-only work; robot not contacted yet).

## 1. Status

| Phase | State | Notes |
|---|---|---|
| 0 Research, protocol hypothesis | ✅ | docs/PROTOCOL.md, docs/research/PUBLIC_SOURCES.md |
| 1 PC-only core | ✅ | PR #1 merged. 180+ tests, ruff, mypy --strict, CI on Linux/Windows/macOS × 3.10/3.13 |
| 2 prep (PC) | ✅ | `fanuc-snpx survey`, GE status hints, handoff docs |
| Oracle parsers (PC) | ✅ | `parsers.py`: numreg, posreg, sysframe, system.va (`$SNPX_*`), curpos/summary, errall, iostate; `fanuc-snpx parse`. Learned from public V7.70-V9.40 files; re-check against the robot's files |
| 2 Robot read-only: handshake, info, small reads, ASG map | ⬜ ❓ | waiting for the owner to say "connected" → GATE 1 |
| 3 Robot read-only: every data type vs FTP oracle | ⬜ | → GATE 2 |
| 4 Writes on scratch targets | ⬜ ❓ | needs the owner's policy file + named targets → GATE 3 |
| 5 Release | ⬜ | |

Robot traffic so far: one 56-byte init exchange on 2026-09-30 (before this repo). Nothing read or written.

## 2. Phase 2 checklist (read-only) — do these in order, stop at GATE 1

Pre-flight:
- ⬜ The owner has said you are connected, and who is at the pendant. Write both into the report.
- ⬜ `ping 10.50.160.51` works; `git pull`; all checks green.
- ⬜ Create a run folder name with the date, e.g. `evidence/2026-10-xx-phase2/` (git-ignored).

Steps:
1. ⬜ `fanuc-snpx survey 10.50.160.51 --out evidence/<run>` (default: no error probe, max 2 KiB reads).
   Read `survey.md`. Check the pendant for alarms afterwards (expect none).
2. ⬜ If the handshake fails: try `--no-session-control` path results in the survey; compare the reply
   bytes with docs/PROTOCOL.md §2-4; fix `srtp.py` with a test reproducing the robot's bytes. Robot wins.
3. ⬜ Record in docs/VALIDATION_LOG.md: init reply, `0x4F` reply, short status bytes 42-55 with and
   without `0x4F` (byte 51 privilege?), the raw `0x43`/`0x03`/`0x38` replies, largest read size, latency.
4. ⬜ Check the survey's `asg_map` step and `asg_map.md` (parsed from `files/system.va`, which holds
   `$SNPX_ASG`/`$SNPX_PARAM`). If `system.va` is missing, look in `ftp-listing.txt` (also
   `fanuc-snpx ftp-get 10.50.160.51 --list`) and fetch it with `fanuc-snpx ftp-get`.
5. ⬜ Fill docs/ASG_MAP.md: every used slot and the `$SNPX_PARAM` fields. Record `$NUM_CIMP`
   (public V9.x files: 0) and the survey's `multiplexed` verdict. If `multiplexed` is False, `CLRASG`
   would erase the shared table: session-scoped assignments are NOT available (see Q8).
6. ⬜ Read the survey's `oracle_R1_10` step: %R1..%R10 (factory `R[1]@1.1` 16-bit view) next to
   `numreg.va`. Compare DO/DI/GO/GI reads with `files/iostate.dg` (`fanuc-snpx parse files/iostate.dg`).
7. ⬜ Optional, only with the owner's OK (may post PRIO-090 on the pendant): `--error-probe` to learn the
   real error status bytes; `--large-sizes` to find the size limit above 2 KiB.
8. ⬜ Run `fanuc-snpx parse` on every downloaded file. Where the robot's format differs from the
   fixtures in `tests/fixtures/`, fix `parsers.py` and add a fixture in the robot's layout **with all
   values replaced** (no cell data in the repo). Write the `strreg.va` parser from the real file.
9. ⬜ GATE 1 report (format in CLAUDE.md). Ask Q3 and Q8 (how assignments get created).

## 3. Phase 3 checklist (read-only, after GATE 1)

Needs assignments that map reals, PRs, frames, current position. Either the owner sets them on the
pendant (no code writes) or, after GATE 1 approval, `AssignmentManager.apply_session(...,
multiplex_confirmed=True)` with the policy's `snpx_assignments` block (that sends `CLRASG`/`SETASG`).

For each, read twice via SRTP and FTP while values are static; float32 compare; log in VALIDATION_LOG:
- ⬜ R[] real view (multiply 0) and int view vs `numreg.va`
- ⬜ PR[] Cartesian + config + turns + UF/UT, joint-stored PR, untaught PR (VALIDC/VALIDJ: whole word vs
  high byte, see PROTOCOL §7) vs `posreg.va`
- ⬜ `$MNUFRAME[g,i]` / `$MNUTOOL[g,i]` vs `sysframe.va`; also confirm a one-index spelling is rejected by
  the client (never send it)
- ⬜ `POS[G1:0]` / `POS[G1:15]` vs `curpos.dg` (robot stationary): Cartesian and joints from one read
- ⬜ SR[] vs `strreg.va`; comments `R[Cn]`, `PR[Cn]` vs the comments in `numreg.va`/`posreg.va`
- ⬜ DI/DO/RI/RO/UI/UO/SI/SO/GI/GO/AI/AO reads vs pendant or `iostate.dg`; packed I/O assignments (multiply 0)
- ⬜ 32-bit word order (low word first?) with a known integer such as 70000 in an int32 view
- ⬜ Performance: 30-register block, 300-PR block (record ms); choose `max_read_bytes` default and
  propose a rate-limit default to the owner (5 req/s makes 300 PRs take seconds)
- ⬜ Max simultaneous SRTP clients (open 2, 3, ... read-only sessions; stop at the first refusal; watch for
  PRIO-096 "SNPX Connection is full"); then close all
- ⬜ Session-scoped vs persistent assignments: after a session ends, are its entries gone? (read the
  controller file again)
- ⬜ GATE 2 report with the comparison table

## 4. Phase 4 checklist (writes) — only after GATE 2 and with the owner's inputs

- ❓ Owner provides `robot-policy.local.json` (schema in docs/POLICY.md) bound to `controller.host`, with
  scratch targets: one R, one PR, one frame or tool, optionally one comment and one DO nobody drives.
- ⬜ For each target: read original → write → read back via SRTP → read via FTP → restore original →
  verify restore via both paths. Log everything.
- ⬜ Confirm UF/UT of the scratch PR is unchanged after a Cartesian write.
- ⬜ GATE 3 report.

## 5. Open questions for the owner

| # | Question | Status |
|---|---|---|
| Q1 | When are we connected to 10.50.160.51, and who is at the pendant? | ❓ open |
| Q2 | Port 60008: public SNPX clients send the same SRTP frames there. Keep it off-limits as the brief says? | ❓ open (default: yes, off-limits) |
| Q3 | %R block for this client's own assignments (e.g. %R5001-%R6000) | ❓ open (needed at GATE 1) |
| Q4 | Scratch write targets for Phase 4 | ❓ open |
| Q5 | ROBOGUIDE available? A virtual controller with the HMI option would allow earlier testing | ❓ open |
| Q6 | Rate-limit default (5 req/s) vs block-read speed targets | ❓ decide after Phase 3 measurements |
| Q7 | May the survey run the optional error probe / large reads (may post PRIO-090)? | ❓ open |
| Q8 | Public V9.x files show `$SNPX_PARAM.$NUM_CIMP = 0` (multi-connection off), so `CLRASG` would wipe the shared `$SNPX_ASG`. How should mappings for reals/PRs/frames/current pose be created? (a) owner adds them on the pendant in free slots (no code writes); (b) owner enables `$NUM_CIMP` > 0 (controller setting, may need a restart) so the client can use session-scoped tables; (c) a persistent mode that adds/removes only its own slots via `SETASG`/`SETVAR` (not implemented) | ❓ open, decide at GATE 1 |

## 6. Hypotheses to verify on the robot (from docs/PROTOCOL.md)

- ⬜ `0x4F` session control needed on 18245? Byte 51 = privilege (2 → 4)?
- ⬜ Request bytes 9/17/30 (Kepware values `01`/`02`, `06`/`09`) accepted; byte 30 echoed.
- ⬜ Replies: `D4` inline ≤ 6 bytes, `94` for larger; status 42-43 on errors (captures show `00 00` + `D1`).
- ⬜ Largest single read; whether the controller ever sends multi-packet replies.
- ⬜ Services `00`, `03`, `38`, `43` reply layouts (currently returned raw).
- ⬜ `$SNPX_ASG` contents; `$SNPX_PARAM.$NUM_CIMP` (expected 0) and therefore `CLRASG` scope.
- ⬜ 32-bit word order; VALIDC/VALIDJ encoding; `@1.1` = low word of the int32 view.
- ⬜ I/O offsets for SI/SO/WI/WO/WSI/WSO (0- or 1-based index), GI/GO signed or unsigned.
- ⬜ `F[]` flags assignment syntax (undocumented; currently `NotImplementedError`).
- ⬜ Whether GE SNP error tables (status.py) apply to FANUC replies.

## 7. Backlog

| Item | State | Notes |
|---|---|---|
| FTP file parsers | ✅ / ⬜ | ✅ numreg, posreg, sysframe, system.va `$SNPX_*`, curpos/summary, errall, iostate (from public V7.70-V9.40 files; fixtures are invented values in the same layout). ⬜ `strreg.va` (no public file with a stored string). ⬜ confirm all against the robot's files |
| Build the map from the controller file | ✅ | `parse_snpx_config(system_va).to_table(sysvar_types)`; system-variable entries need their type, unsupported entries are reported, never dropped |
| Oracle comparison tool: SRTP vs FTP report → VALIDATION_LOG rows | ⬜ | Phase 3 |
| Persistent assignment mode (SETASG without CLRASG + cleanup via SETVAR) | 🚫 until decided | currently not implemented on purpose |
| Alarm history (`ALM[]`, 100 words) and program status (`PRG[]`, 18 words) decoders | ⬜ | layouts in CIMPLICITY §6.5/6.6; read-only |
| Flags `F[]` | ⬜ | syntax unknown |
| Comments for flags and string registers | ⬜ | no documented SNPX path; FTP read-only fallback |
| Controller identity (model, software, serial) | ⬜ | via string sysvars or `version.dg`/`summary.dg` |
| Release: version, changelog, tag, wheel | ⬜ | Phase 5; wheel build checked OK 2026-10-01 |
| Branch protection on `main` | 🚫 | GitHub plan does not allow it for a private repo; follow PR-only by hand |
| Local note: CPython 3.10.0 + Hypothesis 6.168 fails inside Hypothesis | — | not our code; CI uses latest 3.10 and passes |

## 8. Owner's application needs (generic operations the API must cover)

The owner will write their own adapter against this API. Operations they need, and where they are:

| Need | API | Status |
|---|---|---|
| R read / write / block read | `numeric_registers.read/write/read_block` | fake-tested |
| PR read / write / block read | `position_registers.*` | fake-tested |
| User frame / tool read + write with read-back | `frames.read_user_frame/write_user_frame/...` (`$MNUFRAME[g,i]`, `$MNUTOOL[g,i]`) | fake-tested |
| Current WORLD pose with joints (one read) | `current_position.world()` (`POS[G1:0]`) | fake-tested |
| GI/GO read, GI write | `io.read("GI"/"GO")`, `io.write("GI", ...)` (policy) | fake-tested; note the manual warns about writing robot inputs |
| Comments on R / PR | `comments.read/write("R"/"PR", i)` | fake-tested |
| Comments on F / SR | — | no SNPX path known; FTP read-only fallback planned |
