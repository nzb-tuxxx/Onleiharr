# Onleiharr

Onleiharr lets you automatically download your favorite media like magazines and books from Onleihe to read with any PDF/EPUB reader on Linux.

- Monitor Onleihe URLs and notify on new media.
- Auto-rent or reserve based on keyword filters.
- Optional auto-downloads via libgourou (including delayed downloads via "Mein Konto") and DRM removal with explicit acknowledgment.
- Send media directly to a Kindle via Apprise email/SMTP.

## Installation (recommended: pipx)
Requirements: Python 3.10+.

Install pipx:
- Debian/Ubuntu: `sudo apt install pipx` (or `python3-pipx`) then `pipx ensurepath`
- Fedora/RHEL/CentOS: `sudo dnf install pipx` then `pipx ensurepath`
- Arch/Manjaro: `sudo pacman -S python-pipx` then `pipx ensurepath`

Install/Upgrade onleiharr:
- `pipx install onleiharr`
- `pipx upgrade onleiharr`
- Verify: `onleiharr --version`

## Installation (alternative: from source)
- Clone the repo and install deps: `pip install -r requirements.txt`
- Run directly: `python3 main.py` (auto-creates config on first run)
- Or run as module: `python3 -m onleiharr`

## Installation (Arch Linux AUR)
Onleiharr is also available as an AUR package:
- https://aur.archlinux.org/packages/onleiharr
- Install with `yay`: `yay -S onleiharr`
- Install with `paru`: `paru -S onleiharr`

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
poll_interval_secs = 300.0
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
# bin_dir = "~/bin"
# adept_dir = "~/.config/adept"
# download_dir = "~/Downloads/Onleiharr"
# download_permissions = "0644"
# timeout_secs = 30.0
# remove_drm = false
# remove_drm_ack = "I_UNDERSTAND"
# Scan "Mein Konto -> Ausgeliehen" for ACSM links (useful for fulfilled reservations)
# lendings_poll_interval_secs = 21600.0  # 6h; set to 0 to disable
# lendings_download_keywords_only = true
# lendings_notify = true

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
- Keep the list sorted by publication date in descending order, otherwise new media cannot be detected reliably.
- For readability, add an inline comment per URL (as shown in the example).

### Environment overrides (optional)
- `ONLEIHARR_CONFIG` (config path)
- `ONLEIHARR_URLS` (comma-separated list)
- `ONLEIHARR_USERNAME`, `ONLEIHARR_PASSWORD`, `ONLEIHARR_LIBRARY`, `ONLEIHARR_LIBRARY_ID`
- `ONLEIHARR_EMAIL`, `ONLEIHARR_APPRISE_URLS`, `ONLEIHARR_APPRISE_CONFIG`, `ONLEIHARR_POLL_INTERVAL`, `ONLEIHARR_TEST_NOTIFICATION`, `ONLEIHARR_KEYWORDS`
- `ONLEIHARR_GOUROU_BIN_DIR`, `ONLEIHARR_GOUROU_ADEPT_DIR`, `ONLEIHARR_GOUROU_DOWNLOAD_DIR`, `ONLEIHARR_GOUROU_DOWNLOAD_PERMISSIONS`, `ONLEIHARR_GOUROU_TIMEOUT`, `ONLEIHARR_GOUROU_REMOVE_DRM`, `ONLEIHARR_GOUROU_ACK_DRM`
- `ONLEIHARR_GOUROU_LENDINGS_POLL_INTERVAL`, `ONLEIHARR_GOUROU_LENDINGS_DOWNLOAD_KEYWORDS_ONLY`, `ONLEIHARR_GOUROU_LENDINGS_NOTIFY`

## libgourou setup (optional)
libgourou is only needed for automatic downloads and optional DRM removal. Onleiharr can still notify and auto-rent without it.

