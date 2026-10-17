from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.validators import validate_email
from django.db import connection
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from .models import AdminPin, Enquiry, MediaAsset, Project, ProjectImage, SiteChrome
from .permissions import HasAdminKey, admin_key_matches
from .serializers import (
    EnquirySerializer,
    MediaAssetSerializer,
    MediaUploadSerializer,
    ProjectImageSerializer,
    ProjectSerializer,
    SiteChromeSerializer,
)

CHROME_KEY = "default"
PIN_KEY = "default"
PIN_MIN, PIN_MAX = 4, 128
ENQUIRY_ALIASES = {
    "project_type": "projectType",
    "site": "location",
}
CACHE_TABLE = "createch_cache"


def reorder_pairs(data, identity_key):
    """Accept {"order": [...]} or a list of {identity, sort/order} objects."""
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("order", [])
    else:
        return []
    if not isinstance(entries, list):
        return []
    pairs = []
    for index, entry in enumerate(entries):
        if isinstance(entry, dict):
            identity = entry.get(identity_key)
            if identity is None:
                continue
            explicit = entry.get("sort", entry.get("order", index))
            if isinstance(explicit, bool) or not isinstance(explicit, int):
                explicit = index
            pairs.append((identity, explicit))
        else:
            pairs.append((entry, index))
    return pairs


def body_too_large(request, limit):
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        return True
    try:
        return len(request.body) > limit
    except RequestDataTooBig:
        return True


def _enquiry_value(data, field):
    if field in data:
        return data.get(field, "")
    alias = ENQUIRY_ALIASES.get(field)
    if alias and alias in data:
        return data.get(alias, "")
    return ""


def service_health():
    """Database connectivity, plus the Postgres cache table when that is the limiter."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return False, "database"
    backend = settings.CACHES["default"]["BACKEND"]
    needs_table = connection.vendor == "postgresql" or backend.endswith("DatabaseCache")
    if needs_table:
        try:
            present = CACHE_TABLE in connection.introspection.table_names()
        except Exception:
            return False, "database"
        if not present:
            return False, "cache"
        return True, ""
    try:
        cache.set("health:ping", "ok", 10)
        if cache.get("health:ping") != "ok":
            return False, "cache"
    except Exception:
        return False, "cache"
    return True, ""


# ---------------------------------------------------------------- public reads

class PublicProjectViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectSerializer
    lookup_field = "slug"

    def get_queryset(self):
        qs = Project.objects.filter(publish=True)
        status_param = self.request.query_params.get("status")
        if status_param in {"ongoing", "completed"}:
            qs = qs.filter(status=status_param)
        return qs


class PublicChromeView(APIView):
    def get(self, request):
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        payload = dict(SiteChromeSerializer(chrome).data)
        if isinstance(payload.get("data"), dict):
            data = dict(payload["data"])
            data.pop("draft", None)
            payload["data"] = data
        return Response(payload)


class HealthView(APIView):
    """GET /api/health/ — database, and createch_cache when Postgres backs the limiter."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        ok, error = service_health()
        if not ok:
            return Response({"ok": False, "error": error}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"ok": True})


# ------------------------------------------------------------- admin (X-Admin-Key)

