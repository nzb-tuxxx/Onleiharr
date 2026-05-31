from __future__ import annotations

from types import SimpleNamespace

from onleiharr._vendor.onleihe import MediaItem, OnleiheAPIError, OnleiheAuthError, ProductDetails, SearchResultPage
from onleiharr.cli import (
    WatchedMedia,
    fetch_all_watched_media,
    fetch_category_watch_media,
    fetch_product_watch_media,
    lend_failure_allows_reserve,
    matches_filter,
    maybe_lend_or_reserve,
    process_my_media_downloads,
)
from onleiharr.config import WatchCategory


class FakeClient:
    host = "example.onleihe.de"
    last_search_body = None

    def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
        if product_id == "issue-with-container":
            return ProductDetails(
                id=product_id,
                product_id=product_id,
                title="Finanzen 04/2026",
                subtitle=None,
                media_type="E_MAGAZINE",
                raw={"product": {"containerIds": ["series-1"]}},
            )
        return ProductDetails(
            id=product_id,
            product_id=product_id,
            title="Finanzen Reihe",
            subtitle=None,
            media_type="SERIES",
            included_media=[
                MediaItem(
                    id="issue-1",
                    product_id="issue-1",
                    title="Finanzen 06/2026",
                    subtitle=None,
                    media_type="E_MAGAZINE",
                    authors=[],
                    availability={"isAvailable": True},
                )
            ],
        )

    def search_category_elements(self, category_ids: list[str], *, require_login: bool = False) -> SearchResultPage:
        raise AssertionError("fetch_category_watch_media should build the body from watch options")

    def build_category_search_body(self, category_ids, *, sort):
        assert category_ids == ["cat-1", "cat-2"]
        self.last_search_body = {
            "query": [{"query": "U", "fields": ["categories.id"], "operator": "OR"}],
            "postFilters": [],
            "sort": sort,
            "size": 50,
        }
        return self.last_search_body

    def search_media(self, *, raw_body, require_login: bool = False) -> SearchResultPage:
        assert require_login is False
        self.last_search_body = raw_body
        return SearchResultPage(
            items=[
                MediaItem(
                    id="book-1",
                    product_id="book-1",
                    title="Python fuer Profis",
                    subtitle="Praxiswissen",
                    media_type="E_BOOK",
                    authors=["Ada Lovelace"],
                    availability={"isAvailable": True},
                ),
                MediaItem(
                    id="book-2",
                    product_id="book-2",
                    title="Gartenratgeber",
                    subtitle=None,
                    media_type="E_BOOK",
                    authors=[],
                    availability={"isAvailable": True},
                ),
            ],
            total_items=2,
        )


def test_product_watch_expands_included_media_without_keyword_check():
    media = fetch_product_watch_media(FakeClient(), "series-1")

    assert len(media) == 1
    assert media[0].product_id == "issue-1"
    assert media[0].keyword_required is False
    assert media[0].keyword_matched is True
    assert media[0].url == "https://example.onleihe.de/mymedia/mediadetail?productId=issue-1"


def test_product_watch_resolves_single_issue_container_ids():
    media = fetch_product_watch_media(FakeClient(), "issue-with-container")

    assert [item.product_id for item in media] == ["issue-1"]
    assert media[0].source == "product:issue-with-container"


def test_category_watch_filters_by_keywords():
    client = FakeClient()
    watch = WatchCategory(
        category_ids=["cat-1", "cat-2"],
        keywords=["python"],
        media_types=["E_BOOK"],
        filters=[{"field": "language", "values": ["ger"]}],
        sort_field="licence.stockChangedTimestamp",
        sort_order="DESC",
    )

    media = fetch_category_watch_media(client, watch)

    assert [item.product_id for item in media] == ["book-1"]
    assert media[0].keyword_required is True
    assert media[0].keyword_matched is True
    assert client.last_search_body["sort"] == [{"field": "licence.stockChangedTimestamp", "order": "DESC"}]
    assert client.last_search_body["postFilters"] == [
        {"field": "mediaType", "values": ["E_BOOK"]},
        {"field": "language", "values": ["ger"]},
    ]


