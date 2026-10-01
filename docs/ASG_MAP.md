# `$SNPX_ASG` map of the test robot

**Not read yet.** This page is filled in Phase 2 from the controller's own files (read-only FTP), and only
then compared with what %R reads return.

What is known so far, unverified: the user's notes (source of the capture not recorded) list

```
$SNPX_PARAM: $TIMEOUT 5000, $NUM_ASG 80, $NUM_FRIF 4, $VERSION 2, $STATUS 0, $DISP_INFO 0,
             $MODBUS_ADR 1, $NUM_MODBUS 0, $MODBUS_PORT 502, $CMD_ENDIAN 1, $COMP_FLAG 0
$SNPX_ASG[1]: $ADDRESS 1, $SIZE 10000, $VAR_NAME 'R[1]@1.1', $MULTIPLY 1   (factory default)
$SNPX_ASG[2..80]: empty
```

Open point: the CIMPLICITY manual names `$SNPX_PARAM.$NUM_CIMP` as the multi-connection switch that makes
`CLRASG` session-scoped. That field is not in the list above. Until the controller's own file shows how
multiplexing is configured on V9.30, `CLRASG` must be treated as erasing the shared table.

| Slot | $ADDRESS | $SIZE | $VAR_NAME | $MULTIPLY | Covers | Source / date |
|---|---|---|---|---|---|---|
| | | | | | | |
