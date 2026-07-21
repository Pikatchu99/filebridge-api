from django.conf import settings
from django.db import models


class Dataset(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="datasets"
    )
    name = models.SlugField(max_length=100)
    original_filename = models.CharField(max_length=255)
    # Kept around so the async ingestion task (running in a separate worker process,
    # with no access to the original request) has something to read from. Not exposed
    # via the API — see DatasetSerializer.
    source_file = models.FileField(upload_to="uploads/%Y/%m/%d/", default="")
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(max_length=255, blank=True, default="")
    row_count = models.PositiveIntegerField(default=0)
    column_count = models.PositiveIntegerField(default=0)
    is_public = models.BooleanField(default=False)
    # Fired once after every ingestion attempt (initial upload or retry), success or
    # failure — see apps/datasets/tasks.py and services/webhooks.py. Validated against
    # private/internal addresses at set-time and again right before sending.
    webhook_url = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_owner_dataset_name"),
        ]

    def __str__(self):
        return self.name


class DatasetColumn(models.Model):
    class ColumnType(models.TextChoices):
        STRING = "string", "String"
        NUMBER = "number", "Number"
        DATE = "date", "Date"
        EMAIL = "email", "Email"
        BOOLEAN = "boolean", "Boolean"
        UNKNOWN = "unknown", "Unknown"

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="columns")
    name_original = models.CharField(max_length=255)
    name_normalized = models.SlugField(max_length=255)
    detected_type = models.CharField(
        max_length=10, choices=ColumnType.choices, default=ColumnType.UNKNOWN
    )
    position = models.PositiveIntegerField()
    nullable = models.BooleanField(default=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "position"], name="unique_dataset_column_position"
            ),
            models.UniqueConstraint(
                fields=["dataset", "name_normalized"], name="unique_dataset_column_name"
            ),
        ]

    def __str__(self):
        return self.name_normalized


class DatasetRow(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="rows")
    row_index = models.PositiveIntegerField()
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "row_index"], name="unique_dataset_row_index"
            ),
        ]

    def __str__(self):
        return f"{self.dataset.name}#{self.row_index}"


class DatasetApiKey(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
