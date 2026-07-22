from pathlib import Path

import pytest

from onleiharr.config import GourouConfig
from onleiharr.gourou import GourouClient, GourouError


def _config(tmp_path: Path) -> GourouConfig:
    return GourouConfig(
        bin_dir=None,
        adept_dir=tmp_path / "adept",
        download_dir=tmp_path / "downloads",
        download_permissions=0o644,
        timeout_secs=30.0,
        remove_drm=False,
        remove_drm_ack=None,
        lendings_poll_interval_secs=0.0,
        lendings_notify=False,
    )


def test_validate_download_readiness_accepts_activated_adept_dir(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.adept_dir.mkdir()
    for name in ("device.xml", "activation.xml", "devicesalt"):
        (config.adept_dir / name).write_text("test", encoding="utf-8")
    client = GourouClient(config)
    monkeypatch.setattr(client, "_find_bin", lambda name: "/usr/bin/acsmdownloader")

    client.validate_download_readiness()

    assert config.download_dir.is_dir()


def test_validate_download_readiness_rejects_missing_activation(tmp_path, monkeypatch):
    client = GourouClient(_config(tmp_path))
    monkeypatch.setattr(client, "_find_bin", lambda name: "/usr/bin/acsmdownloader")

    with pytest.raises(GourouError, match="ADEPT directory missing device files"):
        client.validate_download_readiness()
