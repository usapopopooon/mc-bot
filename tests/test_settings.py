import json

import pytest

from mc_bot.settings import RuntimeSettings, SettingsStore


def test_missing_file_uses_defaults(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")

    assert store.load() == RuntimeSettings()


def test_saves_and_loads_settings(tmp_path) -> None:
    path = tmp_path / "nested" / "settings.json"
    store = SettingsStore(path)
    settings = RuntimeSettings(channel_id=123456789, server_label="うさぽサーバー")

    store.save(settings)

    assert store.load() == settings
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "channel_id": 123456789,
        "server_label": "うさぽサーバー",
    }


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"channel_id": "general"}',
        '{"channel_id": -1}',
        '{"server_label": ""}',
    ],
)
def test_rejects_invalid_settings(tmp_path, payload: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError):
        SettingsStore(path).load()
