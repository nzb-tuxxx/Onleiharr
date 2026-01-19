from __future__ import annotations

from datetime import date, datetime
from typing import Iterator

import requests
from bs4 import BeautifulSoup, Tag

from onleiharr.models import Book, Magazine, Media


def extract_book_info(book_element: Tag | BeautifulSoup, library: str) -> Book:
    author_element = book_element.find('p', {'test-id': 'cardAuthor'})
    title_element = book_element.find('h3', {'test-id': 'cardTitle'})
    link_element = book_element.find('a', {'test-id': 'mediaInfoLink'})
    date_element = book_element.find('small', {'test-id': 'cardInsertDate'})

    if not author_element or not title_element or not link_element or not date_element:
        raise ValueError('Book element missing expected child nodes')

    author = author_element.get_text(strip=True).replace('\xa0', ' ')
    title = title_element.get_text(strip=True)
    link_attr = link_element.get('href')
    if link_attr is None:
        raise ValueError('Book link missing href')
    link = str(link_attr)

    span_element = date_element.find('span')
    if not span_element:
        raise ValueError('Book insert date missing span element')
    insert_date_str = span_element.get_text(strip=True)
    insert_date = datetime.strptime(insert_date_str, '%d.%m.%Y').date()

    description_element = book_element.find('p', {'test-id': 'cardAbstract'})
    description = description_element.get_text(strip=True).replace('\xa0', ' ') if description_element else None

    book_format = 'audio' if book_element.find('svg', {'test-id': 'ic_eaudio'}) else 'ebook'

    availability_element = book_element.find('span', {'test-id': 'cardAvailability'})
    if availability_element and availability_element.get_text(strip=True):
        availability_date = datetime.strptime(availability_element.get_text(strip=True), '%d.%m.%Y').date()
        available = False
    else:
        availability_date = date.today()
        available = True

    return Book(
        link=link,
        title=title,
        format=book_format,
        library=library,
        available=available,
        availability_date=availability_date,
        _author=author,
        description=description,
        insert_date=insert_date,
    )


def extract_magazine_info(magazine_element: Tag | BeautifulSoup, library: str) -> Magazine:
    title_element = magazine_element.find('h3', {'test-id': 'cardTitle'})
    link_element = magazine_element.find('a', {'test-id': 'mediaInfoLink'})
    availability_node = magazine_element.select_one('[test-id="cardAvailability"]')

    if not title_element or not link_element or not availability_node:
        raise ValueError('Magazine element missing expected child nodes')

    title = title_element.get_text(strip=True)
    link_attr = link_element.get('href')
    if link_attr is None:
        raise ValueError('Magazine link missing href')
    link = str(link_attr)
    availability_date_text = availability_node.get_text(strip=True)

    if "Verfügbar" in availability_date_text:
        availability_date = date.today()
        available = True
    elif "Ausgeliehen" in availability_date_text:
        availability_date = date(1970, 1, 1)
        available = False
    else:
        availability_date_str = availability_date_text.split('Voraussichtlich verfügbar ab:\xa0')[-1].strip()
        availability_date = datetime.strptime(availability_date_str, '%d.%m.%Y').date()
        available = False

    return Magazine(
        link=link,
        title=title,
        format='emagazine',
        library=library,
        available=available,
        availability_date=availability_date,
    )


def fetch_media(url: str, elements: int = 50, timeout: int = 10) -> Iterator[Media]:
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
