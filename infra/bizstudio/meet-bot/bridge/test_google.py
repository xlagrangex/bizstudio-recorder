"""Google OAuth and Calendar API tests against a fake Google: python test_google.py"""

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

CLIENT_ID = "1234567890-abcdef.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-fake-secret"
OWNER_COOKIE = "__Secure-next-auth.session-token=owner"


def id_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"email": email, "sub": "1"}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJSUzI1NiJ9.{payload}.sig"


class FakeGoogle(BaseHTTPRequestHandler):
    challenges: dict[str, str] = {}
    codes: dict[str, str] = {}
    revoked: list[str] = []
    refreshes = 0
    refresh_error: str | None = None
    grant_scope = "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/calendar.events.readonly"
    valid_access: set[str] = set()
    event_requests: list[dict] = []

    def log_message(self, *args):
        pass

    def reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/o/oauth2/v2/auth":
            code = f"code-{len(FakeGoogle.codes)}"
            FakeGoogle.codes[code] = query["code_challenge"]
            self.send_response(302)
            self.send_header("Location", f"{query['redirect_uri']}?state={query['state']}&code={code}")
            self.end_headers()
            return
        if url.path == "/calendar/v3/calendars/primary/events":
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            if token not in FakeGoogle.valid_access:
                return self.reply(401, {"error": {"code": 401}})
            FakeGoogle.event_requests.append(query)
            if "pageToken" not in query:
                return self.reply(200, {"items": [{"id": "e1", "summary": "Uno"}], "nextPageToken": "p2"})
            return self.reply(200, {"items": [{"id": "e2", "summary": "Due"}]})
        self.reply(404, {})

    def do_POST(self):
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        if self.path == "/revoke":
            FakeGoogle.revoked.append(form["token"])
            return self.reply(200, {})
        if self.path != "/token":
            return self.reply(404, {})
        if (form.get("client_id"), form.get("client_secret")) != (CLIENT_ID, CLIENT_SECRET):
            return self.reply(401, {"error": "invalid_client"})
        if form["grant_type"] == "authorization_code":
            challenge = FakeGoogle.codes.pop(form["code"], None)
            verifier_hash = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if challenge != verifier_hash or not form["redirect_uri"].endswith("/meetbot/google/callback"):
                return self.reply(400, {"error": "invalid_grant", "error_description": "secret detail"})
            FakeGoogle.valid_access.add("access-1")
            return self.reply(200, {"access_token": "access-1", "expires_in": 3599, "refresh_token": "refresh-1",
                                    "scope": FakeGoogle.grant_scope, "token_type": "Bearer",
                                    "id_token": id_token("vincenzopetronebiz@gmail.com")})
        if form["grant_type"] == "refresh_token":
            if FakeGoogle.refresh_error or form["refresh_token"] != "refresh-1":
                return self.reply(400, {"error": FakeGoogle.refresh_error or "invalid_grant"})
            FakeGoogle.refreshes += 1
            token = f"access-r{FakeGoogle.refreshes}"
            FakeGoogle.valid_access.add(token)
            return self.reply(200, {"access_token": token, "expires_in": 3599, "token_type": "Bearer"})
        self.reply(400, {"error": "unsupported_grant_type"})


