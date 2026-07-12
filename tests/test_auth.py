from __future__ import annotations

import stat

from onleiharr._vendor.onleihe import SessionState
from onleiharr.auth import external_login_manual, load_session, save_session


def test_session_round_trip_uses_private_file(tmp_path):
    path = tmp_path / "session.json"
    session = SessionState(
        access_token="access",
        refresh_token="refresh",
        user_id="user-id",
        profile_id="master",
        library_id="library-id",
        onleihe_id="onleihe-id",
    )

    save_session(path, session)

    assert load_session(path) == session
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_manual_external_login_validates_pasted_callback(monkeypatch):
    class FakeClient:
        host = "muenchen.onleihe.de"

        def get_login_method(self):
            return {"loginType": "OPEN_ID"}

        def build_open_id_authorization_url(self, method, *, redirect_url, state):
            assert redirect_url == "https://muenchen.onleihe.de"
            return f"https://provider.invalid/authorize?state={state}"

        def login_open_id(self, code, *, redirect_url):
            assert code == "authorization-code"
            assert redirect_url == "https://muenchen.onleihe.de"
            return SessionState(access_token="access")

    monkeypatch.setattr("onleiharr.auth.secrets.token_urlsafe", lambda size: "expected-state")
    session = external_login_manual(
        FakeClient(),  # type: ignore[arg-type]
        input_func=lambda prompt: (
            "https://muenchen.onleihe.de/?code=authorization-code&state=expected-state"
        ),
    )
    assert session.access_token == "access"
