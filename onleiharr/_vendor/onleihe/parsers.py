from __future__ import annotations

from typing import Any

from .models import (
    AccountInfo,
    JobStatus,
    Library,
    LibraryPage,
    MediaItem,
    Message,
    MessageCount,
    OnleiheInfo,
    ProductDetails,
    SearchResultPage,
    SessionState,
)


def _get(data: dict[str, Any], *path: str, default=None):
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def parse_session(payload: dict[str, Any], *, username: str | None = None) -> SessionState:
    access_token = payload.get("accessToken") or payload.get("access_token") or payload.get("token")
    refresh_token = payload.get("refreshToken") or payload.get("refresh_token")
    claims = _jwt_claims(access_token)
    return SessionState(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=payload.get("tokenType") or payload.get("token_type") or "Bearer",
        expires_at=claims.get("exp"),
        user_id=payload.get("userId") or payload.get("uid") or claims.get("uid"),
        profile_id=payload.get("profileId") or claims.get("pid") or "master",
        library_id=payload.get("libraryId") or claims.get("lid"),
        onleihe_id=payload.get("onleiheId") or claims.get("oid"),
        username=username or claims.get("sub"),
        roles=list(payload.get("roles") or []),
        raw=payload,
    )


def parse_library(data: dict[str, Any]) -> Library:
    address = data.get("address") if isinstance(data.get("address"), dict) else {}
    return Library(
        id=str(data.get("id")),
        name=data.get("name") or data.get("libraryName") or "",
        onleihe_id=data.get("onleiheId"),
        city=data.get("city") or address.get("city"),
        postal_code=data.get("postalCode") or address.get("postalCode"),
        street=data.get("street") or address.get("street"),
        house_number=data.get("houseNumber") or address.get("houseNumber"),
        raw=data,
    )


def parse_library_page(payload: dict[str, Any]) -> LibraryPage:
    content = payload.get("content") if isinstance(payload, dict) else None
    if content is None and isinstance(payload, list):
        content = payload
    return LibraryPage(
        libraries=[parse_library(item) for item in content or []],
        page=payload.get("page") if isinstance(payload, dict) else None,
        size=payload.get("size") if isinstance(payload, dict) else None,
        total_items=payload.get("totalItems") if isinstance(payload, dict) else None,
        total_pages=payload.get("totalPages") if isinstance(payload, dict) else None,
        raw=payload if isinstance(payload, dict) else {"content": payload},
    )


def parse_onleihe_info(payload: dict[str, Any]) -> OnleiheInfo:
    return OnleiheInfo(
        id=payload.get("id") or payload.get("onleiheId"),
        name=payload.get("name") or payload.get("onleiheName"),
        languages=list(payload.get("languages") or []),
        additional_links=list(payload.get("additionalLinks") or []),
        raw=payload,
    )


def parse_account(payload: dict[str, Any]) -> AccountInfo:
    return AccountInfo(
        lend_current=_get(payload, "lend", "current"),
        lend_max=_get(payload, "lend", "max"),
        reservation_current=_get(payload, "reservation", "current"),
        reservation_max=_get(payload, "reservation", "max"),
        watchlist_count=payload.get("watchlistCount") or payload.get("notepadCount"),
        lend_restrictions=dict(payload.get("lendRestrictions") or {}),
        patron_name=_get(payload, "patronInformation", "name"),
        patron_library_number=_get(payload, "patronInformation", "libraryNumber"),
        patron_email=_get(payload, "patronInformation", "accountEmail"),
        library_name=_get(payload, "library", "libraryName"),
        ereader_code=payload.get("ereaderCode"),
        raw=payload,
    )


def parse_message_count(payload: dict[str, Any]) -> MessageCount:
    return MessageCount(
        inbox=payload.get("inbox"),
        unread=payload.get("unread"),
        archive=payload.get("archive"),
        raw=payload,
    )


def parse_message(payload: dict[str, Any]) -> Message:
    return Message(
        id=payload.get("id"),
        title=payload.get("title"),
        text=payload.get("text"),
        read=payload.get("read") if "read" in payload else payload.get("isRead"),
        created_at=payload.get("createdAt") or payload.get("created"),
        updated_at=payload.get("updatedAt") or payload.get("lastModified"),
        raw=payload,
    )


def parse_messages(payload) -> list[Message]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        content = payload.get("content") or payload.get("items") or payload.get("messages")
        items = content if isinstance(content, list) else [payload]
    else:
        items = []
    return [parse_message(item) for item in items if isinstance(item, dict)]


