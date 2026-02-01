from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from random import choice
from typing import Iterable, Set

import apprise
import requests
from requests.exceptions import RequestException

from onleiharr.config import (
    AppConfig,
    ConfigError,
    default_config_path,
    ensure_default_config,
    load_config,
)
from onleiharr.gourou import GourouClient, GourouError
from onleiharr.models import Book, Magazine, Media, MyBibLending
from onleiharr.parser import fetch_media, parse_my_bib_lendings
from onleiharr.onleihe import Onleihe, RentResult

logger = logging.getLogger(__name__)

DRM_ACK_TOKEN = "I_UNDERSTAND"


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
    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        type=Path,
        help="Path to onleiharr.toml",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=load_version(),
    )
    parser.add_argument(
        "--install-as-user-systemd",
        action="store_true",
        dest="install_user_systemd",
        help="Install a user-mode systemd unit for onleiharr",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Logging verbosity",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll iteration and exit",
    )
    parser.add_argument(
        "--interval",
        type=float,
        dest="interval",
        help="Override poll interval in seconds",
    )
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
    logging.basicConfig(
        level=getattr(logging, level),
        format=format_string,
    )


def resolve_config_path(args: argparse.Namespace) -> Path:
    if args.config_path:
        return args.config_path
    env_path = os.getenv("ONLEIHARR_CONFIG")
    if env_path:
        return Path(env_path)
    return default_config_path()


def ensure_config_or_exit(path: Path) -> None:
    if path.exists():
        return
    ensure_default_config(path)
    logger.info("Created default config at %s. Please edit it before running again.", path)
    sys.exit(1)


def load_keywords_from_config(keywords: Iterable[str]) -> Set[str]:
    return {kw.strip() for kw in keywords if kw.strip()}


def matches_filter(title: str, filters: Iterable[str]) -> bool:
    title_lower = title.lower()
    return any(entry.lower() in title_lower for entry in filters)


def build_apprise(config: AppConfig) -> apprise.Apprise:
    apobj = apprise.Apprise()

    if config.notification.urls:
        for url in config.notification.urls:
            apobj.add(url)

    if config.notification.apprise_config_path:
        apprise_config = apprise.AppriseConfig()
        apprise_config.add(str(config.notification.apprise_config_path))
        apobj.add(apprise_config)

    if not apobj:
        raise ConfigError("No apprise notification targets configured.")

    return apobj


def apprise_supports_attachments(apobj: apprise.Apprise) -> bool:
    return any(getattr(server, "attachment_support", False) for server in apobj.find())


def notify(
    apobj: apprise.Apprise,
    message: str,
    attachments: Iterable[Path] | None = None,
) -> None:
    attachment_paths: list[str] = []
    if attachments:
        for attachment in attachments:
            if attachment.exists():
                attachment_paths.append(str(attachment))
            else:
                logger.warning("Attachment not found; skipping: %s", attachment)
        if attachment_paths:
            if apprise_supports_attachments(apobj):
                apobj.notify(title="Onleihe: New media", body=message, attach=attachment_paths)
                return
            logger.warning(
                "No Apprise targets support attachments; sending notification without attachments."
            )
    apobj.notify(title="Onleihe: New media", body=message)


