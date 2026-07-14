from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import onleiharr.cli as cli
import pytest
from onleiharr._vendor.onleihe import (
    MediaItem,
    OnleiheAPIError,
    OnleiheAuthError,
    OnleiheNotFoundError,
    ProductDetails,
    SearchResultPage,
)
from onleiharr.cli import (
    WatchedMedia,
    build_apprise,
    fetch_all_watched_media,
    fetch_category_watch_media,
    fetch_product_watch_media,
    format_message,
    lend_failure_allows_reserve,
    matches_filter,
    maybe_lend_or_reserve,
    normalize_product_watch_ids,
    notify,
    process_my_media_downloads,
)
from onleiharr.config import NotificationConfig, WatchCategory


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


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
                raw={"product": {"isContainer": False, "containerIds": ["series-1"]}},
            )
        if product_id == "standalone":
            return ProductDetails(
                id=product_id,
                product_id=product_id,
                title="Single Book",
                subtitle=None,
                media_type="E_BOOK",
                raw={"product": {"isContainer": False, "containerIds": []}},
            )
        return ProductDetails(
            id=product_id,
            product_id=product_id,
            title="Finanzen Reihe",
            subtitle=None,
            media_type="SERIES",
            raw={"product": {"isContainer": True, "containerIds": []}},
            included_media=[
                MediaItem(
                    id="issue-1",
                    product_id="issue-1",
                    title="Finanzen 06/2026",
                    subtitle=None,
                    media_type="E_MAGAZINE",
                    authors=[],
                    availability={"isAvailable": True},
                    cover_url="https://static.example/cover.jpg",
                )
            ],
        )

    def search_category_elements(self, category_ids: list[str], *, require_login: bool = False) -> SearchResultPage:
        raise AssertionError("fetch_category_watch_media should build the body from watch options")

    def build_category_search_body(self, category_ids, *, sort):
        assert category_ids == ["cat-1", "cat-2"]
        return {
            "query": [{"query": "U", "fields": ["categories.id"], "operator": "OR"}],
            "postFilters": [],
            "sort": sort,
            "size": 50,
        }

    def search_media(self, *, raw_body, require_login: bool = False) -> SearchResultPage:
        assert require_login is False
        self.last_search_body = raw_body
        self.search_bodies = getattr(self, "search_bodies", [])
        self.search_bodies.append(raw_body)
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
                MediaItem(
                    id="audio-1",
                    product_id="audio-1",
                    title="Python Hoerbuch",
                    subtitle=None,
                    media_type="E_AUDIO",
                    authors=["Ada Lovelace"],
                    availability={"isAvailable": True},
                ),
            ],
            total_items=3,
        )


def test_product_watch_expands_included_media_without_keyword_check():
    media = fetch_product_watch_media(FakeClient(), "series-1")

    assert len(media) == 1
    assert media[0].product_id == "issue-1"
    assert media[0].keyword_required is False
    assert media[0].keyword_matched is True
    assert media[0].url == "https://example.onleihe.de/mymedia/mediadetail?productId=issue-1"
    assert media[0].cover_url == "https://static.example/cover.jpg"


def test_product_watch_resolves_single_issue_container_ids():
    media = fetch_product_watch_media(FakeClient(), "issue-with-container")

    assert [item.product_id for item in media] == ["issue-1"]
    assert media[0].source == "product:issue-with-container"


def test_product_watch_rejects_container_without_included_media():
    class Client(FakeClient):
        def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
            return ProductDetails(
                id=product_id,
                product_id=product_id,
                title="Finanzen Reihe",
                subtitle=None,
                media_type="SERIES",
                raw={"product": {"isContainer": True, "containerIds": []}},
            )

    with pytest.raises(OnleiheAPIError, match="container.*no included media"):
        fetch_product_watch_media(Client(), "series-1")


def test_product_watch_rejects_incomplete_resolved_container():
    class Client(FakeClient):
        def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
            if product_id == "issue-1":
                return ProductDetails(
                    id=product_id,
                    product_id=product_id,
                    title="Finanzen 07/2026",
                    subtitle=None,
                    media_type="E_MAGAZINE",
                    raw={"product": {"isContainer": False, "containerIds": ["series-1"]}},
                )
            return ProductDetails(
                id=product_id,
                product_id=product_id,
                title="Finanzen Reihe",
                subtitle=None,
                media_type="SERIES",
                raw={"product": {"isContainer": True, "containerIds": []}},
            )

    with pytest.raises(OnleiheAPIError, match="resolved to container.*no included media"):
        fetch_product_watch_media(Client(), "issue-1")


