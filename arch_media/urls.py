from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminCategoryDetailView,
    AdminCategoryListView,
    AdminChromeView,
    AdminEnquiryDetailView,
    AdminEnquiryListView,
    AdminEnquiryRecipientDetailView,
    AdminEnquiryRecipientListView,
    AdminEnquirySummaryView,
    AdminPinView,
    AdminSettingsView,
    ChromeVersionListView,
    ChromeVersionRestoreView,
    HealthView,
    MediaAssetViewSet,
    ProjectViewSet,
    PublicCategoryViewSet,
    PublicChromeView,
    PublicEnquiryView,
    PublicProjectViewSet,
    PublicSettingsView,
)

public_router = DefaultRouter()
public_router.register(r"projects", PublicProjectViewSet, basename="public-project")
public_router.register(r"categories", PublicCategoryViewSet, basename="public-category")

admin_router = DefaultRouter()
admin_router.register(r"media", MediaAssetViewSet, basename="admin-media")
admin_router.register(r"projects", ProjectViewSet, basename="admin-project")

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("chrome/", PublicChromeView.as_view(), name="public-chrome"),
    path("settings/", PublicSettingsView.as_view(), name="public-settings"),
    path("enquiries/", PublicEnquiryView.as_view(), name="public-enquiry"),
    path("", include(public_router.urls)),
    path("admin/chrome/versions/", ChromeVersionListView.as_view(), name="admin-chrome-versions"),
    path(
        "admin/chrome/versions/<int:pk>/restore/",
        ChromeVersionRestoreView.as_view(),
        name="admin-chrome-restore",
    ),
    path("admin/chrome/", AdminChromeView.as_view(), name="admin-chrome"),
    path("admin/pin/", AdminPinView.as_view(), name="admin-pin"),
    path("admin/settings/", AdminSettingsView.as_view(), name="admin-settings"),
    path("admin/enquiries/summary/", AdminEnquirySummaryView.as_view(), name="admin-enquiries-summary"),
    path("admin/enquiries/", AdminEnquiryListView.as_view(), name="admin-enquiries"),
    path("admin/enquiries/<int:pk>/", AdminEnquiryDetailView.as_view(), name="admin-enquiry"),
    path(
        "admin/enquiry-recipients/",
        AdminEnquiryRecipientListView.as_view(),
        name="admin-enquiry-recipients",
    ),
    path(
        "admin/enquiry-recipients/<int:pk>/",
        AdminEnquiryRecipientDetailView.as_view(),
        name="admin-enquiry-recipient",
    ),
    path("admin/categories/", AdminCategoryListView.as_view(), name="admin-categories"),
    path("admin/categories/<int:pk>/", AdminCategoryDetailView.as_view(), name="admin-category"),
    path("admin/", include(admin_router.urls)),
]
