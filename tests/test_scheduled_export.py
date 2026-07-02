"""Scheduled settings export (v1.12.54)."""
from __future__ import annotations

import json
import time

import pytest

from app.core import scheduled_export


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr("app.config.settings.data_dir", d)
    # reset the module cache so each test reads fresh
    monkeypatch.setattr(scheduled_export, "_CACHE", None)
    (d / "auth_settings.json").write_text(json.dumps({"backend": "local"}))
    return d


def test_defaults_disabled(data_dir):
    s = scheduled_export.get_settings()
    assert s["enabled"] is False
    assert s["interval"] == "daily"
    assert s["target_dir_effective"].endswith("settings_backups")


def test_save_validation(data_dir):
    with pytest.raises(ValueError):
        scheduled_export.save_settings({"interval": "hourly"})
    with pytest.raises(ValueError):
        scheduled_export.save_settings({"interval": "daily", "keep": 0})
    with pytest.raises(ValueError):
        scheduled_export.save_settings({"interval": "daily", "keep": 999})


def test_save_and_reload(data_dir):
    saved = scheduled_export.save_settings({
        "enabled": True, "interval": "weekly", "keep": 5,
        "target_dir": "", "categories": ["auth"]})
    assert saved["enabled"] is True
    assert saved["interval"] == "weekly"
    assert saved["keep"] == 5
    assert saved["categories"] == ["auth"]


def test_run_now_creates_and_rotates(data_dir):
    scheduled_export.save_settings({
        "enabled": True, "interval": "daily", "keep": 2,
        "target_dir": "", "categories": ["auth"]})
    target = data_dir / "settings_backups"
    # Run three times → keep=2 should leave only 2 files.
    for _ in range(3):
        scheduled_export.run_export_now()
        time.sleep(1.05)  # distinct mtimes + filenames (second-resolution)
    zips = sorted(target.glob("jtdt-settings-*.zip"))
    assert len(zips) == 2, [z.name for z in zips]
    # last_run recorded
    assert scheduled_export.get_settings()["last_run"] > 0


def test_due_logic(data_dir):
    cfg = {"enabled": True, "interval": "daily", "last_run": 0.0}
    assert scheduled_export._due(cfg, time.time()) is True
    cfg["last_run"] = time.time()
    assert scheduled_export._due(cfg, time.time()) is False
    cfg["enabled"] = False
    cfg["last_run"] = 0.0
    assert scheduled_export._due(cfg, time.time()) is False
