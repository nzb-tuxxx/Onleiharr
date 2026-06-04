from __future__ import annotations

import getpass
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from onleiharr._vendor.onleihe import Library, OnleiheAPIError, OnleiheAuthError, OnleiheClient
from onleiharr.config import ensure_default_config

try:
    import curses
except ModuleNotFoundError:  # pragma: no cover - platform dependent
    curses = None  # type: ignore[assignment]


PRODUCT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
ONLEIHE_HOSTS_HELP_URL = "https://hilfe.onleihe.de/faq-onleihe-3/c-3739"
PRODUCT_EXAMPLE = "https://niedersachsen.onleihe.de/search/mediadetail?productId=69b3ed6bc56755bf97cb3b9a"
CATEGORY_EXAMPLE = (
    "https://niedersachsen.onleihe.de/search?categories=%5B%2265afa17e40246d5939bdbb53%22%2C%2265afa17e40246d5939bdbb54%22%2C%2265afa17e40246d5939bdbb55%22%2C%2265afa17e40246d5939bdbb56%22%2C%2265afa17e40246d5939bdbb57%22%2C%2265afa17e40246d5939bdbb58%22%2C%2265afa17e40246d5939bdbb59%22%2C%2265afa17e40246d5939bdbb5a%22%2C%2265afa17e40246d5939bdbb5b%22%2C%2265afa17e40246d5939bdbb5c%22%2C%2265afa17e40246d5939bdbb5d%22%2C%2265afa17e40246d5939bdbb5e%22%2C%2265afa17e40246d5939bdbb5f%22%2C%2265afa17e40246d5939bdbb60%22%2C%2265afa17e40246d5939bdbb62%22%2C%2265afa17e40246d5939bdbb63%22%2C%2265afa17e40246d5939bdbb64%22%2C%2265afa17e40246d5939bdbb65%22%2C%2265afa17e40246d5939bdbb66%22%2C%2265afa17e40246d5939bdbb67%22%2C%2265afa17e40246d5939bdbb68%22%2C%2265afa17e40246d5939bdbb69%22%2C%2265afa17e40246d5939bdbb6a%22%2C%2265afa17e40246d5939bdbb6b%22%5D"
)

WIZARD_LOGO = [
    "||  OOO   N   N  L      EEEEE  I  H   H  AAAAA  RRRR   RRRR   ||",
    "|| O   O  NN  N  L      E      I  H   H  A   A  R   R  R   R  ||",
    "|| O   O  N N N  L      EEEE   I  HHHHH  AAAAA  RRRR   RRRR   ||",
    "|| O   O  N  NN  L      E      I  H   H  A   A  R  R   R  R   ||",
    "||  OOO   N   N  LLLLL  EEEEE  I  H   H  A   A  R   R  R   R  ||",
]


@dataclass
class WizardConfig:
    host: str
    onleihe_name: str
    library_name: str
    username: str
    password: str
    onleihe_id: str | None = None
    library_id: str | None = None
    watch_product_ids: list[str] = field(default_factory=list)
    watch_categories: list[dict[str, list[str] | str]] = field(default_factory=list)
    apprise_urls: list[str] = field(default_factory=list)
    test_notification: bool = False
    email: str = ""
    poll_interval_secs: float = 300.0
    download_dir: str = "~/Downloads/Onleiharr"
    lendings_poll_interval_secs: float = 21600.0
    lendings_notify: bool = True
    remove_drm: bool = False
    remove_drm_ack: str | None = None


@dataclass(frozen=True)
class AccountSummary:
    user_id: str | None
    lend_current: int | None
    lend_max: int | None
    reservation_current: int | None
    reservation_max: int | None
    my_media_count: int


class WizardBack(Exception):
    """Raised when the user requests the previous wizard screen."""


def run_first_start_wizard(path: Path, *, version: str) -> bool:
    wizard = FirstStartWizard(path, version=version)
    return wizard.run()


