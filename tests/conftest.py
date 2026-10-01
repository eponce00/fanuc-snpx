from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest

from fanuc_snpx.srtp import SrtpSession
from fanuc_snpx.testing import FakeController, FakeSrtpServer
from helpers import fast_limiter


@pytest.fixture
def server() -> Iterator[FakeSrtpServer]:
    with FakeSrtpServer(FakeController()) as srv:
        yield srv


@pytest.fixture
def make_session(server: FakeSrtpServer) -> Iterator[Callable[..., SrtpSession]]:
    sessions: list[SrtpSession] = []

    def factory(**kwargs: object) -> SrtpSession:
        kwargs.setdefault("timeout", 1.0)
        kwargs.setdefault("rate_limiter", fast_limiter())
        s = SrtpSession(server.host, server.port, **kwargs)  # type: ignore[arg-type]
        sessions.append(s)
        return s

    yield factory
    for s in sessions:
        s.close()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("FANUC_SNPX_HARDWARE") == "1":
        return
    skip = pytest.mark.skip(reason="hardware test: set FANUC_SNPX_HARDWARE=1 to run (read-only)")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)
