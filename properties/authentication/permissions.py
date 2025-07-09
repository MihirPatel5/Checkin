from rest_framework import permissions


class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == "SuperAdmin"

class CanViewUser(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == "SuperAdmin":
            return True
        if user.role == "Admin":
            return obj.role != "SuperAdmin"
        if user.role == "Landlord":
            return obj == user or (obj.role == "Agent" and obj.created_by == user)
        if user.role == "Agent":
            return obj == user
        return False

class CanEditUser(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == "SuperAdmin":
            return True
        if user.role == "Admin":
            return obj.role not in ["Admin", "SuperAdmin"]
        if user.role == "Landlord":
            return obj == user or (obj.role == "Agent" and obj.created_by == user)
        if user.role == "Agent":
            return obj == user
        return False

class CanDeleteUser(CanEditUser):
    pass

class IsSuperOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and 
            request.user.role in ["SuperAdmin", "Admin"]
        )