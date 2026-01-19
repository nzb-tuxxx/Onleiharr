from __future__ import annotations

import logging
from functools import wraps
from typing import Callable, Tuple, TypeVar

import requests
from bs4 import BeautifulSoup, Tag

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
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exception_types as exc:  # type: ignore[misc]
                    if attempt < max_retries:
                        logger.warning(
                            "Attempt %s failed: %s - %s. Retrying...", attempt + 1, type(exc).__name__, exc
                        )
                    else:
                        logger.error("All %s attempts failed. Returning default value.", max_retries)
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


class Onleihe:
    def __init__(self, library: str, library_id: int, username: str, password: str, timeout: int = 10):
        # Create a session to be used for all requests
        self.library = library
        self.library_id = library_id
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.timeout = timeout

    @handle_exceptions(exception_types=(requests.RequestException, LoginError))
    def login(self):
        # URL of the page with the login form
        url = f'https://www.onleihe.de/{self.library}/frontend/login,0-0-0-800-0-0-0-0-0-0-0.html?libraryId={self.library_id}'

        # Step 1: Fetch the page
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()  # Ensure the request was successful

        # Step 2: Parse the HTML and extract form information
        soup = BeautifulSoup(response.text, 'html.parser')
        form = soup.find('form', id='loginForm')

        if not form:
            raise LoginError("Login form could not be found.")

        form_url = str(form['action'])
        form_data = {input_tag['name']: input_tag.get('value', '') for input_tag in
                     form.find_all('input', {'name': True})}

        # Add username and password to form data
        form_data['userName'] = self.username
        form_data['password'] = self.password

        # Step 3: Send the POST request
        response_post = self.session.post(form_url, data=form_data, timeout=self.timeout)
        response_post.raise_for_status()  # Ensure the request was successful

        # Check if login was successful
        soup_post = BeautifulSoup(response_post.text, 'html.parser')
        error_message = soup_post.find('span')
        success_message = soup_post.find('h3', class_='headline my-4')

        if error_message and isinstance(error_message, Tag) and error_message.get_text(strip=True).startswith("danger:"):
            raise LoginError("The login attempt was unsuccessful. Please check your login details and try again.")
        if not success_message:
            raise LoginError("Unable to determine if the login was successful.")

        # Return the response
        return response_post.text

    @handle_exceptions(exception_types=(requests.RequestException, RentError))
    def rent_media(self, media: Media, lend_period: int = 2, login: bool = True):
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

        return response.text

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
