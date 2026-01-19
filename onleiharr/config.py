from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore


DEFAULT_FILENAME = "onleiharr.toml"


@dataclass
class Credentials:
    username: str
    password: str
    library: str
    library_id: int


@dataclass
class NotificationConfig:
    urls: List[str]
    apprise_config_path: Path | None
    test_notification: bool
    email: str | None


@dataclass
class GeneralConfig:
    poll_interval_secs: float
    urls: List[str]
    keywords: List[str]


@dataclass
class AppConfig:
    general: GeneralConfig
    notification: NotificationConfig
    credentials: Credentials
    config_path: Path


class ConfigError(Exception):
    """Raised when configuration loading fails."""


def default_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "onleiharr" / DEFAULT_FILENAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "onleiharr" / DEFAULT_FILENAME
    return Path.home() / ".config" / "onleiharr" / DEFAULT_FILENAME


def ensure_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    template = _default_template()
    path.write_text(template, encoding="utf-8")


def _default_template() -> str:
    return """# onleiharr configuration
# Fill in your credentials and adjust URLs before running.

[general]
poll_interval_secs = 60.0
urls = [
  "https://www.onleihe.de/meinlibrary/frontend/mediaList,0-0-0-0-0-0-0-0-0-0-0.html",
]
keywords = [
  "keyword fragment one",
  "keyword fragment two",
]

[notification]
# apprise URLs; remove comments and fill placeholders
# urls = [
#   "tgram://{bot_token}/{chat_id}/?format=html",
#   "pover://{user_key}@{app_token}/?format=html&priority=-1",
# ]
# Legacy file-based config (optional)
# apprise_config_path = "apprise.yml"

test_notification = false
email = ""

[credentials]
username = "your-username"
password = "your-password"
library = "your-library"
library_id = 0
"""


def load_config(path: Path, env: os._Environ[str] | None = None) -> AppConfig:
    environ = env if env is not None else os.environ
    data = _read_toml(path)

    general_section = data.get("general", {})
    notification_section = data.get("notification", {})
    credentials_section = data.get("credentials", {})

    urls = _env_list(environ.get("ONLEIHARR_URLS")) or general_section.get("urls") or []
    if not urls:
        raise ConfigError("No URLs configured. Set [general].urls or ONLEIHARR_URLS.")

    poll_interval = _env_float(environ.get("ONLEIHARR_POLL_INTERVAL")) or general_section.get(
        "poll_interval_secs", 60.0
    )

    keywords = _env_list(environ.get("ONLEIHARR_KEYWORDS")) or general_section.get("keywords") or []
    if not keywords:
        raise ConfigError("No keywords configured. Set [general].keywords or ONLEIHARR_KEYWORDS.")

    apprise_urls = _env_list(environ.get("ONLEIHARR_APPRISE_URLS")) or notification_section.get("urls") or []
    apprise_value = environ.get("ONLEIHARR_APPRISE_CONFIG") or notification_section.get("apprise_config_path")
    apprise_path = _resolve_optional_path(apprise_value, base=path.parent) if apprise_value else None
    email = environ.get("ONLEIHARR_EMAIL") or notification_section.get("email") or None

    if not apprise_urls and apprise_path is None:
        raise ConfigError(
            "No apprise URLs configured. Set [notification].urls or ONLEIHARR_APPRISE_URLS, or provide apprise_config_path."
        )

    test_notification_env = environ.get("ONLEIHARR_TEST_NOTIFICATION")
    test_notification = (
        _env_bool(test_notification_env)
        if test_notification_env is not None
        else bool(notification_section.get("test_notification", False))
    )

    username = environ.get("ONLEIHARR_USERNAME") or credentials_section.get("username")
    password = environ.get("ONLEIHARR_PASSWORD") or credentials_section.get("password")
    library = environ.get("ONLEIHARR_LIBRARY") or credentials_section.get("library")
    library_id_env = environ.get("ONLEIHARR_LIBRARY_ID")
    library_id = int(library_id_env) if library_id_env is not None else int(credentials_section.get("library_id", 0))

    if not username or not password or not library or library_id == 0:
        raise ConfigError("Credentials incomplete. Set username/password/library/library_id.")

    general = GeneralConfig(
        poll_interval_secs=float(poll_interval),
        urls=list(urls),
        keywords=list(keywords),
    )

    notification = NotificationConfig(
        urls=list(apprise_urls),
        apprise_config_path=apprise_path,
        test_notification=test_notification,
        email=email if email else None,
    )

    credentials = Credentials(
        username=username,
        password=password,
        library=library,
        library_id=library_id,
    )

    return AppConfig(
        general=general,
        notification=notification,
        credentials=credentials,
        config_path=path,
    )


def _read_toml(path: Path) -> dict:
    if tomllib is None:
        raise ConfigError("tomllib not available; use Python 3.11+ or install tomli.")
    try:
        with path.open('rb') as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc


def _env_list(raw: str | None) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(',') if item.strip()]


def _env_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid float value: {raw}") from exc


def _env_bool(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.lower() in {"1", "true", "yes", "on"}


def _resolve_path(value: str | None, base: Path) -> Path:
    if value is None:
        raise ConfigError("Path value is missing in configuration")
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate)


def _resolve_optional_path(value: str | None, base: Path) -> Path:
    if value is None:
        raise ConfigError("Apprise config path is missing in configuration")
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate)
