import io

import pytest
from django.contrib.auth import get_user_model

from apps.datasets.exceptions import UnknownColumnError
from apps.datasets.filters import build_row_queryset
from apps.datasets.models import Dataset
from apps.datasets.services.ingestion import ingest_csv_file

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def dataset():
    owner = User.objects.create_user(username="modeste", password="pass1234")
    dataset = Dataset.objects.create(
        owner=owner, name="inscriptions", original_filename="inscriptions.csv"
    )
    ingest_csv_file(
        dataset,
        io.BytesIO(b"name,search,page\nSarah,keep,1\nLea,drop,2\n"),
    )
    dataset.refresh_from_db()
    return dataset


class TestBuildRowQueryset:
    def test_column_named_like_a_reserved_param_is_still_filterable(self, dataset):
        queryset = build_row_queryset(dataset, {"search": "keep"})
        assert list(queryset.values_list("data__name", flat=True)) == ["Sarah"]

    def test_unknown_column_raises(self, dataset):
        with pytest.raises(UnknownColumnError):
            build_row_queryset(dataset, {"not_a_column": "x"})
