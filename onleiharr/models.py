from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from datetime import date


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

    @property
    def id(self) -> int:
        return int(self.link.split('-')[2])


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
