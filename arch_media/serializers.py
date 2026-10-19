from rest_framework import serializers

from .images import normalize_uploaded_image
from .models import ChromeVersion, Enquiry, MediaAsset, Project, ProjectImage, SiteChrome


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

    class Meta:
        model = Project
        fields = [
            "id", "slug", "title", "location", "year", "typology", "brief",
            "status", "publish", "order", "cover", "cover_id", "gallery",
            "created_at", "updated_at",
        ]
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
