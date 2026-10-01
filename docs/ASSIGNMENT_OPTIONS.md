# How assignments get created (decision memo for TRACKER Q8)

Reading anything other than I/O and 16-bit register values needs `$SNPX_ASG` entries: real values of
`R[]`, `PR[]`, `$MNUFRAME`/`$MNUTOOL`, the current position (`POS[]`), string registers, comments. This
memo lists the ways to create them, the constraints, and a recommendation. **The owner decides at GATE 1**;
facts marked *to verify* are checked in Phase 2.

## Constraints

1. **`CLRASG` scope.** Public V9.x `system.va` files show `$SNPX_PARAM.$NUM_CIMP = 0` (factory value).
   Per the FANUC CIMPLICITY manual §6.11.4, with `$NUM_CIMP = 0` `CLRASG` erases the shared
   `$SNPX_ASG` table; only with `$VERSION >= 2` **and** `$NUM_CIMP > 0` does it create a private,
   per-connection table. The survey's `asg_map` step reports this for the test robot (*to verify*).
2. **%R space.** `$ADDRESS` and `$SIZE` are documented as 1..16384. The factory slot 1 maps `R[1]@1.1`
   over %R1..%R10000, leaving %R10001..%R16384 = **6384 words** (*to verify*: whether %R above 16384 is
   served at all; the survey's optional `--error-probe` reads %R16385).
3. **Sizes per element** (words): R 2, PR/POS/POSITION sysvar 50, SR 40, comment 40, scalar sysvar 2.
   A slice `@first.count` maps only part of each element, e.g. `PR[1]@1.26` = X..R, E1..E3,
   configuration, turns and VALIDC (enough to read and write a Cartesian PR, 26 words);
   `PR[1]@1.12` = X..R only; `$MNUFRAME[1,1]@1.12` = X..R of a frame.
4. Lower slot numbers win where assignments overlap; `plan-asg` never plans overlapping entries.

Example: 200 real R + 100 full PRs + 9 frames + 9 tools + 2 POS = 400 + 5000 + 450 + 450 + 100 =
6400 words: does not fit in 6384. With `PR[..]@1.26` (2600) and frames/tools `@1.12` (216): 3316 words: fits.

## Options

| | A. Owner enters slots on the pendant | B. Enable multi-connection, client uses session tables | C. Library writes persistent slots |
|---|---|---|---|
| Controller change | Adds entries in free `$SNPX_ASG` slots (persistent, visible on the pendant) | Sets `$SNPX_PARAM.$NUM_CIMP` > 0 (may need a restart; *to verify*) | Adds entries in free slots via `SETASG` without `CLRASG`; removes them via `SETVAR` |
| Who changes it | The owner, by hand, once | The owner once; then the client per connection | The client, every time |
| Client writes to the controller | **None** (Phase 3 stays strictly read-only) | `CLRASG` + `SETASG` on every connection (implemented: `AssignmentManager.apply_session`) | `SETASG`/`SETVAR` on `$SNPX_ASG` (**not implemented**) |
| Effect on other SNPX clients (PLC, HMI) | None if the %R block is unused by them | None: their shared table is untouched | Shares the global table; leftovers if a client crashes |
| Risk | Typing errors (mitigated: `plan-asg` prints the exact table; the survey re-reads `system.va`) | Memory use per connection (manual warns against large values); untested on V9.30 | Highest: automated persistent system-variable writes |

**Recommendation:** **A for Phase 3** (validation needs no client writes at all), then decide between A
(fixed, documented layout; simplest for production) and B (if several independent clients need different
maps). Keep C unimplemented unless A and B are both ruled out.

## Option A step by step

1. Phase 2 survey gives the robot's `system.va` (in the run folder under `files/`).
2. Plan the layout against the existing table, e.g.:

   ```bash
   fanuc-snpx plan-asg --existing evidence/<run>/files/system.va \
       --item "R[1] 200" --item "PR[1]@1.26 100" \
       --item "$MNUFRAME[1,1]@1.12 9 POSITION" --item "$MNUTOOL[1,1]@1.12 9 POSITION" \
       --item "POS[G1:0]" --item "POS[G1:15]" \
       --out-map evidence/<run>/asg-plan.json
   ```

   It prints the slot table (free slots, non-overlapping %R ranges) and the size used. Defaults:
   `$MULTIPLY` 0 (IEEE reals) for numbers and positions, 1 for strings and comments.
3. The owner enters each row on the pendant: MENU > SYSTEM > Variables > `$SNPX_ASG` > slot >
   `$ADDRESS`, `$SIZE`, `$VAR_NAME`, `$MULTIPLY` (exact menu path *to verify* on V9.30).
4. Re-run the survey (or just `ftp-get system.va` + `parse`) and check `asg_map.md` matches the plan.
5. Use the plan JSON as the client's table: `SnpxClient(host, assignments=AssignmentTable.load(...))` or
   `fanuc-snpx read-reg ... --map asg-plan.json`, then `fanuc-snpx compare` for Phase 3.

Note: `plan-asg` writes system-variable entries with their type into the JSON map; entries the package
cannot decode (e.g. `F[]`) are listed, never dropped.
