from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]


DEFAULT_FILENAME = "onleiharr.toml"


@dataclass
class Credentials:
    username: str
    password: str
    host: str
    onleihe_name: str | None
    library_name: str | None
    onleihe_id: str | None
    library_id: str | None


@dataclass
class WatchCategory:
    category_ids: list[str]
    keywords: list[str]
    category_urls: list[str] = field(default_factory=list)
    description: str | None = None
    media_types: list[str] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    sort_field: str = "publicationDate"
    sort_order: str = "DESC"


@dataclass
class NotificationConfig:
    urls: list[str]
    apprise_config_path: Path | None
    test_notification: bool
    email: str | None


@dataclass
class GourouConfig:
    bin_dir: Path | None
    adept_dir: Path | None
    download_dir: Path | None
    download_permissions: int | None
    timeout_secs: float
    remove_drm: bool
    remove_drm_ack: str | None
    lendings_poll_interval_secs: float
    lendings_download_keywords_only: bool
    lendings_notify: bool


@dataclass
class GeneralConfig:
    poll_interval_secs: float
    watch_product_ids: list[str]
    watch_categories: list[WatchCategory]


@dataclass
class AppConfig:
    general: GeneralConfig
    notification: NotificationConfig
    credentials: Credentials
    gourou: GourouConfig
    config_path: Path


class ConfigError(Exception):
    """Raised when configuration loading fails."""


TOP_LEVEL_KEYS = {"general", "watch_categories", "notification", "credentials", "gourou"}
SECTION_KEYS = {
    "general": {"poll_interval_secs", "watch_product_ids"},
    "notification": {"urls", "apprise_config_path", "test_notification", "email"},
    "credentials": {
        "username",
        "password",
        "host",
        "onleihe_name",
        "library_name",
        "onleihe_id",
        "library_id",
    },
    "gourou": {
        "bin_dir",
        "adept_dir",
        "download_dir",
        "download_permissions",
        "timeout_secs",
        "remove_drm",
        "remove_drm_ack",
        "lendings_poll_interval_secs",
        "lendings_download_keywords_only",
        "lendings_notify",
    },
}
WATCH_CATEGORY_KEYS = {
    "description",
    "category_ids",
    "category_urls",
    "keywords",
    "media_types",
    "filters",
    "sort_field",
}


def default_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "onleiharr" / DEFAULT_FILENAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "onleiharr" / DEFAULT_FILENAME
    user_path = Path.home() / ".config" / "onleiharr" / DEFAULT_FILENAME
    system_path = Path("/etc/onleiharr") / DEFAULT_FILENAME
    if user_path.exists():
        return user_path
    if system_path.exists():
        return system_path
    return user_path


def ensure_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(_default_template(), encoding="utf-8")


def _default_template() -> str:
    return """# onleiharr configuration

[general]
poll_interval_secs = 300.0
watch_product_ids = [
  "69b3ed6bc56755bf97cb3b9a", # example: magazine/product series
]

[[watch_categories]]
description = "Sachbuch & Ratgeber"
category_ids = [
  "65afa17e40246d5939bdbb53",
  "65afa17e40246d5939bdbb54",
]
category_urls = [
  # "https://niedersachsen.onleihe.de/search?categories=%5B%2265afa17e40246d5939bdbb53%22%5D",
]
keywords = [
  "keyword fragment one",
  "keyword fragment two",
]

[notification]
# urls = [
#   "tgram://{bot_token}/{chat_id}/?format=html",
#   "pover://{user_key}@{app_token}/?format=html&priority=-1",
# ]
# apprise_config_path = "apprise.yml"
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
# lendings_poll_interval_secs = 21600.0
# lendings_download_keywords_only = true
# lendings_notify = true

[credentials]
host = "niedersachsen.onleihe.de"
onleihe_name = "Onleihe Niedersachsen"
library_name = "Stadtbibliothek Achim"
# onleihe_id = ""
# library_id = ""
username = "your-username"
password = "your-password"
"""