def test_fetch_all_watched_media_reports_target_errors():
    class Client(FakeClient):
        def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
            if product_id == "broken":
                raise OnleiheAPIError("not found", status_code=404)
            return super().get_product(product_id, include_user_context=include_user_context)

    config = SimpleNamespace(
        general=SimpleNamespace(
            watch_product_ids=["series-1", "broken"],
            watch_categories=[],
        )
    )

    result = fetch_all_watched_media(Client(), config)  # type: ignore[arg-type]

    assert [item.product_id for item in result.media] == ["issue-1"]
    assert result.errors == 1


def test_matches_filter_uses_title_subtitle_and_authors():
    item = MediaItem(
        id="book-1",
        product_id="book-1",
        title="Neutral",
        subtitle="Praxiswissen",
        media_type="E_BOOK",
        authors=["Ada Lovelace"],
    )

    assert matches_filter(item, ["ada"])
    assert matches_filter(item, ["praxis"])
    assert not matches_filter(item, ["python"])


def test_available_media_reserves_when_lend_fails_because_unavailable():
    class Client:
        host = "example.onleihe.de"
        lent = False
        reserved = False

        def lend(self, product_id: str):
            self.lent = True
            raise OnleiheAPIError(
                "lend failed",
                status_code=409,
                payload={"messageId": "no-available-licences"},
            )

        def reserve(self, product_id: str):
            self.reserved = True
            return {}

    client = Client()
    handled_ids: set[str] = set()
    message, download_path = maybe_lend_or_reserve(
        watched_media(product_id="book-1", available=True),
        client,  # type: ignore[arg-type]
        config=None,  # type: ignore[arg-type]
        gourou_client=None,
        rented_media_ids=handled_ids,
        downloaded_media_ids=set(),
    )

    assert client.lent is True
    assert client.reserved is True
    assert handled_ids == {"book-1"}
    assert message == "auto reserved after lend failed"
    assert download_path is None


def test_available_media_does_not_reserve_after_auth_lend_error():
    class Client:
        reserved = False

        def lend(self, product_id: str):
            raise OnleiheAuthError("auth failed", status_code=401)

        def reserve(self, product_id: str):
            self.reserved = True

    client = Client()

    try:
        maybe_lend_or_reserve(
            watched_media(product_id="book-1", available=True),
            client,  # type: ignore[arg-type]
            config=None,  # type: ignore[arg-type]
            gourou_client=None,
            rented_media_ids=set(),
            downloaded_media_ids=set(),
        )
    except OnleiheAuthError:
        pass
    else:
        raise AssertionError("auth lend errors must be propagated")

    assert client.reserved is False


def test_lend_failure_allows_reserve_only_for_availability_errors():
    assert lend_failure_allows_reserve(
        OnleiheAPIError(
            "lend failed",
            status_code=409,
            payload={"messageId": "no-available-licences"},
        )
    )
    assert not lend_failure_allows_reserve(
        OnleiheAPIError("lend failed", status_code=409, payload={"message": "no licence available maybe"})
    )


def test_my_media_seed_primes_all_lendings_without_keyword_filter():
    class Client:
        def get_my_media_items(self, *, include_player_licences: bool = True):
            return [
                MediaItem(
                    id="loan-1",
                    product_id="loan-1",
                    title="Unrelated Loan",
                    subtitle=None,
                    media_type="E_BOOK",
                    authors=[],
                    lend_id="lend-1",
                )
            ]

    downloaded_ids: set[str] = set()
    count = process_my_media_downloads(
        Client(),  # type: ignore[arg-type]
        config=None,  # type: ignore[arg-type]
        apobj=None,  # type: ignore[arg-type]
        gourou_client=object(),  # type: ignore[arg-type]
        downloaded_media_ids=downloaded_ids,
        seed_only=True,
    )

    assert count == 1
    assert downloaded_ids == {"loan-1"}


def watched_media(*, product_id: str, available: bool) -> WatchedMedia:
    return WatchedMedia(
        product_id=product_id,
        title="Test Book",
        url=f"https://example.onleihe.de/mymedia/mediadetail?productId={product_id}",
        media_type="E_AUDIO",
        authors=(),
        subtitle=None,
        publication_date=None,
        available=available,
        availability_text=None,
        acsm_url=None,
        source="test",
        keyword_required=False,
        keyword_matched=True,
    )
