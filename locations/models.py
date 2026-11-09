"""Datenmodell. Etappe 0/1: Benutzerbasis (vor der ersten Migration festgelegt)."""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


def normalize_email_address(value: str) -> str:
    """Festgelegte Normalisierung (docs/AUTHENTICATION.md).

    Leerraum entfernen und die gesamte Adresse in Kleinbuchstaben. Keine
    providerspezifische Kanonisierung von Plus- oder Punkt-Aliasen.
    """
    return (value or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("E-Mail-Adresse erforderlich")
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        user = self.model(email=normalize_email_address(email), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, *args, **kwargs):
        # Administratoren entstehen nur ueber provision_initial_accounts oder
        # die Rollenvergabe durch einen aktiven Administrator (Auftrag 11.3).
        raise PermissionError(
            "createsuperuser ist deaktiviert; siehe docs/INITIAL_ACCOUNTS.md."
        )


class User(AbstractUser):
    class State(models.TextChoices):
        PENDING_EMAIL = "pending_email", "E-Mail unbestaetigt"
        PENDING_ACTIVATION = "pending_activation", "Aktivierung ausstehend"
        ACTIVE = "active", "Aktiv"
        SUSPENDED = "suspended", "Gesperrt"

    class Origin(models.TextChoices):
        SELF_REGISTRATION = "self_registration", "Selbstregistrierung"
        BOOTSTRAP = "bootstrap", "Initialkonto"
        ADMIN = "admin", "Durch Administration"

    username = None
    email = models.EmailField("E-Mail", unique=True, max_length=254)
    email_verified_at = models.DateTimeField("E-Mail bestaetigt am", null=True, blank=True)
    state = models.CharField(
        "Kontozustand", max_length=24, choices=State.choices, default=State.PENDING_EMAIL
    )
    origin = models.CharField(
        "Herkunft", max_length=24, choices=Origin.choices, default=Origin.SELF_REGISTRATION
    )
    # Erhoehung invalidiert bestehende Sitzungen, Login-Challenges und Tokens.
    security_generation = models.PositiveIntegerField(default=0)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = "Benutzer"
        verbose_name_plural = "Benutzer"

    def save(self, *args, **kwargs):
        self.email = normalize_email_address(self.email)
        super().save(*args, **kwargs)

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None
