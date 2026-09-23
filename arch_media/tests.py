import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

MEDIA = tempfile.mkdtemp()


def tiny_png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (120, 80, 40)).save(buf, format="PNG")
    return SimpleUploadedFile("t.png", buf.getvalue(), content_type="image/png")


@override_settings(ARCH_ADMIN_SECRET="test-secret", MEDIA_ROOT=MEDIA)
class ApiTests(TestCase):
    def setUp(self):
        self.c = APIClient()
        self.auth = {"HTTP_X_ADMIN_KEY": "test-secret"}

    def test_upload_rejected_without_key(self):
        r = self.c.post("/api/admin/media/", {"file": tiny_png()}, format="multipart")
        self.assertEqual(r.status_code, 403)

    def test_upload_and_list_with_key(self):
        r = self.c.post(
            "/api/admin/media/",
            {"file": tiny_png(), "alt": "hero"},
            format="multipart",
            **self.auth,
        )
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertIn("url", body)
        self.assertEqual(body["width"], 4)
        listing = self.c.get("/api/admin/media/", **self.auth)
        self.assertEqual(len(listing.json()), 1)

    def test_chrome_put_then_public_get(self):
        payload = {"data": {"hero": {"line": "From line to built"}}}
        r = self.c.put("/api/admin/chrome/", payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        pub = self.c.get("/api/chrome/")
        self.assertEqual(pub.json()["data"]["hero"]["line"], "From line to built")

    def test_project_publish_visibility(self):
        created = self.c.post(
            "/api/admin/projects/",
            {"slug": "villa-01", "title": "Villa 01", "status": "completed"},
            format="json",
            **self.auth,
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(len(self.c.get("/api/projects/").json()), 0)
        self.c.patch(
            "/api/admin/projects/villa-01/",
            {"publish": True},
            format="json",
            **self.auth,
        )
        published = self.c.get("/api/projects/").json()
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["slug"], "villa-01")

    def test_ongoing_filter(self):
        for slug, st in [("a", "ongoing"), ("b", "completed")]:
            self.c.post(
                "/api/admin/projects/",
                {"slug": slug, "title": slug.upper(), "status": st, "publish": True},
                format="json",
                **self.auth,
            )
        ongoing = self.c.get("/api/projects/?status=ongoing").json()
        self.assertEqual([p["slug"] for p in ongoing], ["a"])
