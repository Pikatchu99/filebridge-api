from rest_framework.permissions import BasePermission

from apps.datasets.models import DatasetApiKey


class IsOwner(BasePermission):
    """Full account-holder access: session/basic-auth user matching the dataset's owner."""

    def has_object_permission(self, request, view, obj):
        return bool(
            request.user and request.user.is_authenticated and obj.owner_id == request.user.id
        )


class HasDatasetReadAccess(BasePermission):
    """Read-only actions (schema/rows/row_detail/export) additionally accept a DatasetApiKey
    scoped to that exact dataset, on top of the normal owner access.
    """

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        return isinstance(request.auth, DatasetApiKey)

    def has_object_permission(self, request, view, obj):
        if request.user and request.user.is_authenticated:
            return obj.owner_id == request.user.id
        api_key = request.auth
        return isinstance(api_key, DatasetApiKey) and api_key.dataset_id == obj.id