def extract_product_id(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    product_ids = parse_qs(parsed.query).get("productId")
    if product_ids and product_ids[0].strip():
        return product_ids[0].strip()
    if PRODUCT_ID_RE.match(value):
        return value.lower()
    return value


def normalize_host(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc or parsed.path
    return host.strip().strip("/").casefold()


def build_config_text(config: WizardConfig) -> str:
    lines: list[str] = [
        "# onleiharr configuration",
        "",
        "[general]",
        f"poll_interval_secs = {float(config.poll_interval_secs):.1f}",
        "watch_product_ids = [",
    ]
    for product_id in config.watch_product_ids:
        lines.append(f"  {_toml_string(product_id)},")
    lines.extend(["]", ""])

    for watch in config.watch_categories:
        lines.extend(
            [
                "[[watch_categories]]",
                f"description = {_toml_string(str(watch.get('description') or 'Category watch'))}",
                "category_urls = [",
            ]
        )
        for url in watch.get("category_urls", []):
            lines.append(f"  {_toml_string(str(url))},")
        lines.extend(["]", "keywords = ["])
        for keyword in watch.get("keywords", []):
            lines.append(f"  {_toml_string(str(keyword))},")
        lines.extend(["]", ""])

    lines.extend(
        [
            "[notification]",
            "urls = [",
        ]
    )
    for url in config.apprise_urls:
        lines.append(f"  {_toml_string(url)},")
    lines.extend(
        [
            "]",
            f"test_notification = {_toml_bool(config.test_notification)}",
            f"email = {_toml_string(config.email)}",
            "",
            "[credentials]",
            f"host = {_toml_string(config.host)}",
            f"onleihe_name = {_toml_string(config.onleihe_name)}",
            f"library_name = {_toml_string(config.library_name)}",
        ]
    )
    if config.onleihe_id:
        lines.append(f"onleihe_id = {_toml_string(config.onleihe_id)}")
    if config.library_id:
        lines.append(f"library_id = {_toml_string(config.library_id)}")
    lines.extend(
        [
            f"username = {_toml_string(config.username)}",
            f"password = {_toml_string(config.password)}",
            "",
            "[gourou]",
            "# bin_dir = \"~/bin\"",
            f"download_dir = {_toml_string(config.download_dir)}",
            "download_permissions = \"0644\"",
            "timeout_secs = 30.0",
            f"remove_drm = {_toml_bool(config.remove_drm)}",
            f"lendings_poll_interval_secs = {float(config.lendings_poll_interval_secs):.1f}",
            f"lendings_notify = {_toml_bool(config.lendings_notify)}",
        ]
    )
    if config.remove_drm_ack:
        lines.append(f"remove_drm_ack = {_toml_string(config.remove_drm_ack)}")
    lines.append("")
    return "\n".join(lines)


def write_config_atomic(path: Path, config: WizardConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_config_text(config)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if os.name != "nt":
            tmp_path.chmod(0o600)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


class FirstStartWizard:
    def __init__(self, path: Path, *, version: str) -> None:
        self.path = path
        self.version = version

    def run(self) -> bool:
        try:
            if _can_use_curses():
                return curses.wrapper(self._run_curses)
        except curses.error:
            pass
        return self._run_line_mode()

    def _run_line_mode(self) -> bool:
        state: dict[str, object] = {}
        step = 0
        while True:
            try:
                if step == 0:
                    choice = self._line_start_choice()
                    if choice == "dummy":
                        ensure_default_config(self.path)
                        self._print_next_steps()
                        return True
                    if choice != "wizard":
                        return False
                    step = 1
                elif step == 1:
                    state["host"] = self._line_host()
                    step = 2
                elif step == 2:
                    library = self._line_select_library(str(state["host"]))
                    state["library"] = library
                    state["onleihe_name"] = self._resolve_onleihe_name(library)
                    step = 3
                elif step == 3:
                    username = _prompt("Username", back=True)
                    password = getpass.getpass("Password (type :back to return): ")
                    if password.strip() == ":back":
                        raise WizardBack()
                    try:
                        summary = self._validate_login(
                            str(state["host"]),
                            state["library"],  # type: ignore[arg-type]
                            str(state["onleihe_name"]),
                            username,
                            password,
                        )
                    except (OnleiheAPIError, OnleiheAuthError) as exc:
                        print(f"Login failed: {exc}")
                        print("Try again or type ':back' as username to return.")
                        continue
                    state["username"] = username
                    state["password"] = password
                    state["summary"] = summary
                    self._print_login_summary(summary)
                    step = 4
                elif step == 4:
                    state["product_ids"] = self._line_product_ids()
                    step = 5
                elif step == 5:
                    state["category_watches"] = self._line_category_watches()
                    step = 6
                elif step == 6:
                    poll_interval = _prompt_float("Watch poll interval seconds", default=300.0, back=True)
                    lendings_interval = _prompt_float(
                        "My-media poll interval seconds (0 disables)",
                        default=21600.0,
                        back=True,
                    )
                    download_dir = _prompt("Download directory", default="~/Downloads/Onleiharr", back=True)
                    remove_drm = _prompt_bool(
                        "Enable DRM removal? This requires legal confirmation",
                        default=False,
                        back=True,
                    )
                    state["poll_interval"] = poll_interval
                    state["lendings_interval"] = lendings_interval
                    state["download_dir"] = download_dir
                    state["remove_drm"] = remove_drm
                    step = 7
                elif step == 7:
                    config = self._config_from_state(state)
                    self._print_summary(config)
                    if not _prompt_bool(f"Write config to {self.path}?", default=True, back=True):
                        return False
                    write_config_atomic(self.path, config)
                    self._print_next_steps()
                    return True
            except WizardBack:
                step = max(0, step - 1)

    def _run_curses(self, screen) -> bool:
        curses.curs_set(0)
        screen.keypad(True)
        state: dict[str, object] = {}
        step = 0
        while True:
            try:
                if step == 0:
                    choice = self._curses_start_choice(screen)
                    if choice == "dummy":
                        ensure_default_config(self.path)
                        self._curses_page(screen, self._next_steps_lines())
                        return True
                    if choice != "wizard":
                        return False
                    step = 1
                elif step == 1:
                    state["host"] = self._curses_host(screen)
                    step = 2
                elif step == 2:
                    library = self._curses_select_library(screen, str(state["host"]))
                    state["library"] = library
                    state["onleihe_name"] = self._resolve_onleihe_name(library)
                    step = 3
                elif step == 3:
                    username = self._curses_input(screen, "Username")
                    password = self._curses_input(screen, "Password", secret=True)
                    try:
                        summary = self._validate_login(
                            str(state["host"]),
                            state["library"],  # type: ignore[arg-type]
                            str(state["onleihe_name"]),
                            username,
                            password,
                        )
                    except (OnleiheAPIError, OnleiheAuthError) as exc:
                        self._curses_page(screen, ["Login failed.", str(exc), "", "Press Enter to retry, Esc back."])
                        continue
                    state["username"] = username
                    state["password"] = password
                    state["summary"] = summary
                    self._curses_login_summary(screen, summary)
                    step = 4
                elif step == 4:
                    raw_products = self._curses_multiline(
                        screen,
                        "Product IDs or product URLs",
                        [
                            "Optional. Add one product ID or URL per line.",
                            "Example:",
                            PRODUCT_EXAMPLE,
                            "Or just: 69b3ed6bc56755bf97cb3b9a",
                            "Empty line continues. Esc goes back.",
                        ],
                    )
                    state["product_ids"] = [item for item in (extract_product_id(value) for value in raw_products) if item]
                    step = 5
                elif step == 5:
                    state["category_watches"] = self._curses_category_watches(screen)
                    step = 6
                elif step == 6:
                    state["poll_interval"] = self._curses_float(screen, "Watch poll interval seconds", default=300.0)
                    state["lendings_interval"] = self._curses_float(
                        screen,
                        "My-media poll interval seconds (0 disables)",
                        default=21600.0,
                    )
                    state["download_dir"] = self._curses_input(
                        screen,
                        "Download directory",
                        default="~/Downloads/Onleiharr",
                    )
                    state["remove_drm"] = self._curses_confirm(
                        screen,
                        "Enable DRM removal? Only if legal for you.",
                        default=False,
                    )
                    step = 7
                elif step == 7:
                    config = self._config_from_state(state)
                    if not self._curses_confirm(screen, self._summary_text(config), default=True):
                        return False
                    write_config_atomic(self.path, config)
                    self._curses_page(screen, self._next_steps_lines())
                    return True
            except WizardBack:
                step = max(0, step - 1)

    def _print_intro(self) -> None:
        print("\n".join(WIZARD_LOGO))
        print(f"Version {self.version}")
        print()
        print("This wizard can create your first local onleiharr.toml.")
        print("If a field is unclear, continue with the suggested defaults and adjust the file later.")
        print("Type ':back' in text prompts to return to the previous screen.")
        print()

    def _line_start_choice(self) -> str:
        self._print_intro()
        print("1. Create config with interactive wizard")
        print("2. Write dummy config template automatically")
        print("3. Cancel")
        choice = _prompt_int("Select", default=1, minimum=1, maximum=3)
        return {1: "wizard", 2: "dummy", 3: "cancel"}[choice]

    def _line_host(self) -> str:
        print()
        print("Enter your Onleihe URL/host manually.")
        print("Example: niedersachsen.onleihe.de or https://niedersachsen.onleihe.de")
        print(f"List of Onleihe URLs: {ONLEIHE_HOSTS_HELP_URL}")
        while True:
            host = normalize_host(_prompt("Onleihe URL/host", back=True))
            if host:
                return host
            print("Please enter a host.")

    def _line_select_library(self, host: str) -> Library:
        client = OnleiheClient(host=host)
        try:
            client.resolve_onleihe_id()
        except Exception as exc:
            print(f"Could not validate host yet: {exc}")
        while True:
            query = _prompt("Search library", back=True)
            try:
                libraries = client.list_libraries(search_value=query, page=1, size=10).libraries
            except OnleiheAPIError as exc:
                print(f"Library search failed: {exc}")
                print("Check the host or type ':back' to return.")
                continue
            if not libraries:
                print("No libraries found.")
                continue
            for idx, library in enumerate(libraries, start=1):
                city = f" ({library.city})" if library.city else ""
                print(f"{idx}. {library.name}{city}")
            choice = _prompt_int("Select number", default=1, minimum=1, maximum=len(libraries), back=True)
            return libraries[choice - 1]

    def _resolve_onleihe_name(self, library: Library) -> str:
        if not library.onleihe_id:
            return _prompt("Onleihe name", default="Onleihe")
        with OnleiheClient(onleihe_id=library.onleihe_id) as client:
            info = client.get_onleihe()
        return info.name or "Onleihe"

    def _validate_login(
        self,
        host: str,
        library: Library,
        onleihe_name: str,
        username: str,
        password: str,
    ) -> AccountSummary:
        with OnleiheClient(
            host=host,
            onleihe_id=library.onleihe_id,
            onleihe_name=onleihe_name,
            library_id=library.id,
            library_name=library.name,
        ) as client:
            client.login(username, password, onleihe_id=library.onleihe_id, library_id=library.id)
            account = client.get_account()
            my_media = client.get_my_media_items(include_player_licences=False)
        return AccountSummary(
            user_id=client.session.user_id,
            lend_current=account.lend_current,
            lend_max=account.lend_max,
            reservation_current=account.reservation_current,
            reservation_max=account.reservation_max,
            my_media_count=len(my_media),
        )

    def _print_login_summary(self, summary: AccountSummary) -> None:
        print()
        print("Login successful.")
        print(f"User ID: {summary.user_id or 'unknown'}")
        print(f"Borrowed media: {_ratio(summary.lend_current, summary.lend_max)}")
        print(f"Reservations: {_ratio(summary.reservation_current, summary.reservation_max)}")
        print(f"My media items: {summary.my_media_count}")

    def _curses_login_summary(self, screen, summary: AccountSummary) -> None:
        self._curses_page(
            screen,
            [
                "Login successful.",
                "",
                f"User ID: {summary.user_id or 'unknown'}",
                f"Borrowed media: {_ratio(summary.lend_current, summary.lend_max)}",
                f"Reservations: {_ratio(summary.reservation_current, summary.reservation_max)}",
                f"My media items: {summary.my_media_count}",
                "",
                "Press Enter to continue, Esc goes back.",
            ],
        )

    def _line_product_ids(self) -> list[str]:
        print()
        print("Product watches are optional. Paste product IDs or product URLs. Empty line finishes.")
        print(f"Example URL: {PRODUCT_EXAMPLE}")
        print("Example ID: 69b3ed6bc56755bf97cb3b9a")
        product_ids: list[str] = []
        while True:
            value = _prompt("Product ID/URL", default="", back=True)
            if not value:
                break
            product_id = extract_product_id(value)
            if product_id and product_id not in product_ids:
                product_ids.append(product_id)
        return product_ids

    def _line_category_watches(self) -> list[dict[str, list[str] | str]]:
        print()
        print("Category watches are optional. Paste browser search URLs with categories=.")
        print(f"Example Sachbuch & Ratgeber URL: {CATEGORY_EXAMPLE}")
        watches: list[dict[str, list[str] | str]] = []
        while _prompt_bool("Add a category watch?", default=False, back=True):
            description = _prompt("Description", default="Category watch", back=True)
            urls = self._line_list("Category URL")
            keywords = self._line_list("Keyword")
            if urls and keywords:
                watches.append({"description": description, "category_urls": urls, "keywords": keywords})
        return watches

    def _line_list(self, label: str) -> list[str]:
        values: list[str] = []
        while True:
            value = _prompt(label, default="", back=True)
            if not value:
                return values
            values.append(value)

    def _print_summary(self, config: WizardConfig) -> None:
        print()
        print(self._summary_text(config).replace("\n\n", "\n"))

    def _summary_text(self, config: WizardConfig) -> str:
        return "\n".join(
            [
                "Summary:",
                f"Host: {config.host}",
                f"Onleihe: {config.onleihe_name}",
                f"Library: {config.library_name}",
                f"Username: {config.username}",
                "Password: ********",
                f"Product watches: {len(config.watch_product_ids)}",
                f"Category watches: {len(config.watch_categories)}",
                f"Poll interval: {config.poll_interval_secs:.1f}s",
                f"My-media poll interval: {config.lendings_poll_interval_secs:.1f}s",
                f"Download directory: {config.download_dir}",
                f"DRM removal: {'enabled' if config.remove_drm else 'disabled'}",
            ]
        )

    def _config_from_state(self, state: dict[str, object]) -> WizardConfig:
        library = state["library"]
        assert isinstance(library, Library)
        remove_drm = bool(state.get("remove_drm", False))
        return WizardConfig(
            host=str(state["host"]),
            onleihe_name=str(state["onleihe_name"]),
            library_name=library.name,
            library_id=library.id,
            onleihe_id=library.onleihe_id,
            username=str(state["username"]),
            password=str(state["password"]),
            watch_product_ids=list(state.get("product_ids", [])),  # type: ignore[arg-type]
            watch_categories=list(state.get("category_watches", [])),  # type: ignore[arg-type]
            poll_interval_secs=float(state.get("poll_interval", 300.0)),
            lendings_poll_interval_secs=float(state.get("lendings_interval", 21600.0)),
            download_dir=str(state.get("download_dir", "~/Downloads/Onleiharr")),
            remove_drm=remove_drm,
            remove_drm_ack="I_UNDERSTAND" if remove_drm else None,
        )

    def _print_next_steps(self) -> None:
        print()
        print("\n".join(self._next_steps_lines()))

    def _next_steps_lines(self) -> list[str]:
        return [
            f"Config written to {self.path}",
            "",
            "Next steps:",
            f"  onleiharr -c {self.path} --once",
            f"  onleiharr -c {self.path} --install-as-user-systemd",
            "  systemctl --user daemon-reload",
            "  systemctl --user enable --now onleiharr",
            "  journalctl --user -u onleiharr -f",
            "",
            "Press Enter to exit.",
        ]

    def _curses_page(self, screen, lines: list[str]) -> None:
        screen.clear()
        for row, line in enumerate(lines[: curses.LINES - 2], start=1):
            screen.addstr(row, 2, line[: curses.COLS - 4])
        screen.refresh()
        while True:
            key = screen.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return
            if key == 27:
                raise WizardBack()

    def _curses_input(self, screen, label: str, *, default: str = "", secret: bool = False) -> str:
        value = default
        while True:
            screen.clear()
            screen.addstr(1, 2, label)
            shown = "*" * len(value) if secret else value
            screen.addstr(3, 2, f"> {shown}")
            screen.addstr(curses.LINES - 2, 2, "Enter accepts, Backspace edits, Esc goes back.")
            screen.refresh()
            key = screen.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return value.strip()
            if key == 27:
                raise WizardBack()
            if key in (curses.KEY_BACKSPACE, 127, 8):
                value = value[:-1]
            elif 32 <= key <= 126:
                value += chr(key)

    def _curses_start_choice(self, screen) -> str:
        options = [
            ("wizard", "Create config with interactive wizard"),
            ("dummy", "Write dummy config template automatically"),
            ("cancel", "Cancel"),
        ]
        selected = 0
        while True:
            screen.clear()
            lines = (
                WIZARD_LOGO
                + [
                    f"Version {self.version}",
                    "",
                    "If a field is unclear, keep moving; sensible defaults are provided.",
                    "",
                ]
            )
            for row, line in enumerate(lines[: curses.LINES - 6], start=1):
                screen.addstr(row, 2, line[: curses.COLS - 4])
            base = min(len(lines) + 1, curses.LINES - 5)
            for idx, (_, label) in enumerate(options):
                marker = ">" if idx == selected else " "
                screen.addstr(base + idx, 2, f"{marker} {label}"[: curses.COLS - 4])
            screen.addstr(curses.LINES - 2, 2, "Arrows select, Enter accepts.")
            screen.refresh()
            key = screen.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return options[selected][0]
            if key == curses.KEY_UP:
                selected = max(0, selected - 1)
            elif key == curses.KEY_DOWN:
                selected = min(len(options) - 1, selected + 1)

    def _curses_host(self, screen) -> str:
        self._curses_page(
            screen,
            [
                "Onleihe host",
                "",
                "Enter your Onleihe URL/host manually.",
                "Example: niedersachsen.onleihe.de",
                "Also accepted: https://niedersachsen.onleihe.de",
                "",
                f"List of Onleihe URLs: {ONLEIHE_HOSTS_HELP_URL}",
                "",
                "Press Enter to continue, Esc goes back.",
            ],
        )
        while True:
            host = normalize_host(self._curses_input(screen, "Onleihe URL/host"))
            if host:
                return host
            self._curses_page(screen, ["Please enter a host.", "", "Press Enter to retry."])

    def _curses_select_library(self, screen, host: str) -> Library:
        client = OnleiheClient(host=host)
        try:
            client.resolve_onleihe_id()
        except Exception:
            pass
        query = ""
        selected = 0
        libraries: list[Library] = []
        while True:
            if query:
                try:
                    libraries = client.list_libraries(search_value=query, page=1, size=8).libraries
                    selected = min(selected, max(len(libraries) - 1, 0))
                except Exception:
                    libraries = []
            screen.clear()
            screen.addstr(1, 2, "Search library")
            screen.addstr(3, 2, f"> {query}")
            for idx, library in enumerate(libraries):
                marker = ">" if idx == selected else " "
                city = f" ({library.city})" if library.city else ""
                screen.addstr(5 + idx, 2, f"{marker} {library.name}{city}"[: curses.COLS - 4])
            screen.addstr(curses.LINES - 2, 2, "Type to search, arrows select, Enter accepts, Esc goes back.")
            screen.refresh()
            key = screen.getch()
            if key in (10, 13, curses.KEY_ENTER) and libraries:
                return libraries[selected]
            if key == 27:
                raise WizardBack()
            if key == curses.KEY_UP:
                selected = max(0, selected - 1)
            elif key == curses.KEY_DOWN:
                selected = min(max(len(libraries) - 1, 0), selected + 1)
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
            elif 32 <= key <= 126:
                query += chr(key)

    def _curses_multiline(self, screen, title: str, help_lines: list[str]) -> list[str]:
        values: list[str] = []
        while True:
            self._curses_page(screen, help_lines + ["", "Press Enter to add values, Esc goes back."])
            value = self._curses_input(screen, f"{title} ({len(values)} added, empty line finishes)")
            if not value:
                return values
            values.append(value)

    def _curses_category_watches(self, screen) -> list[dict[str, list[str] | str]]:
        watches: list[dict[str, list[str] | str]] = []
        while self._curses_confirm(screen, "Add category watch?", default=False):
            description = self._curses_input(screen, "Category description", default="Category watch")
            urls = self._curses_multiline(
                screen,
                "Category URLs",
                ["Paste browser search URLs with categories=.", "Example Sachbuch & Ratgeber:", CATEGORY_EXAMPLE],
            )
            keywords = self._curses_multiline(screen, "Keywords", ["One keyword per line."])
            if urls and keywords:
                watches.append({"description": description, "category_urls": urls, "keywords": keywords})
        return watches

    def _curses_float(self, screen, label: str, *, default: float) -> float:
        while True:
            value = self._curses_input(screen, label, default=f"{default:.1f}")
            try:
                return float(value)
            except ValueError:
                self._curses_page(screen, ["Invalid number.", "Press Enter to retry."])

    def _curses_confirm(self, screen, question: str, *, default: bool) -> bool:
        suffix = "Y/n" if default else "y/N"
        while True:
            screen.clear()
            for idx, line in enumerate(question.splitlines(), start=1):
                screen.addstr(idx, 2, line[: curses.COLS - 4])
            screen.addstr(curses.LINES - 2, 2, f"{suffix}: ")
            screen.refresh()
            key = screen.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return default
            if key == 27:
                raise WizardBack()
            if key in (ord("y"), ord("Y")):
                return True
            if key in (ord("n"), ord("N")):
                return False


def _prompt(label: str, *, default: str | None = None, back: bool = False) -> str:
    suffix = f" [{default}]" if default is not None and default != "" else ""
    value = input(f"{label}{suffix}: ").strip()
    if back and value == ":back":
        raise WizardBack()
    if value:
        return value
    return default or ""


def _prompt_float(label: str, *, default: float, back: bool = False) -> float:
    while True:
        value = _prompt(label, default=f"{default:.1f}", back=back)
        try:
            return float(value)
        except ValueError:
            print("Please enter a number.")


def _prompt_int(label: str, *, default: int, minimum: int, maximum: int, back: bool = False) -> int:
    while True:
        value = _prompt(label, default=str(default), back=back)
        try:
            parsed = int(value)
        except ValueError:
            parsed = -1
        if minimum <= parsed <= maximum:
            return parsed
        print(f"Please enter a number between {minimum} and {maximum}.")


def _prompt_bool(label: str, *, default: bool, back: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().casefold()
        if back and value == ":back":
            raise WizardBack()
        if not value:
            return default
        if value in {"y", "yes", "j", "ja"}:
            return True
        if value in {"n", "no", "nein"}:
            return False
        print("Please answer yes or no.")


def _ratio(current: int | None, maximum: int | None) -> str:
    if current is None and maximum is None:
        return "unknown"
    if maximum is None:
        return str(current)
    return f"{current or 0}/{maximum}"


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _can_use_curses() -> bool:
    return curses is not None and sys.stdin.isatty() and sys.stdout.isatty() and os.getenv("TERM") not in {None, "", "dumb"}
