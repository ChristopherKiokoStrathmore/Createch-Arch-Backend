import logging
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.mail import get_connection, send_mail
from django.core.validators import validate_email
from django.db import connection, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from .instagram import clean_instagram
from .models import (
    AdminPin,
    Category,
    ChromeVersion,
    Enquiry,
    EnquiryRecipient,
    MediaAsset,
    Project,
    ProjectImage,
    SiteChrome,
    SiteSettings,
)
from .permissions import HasAdminKey, admin_key_matches
from .serializers import (
    CategorySerializer,
    ChromeVersionSerializer,
    EnquiryRecipientSerializer,
    EnquirySerializer,
    MediaAssetSerializer,
    MediaUploadSerializer,
    ProjectImageSerializer,
    ProjectSerializer,
    SiteChromeSerializer,
    unique_category_slug,
)

CHROME_KEY = "default"
VERSION_KEEP = 10
PUBLIC_CHROME_OMIT = ("draft", "versions")
PIN_KEY = "default"
PIN_MIN, PIN_MAX = 4, 128
ENQUIRY_ALIASES = {
    "project_type": "projectType",
    "site": "location",
}
CACHE_TABLE = "createch_cache"
SETTINGS_KEY = "default"
INSTAGRAM_BUCKETS = ("contact", "socials")


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


def _ownership_param(request):
    """Createch vs Earlier Work. `under_createch` wins when both names are sent."""
    if "under_createch" in request.query_params:
        raw = request.query_params.get("under_createch")
    elif "underCreatech" in request.query_params:
        raw = request.query_params.get("underCreatech")
    else:
        return None
    lowered = (raw or "").strip().lower()
    if lowered in {"1", "true", "yes"}:
        return True
    if lowered in {"0", "false", "no"}:
        return False
    raise ParseError("under_createch must be true or false")


def current_instagram_url():
    """Public Instagram string. A stored value that fails the rule is not served."""
    row = SiteSettings.objects.filter(key=SETTINGS_KEY).first()
    if row is None:
        return ""
    cleaned = clean_instagram(row.instagram_url)
    if cleaned is None:
        return None
    return cleaned


def overlay_instagram(data):
    """Show the settings URL on chrome contact/socials. Documents without those objects stay put."""
    if not isinstance(data, dict):
        return data
    if not any(isinstance(data.get(bucket), dict) for bucket in INSTAGRAM_BUCKETS):
        return data
    url = current_instagram_url()
    data = dict(data)
    for bucket in INSTAGRAM_BUCKETS:
        block = data.get(bucket)
        if isinstance(block, dict):
            block = dict(block)
            block["instagram"] = url
            data[bucket] = block
    return data


def instagram_from_chrome(data):
    """The contact or socials instagram value, or None when the document does not carry one."""
    if not isinstance(data, dict):
        return None
    for bucket in INSTAGRAM_BUCKETS:
        block = data.get(bucket)
        if isinstance(block, dict) and "instagram" in block:
            return block.get("instagram")
    return None


def sync_settings_from_chrome(data):
    raw = instagram_from_chrome(data)
    if raw is None:
        return
    cleaned = clean_instagram(raw)
    if cleaned is None:
        return
    row, _ = SiteSettings.objects.get_or_create(key=SETTINGS_KEY, defaults={"instagram_url": cleaned})
    if row.instagram_url != cleaned:
        row.instagram_url = cleaned
        row.save(update_fields=["instagram_url", "updated_at"])


def mirror_instagram_to_chrome(url):
    """Copy a saved Instagram URL into the chrome document the current site already reads."""
    chrome = SiteChrome.objects.filter(key=CHROME_KEY).first()
    if chrome is None or not isinstance(chrome.data, dict):
        return
    data = dict(chrome.data)
    changed = False
    for bucket in INSTAGRAM_BUCKETS:
        block = data.get(bucket)
        if isinstance(block, dict) and block.get("instagram") != url:
            block = dict(block)
            block["instagram"] = url
            data[bucket] = block
            changed = True
    if not changed:
        return
    archive_published(chrome)
    chrome.data = data
    chrome.save()


