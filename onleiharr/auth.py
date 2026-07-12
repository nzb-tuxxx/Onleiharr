from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from onleiharr._vendor.onleihe import OnleiheAuthError, OnleiheClient, SessionState


def default_session_path(config_path: Path) -> Path:
    return config_path.with_name("session.json")


def load_session(path: Path) -> SessionState:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OnleiheAuthError(
            f"Could not load external login session from {path}: {exc}"
        ) from exc
    allowed = set(SessionState.__dataclass_fields__)
    return SessionState(**{key: value for key, value in data.items() if key in allowed})


def save_session(path: Path, session: SessionState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(session), handle, separators=(",", ":"))
        if os.name != "nt":
            temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _complete_external_login(
    client: OnleiheClient,
    *,
    callback_url: str,
    expected_state: str,
    redirect_url: str,
) -> SessionState:
    parsed = urlparse(callback_url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != client.host.casefold():
        raise OnleiheAuthError(
            "Redirect URL does not belong to the configured Onleihe host"
        )
    query = parse_qs(parsed.query)
    if query.get("state", [None])[0] != expected_state:
        raise OnleiheAuthError("OpenID redirect state does not match")
    code = query.get("code", [None])[0]
    if not code:
        error = query.get(
            "error_description", query.get("error", ["missing authorization code"])
        )[0]
        raise OnleiheAuthError(f"OpenID authorization failed: {error}")
    return client.login_open_id(code, redirect_url=redirect_url)


def _external_login_request(client: OnleiheClient) -> tuple[str, str, str]:
    method = client.get_login_method()
    if method.get("loginType") != "OPEN_ID":
        raise OnleiheAuthError(
            f"Library login type is {method.get('loginType')!r}, not OPEN_ID"
        )
    redirect_url = f"https://{client.host}"
    expected_state = secrets.token_urlsafe(24)
    authorization_url = client.build_open_id_authorization_url(
        method, redirect_url=redirect_url, state=expected_state
    )
    return authorization_url, redirect_url, expected_state


def external_login_manual(client: OnleiheClient, *, input_func=input) -> SessionState:
    authorization_url, redirect_url, expected_state = _external_login_request(client)
    print("Open the following URL in a browser on your local workstation.")
    print("Before logging in, disable JavaScript for this browser tab (for example through DevTools).")
    print("This prevents the Onleihe web app from consuming the one-time authorization code.")
    print(authorization_url)
    callback_url = input_func(
        "After the redirect, paste the complete https://...onleihe.de/?code=... URL: "
    ).strip()
    return _complete_external_login(
        client,
        callback_url=callback_url,
        expected_state=expected_state,
        redirect_url=redirect_url,
    )


def external_login_browser(
    client: OnleiheClient,
    *,
    timeout_secs: float = 300.0,
) -> SessionState:
    authorization_url, redirect_url, expected_state = _external_login_request(client)
    print("Open this URL in a browser and complete the library login:")
    print(authorization_url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OnleiheAuthError(
            "External login needs Playwright. Install with: pipx inject onleiharr playwright"
        ) from exc
    executable = next(
        (
            path
            for name in ("chromium", "chromium-browser", "google-chrome", "chrome")
            if (path := shutil.which(name))
        ),
        None,
    )
    if executable is None:
        raise OnleiheAuthError("External login needs Chromium or Google Chrome")
    callback_url: str | None = None
    deadline = time.monotonic() + timeout_secs
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=False)
        page = browser.new_page()

        def intercept(route) -> None:
            nonlocal callback_url
            candidate = route.request.url
            parsed = urlparse(candidate)
            if parsed.netloc.casefold() == client.host.casefold() and (
                "code" in parse_qs(parsed.query) or "error" in parse_qs(parsed.query)
            ):
                callback_url = candidate
                route.abort()
                return
            route.continue_()

        page.route("**/*", intercept)
        page.goto(authorization_url)
        while callback_url is None and time.monotonic() < deadline:
            page.wait_for_timeout(200)
        browser.close()
    if callback_url is None:
        raise OnleiheAuthError("Timed out waiting for the external login callback")
    return _complete_external_login(
        client,
        callback_url=callback_url,
        expected_state=expected_state,
        redirect_url=redirect_url,
    )
