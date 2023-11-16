import requests
from abc import ABC
from bs4 import BeautifulSoup
from dataclasses import dataclass
from datetime import datetime, date


@dataclass
class Media(ABC):
    link: str
    title: str
    format: str  # 'audio', 'ebook', 'emagazine'
    library: str
    available: bool
    availability_date: date

    @property
    def full_url(self):
        return f"https://www.onleihe.de/{self.library}/frontend/{self.link}"

    @property
    def id(self):
        return int(self.link.split('-')[2])


@dataclass
class Book(Media):
    _author: str
    description: str
    insert_date: date

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, Media):
            return NotImplemented
        return self.id == other.id

    @property
    def author(self):
        return self._author.replace("\n", " ")

    @author.setter
    def author(self, value):
        self._author = value


@dataclass
class Magazine(Media):
    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, Media):
            return NotImplemented
        return self.id == other.id


def extract_book_info(book_element: BeautifulSoup, library: str) -> Book:
    author_element = book_element.find('p', {'test-id': 'cardAuthor'})
    author = author_element.text.strip().replace('\xa0', ' ')

    title_element = book_element.find('h3', {'test-id': 'cardTitle'})
    title = title_element.text.strip()

    link_element = book_element.find('a', {'test-id': 'mediaInfoLink'})
    link = link_element['href']

    description_element = book_element.find('p', {'test-id': 'cardAbstract'})
    if description_element:
        description = description_element.text.strip().replace('\xa0', ' ')
    else:
        description = None

    date_element = book_element.find('small', {'test-id': 'cardInsertDate'}).find('span')
    insert_date_str = date_element.text.strip()
    insert_date = datetime.strptime(insert_date_str, '%d.%m.%Y').date()

    if book_element.find('svg', {'test-id': 'ic_eaudio'}):
        book_format = 'audio'
    else:
        book_format = 'ebook'

    try:
        availability_date_element = book_element.find('span', {'test-id': 'cardAvailability'}).text.strip()
        availability_date = datetime.strptime(availability_date_element, '%d.%m.%Y').date()
        available = False
    except AttributeError:
        availability_date = date.today()
        available = True

    return Book(link, title, book_format, library, available, availability_date, author, description, insert_date)


def extract_magazine_info(magazine_element: BeautifulSoup, library: str) -> Magazine:
    title_element = magazine_element.find('h3', {'test-id': 'cardTitle'})
    title = title_element.text.strip()

    link_element = magazine_element.find('a', {'test-id': 'mediaInfoLink'})
    link = link_element['href']

    availability_date_element = magazine_element.select_one('[test-id="cardAvailability"]').text.strip()

    if "Verfügbar" in availability_date_element:
        availability_date = date.today()
        available = True
    else:
        availability_date_str = availability_date_element.split('Voraussichtlich verfügbar ab:\xa0')[-1].strip()
        availability_date = datetime.strptime(availability_date_str, '%d.%m.%Y').date()
        available = False

    return Magazine(link, title, 'emagazine', library, available, availability_date)


def get_media_from_onleihe(url: str, elements: int = 50, timeout: int = 10):
    data = {'elementsPerPage': str(elements)}

    response = requests.post(url, data=data, timeout=timeout)
    response.raise_for_status()

    library = url.split('/')[3]

    soup = BeautifulSoup(response.content, 'html.parser')
    media_containers = soup.find_all('div', class_='card')

    for container in media_containers:
        if container.find('p', {'test-id': 'cardAuthor'}):
            yield extract_book_info(container, library)
        else:
            yield extract_magazine_info(container, library)