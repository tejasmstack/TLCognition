"""Socket guard (spec 03 §7.9.6): no test may reach the network unless marked ``live_vlm``."""

import socket

import pytest


class NetworkDisabledInTests(RuntimeError):
    pass


def pytest_configure(config):
    config.addinivalue_line("markers", "live_vlm: hits real VLM endpoints (nightly only)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("-m", default=None):
        skip = pytest.mark.skip(reason="live_vlm excluded from default run")
        for item in items:
            if "live_vlm" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _socket_guard(request, monkeypatch):
    if "live_vlm" in request.keywords:
        yield
        return

    def _blocked(self, *a, **k):
        raise NetworkDisabledInTests(f"network disabled in tests: connect{a}")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    yield
