import csv
import io

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.datasets.exceptions import DatasetIngestionError, UnknownColumnError
from apps.datasets.filters import build_row_queryset
from apps.datasets.models import Dataset, DatasetRow
from apps.datasets.permissions import IsOwner
from apps.datasets.serializers import (
    DatasetColumnSerializer,
    DatasetRowSerializer,
    DatasetSerializer,
    DatasetUploadSerializer,
)
from apps.datasets.services.ingestion import ingest_csv_file


class DatasetViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DatasetSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Dataset.objects.none()
        return Dataset.objects.filter(owner=self.request.user)

    @action(detail=False, methods=["post"])
    @extend_schema(request=DatasetUploadSerializer, responses=DatasetSerializer)
    def upload(self, request):
        serializer = DatasetUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        dataset = Dataset.objects.create(
            owner=request.user,
            name=serializer.validated_data["name"],
            description=serializer.validated_data.get("description", ""),
            is_public=serializer.validated_data.get("is_public", False),
            original_filename=serializer.validated_data["file"].name,
        )

        try:
            ingest_csv_file(dataset, serializer.validated_data["file"])
        except DatasetIngestionError as exc:
            return Response(
                {"detail": str(exc), "dataset": DatasetSerializer(dataset).data},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(DatasetSerializer(dataset).data, status=status.HTTP_201_CREATED)

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
