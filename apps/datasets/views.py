import csv
import io

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.datasets.exceptions import UnknownColumnError
from apps.datasets.filters import build_row_queryset
from apps.datasets.models import Dataset, DatasetApiKey, DatasetRow
from apps.datasets.permissions import HasDatasetReadAccess, IsOwner
from apps.datasets.serializers import (
    DataQualityReportSerializer,
    DatasetApiKeyCreateSerializer,
    DatasetApiKeySerializer,
    DatasetColumnSerializer,
    DatasetRowSerializer,
    DatasetSerializer,
    DatasetUploadSerializer,
    DatasetVisibilitySerializer,
)
from apps.datasets.services.api_keys import generate_api_key
from apps.datasets.services.quality import build_quality_report
from apps.datasets.tasks import ingest_dataset_file

# Read-only actions reachable three ways: the owner (session/basic auth), a DatasetApiKey
# scoped to the target dataset, or anyone at all if the dataset is public (see
# HasDatasetReadAccess). Everything else (list, upload, destroy, key/visibility
# management) stays strictly owner-only.
_API_KEY_ELIGIBLE_ACTIONS = {"dataset_schema", "rows", "row_detail", "export", "quality"}


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

    @action(detail=False, methods=["post"])
    @extend_schema(request=DatasetUploadSerializer, responses=DatasetSerializer)
    def upload(self, request):
        serializer = DatasetUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        dataset = Dataset.objects.create(
            owner=request.user,
            name=serializer.validated_data["name"],
            description=serializer.validated_data.get("description", ""),
            is_public=serializer.validated_data.get("is_public", False),
            original_filename=uploaded_file.name,
            source_file=uploaded_file,
        )

        ingest_dataset_file.delay(dataset.id)

        # In eager mode (tests, or a dev setup with no worker running) the task above
        # already ran synchronously and resolved dataset's status; in a real deployment
        # it's still PENDING here — the client polls GET .../ for the outcome.
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

    @action(detail=True, methods=["get"], url_path="quality", url_name="quality")
    @extend_schema(responses=DataQualityReportSerializer)
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
