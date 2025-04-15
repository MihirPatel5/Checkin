from rest_framework.permissions import BasePermission, SAFE_METHODS

class IsAdminOrSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ['admin', 'superadmin']

class IsAgent(BasePermission):
    def has_permission(self, request, view):
        return request.user.role == 'agent'

class IsLandlord(BasePermission):
    def has_permission(self, request, view):
        return request.user.role == 'landlord'


class IsOwnerOrAdmin(BasePermission):
    """
    Allow landlords to update/delete only their own properties.
    Admins and SuperAdmins can do anything.
    """
    def has_object_permission(self, request, view, obj):
        if request.user.role in ['admin', 'superadmin']:
            return True
        return obj.owner == request.user
