import mimetypes

from django.apps import AppConfig


class ArchMediaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "arch_media"
    verbose_name = "Createch Arch Media"

    def ready(self):
        # Debian's mime database often has no entry for these, so
        # django.views.static would send application/octet-stream.
        for mime, ext in (
            ("image/webp", ".webp"),
            ("image/avif", ".avif"),
            ("image/jpeg", ".jpg"),
            ("image/jpeg", ".jpeg"),
            ("image/png", ".png"),
            ("image/gif", ".gif"),
            ("image/bmp", ".bmp"),
        ):
            mimetypes.add_type(mime, ext, strict=True)
        from config.settings import log_missing_production_env

        log_missing_production_env()
