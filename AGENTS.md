AGENTS GUIDE FOR THIS REPO

Scope
- Audience: agentic coding agents working in this repo.
- Goal: ship changes safely with minimal surprises.
- Language: Python 3.x; packaged CLI watcher.

Repository Map
- onleiharr/cli.py: argparse CLI, logging, main loop, notifications, auto-rent logic.
- onleiharr/config.py: TOML config loading, env/CLI precedence, default config generation.
- onleiharr/models.py: Media/Book/Magazine dataclasses.
- onleiharr/parser.py: HTML parsing + fetch_media.
- onleiharr/onleihe.py: session handling, login/rent/reserve flows with retry decorator.
- pyproject.toml: packaging metadata, console entrypoint `onleiharr`.
- requirements.txt: runtime deps (apprise, beautifulsoup4, requests).
- onleiharr.toml: user runtime config (auto-created if missing; not shipped in repo).
- apprise.yml: legacy Apprise config (optional; prefer TOML URLs).
- images/: docs image.
- No tests directory today.


Environment Assumptions
- Linux, Python available; install deps via pip.
- Network access required for onleihe and notifications.
- Config files may contain secrets; do not commit.

Build / Run / Lint / Test Commands
- Install deps: `pipx install onleiharr` (recommended) or `pip install -r requirements.txt` for source runs.
- Run watcher: `onleiharr` (uses default config path) or `onleiharr -c path/to/onleiharr.toml`.
- Shim: `python main.py` (source runs).
- Quick smoke: `python -m py_compile onleiharr/*.py main.py`.
- Lint (optional): `pip install ruff` then `ruff check .`.
- Format (optional): `pip install black` then `black onleiharr *.py`.
- Type check (optional): `pip install mypy` then `mypy onleiharr` (add stubs for requests/bs4 if needed).
- Single pytest pattern (if you add pytest): `pytest tests/test_file.py -k name_substring`.
- Docker build: `docker build -t onleiharr .`.
- Docker run example:
  `docker run -it --rm --name onleiharr \
   -v $(pwd)/onleiharr.toml:/app/onleiharr.toml \
   onleiharr`.

Cursor / Copilot Rules
- No .cursor/rules or .cursorrules present.
- No .github/copilot-instructions.md present.
- If added later, surface them in this guide.

Configuration Expectations
- `onleiharr.toml` must define:
  - [general]: poll_interval_secs, urls (list), keywords (list).
  - [notification]: urls (list of Apprise URLs), test_notification, email (optional). Legacy: apprise_config_path.
  - [credentials]: username, password, library, library_id.
- Env overrides: `ONLEIHARR_URLS` (comma list), `ONLEIHARR_USERNAME`, `ONLEIHARR_PASSWORD`, `ONLEIHARR_LIBRARY`, `ONLEIHARR_LIBRARY_ID`, `ONLEIHARR_EMAIL`, `ONLEIHARR_APPRISE_URLS`, `ONLEIHARR_APPRISE_CONFIG`, `ONLEIHARR_POLL_INTERVAL`, `ONLEIHARR_TEST_NOTIFICATION`, `ONLEIHARR_KEYWORDS`, `ONLEIHARR_CONFIG`.
- `apprise.yml` is legacy; prefer TOML notification URLs.
- Be cautious with personal data; avoid logging secrets.

Runtime Behaviors to Preserve
- main loop polls all configured URLs; caches known media; sends notifications on new items.
- Auto-rent / reserve triggered only when title matches keyword set.
- Apprise notifications are HTML-rich strings; keep formatting intact.
- Retry decorator in onleihe.py retries login/rent/reserve up to max_retries; preserves default return on exhaustion.
- Book/Magazine equality is id-based; sets rely on __hash__/__eq__.

Coding Style
- Follow PEP8; prefer Black-compatible formatting (88-120 cols ok; current code uses short lines).
- Imports: standard library, third-party, local; one per line; no wildcard imports.
- Types: use typing annotations when editing/adding functions; dataclasses already typed.
- Naming: snake_case for functions/vars; PascalCase for classes; constants in CAPS; config keys mimic ini.
- Strings: use single quotes unless double quotes needed; f-strings for interpolation.
- Prefer explicit is None checks for optionals.
- Avoid mutating globals from inner scopes; current globals in main.py are module-level config/sets.
- Keep I/O at top-level; avoid side effects on import beyond config parsing.