def test_normalize_product_watch_ids_resolves_issue_ids_to_deduped_containers():
    class Client(FakeClient):
        def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
            if product_id in {"issue-a", "issue-b"}:
                return ProductDetails(
                    id=product_id,
                    product_id=product_id,
                    title="Finanzen",
                    subtitle="04/2026",
                    media_type="E_MAGAZINE",
                    raw={"product": {"isContainer": False, "containerIds": ["series-1"]}},
                )
            return super().get_product(product_id, include_user_context=include_user_context)

    normalized = normalize_product_watch_ids(Client(), ["issue-a", "issue-b", "series-1"])

    assert normalized == ["series-1"]


def test_normalize_product_watch_ids_keeps_single_product_and_warns(caplog):
    normalized = normalize_product_watch_ids(FakeClient(), ["standalone"])

    assert normalized == ["standalone"]
    assert "single product without container ids" in caplog.text


def test_normalize_product_watch_ids_drops_initial_not_found_ids(caplog):
    class Client(FakeClient):
        def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
            if product_id == "missing":
                raise OnleiheNotFoundError(
                    "not found",
                    status_code=500,
                    payload={"messageId": "no-such-element"},
                )
            return super().get_product(product_id, include_user_context=include_user_context)

    normalized = normalize_product_watch_ids(Client(), ["missing", "series-1"])

    assert normalized == ["series-1"]
    assert "disabling this watch target" in caplog.text


def test_fetch_all_watched_media_logs_not_found_without_traceback(caplog):
    class Client(FakeClient):
        def get_product(self, product_id: str, *, include_user_context: bool = True) -> ProductDetails:
            if product_id == "missing":
                raise OnleiheNotFoundError(
                    "not found",
                    status_code=500,
                    payload={"messageId": "no-such-element"},
                )
            return super().get_product(product_id, include_user_context=include_user_context)

    config = SimpleNamespace(
        general=SimpleNamespace(
            watch_product_ids=["missing"],
            watch_categories=[],
        )
    )

    result = fetch_all_watched_media(Client(), config)  # type: ignore[arg-type]

    assert result.media == []
    assert result.errors == 1
    assert "no longer exists" in caplog.text
    assert "Traceback" not in caplog.text


def test_format_message_includes_subtitle_in_display_title():
    media = watched_media(product_id="magazine-1", available=True)
    media = WatchedMedia(
        product_id=media.product_id,
        title="Stiftung Warentest Finanzen",
        url=media.url,
        media_type=media.media_type,
        authors=media.authors,
        subtitle="06/2026",
        publication_date=media.publication_date,
        available=media.available,
        availability_text=media.availability_text,
        acsm_url=media.acsm_url,
        source=media.source,
        keyword_required=media.keyword_required,
        keyword_matched=media.keyword_matched,
    )

    assert "Stiftung Warentest Finanzen (06/2026)" in format_message(media, "auto lent")


def test_notify_sends_cover_to_image_only_target_when_file_attachment_exists(tmp_path):
    class Server:
        attachment_support = True
        attach_supported_mime_type = "^image/.*"

        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    class Apprise:
        def __init__(self, server):
            self.server = server

        def find(self):
            return [self.server]

        def notify(self, **kwargs):
            raise AssertionError("aggregate notify should not be used with attachments")

    attachment = tmp_path / "download.pdf"
    attachment.write_bytes(b"pdf")
    server = Server()
    apobj = Apprise(server)

    notify(
        apobj,
        "message",
        attachments=[attachment],
        image_urls=["https://static.example/cover.jpg"],
    )  # type: ignore[arg-type]

    assert server.calls == [
        {
            "title": "Onleihe: New media",
            "body": "message",
            "attach": ["https://static.example/cover.jpg"],
        }
    ]


