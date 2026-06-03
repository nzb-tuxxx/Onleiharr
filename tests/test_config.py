from __future__ import annotations

from pathlib import Path

import pytest

from onleiharr.config import ConfigError, extract_category_ids_from_url, load_config


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "onleiharr.toml"
    path.write_text(body, encoding="utf-8")
    return path


def base_config() -> str:
    return """
[general]
poll_interval_secs = 300.0
watch_product_ids = ["product-1", "product-2"]

[[watch_categories]]
description = "Sachbuch"
category_ids = ["cat-1"]
category_urls = [
  "https://niedersachsen.onleihe.de/search?categories=%5B%22cat-2%22%2C%22cat-1%22%5D",
]
keywords = ["python"]

[notification]
urls = ["json://localhost"]

[credentials]
host = "niedersachsen.onleihe.de"
onleihe_name = "Onleihe Niedersachsen"
library_name = "Stadtbibliothek Achim"
username = "user"
password = "secret"
"""


def test_load_config_supports_product_ids_category_urls_and_name_credentials(tmp_path: Path):
    config = load_config(write_config(tmp_path, base_config()))

    assert config.general.watch_product_ids == ["product-1", "product-2"]
    assert len(config.general.watch_categories) == 1
    assert config.general.watch_categories[0].category_ids == ["cat-1", "cat-2"]
    assert config.general.watch_categories[0].keywords == ["python"]
    assert config.credentials.host == "niedersachsen.onleihe.de"
    assert config.credentials.onleihe_name == "Onleihe Niedersachsen"
    assert config.credentials.library_name == "Stadtbibliothek Achim"


def test_load_config_allows_no_notification_targets_and_no_watches(tmp_path: Path):
    body = """
[general]
poll_interval_secs = 300.0
watch_product_ids = []

[notification]
urls = []

[credentials]
host = "niedersachsen.onleihe.de"
onleihe_name = "Onleihe Niedersachsen"
library_name = "Stadtbibliothek Achim"
username = "user"
password = "secret"
"""

    config = load_config(write_config(tmp_path, body))

    assert config.notification.urls == []
    assert config.general.watch_product_ids == []
    assert config.general.watch_categories == []


def test_load_config_accepts_id_credentials_and_env_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ONLEIHARR_USERNAME", "env-user")
    monkeypatch.setenv("ONLEIHARR_PASSWORD", "env-secret")
    monkeypatch.setenv("ONLEIHARR_HOST", "env.onleihe.de")
    monkeypatch.setenv("ONLEIHARR_ONLEIHE_ID", "onleihe-id")
    monkeypatch.setenv("ONLEIHARR_LIBRARY_ID", "library-id")

    config = load_config(write_config(tmp_path, base_config()))

    assert config.credentials.username == "env-user"
    assert config.credentials.password == "env-secret"
    assert config.credentials.host == "env.onleihe.de"
    assert config.credentials.onleihe_id == "onleihe-id"
    assert config.credentials.library_id == "library-id"


def test_extract_category_ids_from_url_decodes_browser_categories():
    ids = extract_category_ids_from_url(
        "https://niedersachsen.onleihe.de/search?categories=%5B%22cat-a%22%2C%22cat-b%22%5D"
    )

    assert ids == ["cat-a", "cat-b"]


def test_load_config_derives_media_type_and_sort_from_category_url(tmp_path: Path):
    body = base_config().replace(
        "https://niedersachsen.onleihe.de/search?categories=%5B%22cat-2%22%2C%22cat-1%22%5D",
        "https://niedersachsen.onleihe.de/search?categories=%5B%22cat-2%22%5D&mediaType=E_BOOK&language=ger&authors_fullName=Ada&publisher.name=Pub&rating.value=4&licence.isAvailable=true&sortField=licence.stockChangedTimestamp&sortType=ascending",
    )

    config = load_config(write_config(tmp_path, body))
    watch = config.general.watch_categories[0]

    assert watch.media_types == ["E_BOOK"]
    assert watch.filters == [
        {"field": "mediaType", "values": ["E_BOOK"]},
        {"field": "language", "values": ["ger"]},
        {"field": "authors_fullName", "values": ["Ada"]},
        {"field": "publisher.name", "values": ["Pub"]},
        {"field": "rating.value", "values": ["4"]},
        {"field": "licence.isAvailable", "values": ["true"]},
    ]
    assert watch.sort_field == "licence.stockChangedTimestamp"
    assert watch.sort_order == "DESC"


def test_category_watch_requires_keywords(tmp_path: Path):
    body = base_config().replace('keywords = ["python"]', "keywords = []")

    with pytest.raises(ConfigError, match="needs at least one keyword"):
        load_config(write_config(tmp_path, body))


def test_root_watch_product_ids_is_rejected(tmp_path: Path):
    body = base_config().replace(
        "[general]\npoll_interval_secs = 300.0\nwatch_product_ids = [\"product-1\", \"product-2\"]",
        'watch_product_ids = ["product-1"]\n\n[general]\npoll_interval_secs = 300.0',
    )

    with pytest.raises(ConfigError, match=r"watch_product_ids must be configured under \[general\]"):
        load_config(write_config(tmp_path, body))


def test_watch_product_ids_inside_category_is_rejected(tmp_path: Path):
    body = base_config().replace(
        'keywords = ["python"]',
        'keywords = ["python"]\nwatch_product_ids = ["product-1"]',
    )

    with pytest.raises(ConfigError, match=r"move it to \[general\]"):
        load_config(write_config(tmp_path, body))
