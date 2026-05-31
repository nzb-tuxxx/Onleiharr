from __future__ import annotations

import time
from typing import Any

import httpx

from .exceptions import OnleiheAPIError, OnleiheAuthError
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
from .parsers import (
    parse_account,
    parse_job_status,
    parse_library,
    parse_library_page,
    parse_media_items,
    parse_messages,
    parse_message_count,
    parse_onleihe_info,
    parse_product_details,
    parse_search_results,
    parse_session,
    with_download_urls,
)


DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0"
DEFAULT_SEARCH_SIZE = 50
DEFAULT_CATEGORY_ELEMENTS_CACHE_TTL_SECS = 6 * 60 * 60
NEWEST_PUBLICATION_SORT: list[dict[str, str]] = [{"field": "publicationDate", "order": "DESC"}]


class OnleiheClient:
    def __init__(
        self,
        *,
        host: str = "niedersachsen.onleihe.de",
        onleihe_id: str | None = None,
        onleihe_name: str | None = None,
        library_id: str | None = None,
        library_name: str | None = None,
        api_base_url: str = "https://api.onleihe.de",
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20.0,
        category_elements_cache_ttl_secs: float = DEFAULT_CATEGORY_ELEMENTS_CACHE_TTL_SECS,
        client: httpx.Client | None = None,
    ) -> None:
        self.host = host
        self.onleihe_id = onleihe_id
        self.onleihe_name = onleihe_name
        self.library_id = library_id
        self.library_name = library_name
        self.api_base_url = api_base_url.rstrip("/")
        self.session = SessionState(onleihe_id=onleihe_id, library_id=library_id)
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._category_elements_cache_ttl_secs = category_elements_cache_ttl_secs
        self._category_elements_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
        self._base_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "de,en-US;q=0.9,en;q=0.8",
            "User-Agent": user_agent,
            "Origin": f"https://{host}",
            "Referer": f"https://{host}/",
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OnleiheClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def list_libraries(
        self,
        *,
        search_value: str | None = None,
        onleihe_ids: list[str] | None = None,
        page: int = 1,
        size: int = 20,
    ) -> LibraryPage:
        params: dict[str, Any] = {"page": page, "size": size}
        if search_value:
            params["searchValue"] = search_value
        ids = onleihe_ids or ([self._onleihe_id()] if self._onleihe_id(optional=True) else None)
        if ids:
            params["onleiheIds"] = ids
        return parse_library_page(
            self._get("/user-application/v2/auth/libraries", params=params, auth=False)
        )

    def get_domain_mappings(self) -> list[dict[str, Any]]:
        payload = self._get(
            "/management/v1/auth/domains",
            params={"host": self.host},
            auth=False,
        )
        return list(payload.get("content") or [])

    def resolve_onleihe_id(self, onleihe_name: str | None = None) -> str:
        if self.onleihe_id:
            return self.onleihe_id
        name = onleihe_name or self.onleihe_name
        mappings = self.get_domain_mappings()
        if name:
            matches = [item for item in mappings if _same_name(item.get("onleiheName"), name)]
            if not matches:
                matches = [item for item in mappings if _contains_name(item.get("onleiheName"), name)]
            if not matches:
                raise OnleiheAPIError(f"Could not resolve onleihe_name={name!r} for host {self.host}")
            if len(matches) > 1:
                names = ", ".join(item.get("onleiheName") or "?" for item in matches)
                raise OnleiheAPIError(f"Ambiguous onleihe_name={name!r}; matches: {names}")
            mapping = matches[0]
        elif len(mappings) == 1:
            mapping = mappings[0]
        else:
            raise ValueError("onleihe_id or onleihe_name is required")
        self.onleihe_id = mapping.get("onleiheId")
        self.onleihe_name = mapping.get("onleiheName") or self.onleihe_name
        self.session.onleihe_id = self.session.onleihe_id or self.onleihe_id
        if mapping.get("libraryId") and not self.library_id:
            self.library_id = mapping.get("libraryId")
            self.session.library_id = self.session.library_id or self.library_id
        if mapping.get("libraryName") and not self.library_name:
            self.library_name = mapping.get("libraryName")
        if not self.onleihe_id:
            raise OnleiheAPIError(f"Domain mapping for host {self.host} did not contain an onleihe id")
        return self.onleihe_id

    def resolve_library_id(self, library_name: str | None = None) -> str:
        if self.library_id:
            return self.library_id
        name = library_name or self.library_name
        if not name:
            raise ValueError("library_id or library_name is required")
        onleihe_id = self._onleihe_id(optional=True)
        page = self.list_libraries(
            search_value=name,
            onleihe_ids=[onleihe_id] if onleihe_id else None,
            page=1,
            size=100,
        )
        matches = [library for library in page.libraries if _same_name(library.name, name)]
        if not matches:
            matches = [library for library in page.libraries if _contains_name(library.name, name)]
        if not matches:
            raise OnleiheAPIError(f"Could not resolve library_name={name!r}")
        if len(matches) > 1:
            labels = ", ".join(f"{library.name} ({library.id})" for library in matches[:10])
            raise OnleiheAPIError(f"Ambiguous library_name={name!r}; matches: {labels}")
        library = matches[0]
        self.library_id = library.id
        self.library_name = library.name or self.library_name
        self.onleihe_id = self.onleihe_id or library.onleihe_id
        self.session.library_id = self.session.library_id or self.library_id
        self.session.onleihe_id = self.session.onleihe_id or self.onleihe_id
        return self.library_id

    def resolve_ids(self) -> tuple[str | None, str | None]:
        onleihe_id = self._onleihe_id(optional=True)
        library_id = self._library_id(optional=True)
        return onleihe_id, library_id

    def get_login_method(self, library_id: str | None = None) -> dict[str, Any]:
        library_id = library_id or self._library_id()
        return self._get(
            f"/user-application/v1/auth/libraries/{library_id}/login-method",
            auth=False,
        )

    def login(
        self,
        username: str | None = None,
        password: str | None = None,
        *,
        library_id: str | None = None,
        onleihe_id: str | None = None,
        library_name: str | None = None,
        onleihe_name: str | None = None,
    ) -> SessionState:
        if onleihe_id:
            self.onleihe_id = onleihe_id
            self.session.onleihe_id = self.session.onleihe_id or onleihe_id
        if library_id:
            self.library_id = library_id
            self.session.library_id = self.session.library_id or library_id
        if onleihe_name:
            self.onleihe_name = onleihe_name
        if library_name:
            self.library_name = library_name
        payload: dict[str, Any] = {"onleiheId": self._onleihe_id()}
        if username is not None and password is not None:
            payload.update(
                {
                    "username": username,
                    "password": password,
                    "libraryId": self._library_id(),
                }
            )
        data = self._post("/user-application/v1/auth/login", json=payload)
        self.session = parse_session(data, username=username)
        self.onleihe_id = self.session.onleihe_id or payload["onleiheId"]
        self.library_id = self.session.library_id or self.library_id
        return self.session

    def refresh(self) -> SessionState:
        token = self.session.refresh_token or self.session.access_token
        if not token:
            raise OnleiheAuthError("Cannot refresh without a token")
        data = self._post("/user-application/v1/auth/refresh", json={"token": token}, auth=False)
        previous = self.session
        self.session = parse_session(data, username=previous.username)
        self.session.refresh_token = self.session.refresh_token or previous.refresh_token
        self.session.user_id = self.session.user_id or previous.user_id
        self.session.profile_id = self.session.profile_id or previous.profile_id
        self.session.library_id = self.session.library_id or previous.library_id
        self.session.onleihe_id = self.session.onleihe_id or previous.onleihe_id
        self.onleihe_id = self.session.onleihe_id or self.onleihe_id
        self.library_id = self.session.library_id or self.library_id
        return self.session

    def logout(self) -> None:
        if self.session.refresh_token:
            self._post(
                "/user-application/v1/auth/logout",
                json={"refreshToken": self.session.refresh_token},
                auth=False,
            )
        self.session = SessionState(onleihe_id=self.onleihe_id, library_id=self.library_id)

    def get_onleihe(self, onleihe_id: str | None = None) -> OnleiheInfo:
        self._ensure_anonymous_session()
        return parse_onleihe_info(self._get(f"/management/v1/onleihe/{onleihe_id or self._onleihe_id()}"))

    def get_library(
        self,
        library_id: str | None = None,
        *,
        library_name: str | None = None,
    ) -> Library:
        if library_id:
            self.library_id = library_id
        if library_name:
            self.library_name = library_name
        library_id = library_id or self._library_id()
        if self.session.user_id:
            return parse_library(self._get(f"/management/v1/libraries/{library_id}"))
        return self._find_library_public(library_id)

    def maintenance_active(self) -> bool:
        response = self._request("GET", "/maintenance", auth=False, expected_statuses={200, 404})
        return response.status_code == 200

    def explore(self, *, legacy: bool = False, include_user: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {"legacy": str(legacy).lower()}
        if include_user:
            self._ensure_login()
            params.update(
                {
                    "libraryId": self._library_id(),
                    "userId": self._user_id(),
                    "profileId": self._profile_id(),
                }
            )
        else:
            self._ensure_anonymous_session()
        return self._get(f"/ui/v1/onleihe/{self._onleihe_id()}/explore", params=params)

    def search_media(
        self,
        query: str = "",
        *,
        fields: list[str] | None = None,
        from_: int = 0,
        size: int = 20,
        facets: list[dict[str, Any]] | None = None,
        sort: list[dict[str, Any]] | None = None,
        pre_filters: list[dict[str, Any]] | None = None,
        post_filters: list[dict[str, Any]] | None = None,
        user_language: str = "de_DE",
        require_login: bool = False,
        raw_body: dict[str, Any] | None = None,
    ) -> SearchResultPage:
        body = raw_body or {
            "from": from_,
            "userLanguage": user_language,
            "query": [
                {
                    "query": query,
                    "fields": fields or [],
                    "isExact": False,
                    "operator": "AND",
                }
            ],
            "size": size,
        }
        if facets is not None:
            body["facets"] = facets
        if sort is not None:
            body["sort"] = sort
        if pre_filters is not None:
            body["preFilters"] = pre_filters
        if post_filters is not None:
            body["postFilters"] = post_filters
        if require_login:
            params = self._context_params(include_user=True)
        else:
            params = {}
            self._ensure_anonymous_session()
        results = parse_search_results(
            self._post(f"/ui/v1/onleihe/{self._onleihe_id()}/search", params=params, json=body)
        )
        if require_login:
            self._enrich_media_items_with_player_licences(results.items)
        return results

    def suggest(self, query: str, *, fields: list[str] | None = None, size: int = 10) -> dict[str, Any]:
        self._ensure_anonymous_session()
        body: dict[str, Any] = {"query": query, "from": 0, "size": size}
        if fields:
            body["fields"] = fields
        return self._post(f"/ui/v1/onleihe/{self._onleihe_id()}/suggest", json=body)

    def browse_catalog(
        self,
        query_or_category: dict[str, Any] | str,
        *,
        page_size: int = DEFAULT_SEARCH_SIZE,
        max_pages: int | None = None,
        user_language: str = "de_DE",
    ) -> list:
        if isinstance(query_or_category, dict):
            body = dict(query_or_category)
        else:
            body = {
                "from": 0,
                "userLanguage": user_language,
                "query": [{"query": query_or_category, "fields": ["categories.id"], "operator": "OR"}],
                "size": page_size,
            }
        items = []
        page = 0
        while True:
            body["from"] = page * page_size
            body["size"] = page_size
            result = self.search_media(raw_body=body)
            items.extend(result.items)
            total = result.total_items
            page += 1
            if not result.items or (total is not None and len(items) >= total):
                break
            if max_pages is not None and page >= max_pages:
                break
        return items

    def build_category_search_body(
        self,
        category_element_ids: list[str],
        *,
        from_: int = 0,
        size: int = DEFAULT_SEARCH_SIZE,
        sort: list[dict[str, Any]] | None = None,
        user_language: str = "de_DE",
        facets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not category_element_ids:
            raise ValueError("category_element_ids must not be empty")
        elements = self.get_category_elements_cached(category_element_ids)
        content = elements.get("content")
        if not isinstance(content, list):
            raise OnleiheAPIError(
                "Category element response did not contain a content list",
                payload=elements,
            )

        query: list[dict[str, Any]] = []
        pre_filters: list[dict[str, Any]] = []
        post_filters: list[dict[str, Any]] = []
        seen_queries: set[tuple[tuple[str, Any], ...]] = set()

        for element in content:
            if not isinstance(element, dict):
                continue
            element_query = element.get("query")
            if not isinstance(element_query, dict):
                continue
            for item in element_query.get("query") or []:
                if not isinstance(item, dict):
                    continue
                query_key = _freeze_dict(item)
                if query_key in seen_queries:
                    continue
                seen_queries.add(query_key)
                query.append(dict(item))
            pre_filters.extend(_dict_list(element_query.get("preFilters")))
            post_filters.extend(_dict_list(element_query.get("postFilters")))

        if not query:
            raise OnleiheAPIError(
                "Category elements did not contain usable search queries",
                payload=elements,
            )

        return {
            "from": from_,
            "userLanguage": user_language,
            "facets": facets if facets is not None else [],
            "query": query,
            "size": size,
            "preFilters": _dedupe_dicts(pre_filters),
            "postFilters": _dedupe_dicts(post_filters),
            "sort": sort if sort is not None else list(NEWEST_PUBLICATION_SORT),
        }

    def search_category_elements(
        self,
        category_element_ids: list[str],
        *,
        from_: int = 0,
        size: int = DEFAULT_SEARCH_SIZE,
        sort: list[dict[str, Any]] | None = None,
        user_language: str = "de_DE",
        facets: list[dict[str, Any]] | None = None,
        require_login: bool = False,
    ) -> SearchResultPage:
        body = self.build_category_search_body(
            category_element_ids,
            from_=from_,
            size=size,
            sort=sort,
            user_language=user_language,
            facets=facets,
        )
        return self.search_media(raw_body=body, require_login=require_login)

    def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
        if not include_user_context:
            self._ensure_anonymous_session()
        params = {"onleiheId": self._onleihe_id()}
        if self._library_id(optional=True):
            params["libraryId"] = self._library_id()
        if include_user_context:
            self._ensure_login()
            if self.session.user_id:
                params["userId"] = self.session.user_id
        product = parse_product_details(
            self._get(
                f"/ui/v2/pages/product-details/{product_id}",
                params=params,
            )
        )
        if include_user_context and self.session.user_id:
            self._enrich_media_items_with_player_licences([product, *product.included_media])
        return product

    def get_category_tree(self) -> dict[str, Any]:
        self._ensure_anonymous_session()
        return self._get("/inventory/v1/category-tree", params={"searchQueryBehavior": "FILTER_QUERIES"})

    def get_category_elements(self, element_ids: list[str]) -> dict[str, Any]:
        self._ensure_anonymous_session()
        return self._get("/inventory/v1/category-tree/elements", params={"elementIds": element_ids})

    def get_category_elements_cached(self, element_ids: list[str]) -> dict[str, Any]:
        key = tuple(_dedupe_strings(element_ids))
        now = time.monotonic()
        cached = self._category_elements_cache.get(key)
        if cached is not None:
            cached_at, payload = cached
            if now - cached_at < self._category_elements_cache_ttl_secs:
                return payload
        payload = self.get_category_elements(list(key))
        self._category_elements_cache[key] = (now, payload)
        return payload

    def get_account(self) -> AccountInfo:
        self._ensure_login()
        return parse_account(
            self._get(
                f"/ui/v2/pages/library/{self._library_id()}/my-account",
                params={"userId": self._user_id()},
            )
        )

    def get_settings(self) -> dict[str, Any]:
        self._ensure_login()
        return self._get(f"/user-application/v1/users/{self._user_id()}/settings")

    def get_my_media(self) -> dict[str, Any]:
        self._ensure_login()
        return self._get(
            "/ui/v2/pages/my-media",
            params={
                "onleiheId": self._onleihe_id(),
                "userId": self._user_id(),
                "profileId": self._profile_id(),
                "libraryId": self._library_id(),
            },
        )

    def get_my_media_items(self, *, include_player_licences: bool = True) -> list[MediaItem]:
        items = parse_media_items(self.get_my_media())
        if include_player_licences:
            self._enrich_media_items_with_player_licences(items)
        return items

    def get_message_count(self) -> MessageCount:
        self._ensure_login()
        return parse_message_count(
            self._get(
                f"/user-messaging/users/{self._user_id()}/message-count",
                params={
                    "onleiheId": self._onleihe_id(),
                    "libraryId": self._library_id(),
                    "target": "WEB",
                },
            )
        )

    def list_messages(self) -> list[Message]:
        return self.get_broadcast_messages()

    def get_broadcast_messages(self) -> list[Message]:
        self._ensure_login()
        return parse_messages(
            self._get(
                f"/user-messaging/v1/users/{self._user_id()}/broadcast-messages",
                params={
                    "onleiheId": self._onleihe_id(),
                    "libraryId": self._library_id(),
                    "target": "WEB",
                },
            )
        )

    def lend(
        self,
        product_id: str,
        *,
        lending_duration_days: int | None = None,
        wait_for_acsm: bool = True,
        job_timeout: float = 30.0,
        job_poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        self._ensure_login()
        body: dict[str, Any] = {"productId": product_id}
        if lending_duration_days is not None:
            if lending_duration_days <= 0:
                raise ValueError("lending_duration_days must be positive")
            body["lendingDuration"] = lending_duration_days * 24 * 60 * 60
        response = with_download_urls(
            self._post(
                f"/ui/v2/onleihe/{self._onleihe_id()}/library/{self._library_id()}/"
                f"users/{self._user_id()}/profiles/{self._profile_id()}/lends",
                json=body,
            )
        )
        if wait_for_acsm and response.get("acsm_url") is None and response.get("acs_drm_uri") is None:
            job_id = _job_id_from_response(response)
            if job_id:
                job = self.wait_for_job(
                    job_id,
                    timeout=job_timeout,
                    poll_interval=job_poll_interval,
                )
                if job.api_error:
                    status_code = _int_or_none(job.api_error.get("statusCode"))
                    raise OnleiheAPIError(
                        str(job.api_error.get("message") or "Onleihe lend job failed"),
                        status_code=status_code,
                        payload=job.api_error,
                    )
                response["job"] = job.raw
                response["licence_url"] = job.licence_url
                response["acsm_url"] = job.acsm_url
                response["acs_drm_uri"] = job.acs_drm_uri
                response["care_drm_uri"] = job.care_drm_uri
                response["pdf_url"] = job.pdf_url
                response["epub_url"] = job.epub_url
        if wait_for_acsm and response.get("acsm_url") is None and response.get("acs_drm_uri") is None:
            for item in self.get_my_media_items():
                if item.product_id == product_id or item.id == product_id:
                    response["licence_url"] = item.licence_url
                    response["acsm_url"] = item.acsm_url
                    response["acs_drm_uri"] = item.acs_drm_uri
                    response["care_drm_uri"] = item.care_drm_uri
                    response["pdf_url"] = item.pdf_url
                    response["epub_url"] = item.epub_url
                    break
        return response

    def reserve(self, product_id: str, *, automatic_acceptance: bool = True) -> dict[str, Any]:
        self._ensure_login()
        return self._post(
            f"/ui/v2/onleihe/{self._onleihe_id()}/library/{self._library_id()}/"
            f"user/{self._user_id()}/profiles/{self._profile_id()}/products/{product_id}/reservations",
            json={"automaticAcceptance": automatic_acceptance},
        )

    def return_lend(self, lend_id: str) -> dict[str, Any]:
        self._ensure_login()
        return self._delete(
            f"/ui/v1/onleihe/{self._onleihe_id()}/users/{self._user_id()}/lends/{lend_id}"
        )

    def get_job(self, job_id: str) -> JobStatus:
        self._ensure_login()
        return parse_job_status(
            self._get(f"/ui/v1/users/{self._user_id()}/profiles/{self._profile_id()}/jobs/{job_id}")
        )

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> JobStatus:
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job.completed or job.state in {"DONE", "FAILED", "ERROR"}:
                return job
            if time.monotonic() >= deadline:
                return job
            time.sleep(poll_interval)

    def get_player_licence(self, lend_id: str) -> dict[str, Any]:
        self._ensure_login()
        return with_download_urls(self._get(f"/drm-facade/v1/drm/lend/{lend_id}/player-licence"))

    def download_bytes(self, url: str, *, auth: bool = True) -> bytes:
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must be absolute")
        response = self._request("GET", url, auth=auth)
        return response.content

    def download_acsm(self, url: str) -> bytes:
        return self.download_bytes(url, auth=True)

    def _enrich_media_items_with_player_licences(self, items: list[MediaItem]) -> None:
        for item in items:
            if item.acsm_url or not item.lend_id:
                continue
            licence = self.get_player_licence(item.lend_id)
            item.licence_url = licence.get("licence_url")
            item.acsm_url = licence.get("acsm_url")
            item.acs_drm_uri = item.acs_drm_uri or licence.get("acs_drm_uri")
            item.care_drm_uri = item.care_drm_uri or licence.get("care_drm_uri")
            item.pdf_url = item.pdf_url or licence.get("pdf_url")
            item.epub_url = item.epub_url or licence.get("epub_url")

    def _context_params(self, *, include_user: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.library_id:
            params["libraryId"] = self.library_id
        if include_user:
            self._ensure_login()
            params["userId"] = self._user_id()
        return params

    def _find_library_public(self, library_id: str) -> Library:
        page = 1
        while True:
            libraries = self.list_libraries(onleihe_ids=[self._onleihe_id()], page=page, size=100)
            for library in libraries.libraries:
                if library.id == library_id:
                    return library
            if not libraries.total_pages or page >= libraries.total_pages:
                break
            page += 1
        raise OnleiheAPIError(f"Library {library_id} not found in public library list")

    def _ensure_login(self) -> None:
        if not self.session.access_token:
            raise OnleiheAuthError("This method requires login() first")
        if self.session.expires_at and self.session.expires_at - 30 <= int(time.time()):
            self.refresh()

    def _ensure_anonymous_session(self) -> None:
        if self.session.access_token:
            if self.session.expires_at and self.session.expires_at - 30 <= int(time.time()):
                self.refresh()
            return
        self.login()

    def _get(self, path: str, *, params: dict[str, Any] | None = None, auth: bool = True) -> dict[str, Any]:
        response = self._request("GET", path, params=params, auth=auth)
        return self._json(response)

    def _post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        response = self._request("POST", path, params=params, json=json, auth=auth)
        if not response.content:
            return {}
        return self._json(response)

    def _delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        response = self._request("DELETE", path, params=params, auth=auth)
        if not response.content:
            return {}
        return self._json(response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = True,
        expected_statuses: set[int] | None = None,
        _retried: bool = False,
    ) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.api_base_url}{path}"
        headers = dict(self._base_headers)
        if json is not None:
            headers["Content-Type"] = "application/json"
        if auth and self.session.access_token:
            headers["Authorization"] = f"{self.session.token_type} {self.session.access_token}"
        response = self._send_with_retry(method, url, params=params, json=json, headers=headers)
        expected = expected_statuses or {200}
        if response.status_code in expected:
            return response
        if auth and response.status_code == 401 and self.session.refresh_token and not _retried:
            self.refresh()
            return self._request(
                method,
                path,
                params=params,
                json=json,
                auth=auth,
                expected_statuses=expected_statuses,
                _retried=True,
            )
        payload = _safe_json(response)
        exc = OnleiheAuthError if response.status_code in {401, 403} else OnleiheAPIError
        raise exc(
            f"Onleihe API request failed: {method} {url} returned {response.status_code}",
            status_code=response.status_code,
            payload=payload,
        )

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        payload = _safe_json(response)
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"content": payload}
        return {}

    def _onleihe_id(self, optional: bool = False) -> str | None:
        value = self.session.onleihe_id or self.onleihe_id
        if not value and self.onleihe_name:
            value = self.resolve_onleihe_id()
        if not value and self.library_name and not optional:
            self.resolve_library_id()
            value = self.session.onleihe_id or self.onleihe_id
        if not value and not optional:
            raise ValueError("onleihe_id is required")
        return value

    def _library_id(self, optional: bool = False) -> str | None:
        value = self.session.library_id or self.library_id
        if not value and self.library_name:
            value = self.resolve_library_id()
        if not value:
            if optional:
                return None
            raise ValueError("library_id is required")
        return value

    def _user_id(self) -> str:
        if not self.session.user_id:
            raise OnleiheAuthError("Logged-in session does not contain a user id")
        return self.session.user_id

    def _profile_id(self) -> str:
        return self.session.profile_id or "master"

    def _send_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                return self._client.request(method, url, params=params, json=json, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if method.upper() not in {"GET", "HEAD"} or attempt == 2:
                    raise OnleiheAPIError(f"Onleihe API request failed: {method} {url}: {exc}") from exc
                time.sleep(0.2 * (attempt + 1))
        raise OnleiheAPIError(f"Onleihe API request failed: {method} {url}: {last_error}") from last_error


def _safe_json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return response.text


def _job_id_from_response(payload: dict[str, Any]) -> str | None:
    job_id = payload.get("jobId")
    if job_id:
        return str(job_id)
    response_type = str(payload.get("type") or "")
    if payload.get("state") or response_type.endswith("_JOB") or "JOB" in response_type:
        value = payload.get("id")
        return str(value) if value else None
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, Any], ...]] = set()
    deduped: list[dict[str, Any]] = []
    for value in values:
        key = _freeze_dict(value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _freeze_dict(value: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted((key, _freeze_value(item)) for key, item in value.items()))


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _freeze_dict(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _normalize_name(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _same_name(left: str | None, right: str | None) -> bool:
    return _normalize_name(left) == _normalize_name(right)


def _contains_name(left: str | None, right: str | None) -> bool:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    return bool(left_norm and right_norm and (right_norm in left_norm or left_norm in right_norm))