def test_notify_preserves_file_attachment_for_general_attachment_target(tmp_path):
    class Server:
        attachment_support = True

        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    class Apprise:
        def __init__(self, server):
            self.server = server

        def find(self):
            return [self.server]

        def notify(self, **kwargs):
            raise AssertionError("aggregate notify should not be used with attachments")

    attachment = tmp_path / "download.pdf"
    attachment.write_bytes(b"pdf")
    server = Server()
    apobj = Apprise(server)

    notify(
        apobj,
        "message",
        attachments=[attachment],
        image_urls=["https://static.example/cover.jpg"],
    )  # type: ignore[arg-type]

    assert server.calls == [
        {
            "title": "Onleihe: New media",
            "body": "message",
            "attach": [str(attachment)],
        }
    ]


def test_external_auth_notification_contains_renewal_commands(tmp_path):
    class Apprise:
        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    apobj = Apprise()
    config = SimpleNamespace(config_path=tmp_path / "onleiharr.toml")

    cli.notify_external_auth_required(apobj, config)  # type: ignore[arg-type]

    assert apobj.calls[0]["title"] == "Onleiharr: Anmeldung erneuern"
    assert f"onleiharr --login -c {tmp_path / 'onleiharr.toml'}" in apobj.calls[0]["body"]
    assert "systemctl --user restart onleiharr" in apobj.calls[0]["body"]


def test_notify_uses_cover_for_general_target_when_no_file_attachment():
    class Server:
        attachment_support = True

        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    class Apprise:
        def __init__(self, server):
            self.server = server

        def find(self):
            return [self.server]

        def notify(self, **kwargs):
            raise AssertionError("aggregate notify should not be used with attachments")

    server = Server()
    apobj = Apprise(server)

    notify(apobj, "message", image_urls=["https://static.example/cover.jpg"])  # type: ignore[arg-type]

    assert server.calls == [
        {
            "title": "Onleihe: New media",
            "body": "message",
            "attach": ["https://static.example/cover.jpg"],
        }
    ]


def test_notify_omits_cover_url_when_attachments_are_unsupported():
    class Server:
        attachment_support = False

        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    class Apprise:
        def __init__(self, server):
            self.server = server

        def find(self):
            return [self.server]

        def notify(self, **kwargs):
            raise AssertionError("aggregate notify should not be used with image URLs")

    server = Server()
    apobj = Apprise(server)

    notify(apobj, "message", image_urls=["https://static.example/cover.jpg"])  # type: ignore[arg-type]

    assert server.calls == [{"title": "Onleihe: New media", "body": "message"}]


def test_notify_reports_failed_aggregate_delivery():
    class Apprise:
        def notify(self, **kwargs):
            return False

    assert notify(Apprise(), "message") is False  # type: ignore[arg-type]


def test_category_watch_filters_by_keywords():
    client = FakeClient()
    watch = WatchCategory(
        category_ids=["cat-1", "cat-2"],
        keywords=["python"],
        media_types=["E_BOOK", "E_AUDIO"],
        filters=[{"field": "language", "values": ["ger"]}],
        sort_field="licence.stockChangedTimestamp",
        sort_order="DESC",
    )

    result = fetch_category_watch_media(client, watch)

    assert [item.product_id for item in result.media] == ["book-1", "audio-1"]
    assert result.total == 3
    assert result.media[0].keyword_required is True
    assert result.media[0].keyword_matched is True
    assert len(client.search_bodies) == 1
    assert client.last_search_body["sort"] == [{"field": "licence.stockChangedTimestamp", "order": "DESC"}]
    assert client.last_search_body["postFilters"] == [
        {"field": "mediaType", "values": ["E_BOOK", "E_AUDIO"], "type": "TERMS", "operator": "OR"},
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


def test_run_loop_checks_maintenance_after_poll_error_and_retries(monkeypatch):
    class Client:
        def __init__(self):
            self.maintenance_states = [True, False]
            self.closed = False

        def maintenance_active(self):
            return self.maintenance_states.pop(0)

        def close(self):
            self.closed = True

    client = Client()
    calls = []

    config = SimpleNamespace(
        general=SimpleNamespace(
            poll_interval_secs=300.0,
            watch_product_ids=[],
            watch_categories=[],
        ),
        notification=SimpleNamespace(test_notification=False),
        gourou=SimpleNamespace(lendings_poll_interval_secs=0.0),
    )

    monkeypatch.setattr(cli, "build_apprise", lambda config: None)
    monkeypatch.setattr(cli, "create_onleihe_client", lambda config: client)
    monkeypatch.setattr(cli, "build_gourou_client", lambda config: None)
    monkeypatch.setattr(
        cli,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda secs: calls.append(("sleep", secs))),
    )
    monkeypatch.setattr(cli, "login", lambda client, config: calls.append(("login", None)))

    def fake_fetch_all_watched_media(client, config, *, log_summary=False):
        calls.append(("fetch", log_summary))
        errors = 1 if len([call for call in calls if call[0] == "fetch"]) == 1 else 0
        return cli.WatchPollResult(media=[], errors=errors)

    monkeypatch.setattr(cli, "fetch_all_watched_media", fake_fetch_all_watched_media)

    cli.run_loop(config, SimpleNamespace(test_notification=False, once=True))

    assert calls == [("login", None), ("fetch", True), ("sleep", 300.0), ("fetch", True)]
    assert client.closed is True


