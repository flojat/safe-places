"""Rollen (Abschnitt 3): Objektbetreuung, Redaktion, Administration.

Gruppen werden nie aus Migrationen, Signalen oder beim App-Start angelegt,
sondern nur ausdruecklich (Befehl ensure_role_groups / Rollenvergabe durch
einen Administrator). Registrierung verleiht keine Rolle.
"""

from __future__ import annotations

from django.contrib.auth.models import Group, Permission

OBJECT_MANAGER = "Objektbetreuung"
EDITOR = "Redaktion"

# Redaktion: fachliche Pruef-/Freigaberechte, keine Konten-/Gruppen-/OTP-Verwaltung.
EDITOR_PERMISSIONS = [
    ("locations", "view_location"), ("locations", "add_location"), ("locations", "change_location"),
    ("locations", "view_publishedlocation"),
    ("locations", "view_provider"), ("locations", "add_provider"), ("locations", "change_provider"),
    ("locations", "view_photo"), ("locations", "change_photo"),
    ("locations", "view_auditevent"),
]


def ensure_role_groups() -> dict[str, Group]:
    editor, _ = Group.objects.get_or_create(name=EDITOR)
    manager, _ = Group.objects.get_or_create(name=OBJECT_MANAGER)
    perms = [
        Permission.objects.get(content_type__app_label=app, codename=code)
        for app, code in EDITOR_PERMISSIONS
    ]
    editor.permissions.set(perms)
    manager.permissions.clear()  # Betreuung arbeitet nur im Portal, nicht im Admin
    return {EDITOR: editor, OBJECT_MANAGER: manager}


def has_group(user, name: str) -> bool:
    if not user.is_authenticated:
        return False
    cache = getattr(user, "_role_cache", None)
    if cache is None:
        cache = set(user.groups.values_list("name", flat=True))
        user._role_cache = cache
    return name in cache


def is_object_manager(user) -> bool:
    return has_group(user, OBJECT_MANAGER)


def is_editor(user) -> bool:
    return user.is_authenticated and user.is_staff and (user.is_superuser or has_group(user, EDITOR))


def is_admin(user) -> bool:
    return user.is_authenticated and user.is_staff and user.is_superuser


def default_object_manager():
    """Interner Standard-Betreuer aus DEFAULT_OBJECT_MANAGER_EMAIL, nur wenn aktiv und berechtigt."""
    from django.conf import settings
    from django.contrib.auth import get_user_model

    email = getattr(settings, "DEFAULT_OBJECT_MANAGER_EMAIL", "")
    if not email:
        return None
    User = get_user_model()
    return (
        User.objects.filter(email=email, is_active=True, state=User.State.ACTIVE, groups__name=OBJECT_MANAGER)
        .first()
    )


def is_default_object_manager(user) -> bool:
    default = default_object_manager()
    return default is not None and user is not None and user.pk == default.pk
