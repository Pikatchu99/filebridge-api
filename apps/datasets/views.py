import csv
import io

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.datasets.exceptions import DatasetIngestionError, UnknownColumnError
from apps.datasets.filters import build_row_queryset
from apps.datasets.models import Dataset, DatasetApiKey, DatasetRow
from apps.datasets.permissions import HasDatasetReadAccess, IsOwner
from apps.datasets.serializers import (
    DataQualityReportSerializer,
    DatasetApiKeyCreateSerializer,
    DatasetApiKeySerializer,
    DatasetColumnSerializer,
    DatasetPreviewResultSerializer,
    DatasetPreviewSerializer,
    DatasetRowSerializer,
    DatasetSerializer,
    DatasetUploadSerializer,
    DatasetVisibilitySerializer,
)
from apps.datasets.services.api_keys import generate_api_key
from apps.datasets.services.ingestion import (
    list_workbook_sheets,
    normalize_column_name,
    preview_file,
)
from apps.datasets.services.quality import build_quality_report
from apps.datasets.tasks import ingest_dataset_file
from apps.datasets.throttling import DatasetApiKeyRateThrottle, RetryRateThrottle

# Read-only actions reachable three ways: the owner (session/basic auth), a DatasetApiKey
# scoped to the target dataset, or anyone at all if the dataset is public (see
# HasDatasetReadAccess). Everything else (list, upload, destroy, key/visibility
# management) stays strictly owner-only.
_API_KEY_ELIGIBLE_ACTIONS = {"dataset_schema", "rows", "row_detail", "export", "quality"}

# Substrings that identify an IntegrityError as the Dataset(owner, name) unique
# constraint specifically — Postgres names it in the message, SQLite lists columns.
_NAME_COLLISION_ERROR_MARKERS = (
    "unique_owner_dataset_name",
    "datasets_dataset.owner_id, datasets_dataset.name",
)


class DatasetViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DatasetSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_permissions(self):
        if self.action in _API_KEY_ELIGIBLE_ACTIONS:
            return [HasDatasetReadAccess()]
        return super().get_permissions()

    def get_throttles(self):
        # An API key gets its own isolated rate bucket instead of sharing the global
        # anon-by-IP bucket with every other key and every anonymous public-dataset
        # reader behind the same IP (see DatasetApiKeyRateThrottle).
        if isinstance(getattr(self.request, "auth", None), DatasetApiKey):
            return [DatasetApiKeyRateThrottle()]
        # Retrying re-fires a dataset's webhook if it has one — a much tighter cap
        # than the general user rate (see RetryRateThrottle).
        if self.action == "retry":
            return [RetryRateThrottle()]
        return super().get_throttles()

    def perform_destroy(self, instance):
        # FileField doesn't delete its file on model delete — without this, every
        # upload-then-delete cycle leaks a file under MEDIA_ROOT indefinitely.
        if instance.source_file:
            instance.source_file.delete(save=False)
        super().perform_destroy(instance)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Dataset.objects.none()

        if self.action in _API_KEY_ELIGIBLE_ACTIONS:
            return self._readable_dataset_queryset()

        if not self.request.user.is_authenticated:
            return Dataset.objects.none()
        return Dataset.objects.filter(owner=self.request.user)

    def _readable_dataset_queryset(self):
        """Everything HasDatasetReadAccess would grant: public datasets, plus whichever
        of "the request's own datasets" / "the API key's one dataset" applies.
        """
        query = Q(is_public=True)

        api_key = getattr(self.request, "auth", None)
        if isinstance(api_key, DatasetApiKey):
            query |= Q(pk=api_key.dataset_id)
        elif self.request.user.is_authenticated:
            query |= Q(owner=self.request.user)

        return Dataset.objects.filter(query)

    @extend_schema(request=DatasetUploadSerializer, responses=DatasetSerializer(many=True))
    @action(detail=False, methods=["post"])
    def upload(self, request):
        """Creates one Dataset per sheet for an .xlsx upload (every sheet, unless
        `sheet_names` narrows it down), or a single Dataset for a .csv upload — always
        responds with a list, even when that list has exactly one dataset in it, so
        callers don't have to handle two different response shapes.
        """
        serializer = DatasetUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        base_name = serializer.validated_data["name"]
        is_xlsx = uploaded_file.name.lower().endswith(".xlsx")

        if is_xlsx:
            raw_bytes = uploaded_file.read()
            try:
                available_sheets = list_workbook_sheets(raw_bytes)
            except DatasetIngestionError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            requested_sheets = serializer.validated_data.get("sheet_names")
            if requested_sheets:
                # Only meaningful to check "unknown" against a *different* list — when
                # sheet_names is omitted, requested_sheets IS available_sheets, so this
                # would otherwise be an O(n²) self-comparison for every real sheet in
                # the workbook, run before the cheap cap check just below.
                unknown = [s for s in requested_sheets if s not in available_sheets]
                if unknown:
                    return Response(
                        {
                            "detail": f"Unknown sheet(s): {', '.join(unknown)}. "
                            f"Available sheets: {', '.join(available_sheets)}."
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                requested_sheets = available_sheets

            if len(requested_sheets) > settings.FILEBRIDGE_MAX_SHEETS_PER_UPLOAD:
                return Response(
                    {
                        "detail": f"Too many sheets ({len(requested_sheets)}) requested in one "
                        f"upload — max {settings.FILEBRIDGE_MAX_SHEETS_PER_UPLOAD}."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            sheets_to_create = requested_sheets
        else:
            raw_bytes = None
            sheets_to_create = [None]  # a CSV has one implicit "sheet"

        naming_needs_suffix = len(sheets_to_create) > 1

        # Create every dataset row atomically — either all N sheets get a Dataset or
        # none do. A name collision (this owner already has a dataset with that name)
        # would otherwise raise an uncaught IntegrityError, and without the transaction
        # a multi-sheet upload could partially succeed (e.g. sheet 1 created, sheet 2
        # collides) leaving a confusing, half-finished result.
        datasets = []
        try:
            with transaction.atomic():
                for sheet_name in sheets_to_create:
                    dataset_name = base_name
                    if naming_needs_suffix:
                        dataset_name = f"{base_name}-{normalize_column_name(sheet_name)}"[:100]

                    source = (
                        ContentFile(raw_bytes, name=uploaded_file.name)
                        if is_xlsx
                        else uploaded_file
                    )
                    datasets.append(
                        Dataset.objects.create(
                            owner=request.user,
                            name=dataset_name,
                            description=serializer.validated_data.get("description", ""),
                            is_public=serializer.validated_data.get("is_public", False),
                            webhook_url=serializer.validated_data.get("webhook_url", ""),
                            sheet_name=sheet_name or "",
                            original_filename=uploaded_file.name,
                            source_file=source,
                        )
                    )
        except IntegrityError as exc:
            # Narrow to the specific constraint we expect — anything else is a real,
            # unexpected failure that should surface as a 500, not get mislabeled as
            # "name already exists". Postgres includes the constraint's name in the
            # message; SQLite instead lists the columns it covers.
            if not any(marker in str(exc) for marker in _NAME_COLLISION_ERROR_MARKERS):
                raise
            return Response(
                {
                    "detail": f"A dataset named '{base_name}' already exists"
                    + (" (or would, once a sheet suffix is added)." if naming_needs_suffix else ".")
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only queue ingestion once every dataset row is actually committed — .delay()
        # inside the transaction above could hand a task to a worker before the row it
        # needs is visible, since Celery here talks to a broker in a separate process.
        for dataset in datasets:
            ingest_dataset_file.delay(dataset.id)

        # In eager mode (tests, or a dev setup with no worker running) the task above
        # already ran synchronously and resolved each dataset's status; in a real
        # deployment they're still PENDING here — the client polls GET .../ for the
        # outcome of each one.
        for dataset in datasets:
            dataset.refresh_from_db()
        return Response(
            DatasetSerializer(datasets, many=True).data, status=status.HTTP_202_ACCEPTED
        )

    @extend_schema(request=DatasetPreviewSerializer, responses=DatasetPreviewResultSerializer)
    @action(detail=False, methods=["post"])
    def preview(self, request):
        serializer = DatasetPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data["file"]
        sheet_name = serializer.validated_data.get("sheet_name") or None

        try:
            result = preview_file(uploaded_file, uploaded_file.name, sheet_name=sheet_name)
        except DatasetIngestionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        dataset = self.get_object()
        if dataset.status != Dataset.Status.FAILED:
            return Response(
                {"detail": "Only a failed dataset can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        dataset.status = Dataset.Status.PENDING
        dataset.failure_reason = ""
        dataset.save(update_fields=["status", "failure_reason"])

        ingest_dataset_file.delay(dataset.id)

        dataset.refresh_from_db()
        return Response(DatasetSerializer(dataset).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"], url_path="schema", url_name="schema")
    def dataset_schema(self, request, pk=None):
        dataset = self.get_object()
        columns = dataset.columns.all()
        return Response(DatasetColumnSerializer(columns, many=True).data)

    @action(detail=True, methods=["get"])
    def rows(self, request, pk=None):
        dataset = self.get_object()
        try:
            queryset = build_row_queryset(dataset, request.query_params)
        except UnknownColumnError as exc:
            return Response(
                {"detail": f"Unknown column: '{exc}'."}, status=status.HTTP_400_BAD_REQUEST
            )

        page = self.paginate_queryset(queryset)
        serializer = DatasetRowSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["get"], url_path=r"rows/(?P<row_pk>\d+)", url_name="row-detail")
    def row_detail(self, request, pk=None, row_pk=None):
        dataset = self.get_object()
        row = get_object_or_404(DatasetRow, dataset=dataset, pk=row_pk)
        return Response(DatasetRowSerializer(row).data)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        dataset = self.get_object()
        columns = list(dataset.columns.values_list("name_normalized", flat=True))

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for row in dataset.rows.all():
            writer.writerow([_escape_csv_formula(row.data.get(column, "")) for column in columns])

        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{dataset.name}.csv"'
        return response

    @extend_schema(responses=DataQualityReportSerializer)
    @action(detail=True, methods=["get"], url_path="quality", url_name="quality")
    def quality(self, request, pk=None):
        dataset = self.get_object()
        return Response(build_quality_report(dataset))

    @action(detail=True, methods=["patch"], url_path="visibility", url_name="visibility")
    def visibility(self, request, pk=None):
        dataset = self.get_object()
        serializer = DatasetVisibilitySerializer(dataset, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(DatasetSerializer(dataset).data)

    @action(detail=True, methods=["get", "post"], url_path="api-keys", url_name="api-keys")
    def api_keys(self, request, pk=None):
        dataset = self.get_object()

        if request.method == "POST":
            serializer = DatasetApiKeyCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            raw_key, key_hash = generate_api_key()
            api_key = DatasetApiKey.objects.create(
                dataset=dataset,
                name=serializer.validated_data["name"],
                key_hash=key_hash,
            )
            api_key.key = raw_key  # shown once, in this response only — never stored
            return Response(
                DatasetApiKeyCreateSerializer(api_key).data, status=status.HTTP_201_CREATED
            )

        keys = dataset.api_keys.all()
        return Response(DatasetApiKeySerializer(keys, many=True).data)

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"api-keys/(?P<key_pk>\d+)",
        url_name="api-key-detail",
    )
    def revoke_api_key(self, request, pk=None, key_pk=None):
        dataset = self.get_object()
        api_key = get_object_or_404(DatasetApiKey, dataset=dataset, pk=key_pk)
        api_key.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _escape_csv_formula(value):
    """Neutralize CSV/spreadsheet formula injection (e.g. a cell containing '=cmd(...)').

    Prefixing with a single quote is the standard mitigation recommended by OWASP for
    values that will be opened in Excel/Sheets.
    """
    text = str(value)
    if text.startswith(_FORMULA_TRIGGER_CHARS):
        return f"'{text}"
    return text