def test_run_loop_retries_startup_error_during_maintenance(monkeypatch):
    class Client:
        def __init__(self):
            self.maintenance_states = [True, False]
            self.closed = False

        def maintenance_active(self):
            return self.maintenance_states.pop(0)

        def close(self):
            self.closed = True

    client = Client()
    calls = []

    config = SimpleNamespace(
        general=SimpleNamespace(
            poll_interval_secs=300.0,
            watch_product_ids=[],
            watch_categories=[],
        ),
        notification=SimpleNamespace(test_notification=False),
        gourou=SimpleNamespace(lendings_poll_interval_secs=0.0),
    )

    monkeypatch.setattr(cli, "build_apprise", lambda config: None)
    monkeypatch.setattr(cli, "create_onleihe_client", lambda config: client)
    monkeypatch.setattr(cli, "build_gourou_client", lambda config: None)
    monkeypatch.setattr(
        cli,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda secs: calls.append(("sleep", secs))),
    )

    def fake_login(client, config):
        calls.append(("login", None))
        if len([call for call in calls if call[0] == "login"]) == 1:
            raise OnleiheAPIError("Server disconnected without sending a response.")

    monkeypatch.setattr(cli, "login", fake_login)

    def fake_fetch_all_watched_media(client, config, *, log_summary=False):
        calls.append(("fetch", log_summary))
        return cli.WatchPollResult(media=[], errors=0)

    monkeypatch.setattr(cli, "fetch_all_watched_media", fake_fetch_all_watched_media)

    cli.run_loop(config, SimpleNamespace(test_notification=False, once=True))

    assert calls == [("login", None), ("sleep", 300.0), ("login", None), ("fetch", True)]
    assert client.closed is True


def test_run_loop_retries_transient_media_handling_error(monkeypatch):
    class EndTestLoop(Exception):
        pass

    class Client:
        closed = False

        def maintenance_active(self):
            return False

        def close(self):
            self.closed = True

    client = Client()
    existing = watched_media(product_id="existing", available=True)
    new = watched_media(product_id="new", available=True)
    poll_results = iter(
        [
            cli.WatchPollResult(media=[existing]),
            cli.WatchPollResult(media=[existing, new]),
            cli.WatchPollResult(media=[existing, new]),
        ]
    )
    attempts = 0

    def fetch_media(client, config, *, log_summary=False):
        try:
            return next(poll_results)
        except StopIteration:
            raise EndTestLoop from None

    def handle_media(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OnleiheAPIError("transient failure")
        return "handled", None

    config = SimpleNamespace(
        general=SimpleNamespace(
            poll_interval_secs=1.0,
            watch_product_ids=[],
            watch_categories=[],
        ),
        notification=SimpleNamespace(test_notification=False),
        gourou=SimpleNamespace(lendings_poll_interval_secs=0.0),
    )
    monkeypatch.setattr(cli, "build_apprise", lambda config: None)
    monkeypatch.setattr(cli, "create_onleihe_client", lambda config: client)
    monkeypatch.setattr(cli, "build_gourou_client", lambda config: None)
    monkeypatch.setattr(cli, "login", lambda client, config: None)
    monkeypatch.setattr(cli, "fetch_all_watched_media", fetch_media)
    monkeypatch.setattr(cli, "maybe_lend_or_reserve", handle_media)
    monkeypatch.setattr(cli, "notify", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cli,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda secs: None),
    )

    with pytest.raises(EndTestLoop):
        cli.run_loop(config, SimpleNamespace(test_notification=False, once=False))

    assert attempts == 2
    assert client.closed is True


