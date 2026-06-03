# Onleiharr

Onleiharr watches Onleihe v3 products, product series, and category searches, sends notifications for new media, and can auto-lend/reserve matching items. The Onleihe v3 API client is vendored under `onleiharr/_vendor/onleihe`; no separate `onleihe` PyPI package is required.

- Watch product or series IDs directly.
- Watch broad category searches with keyword filters.
- Auto-lend or reserve watched items.
- Optional auto-downloads via libgourou and DRM removal with explicit acknowledgment.
- Send downloaded media through Apprise targets that support attachments.

## Installation
Requirements: Python 3.10+.

Recommended:
- `pipx install onleiharr`
- `pipx upgrade onleiharr`
- `onleiharr --version`

From source:
- `pip install -r requirements.txt`
- `python3 main.py`
- `python3 -m onleiharr`

## Quick Start
1. Run `onleiharr` in an interactive terminal. If no config exists, the first-start wizard opens.
2. Select your library, validate your credentials, and add optional product/category watches.
3. Test once: `onleiharr --once`
4. Run continuously: `onleiharr`

Manual config setup:
- Start the wizard explicitly: `onleiharr --init-config`
- Disable the wizard for scripts/services: `onleiharr --no-wizard`

Default config path:
- Linux: `~/.config/onleiharr/onleiharr.toml`
- macOS: `~/Library/Application Support/onleiharr/onleiharr.toml`
- Windows: `%APPDATA%\onleiharr\onleiharr.toml`

CLI/env precedence for config path: `-c/--config` > `ONLEIHARR_CONFIG` > OS default.

## Configuration
```toml
[general]
poll_interval_secs = 300.0
watch_product_ids = [
  "69b3ed6bc56755bf97cb3b9a", # product or magazine series
  "69fcc1817003d0749a67b48d",
]

[[watch_categories]]
description = "Sachbuch & Ratgeber"
category_ids = [
  "65afa17e40246d5939bdbb53",
  "65afa17e40246d5939bdbb54",
]
category_urls = [
  "https://niedersachsen.onleihe.de/search?categories=%5B%2265afa17e40246d5939bdbb53%22%5D",
]
keywords = ["python", "meinHobby"]

[notification]
urls = [
  "tgram://{bot_token}/{chat_id}/?format=html",
]
test_notification = false
email = ""

[credentials]
host = "niedersachsen.onleihe.de"
onleihe_name = "Onleihe Niedersachsen"
library_name = "Stadtbibliothek Achim"
# onleihe_id = ""
# library_id = ""
username = "your-username"
password = "your-password"

[gourou]
# bin_dir = "~/bin"
# adept_dir = "~/.config/adept"
# download_dir = "~/Downloads/Onleiharr"
# download_permissions = "0644"
# timeout_secs = 30.0
# remove_drm = false
# remove_drm_ack = "I_UNDERSTAND"
# lendings_poll_interval_secs = 21600.0
# lendings_notify = true
```

Behavior:
- `watch_product_ids` are direct product or series watches and do not use keyword filtering.
- `watch_categories` combine `category_ids` and IDs extracted from `category_urls`, then search newest media first with a fixed page size of 50.
- Category watches only act on media whose title, subtitle, or authors match their `keywords`.
- If `lendings_poll_interval_secs` is greater than `0`, the my-media poller downloads newly discovered lendings that were not primed at startup. Set it to `0` to disable this feature.
- Notification targets are optional. Without Apprise URLs/config, Onleiharr logs notification skips and keeps watching/downloading.
- Credentials can use names by default; IDs are optional exact overrides.

Environment overrides:
- `ONLEIHARR_CONFIG`
- `ONLEIHARR_USERNAME`, `ONLEIHARR_PASSWORD`, `ONLEIHARR_HOST`
- `ONLEIHARR_ONLEIHE_NAME`, `ONLEIHARR_ONLEIHE_ID`
- `ONLEIHARR_LIBRARY_NAME`, `ONLEIHARR_LIBRARY_ID`
- `ONLEIHARR_WATCH_PRODUCT_IDS`
- `ONLEIHARR_EMAIL`, `ONLEIHARR_APPRISE_URLS`, `ONLEIHARR_APPRISE_CONFIG`
- `ONLEIHARR_POLL_INTERVAL`, `ONLEIHARR_TEST_NOTIFICATION`
- `ONLEIHARR_GOUROU_BIN_DIR`, `ONLEIHARR_GOUROU_ADEPT_DIR`, `ONLEIHARR_GOUROU_DOWNLOAD_DIR`
- `ONLEIHARR_GOUROU_DOWNLOAD_PERMISSIONS`, `ONLEIHARR_GOUROU_TIMEOUT`
- `ONLEIHARR_GOUROU_REMOVE_DRM`, `ONLEIHARR_GOUROU_ACK_DRM`
- `ONLEIHARR_GOUROU_LENDINGS_POLL_INTERVAL`
- `ONLEIHARR_GOUROU_LENDINGS_NOTIFY`

## libgourou
libgourou is optional. Onleiharr can notify and auto-lend/reserve without it, but downloads require `acsmdownloader`.

To enable DRM removal, set both:
- `gourou.remove_drm = true`
- `gourou.remove_drm_ack = "I_UNDERSTAND"`

This is not legal advice. You are responsible for verifying whether DRM removal for personal use is lawful in your jurisdiction. See `DISCLAIMER.md`.

## Notifications
Apprise URLs are configured in `[notification].urls`; legacy `apprise_config_path` is still accepted. Notification bodies use HTML formatting. Attachments are only sent when the Apprise target reports attachment support.

## Systemd
- Install user unit: `onleiharr --install-as-user-systemd`
- Reload and enable: `systemctl --user daemon-reload && systemctl --user enable --now onleiharr`
- Logs: `journalctl --user -u onleiharr -f`

## Development
- Smoke: `python -m py_compile onleiharr/*.py onleiharr/_vendor/onleihe/*.py main.py`
- Tests: `pytest`
- Build: `python -m build --no-isolation`

## Runtime Behavior
- First poll primes the in-memory cache without sending new-media notifications.
- Later polls notify on new watched media.
- Product watches are treated as explicit wanted media.
- Category watches require keyword matches.
- My-media polling uses the v3 API to find fulfilled loans with ACSM links.

## License
MIT
