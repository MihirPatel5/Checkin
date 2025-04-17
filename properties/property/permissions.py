from rest_framework.permissions import BasePermission, SAFE_METHODS

class IsAdminOrSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.lower() in ['admin', 'superadmin']

class IsAgent(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.lower() == 'agent'

class IsLandlord(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.lower() == 'landlord'

class IsOwnerOrAdmin(BasePermission):
    """
    Allow landlords to update/delete only their own properties.
    Admins and SuperAdmins can do anything.
    """
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if request.user.role.lower() in ['admin', 'superadmin']:
            return True
        return obj.owner == request.user
    
class IsLandlordOrAdminOrSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.role in ['Landlord', 'Admin', 'SuperAdmin']
        )