def test_run_loop_retries_notification_without_repeating_lend(monkeypatch):
    class EndTestLoop(Exception):
        pass

    class Client:
        closed = False

        def close(self):
            self.closed = True

    client = Client()
    existing = watched_media(product_id="existing", available=True)
    new = watched_media(product_id="new", available=True)
    poll_results = iter(
        [
            cli.WatchPollResult(media=[existing]),
            cli.WatchPollResult(media=[existing, new]),
            cli.WatchPollResult(media=[existing, new]),
        ]
    )
    lend_attempts = 0
    notification_results = iter([False, True])

    def fetch_media(client, config, *, log_summary=False):
        try:
            return next(poll_results)
        except StopIteration:
            raise EndTestLoop from None

    def handle_media(
        media,
        client,
        config,
        gourou_client,
        rented_media_ids,
        downloaded_media_ids,
    ):
        nonlocal lend_attempts
        if media.product_id in rented_media_ids:
            return "already handled this run", None
        lend_attempts += 1
        rented_media_ids.add(media.product_id)
        return "auto lent", None

    config = SimpleNamespace(
        general=SimpleNamespace(
            poll_interval_secs=1.0,
            watch_product_ids=[],
            watch_categories=[],
        ),
        notification=SimpleNamespace(test_notification=False),
        gourou=SimpleNamespace(lendings_poll_interval_secs=0.0),
    )
    monkeypatch.setattr(cli, "build_apprise", lambda config: object())
    monkeypatch.setattr(cli, "create_onleihe_client", lambda config: client)
    monkeypatch.setattr(cli, "build_gourou_client", lambda config: None)
    monkeypatch.setattr(cli, "login", lambda client, config: None)
    monkeypatch.setattr(cli, "fetch_all_watched_media", fetch_media)
    monkeypatch.setattr(cli, "maybe_lend_or_reserve", handle_media)
    monkeypatch.setattr(cli, "notify", lambda *args, **kwargs: next(notification_results))
    monkeypatch.setattr(
        cli,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda secs: None),
    )

    with pytest.raises(EndTestLoop):
        cli.run_loop(config, SimpleNamespace(test_notification=False, once=False))

    assert lend_attempts == 1
    assert client.closed is True


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

        def maintenance_active(self):
            return False

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


def test_available_media_does_not_reserve_during_maintenance():
    class Client:
        reserved = False

        def lend(self, product_id: str):
            raise OnleiheAPIError(
                "lend failed",
                status_code=409,
                payload={"messageId": "no-available-licences"},
            )

        def maintenance_active(self):
            return True

        def reserve(self, product_id: str):
            self.reserved = True

    client = Client()

    with pytest.raises(cli.MaintenanceDetectedError):
        maybe_lend_or_reserve(
            watched_media(product_id="book-1", available=True),
            client,  # type: ignore[arg-type]
            config=None,  # type: ignore[arg-type]
            gourou_client=None,
            rented_media_ids=set(),
            downloaded_media_ids=set(),
        )

    assert client.reserved is False


def test_any_lend_api_error_checks_maintenance():
    class Client:
        maintenance_checks = 0

        def lend(self, product_id: str):
            raise OnleiheAPIError("service unavailable", status_code=503)

        def maintenance_active(self):
            self.maintenance_checks += 1
            return True

    client = Client()

    with pytest.raises(cli.MaintenanceDetectedError):
        maybe_lend_or_reserve(
            watched_media(product_id="book-1", available=True),
            client,  # type: ignore[arg-type]
            config=None,  # type: ignore[arg-type]
            gourou_client=None,
            rented_media_ids=set(),
            downloaded_media_ids=set(),
        )

    assert client.maintenance_checks == 1


def test_available_media_does_not_reserve_after_auth_lend_error():
    class Client:
        reserved = False

        def lend(self, product_id: str):
            raise OnleiheAuthError("auth failed", status_code=401)

        def reserve(self, product_id: str):
            self.reserved = True

        def maintenance_active(self):
            return False

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


def test_build_apprise_returns_none_without_targets():
    config = SimpleNamespace(
        notification=NotificationConfig(
            urls=[],
            apprise_config_path=None,
            test_notification=False,
            email=None,
        )
    )

    assert build_apprise(config) is None  # type: ignore[arg-type]


