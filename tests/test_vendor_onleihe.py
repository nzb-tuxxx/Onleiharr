from __future__ import annotations

from typing import Any

import httpx
import pytest

from onleiharr._vendor.onleihe.client import OnleiheClient
from onleiharr._vendor.onleihe.exceptions import OnleiheAPIError, OnleiheNotFoundError
from onleiharr._vendor.onleihe.models import JobStatus
from onleiharr._vendor.onleihe.models import SessionState
from onleiharr._vendor.onleihe.parsers import parse_job_status
from onleiharr._vendor.onleihe.parsers import parse_product_details
from onleiharr._vendor.onleihe.parsers import parse_session


def test_product_details_include_container_product_includes():
    product = parse_product_details(
        {
            "productId": "series-root",
            "product": {
                "id": "series-root",
                "title": "Stiftung Warentest Finanzen",
                "mediaType": "E_MAGAZINE",
                "includes": [
                    {
                        "productId": "issue-1",
                        "product": {
                            "id": "issue-1",
                            "title": "Stiftung Warentest Finanzen 06/2026",
                            "mediaType": "E_MAGAZINE",
                            "publicationDate": "2026-05-20T03:30:00Z",
                            "covers": [
                                {
                                    "type": "TEASER_LARGE",
                                    "size": 37,
                                    "uri": "https://static.example/teaser-large.jpg",
                                },
                                {
                                    "type": "IMAGE_SMALL",
                                    "size": 115,
                                    "uri": "https://static.example/image-small.jpg",
                                },
                            ],
                        },
                        "status": {"availabilityInformation": {"isAvailable": True}},
                    },
                    {
                        "productId": "issue-2",
                        "product": {
                            "id": "issue-2",
                            "title": "Stiftung Warentest Finanzen 05/2026",
                            "mediaType": "E_MAGAZINE",
                        },
                    },
                ],
            },
        }
    )

    assert [item.product_id for item in product.included_media] == ["issue-1", "issue-2"]
    assert product.included_media[0].publication_date == "2026-05-20T03:30:00Z"
    assert product.included_media[0].availability == {"isAvailable": True}
    assert product.included_media[0].cover_url == "https://static.example/image-small.jpg"


def test_parse_session_accepts_refresh_token_response_shape():
    session = parse_session({"token": "not-a-jwt"}, username="user")

    assert session.access_token == "not-a-jwt"
    assert session.username == "user"


def test_build_open_id_authorization_url_uses_discovered_provider_parameters():
    client = OnleiheClient(host="muenchen.onleihe.de")
    url = client.build_open_id_authorization_url(
        {
            "authorizationEndpoint": "https://ssl.muenchen.de/oidcp/authorize",
            "standardParams": {"client_id": "onleihe001", "response_type": "code"},
            "additionalParams": {"unused": None},
        },
        redirect_url="https://muenchen.onleihe.de",
        state="expected-state",
    )
    assert url.startswith("https://ssl.muenchen.de/oidcp/authorize?")
    assert "client_id=onleihe001" in url
    assert "redirect_uri=https%3A%2F%2Fmuenchen.onleihe.de" in url
    assert "state=expected-state" in url
    assert "unused" not in url


def test_login_open_id_exchanges_code_with_onleihe_api():
    client = OnleiheClient(host="muenchen.onleihe.de", onleihe_id="onleihe-id", library_id="library-id")

    def fake_post(path, *, params=None, json=None, auth=True):
        assert path == "/user-application/v1/auth/login"
        assert json == {
            "libraryId": "library-id",
            "onleiheId": "onleihe-id",
            "openIdCode": "one-time-code",
            "openIdRedirectURL": "https://muenchen.onleihe.de",
        }
        assert auth is False
        return {"accessToken": "access", "refreshToken": "refresh"}

    client._post = fake_post  # type: ignore[method-assign]
    session = client.login_open_id("one-time-code", redirect_url="https://muenchen.onleihe.de")
    assert session.access_token == "access"
    assert session.refresh_token == "refresh"


def test_parse_job_status_keeps_api_error():
    job = parse_job_status(
        {
            "id": "job-1",
            "state": "FAILED",
            "apiError": {
                "statusCode": 409,
                "messageId": "no-available-licences",
            },
        }
    )

    assert job.state == "FAILED"
    assert job.api_error == {"statusCode": 409, "messageId": "no-available-licences"}


def test_refresh_preserves_existing_session_context():
    persisted = []
    client = OnleiheClient(
        host="example.invalid",
        onleihe_id="onleihe-id",
        library_id="library-id",
        session_callback=persisted.append,
    )
    client.session = SessionState(
        access_token="old-access",
        refresh_token="old-refresh",
        user_id="user-id",
        profile_id="master",
        library_id="library-id",
        onleihe_id="onleihe-id",
        username="user",
    )

    def fake_post(path, *, params=None, json=None, auth=True):
        assert path == "/user-application/v1/auth/refresh"
        assert json == {"token": "old-refresh"}
        assert auth is False
        return {"token": "new-access"}

    client._post = fake_post  # type: ignore[method-assign]

    session = client.refresh()

    assert session.access_token == "new-access"
    assert session.refresh_token == "old-refresh"
    assert session.user_id == "user-id"
    assert session.library_id == "library-id"
    assert persisted == [session]


