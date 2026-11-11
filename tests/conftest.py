import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


def pytest_configure(config):
    # App imports initialize repositories during collection, before fixtures run.
    bootstrap = TemporaryDirectory(prefix="okx-bot-pytest-")
    monkeypatch = pytest.MonkeyPatch()
    config.add_cleanup(bootstrap.cleanup)
    config.add_cleanup(monkeypatch.undo)
    monkeypatch.setenv("OKX_BOT_DB_PATH", str(Path(bootstrap.name) / "bot.db"))
    monkeypatch.setenv(
        "OKX_BOT_SETTINGS_PATH", str(Path(bootstrap.name) / "settings.local.yaml")
    )


@pytest.fixture(autouse=True)
def isolate_settings_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(tmp_path / "settings.local.yaml"))


@pytest.fixture(autouse=True)
def isolate_repository_database(tmp_path, monkeypatch):
    monkeypatch.setenv("OKX_BOT_DB_PATH", str(tmp_path / "bot.db"))


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
