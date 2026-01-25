# Onleiharr

![Telegram Notification](images/onleiharr_telegram.jpg)

## Overview
Onleiharr monitors specific Onleihe URLs, sends notifications for new media, and can auto-rent or reserve based on keyword filters.

## Installation (recommended: pipx)
- System requirements: Python 3.10+.
- Debian/Ubuntu: `sudo apt install pipx` (or `python3-pipx`) then `pipx ensurepath`
- Fedora/RHEL/CentOS: `sudo dnf install pipx` then `pipx ensurepath`
- Arch/Manjaro: `sudo pacman -S python-pipx` then `pipx ensurepath`
- Install onleiharr: `pipx install onleiharr`
- Verify: `onleiharr --version`

## Installation (alternative: from source)
- Clone the repo and install deps: `pip install -r requirements.txt`
- Run directly: `python3 main.py` (auto-creates config on first run)
- Or run as module: `python3 -m onleiharr`

## Quick start
1) Create/edit config: run once to auto-create a template if missing:
   `onleiharr --once`
   The default path is OS-specific (see below). Edit the created file with your credentials/URLs.
2) Run once to test: `onleiharr --once`
3) Continuous mode: `onleiharr`

## Configuration (TOML)
- Default name: `onleiharr.toml`.
- Search order: CLI `-c/--config` > env `ONLEIHARR_CONFIG` > OS default path
  - Linux: `~/.config/onleiharr/onleiharr.toml`
  - macOS: `~/Library/Application Support/onleiharr/onleiharr.toml`
  - Windows: `%APPDATA%\onleiharr\onleiharr.toml`
- If missing, the app creates a template at the resolved path and exits so you can fill credentials first.

### Example onleiharr.toml
```toml
[general]
poll_interval_secs = 60.0
urls = [
  "https://www.onleihe.de/nbib24/frontend/versionInfoList,0-0-0-109-0-0-0-2008-400005-812926447-0.html", # ct magazine
  "https://www.onleihe.de/nbib24/frontend/simpleMediaList,0-0-0-109-0-0-0-0-0-1957099581-0.html", # finanzen magazine
]
keywords = [
  "c´t",
  "finanzen",
]

[notification]
# urls = [
#   "tgram://{bot_token}/{chat_id}/?format=html",
#   "pover://{user_key}@{app_token}/?format=html&priority=-1",
# ]
# apprise_config_path = "apprise.yml" # legacy file-based config

test_notification = false
email = ""

[gourou]
# bin_dir = "/usr/local/bin"
# adept_dir = "/home/user/.config/adept"
# download_dir = "/home/user/Downloads/onleihe"
# timeout_secs = 30.0
# verbose = 0
# remove_drm = false
# remove_drm_ack = "I_UNDERSTAND"

[credentials]
username = "your-username"
password = "your-password"
library = "your-library"
library_id = 0
```

How to find `library` and `library_id`
1) First find your consortium/Verbund: https://hilfe.onleihe.de/hilfe-onleihe-de/deine-onleihe-finden/c-3750
2) Open: https://www.onleihe.de/nbib24/frontend/myBib,6465-0-0-100-0-0-0-0-0-0-0.html (replace `nbib24` with your Verbund).
3) Find and select your library.
4) Analyze the resulting URL; it contains both values.
   Example (Achim):
   https://www.onleihe.de/nbib24/frontend/login,0-0-0-800-0-0-0-0-0-0-0.html?libraryId=242
   `library = "nbib24"` and `library_id = 242`

How to get your Onleihe URLs
- In your browser, open the Onleihe section you want to monitor (e.g., magazine list, new releases, etc.).
- Copy the full URL from the address bar and paste it into the `urls` list in `onleiharr.toml`.
- For readability, add an inline comment per URL (as shown in the example).

### Environment overrides (optional)
- `ONLEIHARR_CONFIG` (config path)
- `ONLEIHARR_URLS` (comma-separated list)
- `ONLEIHARR_USERNAME`, `ONLEIHARR_PASSWORD`, `ONLEIHARR_LIBRARY`, `ONLEIHARR_LIBRARY_ID`
- `ONLEIHARR_EMAIL`, `ONLEIHARR_APPRISE_URLS`, `ONLEIHARR_APPRISE_CONFIG`, `ONLEIHARR_POLL_INTERVAL`, `ONLEIHARR_TEST_NOTIFICATION`, `ONLEIHARR_KEYWORDS`
- `ONLEIHARR_GOUROU_BIN_DIR`, `ONLEIHARR_GOUROU_ADEPT_DIR`, `ONLEIHARR_GOUROU_DOWNLOAD_DIR`, `ONLEIHARR_GOUROU_TIMEOUT`, `ONLEIHARR_GOUROU_VERBOSE`, `ONLEIHARR_GOUROU_REMOVE_DRM`, `ONLEIHARR_GOUROU_ACK_DRM`

## libgourou setup (optional)
libgourou is only needed for automatic downloads and optional DRM removal. Onleiharr can still notify and auto-rent without it.

Recommended (AppImage):
1) Download the latest release from https://forge.soutade.fr/soutade/libgourou/releases
2) Grab the AppImage archive, e.g. `libgourou_utils-x.x.x-x86_64.AppImage.tar.gz`
3) Extract it and set `gourou.bin_dir` (or `ONLEIHARR_GOUROU_BIN_DIR`) to the extracted directory containing `acsmdownloader`, `adept_activate`, etc.
4) Before first use, initialize ADEPT once: `adept_activate --anonymous`

Alternative options:
- Build from source following the libgourou project docs.
- Arch Linux: install https://aur.archlinux.org/packages/gourou and you typically do not need to set `gourou.bin_dir`.

## DRM removal (third-party)
DRM removal is disabled by default. To enable it, set both:
- `gourou.remove_drm = true`
- `gourou.remove_drm_ack = "I_UNDERSTAND"` (or `ONLEIHARR_GOUROU_ACK_DRM=I_UNDERSTAND`)

This is not legal advice. You are responsible for verifying whether DRM removal for personal use is lawful in your jurisdiction.
Onleiharr does not include DRM removal code; it only invokes a third-party tool (libgourou) that is not part of Onleiharr.
Onleiharr developers accept no liability for misuse. See `DISCLAIMER.md` for details.

### Notifications (Apprise)
- Preferred: set `[notification].urls` (Telegram, Pushover, etc.).
- Legacy: `apprise.yml` is still supported via `[notification].apprise_config_path`.

## Systemd (user mode)
- Install user unit: `onleiharr --install-as-user-systemd`
- Reload and enable: `systemctl --user daemon-reload` then `systemctl --user enable --now onleiharr`
- Logs: `journalctl --user -u onleiharr -f`
- If user systemd is inactive: `loginctl enable-linger $USER`

## Common flags
- `--log-level DEBUG` for verbose logging
- `--once` for a single poll iteration
- `--interval 30` to override poll interval
- `--test-notification` to send an immediate test notify on first run

## Troubleshooting
- No apprise URLs configured -> add `[notification].urls` or set `ONLEIHARR_APPRISE_URLS`
- User systemd not active -> run `loginctl enable-linger $USER`, then reload/enable the unit
- PATH issues with pipx -> run `pipx ensurepath` and open a new shell

## Runtime behavior
- Polls configured URLs, caches known media, sends notifications on new items.
- Auto-rent/reserve triggers when title matches keywords.
- Notifications are HTML formatted via Apprise.

## License
- MIT
