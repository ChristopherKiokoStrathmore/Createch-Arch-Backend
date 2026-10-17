import os
import sys
from pathlib import Path

import dj_database_url
from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default="False"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


def env_int(name, default):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer") from exc
    if value <= 0:
        raise ImproperlyConfigured(f"{name} must be a positive integer")
    return value


INSECURE_SECRET_DEFAULT = "dev-insecure-secret-change-me"
MIN_DJANGO_SECRET_LENGTH = 32
PRODUCTION_ALLOWED_HOSTS = [
    ".up.railway.app",
    "createch.co.ke",
    "www.createch.co.ke",
]


def _flag(environ, name):
    return (environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def local_relax(environ=None, argv=None):
    """Local DEBUG, ALLOW_SQLITE, and the test runner. Never on Railway."""
    environ = os.environ if environ is None else environ
    argv = sys.argv if argv is None else argv
    on_railway = bool(environ.get("RAILWAY_ENVIRONMENT") or environ.get("RAILWAY_PROJECT_ID"))
    relaxed = _flag(environ, "DEBUG") or _flag(environ, "ALLOW_SQLITE") or ("test" in argv)
    return relaxed and not on_railway


def resolve_secret_key(environ=None, argv=None):
    """Refuse a missing, short, or well-known key when DEBUG is off."""
    environ = os.environ if environ is None else environ
    argv = sys.argv if argv is None else argv
    key = (environ.get("DJANGO_SECRET_KEY") or "").strip()
    if local_relax(environ, argv):
        return key or INSECURE_SECRET_DEFAULT
    if len(key) < MIN_DJANGO_SECRET_LENGTH or key == INSECURE_SECRET_DEFAULT:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is missing or shorter than "
            f"{MIN_DJANGO_SECRET_LENGTH} characters. Refusing to boot while DEBUG is off. "
            "Set a long random DJANGO_SECRET_KEY on Railway."
        )
    return key


def resolve_allowed_hosts(environ=None, argv=None):
    """Production never falls open to '*'. A missing var keeps the public hosts."""
    environ = os.environ if environ is None else environ
    argv = sys.argv if argv is None else argv
    configured = [item.strip() for item in (environ.get("ALLOWED_HOSTS") or "").split(",") if item.strip()]
    if configured:
        return configured
    if local_relax(environ, argv):
        return ["*"]
    return list(PRODUCTION_ALLOWED_HOSTS)


def missing_production_env(environ=None):
    """Names of required production variables that are unset or still placeholders."""
    environ = os.environ if environ is None else environ
    missing = []
    key = (environ.get("DJANGO_SECRET_KEY") or "").strip()
    if len(key) < MIN_DJANGO_SECRET_LENGTH or key == INSECURE_SECRET_DEFAULT:
        missing.append("DJANGO_SECRET_KEY")
    admin = (environ.get("ARCH_ADMIN_SECRET") or "").strip()
    if len(admin) < 32:
        missing.append("ARCH_ADMIN_SECRET")
    if not (environ.get("ALLOWED_HOSTS") or "").strip():
        missing.append("ALLOWED_HOSTS")
    if not (environ.get("DATABASE_URL") or "").strip():
        missing.append("DATABASE_URL")
    if not (environ.get("CORS_ALLOWED_ORIGINS") or "").strip():
        missing.append("CORS_ALLOWED_ORIGINS")
    return missing


def log_missing_production_env(environ=None, argv=None):
    """One startup line naming every required variable that is still unset."""
    import logging

    environ = os.environ if environ is None else environ
    argv = sys.argv if argv is None else argv
    if local_relax(environ, argv):
        return []
    missing = missing_production_env(environ)
    if missing:
        logging.getLogger("django").warning(
            "Missing environment variables: %s", ", ".join(missing)
        )
    return missing


SECRET_KEY = resolve_secret_key()
DEBUG = env_bool("DEBUG", "False")

ALLOWED_HOSTS = resolve_allowed_hosts()
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "arch_media",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

def resolve_database_url(environ=None, argv=None):
    """Railway Postgres via DATABASE_URL. SQLite only for tests or ALLOW_SQLITE."""
    environ = os.environ if environ is None else environ
    argv = sys.argv if argv is None else argv
    url = (environ.get("DATABASE_URL") or "").strip()
    on_railway = bool(environ.get("RAILWAY_ENVIRONMENT") or environ.get("RAILWAY_PROJECT_ID"))
    running_tests = "test" in argv
    allow_sqlite = (
        (environ.get("ALLOW_SQLITE") or "").strip().lower() in ("1", "true", "yes", "on")
        or running_tests
    )
    if not url:
        if allow_sqlite and not on_railway:
            return f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
        raise ImproperlyConfigured(
            "DATABASE_URL is required and must be Railway Postgres "
            "(DATABASE_URL=${{Postgres.DATABASE_URL}}). "
            "SQLite is only for `python manage.py test` or ALLOW_SQLITE=True off Railway."
        )
    if url.startswith("sqlite") and (on_railway or not allow_sqlite):
        raise ImproperlyConfigured(
            "SQLite is not the production database. Set DATABASE_URL to Railway Postgres."
        )
    if not (url.startswith("postgres://") or url.startswith("postgresql://") or url.startswith("sqlite")):
        raise ImproperlyConfigured(
            "DATABASE_URL must be a postgres:// or postgresql:// URL (Railway Postgres)."
        )
    return url


DATABASES = {
    "default": dj_database_url.parse(
        resolve_database_url(),
        conn_max_age=600,
        ssl_require=env_bool("DB_SSL_REQUIRE", "False"),
    )
}

if "postgresql" in DATABASES["default"]["ENGINE"]:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "createch_cache",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "createch-enquiries",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Nairobi")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))
SERVE_MEDIA = env_bool("SERVE_MEDIA", "False")

USE_S3 = env_bool("USE_S3", "False")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

if USE_S3:
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "")
    AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL", "") or None
    AWS_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN", "") or None
    AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "") or None
    AWS_QUERYSTRING_AUTH = False
    AWS_DEFAULT_ACL = None
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Shared secret the Next PIN admin sends as the X-Admin-Key header.
ARCH_ADMIN_SECRET = os.environ.get("ARCH_ADMIN_SECRET", "")

# JSON bodies (enquiry, chrome) and image uploads. DRF honors the Django
# memory cap from 3.17.2; the view-level caps reject oversized writes with 413.
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("FILE_UPLOAD_MAX_MEMORY_SIZE", 2621440)
DATA_UPLOAD_MAX_NUMBER_FIELDS = env_int("DATA_UPLOAD_MAX_NUMBER_FIELDS", 1000)
ARCH_MAX_UPLOAD_BYTES = env_int("ARCH_MAX_UPLOAD_BYTES", 8 * 1024 * 1024)
ENQUIRY_MAX_BODY_BYTES = env_int("ENQUIRY_MAX_BODY_BYTES", 32 * 1024)
CHROME_MAX_BODY_BYTES = env_int("CHROME_MAX_BODY_BYTES", 512 * 1024)

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_HEADERS = list(default_headers) + ["x-admin-key", "x-createch-client-ip"]
CORS_ALLOW_CREDENTIALS = False

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "pin_verify": "10/min",
    },
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