def download_acsm_with_gourou(
    *,
    media_id: int,
    title: str,
    acsm_url: str | None,
    onleihe: Onleihe,
    gourou_client: GourouClient | None,
) -> tuple[bool, Path | None]:
    if gourou_client is None:
        logger.debug("Gourou download disabled; skipping '%s'.", title)
        return False, None
    if not acsm_url:
        logger.warning("No ACSM URL found for '%s'; download skipped.", title)
        return False, None
    success = False
    downloaded_path: Path | None = None
    try:
        logger.info("Fetching ACSM for '%s'...", title)
        logger.debug("Fetching ACSM URL for '%s': %s", title, acsm_url)
        acsm_content = onleihe.fetch_acsm(acsm_url)
        if not acsm_content:
            logger.error("Failed to download ACSM for '%s'.", title)
            return False, None

        fd, tmp_path = tempfile.mkstemp(prefix=f"onleiharr_{media_id}_", suffix=".acsm")
        os.close(fd)
        acsm_path = Path(tmp_path)

        try:
            acsm_path.write_bytes(acsm_content)
        except OSError as exc:
            logger.error("Failed to write ACSM file for '%s': %s", title, exc)
            return False, None

        cleanup = False
        try:
            result = gourou_client.download_acsm(acsm_path, notify=False)
            cleanup = True
            if result.output_path and result.output_path.exists():
                success = True
                downloaded_path = result.output_path
                logger.info("Downloaded media to %s.", result.output_path)
                if gourou_client.config.remove_drm:
                    if result.output_path.suffix.lower() in {".pdf", ".epub"}:
                        try:
                            gourou_client.remove_drm(result.output_path)
                            logger.info("DRM removed for %s.", result.output_path)
                        except GourouError as exc:
                            logger.error("DRM removal failed for %s: %s", result.output_path, exc)
                    else:
                        logger.info(
                            "DRM removal enabled but file is not PDF/EPUB; skipping %s.",
                            result.output_path,
                        )
                if gourou_client.config.download_permissions is not None:
                    permissions = gourou_client.config.download_permissions
                    try:
                        result.output_path.chmod(permissions)
                        logger.info(
                            "Set permissions %s for %s.",
                            f"{permissions:04o}",
                            result.output_path,
                        )
                    except OSError as exc:
                        logger.warning(
                            "Failed to set permissions %s for %s: %s",
                            f"{permissions:04o}",
                            result.output_path,
                            exc,
                        )
            else:
                # Don't treat this as a successful download - we would otherwise suppress retries
                # and potentially send a misleading notification.
                if result.output_path:
                    logger.warning(
                        "acsmdownloader returned an output path but file is missing: %s. Will retry later.",
                        result.output_path,
                    )
                else:
                    logger.warning(
                        "acsmdownloader finished but output path could not be parsed. Will retry later."
                    )
        except GourouError as exc:
            logger.error("Gourou download failed for '%s': %s", title, exc)
        finally:
            if cleanup:
                try:
                    acsm_path.unlink()
                except OSError as exc:
                    logger.debug("Failed to remove ACSM file %s: %s", acsm_path, exc)
            else:
                logger.info("Keeping ACSM file for debugging: %s", acsm_path)
    except Exception as exc:
        logger.exception("Unexpected error during download for '%s': %s", title, exc)
    return success, downloaded_path


def download_media_with_gourou(
    media: Media,
    rent_result: RentResult,
    onleihe: Onleihe,
    gourou_client: GourouClient | None,
) -> tuple[bool, Path | None]:
    return download_acsm_with_gourou(
        media_id=media.id,
        title=media.title,
        acsm_url=rent_result.acsm_url,
        onleihe=onleihe,
        gourou_client=gourou_client,
    )


