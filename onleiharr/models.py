from __future__ import annotations

import re
from abc import ABC
from dataclasses import dataclass
from datetime import date


_MEDIA_ID_CANDIDATE_RE = re.compile(r"0-0-([0-9]+)-")
_MIN_MEDIA_ID_DIGITS = 8


@dataclass
class Media(ABC):
    link: str
    title: str
    format: str  # 'audio', 'ebook', 'emagazine'
    library: str
    available: bool
    availability_date: date

    @property
    def full_url(self) -> str:
        return f"https://www.onleihe.de/{self.library}/frontend/{self.link}"

    @staticmethod
    def parse_id_from_href(href: str) -> int | None:
        if not href:
            return None

        def _valid(candidate: str) -> bool:
            if not candidate.isdigit():
                return False
            # IDs with leading zeros are never valid media IDs.
            if candidate.startswith("0"):
                return False
            # Media IDs are long numeric ids; require a minimum length to avoid picking up
            # unrelated short numeric segments (e.g., command ids, years, etc.).
            return len(candidate) >= _MIN_MEDIA_ID_DIGITS

        # Most Onleihe hrefs look like: "<cmd>,0-0-<media_id>-...".
        if "," in href:
            tail = href.split(",", 1)[1]
            parts = tail.split("-")
            if len(parts) > 2 and _valid(parts[2]):
                return int(parts[2])

        # Fallback: search for an "0-0-<digits>-" segment anywhere in the href.
        for match in _MEDIA_ID_CANDIDATE_RE.finditer(href):
            candidate = match.group(1)
            if _valid(candidate):
                return int(candidate)

        return None

    @property
    def id(self) -> int:
        media_id = self.parse_id_from_href(self.link)
        if media_id is None:
            raise ValueError(f"Unable to parse media id from link: {self.link}")
        return media_id


@dataclass(frozen=True)
class MyBibLending:
    """Lightweight representation of an entry on 'Mein Konto' (myBib) pages."""

    media_id: int
    title: str
    media_info_href: str | None
    acsm_url: str | None
    lend_href: str | None


@dataclass
class Book(Media):
    _author: str
    description: str | None
    insert_date: date

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Media):
            return NotImplemented
        return self.id == other.id

    @property
    def author(self) -> str:
        return self._author.replace("\n", " ")

    @author.setter
    def author(self, value: str) -> None:
        self._author = value


@dataclass
class Magazine(Media):
    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Media):
            return NotImplemented
        return self.id == other.id
