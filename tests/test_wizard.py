from __future__ import annotations

import stat

import onleiharr.wizard as wizard
from onleiharr._vendor.onleihe import Library
from onleiharr.config import load_config
from onleiharr.wizard import (
    FirstStartWizard,
    WizardConfig,
    build_config_text,
    extract_product_id,
    normalize_host,
    write_config_atomic,
)


def test_extract_product_id_accepts_ids_and_urls():
    assert extract_product_id("69b3ed6bc56755bf97cb3b9a") == "69b3ed6bc56755bf97cb3b9a"
    assert (
        extract_product_id("https://niedersachsen.onleihe.de/mymedia/mediadetail?productId=69B3ED6BC56755BF97CB3B9A")
        == "69B3ED6BC56755BF97CB3B9A"
    )


def test_build_config_text_loads_without_notifications_or_watches(tmp_path):
    path = tmp_path / "onleiharr.toml"
    write_config_atomic(
        path,
        WizardConfig(
            host="niedersachsen.onleihe.de",
            onleihe_name="Onleihe Niedersachsen",
            library_name="Stadtbibliothek Achim",
            onleihe_id="onleihe-id",
            library_id="library-id",
            username="user",
            password="secret",
        ),
    )

    config = load_config(path, env={})

    assert config.notification.urls == []
    assert config.general.watch_product_ids == []
    assert config.general.watch_categories == []
    assert config.credentials.library_name == "Stadtbibliothek Achim"
    assert config.gourou.download_permissions == 0o644
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_build_config_text_writes_product_and_category_watches(tmp_path):
    path = tmp_path / "onleiharr.toml"
    text = build_config_text(
        WizardConfig(
            host="niedersachsen.onleihe.de",
            onleihe_name="Onleihe Niedersachsen",
            library_name="Stadtbibliothek Achim",
            username="user",
            password="secret",
            watch_product_ids=["product-1"],
            watch_categories=[
                {
                    "description": "Sachbuch",
                    "category_urls": [
                        "https://niedersachsen.onleihe.de/search?categories=%5B%22cat-1%22%5D",
                    ],
                    "keywords": ["python"],
                }
            ],
        )
    )
    path.write_text(text, encoding="utf-8")

    config = load_config(path, env={})

    assert '# bin_dir = "~/bin"' in text
    assert 'download_permissions = "0644"' in text
    assert config.general.watch_product_ids == ["product-1"]
    assert config.general.watch_categories[0].category_ids == ["cat-1"]
    assert config.general.watch_categories[0].keywords == ["python"]
    assert config.general.watch_categories[0].sort_field == "licence.stockChangedTimestamp"
    assert '# Alternative: sort_field = "publicationDate"' in text


def test_normalize_host_accepts_urls_and_hosts():
    assert normalize_host("niedersachsen.onleihe.de") == "niedersachsen.onleihe.de"
    assert normalize_host("https://niedersachsen.onleihe.de/search") == "niedersachsen.onleihe.de"
    assert normalize_host("HTTP://NIEDERSACHSEN.ONLEIHE.DE/") == "niedersachsen.onleihe.de"


def test_curses_library_selection_loads_host_libraries_before_typing(tmp_path, monkeypatch):
    munich = Library(
        id="696652cb9d468a92eb935dd8",
        onleihe_id="696652cb9d468a92eb935dd7",
        name="Münchner Stadtbibliothek",
        city="München",
    )

    class Client:
        def __init__(self, *, host):
            assert host == "muenchen.onleihe.de"

        def resolve_onleihe_id(self):
            return munich.onleihe_id

        def list_libraries(self, *, search_value, page, size):
            assert search_value is None
            return type("Page", (), {"libraries": [munich]})()

    class Screen:
        def __init__(self):
            self.lines = []

        def clear(self):
            pass

        def addstr(self, row, column, text):
            self.lines.append(text)

        def refresh(self):
            pass

        def getch(self):
            return 10

    monkeypatch.setattr(wizard, "OnleiheClient", Client)
    monkeypatch.setattr(wizard.curses, "COLS", 120, raising=False)
    monkeypatch.setattr(wizard.curses, "LINES", 30, raising=False)
    screen = Screen()

    selected = FirstStartWizard(tmp_path / "config.toml", version="test")._curses_select_library(
        screen, "muenchen.onleihe.de"
    )

    assert selected == munich
    assert any("Münchner Stadtbibliothek" in line for line in screen.lines)
