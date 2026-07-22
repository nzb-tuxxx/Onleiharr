AGENTS GUIDE FOR THIS REPO

Scope
- Audience: agentic coding agents working in this repo.
- Goal: ship changes safely with minimal surprises.
- Language: Python 3.x; packaged CLI watcher and manual downloader.

Repository Map
- onleiharr/cli.py: argparse subcommands, logging, watcher loop, manual downloads, notifications, and lend/reserve logic.
- onleiharr/config.py: TOML config loading, env/CLI precedence, default config generation.
- onleiharr/gourou.py: libgourou command wrappers for ACSM download, ADEPT, and optional DRM removal.
- onleiharr/_vendor/onleihe/: vendored Onleihe v3 API client based on httpx.
- pyproject.toml: hatchling/hatch-vcs packaging metadata, console entrypoint `onleiharr`.
- requirements.txt: runtime deps (apprise, httpx, tomli for Python < 3.11).
- tests/: pytest coverage for config, watcher/download behavior, Gourou readiness, and vendored API-facing behavior.
- onleiharr.toml: user runtime config; may contain secrets; do not commit.
- apprise.yml: legacy Apprise config; optional.

Environment Assumptions
- Linux, Python 3.10+ available; install deps via pip/pipx.
- Network access required for Onleihe API and notifications.
- Config files may contain secrets; do not commit.

Build / Run / Lint / Test Commands
- Install deps: `pipx install onleiharr` or `pip install -r requirements.txt`.
- Run watcher: `onleiharr watch` or `onleiharr -c path/to/onleiharr.toml watch`.
- Manual download: `onleiharr download PRODUCT_ID` or `onleiharr download URL`.
- Bare `onleiharr` remains temporarily compatible as a watcher invocation but is deprecated.
- Source shim: `python main.py`.
- Quick smoke: `python -m py_compile onleiharr/*.py onleiharr/_vendor/onleihe/*.py main.py`.
- Tests: `pytest`.
- Single pytest pattern: `pytest tests/test_file.py -k name_substring`.
- Lint optional: `pip install ruff` then `ruff check .`.
- Format optional: `pip install black` then `black onleiharr tests *.py`.
- Build: `python -m build --no-isolation`.
- Docker build: `docker build -t onleiharr .`.

Configuration Expectations
- `onleiharr.toml` must define:
  - [general]: poll_interval_secs, watch_product_ids (optional list).
  - [[watch_categories]]: category_ids/category_urls plus keywords for each category watch.
  - [notification]: urls list or apprise_config_path optional; test_notification; email optional.
  - [credentials]: host, auth_type, onleihe_name or onleihe_id, and library_name or library_id; UPA also needs username/password, while OpenID uses a persisted session_path.
- Env overrides: `ONLEIHARR_CONFIG`, `ONLEIHARR_USERNAME`, `ONLEIHARR_PASSWORD`, `ONLEIHARR_HOST`, `ONLEIHARR_ONLEIHE_NAME`, `ONLEIHARR_ONLEIHE_ID`, `ONLEIHARR_LIBRARY_NAME`, `ONLEIHARR_LIBRARY_ID`, `ONLEIHARR_AUTH_TYPE`, `ONLEIHARR_SESSION_PATH`, `ONLEIHARR_WATCH_PRODUCT_IDS`, `ONLEIHARR_EMAIL`, `ONLEIHARR_APPRISE_URLS`, `ONLEIHARR_APPRISE_CONFIG`, `ONLEIHARR_POLL_INTERVAL`, `ONLEIHARR_TEST_NOTIFICATION`, and the `ONLEIHARR_GOUROU_*` variables.
- `watch_product_ids` are explicit product/series watches and do not use keyword checks.
- `watch_categories` use the vendored API to resolve category tree element ids to API search queries, then filter by keywords.
- Do not add backward compatibility for old Onleihe v1/v2 frontend URLs unless explicitly requested.

Runtime Behaviors to Preserve
- Main loop polls configured product/category watches, caches known product ids, sends notifications on new items.
- The explicit watcher command is `onleiharr watch`; installed user systemd units must use it.
- Product watches auto-lend/reserve without keyword filtering.
- Category watches auto-lend/reserve only after keyword match.
- Apprise notifications are HTML-rich strings; keep formatting intact.
- Missing notification targets must not crash the watcher; log skipped notifications instead.
- My-media/lendings polling uses the v3 API, not HTML scraping.
- libgourou is optional; without binaries, notification and auto-lend/reserve still work.
- Manual downloads reuse the normal config and UPA/OpenID login/session handling.
- Manual downloads validate `acsmdownloader`, ADEPT activation, and the output directory before login or account mutation.
- Product containers offer an interactive media choice; non-interactive callers must provide an individual product id.
- Existing lendings may be downloaded but must never be returned automatically. Only a lending created by the current download command may be returned.
- A newly created lending requires a verified `lend_id` before ACSM download; controlled failures after lending must still attempt its return.
- Automatic returns never delete the downloaded local file; preserve the user-facing legal warning.

User systemd Installer
- `--install-as-user-systemd` overwrites the existing user unit and writes an explicit `watch` command.
- The installer does not run `systemctl --user daemon-reload`, enable, or restart automatically; keep the printed follow-up commands and deprecation warning accurate.

Vendored API Rules
- The vendored client lives under `onleiharr/_vendor/onleihe`.
- Onleiharr code imports it via `onleiharr._vendor.onleihe`, not top-level `onleihe`.
- If API behavior is missing, adjust the vendored client cleanly; do not add API payload workarounds in watcher logic.
- Keep httpx timeouts/retries bounded and avoid logging tokens or credentials.

Coding Style
- Follow PEP8; prefer Black-compatible formatting.
- Imports: standard library, third-party, local; one per line; no wildcard imports.
- Types: use annotations when editing/adding functions.
- Naming: snake_case for functions/vars; PascalCase for classes; constants in CAPS.
- Strings: use double quotes where existing files do; f-strings for interpolation.
- Prefer explicit `is None` checks for optionals.
- Keep I/O at top-level; avoid side effects on import beyond config parsing.

Error Handling
- Network calls go through the vendored httpx API client.
- Avoid swallowing unexpected exceptions silently; log meaningful context without secrets.
- Mutating API calls (`lend`, `reserve`, returns) must remain bounded and intentional.
- Do not expose usernames, passwords, tokens, ACSM URLs, or full API responses in normal logs.

Testing Guidance
- Prefer mocked tests for API behavior; avoid live calls in default pytest.
- Live private tests must be opt-in and use environment credentials.
- Invasive live tests must require a separate explicit opt-in flag.
- If Onleihe API behavior is unclear, verify it with real live credentials/data instead of guessing payloads, status codes, or error messages.
- Achim defaults are acceptable as non-secret public examples.

Git Hygiene for Agents
- Never commit secrets.
- Check `git status` before commits.
- Avoid force pushes; do not amend unless user asks.
- Do not revert user changes unless explicitly requested.

Documentation Updates
- Keep this guide and README in sync with config, dependency, and command changes.
- Record new commands for lint/test/build when introduced.
