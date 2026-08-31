from __future__ import annotations

import argparse
import html
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

import apprise

from onleiharr._vendor.onleihe import (
    MediaItem,
    OnleiheAPIError,
    OnleiheAuthError,
    OnleiheClient,
    OnleiheNotFoundError,
    ProductDetails,
    SessionState,
)
from onleiharr.auth import (
    default_session_path,
    external_login_browser,
    external_login_manual,
    load_session,
    save_session,
)
from onleiharr.config import (
    AppConfig,
    ConfigError,
    DEFAULT_KEYWORD_MATCH_MODE,
    KEYWORD_MATCH_CONTAINS,
    KEYWORD_MATCH_WORD_START,
    WatchCategory,
    default_config_path,
    load_config,
)
from onleiharr.gourou import GourouClient, GourouError
from onleiharr.wizard import run_first_start_wizard

logger = logging.getLogger(__name__)

DRM_ACK_TOKEN = "I_UNDERSTAND"
PRODUCT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


@dataclass(frozen=True)
class WatchedMedia:
    product_id: str
    title: str
    url: str
    media_type: str | None
    authors: tuple[str, ...]
    subtitle: str | None
    publication_date: str | None
    available: bool
    availability_text: str | None
    acsm_url: str | None
    source: str
    keyword_required: bool
    keyword_matched: bool
    cover_url: str | None = None


@dataclass(frozen=True)
class WatchPollResult:
    media: list[WatchedMedia]
    errors: int = 0
    successful_sources: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CategoryWatchResult:
    media: list[WatchedMedia]
    total: int


class MaintenanceDetectedError(OnleiheAPIError):
    """Raised when an operation fails while Onleihe maintenance is active."""


def load_version() -> str:
    try:
        from importlib.metadata import version as metadata_version

        return metadata_version("onleiharr")
    except Exception:
        return "0.0.0"


