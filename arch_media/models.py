from django.db import models


class MediaAsset(models.Model):
    """A single uploaded image. Bytes live on the volume (or R2); the row keeps metadata."""

    file = models.ImageField(upload_to="uploads/", width_field="width", height_field="height")
    alt = models.CharField(max_length=300, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    sort = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort", "id"]

    def __str__(self):
        return self.alt or f"MediaAsset #{self.pk}"


class Project(models.Model):
    STATUS_CHOICES = [("ongoing", "Ongoing"), ("completed", "Completed")]

    slug = models.SlugField(max_length=200, unique=True)
    title = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    year = models.CharField(max_length=20, blank=True)
    typology = models.CharField(max_length=120, blank=True)
    brief = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")
    publish = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    cover = models.ForeignKey(
        MediaAsset, null=True, blank=True, on_delete=models.SET_NULL, related_name="cover_for"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "-year", "id"]

    def __str__(self):
        return self.title


class ProjectImage(models.Model):
    project = models.ForeignKey(Project, related_name="gallery", on_delete=models.CASCADE)
    asset = models.ForeignKey(MediaAsset, on_delete=models.CASCADE)
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort", "id"]
        unique_together = [("project", "asset")]

    def __str__(self):
        return f"{self.project.title} / asset {self.asset_id}"


class SiteChrome(models.Model):
    """Singleton-ish JSON layout map for the site chrome edited by the PIN admin."""

    key = models.CharField(max_length=50, unique=True, default="default")
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SiteChrome<{self.key}>"
