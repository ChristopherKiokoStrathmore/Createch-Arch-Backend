import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


class HasAdminKey(BasePermission):
    """Grants access only when the X-Admin-Key header matches ARCH_ADMIN_SECRET."""

    message = "Invalid or missing X-Admin-Key."

    def has_permission(self, request, view):
        secret = getattr(settings, "ARCH_ADMIN_SECRET", "") or ""
        provided = request.headers.get("X-Admin-Key", "") or ""
        if not secret:
            return False
        return hmac.compare_digest(provided, secret)