def parse_media_item(payload: dict[str, Any]) -> MediaItem:
    product = payload.get("product") if isinstance(payload.get("product"), dict) else payload
    licence_url = extract_licence_url(payload)
    authors = []
    for author in product.get("authors") or []:
        if isinstance(author, dict):
            name = " ".join(
                part for part in [author.get("firstName"), author.get("lastName")] if part
            )
            if name:
                authors.append(name)
        elif author:
            authors.append(str(author))
    return MediaItem(
        id=product.get("id"),
        product_id=payload.get("productId") or product.get("id"),
        title=product.get("title"),
        subtitle=product.get("subTitle") or product.get("subtitle"),
        media_type=product.get("mediaType"),
        lend_id=_get(payload, "userInformation", "lend", "lendId"),
        licence_url=licence_url,
        acsm_url=licence_url or extract_acsm_url(payload),
        acs_drm_uri=extract_drm_uri(payload, "ACS"),
        care_drm_uri=extract_drm_uri(payload, "CARE"),
        pdf_url=extract_mime_type_uri(payload, "PDF"),
        epub_url=extract_mime_type_uri(payload, "EPUB"),
        authors=authors,
        publisher=_publisher_name(product.get("publisher")),
        publication_date=product.get("publicationDate"),
        covers=list(product.get("covers") or []),
        categories=list(product.get("categories") or []),
        availability=dict(_get(payload, "status", "availabilityInformation", default={}) or {}),
        user_actions=list(payload.get("userAction") or []),
        raw=payload,
    )


def parse_product_details(payload: dict[str, Any]) -> ProductDetails:
    base = parse_media_item(payload)
    includes = _get(payload, "product", "includes", default=[]) or payload.get("includes") or []
    return ProductDetails(
        id=base.id,
        product_id=base.product_id,
        title=base.title,
        subtitle=base.subtitle,
        media_type=base.media_type,
        lend_id=base.lend_id,
        licence_url=base.licence_url,
        acsm_url=base.acsm_url,
        acs_drm_uri=base.acs_drm_uri,
        care_drm_uri=base.care_drm_uri,
        pdf_url=base.pdf_url,
        epub_url=base.epub_url,
        authors=base.authors,
        publisher=base.publisher,
        publication_date=base.publication_date,
        covers=base.covers,
        categories=base.categories,
        availability=base.availability,
        user_actions=base.user_actions,
        raw=payload,
        included_media=[parse_media_item(item) for item in includes],
    )


def parse_search_results(payload: dict[str, Any]) -> SearchResultPage:
    content = payload.get("content") or payload.get("items") or payload.get("results") or []
    return SearchResultPage(
        items=[parse_media_item(item) for item in content],
        total_items=payload.get("totalItems") or payload.get("total"),
        facets=list(payload.get("facets") or []),
        raw=payload,
    )


def parse_job_status(payload: dict[str, Any]) -> JobStatus:
    completed_value = payload.get("completed")
    licence_url = extract_licence_url(payload)
    return JobStatus(
        id=payload.get("id"),
        completed=bool(completed_value) or payload.get("state") == "DONE",
        last_update=payload.get("lastUpdate"),
        completed_at=completed_value if isinstance(completed_value, str) else None,
        state=payload.get("state"),
        licence_url=licence_url,
        acsm_url=licence_url or extract_acsm_url(payload),
        acs_drm_uri=extract_drm_uri(payload, "ACS"),
        care_drm_uri=extract_drm_uri(payload, "CARE"),
        pdf_url=extract_mime_type_uri(payload, "PDF"),
        epub_url=extract_mime_type_uri(payload, "EPUB"),
        raw=payload,
    )


def parse_media_items(payload: Any) -> list[MediaItem]:
    items = []
    seen = set()
    for item in _iter_media_payloads(payload):
        parsed = parse_media_item(item)
        key = parsed.product_id or parsed.id or id(item)
        if key in seen:
            continue
        seen.add(key)
        items.append(parsed)
    return items


