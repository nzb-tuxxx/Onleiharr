from __future__ import annotations

from typing import Any

from onleiharr._vendor.onleihe.client import OnleiheClient
from onleiharr._vendor.onleihe.models import SessionState
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


def test_parse_session_accepts_refresh_token_response_shape():
    session = parse_session({"token": "not-a-jwt"}, username="user")

    assert session.access_token == "not-a-jwt"
    assert session.username == "user"


def test_refresh_preserves_existing_session_context():
    client = OnleiheClient(host="example.invalid", onleihe_id="onleihe-id", library_id="library-id")
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
