import operator
from functools import reduce

from django.db.models import Q

from apps.datasets.exceptions import UnknownColumnError
from apps.datasets.models import DatasetRow

RESERVED_QUERY_PARAMS = {"page", "search", "format"}


def build_row_queryset(dataset, query_params):
    """Filter a dataset's rows by column=value query params, plus a global `search`.

    Raises UnknownColumnError if a query param doesn't match any detected column,
    so the view can turn it into a clean 400 instead of silently ignoring it.
    """
    known_columns = set(dataset.columns.values_list("name_normalized", flat=True))
    queryset = DatasetRow.objects.filter(dataset=dataset)

    search = query_params.get("search")
    if search:
        if not known_columns:
            return queryset.none()
        lookups = [Q(**{f"data__{column}__icontains": search}) for column in known_columns]
        queryset = queryset.filter(reduce(operator.or_, lookups))

    for key, value in query_params.items():
        if key in known_columns:
            queryset = queryset.filter(**{f"data__{key}": value})
            continue
        if key in RESERVED_QUERY_PARAMS:
            continue
        raise UnknownColumnError(key)

    return queryset
