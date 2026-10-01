# FANUC PLC Motion I/O Reference (B-84214EN/01)

This manual describes PLC-controlled robot commands over a mapped DI/DO
request/acknowledgement interface on R-30iB Plus-family controllers.

Full PDF and extract:

- `docs/manuals/B-84214EN_01_PLC_Motion_IO.pdf`
- `docs/manuals/extracts/B-84214EN_01_PLC_Motion_IO.txt`

## Interface model

1. PLC function blocks write command data to the robot's DI request area.
2. The robot validates the magic number and function-block version.
3. The robot processes the request and returns status through the DO
   acknowledgement area.
4. Motion function blocks append corresponding instructions to a TP program.
5. The PLC polls Active/Done, resets the buffer, or aborts the session.

## Function-block groups

- Communication: `FRC_WriteGroupData`, `FRC_ReadGroupData`
- Administrative: initialize, abort, pause, continue, reset, frame/tool access,
  touch-up, override, and actual-position reads
- Motion/instruction: linear, direct, joint, relative, circular, path trigger,
  motion options, frame/tool/payload selection, calls, DIN waits, and timed waits

## Constraints

- The documented interface supports a single motion group.
- Hot Start, short-distance motion, original-path resume, and backup behavior
  have documented limitations.
- Robot setup requires the specified DI/DO map and system variables.

## Repo relationship

This interface is distinct from the Amperesand PNS/UOP baseline and from normal
EtherNet/IP implicit I/O. Do not substitute PLC Motion I/O mappings for the
frozen Gen3 I/O workbook without an explicitly approved architecture change.

