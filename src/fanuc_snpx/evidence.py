"""Opt-in evidence log: every frame and every write decision as JSON lines.

Each line is one JSON object with at least ``ts`` (UTC ISO-8601) and ``event``.
Frame events carry the full frame as hex plus a decoded summary, so a log can be
re-checked later without re-running anything against the robot.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any


class EvidenceLog:
    """Append-only JSONL writer. Safe to share between threads."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._fh: IO[str] | None = None

    def _file(self) -> IO[str]:
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8", newline="\n")
        return self._fh

    def record(self, event: str, **fields: Any) -> None:
        """Write one event line and flush it immediately."""
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "event": event,
        }
        entry.update(fields)
        line = json.dumps(entry, sort_keys=False, default=_json_default)
        with self._lock:
            fh = self._file()
            fh.write(line + "\n")
            fh.flush()

    def frame(self, direction: str, data: bytes, **fields: Any) -> None:
        """Record a raw frame (``direction`` is ``"tx"`` or ``"rx"``)."""
        self.record("frame", direction=direction, length=len(data), hex=data.hex(" "), **fields)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> EvidenceLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _json_default(obj: object) -> object:
    if isinstance(obj, bytes | bytearray):
        return bytes(obj).hex(" ")
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return repr(obj)
