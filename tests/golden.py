"""Golden frames.

``CAPTURED_*`` frames were captured between a commercial GE Ethernet driver
(Kepware) or a test client and a FANUC controller on TCP 18245. They are
published in Booozie-Z/Fanuc_GESRTP_Driver, docs/"srtp packets.txt" (MIT).
Where Kepware used a 16-bit transaction number in bytes 2-3, the test masks
byte 3 (this client sends an 8-bit sequence number and byte 3 = 0).

Nothing here comes from the test robot of this project yet; see
docs/VALIDATION_LOG.md for hardware evidence.
"""

from __future__ import annotations


def h(text: str) -> bytes:
    return bytes.fromhex(text)


# Session control (service 0x4F) sent by Kepware right after the init packet.
CAPTURED_SESSION_CONTROL = h(
    "02 00 01 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
    " 00 00 01 c0 00 00 00 00 10 0e 00 00 01 01 4f 01 00 00 00 00 00 00 00 00 00 00 00 00"
)
CAPTURED_SESSION_CONTROL_REPLY = h(
    "03 00 01 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
    " 00 00 01 d4 10 0e 00 00 30 3a 00 00 01 01 00 00 00 00 00 00 01 01 ff 02 00 00 7c 21"
)

# Kepware: read %AI11 (GO[11]), one word. Byte 3 (0x00 here) is part of Kepware's u16 id.
CAPTURED_READ_AI11 = h(
    "02 00 ae 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
    " 00 00 06 c0 00 00 00 00 10 0e 00 00 01 01 04 0a 0a 00 01 00 00 00 00 00 00 00 00 00"
)
CAPTURED_READ_AI11_REPLY = h(
    "03 00 ae 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
    " 00 00 06 d4 10 0e 00 00 30 3a 00 00 01 01 00 00 30 02 00 00 00 00 00 00 00 00 7c 21"
)

# Test client: read %R2043..%R2082 (40 words, an SR[] string) -> 0x94 with 80 bytes.
# The header is as captured; the 80-byte text is reconstructed ("LD PB" + NULs)
# because the capture file runs the tail of this frame into the next line.
CAPTURED_READ_R2043_X40 = h(
    "02 00 06 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
    " 00 00 06 c0 00 00 00 00 10 0e 00 00 01 01 04 08 fa 07 28 00 00 00 00 00 00 00 00 00"
)
CAPTURED_READ_R2043_X40_REPLY = (
    h(
        "03 00 06 00 50 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
        " 00 00 06 94 10 0e 00 00 30 3a 00 00 01 01 00 00 00 00 00 00 01 01 ff 04 00 00 7c 21"
    )
    + b"LD PB"
    + bytes(75)
)

# Kepware: write "adb\0" to %R11001..%R11002 (two words).
CAPTURED_WRITE_STRING = h(
    "02 00 c1 00 04 00 00 00 00 02 00 00 00 00 00 00 00 02 00 00 00 00 00 00 00 00 00 00"
    " 00 00 09 80 00 00 00 00 10 0e 00 00 01 01 04 00 00 00 00 00 01 01 07 08 f8 2a 02 00"
    " 61 64 62 00"
)
CAPTURED_WRITE_ACK = h(
    "03 00 c1 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
    " 00 00 09 d4 10 0e 00 00 30 3a 00 00 01 01 00 00 00 00 00 00 01 01 ff 04 00 00 7c 21"
)

# Kepware: write 1234 to %R1 (byte 3 = 0x68 is Kepware's id high byte; masked in tests).
CAPTURED_WRITE_R1_1234 = h(
    "02 00 fd 68 02 00 00 00 00 02 00 00 00 00 00 00 00 02 00 00 00 00 00 00 00 00 00 00"
    " 00 00 09 80 00 00 00 00 10 0e 00 00 01 01 02 00 00 00 00 00 01 01 07 08 00 00 01 00"
    " d2 04"
)

# Kepware: set DO[10] (%I10 -> bit index 9): payload byte has bit 9 % 8 = 1 set.
CAPTURED_WRITE_DO10_ON = h(
    "02 00 5b 02 01 00 00 00 00 02 00 00 00 00 00 00 00 02 00 00 00 00 00 00 00 00 00 00"
    " 00 00 09 80 00 00 00 00 10 0e 00 00 01 01 01 00 00 00 00 00 01 01 07 46 09 00 01 00"
    " 02"
)

# Kepware: set DI[101] (%Q101 -> bit index 100): 100 % 8 = 4 -> 0x10.
CAPTURED_WRITE_DI101_ON = h(
    "02 00 cb 05 01 00 00 00 00 02 00 00 00 00 00 00 00 02 00 00 00 00 00 00 00 00 00 00"
    " 00 00 09 80 00 00 00 00 10 0e 00 00 01 01 01 00 00 00 00 00 01 01 07 48 64 00 01 00"
    " 10"
)

# An error reply (0xD1) the controller sent to a write the test client made with a
# byte selector. Note the status bytes 42-43 are 00 00 even though it is an error.
CAPTURED_ERROR_REPLY = h(
    "03 00 06 00 00 00 00 00 00 01 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00"
    " 00 00 09 d1 10 0e 00 00 30 3a 00 00 01 01 00 00 00 00 00 00 01 01 ff 04 00 00 7c 21"
)

# Init reply observed from this project's test robot (F546175, V9.30P/17) on 2026-09-30,
# before this repository existed. Only the first 16 bytes were recorded
# ("0100000000000000 0100000000000000..."); the rest is assumed zero here.
OBSERVED_INIT_REPLY = bytes([0x01, 0, 0, 0, 0, 0, 0, 0, 0x01]) + bytes(47)