def test_init_config_existing_interactive_decline_keeps_file(tmp_path, monkeypatch):
    config_path = tmp_path / "onleiharr.toml"
    config_path.write_text("existing", encoding="utf-8")
    called = False

    def fake_wizard(path, *, version):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(cli, "run_first_start_wizard", fake_wizard)
    monkeypatch.setattr(sys, "stdin", TtyStringIO("\n"))
    monkeypatch.setattr(sys, "stdout", TtyStringIO())

    assert cli.main(["--init-config", "-c", str(config_path)]) == 0
    assert called is False
    assert config_path.read_text(encoding="utf-8") == "existing"


def test_init_config_existing_interactive_accept_runs_wizard(tmp_path, monkeypatch):
    config_path = tmp_path / "onleiharr.toml"
    config_path.write_text("existing", encoding="utf-8")
    called_with = None

    def fake_wizard(path, *, version):
        nonlocal called_with
        called_with = path
        path.write_text("new", encoding="utf-8")
        return True

    monkeypatch.setattr(cli, "run_first_start_wizard", fake_wizard)
    monkeypatch.setattr(sys, "stdin", TtyStringIO("y\n"))
    monkeypatch.setattr(sys, "stdout", TtyStringIO())

    assert cli.main(["--init-config", "-c", str(config_path)]) == 0
    assert called_with == config_path
    assert config_path.read_text(encoding="utf-8") == "new"


def test_init_config_existing_non_interactive_refuses_overwrite(tmp_path, monkeypatch):
    config_path = tmp_path / "onleiharr.toml"
    config_path.write_text("existing", encoding="utf-8")
    called = False

    def fake_wizard(path, *, version):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(cli, "run_first_start_wizard", fake_wizard)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert cli.main(["--init-config", "-c", str(config_path)]) == 1
    assert called is False
    assert config_path.read_text(encoding="utf-8") == "existing"


def test_invalid_config_interactive_decline_keeps_file(tmp_path, monkeypatch):
    config_path = tmp_path / "onleiharr.toml"
    config_path.write_text("watch_product_ids = ['legacy-url']\n", encoding="utf-8")
    called = False

    def fake_wizard(path, *, version):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(cli, "run_first_start_wizard", fake_wizard)
    monkeypatch.setattr(sys, "stdin", TtyStringIO("\n"))
    monkeypatch.setattr(sys, "stdout", TtyStringIO())

    assert cli.main(["--once", "-c", str(config_path)]) == 1
    assert called is False
    assert config_path.exists()


def test_invalid_config_interactive_accept_deletes_and_runs_wizard(tmp_path, monkeypatch):
    config_path = tmp_path / "onleiharr.toml"
    config_path.write_text("watch_product_ids = ['legacy-url']\n", encoding="utf-8")
    run_called = False

    def fake_wizard(path, *, version):
        assert not path.exists()
        path.write_text(
            """
[general]
poll_interval_secs = 300.0
watch_product_ids = []

[notification]
urls = []

[credentials]
host = "niedersachsen.onleihe.de"
onleihe_name = "Onleihe Niedersachsen"
library_name = "Stadtbibliothek Achim"
username = "user"
password = "secret"
""",
            encoding="utf-8",
        )
        return True

    def fake_run_loop(config, args):
        nonlocal run_called
        run_called = True

    monkeypatch.setattr(cli, "run_first_start_wizard", fake_wizard)
    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    monkeypatch.setattr(sys, "stdin", TtyStringIO("y\n"))
    monkeypatch.setattr(sys, "stdout", TtyStringIO())

    assert cli.main(["--once", "-c", str(config_path)]) == 0
    assert run_called is True
    assert "watch_product_ids = []" in config_path.read_text(encoding="utf-8")


def test_invalid_config_non_interactive_does_not_delete(tmp_path, monkeypatch):
    config_path = tmp_path / "onleiharr.toml"
    config_path.write_text("watch_product_ids = ['legacy-url']\n", encoding="utf-8")
    called = False

    def fake_wizard(path, *, version):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(cli, "run_first_start_wizard", fake_wizard)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert cli.main(["--once", "-c", str(config_path)]) == 1
    assert called is False
    assert config_path.exists()


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
