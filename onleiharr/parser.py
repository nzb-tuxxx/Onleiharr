from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterator

import requests
from bs4 import BeautifulSoup, Tag

from onleiharr.http import DEFAULT_HEADERS, DEFAULT_TIMEOUT_SECS
from onleiharr.models import Book, Magazine, Media, MyBibLending

logger = logging.getLogger(__name__)


def get_card_media_info_href(card: Tag | BeautifulSoup) -> str | None:
    link = card.select_one('a[test-id="mediaInfoLink"][href]') or card.select_one("a.stretched-link[href]")
    href = link.get("href") if link else None
    return str(href) if href else None


def get_card_title(card: Tag | BeautifulSoup) -> str | None:
    title_tag = card.select_one('h3[test-id="cardTitle"]') or card.select_one("h3.headline")
    if not title_tag:
        return None
    return title_tag.get_text(strip=True)


def extract_card_title_and_link(card: Tag | BeautifulSoup) -> tuple[str, str]:
    title = get_card_title(card)
    href = get_card_media_info_href(card)
    if not title or not href:
        raise ValueError("Media card missing title or detail link")
    return title, href


def extract_acsm_url_from_card(card: Tag) -> str | None:
    link = card.select_one('a[title="Download"][href*=".acsm"]')
    if not link:
        link = card.select_one('a[aria-label="Download"][href*=".acsm"]')
    if not link:
        link = card.find("a", href=lambda value: value and ".acsm" in value.lower())
    if not link:
        return None
    href = link.get("href")
    return str(href) if href else None


def parse_my_bib_lendings(html: str) -> list[MyBibLending]:
    soup = BeautifulSoup(html, "html.parser")
    by_id: dict[int, MyBibLending] = {}

    for card in soup.select('div[test-id="mediaCard"]'):
        href = get_card_media_info_href(card)
        if not href:
            continue

        media_id = Media.parse_id_from_href(href)
        if media_id is None:
            continue

        title = get_card_title(card) or ""
        acsm_url = extract_acsm_url_from_card(card)

        candidate = MyBibLending(
            media_id=media_id,
            title=title,
            media_info_href=href,
            acsm_url=acsm_url,
        )
        existing = by_id.get(media_id)
        if existing is None:
            by_id[media_id] = candidate
        else:
            # If the same media id appears multiple times (e.g., in reservations and lendings),
            # prefer the entry that actually contains an ACSM download link.
            if existing.acsm_url is None and candidate.acsm_url is not None:
                by_id[media_id] = candidate

    return list(by_id.values())


def extract_book_info(book_element: Tag | BeautifulSoup, library: str) -> Book:
    author_element = book_element.find('p', {'test-id': 'cardAuthor'})
    date_element = book_element.find('small', {'test-id': 'cardInsertDate'})

    if not author_element or not date_element:
        raise ValueError('Book element missing expected child nodes')

    title, link = extract_card_title_and_link(book_element)
    author = author_element.get_text(strip=True).replace('\xa0', ' ')

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
    availability_node = magazine_element.select_one('[test-id="cardAvailability"]')

    if not availability_node:
        raise ValueError('Magazine element missing expected child nodes')

    title, link = extract_card_title_and_link(magazine_element)
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


def fetch_media(
    url: str,
    elements: int = 50,
    timeout: int = DEFAULT_TIMEOUT_SECS,
    session: requests.Session | None = None,
) -> Iterator[Media]:
    data = {'elementsPerPage': str(elements)}

    if session is None:
        response = requests.post(url, data=data, timeout=timeout, headers=DEFAULT_HEADERS)
    else:
        response = session.post(url, data=data, timeout=timeout, headers=DEFAULT_HEADERS)
    response.raise_for_status()

    library = url.split('/')[3]

    soup = BeautifulSoup(response.content, 'html.parser')
    media_containers = soup.find_all('div', class_='card')

    for container in media_containers:
        try:
            if container.find('p', {'test-id': 'cardAuthor'}):
                yield extract_book_info(container, library)
            else:
                yield extract_magazine_info(container, library)
        except Exception as exc:
            logger.warning("Skipping media item due to parse error: %s", exc, exc_info=True)
