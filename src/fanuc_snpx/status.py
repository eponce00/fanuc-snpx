"""Descriptions of GE SNP/SRTP error status codes.

The codes come from GE's Series 90 SNP documentation (GFK-0582D, tables 6-2
and 6-3: major codes, and minor codes for major code 5). Whether a FANUC
controller reports the same codes in reply bytes 42/43 is **not verified**;
captured FANUC error replies carried 00 00 there. Descriptions are therefore
hints for humans, never used for decisions in code.
"""

from __future__ import annotations

MAJOR: dict[int, str] = {
    0x01: "successful completion",
    0x02: "insufficient privilege",
    0x04: "protocol sequence error (message out of order)",
    0x05: "service request error",
    0x06: "illegal mailbox type",
    0x07: "service request queue full (retry after at least 10 ms)",
    0x0A: "SNP DOS driver error",
    0x0B: "illegal service request (not defined or not supported)",
    0x0C: "local SNP/SNP-X error",
    0x0D: "remote SNP error",
    0x0E: "autodial error",
    0x0F: "SNP-X slave error",
    0x13: "port configurator error",
}

_MINOR_5 = [
    "service request aborted",
    "no privilege for attempted operation",
    "unable to perform auto configuration",
    "I/O configuration is invalid",
    "cannot clear I/O configuration",
    "cannot replace I/O module",
    "task address out of range",
    "invalid task name referenced",
    "required to log in to a task for service",
    "invalid sweep state to set",
    "invalid password",
    "invalid input parameter in request",
    "I/O configuration mismatch",
    "invalid program cannot log in",
    "request only valid from programmer",
    "request only valid in stop mode",
    "programmer is already attached",
    "could not return block sizes",
    "VME bus error",
    "task unable to be created",
    "task unable to be deleted",
    "not logged in to process service request",
    "memory type selector not valid in context",
    "no user memory available to allocate",
    "configuration is not valid",
    "CPU model number does not match",
    "DOS file area not formatted",
    "memory type for this selector does not exist",
    "CPU revision number does not match",
    "IOS could not delete configuration or bad type",
    "no I/O configuration to read or delete",
    "service in process cannot log in",
    "invalid datagram connection address",
    "size of datagram connection invalid",
    "unable to locate given datagram connection ID",
    "unable to find connection address",
    "invalid memory type selector in datagram",
    "null pointer to data in memory type selector",
    "transfer type invalid for this memory type selector",
    "point length not allowed",
    "invalid datagram type specified",
    "total datagram connection memory exceeded",
    "invalid block name specified in datagram",
    "mismatch of configuration checksum",
    "user program module read or write exceeded block end",
    "invalid write mode parameter",
    "packet size or total program size does not match input",
    "a configured module has an unsupported revision",
    "specified device is not available",
    "specified device has insufficient memory",
    "device has no stored data",
    "data stored on device is corrupted",
    "comm or write verify error during save or restore",
    "device is write-protected",
    "login with non-zero buffer size required for block commands",
    "passwords already enabled and cannot be forced inactive",
    "passwords are inactive and cannot be enabled or disabled",
    "control program tasks exist but requestor not logged into main CP",
    "no task-level rack/slot configuration to read or delete",
    "verify with FA card or EEPROM failed",
    "text length does not match traffic type",
    "OEM key is null (inactive)",
    "invalid block state transition",
]
# Minor codes for major 5 are negative numbers: -1 = 0xFF, -2 = 0xFE, ...
MINOR_FOR_SERVICE_ERROR: dict[int, str] = {0x100 - (i + 1): t for i, t in enumerate(_MINOR_5)}


def describe_status(major: int, minor: int) -> str | None:
    """A human-readable hint for a status pair, or ``None`` if the codes are unknown."""
    text = MAJOR.get(major)
    if text is None:
        return None
    if major == 0x05 and minor in MINOR_FOR_SERVICE_ERROR:
        return f"{text}: {MINOR_FOR_SERVICE_ERROR[minor]}"
    return text
