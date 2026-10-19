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


class ChromeVersion(models.Model):
    """A previous published chrome document. Drafts are not stored here."""

    label = models.CharField(max_length=80)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.label


class Enquiry(models.Model):
    """A public contact-form submission. Bytes of the message live in Postgres."""

    STATUS_CHOICES = [("new", "New"), ("replied", "Replied"), ("archived", "Archived")]

    name = models.CharField(max_length=120)
    email = models.EmailField(max_length=200)
    phone = models.CharField(max_length=40, blank=True)
    project_type = models.CharField(max_length=120, blank=True)
    site = models.CharField(max_length=200, blank=True)
    stage = models.CharField(max_length=120, blank=True)
    timeline = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "enquiries"

    def __str__(self):
        return f"{self.name} <{self.email}>"


class AdminPin(models.Model):
    """The editor's /admin PIN, hashed. One row; replaces ADMIN_PIN on Vercel once set."""

    key = models.CharField(max_length=50, unique=True, default="default")
    pin_hash = models.CharField(max_length=256)
    # Bumped on every change; the site signs it into the session cookie, so
    # changing the PIN signs out every other open admin session.
    version = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AdminPin<{self.key}> v{self.version}"