def _build_onleihe_frontend_url(library: str, href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"https://www.onleihe.de{href}"
    return f"https://www.onleihe.de/{library}/frontend/{href}"


def format_my_bib_download_message(library: str, lending: MyBibLending) -> str:
    title = lending.title or f"media id {lending.media_id}"
    url = _build_onleihe_frontend_url(library, lending.media_info_href)
    if url:
        link = f"<a href=\"{url}\">{title}</a>"
    else:
        link = title
    return f"[MYBIB] <b>{link}</b> auto downloaded"


def _looks_like_login_page(html: str) -> bool:
    # Cheap but effective: if we got redirected to the login form, don't treat this as a successful scan/prime.
    return 'id="loginForm"' in html or "id='loginForm'" in html


def prime_my_bib_lendings_cache(onleihe: Onleihe, downloaded_media_ids: Set[int]) -> tuple[bool, int]:
    html = onleihe.fetch_my_bib_lendings()
    if not html or _looks_like_login_page(html):
        return False, 0
    lendings = parse_my_bib_lendings(html)
    seeded = 0
    total = len(lendings)
    with_acsm = 0
    for lending in lendings:
        # Only treat items as "known/downloaded" if an ACSM link is present. This avoids
        # poisoning the cache with reservations that are not yet available.
        if not lending.acsm_url:
            continue
        with_acsm += 1
        if lending.media_id not in downloaded_media_ids:
            seeded += 1
        downloaded_media_ids.add(lending.media_id)
    logger.debug(
        "MyBib prime: parsed %d entries (%d with ACSM), seeded %d into download cache",
        total,
        with_acsm,
        seeded,
    )
    return True, seeded


def format_message(media: Media, availability_message: str) -> str:
    if isinstance(media, Book):
        return (
            f"[{media.format.upper()}] <b><a href=\"{media.full_url}\">{media.title} - {media.author}</a></b> "
            f"{availability_message}"
        )
    if isinstance(media, Magazine):
        return f"[MAGAZINE] <b><a href=\"{media.full_url}\">{media.title}</a></b> {availability_message}"
    return f"<b><a href=\"{media.full_url}\">{media.title}</a></b> {availability_message}"


def run_startup_checks(config: AppConfig) -> None:
    if config.gourou.remove_drm:
        if config.gourou.remove_drm_ack != DRM_ACK_TOKEN:
            logger.warning(
                "DRM removal requested but not acknowledged; please acknowledge the DRM removal conditions by setting "
                "gourou.remove_drm_ack or ONLEIHARR_GOUROU_ACK_DRM to '%s'. Disabling DRM removal.",
                DRM_ACK_TOKEN,
            )
            config.gourou.remove_drm = False
        else:
            logger.warning(
                "DRM removal is enabled. Ensure this is legal in your jurisdiction. "
                "Onleiharr only calls third-party libgourou; see DISCLAIMER.md."
            )


def run_loop(config: AppConfig, args: argparse.Namespace) -> None:
    keywords = load_keywords_from_config(config.general.keywords)
    apobj = build_apprise(config)
    onleihe = Onleihe(
        library=config.credentials.library,
        library_id=config.credentials.library_id,
        username=config.credentials.username,
        password=config.credentials.password,
    )
    gourou_client = GourouClient(config.gourou)
    if not gourou_client.has_binaries(["acsmdownloader"]):
        logger.warning(
            "libgourou binaries not found; auto-download disabled (notifications and auto-rent still work). "
            "Install them in PATH or set gourou.bin_dir / ONLEIHARR_GOUROU_BIN_DIR."
        )
        gourou_client = None

    known_media: Set[Media] = set()
    rented_media_ids: Set[int] = set()
    # De-dupe for downloads within a single run. This set is shared across:
    # - immediate downloads after auto-rent
    # - periodic "Mein Konto / Ausgeliehen" scans (MyBib lendings)
    downloaded_media_ids: Set[int] = set()

    lendings_interval_secs = float(config.gourou.lendings_poll_interval_secs)
    lendings_enabled = gourou_client is not None and lendings_interval_secs > 0
    lendings_cache_primed = False
    # Use a monotonic clock for scheduling so system clock adjustments (NTP, manual changes)
    # don't delay or accelerate scans unexpectedly.
    lendings_next_check_ts: float | None = None

    if lendings_enabled:
        primed, primed_count = prime_my_bib_lendings_cache(onleihe, downloaded_media_ids)
        if primed:
            lendings_cache_primed = True
            lendings_next_check_ts = time.monotonic() + lendings_interval_secs
            logger.info(
                "Primed MyBib lendings cache with %d items; next scan in %d seconds",
                primed_count,
                int(lendings_interval_secs),
            )
        else:
            retry_secs = min(600.0, lendings_interval_secs)
            lendings_next_check_ts = time.monotonic() + retry_secs
            logger.warning(
                "Failed to prime MyBib lendings cache (not logged in or network error). Will retry in %ds.",
                int(retry_secs),
            )
    first_run = True
    test_notify = args.test_notification or config.notification.test_notification

    catalog_session = requests.Session()
    try:
        while True:
            try:
                current_media_list: list[Media] = []
                current_media: Set[Media] = set()
                fetch_elements = 100 if first_run else 50
                for url in config.general.urls:
                    try:
                        url_media = list(fetch_media(url, elements=fetch_elements, session=catalog_session))
                        logger.debug("Fetched %d media items from %s", len(url_media), url)
                        if not url_media:
                            logger.warning("No media found for url; check configuration: %s", url)
                        current_media_list.extend(url_media)
                    except RequestException as exc:
                        logger.error("Network error while processing url %s: %s", url, exc)
                    except Exception as exc:
                        logger.exception("Unexpected error while processing url %s: %s", url, exc)

                if current_media_list:
                    current_media = set(current_media_list)

                if first_run:
                    logger.info(
                        "Primed media cache with %d media items (first run); now polling every %d seconds",
                        len(current_media),
                        int(config.general.poll_interval_secs),
                    )
                    if logger.isEnabledFor(logging.DEBUG):
                        for media in current_media:
                            logger.debug("[CACHE] %s", media)
                    known_media = current_media
                    first_run = False

                    if test_notify:
                        if current_media_list:
                            last_media = current_media_list[-1]
                            logger.info("Test notification mode: sending notify for '%s'.", last_media.title)
                            notify_message = format_message(last_media, "test notification")
                            notify(apobj, notify_message)
                        else:
                            logger.warning("Test notification requested but no media found on first run.")
                else:
                    new_media = current_media - known_media
                    if new_media:
                        logger.info("Found %d new media items", len(new_media))
                    else:
                        logger.debug("No new media found this cycle")
                    for media in new_media:
                        try:
                            auto_rent = False
                            auto_reserve = False
                            download_path: Path | None = None
                            if matches_filter(media.title, keywords):
                                logger.info("%s matches filter", media.title)
                                if media.available:
                                    logger.info("%s is available, attempting auto rent", media.title)
                                    if media.id in rented_media_ids:
                                        logger.debug("Media id %s already rented in this run; skipping.", media.id)
                                    else:
                                        rent_result = onleihe.rent_media(media)
                                        if rent_result:
                                            auto_rent = True
                                            rented_media_ids.add(media.id)
                                            if media.format != "audio":
                                                if media.id in downloaded_media_ids:
                                                    logger.debug(
                                                        "Media id %s already downloaded in this run; skipping.",
                                                        media.id,
                                                    )
                                                else:
                                                    downloaded, download_path = download_media_with_gourou(
                                                        media, rent_result, onleihe, gourou_client
                                                    )
                                                    if downloaded:
                                                        downloaded_media_ids.add(media.id)
                                            else:
                                                logger.info("Skipping download for audio media '%s'.", media.title)
                                else:
                                    logger.info("%s is unavailable, attempting to reserve", media.title)
                                    onleihe.reserve_media(media, config.notification.email or "")
                                    auto_reserve = True

                            if auto_rent:
                                availability_message = "auto rented :)"
                            elif auto_reserve:
                                availability_message = (
                                    f"auto reserved - available at <b>{media.availability_date}</b>"
                                )
                            elif media.available:
                                availability_message = "available"
                            else:
                                availability_message = f"not available until <b>{media.availability_date}</b>"

                            notify_message = format_message(media, availability_message)
                            logger.info("Notify: %s", notify_message)
                            notify(
                                apobj,
                                notify_message,
                                attachments=[download_path] if download_path else None,
                            )
                        except Exception as exc:
                            logger.exception("Error handling media '%s': %s", media.title, exc)

                    known_media.update(new_media)

                # Periodically scan "Mein Konto / Ausgeliehen" to download ACSM links that only appear
                # after a reservation was fulfilled (i.e., loan is now active).
                if lendings_enabled and lendings_next_check_ts is not None:
                    now = time.monotonic()
                    if now >= lendings_next_check_ts:
                        retry_secs = min(600.0, lendings_interval_secs)
                        if not lendings_cache_primed:
                            primed, primed_count = prime_my_bib_lendings_cache(onleihe, downloaded_media_ids)
                            if primed:
                                lendings_cache_primed = True
                                lendings_next_check_ts = now + lendings_interval_secs
                                logger.info(
                                    "Primed MyBib lendings cache with %d items; next scan in %d seconds",
                                    primed_count,
                                    int(lendings_interval_secs),
                                )
                            else:
                                lendings_next_check_ts = now + retry_secs
                                logger.warning(
                                    "Failed to prime MyBib lendings cache (not logged in or network error). "
                                    "Will retry in %ds.",
                                    int(retry_secs),
                                )
                        else:
                            html = onleihe.fetch_my_bib_lendings()
                            if not html:
                                logger.warning("Failed to fetch MyBib lendings page; skipping this scan.")
                                lendings_next_check_ts = now + retry_secs
                            elif _looks_like_login_page(html):
                                logger.warning("MyBib lendings page looks like a login page; skipping this scan.")
                                lendings_next_check_ts = now + retry_secs
                            else:
                                lendings = parse_my_bib_lendings(html)
                                total = len(lendings)
                                with_acsm = sum(1 for lending in lendings if lending.acsm_url)
                                logger.debug("MyBib scan: parsed %d entries (%d with ACSM)", total, with_acsm)

                                skipped_known = 0
                                skipped_keywords = 0
                                skipped_no_acsm = 0
                                attempted = 0
                                downloaded_ok = 0
                                downloaded_failed = 0
                                notified = 0

                                for lending in lendings:
                                    if lending.media_id in downloaded_media_ids:
                                        skipped_known += 1
                                        continue
                                    if (
                                        config.gourou.lendings_download_keywords_only
                                        and (not lending.title or not matches_filter(lending.title, keywords))
                                    ):
                                        skipped_keywords += 1
                                        logger.debug(
                                            "MyBib lending '%s' does not match keywords; skipping.",
                                            lending.title or lending.media_id,
                                        )
                                        continue
                                    if not lending.acsm_url:
                                        skipped_no_acsm += 1
                                        logger.debug(
                                            "No ACSM URL found for MyBib lending '%s' (id %s); download skipped.",
                                            lending.title or "",
                                            lending.media_id,
                                        )
                                        continue

                                    attempted += 1
                                    downloaded, lending_download_path = download_acsm_with_gourou(
                                        media_id=lending.media_id,
                                        title=lending.title or f"media id {lending.media_id}",
                                        acsm_url=lending.acsm_url,
                                        onleihe=onleihe,
                                        gourou_client=gourou_client,
                                    )
                                    if downloaded:
                                        downloaded_media_ids.add(lending.media_id)
                                        downloaded_ok += 1
                                        if config.gourou.lendings_notify:
                                            notify_message = format_my_bib_download_message(
                                                config.credentials.library, lending
                                            )
                                            logger.info("Notify (MyBib): %s", notify_message)
                                            notify(
                                                apobj,
                                                notify_message,
                                                attachments=[lending_download_path]
                                                if lending_download_path
                                                else None,
                                            )
                                            notified += 1
                                    else:
                                        downloaded_failed += 1

                                logger.debug(
                                    "MyBib scan: attempted=%d downloaded=%d failed=%d skipped_known=%d skipped_keywords=%d skipped_no_acsm=%d notified=%d; next scan in %d seconds",
                                    attempted,
                                    downloaded_ok,
                                    downloaded_failed,
                                    skipped_known,
                                    skipped_keywords,
                                    skipped_no_acsm,
                                    notified,
                                    int(lendings_interval_secs),
                                )
                                lendings_next_check_ts = now + lendings_interval_secs
            except Exception as exc:
                logger.exception("Unhandled error in polling loop: %s", exc)

            if args.once:
                logger.info("--once set; exiting after first iteration")
                break

            time.sleep(config.general.poll_interval_secs)
    finally:
        try:
            catalog_session.close()
        except Exception:
            logger.debug("Failed to close catalog session.", exc_info=True)


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
        if not config_path.exists():
            ensure_default_config(config_path)
            logger.info(
                "Created default config at %s. Please edit it before enabling the service.",
                config_path,
            )
        install_user_systemd(config_path)
        return 0

    ensure_config_or_exit(config_path)

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    if args.interval is not None:
        config.general.poll_interval_secs = args.interval
    if args.test_notification:
        config.notification.test_notification = True

    run_startup_checks(config)

    try:
        run_loop(config, args)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