class FakeCap(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        token = (self.headers.get("Cookie") or "").split("next-auth.session-token=")[-1].split(";")[0]
        users = {"owner": {"id": "cng79m7693kkajy", "email": "vincenzopetronebiz@gmail.com"},
                 "owner2": {"id": "cng79m7693kkajy", "email": "vincenzopetronebiz@gmail.com"}}
        body = json.dumps({"user": users[token]} if token in users else {}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def serve(handler) -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


GOOGLE = serve(FakeGoogle)
STATE = tempfile.mkdtemp()
os.environ.update(
    STATE_DIR=STATE, CAP_URL=serve(FakeCap), MEETBOT_API_TOKEN="test-token", PUBLIC_URL="https://rec.bizstudio.it",
    ATTENDEE_URL="http://127.0.0.1:9", GOOGLE_AUTH_URL=f"{GOOGLE}/o/oauth2/v2/auth", GOOGLE_TOKEN_URL=f"{GOOGLE}/token",
    GOOGLE_REVOKE_URL=f"{GOOGLE}/revoke", GOOGLE_CALENDAR_API=f"{GOOGLE}/calendar/v3",
)
import bridge  # noqa: E402
import gcal  # noqa: E402

bridge.conn = bridge.connect()
BASE = serve(bridge.Handler) + "/meetbot"
SAME_ORIGIN = {"Origin": "https://rec.bizstudio.it"}


def reset() -> None:
    bridge.google.path.unlink(missing_ok=True)
    bridge.google.pending.clear()
    FakeGoogle.revoked.clear()
    FakeGoogle.event_requests.clear()
    FakeGoogle.refresh_error = None
    FakeGoogle.grant_scope = "openid email https://www.googleapis.com/auth/calendar.events.readonly"
    with bridge.db_lock:
        bridge.conn.execute("DELETE FROM settings")
        bridge.conn.commit()


def get(path: str, cookie: str = OWNER_COOKIE) -> requests.Response:
    return requests.get(BASE + path, headers={"Cookie": cookie}, allow_redirects=False, timeout=5)


def post(path: str, data: dict | None = None, cookie: str = OWNER_COOKIE) -> requests.Response:
    return requests.post(BASE + path, data=data or {}, headers={"Cookie": cookie, **SAME_ORIGIN}, allow_redirects=False, timeout=5)


def connect_flow(cookie: str = OWNER_COOKIE, callback_cookie: str | None = None) -> requests.Response:
    to_google = get("/google/connect", cookie)
    assert to_google.status_code == 302, to_google.status_code
    back = requests.get(to_google.headers["Location"], allow_redirects=False, timeout=5)
    callback = urlsplit(back.headers["Location"])
    assert callback.netloc == "rec.bizstudio.it" and callback.path == "/meetbot/google/callback"
    return get(f"/google/callback?{callback.query}", callback_cookie or cookie)


class OAuthClient(unittest.TestCase):
    def setUp(self):
        reset()
        self.google = gcal.Google(Path(STATE) / "unit.json", "https://rec.bizstudio.it/meetbot/google/callback")
        self.google.path.unlink(missing_ok=True)
        self.google.set_credentials(CLIENT_ID, CLIENT_SECRET)

    def authorize(self, session: str = "s1") -> dict:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.google.authorize_url(session)).query).items()}

    def test_authorize_url(self):
        params = self.authorize()
        self.assertEqual(params["client_id"], CLIENT_ID)
        self.assertEqual(params["redirect_uri"], "https://rec.bizstudio.it/meetbot/google/callback")
        self.assertEqual(params["scope"], "openid email https://www.googleapis.com/auth/calendar.events.readonly")
        self.assertEqual((params["response_type"], params["access_type"], params["prompt"]), ("code", "offline", "consent"))
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertGreaterEqual(len(params["state"]), 40)
        self.assertNotEqual(params["state"], self.authorize()["state"])

    def code_for(self, params: dict) -> str:
        code = f"code-unit-{params['state'][:6]}"
        FakeGoogle.codes[code] = params["code_challenge"]
        return code

    def test_code_exchange_saves_tokens_privately(self):
        params = self.authorize("s1")
        email = self.google.finish(params["state"], self.code_for(params), "s1")
        self.assertEqual(email, "vincenzopetronebiz@gmail.com")
        self.assertTrue(self.google.connected())
        self.assertEqual(oct(self.google.path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(self.google.load()["refresh_token"], "refresh-1")
        with self.assertRaises(gcal.GoogleError):
            self.google.finish(params["state"], self.code_for(params), "s1")

    def test_state_is_bound_to_session_and_expires(self):
        params = self.authorize("s1")
        with self.assertRaisesRegex(gcal.GoogleError, "altra sessione"):
            self.google.finish(params["state"], self.code_for(params), "s2")
        with self.assertRaisesRegex(gcal.GoogleError, "scaduta"):
            self.google.finish("forged", "code", "s1")
        params = self.authorize("s1")
        state = params["state"]
        hashed, verifier, _ = self.google.pending[state]
        self.google.pending[state] = (hashed, verifier, time.time() - 1)
        with self.assertRaisesRegex(gcal.GoogleError, "scaduta"):
            self.google.finish(state, self.code_for(params), "s1")
        self.assertFalse(self.google.connected())

    def test_wrong_verifier_is_rejected_without_leaking_details(self):
        params = self.authorize("s1")
        FakeGoogle.codes["bad"] = "not-the-challenge"
        with self.assertRaises(gcal.GoogleError) as caught:
            self.google.finish(params["state"], "bad", "s1")
        self.assertIn("invalid_grant", str(caught.exception))
        self.assertNotIn("secret detail", str(caught.exception))

    def test_missing_calendar_scope_is_refused_and_revoked(self):
        FakeGoogle.grant_scope = "openid email"
        params = self.authorize("s1")
        with self.assertRaisesRegex(gcal.GoogleError, "calendario"):
            self.google.finish(params["state"], self.code_for(params), "s1")
        self.assertFalse(self.google.connected())
        self.assertEqual(FakeGoogle.revoked, ["refresh-1"])

    def connected(self) -> None:
        params = self.authorize("s1")
        self.google.finish(params["state"], self.code_for(params), "s1")

    def test_access_token_is_cached_then_refreshed(self):
        self.connected()
        self.assertEqual(self.google.access_token(), "access-1")
        self.google.update(expires_at=time.time() + 30)
        refreshed = self.google.access_token()
        self.assertTrue(refreshed.startswith("access-r"))
        self.assertEqual(self.google.access_token(), refreshed)
        self.assertEqual(self.google.load()["refresh_token"], "refresh-1")

    def test_revoked_refresh_token_disconnects(self):
        self.connected()
        self.google.update(expires_at=0)
        FakeGoogle.refresh_error = "invalid_grant"
        with self.assertRaises(gcal.Revoked):
            self.google.access_token()
        self.assertFalse(self.google.connected())
        self.assertTrue(self.google.configured())

    def test_list_events_paginates_and_retries_on_401(self):
        self.connected()
        self.google.update(access_token="stale")
        items = self.google.list_events(1_793_000_000, 1_793_086_400)
        self.assertEqual([i["id"] for i in items], ["e1", "e2"])
        first = FakeGoogle.event_requests[0]
        self.assertEqual((first["singleEvents"], first["orderBy"]), ("true", "startTime"))
        self.assertEqual((first["timeMin"], first["timeMax"]), ("2026-10-26T07:33:20Z", "2026-10-27T07:33:20Z"))
        self.assertEqual(FakeGoogle.event_requests[1]["pageToken"], "p2")

    def test_disconnect_revokes_and_forgets(self):
        self.connected()
        self.assertTrue(self.google.disconnect())
        self.assertEqual(FakeGoogle.revoked, ["refresh-1"])
        self.assertFalse(self.google.connected())
        self.assertTrue(self.google.configured())
        self.assertNotIn("access_token", self.google.load())

    def test_credentials_validation(self):
        with self.assertRaises(ValueError):
            self.google.set_credentials("not-a-client-id", CLIENT_SECRET)
        with self.assertRaises(ValueError):
            self.google.set_credentials(CLIENT_ID, "")


class BridgeRoutes(unittest.TestCase):
    def setUp(self):
        reset()

    def test_setup_panel_until_credentials_are_saved(self):
        page = get("/").text
        self.assertIn("Configurazione Google (una tantum)", page)
        self.assertIn("https://rec.bizstudio.it/meetbot/google/callback", page)
        self.assertNotIn("Collega il tuo account Google Calendar", page)
        connect = get("/google/connect")
        self.assertEqual(connect.headers["Location"], "https://rec.bizstudio.it/dashboard/meet-bot")
        self.assertIn("Configura prima", get("/").text)
        self.assertEqual(post("/google/credentials", {"client_id": "x", "client_secret": "y"}).status_code, 400)
        self.assertEqual(post("/google/credentials", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}).status_code, 200)
        self.assertEqual(oct(bridge.google.path.stat().st_mode & 0o777), "0o600")
        page = get("/").text
        self.assertIn('href="/meetbot/google/connect" target="_top"', page)
        self.assertIn("Collega il tuo account Google Calendar", page)
        self.assertNotIn(CLIENT_SECRET, page)
        self.assertNotIn("Configurazione Google (una tantum)", page)

    def test_full_connect_flow_returns_to_dashboard(self):
        post("/google/credentials", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        done = connect_flow()
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done.headers["Location"], "https://rec.bizstudio.it/dashboard/meet-bot")
        page = get("/").text
        self.assertIn("Google Calendar collegato come vincenzopetronebiz@gmail.com", page)
        self.assertIn("Collegato come <b>vincenzopetronebiz@gmail.com</b>", page)
        self.assertIn("Sincronizza ora", page)
        api = requests.get(BASE + "/api/calendar", headers={"Authorization": "Bearer test-token"}, timeout=5).text
        for secret in ("refresh-1", "access-1", CLIENT_SECRET):
            self.assertNotIn(secret, api)
            self.assertNotIn(secret, page)
        self.assertTrue(json.loads(api)["connected"])
        self.assertEqual(post("/google/disconnect").status_code, 200)
        self.assertEqual(FakeGoogle.revoked, ["refresh-1"])
        self.assertIn("Collega il tuo account Google Calendar", get("/").text)

    def test_callback_from_another_session_is_refused(self):
        post("/google/credentials", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        done = connect_flow(callback_cookie="__Secure-next-auth.session-token=owner2")
        self.assertEqual(done.status_code, 302)
        self.assertFalse(bridge.google.connected())
        self.assertIn("altra sessione", get("/").text)

    def test_callback_requires_login_and_handles_denial(self):
        post("/google/credentials", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        self.assertTrue(get("/google/callback?state=x&code=y", cookie="").headers["Location"].startswith("https://rec.bizstudio.it/login"))
        denied = get("/google/callback?error=access_denied&state=x")
        self.assertEqual(denied.headers["Location"], "https://rec.bizstudio.it/dashboard/meet-bot")
        self.assertIn("Collegamento annullato", get("/").text)
        self.assertFalse(bridge.google.connected())


if __name__ == "__main__":
    unittest.main(verbosity=2)
