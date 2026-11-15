from contextlib import contextmanager
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.database as app_db
import app.main as app_main
from app.config import settings as app_settings
from app.database import Base
from app.modules.auth.models import UserSession
from app.modules.auth.rate_limit import auth_rate_limiter
from app.modules.auth.security import is_safe_redirect_url


@contextmanager
def _test_client(monkeypatch, tmp_path):
    auth_rate_limiter.reset()
    test_db_path = tmp_path / "test_auth.db"
    test_engine = create_engine(
        f"sqlite:///{test_db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(app_settings, "email_mode", "demo")
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_main, "SessionLocal", TestSessionLocal)
    app_main.app.dependency_overrides[app_db.get_db] = _override_get_db

    try:
        with TestClient(app_main.app) as client:
            yield client, TestSessionLocal
    finally:
        app_main.app.dependency_overrides.clear()
        test_engine.dispose()
        auth_rate_limiter.reset()


def _register_owner(client: TestClient, email: str = "owner@example.com", password: str = "Strong!Pass#2026"):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.post(
        "/api/auth/register",
        json={"full_name": "Owner", "email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


def test_login_page_security_headers_and_redirect_safety(monkeypatch, tmp_path):
    with _test_client(monkeypatch, tmp_path) as (client, _):
        resp = client.get("/login")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert "require-trusted-types-for 'script'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'none'" in csp
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert "no-store" in resp.headers.get("cache-control", "")

    assert is_safe_redirect_url("/") is True
    assert is_safe_redirect_url("/?tab=wealth") is True
    assert is_safe_redirect_url("https://evil.com") is False
    assert is_safe_redirect_url("//evil.com") is False
    assert is_safe_redirect_url("/\\evil.com") is False


def test_bootstrap_closes_public_signup_and_hashes_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "allow_public_signup", False)
    with _test_client(monkeypatch, tmp_path) as (client, SessionLocal):
        # Weak password rejected
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        weak = client.post(
            "/api/auth/register",
            json={"full_name": "Owner", "email": "owner@example.com", "password": "password1234"},
            headers={"X-CSRF-Token": csrf},
        )
        assert weak.status_code == 400

        # Valid owner registration succeeds
        res = _register_owner(client)
        assert res.status_code == 201
        raw_session_cookie = client.cookies.get("erp_session")
        assert raw_session_cookie

        # Session token stored only as SHA-256 hash in DB
        with SessionLocal() as db:
            sess = db.scalar(select(UserSession))
            assert sess is not None
            assert sess.token_hash != raw_session_cookie
            assert len(sess.token_hash) == 64

        # Second registration is blocked once owner exists
        res2 = _register_owner(client, email="attacker@example.com")
        assert res2.status_code == 403


def test_csrf_fetch_metadata_and_origin_enforcement(monkeypatch, tmp_path):
    with _test_client(monkeypatch, tmp_path) as (client, _):
        assert _register_owner(client).status_code == 201

        # Missing CSRF token on mutating request -> 403
        no_csrf = client.post("/api/auth/logout")
        assert no_csrf.status_code == 403

        # Wrong CSRF token -> 403
        bad_csrf = client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong-token"})
        assert bad_csrf.status_code == 403

        # Cross-site Fetch Metadata blocked -> 403
        cross_site = client.get(
            "/api/wealth/summary",
            headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"},
        )
        assert cross_site.status_code == 403

        # Cross-origin Origin header on mutation blocked -> 403
        csrf_token = client.cookies.get("erp_csrf", "")
        cross_origin = client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": csrf_token, "Origin": "https://attacker.example"},
        )
        assert cross_origin.status_code == 403

        # Valid CSRF token succeeds
        ok = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})
        assert ok.status_code == 200
        assert "cookies" in ok.headers.get("clear-site-data", "")
        assert client.get("/api/wealth/summary").status_code == 401


