from __future__ import annotations

import socket
from collections.abc import Callable

from fanuc_snpx.srtp import HEADER_LEN, RateLimiter


def fast_limiter() -> RateLimiter:
    return RateLimiter(10_000)


class ScriptedSocket:
    """A socket stand-in that answers each request with a scripted reply.

    The reply's sequence bytes (2-3) are rewritten to echo the request, so captured
    frames can be replayed against any sequence number.
    """

    def __init__(self, replies: list[bytes], *, echo_seq: bool = True) -> None:
        self.replies = list(replies)
        self.sent: list[bytes] = []
        self._rx = bytearray()
        self.closed = False
        self.echo_seq = echo_seq

    def setsockopt(self, *args: object) -> None:
        pass

    def settimeout(self, value: float | None) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        if len(data) < HEADER_LEN or not self.replies:
            return
        reply = bytearray(self.replies.pop(0))
        if self.echo_seq and data[0] != 0x00:
            reply[2:4] = data[2:4]
        self._rx += reply

    def recv(self, n: int) -> bytes:
        if not self._rx:
            raise TimeoutError("scripted socket has no more data")
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def close(self) -> None:
        self.closed = True


def scripted_factory(sock: ScriptedSocket) -> Callable[[tuple[str, int], float], socket.socket]:
    def factory(address: tuple[str, int], timeout: float) -> socket.socket:
        return sock  # type: ignore[return-value]

    return factory
