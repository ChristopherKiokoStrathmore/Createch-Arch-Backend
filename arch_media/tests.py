import io
import mimetypes
import os
import tempfile
from unittest.mock import patch

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import OperationalError
from django.test import RequestFactory, TestCase, override_settings
from django.views.static import serve
from PIL import Image
from rest_framework.test import APIClient

from arch_media.content_migrate import add_project_category_slots
from arch_media.models import (
    AdminPin,
    Category,
    Enquiry,
    EnquiryRecipient,
    MediaAsset,
    Project,
    SiteChrome,
    SiteSettings,
)
from arch_media.views import EMAIL_LIMIT, IP_LIMIT
from config.settings import (
    INSECURE_SECRET_DEFAULT,
    log_missing_production_env,
    missing_production_env,
    resolve_allowed_hosts,
    resolve_database_url,
    resolve_secret_key,
)

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

    def test_public_chrome_hides_stored_draft(self):
        payload = {
            "data": {
                "hero": {"line": "From line to built"},
                "draft": {"hero": {"line": "Unpublished"}},
            }
        }
        saved = self.c.put("/api/admin/chrome/", payload, format="json", **self.auth)
        self.assertEqual(saved.status_code, 200, saved.content)
        admin = self.c.get("/api/admin/chrome/", **self.auth)
        self.assertEqual(admin.status_code, 200, admin.content)
        self.assertEqual(admin.json()["data"]["draft"], {"hero": {"line": "Unpublished"}})
        pub = self.c.get("/api/chrome/")
        self.assertEqual(pub.status_code, 200, pub.content)
        self.assertNotIn("draft", pub.json()["data"])
        self.assertEqual(pub.json()["data"]["hero"]["line"], "From line to built")

    def test_chrome_versions_restore_keeps_draft_and_stay_private(self):
        denied = self.c.get("/api/admin/chrome/versions/")
        self.assertEqual(denied.status_code, 403)
        first = self.c.put(
            "/api/admin/chrome/",
            {"data": {"hero": "A", "draft": {"note": "keep me"}, "versions": [{"id": 1}]}},
            format="json",
            **self.auth,
        )
        self.assertEqual(first.status_code, 200, first.content)
        second = self.c.put(
            "/api/admin/chrome/",
            {"data": {"hero": "B", "draft": {"note": "keep me"}}},
            format="json",
            **self.auth,
        )
        self.assertEqual(second.status_code, 200, second.content)
        listing = self.c.get("/api/admin/chrome/versions/", **self.auth)
        self.assertEqual(listing.status_code, 200, listing.content)
        versions = listing.json()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["data"], {"hero": "A"})
        self.assertNotIn("draft", versions[0]["data"])
        self.assertNotIn("versions", versions[0]["data"])
        self.assertTrue(versions[0]["label"])
        self.assertIn("created_at", versions[0])

        restored = self.c.post(
            f"/api/admin/chrome/versions/{versions[0]['id']}/restore/",
            {},
            format="json",
            **self.auth,
        )
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertEqual(restored.json()["data"]["hero"], "A")
        self.assertEqual(restored.json()["data"]["draft"], {"note": "keep me"})
        self.assertNotIn("versions", restored.json()["data"])
        admin = self.c.get("/api/admin/chrome/", **self.auth)
        self.assertEqual(admin.json()["data"]["draft"], {"note": "keep me"})
        public = self.c.get("/api/chrome/").json()["data"]
        self.assertEqual(public, {"hero": "A"})
        self.assertNotIn("draft", public)
        self.assertNotIn("versions", public)
        missing = self.c.post("/api/admin/chrome/versions/9999/restore/", {}, format="json", **self.auth)
        self.assertEqual(missing.status_code, 404)

        for index in range(12):
            saved = self.c.put(
                "/api/admin/chrome/",
                {"data": {"hero": f"v{index}", "draft": {"note": "keep me"}}},
                format="json",
                **self.auth,
            )
            self.assertEqual(saved.status_code, 200, saved.content)
        history = self.c.get("/api/admin/chrome/versions/", **self.auth).json()
        self.assertEqual(len(history), 10)
        stored = {row["data"]["hero"] for row in history}
        self.assertNotIn("A", stored)
        self.assertIn("v10", stored)
        self.assertNotIn("v11", stored)

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
        too_long = self.c.put("/api/admin/pin/", {"pin": "x" * 129}, format="json", **self.auth)
        self.assertEqual(too_long.status_code, 400)
        control = self.c.put("/api/admin/pin/", {"pin": "ab\ncd"}, format="json", **self.auth)
        self.assertEqual(control.status_code, 400)

    def test_pin_accepts_letters_symbols_spaces_and_existing_numeric(self):
        pin = " Naïve #4! "
        created = self.c.put("/api/admin/pin/", {"pin": pin}, format="json", **self.auth)
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(created.json(), {"set": True, "version": 1})
        ok = self.c.post("/api/admin/pin/", {"pin": pin}, format="json", **self.auth)
        self.assertTrue(ok.json()["ok"])
        self.assertFalse(
            self.c.post("/api/admin/pin/", {"pin": pin.strip()}, format="json", **self.auth).json()["ok"]
        )
        self.assertFalse(
            self.c.post("/api/admin/pin/", {"pin": pin.casefold()}, format="json", **self.auth).json()["ok"]
        )
        wrong = self.c.post("/api/admin/pin/", {"pin": "wrong-pin"}, format="json", **self.auth)
        self.assertEqual(wrong.status_code, 200)
        self.assertFalse(wrong.json()["ok"])

        # The live database already has this numeric PIN. Verification must
        # accept the stored hash without setting it again.
        AdminPin.objects.filter(key="default").delete()
        AdminPin.objects.create(key="default", pin_hash=make_password("1234"), version=7)
        legacy = self.c.post("/api/admin/pin/", {"pin": "1234"}, format="json", **self.auth)
        self.assertEqual(legacy.status_code, 200, legacy.content)
        self.assertTrue(legacy.json()["ok"])
        self.assertEqual(legacy.json()["version"], 7)
        self.assertFalse(
            self.c.post("/api/admin/pin/", {"pin": "1234 "}, format="json", **self.auth).json()["ok"]
        )
        edges = self.c.put("/api/admin/pin/", {"pin": "1234"}, format="json", **self.auth)
        self.assertEqual(edges.status_code, 200, edges.content)
        self.assertTrue(
            self.c.post("/api/admin/pin/", {"pin": "1234"}, format="json", **self.auth).json()["ok"]
        )

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
        self.assertEqual(body["status"], "new")
        self.assertIn("created_at", body)
        self.assertNotIn("read", body)
        self.assertEqual(Enquiry.objects.count(), 1)

        denied = self.c.get("/api/admin/enquiries/")
        self.assertEqual(denied.status_code, 403)

        listing = self.c.get("/api/admin/enquiries/", **self.auth)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["count"], 1)
        self.assertEqual(len(listing.json()["results"]), 1)
        self.assertEqual(listing.json()["results"][0]["id"], body["id"])

        patched = self.c.patch(
            f"/api/admin/enquiries/{body['id']}/",
            {"status": "replied"},
            format="json",
            **self.auth,
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["status"], "replied")
        self.assertEqual(
            self.c.patch(
                f"/api/admin/enquiries/{body['id']}/",
                {"status": "yes"},
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

    def test_site_enquiry_form_is_stored(self):
        form = {
            "name": "Amina Okello",
            "email": "form@example.com",
            "phone": "+254700000000",
            "projectType": "Restaurant, bar or café",
            "location": "Mombasa",
            "stage": "Just an idea",
            "timeline": "On site by June",
            "message": "A terrace and a small bar.",
            "company": "",
        }
        saved = self.c.post("/api/enquiries/", form, format="multipart")
        self.assertEqual(saved.status_code, 201, saved.content)
        row = Enquiry.objects.get(email="form@example.com")
        self.assertEqual(row.project_type, "Restaurant, bar or café")
        self.assertEqual(row.site, "Mombasa")
        self.assertEqual(row.stage, "Just an idea")
        self.assertEqual(row.timeline, "On site by June")
        self.assertEqual(row.message, "A terrace and a small bar.")
        self.assertEqual(row.status, "new")
        self.assertIsNotNone(row.created_at)

        numbered = self.c.post(
            "/api/enquiries/",
            {"name": "Num", "email": "num@example.com", "phone": 254700000000, "message": "Call me"},
            format="json",
        )
        self.assertEqual(numbered.status_code, 201, numbered.content)
        self.assertEqual(Enquiry.objects.get(email="num@example.com").phone, "254700000000")

    def test_enquiry_inbox_filter_summary_and_order(self):
        for index, enquiry_status in enumerate(("new", "replied", "archived", "new")):
            Enquiry.objects.create(
                name=f"Person {index}",
                email=f"box{index}@example.com",
                message="Hello",
                status=enquiry_status,
            )
        denied = self.c.get("/api/admin/enquiries/summary/")
        self.assertEqual(denied.status_code, 403)
        summary = self.c.get("/api/admin/enquiries/summary/", **self.auth)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json(), {"new": 2, "replied": 1, "archived": 1})

        newest = self.c.get("/api/admin/enquiries/", **self.auth)
        self.assertEqual([row["name"] for row in newest.json()["results"]], ["Person 3", "Person 2", "Person 1", "Person 0"])
        replied = self.c.get("/api/admin/enquiries/?status=replied", **self.auth)
        self.assertEqual(replied.json()["count"], 1)
        self.assertEqual(replied.json()["results"][0]["status"], "replied")
        bad = self.c.get("/api/admin/enquiries/?status=read", **self.auth)
        self.assertEqual(bad.status_code, 400)

        for index in range(21):
            Enquiry.objects.create(name=f"Page {index}", email=f"page{index}@example.com", message="Hello")
        first = self.c.get("/api/admin/enquiries/", **self.auth)
        self.assertEqual(first.json()["count"], 25)
        self.assertEqual(len(first.json()["results"]), 20)
        self.assertEqual(first.json()["results"][0]["name"], "Page 20")
        second = self.c.get("/api/admin/enquiries/?page=2", **self.auth)
        self.assertEqual(len(second.json()["results"]), 5)
        self.assertIsNone(second.json()["next"])

    def test_enquiry_email_is_best_effort(self):
        mail_env = {
            "EMAIL_HOST": "smtp.example.com",
            "EMAIL_PORT": "587",
            "EMAIL_HOST_USER": "studio@createch.co.ke",
            "EMAIL_HOST_PASSWORD": "secret-not-logged",
            "EMAIL_USE_TLS": "true",
            "DEFAULT_FROM_EMAIL": "studio@createch.co.ke",
            "ENQUIRY_NOTIFY_TO": "anvi@createch.co.ke, studio@createch.co.ke",
        }
        with patch.dict(os.environ, mail_env, clear=False):
            with patch("arch_media.views.send_mail") as send:
                saved = self.c.post("/api/enquiries/", self.enquiry(email="mail@example.com"), format="json")
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(Enquiry.objects.filter(email="mail@example.com").count(), 1)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], ["anvi@createch.co.ke"])
        self.assertEqual(kwargs["from_email"], "studio@createch.co.ke")
        self.assertIn("Amina Okello", kwargs["message"])
        self.assertIn("mail@example.com", kwargs["message"])
        self.assertIn("A short note about the site.", kwargs["message"])
        self.assertIn("https://createch.co.ke/admin", kwargs["message"])
        self.assertNotIn("secret-not-logged", kwargs["message"])

        with patch.dict(os.environ, mail_env, clear=False):
            with patch("arch_media.views.send_mail", side_effect=OSError("smtp down")):
                with self.assertLogs("arch_media.views", level="WARNING") as failed:
                    still = self.c.post(
                        "/api/enquiries/",
                        self.enquiry(email="failed-mail@example.com"),
                        format="json",
                    )
        self.assertEqual(still.status_code, 201, still.content)
        self.assertEqual(Enquiry.objects.filter(email="failed-mail@example.com").count(), 1)
        self.assertTrue(any("email alert failed" in line for line in failed.output))

        blank = {key: "" for key in mail_env}
        with patch.dict(os.environ, blank, clear=False):
            with patch("arch_media.views.send_mail") as send:
                with self.assertLogs("arch_media.views", level="WARNING") as missing:
                    quiet = self.c.post(
                        "/api/enquiries/",
                        self.enquiry(email="no-mail@example.com"),
                        format="json",
                    )
        self.assertEqual(quiet.status_code, 201, quiet.content)
        self.assertEqual(Enquiry.objects.filter(email="no-mail@example.com").count(), 1)
        send.assert_not_called()
        self.assertTrue(any("mail environment is incomplete" in line for line in missing.output))

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

    def test_non_ascii_admin_key_is_forbidden(self):
        response = self.c.get("/api/admin/pin/", HTTP_X_ADMIN_KEY="é" * 10)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"Traceback", response.content)

    def test_reorder_accepts_object_and_readme_list(self):
        for slug in ("alpha", "beta"):
            created = self.c.post(
                "/api/admin/projects/",
                {"slug": slug, "title": slug, "publish": True},
                format="json",
                **self.auth,
            )
            self.assertEqual(created.status_code, 201, created.content)
        listed = self.c.post(
            "/api/admin/projects/reorder/",
            [{"slug": "beta", "order": 0}, {"slug": "alpha", "order": 1}],
            format="json",
            **self.auth,
        )
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(Project.objects.get(slug="beta").order, 0)
        self.assertEqual(Project.objects.get(slug="alpha").order, 1)
        wrapped = self.c.post(
            "/api/admin/projects/reorder/",
            {"order": ["alpha", "beta"]},
            format="json",
            **self.auth,
        )
        self.assertEqual(wrapped.status_code, 200, wrapped.content)
        self.assertEqual(Project.objects.get(slug="alpha").order, 0)
        self.assertEqual(Project.objects.get(slug="beta").order, 1)

        uploaded = self.c.post(
            "/api/admin/media/",
            {"file": tiny_png(), "alt": "one"},
            format="multipart",
            **self.auth,
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        asset_id = uploaded.json()["id"]
        media = self.c.post(
            "/api/admin/media/reorder/",
            [{"id": asset_id, "sort": 4}],
            format="json",
            **self.auth,
        )
        self.assertEqual(media.status_code, 200, media.content)
        self.assertEqual(media.json()["count"], 1)
        self.assertEqual(MediaAsset.objects.get(pk=asset_id).sort, 4)

    def test_enquiry_accepts_camel_case_aliases(self):
        aliased_body = self.enquiry(email="alias@example.com")
        aliased_body.pop("site")
        aliased_body["projectType"] = "Hotel or lodge"
        aliased_body["location"] = "Mombasa"
        aliased = self.c.post("/api/enquiries/", aliased_body, format="json")
        self.assertEqual(aliased.status_code, 201, aliased.content)
        self.assertEqual(aliased.json()["project_type"], "Hotel or lodge")
        self.assertEqual(aliased.json()["site"], "Mombasa")
        canonical = self.c.post(
            "/api/enquiries/",
            self.enquiry(
                project_type="Lodge",
                projectType="Ignored",
                site="Kisumu",
                location="Ignored",
                email="canonical@example.com",
            ),
            format="json",
        )
        self.assertEqual(canonical.status_code, 201, canonical.content)
        self.assertEqual(canonical.json()["project_type"], "Lodge")
        self.assertEqual(canonical.json()["site"], "Kisumu")

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_pin_verify_is_rate_limited(self):
        created = self.c.put("/api/admin/pin/", {"pin": "studio-pin"}, format="json", **self.auth)
        self.assertEqual(created.status_code, 200, created.content)
        for _ in range(10):
            ok = self.c.post("/api/admin/pin/", {"pin": "nope"}, format="json", **self.auth)
            self.assertEqual(ok.status_code, 200, ok.content)
        blocked = self.c.post("/api/admin/pin/", {"pin": "nope"}, format="json", **self.auth)
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
        state = self.c.get("/api/admin/pin/", **self.auth)
        self.assertEqual(state.status_code, 200)

    def test_gallery_url_is_absolute(self):
        uploaded = self.c.post(
            "/api/admin/media/",
            {"file": tiny_png(), "alt": "cover"},
            format="multipart",
            **self.auth,
        )
        project = self.c.post(
            "/api/admin/projects/",
            {"slug": "with-photo", "title": "With photo"},
            format="json",
            **self.auth,
        )
        self.assertEqual(project.status_code, 201, project.content)
        attached = self.c.post(
            "/api/admin/projects/with-photo/gallery/",
            {"asset_id": uploaded.json()["id"], "sort": 1},
            format="json",
            **self.auth,
        )
        self.assertEqual(attached.status_code, 201, attached.content)
        self.assertTrue(attached.json()["asset"]["url"].startswith("http://"))

    def test_ip_window_starts_at_first_hit(self):
        real_set = cache.set
        real_incr = cache.incr
        sets = []
        incrs = []

        def track_set(key, value, timeout=None, version=None):
            sets.append(key)
            return real_set(key, value, timeout, version=version)

        def track_incr(key, delta=1, version=None):
            incrs.append(key)
            return real_incr(key, delta, version=version)

        with patch("arch_media.views.cache.set", side_effect=track_set), patch(
            "arch_media.views.cache.incr", side_effect=track_incr
        ):
            first = self.c.post("/api/enquiries/", self.enquiry(email="one@example.com"), format="json")
            second = self.c.post("/api/enquiries/", self.enquiry(email="two@example.com"), format="json")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        ip_sets = [key for key in sets if str(key).startswith("enquiry:ip:")]
        ip_incrs = [key for key in incrs if str(key).startswith("enquiry:ip:")]
        self.assertEqual(ip_sets, ip_incrs)
        self.assertEqual(len(ip_sets), 1)

    def test_enquiry_and_chrome_reject_oversized_bodies(self):
        bulky = {"name": "A", "email": "a@example.com", "message": "x" * 80}
        with override_settings(ENQUIRY_MAX_BODY_BYTES=40):
            rejected = self.c.post("/api/enquiries/", bulky, format="json")
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(Enquiry.objects.count(), 0)
        with override_settings(CHROME_MAX_BODY_BYTES=30):
            chrome = self.c.put(
                "/api/admin/chrome/",
                {"data": {"hero": "x" * 80}},
                format="json",
                **self.auth,
            )
        self.assertEqual(chrome.status_code, 413, chrome.content)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=80, ENQUIRY_MAX_BODY_BYTES=10_000)
    def test_django_json_memory_cap_rejects_enquiry(self):
        payload = {"name": "A" * 40, "email": "cap@example.com", "message": "B" * 40}
        rejected = self.c.post("/api/enquiries/", payload, format="json")
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(Enquiry.objects.filter(email="cap@example.com").count(), 0)

    @override_settings(ARCH_MAX_UPLOAD_BYTES=10)
    def test_upload_size_cap(self):
        rejected = self.c.post(
            "/api/admin/media/",
            {"file": tiny_png(), "alt": "too big"},
            format="multipart",
            **self.auth,
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("file", rejected.json())

    def test_webp_is_served_as_image_webp(self):
        self.assertEqual(mimetypes.guess_type("hero.webp")[0], "image/webp")
        self.assertEqual(mimetypes.guess_type("hero.png")[0], "image/png")
        self.assertEqual(mimetypes.guess_type("hero.jpg")[0], "image/jpeg")
        self.assertEqual(mimetypes.guess_type("hero.gif")[0], "image/gif")
        buf = io.BytesIO()
        Image.new("RGBA", (2, 2), (1, 2, 3, 128)).save(buf, format="WEBP")
        uploaded = self.c.post(
            "/api/admin/media/",
            {"file": SimpleUploadedFile("hero.webp", buf.getvalue(), content_type="image/webp"), "alt": "webp"},
            format="multipart",
            **self.auth,
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        relative = uploaded.json()["url"].split("/media/", 1)[1]
        response = serve(RequestFactory().get("/media/" + relative), relative, document_root=MEDIA)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertTrue(relative.endswith(".webp"))

    def test_upload_is_resized_and_exif_is_stripped(self):
        image = Image.new("RGB", (3000, 1200), (8, 16, 24))
        exif = image.getexif()
        exif[270] = "secret-camera"
        buf = io.BytesIO()
        image.save(buf, format="JPEG", exif=exif.tobytes(), quality=95)
        uploaded = self.c.post(
            "/api/admin/media/",
            {
                "file": SimpleUploadedFile("wide.jpg", buf.getvalue(), content_type="image/jpeg"),
                "alt": "wide",
            },
            format="multipart",
            **self.auth,
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        body = uploaded.json()
        self.assertEqual(body["width"], 2400)
        self.assertEqual(body["height"], 960)
        self.assertTrue(body["url"].endswith(".jpg"))
        asset = MediaAsset.objects.get(pk=body["id"])
        with asset.file.open("rb") as handle:
            saved = Image.open(handle)
            saved.load()
            self.assertEqual(saved.format, "JPEG")
            self.assertLessEqual(max(saved.size), 2400)
            self.assertFalse(saved.getexif())

        alpha = io.BytesIO()
        Image.new("RGBA", (10, 20), (1, 2, 3, 200)).save(alpha, format="PNG")
        webp = self.c.post(
            "/api/admin/media/",
            {
                "file": SimpleUploadedFile("mark.png", alpha.getvalue(), content_type="image/png"),
                "alt": "mark",
            },
            format="multipart",
            **self.auth,
        )
        self.assertEqual(webp.status_code, 201, webp.content)
        self.assertTrue(webp.json()["url"].endswith(".webp"))
        self.assertEqual(webp.json()["width"], 10)
        self.assertEqual(webp.json()["height"], 20)

        rejected = self.c.post(
            "/api/admin/media/",
            {"file": SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain"), "alt": "nope"},
            format="multipart",
            **self.auth,
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)

    def test_health_ok(self):
        response = self.c.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_health_database_down(self):
        with patch("arch_media.views.connection.cursor", side_effect=OperationalError("down")):
            response = self.c.get("/api/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["ok"], False)
        self.assertEqual(response.json()["error"], "database")

    @override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": "createch_cache",
            }
        }
    )
    def test_health_requires_cache_table(self):
        missing = self.c.get("/api/health/")
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(missing.json(), {"ok": False, "error": "cache"})
        call_command("createcachetable", "createch_cache")
        ready = self.c.get("/api/health/")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json(), {"ok": True})

    def test_secret_key_fails_closed_outside_local_dev(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key({}, ["gunicorn", "config.wsgi"])
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key({"DJANGO_SECRET_KEY": "short"}, ["gunicorn"])
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key({"DJANGO_SECRET_KEY": INSECURE_SECRET_DEFAULT}, ["gunicorn"])
        key = resolve_secret_key({"DJANGO_SECRET_KEY": "k" * 40}, ["gunicorn"])
        self.assertEqual(key, "k" * 40)
        self.assertEqual(resolve_secret_key({}, ["manage.py", "test"]), INSECURE_SECRET_DEFAULT)
        self.assertTrue(resolve_secret_key({"DEBUG": "True"}, ["runserver"]))
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key(
                {"DEBUG": "True", "RAILWAY_ENVIRONMENT": "production"},
                ["gunicorn"],
            )

    def test_allowed_hosts_do_not_default_to_wildcard(self):
        hosts = resolve_allowed_hosts({}, ["gunicorn"])
        self.assertNotIn("*", hosts)
        self.assertEqual(
            hosts,
            [".up.railway.app", "createch.co.ke", "www.createch.co.ke"],
        )
        self.assertEqual(resolve_allowed_hosts({"DEBUG": "True"}, ["runserver"]), ["*"])
        self.assertEqual(resolve_allowed_hosts({"ALLOW_SQLITE": "True"}, ["runserver"]), ["*"])
        self.assertEqual(
            resolve_allowed_hosts({"ALLOWED_HOSTS": "example.com, www.createch.co.ke"}, ["gunicorn"]),
            ["example.com", "www.createch.co.ke"],
        )
        railway = resolve_allowed_hosts(
            {"DEBUG": "True", "RAILWAY_ENVIRONMENT": "production"},
            ["gunicorn"],
        )
        self.assertNotIn("*", railway)

    def test_startup_log_names_missing_env(self):
        missing = missing_production_env({})
        for name in (
            "DJANGO_SECRET_KEY",
            "ARCH_ADMIN_SECRET",
            "ALLOWED_HOSTS",
            "DATABASE_URL",
            "CORS_ALLOWED_ORIGINS",
        ):
            self.assertIn(name, missing)
        with self.assertLogs("django", level="WARNING") as logs:
            logged = log_missing_production_env({}, ["gunicorn"])
        self.assertEqual(logged, missing)
        self.assertIn("Missing environment variables:", logs.output[0])
        self.assertIn("DJANGO_SECRET_KEY", logs.output[0])
        self.assertIn("ALLOWED_HOSTS", logs.output[0])
        complete = {
            "DJANGO_SECRET_KEY": "k" * 40,
            "ARCH_ADMIN_SECRET": "a" * 40,
            "ALLOWED_HOSTS": ".up.railway.app",
            "DATABASE_URL": "postgres://u:p@localhost:5432/createch",
            "CORS_ALLOWED_ORIGINS": "https://createch.co.ke",
        }
        self.assertEqual(log_missing_production_env(complete, ["gunicorn"]), [])
        self.assertEqual(log_missing_production_env({}, ["manage.py", "test"]), [])

    def test_enquiry_recipients_are_editable_and_replace_the_env_list(self):
        denied = self.c.get("/api/admin/enquiry-recipients/")
        self.assertEqual(denied.status_code, 403)
        listing = self.c.get("/api/admin/enquiry-recipients/", **self.auth)
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()[0]["email"], "anvi@createch.co.ke")
        self.assertTrue(listing.json()[0]["active"])

        duplicate = self.c.post(
            "/api/admin/enquiry-recipients/",
            {"email": "ANVI@createch.co.ke"},
            format="json",
            **self.auth,
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        invalid = self.c.post(
            "/api/admin/enquiry-recipients/",
            {"email": "not-an-email"},
            format="json",
            **self.auth,
        )
        self.assertEqual(invalid.status_code, 400, invalid.content)

        created = self.c.post(
            "/api/admin/enquiry-recipients/",
            {"email": "Studio@createch.co.ke", "active": True},
            format="json",
            **self.auth,
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["email"], "studio@createch.co.ke")
        studio_id = created.json()["id"]

        mail_env = {
            "EMAIL_HOST": "smtp.example.com",
            "EMAIL_PORT": "587",
            "EMAIL_HOST_USER": "studio@createch.co.ke",
            "EMAIL_HOST_PASSWORD": "secret-not-logged",
            "EMAIL_USE_TLS": "true",
            "DEFAULT_FROM_EMAIL": "studio@createch.co.ke",
            "ENQUIRY_NOTIFY_TO": "noreply@createch.co.ke",
        }
        with patch.dict(os.environ, mail_env, clear=False):
            with patch("arch_media.views.send_mail") as send:
                saved = self.c.post("/api/enquiries/", self.enquiry(email="both@example.com"), format="json")
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(
            send.call_args.kwargs["recipient_list"],
            ["anvi@createch.co.ke", "studio@createch.co.ke"],
        )
        self.assertNotIn("noreply@createch.co.ke", send.call_args.kwargs["recipient_list"])
        self.assertNotIn("secret-not-logged", send.call_args.kwargs["message"])
        self.assertNotIn("recipients", saved.json())

        paused = self.c.patch(
            f"/api/admin/enquiry-recipients/{studio_id}/",
            {"active": False},
            format="json",
            **self.auth,
        )
        self.assertEqual(paused.status_code, 200, paused.content)
        self.assertFalse(paused.json()["active"])
        with patch.dict(os.environ, mail_env, clear=False):
            with patch("arch_media.views.send_mail") as send:
                self.c.post("/api/enquiries/", self.enquiry(email="anvi-only@example.com"), format="json")
        self.assertEqual(send.call_args.kwargs["recipient_list"], ["anvi@createch.co.ke"])

        renamed = self.c.patch(
            f"/api/admin/enquiry-recipients/{listing.json()[0]['id']}/",
            {"email": "anvi.shah@createch.co.ke"},
            format="json",
            **self.auth,
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        self.assertEqual(renamed.json()["email"], "anvi.shah@createch.co.ke")

        EnquiryRecipient.objects.update(active=False)
        with patch.dict(os.environ, mail_env, clear=False):
            with patch("arch_media.views.send_mail") as send:
                fallback = self.c.post(
                    "/api/enquiries/",
                    self.enquiry(email="fallback@example.com"),
                    format="json",
                )
        self.assertEqual(fallback.status_code, 201, fallback.content)
        self.assertEqual(send.call_args.kwargs["recipient_list"], ["noreply@createch.co.ke"])

        removed = self.c.delete(f"/api/admin/enquiry-recipients/{studio_id}/", **self.auth)
        self.assertEqual(removed.status_code, 204)
        self.assertFalse(EnquiryRecipient.objects.filter(pk=studio_id).exists())

    def test_instagram_is_public_only_when_set_and_mirrors_into_chrome(self):
        self.assertEqual(self.c.get("/api/settings/").json(), {"instagram": None})
        denied = self.c.put(
            "/api/admin/settings/",
            {"instagram": "https://www.instagram.com/createch/"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.c.put("/api/settings/", {"instagram": "https://example.com"}, format="json").status_code, 405)

        seeded = self.c.put(
            "/api/admin/chrome/",
            {
                "data": {
                    "hero": "A",
                    "contact": {"email": "anvi@createch.co.ke", "instagram": ""},
                    "draft": {"note": "keep me"},
                }
            },
            format="json",
            **self.auth,
        )
        self.assertEqual(seeded.status_code, 200, seeded.content)

        saved = self.c.put(
            "/api/admin/settings/",
            {"instagram": "https://www.instagram.com/createch/"},
            format="json",
            **self.auth,
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["instagram"], "https://www.instagram.com/createch/")
        self.assertIn("updated_at", saved.json())
        self.assertEqual(
            self.c.get("/api/settings/").json()["instagram"],
            "https://www.instagram.com/createch/",
        )
        admin = self.c.get("/api/admin/chrome/", **self.auth).json()["data"]
        self.assertEqual(admin["contact"]["instagram"], "https://www.instagram.com/createch/")
        self.assertEqual(admin["draft"], {"note": "keep me"})
        self.assertEqual(admin["hero"], "A")
        public = self.c.get("/api/chrome/").json()["data"]
        self.assertEqual(public["contact"]["instagram"], "https://www.instagram.com/createch/")
        self.assertNotIn("draft", public)
        self.assertEqual(public["contact"]["email"], "anvi@createch.co.ke")

        cleared = self.c.patch(
            "/api/admin/settings/",
            {"instagram": None},
            format="json",
            **self.auth,
        )
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertIsNone(cleared.json()["instagram"])
        self.assertEqual(self.c.get("/api/chrome/").json()["data"]["contact"]["instagram"], "")
        self.assertEqual(self.c.get("/api/admin/chrome/", **self.auth).json()["data"]["draft"], {"note": "keep me"})

        rejected = self.c.put(
            "/api/admin/settings/",
            {"instagram": "javascript:alert(1)"},
            format="json",
            **self.auth,
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIsNone(self.c.get("/api/settings/").json()["instagram"])

        from_chrome = self.c.put(
            "/api/admin/chrome/",
            {
                "data": {
                    "hero": "A",
                    "contact": {"instagram": "https://instagram.com/from-chrome", "email": "anvi@createch.co.ke"},
                    "draft": {"note": "keep me"},
                }
            },
            format="json",
            **self.auth,
        )
        self.assertEqual(from_chrome.status_code, 200, from_chrome.content)
        self.assertEqual(self.c.get("/api/settings/").json()["instagram"], "https://instagram.com/from-chrome")

        untouched = self.c.put(
            "/api/admin/chrome/",
            {"data": {"hero": "B", "draft": {"note": "keep me"}}},
            format="json",
            **self.auth,
        )
        self.assertEqual(untouched.status_code, 200, untouched.content)
        self.assertEqual(self.c.get("/api/settings/").json()["instagram"], "https://instagram.com/from-chrome")
        self.assertEqual(untouched.json()["data"], {"hero": "B", "draft": {"note": "keep me"}})

        blank = self.c.put(
            "/api/admin/chrome/",
            {"data": {"contact": {"instagram": ""}, "hero": "C"}},
            format="json",
            **self.auth,
        )
        self.assertEqual(blank.status_code, 200, blank.content)
        self.assertIsNone(self.c.get("/api/settings/").json()["instagram"])
        self.assertEqual(blank.json()["data"]["contact"]["instagram"], "")

    def test_instagram_url_is_rejected_in_django_admin_and_hidden_if_already_stored(self):
        row = SiteSettings.objects.get(key="default")
        row.instagram_url = "javascript:alert(1)"
        with self.assertRaises(ValidationError):
            row.full_clean()
        with self.assertRaises(ValidationError):
            row.save()
        row.instagram_url = "ftp://files.example.com/photo"
        with self.assertRaises(ValidationError):
            row.save()
        row.instagram_url = ""
        row.save()
        row.instagram_url = "https://www.instagram.com/createch/"
        row.full_clean()

        seeded = self.c.put(
            "/api/admin/chrome/",
            {"data": {"contact": {"email": "anvi@createch.co.ke", "instagram": ""}}},
            format="json",
            **self.auth,
        )
        self.assertEqual(seeded.status_code, 200, seeded.content)
        SiteSettings.objects.filter(key="default").update(instagram_url="javascript:alert(1)")
        public = self.c.get("/api/settings/")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertIsNone(public.json()["instagram"])
        self.assertNotIn("javascript:", public.content.decode())
        admin = self.c.get("/api/admin/settings/", **self.auth)
        self.assertIsNone(admin.json()["instagram"])
        chrome = self.c.get("/api/chrome/").json()["data"]["contact"]
        self.assertIsNone(chrome["instagram"])
        self.assertEqual(chrome["email"], "anvi@createch.co.ke")
        self.assertNotIn(b"javascript:", self.c.get("/api/chrome/").content)

    def test_recipient_active_must_be_a_boolean_when_present(self):
        missing = self.c.post(
            "/api/admin/enquiry-recipients/",
            {"email": "default-active@example.com"},
            format="json",
            **self.auth,
        )
        self.assertEqual(missing.status_code, 201, missing.content)
        self.assertTrue(missing.json()["active"])

        for payload in (
            {"email": "null-active@example.com", "active": None},
            {"email": "text-active@example.com", "active": "true"},
            {"email": "num-active@example.com", "active": 1},
        ):
            rejected = self.c.post(
                "/api/admin/enquiry-recipients/",
                payload,
                format="json",
                **self.auth,
            )
            self.assertEqual(rejected.status_code, 400, rejected.content)
            self.assertFalse(EnquiryRecipient.objects.filter(email=payload["email"]).exists())

        row = EnquiryRecipient.objects.get(email="anvi@createch.co.ke")
        for body in ({"active": None}, {"active": "false"}, {"active": 0}):
            rejected = self.c.patch(
                f"/api/admin/enquiry-recipients/{row.pk}/",
                body,
                format="json",
                **self.auth,
            )
            self.assertEqual(rejected.status_code, 400, rejected.content)
        row.refresh_from_db()
        self.assertTrue(row.active)

    def test_category_object_id_must_be_a_positive_integer(self):
        created = self.c.post(
            "/api/admin/projects/",
            {
                "slug": "id-check",
                "title": "Id check",
                "publish": True,
                "discipline": "architecture",
            },
            format="json",
            **self.auth,
        )
        self.assertEqual(created.status_code, 201, created.content)
        bad_ids = (
            ("discipline", True),
            ("discipline", 1.5),
            ("discipline", "commercial"),
            ("segment", True),
            ("segment", 1.5),
            ("segment", "architecture"),
        )
        for field, raw_id in bad_ids:
            rejected = self.c.patch(
                "/api/admin/projects/id-check/",
                {field: {"id": raw_id}},
                format="json",
                **self.auth,
            )
            self.assertEqual(rejected.status_code, 400, rejected.content)
            self.assertIn(f"unknown {field}", str(rejected.json()[field]))
        project = Project.objects.get(slug="id-check")
        self.assertEqual(project.discipline.slug, "architecture")
        self.assertIsNone(project.segment_id)

        commercial = Category.objects.get(slug="commercial")
        accepted = self.c.patch(
            "/api/admin/projects/id-check/",
            {"segment": {"id": str(commercial.pk)}},
            format="json",
            **self.auth,
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(accepted.json()["segment"]["slug"], "commercial")
        self.assertEqual(accepted.json()["discipline"]["slug"], "architecture")

    def test_categories_are_data_and_existing_project_fields_remain(self):
        public = self.c.get("/api/categories/")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(
            [(row["slug"], row["kind"]) for row in public.json()],
            [
                ("architecture", "discipline"),
                ("interior-design", "discipline"),
                ("commercial", "segment"),
                ("residential", "segment"),
            ],
        )
        denied = self.c.post("/api/admin/categories/", {"name": "Landscape", "kind": "discipline"}, format="json")
        self.assertEqual(denied.status_code, 403)

        created = self.c.post(
            "/api/admin/projects/",
            {
                "slug": "villa-01",
                "title": "Villa 01",
                "typology": "hospitality",
                "status": "completed",
                "publish": True,
            },
            format="json",
            **self.auth,
        )
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual(body["typology"], "hospitality")
        self.assertIsNone(body["discipline"])
        self.assertIsNone(body["segment"])
        self.assertIs(body["under_createch"], False)
        self.assertIn("brief", body)
        self.assertIn("gallery", body)

        assigned = self.c.patch(
            "/api/admin/projects/villa-01/",
            {"discipline": "architecture", "segment": "residential", "underCreatech": True},
            format="json",
            **self.auth,
        )
        self.assertEqual(assigned.status_code, 200, assigned.content)
        self.assertEqual(assigned.json()["discipline"]["slug"], "architecture")
        self.assertEqual(assigned.json()["segment"]["name"], "Residential")
        self.assertIs(assigned.json()["under_createch"], True)
        self.assertEqual(assigned.json()["typology"], "hospitality")

        mismatch = self.c.patch(
            "/api/admin/projects/villa-01/",
            {"discipline": "commercial"},
            format="json",
            **self.auth,
        )
        self.assertEqual(mismatch.status_code, 400, mismatch.content)
        self.assertEqual(Project.objects.get(slug="villa-01").discipline.slug, "architecture")

        by_id = self.c.patch(
            "/api/admin/projects/villa-01/",
            {"segment_id": Category.objects.get(slug="commercial").pk},
            format="json",
            **self.auth,
        )
        self.assertEqual(by_id.status_code, 200, by_id.content)
        self.assertEqual(by_id.json()["segment"]["slug"], "commercial")

        added = self.c.post(
            "/api/admin/categories/",
            {"name": "Landscape", "kind": "discipline"},
            format="json",
            **self.auth,
        )
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual(added.json()["slug"], "landscape")
        self.assertTrue(added.json()["active"])
        landscape_id = added.json()["id"]
        self.assertEqual(
            [row["slug"] for row in self.c.get("/api/categories/?kind=discipline").json()],
            ["architecture", "interior-design", "landscape"],
        )
        hidden = self.c.patch(
            f"/api/admin/categories/{landscape_id}/",
            {"active": False},
            format="json",
            **self.auth,
        )
        self.assertEqual(hidden.status_code, 200, hidden.content)
        self.assertNotIn("landscape", [row["slug"] for row in self.c.get("/api/categories/").json()])
        self.assertIn(
            "landscape",
            [row["slug"] for row in self.c.get("/api/admin/categories/?kind=discipline", **self.auth).json()],
        )

        duplicate = self.c.post(
            "/api/admin/categories/",
            {"name": "Architecture", "kind": "discipline"},
            format="json",
            **self.auth,
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        bad_kind = self.c.post(
            "/api/admin/categories/",
            {"name": "Hospitality", "kind": "theme"},
            format="json",
            **self.auth,
        )
        self.assertEqual(bad_kind.status_code, 400, bad_kind.content)

        earlier = self.c.post(
            "/api/admin/projects/",
            {
                "slug": "earlier-01",
                "title": "Earlier 01",
                "publish": True,
                "discipline_id": Category.objects.get(slug="interior-design").pk,
                "segment": "commercial",
                "under_createch": False,
            },
            format="json",
            **self.auth,
        )
        self.assertEqual(earlier.status_code, 201, earlier.content)
        own = self.c.get("/api/projects/?under_createch=true&discipline=architecture").json()
        self.assertEqual([row["slug"] for row in own], ["villa-01"])
        interiors = self.c.get("/api/projects/?underCreatech=false&segment=commercial").json()
        self.assertEqual([row["slug"] for row in interiors], ["earlier-01"])
        bad_flag = self.c.get("/api/projects/?under_createch=maybe")
        self.assertEqual(bad_flag.status_code, 400)

        removed = self.c.delete(
            f"/api/admin/categories/{Category.objects.get(slug='architecture').pk}/",
            **self.auth,
        )
        self.assertEqual(removed.status_code, 204)
        project = Project.objects.get(slug="villa-01")
        self.assertIsNone(project.discipline_id)
        self.assertEqual(project.typology, "hospitality")
        self.assertTrue(project.under_createch)
        self.assertEqual(project.title, "Villa 01")

    def test_chrome_project_slots_do_not_drop_existing_fields(self):
        original = {
            "contact": {"instagram": "", "email": "anvi@createch.co.ke"},
            "portfolio": {
                "projects": [
                    {
                        "slug": "networks-padel-village",
                        "title": "Networks Padel Village & Vamos Restaurant",
                        "typology": "hospitality",
                        "underCreatech": True,
                        "order": 1,
                    },
                    {
                        "slug": "indian-restaurant-mumbai",
                        "typology": "fnb-interiors",
                        "underCreatech": False,
                        "discipline": "interior-design",
                        "segment": "commercial",
                    },
                    "leave-me",
                ]
            },
        }
        updated, changed = add_project_category_slots(original)
        self.assertTrue(changed)
        first = updated["portfolio"]["projects"][0]
        self.assertEqual(first["typology"], "hospitality")
        self.assertIs(first["underCreatech"], True)
        self.assertEqual(first["title"], "Networks Padel Village & Vamos Restaurant")
        self.assertEqual(first["order"], 1)
        self.assertIsNone(first["discipline"])
        self.assertIsNone(first["segment"])
        second = updated["portfolio"]["projects"][1]
        self.assertEqual(second["discipline"], "interior-design")
        self.assertEqual(second["segment"], "commercial")
        self.assertEqual(second["typology"], "fnb-interiors")
        self.assertIs(updated["portfolio"]["projects"][2], "leave-me")
        self.assertNotIn("discipline", original["portfolio"]["projects"][0])
        again, changed_again = add_project_category_slots(updated)
        self.assertFalse(changed_again)
        self.assertIs(again, updated)

        chrome = SiteChrome.objects.create(key="default", data=original)
        stored, stored_changed = add_project_category_slots(chrome.data)
        self.assertTrue(stored_changed)
        chrome.data = stored
        chrome.save(update_fields=["data"])
        reloaded = SiteChrome.objects.get(pk=chrome.pk).data["portfolio"]["projects"][0]
        self.assertEqual(reloaded["slug"], "networks-padel-village")
        self.assertIs(reloaded["underCreatech"], True)
        self.assertIsNone(reloaded["discipline"])
        self.assertIsNone(reloaded["segment"])


@override_settings(ARCH_ADMIN_SECRET="generate-a-long-random-string")
class PlaceholderSecretTests(TestCase):
    def test_placeholder_secret_opens_nothing(self):
        c = APIClient()
        r = c.get("/api/admin/chrome/", HTTP_X_ADMIN_KEY="generate-a-long-random-string")
        self.assertEqual(r.status_code, 403)