def load_config(path: Path, env: os._Environ[str] | None = None) -> AppConfig:
    environ = env if env is not None else os.environ
    data = _read_toml(path)
    _validate_config_shape(data)

    general_section = _section(data, "general")
    notification_section = _section(data, "notification")
    credentials_section = _section(data, "credentials")
    gourou_section = _section(data, "gourou")

    poll_interval_env = _env_float(environ.get("ONLEIHARR_POLL_INTERVAL"))
    poll_interval = (
        poll_interval_env
        if poll_interval_env is not None
        else float(general_section.get("poll_interval_secs", 300.0))
    )

    watch_product_ids = _dedupe(_env_list(environ.get("ONLEIHARR_WATCH_PRODUCT_IDS"))
                                or _string_list(general_section.get("watch_product_ids")))
    watch_categories = _load_watch_categories(data.get("watch_categories", []))
    if not watch_product_ids and not watch_categories:
        raise ConfigError("No watch targets configured. Set [general].watch_product_ids or [[watch_categories]].")

    apprise_urls = _env_list(environ.get("ONLEIHARR_APPRISE_URLS")) or _string_list(
        notification_section.get("urls")
    )
    apprise_value = environ.get("ONLEIHARR_APPRISE_CONFIG") or notification_section.get("apprise_config_path")
    apprise_path = _resolve_optional_path_value(str(apprise_value), base=path.parent) if apprise_value else None
    email = environ.get("ONLEIHARR_EMAIL") or notification_section.get("email") or None
    if not apprise_urls and apprise_path is None:
        raise ConfigError(
            "No apprise URLs configured. Set [notification].urls or ONLEIHARR_APPRISE_URLS, "
            "or provide apprise_config_path."
        )

    test_notification_env = environ.get("ONLEIHARR_TEST_NOTIFICATION")
    test_notification = (
        _env_bool(test_notification_env)
        if test_notification_env is not None
        else bool(notification_section.get("test_notification", False))
    )

    credentials = _load_credentials(credentials_section, environ)
    gourou = _load_gourou(gourou_section, environ, base=path.parent)

    return AppConfig(
        general=GeneralConfig(
            poll_interval_secs=float(poll_interval),
            watch_product_ids=watch_product_ids,
            watch_categories=watch_categories,
        ),
        notification=NotificationConfig(
            urls=apprise_urls,
            apprise_config_path=apprise_path,
            test_notification=test_notification,
            email=str(email).strip() if email else None,
        ),
        credentials=credentials,
        gourou=gourou,
        config_path=path,
    )


def _load_credentials(section: dict[str, Any], environ: os._Environ[str]) -> Credentials:
    username = environ.get("ONLEIHARR_USERNAME") or section.get("username")
    password = environ.get("ONLEIHARR_PASSWORD") or section.get("password")
    host = environ.get("ONLEIHARR_HOST") or section.get("host")
    onleihe_name = environ.get("ONLEIHARR_ONLEIHE_NAME") or section.get("onleihe_name")
    library_name = environ.get("ONLEIHARR_LIBRARY_NAME") or section.get("library_name")
    onleihe_id = environ.get("ONLEIHARR_ONLEIHE_ID") or section.get("onleihe_id")
    library_id = environ.get("ONLEIHARR_LIBRARY_ID") or section.get("library_id")

    if not username or not password or not host:
        raise ConfigError("Credentials incomplete. Set username/password/host.")
    if not (onleihe_id or onleihe_name):
        raise ConfigError("Credentials incomplete. Set onleihe_name or onleihe_id.")
    if not (library_id or library_name):
        raise ConfigError("Credentials incomplete. Set library_name or library_id.")

    return Credentials(
        username=str(username),
        password=str(password),
        host=str(host),
        onleihe_name=_optional_str(onleihe_name),
        library_name=_optional_str(library_name),
        onleihe_id=_optional_str(onleihe_id),
        library_id=_optional_str(library_id),
    )


