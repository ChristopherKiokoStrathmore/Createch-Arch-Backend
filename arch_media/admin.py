from django.contrib import admin

from .models import (
    AdminPin,
    Category,
    Enquiry,
    EnquiryRecipient,
    MediaAsset,
    Project,
    ProjectImage,
    SiteChrome,
    SiteSettings,
)


class ProjectImageInline(admin.TabularInline):
    model = ProjectImage
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "publish",
        "under_createch",
        "discipline",
        "segment",
        "order",
        "year",
        "location",
    )
    list_filter = ("status", "publish", "under_createch", "discipline", "segment")
    search_fields = ("title", "location", "typology")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [ProjectImageInline]


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ("id", "alt", "width", "height", "sort", "created_at")


@admin.register(SiteChrome)
class SiteChromeAdmin(admin.ModelAdmin):
    list_display = ("key", "updated_at")


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "email", "project_type", "site", "status")
    list_filter = ("status", "project_type", "stage")
    search_fields = ("name", "email", "site", "message")
    readonly_fields = ("created_at",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "kind", "order", "active")
    list_filter = ("kind", "active")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(EnquiryRecipient)
class EnquiryRecipientAdmin(admin.ModelAdmin):
    list_display = ("email", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("email",)


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("key", "instagram_url", "updated_at")


@admin.register(AdminPin)
class AdminPinAdmin(admin.ModelAdmin):
    list_display = ("key", "version", "updated_at")
    readonly_fields = ("pin_hash", "version", "updated_at")