### Download fulfilled reservations (MyBib / "Mein Konto")
Onleihe may only expose the ACSM download link once a reservation is fulfilled and the loan shows up under
"Mein Konto -> Ausgeliehen". Onleiharr can periodically scan that page and download newly borrowed items via libgourou.

Config options (in `[gourou]`):
- `lendings_poll_interval_secs` (default: `21600.0`; set to `0` to disable)
- `lendings_download_keywords_only` (default: `true`)
- `lendings_notify` (default: `true`, sends a dedicated notification when a MyBib download happens, incl. attachment if available)

Notes:
- Requires libgourou (`acsmdownloader`) to be available; otherwise this feature is disabled.
- This is in-memory only. On startup, Onleiharr primes the MyBib cache and will not download existing loans.
- Only entries that expose an ACSM download link are treated as "handled" (reservations without ACSM do not block later downloads).
- The MyBib scan runs inside the main polling loop, so it will not execute more often than `poll_interval_secs`.
- Defaults are conservative: by default only items matching your `keywords` are downloaded from MyBib.

Recommended (AppImage):
1) Download the latest release from https://forge.soutade.fr/soutade/libgourou/releases
   Note: AppImage builds are for x86_64 only; other architectures (e.g., Raspberry Pi) should build from source following the vendor instructions.
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
![Telegram Notification](https://raw.githubusercontent.com/nzb-tuxxx/Onleiharr/main/images/onleiharr_telegram.jpg)
- Preferred: set `[notification].urls` (Telegram, Discord, Slack, etc.).
- Legacy: `apprise.yml` is still supported via `[notification].apprise_config_path`.

#### Send media via Apprise
Media is sent via Apprise's attachment support and only works when the provider reports attachment capability.
Common providers that support media include: Telegram, Discord, Slack, Gotify, and Email/SMTP.
Email/SMTP media may be subject to provider size limits.
If media attachment is missing, first enable `--log-level DEBUG` and check the logs to confirm whether
Apprise reports attachment support for your provider before troubleshooting further.

### Kindle delivery via SMTP (Apprise)
You can route downloaded media directly to your Kindle by sending email via SMTP through Apprise.
Use a mailto-style Apprise URL and set the recipient to your Kindle address.

Example (replace placeholders with real values):
```toml
[notification]
urls = [
  "mailto://smtp-user:smtp-password@smtp.example.com:587?to=user@kindle.com&from=you@example.com&format=html"
]
```

Notes:
- Add your sender address (the `from=` value) to your Amazon "Approved Personal Document E-mail List".
- Many providers require an app-specific password for SMTP.
- If your SMTP server needs SSL/TLS on 465, use `mailtos://` instead of `mailto://`.

## Systemd (user mode)
- Install user unit: `onleiharr --install-as-user-systemd`
- Reload and enable: `systemctl --user daemon-reload` then `systemctl --user enable --now onleiharr`
- Logs: `journalctl --user -u onleiharr -f`
- If user systemd is inactive: `loginctl enable-linger $USER`

## Common flags
- `--log-level DEBUG` for verbose logging (also enables libgourou `-v`)
- `--once` for a single poll iteration
- `--interval 30` to override poll interval
- `--test-notification` to send an immediate test notify on first run

## Troubleshooting
- No apprise URLs configured -> add `[notification].urls` or set `ONLEIHARR_APPRISE_URLS`
- User systemd not active -> run `loginctl enable-linger $USER`, then reload/enable the unit
- PATH issues with pipx -> run `pipx ensurepath` and open a new shell
- If something behaves unexpectedly, reproduce with `--log-level DEBUG` and open an issue with the (redacted) logs.
  Debug logs may contain sensitive data (e.g. ACSM URLs, tokens, library/user info) - only publish a censored version.
  If you cannot share logs publicly, you can also send the redacted debug log to the maintainer via email (ask in the issue).

## Runtime behavior
- Polls configured URLs, caches known media, sends notifications on new items.
- Auto-rent/reserve triggers when title matches keywords.
- Notifications are HTML formatted via Apprise.

## License
- MIT