def with_download_urls(payload: dict[str, Any]) -> dict[str, Any]:
    keys = {"licence_url", "acsm_url", "acs_drm_uri", "care_drm_uri", "pdf_url", "epub_url"}
    if keys.issubset(payload):
        return payload
    enriched = dict(payload)
    enriched["licence_url"] = extract_licence_url(payload)
    enriched["acsm_url"] = enriched["licence_url"] or extract_acsm_url(payload)
    enriched["acs_drm_uri"] = extract_drm_uri(payload, "ACS")
    enriched["care_drm_uri"] = extract_drm_uri(payload, "CARE")
    enriched["pdf_url"] = extract_mime_type_uri(payload, "PDF")
    enriched["epub_url"] = extract_mime_type_uri(payload, "EPUB")
    return enriched


def extract_acsm_url(payload: Any) -> str | None:
    return (
        _extract_named_url(payload, {"acsmUrl", "acsmURL", "acsm_url"})
        or _extract_acsm_download_url(payload)
    )


def extract_licence_url(payload: Any) -> str | None:
    return _extract_named_url(payload, {"licenceUrl", "licenseUrl", "licence_url", "license_url"})


def extract_drm_uri(payload: Any, drm_system: str) -> str | None:
    return _extract_drm_uri(payload, drm_system)


def extract_mime_type_uri(payload: Any, mime_type: str) -> str | None:
    return _extract_drm_mime_type_uri(payload, mime_type) or _extract_mime_type_uri(payload, mime_type)


def _publisher_name(value) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return value


def _iter_media_payloads(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_media_payloads(item)
        return
    if not isinstance(payload, dict):
        return

    if isinstance(payload.get("product"), dict) or "mediaType" in payload or "productId" in payload:
        yield payload

    for value in payload.values():
        yield from _iter_media_payloads(value)


def _extract_drm_uri(payload: Any, drm_system: str) -> str | None:
    if isinstance(payload, dict):
        if _is_drm_information(payload, drm_system):
            uri = payload.get("uri")
            if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                return uri
        for value in payload.values():
            uri = _extract_drm_uri(value, drm_system)
            if uri:
                return uri
    elif isinstance(payload, list):
        for item in payload:
            uri = _extract_drm_uri(item, drm_system)
            if uri:
                return uri
    return None


def _is_drm_information(payload: dict[str, Any], drm_system: str) -> bool:
    expected = drm_system.casefold()
    actual = str(payload.get("drmSystem") or "").casefold()
    class_name = str(payload.get("_class") or "").casefold()
    return actual == expected or class_name.endswith(f"{expected}drminformation")


def _extract_mime_type_uri(payload: Any, mime_type: str) -> str | None:
    if isinstance(payload, dict):
        actual = str(payload.get("mimeType") or "").casefold()
        if actual == mime_type.casefold():
            uri = payload.get("uri") or payload.get("url")
            if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                return uri
        for value in payload.values():
            uri = _extract_mime_type_uri(value, mime_type)
            if uri:
                return uri
    elif isinstance(payload, list):
        for item in payload:
            uri = _extract_mime_type_uri(item, mime_type)
            if uri:
                return uri
    return None


def _extract_drm_mime_type_uri(payload: Any, mime_type: str) -> str | None:
    if isinstance(payload, dict):
        drm_information = payload.get("drmInformation")
        if isinstance(drm_information, list):
            for item in drm_information:
                if not isinstance(item, dict):
                    continue
                actual = str(item.get("mimeType") or "").casefold()
                uri = item.get("uri")
                if actual == mime_type.casefold() and isinstance(uri, str):
                    return uri
        for value in payload.values():
            uri = _extract_drm_mime_type_uri(value, mime_type)
            if uri:
                return uri
    elif isinstance(payload, list):
        for item in payload:
            uri = _extract_drm_mime_type_uri(item, mime_type)
            if uri:
                return uri
    return None


def _extract_named_url(payload: Any, names: set[str]) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in names and isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
            uri = _extract_named_url(value, names)
            if uri:
                return uri
    elif isinstance(payload, list):
        for item in payload:
            uri = _extract_named_url(item, names)
            if uri:
                return uri
    return None


def _extract_acsm_download_url(payload: Any) -> str | None:
    if isinstance(payload, dict):
        download_type = str(payload.get("type") or payload.get("mimeType") or "").casefold()
        if "acsm" in download_type:
            for key in ("url", "uri", "downloadUrl"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
        for value in payload.values():
            uri = _extract_acsm_download_url(value)
            if uri:
                return uri
    elif isinstance(payload, list):
        for item in payload:
            uri = _extract_acsm_download_url(item)
            if uri:
                return uri
    return None


def _jwt_claims(token: str | None) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {}
