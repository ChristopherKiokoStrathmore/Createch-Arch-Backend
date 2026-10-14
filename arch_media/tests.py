import io
import tempfile

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from arch_media.models import AdminPin, Enquiry
from arch_media.views import EMAIL_LIMIT, IP_LIMIT
from config.settings import resolve_database_url

MEDIA = tempfile.mkdtemp()
SECRET = "t" * 40


def tiny_png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (120, 80, 40)).save(buf, format="PNG")
    return SimpleUploadedFile("t.png", buf.getvalue(), content_type="image/png")


@override_settings(ARCH_ADMIN_SECRET=SECRET, MEDIA_ROOT=MEDIA)
class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c = APIClient()
        self.auth = {"HTTP_X_ADMIN_KEY": SECRET}

    def enquiry(self, **overrides):
        body = {
            "name": "Amina Okello",
            "email": "amina@example.com",
            "phone": "+254700000000",
            "project_type": "Hotel or lodge",
            "site": "Nairobi",
            "stage": "Concept design",
            "timeline": "On site by June",
            "message": "A short note about the site.",
        }
        body.update(overrides)
        return body

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

    def test_pin_starts_unset(self):
        r = self.c.get("/api/admin/pin/", **self.auth)
        self.assertEqual(r.json(), {"set": False, "version": 0})
        v = self.c.post("/api/admin/pin/", {"pin": "anything"}, format="json", **self.auth)
        self.assertFalse(v.json()["ok"])

    def test_pin_set_verify_and_change(self):
        r = self.c.put("/api/admin/pin/", {"pin": "first-pin"}, format="json", **self.auth)
        self.assertEqual(r.json(), {"set": True, "version": 1})
        ok = self.c.post("/api/admin/pin/", {"pin": "first-pin"}, format="json", **self.auth)
        self.assertTrue(ok.json()["ok"])
        self.c.put("/api/admin/pin/", {"pin": "second-pin"}, format="json", **self.auth)
        old = self.c.post("/api/admin/pin/", {"pin": "first-pin"}, format="json", **self.auth)
        self.assertEqual((old.json()["ok"], old.json()["version"]), (False, 2))

    def test_pin_length_enforced(self):
        r = self.c.put("/api/admin/pin/", {"pin": "123"}, format="json", **self.auth)
        self.assertEqual(r.status_code, 400)

    def test_pin_requires_key(self):
        self.assertEqual(self.c.get("/api/admin/pin/").status_code, 403)
        r = self.c.put("/api/admin/pin/", {"pin": "attacker"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_pin_hash_is_persisted_not_plaintext(self):
        pin = "studio-pin-9"
        r = self.c.put("/api/admin/pin/", {"pin": pin}, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        row = AdminPin.objects.get(key="default")
        self.assertNotEqual(row.pin_hash, pin)
        self.assertTrue(row.pin_hash.startswith("pbkdf2_"))
        self.assertTrue(check_password(pin, row.pin_hash))
        self.assertFalse(check_password("other-pin", row.pin_hash))

    def test_public_enquiry_create_and_admin_crud(self):
        created = self.c.post("/api/enquiries/", self.enquiry(), format="json")
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual(body["name"], "Amina Okello")
        self.assertEqual(body["project_type"], "Hotel or lodge")
        self.assertEqual(body["site"], "Nairobi")
        self.assertFalse(body["read"])
        self.assertIn("created_at", body)
        self.assertEqual(Enquiry.objects.count(), 1)

        denied = self.c.get("/api/admin/enquiries/")
        self.assertEqual(denied.status_code, 403)

        listing = self.c.get("/api/admin/enquiries/", **self.auth)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 1)

        patched = self.c.patch(
            f"/api/admin/enquiries/{body['id']}/",
            {"read": True},
            format="json",
            **self.auth,
        )
        self.assertEqual(patched.status_code, 200)
        self.assertTrue(patched.json()["read"])
        self.assertEqual(
            self.c.patch(
                f"/api/admin/enquiries/{body['id']}/",
                {"read": "yes"},
                format="json",
                **self.auth,
            ).status_code,
            400,
        )
        self.assertEqual(
            self.c.delete(f"/api/admin/enquiries/{body['id']}/").status_code,
            403,
        )
        deleted = self.c.delete(f"/api/admin/enquiries/{body['id']}/", **self.auth)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(Enquiry.objects.count(), 0)

    def test_honeypot_is_not_stored(self):
        r = self.c.post(
            "/api/enquiries/",
            self.enquiry(company="Acme Bots"),
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Enquiry.objects.count(), 0)

    def test_enquiry_validation(self):
        self.assertEqual(
            self.c.post("/api/enquiries/", self.enquiry(email="not-an-email"), format="json").status_code,
            400,
        )
        self.assertEqual(
            self.c.post("/api/enquiries/", self.enquiry(message=""), format="json").status_code,
            400,
        )

    def test_enquiry_rate_limit_by_ip_and_email(self):
        for i in range(IP_LIMIT):
            r = self.c.post(
                "/api/enquiries/",
                self.enquiry(email=f"ip{i}@example.com"),
                format="json",
            )
            self.assertEqual(r.status_code, 201, r.content)
        blocked = self.c.post(
            "/api/enquiries/",
            self.enquiry(email="extra@example.com"),
            format="json",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(Enquiry.objects.count(), IP_LIMIT)

        cache.clear()
        for i in range(EMAIL_LIMIT):
            r = self.c.post(
                "/api/enquiries/",
                self.enquiry(email="repeat@example.com", name=f"N{i}"),
                format="json",
                HTTP_X_ADMIN_KEY=SECRET,
                HTTP_X_CREATECH_CLIENT_IP=f"203.0.113.{i}",
            )
            self.assertEqual(r.status_code, 201, r.content)
        again = self.c.post(
            "/api/enquiries/",
            self.enquiry(email="repeat@example.com"),
            format="json",
            HTTP_X_ADMIN_KEY=SECRET,
            HTTP_X_CREATECH_CLIENT_IP="198.51.100.9",
        )
        self.assertEqual(again.status_code, 429)

    def test_forwarded_ip_requires_admin_key(self):
        for i in range(IP_LIMIT):
            self.c.post(
                "/api/enquiries/",
                self.enquiry(email=f"nat{i}@example.com"),
                format="json",
                HTTP_X_CREATECH_CLIENT_IP="203.0.113.50",
            )
        spoofed = self.c.post(
            "/api/enquiries/",
            self.enquiry(email="spoof@example.com"),
            format="json",
            HTTP_X_CREATECH_CLIENT_IP="203.0.113.51",
        )
        self.assertEqual(spoofed.status_code, 429)
        allowed = self.c.post(
            "/api/enquiries/",
            self.enquiry(email="proxied@example.com"),
            format="json",
            HTTP_X_ADMIN_KEY=SECRET,
            HTTP_X_CREATECH_CLIENT_IP="203.0.113.51",
        )
        self.assertEqual(allowed.status_code, 201, allowed.content)

    def test_production_database_must_be_postgres(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_database_url({}, ["gunicorn", "config.wsgi"])
        with self.assertRaises(ImproperlyConfigured):
            resolve_database_url(
                {"DATABASE_URL": "sqlite:///tmp/db.sqlite3"},
                ["gunicorn", "config.wsgi"],
            )
        with self.assertRaises(ImproperlyConfigured):
            resolve_database_url({}, ["gunicorn", "config.wsgi"],)
        railway = {
            "DATABASE_URL": "sqlite:///tmp/db.sqlite3",
            "RAILWAY_ENVIRONMENT": "production",
            "ALLOW_SQLITE": "True",
        }
        with self.assertRaises(ImproperlyConfigured):
            resolve_database_url(railway, ["gunicorn"])
        url = resolve_database_url(
            {"DATABASE_URL": "postgres://u:p@localhost:5432/createch"},
            ["gunicorn"],
        )
        self.assertTrue(url.startswith("postgres://"))
        tests = resolve_database_url({}, ["manage.py", "test"])
        self.assertTrue(tests.startswith("sqlite:///"))


@override_settings(ARCH_ADMIN_SECRET="generate-a-long-random-string")
class PlaceholderSecretTests(TestCase):
    def test_placeholder_secret_opens_nothing(self):
        c = APIClient()
        r = c.get("/api/admin/chrome/", HTTP_X_ADMIN_KEY="generate-a-long-random-string")
        self.assertEqual(r.status_code, 403)
