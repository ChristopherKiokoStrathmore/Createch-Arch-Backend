import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission

MIN_SECRET_LENGTH = 32


def admin_key_matches(provided, secret):
    """Constant-time match. Non-ASCII input is a rejection, not a 500."""
    secret = secret or ""
    provided = provided or ""
    if len(secret) < MIN_SECRET_LENGTH:
        return False
    try:
        secret_bytes = secret.encode("ascii")
        provided_bytes = provided.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(provided_bytes, secret_bytes)


class HasAdminKey(BasePermission):
    """Grants access only when the X-Admin-Key header matches ARCH_ADMIN_SECRET."""

    message = "Invalid or missing X-Admin-Key."

    def has_permission(self, request, view):
        secret = getattr(settings, "ARCH_ADMIN_SECRET", "") or ""
        provided = request.headers.get("X-Admin-Key", "") or ""
        # A short secret is a placeholder someone forgot to replace, and the
        # placeholder in .env.example is public. Refuse rather than accept it.
        return admin_key_matches(provided, secret)