def test_account_lockout_and_ip_rate_limit(monkeypatch, tmp_path):
    with _test_client(monkeypatch, tmp_path) as (client, _):
        assert _register_owner(client).status_code == 201
        csrf_token = client.cookies.get("erp_csrf", "")
        assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token}).status_code == 200

        # 5 failed attempts trigger account lockout
        for _ in range(5):
            pre_csrf = client.get("/api/auth/csrf").json()["csrf_token"]
            resp = client.post(
                "/api/auth/login",
                json={"email": "owner@example.com", "password": "Wrong!Password#999"},
                headers={"X-CSRF-Token": pre_csrf},
            )
            assert resp.status_code == 401

        # Even correct password is rejected while locked
        pre_csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        locked_resp = client.post(
            "/api/auth/login",
            json={"email": "owner@example.com", "password": "Strong!Pass#2026"},
            headers={"X-CSRF-Token": pre_csrf},
        )
        assert locked_resp.status_code == 401

        # Hammering beyond rate_limit_max_attempts (15) triggers 429
        got_429 = False
        for _ in range(12):
            pre_csrf = client.get("/api/auth/csrf").json()["csrf_token"]
            r = client.post(
                "/api/auth/login",
                json={"email": "owner@example.com", "password": "Wrong!Password#999"},
                headers={"X-CSRF-Token": pre_csrf},
            )
            if r.status_code == 429:
                got_429 = True
                assert "retry-after" in {k.lower() for k in r.headers.keys()}
                break
        assert got_429


