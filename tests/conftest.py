import pytest


@pytest.fixture(autouse=True)
def isolate_settings_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(tmp_path / "settings.local.yaml"))
