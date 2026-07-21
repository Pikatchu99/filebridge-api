from apps.datasets.models import DatasetColumn
from apps.datasets.services.type_detection import detect_column_type, is_valid_for_type


class TestDetectColumnType:
    def test_detects_email(self):
        assert detect_column_type(["a@b.com", "c@d.com", ""]) == DatasetColumn.ColumnType.EMAIL

    def test_detects_number(self):
        assert detect_column_type(["1", "2.5", "3"]) == DatasetColumn.ColumnType.NUMBER

    def test_detects_boolean(self):
        assert detect_column_type(["true", "false", "TRUE"]) == DatasetColumn.ColumnType.BOOLEAN

    def test_detects_date(self):
        assert detect_column_type(["2027-01-01", "2027-02-15"]) == DatasetColumn.ColumnType.DATE

    def test_falls_back_to_string(self):
        assert detect_column_type(["Paris", "Lyon"]) == DatasetColumn.ColumnType.STRING

    def test_unknown_when_all_values_empty(self):
        assert detect_column_type(["", "", ""]) == DatasetColumn.ColumnType.UNKNOWN

    def test_badly_mixed_values_fall_back_to_string(self):
        assert detect_column_type(["1", "abc", "3"]) == DatasetColumn.ColumnType.STRING

    def test_a_small_minority_of_mismatches_does_not_break_detection(self):
        # 9/10 valid emails is still "an email column" — one typo shouldn't downgrade
        # the whole column to STRING (that's what the quality report is for).
        values = ["a@b.com"] * 9 + ["not-an-email"]
        assert detect_column_type(values) == DatasetColumn.ColumnType.EMAIL

    def test_a_bare_majority_is_not_enough(self):
        # 60% numbers isn't a strong enough signal — this should read as a string column.
        values = ["1", "2", "3", "abc", "def"]
        assert detect_column_type(values) == DatasetColumn.ColumnType.STRING


class TestIsValidForType:
    def test_valid_email_passes(self):
        assert is_valid_for_type("a@b.com", DatasetColumn.ColumnType.EMAIL) is True

    def test_invalid_email_fails(self):
        assert is_valid_for_type("not-an-email", DatasetColumn.ColumnType.EMAIL) is False

    def test_valid_number_passes(self):
        assert is_valid_for_type("42.5", DatasetColumn.ColumnType.NUMBER) is True

    def test_invalid_number_fails(self):
        assert is_valid_for_type("abc", DatasetColumn.ColumnType.NUMBER) is False

    def test_valid_date_passes(self):
        assert is_valid_for_type("2027-01-01", DatasetColumn.ColumnType.DATE) is True

    def test_invalid_date_fails(self):
        assert is_valid_for_type("not-a-date", DatasetColumn.ColumnType.DATE) is False

    def test_valid_boolean_passes(self):
        assert is_valid_for_type("true", DatasetColumn.ColumnType.BOOLEAN) is True

    def test_invalid_boolean_fails(self):
        assert is_valid_for_type("maybe", DatasetColumn.ColumnType.BOOLEAN) is False

    def test_string_type_accepts_anything(self):
        assert is_valid_for_type("literally anything !@#", DatasetColumn.ColumnType.STRING) is True

    def test_unknown_type_accepts_anything(self):
        assert is_valid_for_type("literally anything !@#", DatasetColumn.ColumnType.UNKNOWN) is True
