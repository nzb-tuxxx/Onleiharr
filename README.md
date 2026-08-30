# Onleiharr

> [!WARNING]
> **Onleiharr 0.3 is a Public Beta for Onleihe v3 only.**
>
> Install the 0.3 beta only if your Onleihe account/library already uses Onleihe v3. The beta can still contain bugs; please report issues at <https://github.com/nzb-tuxxx/Onleiharr/issues>.
>
> Onleihe v2 users should stay on the latest stable v2-compatible release. Install the Onleihe v3 beta using the exact version `0.3.0b4`; do not enable pre-releases globally because that can also select incompatible pre-release dependencies.

Onleiharr watches Onleihe v3 products, product series, and category searches, sends notifications for new media, and can auto-lend/reserve matching items. The Onleihe v3 API client is vendored under `onleiharr/_vendor/onleihe`; no separate `onleihe` PyPI package is required.

- Watch product or series IDs directly.
- Watch broad category searches with keyword filters.
- Auto-lend or reserve watched items.
- Download a product or product URL manually from the CLI.
- Optional auto-downloads via libgourou and DRM removal with explicit acknowledgment.
- Send downloaded media through Apprise targets that support attachments.

## Installation
Requirements: Python 3.10+.

Stable / Onleihe v2-compatible:
- `pipx install onleiharr`
- `pipx upgrade onleiharr`
- `onleiharr --version`

Public Beta / Onleihe v3:
- pipx install or replacement: `pipx install --force 'onleiharr==0.3.0b4'`
- pip install: `pip install 'onleiharr==0.3.0b4'`
- pip upgrade: `pip install --upgrade 'onleiharr==0.3.0b4'`
- `onleiharr --version`

Before starting the 0.3 beta after upgrading from a v2-compatible Onleiharr release, move or remove your old config file. The old v2 config format is not compatible with 0.3 because watches now use Onleihe v3 product/category IDs instead of legacy URLs. On first start, Onleiharr can create a new config with the interactive wizard; alternatively run `onleiharr --init-config` and choose the dummy config template.

From source:
- `pip install -r requirements.txt`
- `python3 main.py`
- `python3 -m onleiharr`

## Quick Start
1. Run `onleiharr watch` in an interactive terminal. If no config exists, the first-start wizard opens.
2. Select your library, validate your credentials, and add optional product/category watches.
3. Test once: `onleiharr watch --once`
4. Run continuously: `onleiharr watch`

Manual downloads use the same config and login as the watcher:

- Download one product: `onleiharr download 69b3ed6bc56755bf97cb3b9a`
- Download from a product URL: `onleiharr download 'https://niedersachsen.onleihe.de/search/mediadetail?productId=69b3ed6bc56755bf97cb3b9a'`

If the config is missing in an interactive terminal, the normal first-start wizard opens.

Running the watcher as bare `onleiharr` remains temporarily compatible but is deprecated and will be
removed in a future release. Scripts and service units should use `onleiharr watch`.
Re-running `onleiharr --install-as-user-systemd -c /path/to/onleiharr.toml` overwrites an existing
user unit with the explicit command. Afterwards run `systemctl --user daemon-reload` and
`systemctl --user restart onleiharr`.

Manual config setup:
- Start the wizard explicitly: `onleiharr --init-config`
- Disable the wizard for scripts/services: `onleiharr --no-wizard`
- Renew an external-library login: `onleiharr --login`

### External library login (OpenID)
Libraries such as Münchner Stadtbibliothek redirect authentication to their own identity provider. The wizard detects this through the Onleihe v3 API and prints an authorization URL. Open that URL on a local workstation, disable JavaScript for the login tab so the Onleihe web app cannot consume the one-time code, then paste the complete redirected URL into the SSH terminal. Onleiharr stores only the resulting Onleihe session in a private `session.json`; the library credentials are not written to the config.

No browser is required on the Onleiharr server. As an optional convenience on desktop installations, `onleiharr --login --login-browser` can manage a local Chromium/Chrome window:

```sh
pipx inject onleiharr playwright
# Arch Linux: sudo pacman -S chromium
```

The watcher refreshes the cached Onleihe session automatically and atomically persists every renewed token. If the session can no longer be refreshed, Onleiharr sends an Apprise notification containing the exact `onleiharr --login -c ...` and systemd restart commands, then exits with an authentication error.

For UPA libraries, the watcher instead attempts one fresh login and immediately retries the interrupted poll cycle. If that login or the retried cycle fails authentication again, Onleiharr exits with an authentication error so a user systemd unit can apply its `Restart=on-failure` policy. A successful full poll cycle resets this one-attempt recovery allowance.

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
sort_field = "licence.stockChangedTimestamp"  # Recommended: newest Onleihe stock changes first.
# Alternative: sort_field = "publicationDate"  # Newest bibliographic publication dates first.
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
download_permissions = "0644"
# timeout_secs = 30.0
# remove_drm = false
# remove_drm_ack = "I_UNDERSTAND"
# lendings_poll_interval_secs = 21600.0
# lendings_notify = true
```

For an external-login library, the wizard writes `auth_type = "open_id"` and `session_path = "session.json"` instead of `username` and `password`.

Behavior:
- `watch_product_ids` are direct product or series watches and do not use keyword filtering.
- `watch_categories` combine `category_ids` and IDs extracted from `category_urls`, then search newest media first with a fixed page size of 50.
- Category watches default to `sort_field = "licence.stockChangedTimestamp"` because it puts recent Onleihe licence-stock additions or changes first, including older publications newly added to the catalogue. Put it directly in the corresponding `[[watch_categories]]` table, alongside `category_urls` and `keywords`, as shown above. The alternative `sort_field = "publicationDate"` follows the title's bibliographic publication date. An explicit `sort_field` takes precedence over a `sortField` imported from a category URL. Category-watch sorting is always descending.
- Category watches only act on media whose title, subtitle, or authors match their `keywords`.
- If `lendings_poll_interval_secs` is greater than `0`, the my-media poller downloads newly discovered lendings that were not primed at startup. Set it to `0` to disable this feature.
- Notification targets are optional. Without Apprise URLs/config, Onleiharr logs notification skips and keeps watching/downloading.
- Credentials can use names by default; IDs are optional exact overrides.

Environment overrides:
- `ONLEIHARR_CONFIG`
- `ONLEIHARR_USERNAME`, `ONLEIHARR_PASSWORD`, `ONLEIHARR_HOST`
- `ONLEIHARR_ONLEIHE_NAME`, `ONLEIHARR_ONLEIHE_ID`
- `ONLEIHARR_LIBRARY_NAME`, `ONLEIHARR_LIBRARY_ID`
- `ONLEIHARR_AUTH_TYPE`, `ONLEIHARR_SESSION_PATH`
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

### One-off downloads

`onleiharr download URL_OR_PRODUCT_ID` requires `acsmdownloader`, an activated ADEPT directory, and a
writable download directory. Existing lendings are reused; newly created lendings are returned after
the download. Series can be selected interactively, while scripts should pass an individual product ID.

> [!WARNING]
> An early return ends the right to use the downloaded copy. Onleiharr does not delete local files;
> you are responsible for deleting returned media as required by the Onleihe terms of use.

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

## Docker
Build the image without adding local configuration or session files to the image:

```sh
docker build -t onleiharr .
docker run --rm \
  -v "$HOME/.config/onleiharr:/config" \
  onleiharr
```

The container reads `/config/onleiharr.toml`. Keep `session.json` in the same
mounted directory when using external OpenID authentication. The configuration
directory is mounted at runtime and is deliberately excluded from the Docker
build context and image layers.

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
