from .permissions import can_publish, can_use_owner_portal, is_fully_verified


def navigation(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {
        "nav_portal": can_use_owner_portal(request),
        "nav_admin": is_fully_verified(request) and request.user.is_staff and (
            can_publish(request) or request.user.is_superuser),
    }
