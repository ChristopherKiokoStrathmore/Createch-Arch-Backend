from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminChromeView,
    AdminPinView,
    MediaAssetViewSet,
    ProjectViewSet,
    PublicChromeView,
    PublicProjectViewSet,
)

public_router = DefaultRouter()
public_router.register(r"projects", PublicProjectViewSet, basename="public-project")

admin_router = DefaultRouter()
admin_router.register(r"media", MediaAssetViewSet, basename="admin-media")
admin_router.register(r"projects", ProjectViewSet, basename="admin-project")

urlpatterns = [
    path("chrome/", PublicChromeView.as_view(), name="public-chrome"),
    path("", include(public_router.urls)),
    path("admin/chrome/", AdminChromeView.as_view(), name="admin-chrome"),
    path("admin/pin/", AdminPinView.as_view(), name="admin-pin"),
    path("admin/", include(admin_router.urls)),
]
