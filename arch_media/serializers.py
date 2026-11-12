from django.utils.text import slugify
from rest_framework import serializers

from .images import normalize_uploaded_image
from .models import (
    Category,
    ChromeVersion,
    Enquiry,
    EnquiryRecipient,
    MediaAsset,
    Project,
    ProjectImage,
    SiteChrome,
)


class MediaAssetSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = ["id", "url", "alt", "width", "height", "sort", "created_at"]
        read_only_fields = ["id", "url", "width", "height", "created_at"]

    def get_url(self, obj):
        if not obj.file:
            return None
        url = obj.file.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class MediaUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaAsset
        fields = ["id", "file", "alt", "sort"]
        read_only_fields = ["id"]

    def validate_file(self, uploaded):
        return normalize_uploaded_image(uploaded)


class ProjectImageSerializer(serializers.ModelSerializer):
    asset = MediaAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=MediaAsset.objects.all(), source="asset", write_only=True
    )

    class Meta:
        model = ProjectImage
        fields = ["id", "asset", "asset_id", "sort"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "kind", "order", "active"]
        read_only_fields = ["id"]


class CategoryLinkField(serializers.Field):
    """Read a category as an object. Write an id, a slug, that object, or null."""

    def __init__(self, kind, **kwargs):
        self.kind = kind
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        super().__init__(**kwargs)

    def to_representation(self, value):
        if value is None:
            return None
        return {"id": value.id, "name": value.name, "slug": value.slug, "kind": value.kind}

    def to_internal_value(self, data):
        if data is None or data == "":
            return None
        queryset = Category.objects.filter(kind=self.kind)
        if isinstance(data, bool) or isinstance(data, int):
            if isinstance(data, bool) or data < 1:
                raise serializers.ValidationError(f"unknown {self.kind}")
            category = queryset.filter(pk=data).first()
        elif isinstance(data, str):
            text = data.strip()
            if text.isdigit():
                category = queryset.filter(pk=int(text)).first() or queryset.filter(slug=text).first()
            else:
                category = queryset.filter(slug=text).first()
        elif isinstance(data, dict):
            if data.get("id") is not None:
                category = queryset.filter(pk=data.get("id")).first()
            elif data.get("slug"):
                category = queryset.filter(slug=data.get("slug")).first()
            else:
                category = None
        else:
            raise serializers.ValidationError("must be an id, a slug, or null")
        if category is None:
            raise serializers.ValidationError(f"unknown {self.kind}")
        return category


def coerce_bool(value, field):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
        return False
    raise serializers.ValidationError({field: "must be a boolean"})


class ProjectSerializer(serializers.ModelSerializer):
    cover = MediaAssetSerializer(read_only=True)
    cover_id = serializers.PrimaryKeyRelatedField(
        queryset=MediaAsset.objects.all(),
        source="cover",
        write_only=True,
        required=False,
        allow_null=True,
    )
    gallery = ProjectImageSerializer(many=True, read_only=True)
    discipline = CategoryLinkField(kind="discipline")
    segment = CategoryLinkField(kind="segment")

    class Meta:
        model = Project
        fields = [
            "id", "slug", "title", "location", "year", "typology",
            "under_createch", "discipline", "segment", "brief",
            "status", "publish", "order", "cover", "cover_id", "gallery",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        initial = self.initial_data if isinstance(self.initial_data, dict) else {}
        if "under_createch" not in initial and "underCreatech" in initial:
            attrs["under_createch"] = coerce_bool(initial.get("underCreatech"), "under_createch")
        if "discipline" not in initial and "discipline_id" in initial:
            attrs["discipline"] = self.fields["discipline"].run_validation(initial.get("discipline_id"))
        if "segment" not in initial and "segment_id" in initial:
            attrs["segment"] = self.fields["segment"].run_validation(initial.get("segment_id"))
        return attrs


def unique_category_slug(name, slug, instance=None):
    text = slugify((slug or "").strip()) or slugify(name or "")
    if not text:
        raise serializers.ValidationError({"slug": "slug is required"})
    taken = Category.objects.filter(slug=text)
    if instance is not None:
        taken = taken.exclude(pk=instance.pk)
    if taken.exists():
        raise serializers.ValidationError({"slug": "slug already exists"})
    return text


class EnquiryRecipientSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnquiryRecipient
        fields = ["id", "email", "active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SiteChromeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteChrome
        fields = ["data", "updated_at"]
        read_only_fields = ["updated_at"]


class ChromeVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChromeVersion
        fields = ["id", "label", "created_at", "data"]
        read_only_fields = fields


class EnquirySerializer(serializers.ModelSerializer):
    class Meta:
        model = Enquiry
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "project_type",
            "site",
            "stage",
            "timeline",
            "message",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "status", "created_at"]