def chrome_response(chrome, public=False):
    payload = dict(SiteChromeSerializer(chrome).data)
    data = payload.get("data")
    if isinstance(data, dict):
        data = dict(data)
        if public:
            for key in PUBLIC_CHROME_OMIT:
                data.pop(key, None)
        payload["data"] = overlay_instagram(data)
    return payload


def settings_payload(row):
    cleaned = clean_instagram(row.instagram_url)
    return {"instagram": (cleaned or None), "updated_at": row.updated_at}


# ---------------------------------------------------------------- public reads

class PublicProjectViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectSerializer
    lookup_field = "slug"

    def get_queryset(self):
        qs = Project.objects.filter(publish=True).select_related("discipline", "segment", "cover")
        status_param = self.request.query_params.get("status")
        if status_param in {"ongoing", "completed"}:
            qs = qs.filter(status=status_param)
        discipline = (self.request.query_params.get("discipline") or "").strip()
        if discipline:
            qs = qs.filter(discipline__slug=discipline, discipline__kind="discipline")
        segment = (self.request.query_params.get("segment") or "").strip()
        if segment:
            qs = qs.filter(segment__slug=segment, segment__kind="segment")
        flag = _ownership_param(self.request)
        if flag is not None:
            qs = qs.filter(under_createch=flag)
        return qs


class PublicChromeView(APIView):
    def get(self, request):
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        return Response(chrome_response(chrome, public=True))


class PublicSettingsView(APIView):
    """GET /api/settings/ — public. instagram is null when the studio has not set a URL."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"instagram": current_instagram_url() or None})


class PublicCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Active disciplines and segments. kind is discipline or segment."""

    serializer_class = CategorySerializer

    def get_queryset(self):
        kind = (self.request.query_params.get("kind") or "").strip()
        if kind and kind not in dict(Category.KIND_CHOICES):
            raise ParseError("kind must be discipline or segment")
        qs = Category.objects.filter(active=True)
        if kind:
            qs = qs.filter(kind=kind)
        return qs


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
    queryset = Project.objects.select_related("discipline", "segment", "cover")
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


def published_document(data):
    """Chrome JSON as the public site sees it: no draft and no version list."""
    if not isinstance(data, dict):
        return {}
    cleaned = dict(data)
    for key in PUBLIC_CHROME_OMIT:
        cleaned.pop(key, None)
    return cleaned


def archive_published(chrome):
    """Keep the current published document, then drop anything past the last 10."""
    published = published_document(chrome.data)
    if not published:
        return None
    when = timezone.localtime(chrome.updated_at or timezone.now())
    ChromeVersion.objects.create(label=when.strftime("%d %b %Y, %H:%M")[:80], data=published)
    extra = list(ChromeVersion.objects.order_by("-created_at", "-id").values_list("id", flat=True)[VERSION_KEEP:])
    if extra:
        ChromeVersion.objects.filter(pk__in=extra).delete()
    return published