def _load_watch_categories(raw_categories: Any) -> list[WatchCategory]:
    if raw_categories is None:
        return []
    if not isinstance(raw_categories, list):
        raise ConfigError("[[watch_categories]] must be an array of tables.")

    categories: list[WatchCategory] = []
    for index, raw in enumerate(raw_categories, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"watch_categories entry {index} must be a table.")
        category_ids = _string_list(raw.get("category_ids"))
        category_urls = _string_list(raw.get("category_urls"))
        extracted_ids: list[str] = []
        media_types = _string_list(raw.get("media_types"))
        filters = _dict_list(raw.get("filters"))
        sort_field = _optional_str(raw.get("sort_field")) or "publicationDate"
        for url in category_urls:
            extracted_ids.extend(extract_category_ids_from_url(url))
            options = extract_category_options_from_url(url)
            media_types.extend(options.media_types)
            filters.extend(options.filters)
            if options.sort_field:
                sort_field = options.sort_field
        merged_ids = _dedupe([*category_ids, *extracted_ids])
        if not merged_ids:
            raise ConfigError(
                f"watch_categories entry {index} needs category_ids or category_urls with categories=."
            )
        keywords = _string_list(raw.get("keywords"))
        if not keywords:
            raise ConfigError(f"watch_categories entry {index} needs at least one keyword.")
        categories.append(
            WatchCategory(
                category_ids=merged_ids,
                category_urls=category_urls,
                keywords=keywords,
                description=_optional_str(raw.get("description")),
                media_types=_dedupe(media_types),
                filters=_dedupe_filters(filters),
                sort_field=sort_field,
                sort_order="DESC",
            )
        )
    return categories


def _validate_config_shape(data: dict[str, Any]) -> None:
    unknown_top_level = sorted(set(data) - TOP_LEVEL_KEYS)
    if unknown_top_level:
        if "watch_product_ids" in unknown_top_level:
            raise ConfigError("watch_product_ids must be configured under [general].")
        raise ConfigError(f"Unknown top-level config keys: {', '.join(unknown_top_level)}")

    for section_name, allowed_keys in SECTION_KEYS.items():
        section = data.get(section_name)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ConfigError(f"[{section_name}] must be a table.")
        unknown_keys = sorted(set(section) - allowed_keys)
        if unknown_keys:
            raise ConfigError(f"Unknown [{section_name}] config keys: {', '.join(unknown_keys)}")

    raw_categories = data.get("watch_categories")
    if raw_categories is None:
        return
    if not isinstance(raw_categories, list):
        raise ConfigError("[[watch_categories]] must be an array of tables.")
    for index, category in enumerate(raw_categories, start=1):
        if not isinstance(category, dict):
            raise ConfigError(f"watch_categories entry {index} must be a table.")
        unknown_keys = sorted(set(category) - WATCH_CATEGORY_KEYS)
        if unknown_keys:
            if "watch_product_ids" in unknown_keys:
                raise ConfigError(
                    f"watch_product_ids is inside watch_categories entry {index}; "
                    "move it to [general]."
                )
            raise ConfigError(
                f"Unknown watch_categories entry {index} config keys: {', '.join(unknown_keys)}"
            )


@dataclass(frozen=True)
class CategoryUrlOptions:
    media_types: list[str]
    filters: list[dict[str, Any]]
    sort_field: str | None
    sort_order: str


FILTER_QUERY_FIELDS = {
    "authors_fullName",
    "language",
    "licence.isAvailable",
    "mediaType",
    "publisher.name",
    "rating.value",
    "themaCategories.id",
}

FILTER_QUERY_ALIASES = {
    "author": "authors_fullName",
    "authors": "authors_fullName",
    "available": "licence.isAvailable",
    "isAvailable": "licence.isAvailable",
    "languages": "language",
    "publisher": "publisher.name",
    "rating": "rating.value",
    "themaCategory": "themaCategories.id",
    "themaCategories": "themaCategories.id",
}


def extract_category_ids_from_url(url: str) -> list[str]:
    query = parse_qs(urlparse(url).query)
    values = query.get("categories")
    if not values:
        return []
    ids: list[str] = []
    for value in values:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid categories= JSON in category URL: {url}") from exc
        if not isinstance(parsed, list):
            raise ConfigError(f"Invalid categories= value in category URL: {url}")
        ids.extend(str(item) for item in parsed if str(item).strip())
    return ids


