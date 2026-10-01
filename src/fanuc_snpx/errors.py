"""Exception types raised by fanuc_snpx.

Every failure is reported by raising one of these. The library never returns a
partial result as success and never swallows an error.
"""

from __future__ import annotations


class SnpxError(Exception):
    """Base class for every error raised by this package."""


class SrtpProtocolError(SnpxError):
    """The controller sent something this client does not understand.

    Examples: a malformed or truncated frame, an unexpected packet or message
    type, a response whose sequence number does not match the request, or a
    multi-packet response. The session that saw it is closed.
    """

    def __init__(self, message: str, *, frame: bytes | None = None) -> None:
        super().__init__(message)
        self.frame = frame


class SrtpServiceError(SrtpProtocolError):
    """The controller answered a request with an error status.

    ``major`` and ``minor`` are the raw status bytes from the response header
    (bytes 42 and 43). ``msg_type`` is header byte 31 (0xD1 for an error reply).
    """

    def __init__(
        self,
        message: str,
        *,
        major: int,
        minor: int,
        msg_type: int,
        service: int,
        segment: int | None,
        frame: bytes | None = None,
    ) -> None:
        super().__init__(message, frame=frame)
        self.major = major
        self.minor = minor
        self.msg_type = msg_type
        self.service = service
        self.segment = segment


class SrtpTimeout(SnpxError, TimeoutError):
    """No complete response arrived within the operation timeout.

    The session that timed out is closed; a later request opens a new one.
    """


class SrtpConnectionError(SnpxError, ConnectionError):
    """The TCP connection could not be opened, or the peer closed it."""


class SnpxAssignmentError(SnpxError):
    """The SNPX assignment table cannot provide the requested data.

    Raised when no assignment covers a variable, when an assignment would
    overlap one this client does not own, or when an assignment definition is
    invalid.
    """


class WriteNotAllowed(SnpxError, PermissionError):
    """A write was refused before anything was sent.

    Raised when the client is read-only, when no policy file is loaded, when the
    target is not inside a ``writable`` range, or when no reason was given.
    """


class WriteVetoed(WriteNotAllowed):
    """The caller's ``write_guard`` callback rejected the write."""


class PolicyViolation(SnpxError, PermissionError):
    """The access policy forbids the operation.

    Raised for writes to a ``protected`` target (even with ``allow_writes=True``)
    and for reads or writes outside the policy ``limits``.
    """


class PolicyFileError(SnpxError, ValueError):
    """The access-policy file is missing required fields or is malformed."""


class ReadBackMismatch(SnpxError):
    """The value read back after a write differs from the value written."""

    def __init__(self, message: str, *, target: str, expected: object, actual: object) -> None:
        super().__init__(message)
        self.target = target
        self.expected = expected
        self.actual = actual


class RepresentationError(SnpxError, ValueError):
    """A position is not available in the requested representation.

    For example, asking for the Cartesian view of a position register whose
    Cartesian data is invalid (VALIDC = 0).
    """