class AdminChromeView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        return Response(chrome_response(chrome))

    def put(self, request):
        if body_too_large(request, settings.CHROME_MAX_BODY_BYTES):
            return Response({"error": "payload too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        archive_published(chrome)
        chrome.data = request.data.get("data", request.data) if hasattr(request.data, "get") else request.data
        chrome.save()
        sync_settings_from_chrome(chrome.data)
        return Response(chrome_response(chrome))


class ChromeVersionListView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        rows = ChromeVersion.objects.all()
        return Response(ChromeVersionSerializer(rows, many=True).data)


class ChromeVersionRestoreView(APIView):
    permission_classes = [HasAdminKey]

    def post(self, request, pk):
        version = get_object_or_404(ChromeVersion, pk=pk)
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        current = chrome.data if isinstance(chrome.data, dict) else {}
        draft = current.get("draft") if isinstance(current, dict) and "draft" in current else None
        incoming = published_document(version.data)
        if published_document(current) != incoming:
            archive_published(chrome)
        restored = dict(incoming)
        if draft is not None:
            restored["draft"] = draft
        chrome.data = restored
        chrome.save()
        sync_settings_from_chrome(chrome.data)
        return Response(chrome_response(chrome))


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

logger = logging.getLogger(__name__)
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
    """Coerce a form or JSON value into a stored string. None means the value is unusable."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if value is None:
        return ""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(value)
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


MAIL_ENV_VARS = (
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS",
    "DEFAULT_FROM_EMAIL",
    "ENQUIRY_NOTIFY_TO",
)
ADMIN_INBOX_URL = "https://createch.co.ke/admin"


def _mail_config():
    """SMTP settings from the environment. Recipients are the active admin list.

    ENQUIRY_NOTIFY_TO is used only when that list has nobody active, so a cleared
    list can still fall back to the previous destination. Incomplete SMTP means no send.
    """
    values = {name: (os.environ.get(name) or "").strip() for name in MAIL_ENV_VARS}
    smtp_names = [name for name in MAIL_ENV_VARS if name != "ENQUIRY_NOTIFY_TO"]
    if not all(values[name] for name in smtp_names):
        return None
    try:
        port = int(values["EMAIL_PORT"])
    except ValueError:
        return None
    if port <= 0:
        return None
    recipients = list(
        EnquiryRecipient.objects.filter(active=True).order_by("id").values_list("email", flat=True)
    )
    if not recipients:
        recipients = [part.strip() for part in values["ENQUIRY_NOTIFY_TO"].split(",") if part.strip()]
    if not recipients:
        return None
    use_tls = values["EMAIL_USE_TLS"].lower() in ("1", "true", "yes", "on")
    return {
        "host": values["EMAIL_HOST"],
        "port": port,
        "username": values["EMAIL_HOST_USER"],
        "password": os.environ.get("EMAIL_HOST_PASSWORD") or "",
        "use_tls": use_tls,
        "from_email": values["DEFAULT_FROM_EMAIL"],
        "recipients": recipients,
    }


def notify_new_enquiry(enquiry):
    """Best-effort alert. A missing setting or a send failure never blocks the saved row."""
    try:
        config = _mail_config()
        if config is None:
            logger.warning(
                "Enquiry %s saved but email alert was not sent: mail environment is incomplete",
                enquiry.pk,
            )
            return
        body = "\n".join(
            [
                f"Name: {enquiry.name}",
                f"Email: {enquiry.email}",
                f"Phone: {enquiry.phone}",
                f"Project: {enquiry.project_type}",
                f"Site: {enquiry.site}",
                f"Stage: {enquiry.stage}",
                f"Timeline: {enquiry.timeline}",
                "",
                enquiry.message,
                "",
                ADMIN_INBOX_URL,
            ]
        )
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=config["host"],
            port=config["port"],
            username=config["username"],
            password=config["password"],
            use_tls=config["use_tls"],
        )
        send_mail(
            subject=f"New enquiry from {enquiry.name}",
            message=body,
            from_email=config["from_email"],
            recipient_list=config["recipients"],
            connection=connection,
            fail_silently=False,
        )
    except Exception:
        logger.warning(
            "Enquiry %s saved but the email alert failed",
            getattr(enquiry, "pk", None),
            exc_info=True,
        )


class PublicEnquiryView(APIView):
    """POST /api/enquiries/ — public. Honeypot field `company` is dropped."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        if body_too_large(request, settings.ENQUIRY_MAX_BODY_BYTES):
            return Response({"error": "payload too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        if not hasattr(request.data, "get"):
            return Response({"error": "JSON object required"}, status=status.HTTP_400_BAD_REQUEST)
        company = _text(request.data.get("company", ""), 200)
        if company:
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
        notify_new_enquiry(enquiry)
        return Response(EnquirySerializer(enquiry).data, status=status.HTTP_201_CREATED)


class EnquiryPagination(PageNumberPagination):
    page_size = 20


class AdminEnquiryListView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        chosen = (request.query_params.get("status") or "").strip()
        rows = Enquiry.objects.all()
        if chosen:
            if chosen not in dict(Enquiry.STATUS_CHOICES):
                return Response(
                    {"error": "status must be new, replied, or archived"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            rows = rows.filter(status=chosen)
        paginator = EnquiryPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(EnquirySerializer(page, many=True).data)


class AdminEnquirySummaryView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        counts = {key: 0 for key, _label in Enquiry.STATUS_CHOICES}
        grouped = Enquiry.objects.values("status").annotate(total=Count("id"))
        for row in grouped:
            if row["status"] in counts:
                counts[row["status"]] = row["total"]
        return Response(counts)


class AdminEnquiryDetailView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request, pk):
        row = get_object_or_404(Enquiry, pk=pk)
        return Response(EnquirySerializer(row).data)

    def patch(self, request, pk):
        row = get_object_or_404(Enquiry, pk=pk)
        new_status = request.data.get("status") if hasattr(request.data, "get") else None
        if new_status not in dict(Enquiry.STATUS_CHOICES):
            return Response(
                {"error": "status must be new, replied, or archived"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        row.status = new_status
        row.save(update_fields=["status"])
        return Response(EnquirySerializer(row).data)

    def delete(self, request, pk):
        row = get_object_or_404(Enquiry, pk=pk)
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------- enquiry recipients

def _recipient_email(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text or len(text) > 200:
        return None
    try:
        validate_email(text)
    except ValidationError:
        return None
    return text


def _recipient_active(value):
    """True or false only. JSON null and any other type are rejected."""
    if isinstance(value, bool):
        return value
    return None


class AdminEnquiryRecipientListView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        rows = EnquiryRecipient.objects.all()
        return Response(EnquiryRecipientSerializer(rows, many=True).data)

    def post(self, request):
        if not hasattr(request.data, "get"):
            return Response({"error": "JSON object required"}, status=status.HTTP_400_BAD_REQUEST)
        email = _recipient_email(request.data.get("email"))
        if not email:
            return Response({"error": "email is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        if "active" in request.data:
            active = _recipient_active(request.data.get("active"))
            if active is None:
                return Response({"error": "active must be a boolean"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            active = True
        if EnquiryRecipient.objects.filter(email__iexact=email).exists():
            return Response({"error": "email already exists"}, status=status.HTTP_400_BAD_REQUEST)
        row = EnquiryRecipient.objects.create(email=email, active=active)
        return Response(EnquiryRecipientSerializer(row).data, status=status.HTTP_201_CREATED)


class AdminEnquiryRecipientDetailView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request, pk):
        row = get_object_or_404(EnquiryRecipient, pk=pk)
        return Response(EnquiryRecipientSerializer(row).data)

    def patch(self, request, pk):
        row = get_object_or_404(EnquiryRecipient, pk=pk)
        if not hasattr(request.data, "get"):
            return Response({"error": "JSON object required"}, status=status.HTTP_400_BAD_REQUEST)
        if "email" in request.data:
            email = _recipient_email(request.data.get("email"))
            if not email:
                return Response({"error": "email is invalid"}, status=status.HTTP_400_BAD_REQUEST)
            if EnquiryRecipient.objects.filter(email__iexact=email).exclude(pk=row.pk).exists():
                return Response({"error": "email already exists"}, status=status.HTTP_400_BAD_REQUEST)
            row.email = email
        if "active" in request.data:
            active = _recipient_active(request.data.get("active"))
            if active is None:
                return Response({"error": "active must be a boolean"}, status=status.HTTP_400_BAD_REQUEST)
            row.active = active
        if "email" not in request.data and "active" not in request.data:
            return Response({"error": "email or active is required"}, status=status.HTTP_400_BAD_REQUEST)
        row.save()
        return Response(EnquiryRecipientSerializer(row).data)

    def delete(self, request, pk):
        row = get_object_or_404(EnquiryRecipient, pk=pk)
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------- site settings

class AdminSettingsView(APIView):
    """GET/PUT/PATCH /api/admin/settings/ — set or clear the public Instagram URL."""

    permission_classes = [HasAdminKey]

    def get(self, request):
        row, _ = SiteSettings.objects.get_or_create(key=SETTINGS_KEY, defaults={"instagram_url": ""})
        return Response(settings_payload(row))

    def put(self, request):
        return self._save(request)

    def patch(self, request):
        return self._save(request)

    def _save(self, request):
        if not hasattr(request.data, "get") or "instagram" not in request.data:
            return Response({"error": "instagram is required"}, status=status.HTTP_400_BAD_REQUEST)
        cleaned = clean_instagram(request.data.get("instagram"))
        if cleaned is None:
            return Response(
                {"error": "instagram must be empty or an http(s) URL"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            row, _ = SiteSettings.objects.get_or_create(key=SETTINGS_KEY, defaults={"instagram_url": cleaned})
            if row.instagram_url != cleaned:
                row.instagram_url = cleaned
                row.save(update_fields=["instagram_url", "updated_at"])
            mirror_instagram_to_chrome(cleaned)
        row.refresh_from_db()
        return Response(settings_payload(row))


# --------------------------------------------------------- categories

def _category_error(exc):
    detail = exc.detail
    if isinstance(detail, dict):
        for messages in detail.values():
            if isinstance(messages, (list, tuple)) and messages:
                return str(messages[0])
            return str(messages)
    if isinstance(detail, (list, tuple)) and detail:
        return str(detail[0])
    return str(detail)


def _category_payload(request, instance=None):
    if not hasattr(request.data, "get"):
        return None, Response({"error": "JSON object required"}, status=status.HTTP_400_BAD_REQUEST)
    data = request.data
    fields = {}
    if instance is None or "name" in data:
        name = data.get("name", instance.name if instance else "")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            return None, Response({"error": "name is required"}, status=status.HTTP_400_BAD_REQUEST)
        fields["name"] = name.strip()
    if instance is None or "kind" in data:
        kind = data.get("kind", instance.kind if instance else "")
        if kind not in dict(Category.KIND_CHOICES):
            return None, Response(
                {"error": "kind must be discipline or segment"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        fields["kind"] = kind
    if instance is None or "slug" in data or "name" in fields:
        supplied = data.get("slug") if "slug" in data else None
        if supplied is not None and not isinstance(supplied, str):
            return None, Response({"error": "slug is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            fields["slug"] = unique_category_slug(
                fields.get("name", instance.name if instance else ""),
                supplied if "slug" in data else (None if instance is None else instance.slug),
                instance,
            )
        except DRFValidationError as exc:
            return None, Response({"error": _category_error(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if "order" in data or instance is None:
        order = data.get("order")
        if order is None and instance is None:
            kind = fields.get("kind")
            order = Category.objects.filter(kind=kind).count()
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            return None, Response({"error": "order must be a positive integer or zero"}, status=status.HTTP_400_BAD_REQUEST)
        fields["order"] = order
    if "active" in data or instance is None:
        active = data.get("active", True if instance is None else instance.active)
        if not isinstance(active, bool):
            return None, Response({"error": "active must be a boolean"}, status=status.HTTP_400_BAD_REQUEST)
        fields["active"] = active
    if instance is not None and "kind" in fields and fields["kind"] != instance.kind:
        in_use = instance.discipline_projects.exists() or instance.segment_projects.exists()
        if in_use:
            return None, Response(
                {"error": "kind cannot change while projects use this category"},
                status=status.HTTP_400_BAD_REQUEST,
            )
    return fields, None


class AdminCategoryListView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        kind = (request.query_params.get("kind") or "").strip()
        if kind and kind not in dict(Category.KIND_CHOICES):
            return Response(
                {"error": "kind must be discipline or segment"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = Category.objects.all()
        if kind:
            rows = rows.filter(kind=kind)
        return Response(CategorySerializer(rows, many=True).data)

    def post(self, request):
        fields, error = _category_payload(request)
        if error is not None:
            return error
        row = Category.objects.create(**fields)
        return Response(CategorySerializer(row).data, status=status.HTTP_201_CREATED)


class AdminCategoryDetailView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request, pk):
        row = get_object_or_404(Category, pk=pk)
        return Response(CategorySerializer(row).data)

    def patch(self, request, pk):
        row = get_object_or_404(Category, pk=pk)
        fields, error = _category_payload(request, instance=row)
        if error is not None:
            return error
        for key, value in fields.items():
            setattr(row, key, value)
        row.save()
        return Response(CategorySerializer(row).data)

    def delete(self, request, pk):
        row = get_object_or_404(Category, pk=pk)
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
