"""Regression tests for the Render web service and explicit camera flow."""

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PhotoShareServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        data_dir = Path(cls._tempdir.name)
        os.environ["DATABASE_PATH"] = str(data_dir / "links.sqlite3")
        os.environ["LEGACY_DATABASE_PATH"] = str(data_dir / "missing-links.json")
        os.environ["BOT_TOKEN"] = "123456:TEST_TOKEN"
        os.environ["WEBSITE_URL"] = "https://photos.example.test"

        import database
        import server

        cls.database = database
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls._tempdir.cleanup()

    def setUp(self):
        self.client = self.server.app.test_client()
        self.jpeg = b"\xff\xd8\xfftest-jpeg-data"
        self.link_id = self.database.create_link(
            987654, base64.b64encode(b"\xff\xd8\xfforiginal-photo").decode(),
            "A test photo was shared.",
        )

    def test_health_and_shared_page(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json["status"], "healthy")

        page = self.client.get(f"/view/{self.link_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"A test photo was shared.", page.data)
        self.assertIn(b"Telegram account that shared this link", page.data)
        self.assertIn(b"Nothing is captured or sent automatically", page.data)
        self.assertIn(b"Agree &amp; enable camera", page.data)
        self.assertEqual(page.headers["Permissions-Policy"], "camera=(self), microphone=(), geolocation=()")

    def test_photo_is_forwarded_only_through_capture_post(self):
        photo_data = "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode()
        with patch.object(self.server, "send_photo_to_telegram", return_value=True) as send:
            response = self.client.post(
                "/api/capture",
                json={"link_id": self.link_id, "photo": photo_data},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "sent")
        send.assert_called_once_with("987654", self.jpeg)
        self.assertEqual(self.database.get_link(self.link_id)["photos_received"], 1)

    def test_invalid_and_inactive_links_are_rejected(self):
        invalid = self.client.post(
            "/api/capture",
            json={"link_id": self.link_id, "photo": "not-a-data-url"},
        )
        self.assertEqual(invalid.status_code, 400)

        photo_data = "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode()
        missing = self.client.post(
            "/api/capture", json={"link_id": "does-not-exist", "photo": photo_data}
        )
        self.assertEqual(missing.status_code, 404)

        self.database.deactivate_link(self.link_id)
        inactive_page = self.client.get(f"/view/{self.link_id}")
        inactive_api = self.client.post(
            "/api/capture",
            json={"link_id": self.link_id, "photo": photo_data},
        )
        self.assertEqual(inactive_page.status_code, 410)
        self.assertEqual(inactive_api.status_code, 410)

    def test_starting_telegram_application_builds_polling_updater(self):
        from bot import get_bot_app

        application = get_bot_app()
        self.assertIsNotNone(application.updater)


if __name__ == "__main__":
    unittest.main()