Error Handling
- Network calls: use requests with timeouts; propagate or log meaningful messages.
- Preserve RequestException handling in main loop; avoid swallowing unexpected exceptions silently unless justified.
- When extending retry logic, ensure idempotency and bounded retries; log attempts with context.
- For HTML parsing, guard against missing elements; return defaults or raise with context.
- Do not expose credentials in logs or notifications.

Data Models
- Media dataclass: fields link/title/format/library/available/availability_date; id derived from link.
- Book extends Media with author (stored as _author), description, insert_date; __hash__/__eq__ use id.
- Magazine extends Media; __hash__/__eq__ also id-based.
- Keep __hash__/__eq__ consistent if adding fields; sets/dicts rely on id uniqueness.

Parsing Conventions
- BeautifulSoup HTML parsing assumes specific test-id attributes; keep selectors stable.
- Dates parsed with '%d.%m.%Y'; adjust carefully if site changes.
- Availability parsing: book availability defaults to today if no unavailable marker; magazine parsing uses German labels.

Networking
- All requests currently POST with timeout defaults (10s); specify timeouts on new calls.
- Session in Onleihe persists cookies; reuse for login-dependent actions.
- Respect robots/usage; this is user automation—avoid excessive polling; keep poll_interval_secs sensible.

Notifications
- Apprise URLs come from TOML; legacy apprise.yml remains optional.
- Notify message formatting uses HTML tags; preserve when adjusting strings.
- Test notification sends an immediate notify on first run.

Configuration Safety
- Do not commit onleiharr.toml, apprise.yml, or any credentials.
- Provide example/template changes in *.example files if needed.

Extending Functionality
- When adding CLI args or flags, keep config precedence: CLI > env > TOML.
- For new media types, update parser formatting and notification formatting.
- If adding persistence, isolate storage layer and keep in-memory cache behavior compatible.

Testing Guidance
- No tests exist; prefer pytest structure tests/ for new coverage.
- Suggest unit tests for parsing (parser.py) with sample HTML, and for retry decorator behaviors.
- For networked tests, use VCR/requests-mock; avoid live calls in CI.
- Single test command pattern restated: `pytest tests/test_file.py -k case`.

Style for Contributions
- Keep functions small; extract helpers for clarity.
- Document non-obvious logic with concise docstrings; avoid redundant comments.
- Maintain English logs/messages; keep user-facing text consistent.
- Prefer dependency minimalism; avoid heavy additions unless necessary.

CI/CD
- No CI config present; if adding, include lint + pytest + docker build steps.

Git Hygiene for Agents
- Never commit secrets; check `git status` before commits.
- Avoid force pushes; do not amend unless user asks.
- If adding tests or tooling, update this guide with commands and rules.

Operational Notes
- Monitor for site markup changes; adjust selectors promptly.
- Polling loop is infinite; consider graceful shutdown hooks if extending.
- Ensure time.sleep uses config poll interval; avoid drift when adding work per cycle.
- Use explicit timeouts on any new requests; keep retries bounded and idempotent.
- Handle HTML parsing defensively; None-check before `.text`/attribute access.

Logging / Observability
- Use stdlib logging (configured in CLI); avoid prints.
- Include URL/context in network error logs; avoid leaking credentials or full responses.
- Keep notification bodies HTML-friendly; avoid accidental escaping/encoding changes.

Dependency Management
- Keep requirements lean; avoid heavy deps unless justified.
- If adding dev-only tools, prefer requirements-dev.txt and document commands here.
- Pin versions only when needed for stability; test upgrades locally before raising minimums.

Local Dev Tips
- Use a virtualenv/venv; Python 3.x expected (3.10+ recommended).
- `python -m py_compile onleiharr/*.py main.py` is the fastest smoke check.
- For manual runs, ensure onleiharr.toml exists; defaults are auto-created if missing.
- If adding tests, place them under `tests/` and use `pytest tests/test_file.py -k name_substring` for one test.

Safety With Secrets
- Treat onleiharr.toml and apprise.yml as sensitive; never commit.
- Mask usernames/passwords in logs and PRs; redact library ids if unsure.
- Validate user-provided paths before opening files; fail loud with context, not contents.

Documentation Updates
- Keep this AGENTS.md in sync with tooling changes.
- Add Cursor/Copilot rules here if files appear.
- Record new commands for lint/test/build when introduced.
- Update README if runtime/config expectations change alongside code updates.