def extract_category_options_from_url(url: str) -> CategoryUrlOptions:
    query = parse_qs(urlparse(url).query)
    media_types = [value for value in query.get("mediaType", []) if value]
    sort_field = (query.get("sortField") or [None])[0]
    filters = []
    for key, values in query.items():
        field = FILTER_QUERY_ALIASES.get(key, key)
        if key in {"categories", "sortField", "sortType"}:
            continue
        if field not in FILTER_QUERY_FIELDS:
            continue
        clean_values = [value for value in values if value]
        if clean_values:
            filters.append({"field": field, "values": clean_values})
    return CategoryUrlOptions(
        media_types=media_types,
        filters=filters,
        sort_field=sort_field,
        sort_order="DESC",
    )


def _load_gourou(section: dict[str, Any], environ: os._Environ[str], *, base: Path) -> GourouConfig:
    timeout = _env_float(environ.get("ONLEIHARR_GOUROU_TIMEOUT"))
    remove_drm_env = environ.get("ONLEIHARR_GOUROU_REMOVE_DRM")
    lendings_poll_interval_env = _env_float(environ.get("ONLEIHARR_GOUROU_LENDINGS_POLL_INTERVAL"))
    lendings_keywords_only_env = environ.get("ONLEIHARR_GOUROU_LENDINGS_DOWNLOAD_KEYWORDS_ONLY")
    lendings_notify_env = environ.get("ONLEIHARR_GOUROU_LENDINGS_NOTIFY")

    return GourouConfig(
        bin_dir=_resolve_optional_path_value(
            environ.get("ONLEIHARR_GOUROU_BIN_DIR") or section.get("bin_dir"),
            base=base,
        ),
        adept_dir=_resolve_optional_path_value(
            environ.get("ONLEIHARR_GOUROU_ADEPT_DIR") or section.get("adept_dir"),
            base=base,
        ),
        download_dir=_resolve_optional_path_value(
            environ.get("ONLEIHARR_GOUROU_DOWNLOAD_DIR") or section.get("download_dir"),
            base=base,
        ),
        download_permissions=_parse_permissions(
            environ.get("ONLEIHARR_GOUROU_DOWNLOAD_PERMISSIONS") or section.get("download_permissions")
        ),
        timeout_secs=float(timeout if timeout is not None else section.get("timeout_secs", 30.0)),
        remove_drm=(
            _env_bool(remove_drm_env)
            if remove_drm_env is not None
            else bool(section.get("remove_drm", False))
        ),
        remove_drm_ack=_optional_str(environ.get("ONLEIHARR_GOUROU_ACK_DRM") or section.get("remove_drm_ack")),
        lendings_poll_interval_secs=float(
            lendings_poll_interval_env
            if lendings_poll_interval_env is not None
            else section.get("lendings_poll_interval_secs", 21600.0)
        ),
        lendings_download_keywords_only=(
            _env_bool(lendings_keywords_only_env)
            if lendings_keywords_only_env is not None
            else bool(section.get("lendings_download_keywords_only", True))
        ),
        lendings_notify=(
            _env_bool(lendings_notify_env)
            if lendings_notify_env is not None
            else bool(section.get("lendings_notify", True))
        ),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise ConfigError("tomllib not available; use Python 3.11+ or install tomli.")
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in config file {path}: {exc}") from exc


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table.")
    return value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("Expected a list of strings.")
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("Expected a list of tables.")
    return [dict(item) for item in value if isinstance(item, dict)]


def _env_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_field: dict[str, list[str]] = {}
    for item in filters:
        field = _optional_str(item.get("field"))
        values = _string_list(item.get("values"))
        if not field or not values:
            continue
        existing = by_field.setdefault(field, [])
        for value in values:
            if value not in existing:
                existing.append(value)
    return [{"field": field, "values": values} for field, values in by_field.items()]


def _parse_permissions(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigError("Invalid download_permissions value; expected octal string like 0644.")
    if isinstance(value, int):
        if value <= 0o777:
            return value
        if value <= 777:
            return int(str(value), 8)
        raise ConfigError("Invalid download_permissions value; use 0644/0o644 or an integer <= 0o777.")
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return int(raw, 8)
    except ValueError as exc:
        raise ConfigError("Invalid download_permissions value; use octal like 0644 or 0o644.") from exc


def _resolve_optional_path_value(value: str | Path | None, base: Path) -> Path | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else (base / candidate)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    return raw or None
