from django.contrib import admin

from .models import AdminPin, Enquiry, MediaAsset, Project, ProjectImage, SiteChrome


class ProjectImageInline(admin.TabularInline):
    model = ProjectImage
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "publish", "order", "year", "location")
    list_filter = ("status", "publish")
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
    list_display = ("created_at", "name", "email", "project_type", "site", "read")
    list_filter = ("read", "project_type", "stage")
    search_fields = ("name", "email", "site", "message")
    readonly_fields = ("created_at",)


@admin.register(AdminPin)
class AdminPinAdmin(admin.ModelAdmin):
    list_display = ("key", "version", "updated_at")
    readonly_fields = ("pin_hash", "version", "updated_at")