class MediaAssetViewSet(viewsets.ModelViewSet):
    queryset = MediaAsset.objects.all()
    serializer_class = MediaAssetSerializer
    permission_classes = [HasAdminKey]

    def create(self, request, *args, **kwargs):
        serializer = MediaUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        asset = serializer.save()
        out = MediaAssetSerializer(asset, context={"request": request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        pairs = reorder_pairs(request.data, "id")
        for asset_id, position in pairs:
            MediaAsset.objects.filter(pk=asset_id).update(sort=position)
        return Response({"status": "ok", "count": len(pairs)})


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [HasAdminKey]
    lookup_field = "slug"

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        pairs = reorder_pairs(request.data, "slug")
        for slug, position in pairs:
            Project.objects.filter(slug=slug).update(order=position)
        return Response({"status": "ok", "count": len(pairs)})

    @action(detail=True, methods=["post"])
    def gallery(self, request, slug=None):
        project = self.get_object()
        asset = get_object_or_404(MediaAsset, pk=request.data.get("asset_id"))
        item, _ = ProjectImage.objects.update_or_create(
            project=project, asset=asset, defaults={"sort": request.data.get("sort", 0)}
        )
        return Response(
            ProjectImageSerializer(item, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AdminChromeView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        return Response(SiteChromeSerializer(chrome).data)

    def put(self, request):
        if body_too_large(request, settings.CHROME_MAX_BODY_BYTES):
            return Response({"error": "payload too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        chrome.data = request.data.get("data", request.data) if hasattr(request.data, "get") else request.data
        chrome.save()
        return Response(SiteChromeSerializer(chrome).data)


def pin_error(pin):
    """Accept any printable Unicode string in range. No trim, no case folding."""
    if not isinstance(pin, str) or not (PIN_MIN <= len(pin) <= PIN_MAX) or not pin.isprintable():
        return f"PIN must be {PIN_MIN}-{PIN_MAX} printable characters"
    return None


class PinVerifyThrottle(SimpleRateThrottle):
    """10 verification attempts per minute per IP. GET and PUT are not counted."""

    scope = "pin_verify"

    def get_cache_key(self, request, view):
        if request.method != "POST":
            return None
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class AdminPinView(APIView):
    """The /admin PIN. Only the Next server calls this, with X-Admin-Key.

    GET  -> {set, version}
    POST {pin} -> {ok, set, version}      verify
    PUT  {pin} -> {set, version}          replace

    A PIN is any printable Unicode string of 4 to 128 characters (letters,
    numbers, symbols, spaces). It is hashed and compared exactly.
    """

    permission_classes = [HasAdminKey]
    throttle_classes = [PinVerifyThrottle]

    @staticmethod
    def state(row):
        return {"set": row is not None, "version": row.version if row else 0}

    def get(self, request):
        return Response(self.state(AdminPin.objects.filter(key=PIN_KEY).first()))

    def post(self, request):
        row = AdminPin.objects.filter(key=PIN_KEY).first()
        pin = request.data.get("pin")
        ok = bool(row) and isinstance(pin, str) and check_password(pin, row.pin_hash)
        return Response({"ok": ok, **self.state(row)})

    def put(self, request):
        pin = request.data.get("pin")
        error = pin_error(pin)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        row, _ = AdminPin.objects.get_or_create(key=PIN_KEY, defaults={"pin_hash": ""})
        row.pin_hash = make_password(pin)
        row.version += 1
        row.save()
        return Response(self.state(row))


# ---------------------------------------------------------------- enquiries

IP_LIMIT = 10
EMAIL_LIMIT = 5
WINDOW_SECONDS = 60 * 60
FIELD_LIMITS = {
    "name": 120,
    "email": 200,
    "phone": 40,
    "project_type": 120,
    "site": 200,
    "stage": 120,
    "timeline": 200,
    "message": 5000,
}


def _text(value, limit):
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        ch for ch in value if ch == "\n" or ch == "\r" or ch == "\t" or ord(ch) >= 32
    ).strip()
    return cleaned[:limit]


def _admin_key_ok(request):
    secret = getattr(settings, "ARCH_ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Key", "") or ""
    return admin_key_matches(provided, secret)


def client_ip(request):
    """Unauthenticated callers are bucketed by REMOTE_ADDR.

    The Next server is one IP for every visitor, so when it presents a valid
    X-Admin-Key it may pass the visitor address as X-Createch-Client-Ip.
    """
    if _admin_key_ok(request):
        forwarded = (request.headers.get("X-Createch-Client-Ip") or "").split(",")[0].strip()
        if forwarded:
            return forwarded[:80]
    return (request.META.get("REMOTE_ADDR") or "unknown")[:80]


def enquiry_rate_limited(request, email):
    ip_key = f"enquiry:ip:{client_ip(request)}"
    hits = cache.get(ip_key, 0)
    if hits >= IP_LIMIT:
        return True
    since = timezone.now() - timedelta(seconds=WINDOW_SECONDS)
    if (
        Enquiry.objects.filter(email__iexact=email, created_at__gte=since).count()
        >= EMAIL_LIMIT
    ):
        return True
    # The timeout is set on the first hit and left alone after that, so a
    # steady drip cannot keep the window open forever.
    if not hits:
        cache.set(ip_key, 1, WINDOW_SECONDS)
    else:
        try:
            cache.incr(ip_key)
        except ValueError:
            cache.set(ip_key, 1, WINDOW_SECONDS)
    return False


class PublicEnquiryView(APIView):
    """POST /api/enquiries/ — public. Honeypot field `company` is dropped."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        if body_too_large(request, settings.ENQUIRY_MAX_BODY_BYTES):
            return Response({"error": "payload too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        if not hasattr(request.data, "get"):
            return Response({"error": "JSON object required"}, status=status.HTTP_400_BAD_REQUEST)
        company = request.data.get("company", "")
        if isinstance(company, str) and company.strip():
            enquiry_rate_limited(request, "")
            return Response({"ok": True}, status=status.HTTP_201_CREATED)

        payload = {}
        for field, limit in FIELD_LIMITS.items():
            text = _text(_enquiry_value(request.data, field), limit)
            if text is None:
                return Response(
                    {"error": f"{field} must be a string"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            payload[field] = text

        if not payload["name"]:
            return Response({"error": "name is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not payload["message"]:
            return Response({"error": "message is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_email(payload["email"])
        except ValidationError:
            return Response({"error": "email is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        if enquiry_rate_limited(request, payload["email"]):
            return Response(
                {"error": "Too many enquiries. Try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        enquiry = Enquiry.objects.create(**payload)
        return Response(EnquirySerializer(enquiry).data, status=status.HTTP_201_CREATED)


class AdminEnquiryListView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        rows = Enquiry.objects.all()
        return Response(EnquirySerializer(rows, many=True).data)


class AdminEnquiryDetailView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request, pk):
        row = get_object_or_404(Enquiry, pk=pk)
        return Response(EnquirySerializer(row).data)

    def patch(self, request, pk):
        row = get_object_or_404(Enquiry, pk=pk)
        if "read" not in request.data or not isinstance(request.data.get("read"), bool):
            return Response(
                {"error": "read must be a boolean"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        row.read = request.data["read"]
        row.save(update_fields=["read"])
        return Response(EnquirySerializer(row).data)

    def delete(self, request, pk):
        row = get_object_or_404(Enquiry, pk=pk)
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
