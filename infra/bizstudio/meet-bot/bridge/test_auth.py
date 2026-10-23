"""Auth gate tests with a stubbed cap-web session endpoint: python test_auth.py"""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

SESSIONS = {
    "owner": {"user": {"id": "cng79m7693kkajy", "email": "vincenzopetronebiz@gmail.com"}},
    "owner-by-email": {"user": {"id": "otherid", "email": "VincenzoPetroneBiz@gmail.com"}},
    "stranger": {"user": {"id": "someoneelse", "email": "x@example.com"}},
    "expired": {},
}


class FakeCap(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        token = (self.headers.get("Cookie") or "").split("next-auth.session-token=")[-1].split(";")[0]
        body = json.dumps(SESSIONS.get(token, {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def serve(handler) -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


os.environ.update(
    STATE_DIR=tempfile.mkdtemp(),
    CAP_URL=serve(FakeCap),
    MEETBOT_API_TOKEN="test-token",
    PUBLIC_URL="https://rec.bizstudio.it",
    ATTENDEE_URL="http://127.0.0.1:9",
)
import bridge  # noqa: E402

bridge.conn = bridge.connect()
BASE = serve(bridge.Handler) + "/meetbot"


def get(path, cookie=None, **headers):
    if cookie:
        headers["Cookie"] = f"__Secure-next-auth.session-token={cookie}"
    return requests.get(BASE + path, headers=headers, allow_redirects=False, timeout=5)


def post(path, cookie=None, **headers):
    if cookie:
        headers["Cookie"] = f"__Secure-next-auth.session-token={cookie}"
    return requests.post(BASE + path, data={"meeting_url": "not-a-meet-link"}, headers=headers, allow_redirects=False, timeout=5)


class AuthGate(unittest.TestCase):
    def test_page_without_cookie_redirects_to_login(self):
        r = get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "https://rec.bizstudio.it/login?next=/meetbot/")

    def test_expired_session_redirects_to_login(self):
        self.assertEqual(get("/", "expired").status_code, 302)

    def test_owner_session_sees_page(self):
        r = get("/", "owner")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Registra un Meet", r.text)
        self.assertEqual(get("/", "owner-by-email").status_code, 200)

    def test_other_user_is_forbidden(self):
        self.assertEqual(get("/", "stranger").status_code, 403)
        self.assertEqual(post("/join", "stranger", Origin="https://rec.bizstudio.it").status_code, 403)

    def test_session_post_requires_same_origin(self):
        self.assertEqual(post("/join", "owner", Origin="https://evil.example").status_code, 403)
        self.assertEqual(post("/join", "owner").status_code, 403)
        # Reaches the handler: rejected only because the link is invalid.
        r = post("/join", "owner", Origin="https://rec.bizstudio.it")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Link non valido", r.text)
        self.assertEqual(post("/join", "owner", **{"Sec-Fetch-Site": "same-origin"}).status_code, 400)

    def test_api_bearer_token(self):
        self.assertEqual(get("/api/bots").status_code, 401)
        self.assertEqual(get("/api/bots", Authorization="Bearer wrong").status_code, 403)
        self.assertEqual(get("/api/bots", Authorization="Bearer test-token").status_code, 200)
        self.assertEqual(get("/", Authorization="Bearer test-token").status_code, 403)
        self.assertEqual(get("/api/bots", "owner").status_code, 200)

    def test_health_is_public(self):
        self.assertEqual(get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
