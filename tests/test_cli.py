from __future__ import annotations

from onleiharr._vendor.onleihe import MediaItem, ProductDetails, SearchResultPage
from onleiharr.cli import fetch_category_watch_media, fetch_product_watch_media, matches_filter
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
