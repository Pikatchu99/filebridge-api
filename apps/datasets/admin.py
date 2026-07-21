from django.contrib import admin

from apps.datasets.models import Dataset, DatasetApiKey, DatasetColumn


class DatasetColumnInline(admin.TabularInline):
    model = DatasetColumn
    extra = 0


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "status", "row_count", "column_count", "created_at"]
    list_filter = ["status", "is_public"]
    search_fields = ["name", "original_filename", "owner__username"]
    inlines = [DatasetColumnInline]


@admin.register(DatasetApiKey)
class DatasetApiKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "dataset", "is_active", "created_at", "last_used_at"]
    # key_hash is a SHA-256 digest, not sensitive on its own, but there's no reason to
    # surface it anywhere beyond the one-time creation response — keep the admin
    # change form from displaying it at all rather than relying on that being harmless.
    exclude = ["key_hash"]
    readonly_fields = ["dataset", "created_at", "last_used_at"]
