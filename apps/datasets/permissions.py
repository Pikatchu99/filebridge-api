from rest_framework.permissions import BasePermission

from apps.datasets.models import DatasetApiKey


class IsOwner(BasePermission):
    """Full account-holder access: session/basic-auth user matching the dataset's owner."""

    def has_object_permission(self, request, view, obj):
        return bool(
            request.user and request.user.is_authenticated and obj.owner_id == request.user.id
        )


class HasDatasetReadAccess(BasePermission):
    """Read-only actions (schema/rows/row_detail/export) are reachable three ways:
    the owner (session/basic auth), a DatasetApiKey scoped to that exact dataset, or
    anyone at all if the dataset has been marked public. The object-level check below
    covers all three; view-level access is left open since whether a dataset is public
    can only be known once it's fetched (get_queryset() also scopes this — see
    DatasetViewSet.get_queryset).
    """

    def has_permission(self, request, view):
        return True

    def has_object_permission(self, request, view, obj):
        if obj.is_public:
            return True
        if request.user and request.user.is_authenticated:
            return obj.owner_id == request.user.id
        api_key = request.auth
        return isinstance(api_key, DatasetApiKey) and api_key.dataset_id == obj.id
