# `$SNPX_ASG` map of the test robot

**Not read yet.** Filled in Phase 2 from the controller's own `system.va` (read-only FTP). The survey does
it automatically: `fanuc-snpx survey 10.50.160.51 --out evidence/<run>` writes `asg_map.md` and
`asg_map.json` into the run folder; copy the table below with the date. `fanuc-snpx parse system.va` does
the same offline.

What is known so far, unverified: the owner's notes (source of the capture not recorded) list

```
$SNPX_PARAM: $TIMEOUT 5000, $NUM_ASG 80, $NUM_FRIF 4, $VERSION 2, $STATUS 0, $DISP_INFO 0,
             $MODBUS_ADR 1, $NUM_MODBUS 0, $MODBUS_PORT 502, $CMD_ENDIAN 1, $COMP_FLAG 0
$SNPX_ASG[1]: $ADDRESS 1, $SIZE 10000, $VAR_NAME 'R[1]@1.1', $MULTIPLY 1   (factory default)
$SNPX_ASG[2..80]: empty
```

That list has no `$NUM_CIMP`, but every public V9.x `system.va` has it (factory value 0) next to
`$NUM_FRIF = 4`. Expect `$NUM_CIMP = 0` on the test robot, which means **`CLRASG` would erase the shared
table** and session-scoped assignments are not available without changing `$NUM_CIMP` (a controller
configuration change for the owner to decide; see docs/TRACKER.md Q8).

| Slot | $ADDRESS | $SIZE | $VAR_NAME | $MULTIPLY | Covers | Source / date |
|---|---|---|---|---|---|---|
| | | | | | | |
