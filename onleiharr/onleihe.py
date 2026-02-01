from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Tuple, TypeVar
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from onleiharr.http import DEFAULT_HEADERS, DEFAULT_TIMEOUT_SECS
from onleiharr.models import Media

logger = logging.getLogger(__name__)


F = TypeVar('F', bound=Callable[..., object])


def handle_exceptions(
    exception_types: Tuple[type[Exception], ...] = (Exception,),
    default_value: object | None = None,
    max_retries: int = 3,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempts = max_retries + 1
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except exception_types as exc:  # type: ignore[misc]
                    if attempt < max_retries:
                        # Basic exponential backoff with jitter to avoid hammering Onleihe on flaky networks.
                        delay_secs = min(30.0, (2.0**attempt)) + random.random()
                        logger.warning(
                            "Attempt %d/%d failed: %s - %s. Retrying in %.1fs...",
                            attempt + 1,
                            attempts,
                            type(exc).__name__,
                            exc,
                            delay_secs,
                        )
                        time.sleep(delay_secs)
                    else:
                        logger.error(
                            "Attempt %d/%d failed: %s - %s. Returning default value.",
                            attempt + 1,
                            attempts,
                            type(exc).__name__,
                            exc,
                            exc_info=logger.isEnabledFor(logging.DEBUG),
                        )
            return default_value

        return wrapper  # type: ignore[return-value]

    return decorator


class LoginError(Exception):
    """Exception raised when login fails."""
    pass


class RentError(Exception):
    """Exception raised when renting a media fails."""
    pass


class ReserveError(Exception):
    """Exception raised when reserving a media fails."""
    pass


@dataclass(frozen=True)
class RentResult:
    html: str
    acsm_url: str | None


def extract_acsm_url(html: str) -> str | None:
    soup = BeautifulSoup(html, 'html.parser')
    link = soup.select_one('a[title="Download"][href*=".acsm"]')
    if not link:
        link = soup.select_one('a[aria-label="Download"][href*=".acsm"]')
    if not link:
        link = soup.find('a', href=lambda value: value and '.acsm' in value.lower())
    if not link:
        return None
    href = link.get('href')
    return str(href) if href else None


class Onleihe:
    def __init__(
        self,
        library: str,
        library_id: int,
        username: str,
        password: str,
        timeout: int = DEFAULT_TIMEOUT_SECS,
    ):
        # Create a session to be used for all requests
        self.library = library
        self.library_id = library_id
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.timeout = timeout

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        return session

    def _close_session(self, session: requests.Session) -> None:
        try:
            session.close()
        except Exception:
            # Best-effort close; ignore errors during teardown.
            pass

    @handle_exceptions(exception_types=(requests.RequestException, LoginError))
    def login(self, session: requests.Session | None = None):
        target_session = session or self.session
        # URL of the page with the login form
        url = f'https://www.onleihe.de/{self.library}/frontend/login,0-0-0-800-0-0-0-0-0-0-0.html?libraryId={self.library_id}'

        # Step 1: Fetch the page
        response = target_session.get(url, timeout=self.timeout)
        response.raise_for_status()  # Ensure the request was successful

        # Step 2: Parse the HTML and extract form information
        soup = BeautifulSoup(response.text, 'html.parser')
        form = soup.find('form', id='loginForm')

        if not form:
            raise LoginError("Login form could not be found.")

        action = form.get('action')
        if not action:
            raise LoginError("Login form action URL could not be found.")
        # Onleihe sometimes returns a relative form action; resolve it against the fetched page URL.
        form_url = urljoin(response.url, str(action))
        form_data = {input_tag['name']: input_tag.get('value', '') for input_tag in
                     form.find_all('input', {'name': True})}

        # Add username and password to form data
        form_data['userName'] = self.username
        form_data['password'] = self.password

        # Step 3: Send the POST request
        response_post = target_session.post(form_url, data=form_data, timeout=self.timeout)
        response_post.raise_for_status()  # Ensure the request was successful

        # Check if login was successful
        soup_post = BeautifulSoup(response_post.text, 'html.parser')

        # Some Onleihe variants show login errors as a "danger:" span and keep the login form.
        danger_text: str | None = None
        for span in soup_post.find_all('span'):
            if not isinstance(span, Tag):
                continue
            text = span.get_text(strip=True)
            if text.startswith("danger:"):
                danger_text = text
                break

        if soup_post.find('form', id='loginForm'):
            raise LoginError(
                danger_text
                or "The login attempt was unsuccessful. Please check your login details and try again."
            )

        # Not all variants include a stable "success" marker; if we are not on the login page
        # anymore, treat this as a successful login.
        success_message = soup_post.find('h3', class_='headline my-4')
        if not success_message:
            logger.debug("Login POST did not contain an explicit success marker; proceeding anyway.")

        # Return the response
        return response_post.text

    @handle_exceptions(exception_types=(requests.RequestException, RentError))
    def rent_media(self, media: Media, lend_period: int = 2, login: bool = True) -> RentResult | None:
        if login:
            self.login()

        rent_url = f"https://www.onleihe.de/{self.library}/frontend/mediaLend,0-0-{media.id}-303-0-0-0-0-0-0-0.html"

        data = {
            'pVersionId': str(media.id),
            'pLendPeriod': str(lend_period)
        }

        response = self.session.post(rent_url, data=data, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        error_paragraph = soup.find('p', class_='text-center mb-0')
        if error_paragraph and "unerwarteter Fehler" in error_paragraph.get_text():
            raise RentError("An unexpected error occurred while trying to rent the media. Please try again later.")

        acsm_url = extract_acsm_url(response.text)
        return RentResult(html=response.text, acsm_url=acsm_url)

    @handle_exceptions(exception_types=(requests.RequestException, ReserveError))
    def reserve_media(self, media: Media, email: str | None = None, login: bool = True):
        if login:
            self.login()

        reserve_url = f"https://www.onleihe.de/{self.library}/frontend/mediaReserve,0-0-0-1003-0-0-0-0-0-0-0.html"

        data = {
            'mvId': str(media.id),
        }
        if email:
            data['pRecipient'] = email
            data['pConfirmedRecipient'] = email

        response = self.session.post(reserve_url, data=data, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        error_paragraph = soup.find('p', class_='text-center mb-0')
        if error_paragraph and "unerwarteter Fehler" in error_paragraph.get_text():
            raise ReserveError("An unexpected error occurred while trying to reserve the media. Please try again later.")

        return response.text

    @handle_exceptions(exception_types=(requests.RequestException,), default_value=None)
    def fetch_acsm(self, url: str) -> bytes | None:
        normalized = self._normalize_url(url)
        response = self.session.get(normalized, timeout=self.timeout)
        response.raise_for_status()
        return response.content

    @handle_exceptions(exception_types=(requests.RequestException,), default_value=None)
    def fetch_my_bib_lendings(self, login: bool = True) -> str | None:
        session = self._new_session()
        try:
            if login:
                self.login(session=session)
            url = (
                f"https://www.onleihe.de/{self.library}/frontend/"
                "myBib,0-0-0-100-0-0-0-0-0-0-0.html"
            )
            response = session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.text
        finally:
            self._close_session(session)

    def _normalize_url(self, href: str) -> str:
        href = (href or "").strip()
        if href.startswith("http://") or href.startswith("https://"):
            return href
        if href.startswith("/"):
            return urljoin("https://www.onleihe.de", href)
        # Relative links in Onleihe HTML are usually relative to the frontend base.
        return urljoin(f"https://www.onleihe.de/{self.library}/frontend/", href)
