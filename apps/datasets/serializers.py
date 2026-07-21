from django.conf import settings
from rest_framework import serializers

from apps.datasets.models import Dataset, DatasetApiKey, DatasetColumn, DatasetRow


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = [
            "id",
            "name",
            "original_filename",
            "description",
            "status",
            "failure_reason",
            "row_count",
            "column_count",
            "is_public",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


ALLOWED_UPLOAD_EXTENSIONS = (".csv", ".xlsx")


def validate_upload_file(file):
    if not file.name.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS):
        raise serializers.ValidationError("Only .csv or .xlsx files are supported.")
    if file.size > settings.FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES:
        max_mb = settings.FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
        raise serializers.ValidationError(f"File exceeds the {max_mb:.0f} MB size limit.")
    return file


class DatasetUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)

    class Meta:
        model = Dataset
        fields = ["id", "name", "description", "is_public", "file"]

    def validate_file(self, file):
        return validate_upload_file(file)


class DatasetPreviewSerializer(serializers.Serializer):
    """Same file validation as upload, but preview never creates a Dataset — see the
    `preview` action, which parses the file and discards it without persisting anything.
    """

    file = serializers.FileField()

    def validate_file(self, file):
        return validate_upload_file(file)


class PreviewColumnSerializer(serializers.Serializer):
    name_original = serializers.CharField()
    name_normalized = serializers.CharField()
    detected_type = serializers.CharField()


class DatasetPreviewResultSerializer(serializers.Serializer):
    columns = PreviewColumnSerializer(many=True)
    row_count = serializers.IntegerField()
    sample_rows = serializers.ListField(child=serializers.DictField())


class DatasetVisibilitySerializer(serializers.ModelSerializer):
    """Only field an owner can change post-upload: whether the dataset is publicly readable."""

    class Meta:
        model = Dataset
        fields = ["is_public"]


class DatasetColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetColumn
        fields = ["name_original", "name_normalized", "detected_type", "position", "nullable"]


class DatasetRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetRow
        fields = ["id", "row_index", "data"]


class ColumnQualitySerializer(serializers.Serializer):
    name = serializers.CharField()
    detected_type = serializers.CharField()
    missing_count = serializers.IntegerField()
    invalid_count = serializers.IntegerField()


class DataQualityReportSerializer(serializers.Serializer):
    row_count = serializers.IntegerField()
    duplicate_row_count = serializers.IntegerField()
    columns = ColumnQualitySerializer(many=True)


class DatasetApiKeySerializer(serializers.ModelSerializer):
    """Used for listing keys — never exposes key_hash, let alone the raw key."""

    class Meta:
        model = DatasetApiKey
        fields = ["id", "name", "is_active", "created_at", "last_used_at"]
        read_only_fields = fields


class DatasetApiKeyCreateSerializer(serializers.ModelSerializer):
    key = serializers.CharField(read_only=True)

    class Meta:
        model = DatasetApiKey
        fields = ["id", "name", "is_active", "created_at", "key"]
        read_only_fields = ["id", "is_active", "created_at", "key"]

    def validate_name(self, name):
        if not name.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return name
