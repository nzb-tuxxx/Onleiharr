from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from random import choice
from typing import Iterable, Set

import apprise
from requests.exceptions import RequestException

from onleiharr.config import (
    AppConfig,
    ConfigError,
    default_config_path,
    ensure_default_config,
    load_config,
)
from onleiharr.models import Book, Magazine, Media
from onleiharr.parser import fetch_media
from onleiharr.onleihe import Onleihe

logger = logging.getLogger(__name__)


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
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
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


def notify(apobj: apprise.Apprise, message: str) -> None:
    apobj.notify(title="Onleihe: New media", body=message)


def format_message(media: Media, availability_message: str) -> str:
    if isinstance(media, Book):
        return (
            f"[{media.format.upper()}] <b><a href=\"{media.full_url}\">{media.title} - {media.author}</a></b> "
            f"{availability_message}"
        )
    if isinstance(media, Magazine):
        return f"[MAGAZINE] <b><a href=\"{media.full_url}\">{media.title}</a></b> {availability_message}"
    return f"<b><a href=\"{media.full_url}\">{media.title}</a></b> {availability_message}"


def run_loop(config: AppConfig, args: argparse.Namespace) -> None:
    keywords = load_keywords_from_config(config.general.keywords)
    apobj = build_apprise(config)
    onleihe = Onleihe(
        library=config.credentials.library,
        library_id=config.credentials.library_id,
        username=config.credentials.username,
        password=config.credentials.password,
    )

    known_media: Set[Media] = set()
    first_run = True
    test_notify = args.test_notification or config.notification.test_notification

    while True:
        current_media_list: list[Media] = []
        current_media: Set[Media] = set()
        fetch_elements = 100 if first_run else 50
        for url in config.general.urls:
            try:
                url_media = list(fetch_media(url, elements=fetch_elements))
                logger.debug("Fetched %d media items from %s", len(url_media), url)
                if not url_media:
                    logger.warning("No media found for url; check configuration: %s", url)
                current_media_list.extend(url_media)
            except RequestException as exc:
                logger.error("Network error while processing url %s: %s", url, exc)

        if current_media_list:
            current_media = set(current_media_list)

        if first_run:
            logger.info("First run, populating cache with %d media items", len(current_media))
            logger.info(
                "Done - now polling every %s seconds", int(config.general.poll_interval_secs)
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
                auto_rent = False
                auto_reserve = False
                if matches_filter(media.title, keywords):
                    logger.info("%s matches filter", media.title)
                    if media.available:
                        logger.info("%s is available, attempting auto rent", media.title)
                        onleihe.rent_media(media)
                        auto_rent = True
                    else:
                        logger.info("%s is unavailable, attempting to reserve", media.title)
                        onleihe.reserve_media(media, config.notification.email or "")
                        auto_reserve = True

                if auto_rent:
                    availability_message = "auto rented :)"
                elif auto_reserve:
                    availability_message = f"auto reserved - available at <b>{media.availability_date}</b>"
                elif media.available:
                    availability_message = "available"
                else:
                    availability_message = f"not available until <b>{media.availability_date}</b>"

                notify_message = format_message(media, availability_message)
                logger.info("Notify: %s", notify_message)
                notify(apobj, notify_message)

            known_media.update(new_media)

        if args.once:
            logger.info("--once set; exiting after first iteration")
            break

        time.sleep(config.general.poll_interval_secs)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    logger.info(
"""
||  OOO   N   N  L      EEEEE  I  H   H  AAAAA  RRRR   RRRR   ||
|| O   O  NN  N  L      E      I  H   H  A   A  R   R  R   R  ||
|| O   O  N N N  L      EEEE   I  HHHHH  AAAAA  RRRR   RRRR   ||
|| O   O  N  NN  L      E      I  H   H  A   A  R  R   R  R   ||
||  OOO   N   N  LLLLL  EEEEE  I  H   H  A   A  R   R  R   R  ||
""".rstrip()
    )
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
