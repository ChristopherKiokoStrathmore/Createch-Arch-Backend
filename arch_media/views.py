from django.contrib.auth.hashers import check_password, make_password
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AdminPin, MediaAsset, Project, ProjectImage, SiteChrome
from .permissions import HasAdminKey
from .serializers import (
    MediaAssetSerializer,
    MediaUploadSerializer,
    ProjectImageSerializer,
    ProjectSerializer,
    SiteChromeSerializer,
)

CHROME_KEY = "default"
PIN_KEY = "default"
PIN_MIN, PIN_MAX = 6, 64


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
        return Response(SiteChromeSerializer(chrome).data)


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
        order = request.data.get("order", [])
        for index, asset_id in enumerate(order):
            MediaAsset.objects.filter(pk=asset_id).update(sort=index)
        return Response({"status": "ok", "count": len(order)})


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [HasAdminKey]
    lookup_field = "slug"

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        order = request.data.get("order", [])
        for index, slug in enumerate(order):
            Project.objects.filter(slug=slug).update(order=index)
        return Response({"status": "ok", "count": len(order)})

    @action(detail=True, methods=["post"])
    def gallery(self, request, slug=None):
        project = self.get_object()
        asset = get_object_or_404(MediaAsset, pk=request.data.get("asset_id"))
        item, _ = ProjectImage.objects.update_or_create(
            project=project, asset=asset, defaults={"sort": request.data.get("sort", 0)}
        )
        return Response(ProjectImageSerializer(item).data, status=status.HTTP_201_CREATED)


class AdminChromeView(APIView):
    permission_classes = [HasAdminKey]

    def get(self, request):
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        return Response(SiteChromeSerializer(chrome).data)

    def put(self, request):
        chrome, _ = SiteChrome.objects.get_or_create(key=CHROME_KEY)
        chrome.data = request.data.get("data", request.data)
        chrome.save()
        return Response(SiteChromeSerializer(chrome).data)


class AdminPinView(APIView):
    """The /admin PIN. Only the Next server calls this, with X-Admin-Key.

    GET  -> {set, version}
    POST {pin} -> {ok, set, version}      verify
    PUT  {pin} -> {set, version}          replace
    """

    permission_classes = [HasAdminKey]

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
        if not isinstance(pin, str) or not (PIN_MIN <= len(pin) <= PIN_MAX):
            return Response(
                {"error": f"PIN must be {PIN_MIN}-{PIN_MAX} characters"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        row, _ = AdminPin.objects.get_or_create(key=PIN_KEY, defaults={"pin_hash": ""})
        row.pin_hash = make_password(pin)
        row.version += 1
        row.save()
        return Response(self.state(row))
