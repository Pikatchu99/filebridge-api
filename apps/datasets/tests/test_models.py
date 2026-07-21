import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from apps.datasets.models import Dataset, DatasetApiKey, DatasetColumn, DatasetRow

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="modeste", password="pass1234")


@pytest.fixture
def dataset(user):
    return Dataset.objects.create(
        owner=user,
        name="inscriptions",
        original_filename="inscriptions.csv",
    )


class TestDataset:
    def test_defaults_on_creation(self, dataset):
        assert dataset.status == Dataset.Status.PENDING
        assert dataset.row_count == 0
        assert dataset.column_count == 0
        assert dataset.is_public is False
        assert dataset.created_at is not None

    def test_str_returns_name(self, dataset):
        assert str(dataset) == "inscriptions"

    def test_owner_and_name_must_be_unique_together(self, user, dataset):
        with pytest.raises(IntegrityError):
            Dataset.objects.create(
                owner=user,
                name="inscriptions",
                original_filename="other.csv",
            )

    def test_deleting_dataset_cascades_to_columns_and_rows(self, dataset):
        DatasetColumn.objects.create(
            dataset=dataset, name_original="Email", name_normalized="email", position=0
        )
        DatasetRow.objects.create(dataset=dataset, row_index=0, data={"email": "a@b.com"})

        dataset.delete()

        assert DatasetColumn.objects.count() == 0
        assert DatasetRow.objects.count() == 0


class TestDatasetColumn:
    def test_detected_type_defaults_to_unknown(self, dataset):
        column = DatasetColumn.objects.create(
            dataset=dataset, name_original="Campus", name_normalized="campus", position=0
        )
        assert column.detected_type == DatasetColumn.ColumnType.UNKNOWN
        assert column.nullable is True

    def test_str_returns_normalized_name(self, dataset):
        column = DatasetColumn.objects.create(
            dataset=dataset, name_original="E-mail", name_normalized="e_mail", position=0
        )
        assert str(column) == "e_mail"

    def test_position_and_dataset_are_unique_together(self, dataset):
        DatasetColumn.objects.create(
            dataset=dataset, name_original="A", name_normalized="a", position=0
        )
        with pytest.raises(IntegrityError):
            DatasetColumn.objects.create(
                dataset=dataset, name_original="B", name_normalized="b", position=0
            )


class TestDatasetRow:
    def test_stores_arbitrary_json_payload(self, dataset):
        row = DatasetRow.objects.create(
            dataset=dataset,
            row_index=0,
            data={"name": "Modeste", "campus": "Paris", "promo": "2027"},
        )
        row.refresh_from_db()
        assert row.data == {"name": "Modeste", "campus": "Paris", "promo": "2027"}

    def test_row_index_and_dataset_are_unique_together(self, dataset):
        DatasetRow.objects.create(dataset=dataset, row_index=0, data={})
        with pytest.raises(IntegrityError):
            DatasetRow.objects.create(dataset=dataset, row_index=0, data={})

    def test_default_ordering_is_by_row_index(self, dataset):
        DatasetRow.objects.create(dataset=dataset, row_index=2, data={})
        DatasetRow.objects.create(dataset=dataset, row_index=0, data={})
        DatasetRow.objects.create(dataset=dataset, row_index=1, data={})

        indexes = list(
            DatasetRow.objects.filter(dataset=dataset).values_list("row_index", flat=True)
        )
        assert indexes == [0, 1, 2]


class TestDatasetApiKey:
    def test_key_hash_is_stored_not_raw_key(self, dataset):
        api_key = DatasetApiKey.objects.create(
            dataset=dataset, name="n8n integration", key_hash="a" * 64
        )
        assert api_key.key_hash == "a" * 64
        assert api_key.is_active is True
        assert api_key.last_used_at is None

    def test_str_returns_name(self, dataset):
        api_key = DatasetApiKey.objects.create(dataset=dataset, name="zapier", key_hash="b" * 64)
        assert str(api_key) == "zapier"