def test_password_change_rotates_and_revokes_prior_sessions(monkeypatch, tmp_path):
    with _test_client(monkeypatch, tmp_path) as (client, _):
        assert _register_owner(client).status_code == 201
        old_session = client.cookies.get("erp_session")
        csrf_token = client.cookies.get("erp_csrf", "")

        res = client.post(
            "/api/auth/change-password",
            json={
                "current_password": "Strong!Pass#2026",
                "new_password": "Newer!Strong#Pass2027",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        new_session = client.cookies.get("erp_session")
        assert new_session and new_session != old_session

        # Old session cookie is no longer valid
        client.cookies.set("erp_session", old_session)
        assert client.get("/api/auth/me").status_code == 401

        # New session cookie works
        client.cookies.set("erp_session", new_session)
        assert client.get("/api/auth/me").status_code == 200


def test_google_oauth_login_and_gmail_email_reading(monkeypatch, tmp_path):
    import base64
    from datetime import date
    from email.message import EmailMessage
    from urllib.parse import parse_qs, urlparse
    import app.modules.auth.google_oauth as google_oauth
    from app.modules.wealth.email_client import GmailApiEmailProvider, get_email_provider

    monkeypatch.setattr(app_settings, "google_client_id", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(app_settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(app_settings, "google_redirect_uri", "http://testserver/api/auth/google/callback")

    # Build a realistic RFC822 receipt email for Gmail API mock
    mime_msg = EmailMessage()
    mime_msg["From"] = "Maybank2u <m2u@maybank.com.my>"
    mime_msg["To"] = "owner@gmail.com"
    mime_msg["Subject"] = "Maybank2u DuitNow Transaction Alert"
    mime_msg["Date"] = "Sun, 15 Nov 2026 10:00:00 +0800"
    mime_msg["Message-ID"] = "<m2u-google-1@maybank.com.my>"
    mime_msg.set_content("You transferred RM 42.50 to GrabFood.")
    raw_b64 = base64.urlsafe_b64encode(mime_msg.as_bytes()).rstrip(b"=").decode("ascii")

    captured = {}

    class _FakeResp:
        def __init__(self, status_code, data):
            self.status_code = status_code
            self._data = data

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None, **kwargs):
            return _FakeResp(
                200,
                {
                    "access_token": "ya29.test-access",
                    "refresh_token": "1//test-refresh",
                    "expires_in": 3600,
                    "id_token": "fake.id.token",
                    "scope": google_oauth.GOOGLE_SCOPES,
                },
            )

        def get(self, url, params=None, headers=None, **kwargs):
            if "tokeninfo" in url:
                return _FakeResp(
                    200,
                    {
                        "aud": "test-client-id.apps.googleusercontent.com",
                        "nonce": captured["nonce"],
                        "email_verified": "true",
                        "sub": "google-sub-123",
                        "email": "owner@gmail.com",
                    },
                )
            if "userinfo" in url:
                return _FakeResp(
                    200,
                    {"sub": "google-sub-123", "email": "owner@gmail.com", "name": "Google Owner"},
                )
            if url.endswith("/messages"):
                return _FakeResp(200, {"messages": [{"id": "gmsg-1", "threadId": "18f0abc123"}]})
            if url.endswith("/messages/gmsg-1"):
                return _FakeResp(
                    200,
                    {"id": "gmsg-1", "threadId": "18f0abc123", "raw": raw_b64},
                )
            return _FakeResp(404, {})

    monkeypatch.setattr(google_oauth.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr("app.modules.wealth.email_client.httpx.Client", _FakeHttpxClient)

    with _test_client(monkeypatch, tmp_path) as (client, SessionLocal):
        # 1. Start Google OAuth flow
        start = client.get("/api/auth/google/start", follow_redirects=False)
        assert start.status_code == 302
        parsed = urlparse(start.headers["location"])
        qs = parse_qs(parsed.query)
        assert qs["code_challenge_method"] == ["S256"]
        assert "https://www.googleapis.com/auth/gmail.readonly" in qs["scope"][0]
        state = qs["state"][0]
        captured["nonce"] = qs["nonce"][0]

        # 2. Tampered state is rejected
        bad_cb = client.get(
            "/api/auth/google/callback?code=abc&state=tampered-state",
            follow_redirects=False,
        )
        assert bad_cb.status_code == 303
        assert "error=google_failed" in bad_cb.headers["location"]

        # 3. Valid callback logs user in, sets session + CSRF cookies, and saves Gmail tokens
        ok_cb = client.get(
            f"/api/auth/google/callback?code=valid-code&state={state}",
            follow_redirects=False,
        )
        assert ok_cb.status_code == 303
        assert ok_cb.headers["location"] == "/"
        assert client.cookies.get("erp_session")

        status_data = client.get("/api/auth/status").json()
        assert status_data["authenticated"] is True
        assert status_data["google_connected"] is True
        assert status_data["user"]["email"] == "owner@gmail.com"

        # 4. get_email_provider(db) now returns GmailApiEmailProvider and reads Google emails
        with SessionLocal() as db:
            provider = get_email_provider(db)
            assert isinstance(provider, GmailApiEmailProvider)
            emails = provider.fetch_messages(start_date=date(2026, 11, 1), end_date=date(2026, 11, 30))
            assert len(emails) == 1
            assert emails[0].sender_email == "m2u@maybank.com.my"
            assert emails[0].gmail_thread_id == "18f0abc123"
            assert "RM 42.50" in emails[0].body

        # 5. On-demand Google email sync endpoint works with CSRF
        csrf_token = client.cookies.get("erp_csrf", "")
        sync_res = client.post("/api/auth/google/sync", headers={"X-CSRF-Token": csrf_token})
        assert sync_res.status_code == 200
        assert "status" in sync_res.json()


def test_multi_user_data_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "allow_public_signup", True)

    with _test_client(monkeypatch, tmp_path) as (client, _):
        # User 1 registers and creates private data
        assert _register_owner(client, email="user1@example.com").status_code == 201
        csrf1 = client.cookies.get("erp_csrf", "")
        entry_res = client.post(
            "/api/wealth/entries/manual",
            json={
                "merchant": "User1 Secret Merchant",
                "category": "Dining",
                "tx_type": "expense",
                "amount_myr": 88.88,
                "occurred_at": "2026-11-15T10:00:00Z",
            },
            headers={"X-CSRF-Token": csrf1},
        )
        assert entry_res.status_code == 200
        u1_entry_id = entry_res.json()["id"]

        put_res = client.put(
            "/api/settings",
            json={
                "auto_sync_enabled": True,
                "auto_sync_hour": 9,
                "auto_sync_lookback_days": 5,
                "monthly_budget_myr": 5555.0,
                "default_calories": 2400,
                "default_protein_g": 160,
            },
            headers={"X-CSRF-Token": csrf1},
        )
        assert put_res.status_code == 200

        # Sign out User 1
        assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf1}).status_code == 200

        # User 2 registers and must NOT see User 1's data or collide on default seeds
        assert _register_owner(client, email="user2@example.com").status_code == 201
        csrf2 = client.cookies.get("erp_csrf", "")

        u2_entries = client.get("/api/wealth/entries?month=2026-11").json()
        assert len(u2_entries) == 0

        u2_settings = client.get("/api/settings").json()
        assert u2_settings["monthly_budget_myr"] == 3000.0

        # User 2 cannot toggle or delete User 1's ledger entry by ID
        toggle_attempt = client.patch(
            f"/api/wealth/entries/{u1_entry_id}/toggle-type",
            headers={"X-CSRF-Token": csrf2},
        )
        assert toggle_attempt.status_code == 404

