"""Read-only FTP access to controller files, used as an independent oracle.

FANUC controllers publish their own state as files (``numreg.va``,
``posreg.va``, ``sysframe.va``, ``curpos.dg``, ``errall.ls``, ...). Comparing
SRTP reads with these files validates the client without trusting it.

:class:`ReadOnlyFtp` refuses every FTP command that could change the controller
(STOR, DELE, RNFR, MKD, SITE, ...) at the lowest level of :mod:`ftplib`, before
anything is sent. Parsers for the file formats are added only after they have
been checked against real files from a controller (docs/VALIDATION_LOG.md).
"""

from __future__ import annotations

import ftplib
import io
from pathlib import Path

ALLOWED_COMMANDS = frozenset(
    {
        "USER",
        "PASS",
        "ACCT",
        "TYPE",
        "PASV",
        "EPSV",
        "PORT",
        "EPRT",
        "RETR",
        "NLST",
        "LIST",
        "CWD",
        "PWD",
        "SYST",
        "FEAT",
        "NOOP",
        "QUIT",
        "SIZE",
        "MDTM",
    }
)


class ReadOnlyFtp(ftplib.FTP):
    """An :class:`ftplib.FTP` that can only log in, list and download."""

    def putcmd(self, line: str) -> None:
        verb = line.split(" ", 1)[0].upper()
        if verb not in ALLOWED_COMMANDS:
            raise PermissionError(f"FTP command {verb!r} is not allowed (read-only oracle)")
        super().putcmd(line)


class ControllerFiles:
    """Anonymous, read-only file access to a controller.

    Use as a context manager::

        with ControllerFiles("192.0.2.10") as files:
            text = files.read_text("numreg.va")
    """

    def __init__(
        self,
        host: str,
        *,
        user: str = "anonymous",
        password: str = "",
        timeout: float = 10.0,
        port: int = 21,
    ) -> None:
        self.host = host
        self._ftp = ReadOnlyFtp()
        self._ftp.connect(host, port, timeout=timeout)
        self._ftp.login(user, password)

    def list(self, path: str = "") -> list[str]:
        return self._ftp.nlst(path) if path else self._ftp.nlst()

    def read_bytes(self, name: str) -> bytes:
        buf = io.BytesIO()
        self._ftp.retrbinary(f"RETR {name}", buf.write)
        return buf.getvalue()

    def read_text(self, name: str, encoding: str = "latin-1") -> str:
        return self.read_bytes(name).decode(encoding)

    def download(self, name: str, dest_dir: str | Path) -> Path:
        """Save ``name`` into ``dest_dir`` (created if needed) and return the path."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / Path(name).name
        out.write_bytes(self.read_bytes(name))
        return out

    def close(self) -> None:
        try:
            self._ftp.quit()
        except (OSError, EOFError, ftplib.Error):
            self._ftp.close()

    def __enter__(self) -> ControllerFiles:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