def test_lend_raises_api_error_when_lend_job_fails():
    client = OnleiheClient(host="example.invalid", onleihe_id="onleihe-id", library_id="library-id")
    client.session = SessionState(
        access_token="access",
        refresh_token="refresh",
        user_id="user-id",
        profile_id="master",
        library_id="library-id",
        onleihe_id="onleihe-id",
    )

    def fake_post(path, *, params=None, json=None, auth=True):
        return {"id": "job-1", "state": "IN_PROGRESS"}

    def fake_wait_for_job(job_id, *, timeout=30.0, poll_interval=1.0):
        return JobStatus(
            id=job_id,
            completed=False,
            state="FAILED",
            api_error={"statusCode": 409, "messageId": "no-available-licences"},
        )

    client._post = fake_post  # type: ignore[method-assign]
    client.wait_for_job = fake_wait_for_job  # type: ignore[method-assign]

    with pytest.raises(OnleiheAPIError) as exc_info:
        client.lend("product-id")

    assert exc_info.value.status_code == 409
    assert exc_info.value.payload == {"statusCode": 409, "messageId": "no-available-licences"}


def test_anonymous_search_preserves_library_context_without_user_id():
    client = OnleiheClient(host="example.invalid", onleihe_id="onleihe-id", library_id="library-id")
    client.session = SessionState(
        access_token="access",
        refresh_token="refresh",
        user_id="user-id",
        profile_id="master",
        library_id="library-id",
        onleihe_id="onleihe-id",
    )
    calls: list[dict[str, Any] | None] = []

    def fake_post(path, *, params=None, json=None, auth=True):
        assert path == "/ui/v1/onleihe/onleihe-id/search"
        calls.append(params)
        return {"content": []}

    client._post = fake_post  # type: ignore[method-assign]

    client.search_media(raw_body={"query": [], "size": 50}, require_login=False)

    assert calls == [{"libraryId": "library-id"}]


def test_transport_timeout_is_normalized_to_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OnleiheClient(
        host="example.invalid",
        onleihe_id="onleihe-id",
        client=http_client,
    )

    with pytest.raises(OnleiheAPIError, match="timed out"):
        client.maintenance_active()


def test_maintenance_active_uses_rest_status_code():
    status_codes = [200, 404]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.onleihe.de/maintenance"
        return httpx.Response(status_codes.pop(0), text="Wartungsseite aktiviert")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OnleiheClient(
        host="example.invalid",
        onleihe_id="onleihe-id",
        client=http_client,
    )

    assert client.maintenance_active() is True
    assert client.maintenance_active() is False


def test_no_such_element_response_is_not_found_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "message": "List is empty.",
                "messageId": "no-such-element",
                "status": "Internal Server Error",
                "statusCode": 500,
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OnleiheClient(
        host="example.invalid",
        onleihe_id="onleihe-id",
        client=http_client,
    )

    with pytest.raises(OnleiheNotFoundError) as exc_info:
        client._get("/ui/v2/pages/product-details/missing")  # noqa: SLF001

    assert exc_info.value.status_code == 500
    assert exc_info.value.payload["messageId"] == "no-such-element"


def test_category_element_resolution_is_cached_per_client():
    client = OnleiheClient(host="example.invalid", onleihe_id="onleihe-id")
    calls: list[list[str]] = []

    def fake_get_category_elements(element_ids: list[str]) -> dict[str, Any]:
        calls.append(element_ids)
        return {
            "content": [
                {
                    "query": {
                        "query": [
                            {
                                "query": "A",
                                "fields": ["categories.id"],
                                "isWildcard": False,
                                "isExact": False,
                                "operator": "OR",
                            }
                        ]
                    }
                }
            ]
        }

    client.get_category_elements = fake_get_category_elements  # type: ignore[method-assign]

    client.build_category_search_body(["cat-a"])
    client.build_category_search_body(["cat-a"])

    assert calls == [["cat-a"]]


def test_category_element_cache_expires():
    client = OnleiheClient(
        host="example.invalid",
        onleihe_id="onleihe-id",
        category_elements_cache_ttl_secs=0,
    )
    calls: list[list[str]] = []

    def fake_get_category_elements(element_ids: list[str]) -> dict[str, Any]:
        calls.append(element_ids)
        return {
            "content": [
                {
                    "query": {
                        "query": [
                            {
                                "query": "A",
                                "fields": ["categories.id"],
                                "isWildcard": False,
                                "isExact": False,
                                "operator": "OR",
                            }
                        ]
                    }
                }
            ]
        }

    client.get_category_elements = fake_get_category_elements  # type: ignore[method-assign]

    client.build_category_search_body(["cat-a"])
    client.build_category_search_body(["cat-a"])

    assert calls == [["cat-a"], ["cat-a"]]
