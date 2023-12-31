import requests
from bs4 import BeautifulSoup
from data_extraction import Media
from functools import wraps


def handle_exceptions(exception_types=(Exception,), default_value=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_types as e:
                print(f"An error of type {type(e).__name__} occurred: {e}")
                return default_value
        return wrapper
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

        form_url = form['action']
        form_data = {input_tag['name']: input_tag.get('value', '') for input_tag in
                     form.find_all('input', {'name': True})}

        # Add username and password to form data
        form_data['userName'] = self.username
        form_data['password'] = self.password

        # Step 3: Send the POST request
        response_post = self.session.post(form_url, data=form_data)
        response_post.raise_for_status()  # Ensure the request was successful

        # Check if login was successful
        soup_post = BeautifulSoup(response_post.text, 'html.parser')
        error_message = soup_post.find('span', string="danger: ")
        success_message = soup_post.find('h3', class_='headline my-4', string="Ihr Benutzerkonto")

        if error_message:
            raise LoginError("The login attempt was unsuccessful. Please check your login details and try again.")
        elif not success_message:
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

        # Check for errors in response
        soup = BeautifulSoup(response.text, 'html.parser')
        error_message = soup.find('p', class_='text-center mb-0',
                                  string="Ein unerwarteter Fehler ist aufgetreten! Bitte versuchen Sie es später noch einmal.")
        if error_message:
            raise RentError("An unexpected error occurred while trying to rent the media. Please try again later.")

        return response.text

    @handle_exceptions(exception_types=(requests.RequestException, ReserveError))
    def reserve_media(self, media: Media, email: str = None, login: bool = True):
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

        # Check for errors in response
        soup = BeautifulSoup(response.text, 'html.parser')
        error_message = soup.find('p', class_='text-center mb-0',
                                  string="Ein unerwarteter Fehler ist aufgetreten! Bitte versuchen Sie es später noch einmal.")
        if error_message:
            raise ReserveError("An unexpected error occurred while trying to reserve the media. Please try again later.")

        return response.text
