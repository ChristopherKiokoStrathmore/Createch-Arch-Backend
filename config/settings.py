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


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-change-me")
DEBUG = env_bool("DEBUG", "False")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "*") or ["*"]
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
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
