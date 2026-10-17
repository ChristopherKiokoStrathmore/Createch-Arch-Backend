import io
import mimetypes
import tempfile
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import OperationalError
from django.test import RequestFactory, TestCase, override_settings
from django.views.static import serve
from PIL import Image
from rest_framework.test import APIClient

from arch_media.models import AdminPin, Enquiry, MediaAsset, Project
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
        Image.new("RGB", (2, 2), (1, 2, 3)).save(buf, format="WEBP")
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


@override_settings(ARCH_ADMIN_SECRET="generate-a-long-random-string")
class PlaceholderSecretTests(TestCase):
    def test_placeholder_secret_opens_nothing(self):
        c = APIClient()
        r = c.get("/api/admin/chrome/", HTTP_X_ADMIN_KEY="generate-a-long-random-string")
        self.assertEqual(r.status_code, 403)
