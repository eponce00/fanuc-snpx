"""fanuc_snpx: a pure-Python SNPX (GE SRTP) client for FANUC robot controllers.

Read-only by default. See README.md and docs/ for the protocol, the safety model
and the verification status of every field.

Typical read-only use::

    from fanuc_snpx import SnpxClient, AssignmentTable

    with SnpxClient("192.0.2.10", assignments=AssignmentTable.factory_default()) as robot:
        print(robot.numeric_registers.read(1))
        print(robot.io.read("DO", 1, 8))
"""

from __future__ import annotations

from .assignments import (
    Assignment,
    AssignmentRequest,
    AssignmentTable,
    SysvarType,
    VarName,
    register_array_dims,
)
from .client import SnpxClient
from .errors import (
    PolicyFileError,
    PolicyViolation,
    ReadBackMismatch,
    RepresentationError,
    SnpxAssignmentError,
    SnpxError,
    SrtpConnectionError,
    SrtpProtocolError,
    SrtpServiceError,
    SrtpTimeout,
    WriteNotAllowed,
    WriteVetoed,
)
from .evidence import EvidenceLog
from .memory import Segment
from .policy import AccessPolicy
from .srtp import DEFAULT_PORT, SrtpSession
from .types import Cartesian, Configuration, IoFamily, Joints, Position

__version__ = "0.1.0.dev0"

__all__ = [
    "DEFAULT_PORT",
    "AccessPolicy",
    "Assignment",
    "AssignmentRequest",
    "AssignmentTable",
    "Cartesian",
    "Configuration",
    "EvidenceLog",
    "IoFamily",
    "Joints",
    "PolicyFileError",
    "PolicyViolation",
    "Position",
    "ReadBackMismatch",
    "RepresentationError",
    "Segment",
    "SnpxAssignmentError",
    "SnpxClient",
    "SnpxError",
    "SrtpConnectionError",
    "SrtpProtocolError",
    "SrtpServiceError",
    "SrtpSession",
    "SrtpTimeout",
    "SysvarType",
    "VarName",
    "WriteNotAllowed",
    "WriteVetoed",
    "__version__",
    "register_array_dims",
]
