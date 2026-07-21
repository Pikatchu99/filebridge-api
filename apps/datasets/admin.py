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
