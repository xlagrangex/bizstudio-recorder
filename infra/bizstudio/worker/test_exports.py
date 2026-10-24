import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError

import exports


class FakeS3:
    def __init__(self):
        self.objects: dict[str, dict] = {}

    def put(self, key, body=b"", metadata=None, etag="abc"):
        self.objects[key] = {"body": body, "metadata": metadata or {}, "etag": etag,
                             "modified": datetime.now(timezone.utc)}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        item = self.objects[Key]
        return {"ETag": f'"{item["etag"]}"', "Metadata": item["metadata"]}

    def get_object(self, Bucket, Key):
        body = self.objects[Key]["body"]
        return {"Body": type("B", (), {"read": lambda self: body})()}

    def download_file(self, bucket, key, path):
        Path(path).write_bytes(self.objects[key]["body"])

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.put(key, Path(path).read_bytes(), ExtraArgs.get("Metadata"))
        self.objects[key]["type"] = ExtraArgs["ContentType"]

    def put_object(self, Bucket, Key, Body, ContentType):
        self.put(Key, Body)

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k, "LastModified": v["modified"]}
                             for k, v in self.objects.items() if k.startswith(Prefix)]}


def queue(client, video_id, export_format, owner="owner1"):
    client.put(f"export-queue/{video_id}--{export_format}.json", json.dumps({"ownerId": owner}).encode())


class MarkerTest(unittest.TestCase):
    def test_valid_markers_parse(self):
        self.assertEqual(exports.parse_marker("export-queue/en8dj9y7a6s3ssd--mp4-720.json"), ("en8dj9y7a6s3ssd", "mp4-720"))

    def test_unknown_format_and_bad_ids_are_rejected(self):
        for key in ["export-queue/abc--mov.json", "export-queue/../x--mp3.json", "export-queue/abc.json", "export-queue/abc--mp3"]:
            self.assertIsNone(exports.parse_marker(key), key)


class DrainTest(unittest.TestCase):
    def test_malformed_markers_are_dropped(self):
        client = FakeS3()
        client.put("export-queue/abc--mov.json", b"{}")
        exports.drain(client, "cap", lambda *_: None)
        self.assertEqual(client.objects, {})

    def test_failure_leaves_an_error_note(self):
        client = FakeS3()
        queue(client, "vid1", "mp3")
        exports.drain(client, "cap", lambda *_: None)
        self.assertIn("owner1/vid1/exports/mp3.error", client.objects)
        self.assertNotIn("export-queue/vid1--mp3.json", client.objects)

    def test_up_to_date_export_is_not_rerendered(self):
        client = FakeS3()
        client.put("owner1/vid1/result.mp4", b"not a video", etag="e1")
        client.put("owner1/vid1/exports/audio.mp3", b"old", {"source-etag": "e1"})
        queue(client, "vid1", "mp3")
        exports.drain(client, "cap", lambda *_: None)
        self.assertEqual(client.objects["owner1/vid1/exports/audio.mp3"]["body"], b"old")
        self.assertNotIn("owner1/vid1/exports/mp3.error", client.objects)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RenderTest(unittest.TestCase):
    def test_every_format_renders_from_a_real_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
                            "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
            client = FakeS3()
            client.put("owner1/vid1/result.mp4", source.read_bytes(), etag="e1")
            for export_format in exports.FORMATS:
                queue(client, "vid1", export_format)
            exports.drain(client, "cap", lambda *_: None)
            errors = {k: v["body"] for k, v in client.objects.items() if k.endswith(".error")}
            self.assertEqual(errors, {})
            for spec in exports.FORMATS.values():
                item = client.objects[f"owner1/vid1/exports/{spec['file']}"]
                self.assertGreater(len(item["body"]), 0)
                self.assertEqual(item["metadata"], {"source-etag": "e1"})
                self.assertEqual(item["type"], spec["type"])
            height = Path(tmp) / "720.mp4"
            height.write_bytes(client.objects["owner1/vid1/exports/video-720p.mp4"]["body"])
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=height",
                                    "-of", "csv=p=0", str(height)], capture_output=True, text=True, check=True)
            self.assertEqual(probe.stdout.strip(), "720")


if __name__ == "__main__":
    unittest.main()
