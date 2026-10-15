import socket

import pytest


@pytest.fixture(autouse=True)
def isolate_settings_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(tmp_path / "settings.local.yaml"))


@pytest.fixture(autouse=True)
def deny_network_requests(monkeypatch):
    attempts = []

    def deny_call(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("Automated tests must not make network requests")

    monkeypatch.setattr(socket, "getaddrinfo", deny_call)
    monkeypatch.setattr(socket.socket, "connect", deny_call)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_call)

    return attempts
