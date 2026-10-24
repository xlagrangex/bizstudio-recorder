"""Bot identity (name, camera tile, greeting) and the public tile assets: python test_branding.py"""

import base64
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

import requests

os.environ.update(
    STATE_DIR=tempfile.mkdtemp(),
    MEETBOT_API_TOKEN="test-token",
    PUBLIC_URL="https://rec.bizstudio.it",
    ATTENDEE_URL="http://127.0.0.1:9",
)
os.environ.pop("BOT_NAME", None)
import bridge  # noqa: E402

bridge.conn = bridge.connect()
server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}/meetbot"


def jpeg_size(data: bytes) -> tuple[int, int]:
    index = 2
    while index < len(data):
        marker, length = data[index + 1], struct.unpack(">H", data[index + 2:index + 4])[0]
        if 0xC0 <= marker <= 0xC2:
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return width, height
        index += 2 + length
    raise ValueError("no SOF marker")


class FakeAttendee:
    def __init__(self, state="joined_recording", output_error=None):
        self.state, self.output_error, self.calls = state, output_error, []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, json.loads(kwargs["data"]) if "data" in kwargs else None))
        if path.endswith("/output_video") and self.output_error:
            raise RuntimeError(f"Attendee POST {path} -> 400: {self.output_error}")
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"id": "bot_x", "state": self.state, "events": []}).encode()
        return response


class BotIdentity(unittest.TestCase):
    def test_payload_brands_calendar_and_manual_bots_alike(self):
        payload = bridge.bot_payload("https://meet.google.com/abc-defg-hij", "Demo")
        self.assertEqual(payload["bot_name"], "Bizstudio Notetaker")
        self.assertEqual(payload["bot_image"]["type"], "image/jpeg")
        image = base64.b64decode(payload["bot_image"]["data"])
        self.assertEqual(image[:2], b"\xff\xd8")
        self.assertEqual(jpeg_size(image), (1280, 720))
        message = payload["bot_chat_message"]
        self.assertEqual(message["to"], "everyone")
        self.assertTrue(all(ord(char) <= 0xFFFF for char in message["message"]))
        self.assertEqual(payload["metadata"], {"source": "meetbot", "title": "Demo"})

    def test_launch_sends_the_branded_payload(self):
        fake = FakeAttendee(state="joining")
        original = bridge.attendee
        bridge.attendee = fake
        try:
            bridge.launch("https://meet.google.com/abc-defg-hij?authuser=0", None)
        finally:
            bridge.attendee = original
            with bridge.db_lock:
                bridge.conn.execute("DELETE FROM bots")
                bridge.conn.commit()
        method, path, body = fake.calls[0]
        self.assertEqual((method, path), ("POST", "/bots"))
        self.assertEqual(body["meeting_url"], "https://meet.google.com/abc-defg-hij")
        self.assertIn("bot_image", body)
        self.assertIn("bot_chat_message", body)


class VideoTile(unittest.TestCase):
    def setUp(self):
        bridge.video_attempts.clear()
        self.original = bridge.attendee

    def tearDown(self):
        bridge.attendee = self.original
        with bridge.db_lock:
            bridge.conn.execute("DELETE FROM bots")
            bridge.conn.commit()

    def active_bot(self):
        now = time.time()
        with bridge.db_lock:
            bridge.conn.execute(
                "INSERT INTO bots (bot_id, meeting_url, attendee_state, created_at, updated_at) VALUES ('bot_x', 'https://meet.google.com/abc-defg-hij', 'joining', ?, ?)",
                (now, now),
            )
            bridge.conn.commit()
        return bridge.rows("bot_id = 'bot_x'")[0]

    def outputs(self, fake):
        return [body for _, path, body in fake.calls if path.endswith("/output_video")]

    def test_loop_starts_once_when_the_bot_is_in(self):
        bridge.attendee = fake = FakeAttendee(state="waiting_room")
        row = self.active_bot()
        bridge.step(row)
        self.assertEqual(self.outputs(fake), [])
        fake.state = "joined_recording"
        bridge.step(bridge.rows("bot_id = 'bot_x'")[0])
        bridge.step(bridge.rows("bot_id = 'bot_x'")[0])
        self.assertEqual(self.outputs(fake), [{
            "url": "https://rec.bizstudio.it/meetbot/assets/notetaker.mp4", "loop": True, "mute_video": True,
        }])

    def test_already_playing_counts_as_done_and_errors_retry_three_times(self):
        bridge.attendee = fake = FakeAttendee(output_error='{"error": "Bot is already playing media."}')
        for _ in range(3):
            bridge.start_video("bot_a")
        self.assertEqual(len(self.outputs(fake)), 1)
        fake.output_error = '{"error": "Bot is in state joining and cannot play media"}'
        for _ in range(5):
            bridge.start_video("bot_b")
        self.assertEqual(len(self.outputs(fake)), 4)


class TileAssets(unittest.TestCase):
    def test_video_is_public_cross_origin_and_seekable(self):
        size = (bridge.ASSETS_DIR / "notetaker.mp4").stat().st_size
        full = requests.get(f"{BASE}/assets/notetaker.mp4", timeout=5)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.headers["Content-Type"], "video/mp4")
        self.assertEqual(full.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(full.headers["Accept-Ranges"], "bytes")
        self.assertEqual(len(full.content), size)
        part = requests.get(f"{BASE}/assets/notetaker.mp4", headers={"Range": "bytes=100-199"}, timeout=5)
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.headers["Content-Range"], f"bytes 100-199/{size}")
        self.assertEqual(part.content, full.content[100:200])
        tail = requests.get(f"{BASE}/assets/notetaker.mp4", headers={"Range": "bytes=-10"}, timeout=5)
        self.assertEqual(tail.content, full.content[-10:])
        open_ended = requests.get(f"{BASE}/assets/notetaker.mp4", headers={"Range": "bytes=0-"}, timeout=5)
        self.assertEqual((open_ended.status_code, len(open_ended.content)), (206, size))
        beyond = requests.get(f"{BASE}/assets/notetaker.mp4", headers={"Range": f"bytes={size}-"}, timeout=5)
        self.assertEqual(beyond.status_code, 416)
        head = requests.head(f"{BASE}/assets/notetaker.mp4", timeout=5)
        self.assertEqual((head.status_code, head.headers["Content-Length"], head.content), (200, str(size), b""))

    def test_only_the_named_assets_skip_the_gate(self):
        for path in ("/assets/../bridge.py", "/assets/bridge.py", "/assets/", "/api/bots"):
            response = requests.get(BASE + path, allow_redirects=False, timeout=5)
            self.assertIn(response.status_code, (302, 401, 404), path)
            self.assertNotIn(b"import", response.content)


if __name__ == "__main__":
    unittest.main()