def install_user_systemd(config_path: Path) -> None:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise ConfigError("systemctl not found; cannot install user systemd unit")

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "onleiharr.service"

    executable = shutil.which("onleiharr") or "%h/.local/bin/onleiharr"
    unit_content = """[Unit]
Description=Onleiharr (user)
After=network.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
""".format(exec_start=f"{executable} -c {config_path} watch")

    unit_path.write_text(unit_content, encoding="utf-8")

    logger.info("Installed user unit at %s", unit_path)
    try:
        subprocess.run([systemctl, "--user", "is-active", "default.target"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        logger.warning("User systemd may be inactive. Consider: loginctl enable-linger $USER")

    logger.info("Next steps:")
    logger.info("  systemctl --user daemon-reload")
    logger.info("  systemctl --user enable --now onleiharr")
    logger.info("  journalctl --user -u onleiharr -f")


def _add_common_options(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        type=Path,
        default=default,
        help="Path to onleiharr.toml",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=argparse.SUPPRESS if suppress_defaults else "INFO",
        help="Logging verbosity",
    )
    parser.add_argument("--version", action="version", version=load_version())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Onleiharr watcher and auto-renter")
    _add_common_options(parser)
    parser.add_argument(
        "--install-as-user-systemd",
        action="store_true",
        dest="install_user_systemd",
        help="Install a user-mode systemd unit for onleiharr",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        dest="init_config",
        help="Run the first-start configuration wizard",
    )
    parser.add_argument(
        "--no-wizard",
        action="store_true",
        dest="no_wizard",
        help="Do not start the interactive wizard when the config is missing",
    )
    parser.add_argument("--once", action="store_true", help="Run a single poll iteration and exit")
    parser.add_argument(
        "--login",
        action="store_true",
        dest="external_login",
        help="Authorize an external-library login and update its local session",
    )
    parser.add_argument(
        "--login-browser",
        action="store_true",
        help="Use a managed local browser for --login instead of the SSH-friendly manual flow",
    )
    parser.add_argument("--interval", type=float, dest="interval", help="Override poll interval in seconds")
    parser.add_argument(
        "--test-notification",
        action="store_true",
        dest="test_notification",
        help="Force a notification test on startup",
    )

    subparsers = parser.add_subparsers(dest="command")
    watch_parser = subparsers.add_parser("watch", help="Run the Onleiharr watcher")
    _add_common_options(watch_parser, suppress_defaults=True)
    watch_parser.add_argument(
        "--once",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run a single poll iteration and exit",
    )
    watch_parser.add_argument(
        "--interval",
        type=float,
        dest="interval",
        default=argparse.SUPPRESS,
        help="Override poll interval in seconds",
    )
    watch_parser.add_argument(
        "--test-notification",
        action="store_true",
        dest="test_notification",
        default=argparse.SUPPRESS,
        help="Force a notification test on startup",
    )

    download_parser = subparsers.add_parser("download", help="Download one Onleihe medium")
    _add_common_options(download_parser, suppress_defaults=True)
    download_parser.add_argument("target", help="Onleihe product ID or product URL")
    return parser.parse_args(argv)


def setup_logging(level: str) -> None:
    format_string = (
        "%(levelname)s %(name)s: %(message)s"
        if os.getenv("JOURNAL_STREAM") or os.getenv("INVOCATION_ID")
        else "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logging.basicConfig(level=getattr(logging, level), format=format_string)


def resolve_config_path(args: argparse.Namespace) -> Path:
    if args.config_path:
        return args.config_path
    env_path = os.getenv("ONLEIHARR_CONFIG")
    if env_path:
        return Path(env_path)
    return default_config_path()


def ensure_config_or_exit(path: Path, *, allow_wizard: bool = True) -> None:
    if path.exists():
        return
    if allow_wizard and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            created = run_first_start_wizard(path, version=load_version())
        except KeyboardInterrupt:
            logger.info("Wizard interrupted; no config written.")
            sys.exit(1)
        if created:
            return
        logger.info("Wizard cancelled; no config written.")
        sys.exit(1)
    logger.error(
        "Config file not found at %s. Run 'onleiharr --init-config%s' in an interactive terminal.",
        path,
        f" -c {path}" if path else "",
    )
    sys.exit(1)


def confirm_config_overwrite(path: Path) -> bool:
    logger.warning("Config already exists at %s.", path)
    answer = input(f"Overwrite and create a new config at {path}? [y/N]: ").strip().casefold()
    return answer in {"y", "yes", "j", "ja"}


def confirm_invalid_config_delete(path: Path, exc: ConfigError) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False

    logger.error("Configuration error in %s: %s", path, exc)
    logger.error(
        "If this config was migrated from an older v2-compatible Onleiharr release, "
        "remove it and create a new v3 config. The old config format is not compatible."
    )
    answer = input(f"Delete invalid config at {path} and create a new one? [y/N]: ").strip().casefold()
    return answer in {"y", "yes", "j", "ja"}


def recover_invalid_config_or_exit(path: Path, exc: ConfigError, *, allow_wizard: bool) -> bool:
    if not allow_wizard or not confirm_invalid_config_delete(path, exc):
        return False
    try:
        path.unlink()
    except OSError as unlink_exc:
        logger.error("Failed to delete invalid config at %s: %s", path, unlink_exc)
        return False
    logger.info("Deleted invalid config at %s.", path)
    ensure_config_or_exit(path, allow_wizard=allow_wizard)
    return True


def matches_filter(
    media: WatchedMedia | MediaItem,
    filters: Iterable[str],
    *,
    mode: str = DEFAULT_KEYWORD_MATCH_MODE,
) -> bool:
    fields = [
        getattr(media, "title", None),
        getattr(media, "subtitle", None),
        " ".join(getattr(media, "authors", []) or []),
    ]
    entries = [entry for entry in filters if entry]
    if mode == KEYWORD_MATCH_CONTAINS:
        haystack = " ".join(str(field) for field in fields if field).lower()
        return any(entry.lower() in haystack for entry in entries)
    if mode != KEYWORD_MATCH_WORD_START:
        raise ValueError(f"Unsupported keyword match mode: {mode}")

    keywords = [entry.casefold() for entry in entries]
    for field in fields:
        if not field:
            continue
        haystack = str(field).casefold()
        for keyword in keywords:
            start = haystack.find(keyword)
            while start >= 0:
                if start == 0 or not _is_keyword_word_character(haystack[start - 1]):
                    return True
                start = haystack.find(keyword, start + 1)
    return False


def _is_keyword_word_character(character: str) -> bool:
    return character.isalnum() or unicodedata.category(character).startswith("M")


def build_apprise(config: AppConfig) -> apprise.Apprise | None:
    apobj = apprise.Apprise()

    for url in config.notification.urls:
        apobj.add(url)

    if config.notification.apprise_config_path:
        apprise_config = apprise.AppriseConfig()
        apprise_config.add(str(config.notification.apprise_config_path))
        apobj.add(apprise_config)

    if not apobj:
        logger.warning("No Apprise notification targets configured; notifications disabled.")
        return None

    return apobj


def target_supports_only_image_attachments(target: object) -> bool:
    supported = getattr(target, "attach_supported_mime_type", None)
    return isinstance(supported, str) and "image/" in supported and "|" not in supported


def attachment_for_target(
    target: object,
    file_attachments: list[str],
    image_attachments: list[str],
) -> list[str]:
    if not getattr(target, "attachment_support", False):
        return []
    if target_supports_only_image_attachments(target):
        return image_attachments[:1]
    return file_attachments or image_attachments[:1]


def notify(
    apobj: apprise.Apprise | None,
    message: str,
    attachments: Iterable[Path | str] | None = None,
    image_urls: Iterable[str] | None = None,
    *,
    title: str = "Onleihe: New media",
) -> bool:
    if apobj is None:
        logger.warning("Notification skipped because no Apprise targets are configured: %s", message)
        return True
    file_attachments: list[str] = []
    image_attachments = [str(url) for url in image_urls or [] if url]
    if attachments:
        for attachment in attachments:
            if isinstance(attachment, Path):
                if attachment.exists():
                    file_attachments.append(str(attachment))
                else:
                    logger.warning("Attachment not found; skipping: %s", attachment)
            else:
                file_attachments.append(str(attachment))

    if not file_attachments and not image_attachments:
        return apobj.notify(title=title, body=message) is not False

    notified = False
    delivery_succeeded = True
    unsupported_targets = 0
    for target in apobj.find():
        target_attachments = attachment_for_target(target, file_attachments, image_attachments)
        if target_attachments:
            result = target.notify(title=title, body=message, attach=target_attachments)
            notified = True
            delivery_succeeded = delivery_succeeded and result is not False
            continue
        unsupported_targets += 1
        result = target.notify(title=title, body=message)
        notified = True
        delivery_succeeded = delivery_succeeded and result is not False
    if unsupported_targets:
        logger.warning("Some Apprise targets do not support usable attachments; sent text-only notification to them.")
    if not notified:
        logger.warning("No Apprise targets available for notification: %s", message)
    return notified and delivery_succeeded


def notify_external_auth_required(
    apobj: apprise.Apprise | None, config: AppConfig
) -> None:
    login_command = f"onleiharr --login -c {shlex.quote(str(config.config_path))}"
    restart_command = "systemctl --user restart onleiharr"
    message = (
        "<b>Die externe Onleihe-Anmeldung ist abgelaufen.</b><br>"
        "Session per SSH erneuern:<br>"
        f"<code>{html.escape(login_command)}</code><br>"
        "Danach den Dienst neu starten:<br>"
        f"<code>{html.escape(restart_command)}</code>"
    )
    notify(apobj, message, title="Onleiharr: Anmeldung erneuern")


def create_onleihe_client(config: AppConfig) -> OnleiheClient:
    session_callback = None
    if config.credentials.auth_type == "open_id":
        session_path = config.credentials.session_path or default_session_path(config.config_path)

        def session_callback(session: SessionState) -> None:
            save_session(session_path, session)

    return OnleiheClient(
        host=config.credentials.host,
        onleihe_id=config.credentials.onleihe_id,
        onleihe_name=config.credentials.onleihe_name,
        library_id=config.credentials.library_id,
        library_name=config.credentials.library_name,
        session_callback=session_callback,
    )


def login(client: OnleiheClient, config: AppConfig) -> None:
    if config.credentials.auth_type == "open_id":
        session_path = config.credentials.session_path or default_session_path(config.config_path)
        client.session = load_session(session_path)
        client.onleihe_id = client.session.onleihe_id or client.onleihe_id
        client.library_id = client.session.library_id or client.library_id
        client.refresh()
        return
    client.login(
        config.credentials.username,
        config.credentials.password,
        onleihe_id=config.credentials.onleihe_id,
        onleihe_name=config.credentials.onleihe_name,
        library_id=config.credentials.library_id,
        library_name=config.credentials.library_name,
    )


def recover_upa_authentication(
    client: OnleiheClient,
    config: AppConfig,
    *,
    failed_operation: str,
    recovery_already_attempted: bool,
) -> bool:
    if config.credentials.auth_type != "upa":
        return False
    if recovery_already_attempted:
        logger.error(
            "Onleihe authentication failed again during %s before a poll cycle completed.",
            failed_operation,
        )
        return False
    logger.warning(
        "Onleihe authentication expired during %s; attempting a fresh UPA login.",
        failed_operation,
    )
    login(client, config)
    logger.info("Onleihe UPA login recovered; retrying the poll cycle.")
    return True


def media_from_item(
    item: MediaItem,
    *,
    host: str,
    source: str,
    keyword_required: bool,
    keyword_matched: bool,
) -> WatchedMedia | None:
    product_id = item.product_id or item.id
    if not product_id:
        return None
    title = item.title or product_id
    availability = item.availability or {}
    availability_count = availability.get("availability", 0)
    if not isinstance(availability_count, int):
        availability_count = 0
    available = bool(availability.get("isAvailable") or availability_count > 0)
    availability_text = availability.get("availabilityDate") or availability.get("expectedAvailableAt")
    return WatchedMedia(
        product_id=str(product_id),
        title=title,
        url=f"https://{host}/mymedia/mediadetail?productId={product_id}",
        media_type=item.media_type,
        authors=tuple(item.authors),
        subtitle=item.subtitle,
        publication_date=item.publication_date,
        available=available,
        availability_text=str(availability_text) if availability_text else None,
        acsm_url=item.acsm_url,
        source=source,
        keyword_required=keyword_required,
        keyword_matched=keyword_matched,
        cover_url=item.cover_url,
    )


def raw_product(product: ProductDetails) -> dict[str, object]:
    raw = product.raw.get("product")
    return raw if isinstance(raw, dict) else {}


def product_is_container(product: ProductDetails) -> bool:
    raw = raw_product(product)
    return raw.get("isContainer") is True or bool(product.included_media)


def product_container_ids(product: ProductDetails) -> list[str]:
    container_ids = raw_product(product).get("containerIds", [])
    if not isinstance(container_ids, list):
        return []
    return [str(container_id) for container_id in container_ids if container_id]


def product_label(product: ProductDetails) -> str:
    return (
        " ".join(part for part in [product.title, product.subtitle] if part)
        or product.product_id
        or product.id
        or "unknown"
    )


def normalize_product_watch_ids(client: OnleiheClient, product_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    def add(product_id: str) -> None:
        if product_id in seen:
            return
        seen.add(product_id)
        normalized.append(product_id)

    for product_id in product_ids:
        try:
            product = client.get_product(product_id, include_user_context=False)
        except OnleiheNotFoundError as exc:
            logger.error(
                "Product watch %s does not exist in the Onleihe API; disabling this watch target: %s",
                product_id,
                exc,
            )
            continue
        except OnleiheAPIError:
            raise
        except Exception as exc:
            logger.exception(
                "Failed to resolve product watch %s during startup; keeping original id: %s",
                product_id,
                exc,
            )
            add(product_id)
            continue

        resolved_id = product.product_id or product.id or product_id
        if product_is_container(product):
            if resolved_id != product_id:
                logger.info("Product watch %s resolves to container %s.", product_id, resolved_id)
            add(str(resolved_id))
            continue

        container_ids = product_container_ids(product)
        if container_ids:
            logger.info(
                "Product watch %s is issue \"%s\"; watching container id(s) instead: %s",
                product_id,
                product_label(product),
                ", ".join(container_ids),
            )
            for container_id in container_ids:
                add(container_id)
            continue

        logger.warning(
            "Product watch %s is a single product without container ids; only this medium will be watched.",
            product_id,
        )
        add(product_id)

    if len(normalized) < len(product_ids):
        logger.info(
            "Deduplicated product watches from %d configured ids to %d effective ids.",
            len(product_ids),
            len(normalized),
        )
    return normalized


def fetch_product_watch_media(client: OnleiheClient, product_id: str) -> list[WatchedMedia]:
    product = client.get_product(product_id, include_user_context=False)
    items = product.included_media
    if not items:
        container_ids = product_container_ids(product)
        if container_ids:
            items = []
            for container_id in container_ids:
                container = client.get_product(container_id, include_user_context=False)
                container_items = container.included_media
                if not container_items and product_is_container(container):
                    raise OnleiheAPIError(
                        f"Product watch {product_id} resolved to container {container_id}, "
                        "but its product details contained no included media"
                    )
                items.extend(container_items or [container])
            logger.debug(
                "Resolved product watch %s through %d container ids to %d media items",
                product_id,
                len(container_ids),
                len(items),
            )
    if not items:
        if product_is_container(product):
            raise OnleiheAPIError(
                f"Product watch {product_id} is a container, but its product details "
                "contained no included media"
            )
        logger.warning(
            "Product watch %s is a single product without container ids; only this medium will be watched.",
            product_id,
        )
        items = [product]
    media: list[WatchedMedia] = []
    for item in items:
        converted = media_from_item(
            item,
            host=client.host,
            source=f"product:{product_id}",
            keyword_required=False,
            keyword_matched=True,
        )
        if converted:
            media.append(converted)
    return media


def fetch_category_watch_media(
    client: OnleiheClient,
    watch: WatchCategory,
    *,
    source: str | None = None,
    keyword_match_mode: str = DEFAULT_KEYWORD_MATCH_MODE,
) -> CategoryWatchResult:
    body = client.build_category_search_body(
        watch.category_ids,
        sort=[{"field": watch.sort_field, "order": watch.sort_order}],
    )
    post_filters = list(body.get("postFilters", []))
    if watch.media_types:
        post_filters.append({"field": "mediaType", "values": watch.media_types})
    post_filters.extend(watch.filters)
    if post_filters:
        body["postFilters"] = merge_filters(post_filters)
    result = client.search_media(raw_body=body, require_login=False)
    media: list[WatchedMedia] = []
    source = source or f"category:{watch.description or ','.join(watch.category_ids[:2])}"
    total = len(result.items)
    for item in result.items:
        keyword_matched = matches_filter(item, watch.keywords, mode=keyword_match_mode)
        if not keyword_matched:
            continue
        converted = media_from_item(
            item,
            host=client.host,
            source=source,
            keyword_required=True,
            keyword_matched=True,
        )
        if converted:
            media.append(converted)
    return CategoryWatchResult(media=media, total=total)


def merge_filters(filters: list[dict[str, object]]) -> list[dict[str, object]]:
    by_field: dict[str, dict[str, object]] = {}
    for item in filters:
        field = item.get("field")
        values = item.get("values")
        if not isinstance(field, str) or not isinstance(values, list):
            continue
        merged = by_field.setdefault(field, {"values": []})
        target = merged["values"]
        if not isinstance(target, list):
            continue
        if "type" in item and "type" not in merged:
            merged["type"] = item["type"]
        if "operator" in item and "operator" not in merged:
            merged["operator"] = item["operator"]
        for value in values:
            value_str = str(value)
            if value_str not in target:
                target.append(value_str)
    result = []
    for field, item in by_field.items():
        values = item["values"]
        if not isinstance(values, list):
            continue
        merged_filter = {"field": field, "values": values}
        if len(values) > 1:
            merged_filter["type"] = str(item.get("type") or "TERMS")
            merged_filter["operator"] = str(item.get("operator") or "OR")
        else:
            if "type" in item:
                merged_filter["type"] = str(item["type"])
            if "operator" in item:
                merged_filter["operator"] = str(item["operator"])
        result.append(merged_filter)
    return result


def fetch_all_watched_media(
    client: OnleiheClient,
    config: AppConfig,
    *,
    log_summary: bool = False,
) -> WatchPollResult:
    media: list[WatchedMedia] = []
    errors = 0
    successful_sources: set[str] = set()
    for product_id in config.general.watch_product_ids:
        source = f"product:{product_id}"
        try:
            product_media = fetch_product_watch_media(client, product_id)
            successful_sources.add(source)
            if log_summary:
                logger.info("Primed product watch %s with %d media items.", product_id, len(product_media))
            else:
                logger.debug("Fetched %d media items for product watch %s", len(product_media), product_id)
            media.extend(product_media)
        except OnleiheNotFoundError as exc:
            errors += 1
            logger.error("Product watch %s no longer exists; skipping this poll: %s", product_id, exc)
        except OnleiheAuthError:
            raise
        except Exception as exc:
            errors += 1
            logger.exception("Failed to fetch product watch %s: %s", product_id, exc)
    for index, watch in enumerate(config.general.watch_categories):
        label = watch.description or ",".join(watch.category_ids[:2])
        source = f"category:{index}:{label}"
        try:
            category_result = fetch_category_watch_media(
                client,
                watch,
                source=source,
                keyword_match_mode=getattr(
                    config.general,
                    "keyword_match_mode",
                    DEFAULT_KEYWORD_MATCH_MODE,
                ),
            )
            successful_sources.add(source)
            if log_summary:
                logger.info(
                    "Primed category watch %s with %d keyword-matched media items (%d total).",
                    label,
                    len(category_result.media),
                    category_result.total,
                )
            else:
                logger.debug(
                    "Fetched %d matched media items out of %d total for category watch %s",
                    len(category_result.media),
                    category_result.total,
                    watch,
                )
            media.extend(category_result.media)
        except OnleiheAuthError:
            raise
        except Exception as exc:
            errors += 1
            logger.exception("Failed to fetch category watch %s: %s", watch.description or watch.category_ids, exc)
    return WatchPollResult(
        media=media,
        errors=errors,
        successful_sources=frozenset(successful_sources),
    )


def display_title(media: WatchedMedia | MediaItem) -> str:
    title = getattr(media, "title", None) or getattr(media, "product_id", None) or getattr(media, "id", None) or "unknown"
    subtitle = getattr(media, "subtitle", None)
    if not subtitle:
        return str(title)
    title_text = str(title)
    subtitle_text = str(subtitle).strip()
    if not subtitle_text or subtitle_text in title_text:
        return title_text
    return f"{title_text} ({subtitle_text})"


def format_message(media: WatchedMedia, availability_message: str) -> str:
    label = media.media_type or "MEDIA"
    author = f" - {', '.join(media.authors)}" if media.authors else ""
    return f"[{label}] <b><a href=\"{media.url}\">{display_title(media)}{author}</a></b> {availability_message}"


def media_image_urls(media: WatchedMedia | None) -> list[str]:
    return [media.cover_url] if media and media.cover_url else []


def download_acsm_with_gourou(
    *,
    product_id: str,
    title: str,
    acsm_url: str | None,
    client: OnleiheClient,
    gourou_client: GourouClient | None,
) -> tuple[bool, Path | None]:
    if gourou_client is None:
        logger.debug("Gourou download disabled; skipping '%s'.", title)
        return False, None
    if not acsm_url:
        logger.warning("No ACSM URL found for '%s'; download skipped.", title)
        return False, None

    try:
        logger.info("Fetching ACSM for '%s'...", title)
        acsm_content = client.download_acsm(acsm_url)
        if not acsm_content:
            logger.error("Failed to download ACSM for '%s'.", title)
            return False, None

        fd, tmp_path = tempfile.mkstemp(prefix=f"onleiharr_{product_id}_", suffix=".acsm")
        os.close(fd)
        acsm_path = Path(tmp_path)
        acsm_path.write_bytes(acsm_content)

        cleanup = False
        try:
            result = gourou_client.download_acsm(acsm_path, notify=False)
            cleanup = True
            if not result.output_path or not result.output_path.exists():
                logger.warning("acsmdownloader finished without a usable output path. Will retry later.")
                return False, None
            if gourou_client.config.remove_drm and result.output_path.suffix.lower() in {".pdf", ".epub"}:
                try:
                    gourou_client.remove_drm(result.output_path)
                    logger.info("DRM removed for %s.", result.output_path)
                except GourouError as exc:
                    logger.error("DRM removal failed for %s: %s", result.output_path, exc)
            if gourou_client.config.download_permissions is not None:
                result.output_path.chmod(gourou_client.config.download_permissions)
            logger.info("Downloaded media to %s.", result.output_path)
            return True, result.output_path
        except GourouError as exc:
            logger.error("Gourou download failed for '%s': %s", title, exc)
            return False, None
        finally:
            if cleanup:
                try:
                    acsm_path.unlink()
                except OSError:
                    logger.debug("Failed to remove ACSM file %s.", acsm_path, exc_info=True)
            else:
                logger.info("Keeping ACSM file for debugging: %s", acsm_path)
    except Exception as exc:
        logger.exception("Unexpected error during download for '%s': %s", title, exc)
        return False, None


def run_startup_checks(config: AppConfig) -> None:
    if config.gourou.remove_drm:
        if config.gourou.remove_drm_ack != DRM_ACK_TOKEN:
            logger.warning(
                "DRM removal requested but not acknowledged; set gourou.remove_drm_ack or "
                "ONLEIHARR_GOUROU_ACK_DRM to '%s'. Disabling DRM removal.",
                DRM_ACK_TOKEN,
            )
            config.gourou.remove_drm = False
        else:
            logger.warning("DRM removal is enabled. Ensure this is legal in your jurisdiction.")


def build_gourou_client(config: AppConfig) -> GourouClient | None:
    gourou_client = GourouClient(config.gourou)
    if not gourou_client.has_binaries(["acsmdownloader"]):
        logger.warning(
            "libgourou binaries not found; auto-download disabled. "
            "Install them in PATH or set gourou.bin_dir / ONLEIHARR_GOUROU_BIN_DIR."
        )
        return None
    return gourou_client


def parse_download_target(value: str, *, expected_host: str) -> str:
    value = value.strip()
    if PRODUCT_ID_RE.fullmatch(value):
        return value.casefold()

    parsed = urlparse(value)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise ValueError("target must be a 24-character product ID or an HTTPS Onleihe URL")
    if parsed.hostname.casefold() != expected_host.casefold():
        raise ValueError(
            f"product URL host {parsed.hostname!r} does not match configured host {expected_host!r}"
        )
    product_ids = [item.strip() for item in parse_qs(parsed.query).get("productId", []) if item.strip()]
    if len(product_ids) != 1 or not PRODUCT_ID_RE.fullmatch(product_ids[0]):
        raise ValueError("product URL must contain exactly one valid productId parameter")
    return product_ids[0].casefold()


def select_download_media(
    product: ProductDetails,
    *,
    input_func=input,
) -> MediaItem | ProductDetails:
    if not product_is_container(product):
        return product
    media = product.included_media
    if not media:
        raise ConfigError(f"Product {product_label(product)} is a container without downloadable media.")
    if len(media) == 1:
        return media[0]
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        choices = ", ".join(
            f"{display_title(item)} ({item.product_id or item.id or 'unknown'})" for item in media
        )
        raise ConfigError(f"Product contains multiple media; pass one product ID directly: {choices}")

    print("Select a medium to download:")
    for index, item in enumerate(media, start=1):
        print(
            f"  {index}. {display_title(item)} "
            f"[{item.media_type or 'MEDIA'}] ({item.product_id or item.id or 'unknown'})"
        )
    while True:
        answer = input_func("Selection (empty to cancel): ").strip()
        if not answer:
            raise ConfigError("Download cancelled.")
        try:
            selected = int(answer)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(media):
            return media[selected - 1]
        print(f"Enter a number between 1 and {len(media)}.")


def _media_product_id(media: MediaItem | ProductDetails) -> str:
    product_id = media.product_id or media.id
    if not product_id:
        raise ConfigError("Selected medium has no product ID.")
    return str(product_id)


def _media_is_available(media: MediaItem | ProductDetails) -> bool:
    availability = media.availability or {}
    count = availability.get("availability", 0)
    return bool(availability.get("isAvailable") or (isinstance(count, int) and count > 0))


def _find_lend_id(payload: object) -> str | None:
    if isinstance(payload, dict):
        for key in ("lendId", "lend_id"):
            value = payload.get(key)
            if value:
                return str(value)
        for value in payload.values():
            found = _find_lend_id(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_lend_id(value)
            if found:
                return found
    return None


def _find_my_media_item(client: OnleiheClient, product_id: str) -> MediaItem | None:
    for item in client.get_my_media_items(include_player_licences=False):
        if item.product_id == product_id or item.id == product_id:
            return item
    return None


def _wait_for_lend_id(
    client: OnleiheClient,
    product_id: str,
    *,
    attempts: int = 6,
    interval: float = 0.5,
) -> tuple[str | None, MediaItem | None]:
    for attempt in range(attempts):
        item = _find_my_media_item(client, product_id)
        if item is not None and item.lend_id:
            return item.lend_id, item
        if attempt + 1 < attempts:
            time.sleep(interval)
    return None, None


def _verify_lend_returned(
    client: OnleiheClient,
    product_id: str,
    *,
    attempts: int = 6,
    interval: float = 0.5,
) -> bool:
    for attempt in range(attempts):
        items = client.get_my_media_items(include_player_licences=False)
        if not any(item.product_id == product_id or item.id == product_id for item in items):
            return True
        if attempt + 1 < attempts:
            time.sleep(interval)
    return False


def run_download_command(config: AppConfig, target: str) -> int:
    try:
        product_id = parse_download_target(target, expected_host=config.credentials.host)
    except ValueError as exc:
        logger.error("Invalid download target: %s", exc)
        return 1

    run_startup_checks(config)
    gourou_client = GourouClient(config.gourou)
    try:
        gourou_client.validate_download_readiness()
    except GourouError as exc:
        logger.warning("Gourou download is not ready: %s", exc)
        return 1

    with create_onleihe_client(config) as client:
        login(client, config)
        product = client.get_product(product_id, include_user_context=True)
        selected = select_download_media(product)
        selected_id = _media_product_id(selected)
        title = display_title(selected)

        existing = _find_my_media_item(client, selected_id)
        created_lend_id: str | None = None
        acsm_url = existing.acsm_url if existing is not None else selected.acsm_url
        if existing is not None and not acsm_url and existing.lend_id:
            acsm_url = client.get_player_licence(existing.lend_id).get("acsm_url")
        if existing is None:
            if not _media_is_available(selected):
                logger.error("'%s' is not currently available; download does not create reservations.", title)
                return 1
            logger.warning(
                "This command will lend '%s', download it, and return the new lending immediately. "
                "Early return ends your right to use the downloaded copy; delete it yourself to comply "
                "with the Onleihe terms.",
                title,
            )
            lend_result = client.lend(selected_id)
            created_lend_id = _find_lend_id(lend_result)
            lent_item: MediaItem | None = None
            if created_lend_id is None:
                created_lend_id, lent_item = _wait_for_lend_id(client, selected_id)
            if created_lend_id is None:
                logger.error(
                    "The lending succeeded, but its lending ID could not be determined. "
                    "No download was started; return the medium manually in Onleihe."
                )
                return 1
            if lent_item is None:
                lent_item = _find_my_media_item(client, selected_id)
            acsm_url = lend_result.get("acsm_url") or (lent_item.acsm_url if lent_item else None)
            if not acsm_url and lent_item is not None and lent_item.lend_id:
                acsm_url = client.get_player_licence(lent_item.lend_id).get("acsm_url")

        downloaded = False
        returned = created_lend_id is None
        try:
            downloaded, _ = download_acsm_with_gourou(
                product_id=selected_id,
                title=title,
                acsm_url=acsm_url,
                client=client,
                gourou_client=gourou_client,
            )
        finally:
            if created_lend_id is not None:
                try:
                    client.return_lend(created_lend_id)
                    returned = _verify_lend_returned(client, selected_id)
                    if returned:
                        logger.info("Returned newly created lending for '%s'.", title)
                    else:
                        logger.error(
                            "Return was accepted but could not be verified; the lending for '%s' may still be active.",
                            title,
                        )
                except OnleiheAPIError as exc:
                    logger.error(
                        "Could not return the newly created lending for '%s'; it may still be active: %s",
                        title,
                        exc,
                    )
                    returned = False
        return 0 if downloaded and returned else 1


def maybe_lend_or_reserve(
    media: WatchedMedia,
    client: OnleiheClient,
    config: AppConfig,
    gourou_client: GourouClient | None,
    rented_media_ids: set[str],
    downloaded_media_ids: set[str],
) -> tuple[str, Path | None]:
    if not media.keyword_matched:
        return ("available" if media.available else availability_text(media)), None
    if media.product_id in rented_media_ids:
        return "already handled this run", None

    if media.available:
        logger.info("%s is available, attempting auto lend", media.title)
        try:
            lend_result = client.lend(media.product_id)
        except OnleiheAPIError as exc:
            if is_maintenance_active(client):
                raise MaintenanceDetectedError(
                    f"Auto lend failed for {media.product_id} while Onleihe maintenance is active"
                ) from exc
            if not lend_failure_allows_reserve(exc):
                raise
            logger.warning(
                "Auto lend failed for '%s' because it no longer appears lendable; attempting reservation.",
                media.title,
            )
            client.reserve(media.product_id)
            rented_media_ids.add(media.product_id)
            return "auto reserved after lend failed", None
        rented_media_ids.add(media.product_id)
        acsm_url = lend_result.get("acsm_url") or media.acsm_url
        download_path: Path | None = None
        if media.media_type not in {"E_AUDIO", "AUDIO"}:
            if media.product_id not in downloaded_media_ids:
                downloaded, download_path = download_acsm_with_gourou(
                    product_id=media.product_id,
                    title=media.title,
                    acsm_url=acsm_url,
                    client=client,
                    gourou_client=gourou_client,
                )
                if downloaded:
                    downloaded_media_ids.add(media.product_id)
        else:
            logger.info("Skipping download for audio media '%s'.", media.title)
        return "auto lent :)", download_path

    logger.info("%s is unavailable, attempting to reserve", media.title)
    client.reserve(media.product_id)
    rented_media_ids.add(media.product_id)
    return f"auto reserved - {availability_text(media)}", None


def lend_failure_allows_reserve(exc: OnleiheAPIError) -> bool:
    if isinstance(exc, OnleiheAuthError):
        return False
    if exc.status_code != 409 or not isinstance(exc.payload, dict):
        return False
    return exc.payload.get("messageId") == "no-available-licences"


def availability_text(media: WatchedMedia) -> str:
    if media.available:
        return "available"
    if media.availability_text:
        return f"not available until <b>{media.availability_text}</b>"
    return "not available"


def process_my_media_downloads(
    client: OnleiheClient,
    config: AppConfig,
    apobj: apprise.Apprise | None,
    gourou_client: GourouClient | None,
    downloaded_media_ids: set[str],
    *,
    seed_only: bool,
) -> int:
    if gourou_client is None:
        return 0
    handled = 0
    for item in client.get_my_media_items(include_player_licences=False):
        product_id = item.product_id or item.id
        if not product_id or product_id in downloaded_media_ids:
            continue
        if seed_only and (item.acsm_url or item.lend_id):
            downloaded_media_ids.add(product_id)
            handled += 1
            continue
        acsm_url = item.acsm_url
        if not acsm_url and item.lend_id and not seed_only:
            licence = client.get_player_licence(item.lend_id)
            acsm_url = licence.get("acsm_url")
        if not acsm_url:
            continue
        downloaded, download_path = download_acsm_with_gourou(
            product_id=product_id,
            title=item.title or product_id,
            acsm_url=acsm_url,
            client=client,
            gourou_client=gourou_client,
        )
        if downloaded:
            downloaded_media_ids.add(product_id)
            handled += 1
            if config.gourou.lendings_notify:
                media = media_from_item(
                    item,
                    host=client.host,
                    source="my-media",
                    keyword_required=False,
                    keyword_matched=True,
                )
                message = format_message(media, "auto downloaded") if media else f"<b>{product_id}</b> auto downloaded"
                notify(
                    apobj,
                    message,
                    attachments=[download_path] if download_path else None,
                    image_urls=media_image_urls(media),
                )
    return handled


def is_maintenance_active(client: OnleiheClient) -> bool:
    try:
        return client.maintenance_active()
    except OnleiheAPIError as exc:
        logger.warning("Could not check Onleihe maintenance status; continuing normal polling: %s", exc)
        return False


def wait_for_maintenance_end(
    client: OnleiheClient,
    poll_interval_secs: float,
    *,
    already_active: bool = False,
) -> None:
    waiting = already_active
    if already_active:
        time.sleep(poll_interval_secs)
    while is_maintenance_active(client):
        if not waiting:
            logger.warning("Onleihe maintenance is active; suspending polling until maintenance ends.")
        else:
            logger.debug("Onleihe maintenance is still active; polling remains suspended.")
        waiting = True
        time.sleep(poll_interval_secs)
    if waiting:
        logger.info("Onleihe maintenance ended; resuming polling.")


def suspend_if_maintenance_active(
    client: OnleiheClient,
    poll_interval_secs: float,
    *,
    failed_operation: str,
) -> bool:
    if not is_maintenance_active(client):
        return False
    logger.warning("%s failed while Onleihe maintenance is active; retrying after maintenance ends.", failed_operation)
    wait_for_maintenance_end(client, poll_interval_secs, already_active=True)
    return True


def initialize_onleihe_session(
    client: OnleiheClient,
    config: AppConfig,
    apobj: apprise.Apprise | None,
    gourou_client: GourouClient | None,
    downloaded_media_ids: set[str],
    lendings_enabled: bool,
    lendings_interval_secs: float,
) -> float | None:
    while True:
        try:
            login(client, config)
            if config.general.watch_product_ids:
                config.general.watch_product_ids = normalize_product_watch_ids(
                    client,
                    config.general.watch_product_ids,
                )
            if not lendings_enabled:
                return None
            try:
                seeded = process_my_media_downloads(
                    client,
                    config,
                    apobj,
                    gourou_client,
                    downloaded_media_ids,
                    seed_only=True,
                )
            except OnleiheAPIError as exc:
                seeded = 0
                if suspend_if_maintenance_active(
                    client,
                    config.general.poll_interval_secs,
                    failed_operation="Initial my-media scan",
                ):
                    continue
                logger.exception("Failed to prime my-media cache; will retry later: %s", exc)
            except Exception as exc:
                seeded = 0
                logger.exception("Failed to prime my-media cache; will retry later: %s", exc)
            logger.info("Primed my-media cache with %d items.", seeded)
            return time.monotonic() + lendings_interval_secs
        except OnleiheAPIError:
            if suspend_if_maintenance_active(
                client,
                config.general.poll_interval_secs,
                failed_operation="Onleihe startup",
            ):
                continue
            raise


def run_loop(config: AppConfig, args: argparse.Namespace) -> None:
    apobj = build_apprise(config)
    client = create_onleihe_client(config)
    gourou_client = build_gourou_client(config)

    known_media_ids: set[str] = set()
    primed_sources: set[str] = set()
    rented_media_ids: set[str] = set()
    downloaded_media_ids: set[str] = set()
    first_run = True
    test_notify = args.test_notification or config.notification.test_notification
    lendings_interval_secs = float(config.gourou.lendings_poll_interval_secs)
    lendings_enabled = gourou_client is not None and lendings_interval_secs > 0
    lendings_next_check_ts: float | None = None
    auth_recovery_attempted = False

    try:
        lendings_next_check_ts = initialize_onleihe_session(
            client,
            config,
            apobj,
            gourou_client,
            downloaded_media_ids,
            lendings_enabled,
            lendings_interval_secs,
        )

        while True:
            try:
                poll_result = fetch_all_watched_media(client, config, log_summary=first_run)
            except OnleiheAuthError:
                if not recover_upa_authentication(
                    client,
                    config,
                    failed_operation="watch poll",
                    recovery_already_attempted=auth_recovery_attempted,
                ):
                    raise
                auth_recovery_attempted = True
                continue
            if poll_result.errors and suspend_if_maintenance_active(
                client,
                config.general.poll_interval_secs,
                failed_operation="Watch poll",
            ):
                continue
            current_media_list = poll_result.media
            current_media_by_id = {media.product_id: media for media in current_media_list}
            current_sources_by_id: dict[str, set[str]] = {}
            for media in current_media_list:
                current_sources_by_id.setdefault(media.product_id, set()).add(media.source)
            successful_sources = set(poll_result.successful_sources)
            if not successful_sources and not poll_result.errors:
                successful_sources.update(media.source for media in current_media_list)
            newly_primed_sources = successful_sources - primed_sources
            new_media_ids: set[str] = set()
            for product_id, sources in current_sources_by_id.items():
                if product_id in known_media_ids:
                    continue
                if sources & primed_sources:
                    new_media_ids.add(product_id)
                elif sources & newly_primed_sources:
                    known_media_ids.add(product_id)
            primed_sources.update(newly_primed_sources)

            if first_run:
                if poll_result.errors:
                    logger.warning(
                        "Initial media cache priming had %d failed watch target(s); "
                        "successful targets remain active and failed targets will be primed after recovery.",
                        poll_result.errors,
                    )
                first_run = False
                logger.info(
                    "Primed media cache with %d media items from %d watch target(s); "
                    "now polling every %d seconds",
                    len(known_media_ids),
                    len(primed_sources),
                    int(config.general.poll_interval_secs),
                )
                if test_notify and current_media_list:
                    media = current_media_list[-1]
                    notify(
                        apobj,
                        format_message(media, "test notification"),
                        image_urls=media_image_urls(media),
                    )
            else:
                if newly_primed_sources:
                    logger.info(
                        "Primed %d recovered watch target(s) without treating existing media as new.",
                        len(newly_primed_sources),
                    )
                if poll_result.errors:
                    logger.warning(
                        "Watch poll had %d failed target(s); keeping existing cache for failed targets.",
                        poll_result.errors,
                    )
                if new_media_ids:
                    logger.info("Found %d new media items", len(new_media_ids))
                else:
                    logger.debug("No new media found this cycle")

                processed_media_ids: set[str] = set()
                restart_poll_after_auth_recovery = False
                for product_id in new_media_ids:
                    media = current_media_by_id[product_id]
                    try:
                        message, download_path = maybe_lend_or_reserve(
                            media,
                            client,
                            config,
                            gourou_client,
                            rented_media_ids,
                            downloaded_media_ids,
                        )
                        notification_succeeded = notify(
                            apobj,
                            format_message(media, message),
                            attachments=[download_path] if download_path else None,
                            image_urls=media_image_urls(media),
                        )
                        if not notification_succeeded:
                            logger.error(
                                "Notification for '%s' failed; leaving it new for a retry.",
                                media.title,
                            )
                            continue
                        processed_media_ids.add(product_id)
                    except MaintenanceDetectedError:
                        logger.warning(
                            "Auto lend for '%s' failed during Onleihe maintenance; "
                            "leaving it new for a retry after maintenance.",
                            media.title,
                        )
                        wait_for_maintenance_end(
                            client,
                            config.general.poll_interval_secs,
                            already_active=True,
                        )
                        break
                    except OnleiheAuthError:
                        if not recover_upa_authentication(
                            client,
                            config,
                            failed_operation=f"handling media '{media.title}'",
                            recovery_already_attempted=auth_recovery_attempted,
                        ):
                            raise
                        auth_recovery_attempted = True
                        restart_poll_after_auth_recovery = True
                        break
                    except OnleiheAPIError as exc:
                        if suspend_if_maintenance_active(
                            client,
                            config.general.poll_interval_secs,
                            failed_operation=f"Handling media '{media.title}'",
                        ):
                            break
                        logger.exception("Onleihe API error handling media '%s': %s", media.title, exc)
                    except Exception as exc:
                        logger.exception("Error handling media '%s': %s", media.title, exc)

                known_media_ids.update(processed_media_ids)
                if restart_poll_after_auth_recovery:
                    continue

            if lendings_enabled and lendings_next_check_ts is not None and time.monotonic() >= lendings_next_check_ts:
                try:
                    count = process_my_media_downloads(
                        client,
                        config,
                        apobj,
                        gourou_client,
                        downloaded_media_ids,
                        seed_only=False,
                    )
                    logger.debug("My-media scan handled %d items.", count)
                except OnleiheAuthError:
                    if not recover_upa_authentication(
                        client,
                        config,
                        failed_operation="my-media scan",
                        recovery_already_attempted=auth_recovery_attempted,
                    ):
                        raise
                    auth_recovery_attempted = True
                    continue
                except OnleiheAPIError as exc:
                    if not suspend_if_maintenance_active(
                        client,
                        config.general.poll_interval_secs,
                        failed_operation="My-media scan",
                    ):
                        logger.exception("My-media scan failed; will retry later: %s", exc)
                except Exception as exc:
                    logger.exception("My-media scan failed; will retry later: %s", exc)
                lendings_next_check_ts = time.monotonic() + lendings_interval_secs

            if args.once:
                logger.info("--once set; exiting after first iteration")
                break

            auth_recovery_attempted = False
            time.sleep(config.general.poll_interval_secs)
    except OnleiheAuthError:
        if config.credentials.auth_type == "open_id":
            notify_external_auth_required(apobj, config)
        raise
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    version = load_version()
    banner_lines = [
        "||  OOO   N   N  L      EEEEE  I  H   H  AAAAA  RRRR   RRRR   ||",
        "|| O   O  NN  N  L      E      I  H   H  A   A  R   R  R   R  ||",
        "|| O   O  N N N  L      EEEE   I  HHHHH  AAAAA  RRRR   RRRR   ||",
        "|| O   O  N  NN  L      E      I  H   H  A   A  R  R   R  R   ||",
        f"||  OOO   N   N  LLLLL  EEEEE  I  H   H  A   A  R   R  R   R  || {version}",
    ]
    for line in banner_lines:
        logger.info(line)
    config_path = resolve_config_path(args)

    if args.install_user_systemd:
        ensure_config_or_exit(config_path, allow_wizard=not args.no_wizard)
        install_user_systemd(config_path)
        return 0

    if args.init_config:
        if config_path.exists():
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                logger.error("Config already exists at %s; refusing to overwrite in non-interactive mode.", config_path)
                return 1
            if not confirm_config_overwrite(config_path):
                logger.info("Keeping existing config at %s.", config_path)
                return 0
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            logger.error("--init-config requires an interactive terminal.")
            return 1
        try:
            return 0 if run_first_start_wizard(config_path, version=version) else 1
        except KeyboardInterrupt:
            logger.info("Wizard interrupted; no config written.")
            return 1

    ensure_config_or_exit(config_path, allow_wizard=not args.no_wizard)

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        if recover_invalid_config_or_exit(config_path, exc, allow_wizard=not args.no_wizard):
            try:
                config = load_config(config_path)
            except ConfigError as retry_exc:
                logger.error("Configuration error after recreating config: %s", retry_exc)
                return 1
        else:
            logger.error("Configuration error: %s", exc)
            return 1

    if args.interval is not None:
        config.general.poll_interval_secs = args.interval
    if args.test_notification:
        config.notification.test_notification = True

    if args.external_login:
        if config.credentials.auth_type != "open_id":
            logger.error("--login requires credentials.auth_type = 'open_id'.")
            return 1
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            logger.error("--login requires an interactive terminal.")
            return 1
        with create_onleihe_client(config) as client:
            login_flow = external_login_browser if args.login_browser else external_login_manual
            session = login_flow(client)
            session_path = config.credentials.session_path or default_session_path(config.config_path)
            save_session(session_path, session)
            if session.expires_at:
                logger.info(
                    "Onleihe access token expires in approximately %d minutes; refresh is automatic.",
                    max(0, (session.expires_at - int(time.time())) // 60),
                )
        logger.info("External Onleihe login stored in %s.", session_path)
        return 0

    if args.command == "download":
        try:
            return run_download_command(config, args.target)
        except OnleiheAuthError as exc:
            logger.error("Onleihe authentication failed: %s", exc)
            return 1
        except OnleiheAPIError as exc:
            logger.error("Onleihe API error: %s", exc)
            return 1
        except ConfigError as exc:
            logger.error("Download error: %s", exc)
            return 1
        except KeyboardInterrupt:
            logger.info("Download interrupted by user")
            return 1

    if args.command is None:
        reinstall_command = (
            f"onleiharr --install-as-user-systemd -c {shlex.quote(str(config.config_path))}"
        )
        logger.warning(
            "Starting the watcher without the 'watch' command is deprecated and will be removed "
            "in a future release. Start it with 'onleiharr watch'. If this message comes from an "
            "installed user service, update it with '%s', then run 'systemctl --user daemon-reload' "
            "and 'systemctl --user restart onleiharr'.",
            reinstall_command,
        )

    logger.info(
        "Loaded config from %s with %d product watches and %d category watches.",
        config_path,
        len(config.general.watch_product_ids),
        len(config.general.watch_categories),
    )
    logger.debug("Configured product watch ids: %s", config.general.watch_product_ids)

    run_startup_checks(config)

    try:
        run_loop(config, args)
    except OnleiheAuthError as exc:
        logger.error("Onleihe authentication failed: %s", exc)
        return 1
    except OnleiheAPIError as exc:
        logger.error("Onleihe API startup error: %s", exc)
        return 1
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
