from rest_framework.views import exception_handler


class DatasetIngestionError(Exception):
    """Base error raised while parsing an uploaded file into a Dataset."""


class EmptyFileError(DatasetIngestionError):
    """Raised when the uploaded file has no content at all."""


class NoHeaderError(DatasetIngestionError):
    """Raised when the uploaded file has no usable header row."""


class InvalidCsvError(DatasetIngestionError):
    """Raised when the uploaded file isn't valid CSV/text (bad encoding, malformed rows)."""


class UnknownColumnError(Exception):
    """Raised when a filter references a column that doesn't exist on the dataset."""


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and not isinstance(response.data, dict):
        response.data = {"detail": response.data}
    return response
