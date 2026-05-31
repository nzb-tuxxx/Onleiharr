from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JSON = dict[str, Any]


@dataclass(slots=True)
class SessionState:
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: int | None = None
    user_id: str | None = None
    profile_id: str = "master"
    library_id: str | None = None
    onleihe_id: str | None = None
    username: str | None = None
    roles: list[str] = field(default_factory=list)
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class Library:
    id: str
    name: str
    onleihe_id: str | None = None
    city: str | None = None
    postal_code: str | None = None
    street: str | None = None
    house_number: str | None = None
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class LibraryPage:
    libraries: list[Library]
    page: int | None = None
    size: int | None = None
    total_items: int | None = None
    total_pages: int | None = None
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class OnleiheInfo:
    id: str | None
    name: str | None
    languages: list[JSON] = field(default_factory=list)
    additional_links: list[JSON] = field(default_factory=list)
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class AccountInfo:
    lend_current: int | None
    lend_max: int | None
    reservation_current: int | None
    reservation_max: int | None
    watchlist_count: int | None
    lend_restrictions: JSON
    patron_name: str | None
    patron_library_number: str | None
    patron_email: str | None
    library_name: str | None
    ereader_code: str | None
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class MessageCount:
    inbox: int | None
    unread: int | None
    archive: int | None
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class Message:
    id: str | None
    title: str | None
    text: str | None
    read: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class MediaItem:
    id: str | None
    product_id: str | None
    title: str | None
    subtitle: str | None
    media_type: str | None
    lend_id: str | None = None
    licence_url: str | None = None
    acsm_url: str | None = None
    acs_drm_uri: str | None = None
    care_drm_uri: str | None = None
    pdf_url: str | None = None
    epub_url: str | None = None
    authors: list[str] = field(default_factory=list)
    publisher: str | None = None
    publication_date: str | None = None
    covers: list[JSON] = field(default_factory=list)
    categories: list[JSON] = field(default_factory=list)
    availability: JSON = field(default_factory=dict)
    user_actions: list[JSON] = field(default_factory=list)
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class ProductDetails(MediaItem):
    included_media: list[MediaItem] = field(default_factory=list)


@dataclass(slots=True)
class SearchResultPage:
    items: list[MediaItem]
    total_items: int | None = None
    facets: list[JSON] = field(default_factory=list)
    raw: JSON = field(default_factory=dict)


@dataclass(slots=True)
class JobStatus:
    id: str | None
    completed: bool | None
    last_update: str | None = None
    completed_at: str | None = None
    state: str | None = None
    licence_url: str | None = None
    acsm_url: str | None = None
    acs_drm_uri: str | None = None
    care_drm_uri: str | None = None
    pdf_url: str | None = None
    epub_url: str | None = None
    api_error: JSON | None = None
    raw: JSON = field(default_factory=dict)
