from rest_framework.permissions import BasePermission


class IsOwner(BasePermission):
    """V1 scope is owner-only: no shared/public reads yet (see roadmap V2)."""

    def has_object_permission(self, request, view, obj):
        return obj.owner_id == request.user.id
