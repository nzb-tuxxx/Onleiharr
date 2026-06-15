from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import apprise

from onleiharr._vendor.onleihe import (
    MediaItem,
    OnleiheAPIError,
    OnleiheAuthError,
    OnleiheClient,
    OnleiheNotFoundError,
    ProductDetails,
)
from onleiharr.config import (
    AppConfig,
    ConfigError,
    WatchCategory,
    default_config_path,
    load_config,
)
from onleiharr.gourou import GourouClient, GourouError
from onleiharr.wizard import run_first_start_wizard

logger = logging.getLogger(__name__)

DRM_ACK_TOKEN = "I_UNDERSTAND"


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


@dataclass(frozen=True)
class CategoryWatchResult:
    media: list[WatchedMedia]
    total: int


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
""".format(exec_start=f"{executable} -c {config_path}")

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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Onleiharr watcher and auto-renter")
    parser.add_argument("-c", "--config", dest="config_path", type=Path, help="Path to onleiharr.toml")
    parser.add_argument("--version", action="version", version=load_version())
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
    parser.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Logging verbosity",
    )
    parser.add_argument("--once", action="store_true", help="Run a single poll iteration and exit")
    parser.add_argument("--interval", type=float, dest="interval", help="Override poll interval in seconds")
    parser.add_argument(
        "--test-notification",
        action="store_true",
        dest="test_notification",
        help="Force a notification test on startup",
    )
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


def matches_filter(media: WatchedMedia | MediaItem, filters: Iterable[str]) -> bool:
    haystack = " ".join(
        part
        for part in [
            getattr(media, "title", None),
            getattr(media, "subtitle", None),
            " ".join(getattr(media, "authors", []) or []),
        ]
        if part
    ).lower()
    return any(entry.lower() in haystack for entry in filters)


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
) -> None:
    if apobj is None:
        logger.warning("Notification skipped because no Apprise targets are configured: %s", message)
        return
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
        apobj.notify(title="Onleihe: New media", body=message)
        return

    notified = False
    unsupported_targets = 0
    for target in apobj.find():
        target_attachments = attachment_for_target(target, file_attachments, image_attachments)
        if target_attachments:
            target.notify(title="Onleihe: New media", body=message, attach=target_attachments)
            notified = True
            continue
        unsupported_targets += 1
        target.notify(title="Onleihe: New media", body=message)
        notified = True
    if unsupported_targets:
        logger.warning("Some Apprise targets do not support usable attachments; sent text-only notification to them.")
    if not notified:
        logger.warning("No Apprise targets available for notification: %s", message)


def create_onleihe_client(config: AppConfig) -> OnleiheClient:
    return OnleiheClient(
        host=config.credentials.host,
        onleihe_id=config.credentials.onleihe_id,
        onleihe_name=config.credentials.onleihe_name,
        library_id=config.credentials.library_id,
        library_name=config.credentials.library_name,
    )


def login(client: OnleiheClient, config: AppConfig) -> None:
    client.login(
        config.credentials.username,
        config.credentials.password,
        onleihe_id=config.credentials.onleihe_id,
        onleihe_name=config.credentials.onleihe_name,
        library_id=config.credentials.library_id,
        library_name=config.credentials.library_name,
    )


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
                items.extend(container.included_media or [container])
            logger.debug(
                "Resolved product watch %s through %d container ids to %d media items",
                product_id,
                len(container_ids),
                len(items),
            )
    if not items:
        if not product_is_container(product):
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


def fetch_category_watch_media(client: OnleiheClient, watch: WatchCategory) -> CategoryWatchResult:
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
    source = f"category:{watch.description or ','.join(watch.category_ids[:2])}"
    total = len(result.items)
    for item in result.items:
        keyword_matched = matches_filter(item, watch.keywords)
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
    for product_id in config.general.watch_product_ids:
        try:
            product_media = fetch_product_watch_media(client, product_id)
            if log_summary:
                logger.info("Primed product watch %s with %d media items.", product_id, len(product_media))
            else:
                logger.debug("Fetched %d media items for product watch %s", len(product_media), product_id)
            media.extend(product_media)
        except OnleiheNotFoundError as exc:
            errors += 1
            logger.error("Product watch %s no longer exists; skipping this poll: %s", product_id, exc)
        except Exception as exc:
            errors += 1
            logger.exception("Failed to fetch product watch %s: %s", product_id, exc)
    for watch in config.general.watch_categories:
        try:
            category_result = fetch_category_watch_media(client, watch)
            label = watch.description or ",".join(watch.category_ids[:2])
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
        except Exception as exc:
            errors += 1
            logger.exception("Failed to fetch category watch %s: %s", watch.description or watch.category_ids, exc)
    return WatchPollResult(media=media, errors=errors)


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
    rented_media_ids: set[str] = set()
    downloaded_media_ids: set[str] = set()
    first_run = True
    test_notify = args.test_notification or config.notification.test_notification
    lendings_interval_secs = float(config.gourou.lendings_poll_interval_secs)
    lendings_enabled = gourou_client is not None and lendings_interval_secs > 0
    lendings_next_check_ts: float | None = None

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
            poll_result = fetch_all_watched_media(client, config, log_summary=first_run)
            if poll_result.errors and suspend_if_maintenance_active(
                client,
                config.general.poll_interval_secs,
                failed_operation="Watch poll",
            ):
                continue
            current_media_list = poll_result.media
            current_media_by_id = {media.product_id: media for media in current_media_list}
            current_media_ids = set(current_media_by_id)

            if first_run:
                if poll_result.errors:
                    logger.warning(
                        "Initial media cache priming had %d failed watch target(s); keeping startup incomplete.",
                        poll_result.errors,
                    )
                    if args.once:
                        logger.info("--once set; exiting after incomplete first iteration")
                        break
                    time.sleep(config.general.poll_interval_secs)
                    continue
                known_media_ids = current_media_ids
                first_run = False
                logger.info(
                    "Primed media cache with %d media items; now polling every %d seconds",
                    len(known_media_ids),
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
                if poll_result.errors:
                    logger.warning(
                        "Watch poll had %d failed target(s); keeping existing cache for failed targets.",
                        poll_result.errors,
                    )
                new_media_ids = current_media_ids - known_media_ids
                if new_media_ids:
                    logger.info("Found %d new media items", len(new_media_ids))
                else:
                    logger.debug("No new media found this cycle")

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
                        notify(
                            apobj,
                            format_message(media, message),
                            attachments=[download_path] if download_path else None,
                            image_urls=media_image_urls(media),
                        )
                    except (OnleiheAPIError, OnleiheAuthError) as exc:
                        logger.exception("Onleihe API error handling media '%s': %s", media.title, exc)
                    except Exception as exc:
                        logger.exception("Error handling media '%s': %s", media.title, exc)

                known_media_ids.update(new_media_ids)

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
                except Exception as exc:
                    logger.exception("My-media scan failed; will retry later: %s", exc)
                lendings_next_check_ts = time.monotonic() + lendings_interval_secs

            if args.once:
                logger.info("--once set; exiting after first iteration")
                break

            time.sleep(config.general.poll_interval_secs)
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
