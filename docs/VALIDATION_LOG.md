# Validation log

Every claim that something works "on the robot" needs an entry here: timestamp, controller, command,
raw bytes, decoded value, and the FTP-oracle value it was compared with. Floats are compared at float32
precision. Files are snapshots; compare only values that are not changing, and read both paths twice.

Test robot: LR Mate 200iD/7L, R-30iB Plus controller F546175, software V9.30P/17, 10.50.160.51.

| # | Date (UTC) | What | Raw (hex) | Decoded | Oracle value / file | Result |
|---|---|---|---|---|---|---|
| 0 | 2026-09-30 | SRTP init on 18245, before this repository existed (user's notes) | reply `01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 …` (56 bytes, first 16 recorded) | init ack, byte 0 = 01 | n/a | handshake answered; no data read or written |

No SRTP data has been read from the robot yet (Phase 2 not started).
