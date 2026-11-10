"""Datenmodell: Benutzerbasis, Anbieter, Objekte, freigegebene Staende, Fotos."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q


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
    # Angeforderte neue Adresse; ersetzt die verifizierte Adresse erst nach Bestaetigung.
    pending_email = models.EmailField("Angeforderte neue E-Mail", blank=True, max_length=254)
    pending_email_requested_at = models.DateTimeField(null=True, blank=True)

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


# ---------------------------------------------------------------------------
# Etappe 1: Anbieter, Objekt (Arbeitsfassung), freigegebener Snapshot, Fotos
# ---------------------------------------------------------------------------

_PUBLIC_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_public_id() -> str:
    """Nicht fortlaufende oeffentliche ID; kein Zugriffsnachweis."""
    return "".join(secrets.choice(_PUBLIC_ID_ALPHABET) for _ in range(12))


def _coordinate_constraints(prefix: str, lat: str, lon: str) -> list:
    return [
        models.CheckConstraint(
            condition=Q(**{f"{lat}__isnull": True}) | Q(**{f"{lat}__gte": -90, f"{lat}__lte": 90}),
            name=f"{prefix}_{lat}_range",
        ),
        models.CheckConstraint(
            condition=Q(**{f"{lon}__isnull": True}) | Q(**{f"{lon}__gte": -180, f"{lon}__lte": 180}),
            name=f"{prefix}_{lon}_range",
        ),
        models.CheckConstraint(
            condition=(Q(**{f"{lat}__isnull": True}) & Q(**{f"{lon}__isnull": True}))
            | (Q(**{f"{lat}__isnull": False}) & Q(**{f"{lon}__isnull": False})),
            name=f"{prefix}_{lat}_{lon}_pair",
        ),
    ]


def _coord_field(label: str):
    return models.DecimalField(label, max_digits=9, decimal_places=6, null=True, blank=True)


# Symbole fuer Kategorien (einfarbige Strichgrafiken; Pfade in static/js/explore.js).
CATEGORY_ICONS = [
    ("etikett", "Etikett (Standard)"), ("sprechblase", "Sprechblase"), ("personen", "Personen"),
    ("haus", "Haus"), ("herz", "Herz"), ("buch", "Buch"), ("sonne", "Sonne"), ("blatt", "Blatt"),
    ("mond", "Mond"), ("wlan_aus", "WLAN aus"), ("berg", "Berg"), ("wasser", "Wasser"),
]


class Category(models.Model):
    """Kategorie, durch die Administration pflegbar.

    Objekte speichern nur die Kennung (slug). Die Kennung bleibt nach dem Anlegen fest;
    Bezeichnung, Symbol und Reihenfolge sind aenderbar. Statt Loeschen: deaktivieren
    (inaktive Kategorien sind weder waehlbar noch fuer Besucher sichtbar).
    """

    slug = models.SlugField("Kennung", max_length=32, unique=True,
                            help_text="Kleinbuchstaben, Ziffern, Bindestrich. Nach dem Anlegen nicht mehr aenderbar.")
    label = models.CharField("Bezeichnung", max_length=60)
    icon = models.CharField("Symbol", max_length=24, choices=CATEGORY_ICONS, default="etikett")
    sort_order = models.PositiveSmallIntegerField("Reihenfolge", default=100)
    is_active = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Kategorie"
        verbose_name_plural = "Kategorien"
        ordering = ["sort_order", "label", "id"]

    def __str__(self) -> str:
        return self.label


def normalize_categories(values) -> list[str]:
    """Nur aktive Kategorien, ohne Duplikate, in der gepflegten Reihenfolge."""
    chosen = {v for v in (values or []) if isinstance(v, str)}
    if not chosen:
        return []
    return list(Category.objects.filter(is_active=True, slug__in=chosen).values_list("slug", flat=True))


class Provider(models.Model):
    """Anbieter: private Stammdaten getrennt von freigabefaehigen Kontaktfeldern."""

    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="managed_providers",
    )
    # Privat, nie in Gast-/Mitgliedsprojektionen.
    name = models.CharField("Interner Name", max_length=200)
    correspondence_address = models.TextField("Korrespondenzadresse (privat)", blank=True)
    internal_notes = models.TextField("Interne Notizen", blank=True)
    # Freigabefaehige Kontaktvorlage; wird nie automatisch in Objekte kopiert.
    contact_name = models.CharField("Kontaktname", max_length=200, blank=True)
    contact_organisation = models.CharField("Organisation", max_length=200, blank=True)
    contact_email = models.EmailField("Kontakt-E-Mail", blank=True)
    contact_phone = models.CharField("Kontakttelefon", max_length=40, blank=True)
    contact_website = models.URLField("Website", blank=True)
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Anbieter"
        verbose_name_plural = "Anbieter"

    def __str__(self) -> str:
        return self.name


class Location(models.Model):
    """Objekt-Arbeitsfassung. Sichtbar fuer Besucher ist nur PublishedLocation."""

    class ReviewState(models.TextChoices):
        DRAFT = "draft", "Entwurf"
        SUBMITTED = "submitted", "Eingereicht"
        APPROVED = "approved", "Freigegeben"
        REJECTED = "rejected", "Zurueckgewiesen"

    public_id = models.CharField(max_length=16, unique=True, default=new_public_id, editable=False)
    external_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="managed_locations",
    )
    provider = models.ForeignKey(
        Provider, null=True, blank=True, on_delete=models.PROTECT, related_name="locations"
    )

    title = models.CharField("Titel", max_length=160, blank=True)
    categories = models.JSONField("Kategorien", default=list, blank=True)
    preview_text = models.CharField("Vorschautext", max_length=400, blank=True)
    long_description = models.TextField("Langbeschreibung (Mitglieder)", blank=True)
    details = models.TextField("Detailangaben (Mitglieder)", blank=True)

    # Genaue Lage: intern; fuer Mitglieder nur bei expliziter Freigabe.
    exact_address = models.CharField("Genaue Adresse (intern)", max_length=300, blank=True)
    exact_lat = _coord_field("Breite genau")
    exact_lon = _coord_field("Laenge genau")
    member_exact_location_released = models.BooleanField(
        "Genaue Lage fuer Mitglieder freigegeben", default=False
    )
    # Grobe, stabile Vorschaulage und Ortstext fuer Gaeste.
    preview_lat = _coord_field("Breite Vorschau")
    preview_lon = _coord_field("Laenge Vorschau")
    place_text = models.CharField("Ort/Region (Vorschau)", max_length=120, blank=True)

    # Objektbezogene, fuer Mitglieder freigabefaehige Kontaktangaben.
    contact_name = models.CharField("Kontaktname", max_length=200, blank=True)
    contact_organisation = models.CharField("Organisation", max_length=200, blank=True)
    contact_email = models.EmailField("Kontakt-E-Mail", blank=True)
    contact_phone = models.CharField("Kontakttelefon", max_length=40, blank=True)
    contact_website = models.URLField("Website", blank=True)
    contacts_released = models.BooleanField("Kontakte fuer Mitglieder freigegeben", default=False)
    # Einzelfreigaben fuer nicht angemeldete Besucher (Standard: aus; wirksam ab naechster Freigabe).
    guest_long_description_released = models.BooleanField("Langbeschreibung fuer Besucher freigegeben",
                                                           default=False)
    guest_details_released = models.BooleanField("Detailangaben fuer Besucher freigegeben", default=False)
    guest_contacts_released = models.BooleanField("Kontaktangaben fuer Besucher freigegeben", default=False)

    review_state = models.CharField(max_length=16, choices=ReviewState.choices, default=ReviewState.DRAFT)
    work_version = models.PositiveIntegerField(default=1)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_feedback = models.TextField("Rueckmeldung an Betreuer", blank=True)
    internal_notes = models.TextField("Interne Notizen", blank=True)
    editorial_notes = models.TextField("Redaktionsnotizen (nicht fuer Betreuer)", blank=True)
    # Betreuung hat seit der letzten Einreichung/Freigabe geaendert: Direktfreigabe gesperrt.
    owner_changes_pending = models.BooleanField(default=False, editable=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_demo = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Objekt"
        verbose_name_plural = "Objekte"
        constraints = [
            *_coordinate_constraints("loc", "exact_lat", "exact_lon"),
            *_coordinate_constraints("loc", "preview_lat", "preview_lon"),
        ]

    def __str__(self) -> str:
        return self.title or f"Objekt {self.public_id}"

    def save(self, *args, **kwargs):
        self.categories = normalize_categories(self.categories)
        super().save(*args, **kwargs)


class PublishedLocation(models.Model):
    """Redaktionell freigegebener Stand. Enthaelt nur Gast- und Mitgliedsfelder.

    Interne Felder (Notizen, interne Adresse ohne Freigabe, Anbieterstammdaten)
    werden nie hierher kopiert.
    """

    location = models.OneToOneField(Location, on_delete=models.CASCADE, related_name="published")
    public_id = models.CharField(max_length=16, unique=True)
    is_active = models.BooleanField(default=True)  # False nach Rueckzug
    source_work_version = models.PositiveIntegerField()
    disclosure_version = models.PositiveIntegerField(default=1)
    published_at = models.DateTimeField()
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    # GAST-Projektion
    title = models.CharField(max_length=160)
    categories = models.JSONField(default=list)
    preview_text = models.CharField(max_length=400)
    place_text = models.CharField(max_length=120)
    preview_lat = models.DecimalField(max_digits=9, decimal_places=6)
    preview_lon = models.DecimalField(max_digits=9, decimal_places=6)

    # MITGLIED-Projektion
    long_description = models.TextField()
    details = models.TextField(blank=True)
    member_address = models.CharField(max_length=300, blank=True)
    member_lat = _coord_field("Breite Mitglied")
    member_lon = _coord_field("Laenge Mitglied")
    contact_name = models.CharField(max_length=200, blank=True)
    contact_organisation = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    contact_website = models.URLField(blank=True)
    # Einzelfreigaben fuer nicht angemeldete Besucher (eingefroren mit der Freigabe).
    guest_long_description_released = models.BooleanField(default=False)
    guest_details_released = models.BooleanField(default=False)
    guest_contacts_released = models.BooleanField(default=False)
    is_demo = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Freigegebener Stand"
        verbose_name_plural = "Freigegebene Staende"
        indexes = [models.Index(fields=["is_active", "preview_lat", "preview_lon"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(preview_lat__gte=-90, preview_lat__lte=90, preview_lon__gte=-180, preview_lon__lte=180),
                name="pub_preview_range",
            ),
            *_coordinate_constraints("pub", "member_lat", "member_lon"),
        ]


class Photo(models.Model):
    """Foto der Arbeitsfassung. Dateien privat; Verarbeitung/Auslieferung ab Etappe 3."""

    class Visibility(models.TextChoices):
        GUEST_PREVIEW = "guest_preview", "Gastvorschau"
        MEMBER_DETAIL = "member_detail", "Mitgliederdetail"
        INTERNAL = "internal", "Intern"

    public_id = models.CharField(max_length=16, unique=True, default=new_public_id, editable=False)
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="photos")
    storage_key = models.CharField(max_length=64, unique=True)  # serverseitig vergeben
    # Bildbeschreibung: in der Galerie angezeigt und zugleich Alternativtext (Screenreader).
    alt_text = models.CharField("Bildbeschreibung", max_length=200, blank=True,
                                help_text="Wird in der Galerie angezeigt und als Alternativtext verwendet.")
    sort_order = models.PositiveSmallIntegerField(default=0)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.INTERNAL)
    # Bestaetigung beim Hochladen: selbst erstellt oder Nutzungsrechte (Copyright) geklaert.
    rights_confirmed_at = models.DateTimeField("Bildrechte bestaetigt am", null=True, blank=True, editable=False)
    is_withdrawn = models.BooleanField(default=False)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]


class PublishedPhoto(models.Model):
    """Im freigegebenen Stand eingefrorene Fotofreigabe (Sichtbarkeit, Alttext, Reihenfolge)."""

    published = models.ForeignKey(PublishedLocation, on_delete=models.CASCADE, related_name="photos")
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="+")
    visibility = models.CharField(max_length=16, choices=Photo.Visibility.choices)
    alt_text = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["published", "photo"], name="pubphoto_unique"),
            models.CheckConstraint(
                condition=~Q(visibility="internal"), name="pubphoto_not_internal"
            ),
        ]


class AuditEvent(models.Model):
    """Nachvollziehbarkeit (Abschnitt 7). Keine Kontaktdaten, Codes oder Token-URLs."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=32)
    target_id = models.CharField(max_length=32)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Protokolleintrag"
        verbose_name_plural = "Protokoll"
        indexes = [models.Index(fields=["target_type", "target_id"])]


# ---------------------------------------------------------------------------
# Etappe 2: Atomare Limits und an die Sitzung gebundene Login-Challenges
# ---------------------------------------------------------------------------


class RateBucket(models.Model):
    """Sperrzeile je (Bereich, pseudonymisierter Schluessel) fuer atomare Limits."""

    scope = models.CharField(max_length=40)
    key = models.CharField(max_length=64)  # HMAC, keine Klartext-IP/-Adresse
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope", "key"], name="ratebucket_unique")]


class RateEvent(models.Model):
    """Einzelereignis fuer rollende Zeitfenster. Bereinigung nach 48 h."""

    bucket = models.ForeignKey(RateBucket, on_delete=models.CASCADE, related_name="events")
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [models.Index(fields=["bucket", "created_at"])]


class LoginChallenge(models.Model):
    """Bindet einen versendeten E-Mail-Code an Konto, Zweck und Browsersitzung."""

    class State(models.TextChoices):
        ACTIVE = "active", "Aktiv"
        SUPERSEDED = "superseded", "Ersetzt"
        CONSUMED = "consumed", "Verbraucht"
        FAILED = "failed", "Fehlgeschlagen"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    purpose = models.CharField(max_length=16)
    nonce_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=12, choices=State.choices, default=State.ACTIVE)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["user", "state"])]


# ---------------------------------------------------------------------------
# Etappe 3: Freigabebuchung fuer Mitgliedsdetails
# ---------------------------------------------------------------------------


class DisclosureGrant(models.Model):
    """Kontogebundene Buchung einer Objektfassung (Konto, Objekt, disclosure_version).

    Kein oeffentlicher Link. Gueltig 24 h ab erster Buchung; Wiederabruf verlaengert
    nicht. Mitgliedschaft, MFA und Objektstatus werden bei jedem Zugriff erneut geprueft.
    Enthaelt keine Kontakte oder Geheimnisse; Bereinigung binnen 48 h nach Ablauf.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    published = models.ForeignKey(PublishedLocation, on_delete=models.CASCADE, related_name="+")
    disclosure_version = models.PositiveIntegerField()
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "published", "disclosure_version"], name="grant_unique"),
        ]
        indexes = [models.Index(fields=["user", "created_at"])]


# ---------------------------------------------------------------------------
# Etappe 5: swisstopo-Gateway (globale Provider-Leases, Cache-Stand)
# ---------------------------------------------------------------------------


class ProviderSlot(models.Model):
    """Gleichzeitige ausgehende Providerabrufe: feste Anzahl Slots mit ablaufender Lease."""

    kind = models.CharField(max_length=16)
    slot = models.PositiveSmallIntegerField()
    holder = models.CharField(max_length=32, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "slot"], name="providerslot_unique")]


class TileFetchLock(models.Model):
    """Zusammenfassung paralleler Cachemisses derselben Kachel (ablaufende Lease)."""

    key = models.CharField(max_length=64, unique=True)
    holder = models.CharField(max_length=32)
    expires_at = models.DateTimeField()


class TileCacheState(models.Model):
    """Ein Datensatz je Layer: Cachegeneration und zuletzt bekannter Providerstand."""

    layer = models.CharField(max_length=80, unique=True)
    generation = models.PositiveIntegerField(default=1)
    provider_cache_update = models.CharField(max_length=40, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)


# ---------------------------------------------------------------------------
# Etappe 6: Initialkonten (ausdruecklicher Bootstrap)
# ---------------------------------------------------------------------------


class InitialAccount(models.Model):
    """Durch provision_initial_accounts angelegte Identitaet mit geschuetzt hinterlegter Zielrolle.

    Die Rolle wird erst nach Aktivierung (Passwort, E-Mail) UND eingerichtetem zweiten
    Schritt vergeben; sie stammt nie aus Token, Registrierung oder Mailadresse.
    """

    class Role(models.TextChoices):
        VISITOR = "visitor", "Besucher"
        OBJECT_MANAGER = "object_manager", "Objektbetreuung"
        EDITOR = "editor", "Redaktion"
        ADMIN = "admin", "Administration"

    class State(models.TextChoices):
        PROVISIONED = "provisioned", "Vorbereitet"
        INVITED = "invited", "Eingeladen"
        ACTIVATED = "activated", "Aktiviert, zweiter Schritt ausstehend"
        COMPLETE = "complete", "Abgeschlossen"
        CONFLICT = "conflict", "Konflikt"
        REMOVED = "removed", "Konto entfernt"

    email = models.EmailField(unique=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="initial_account")
    target_role = models.CharField(max_length=16, choices=Role.choices)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PROVISIONED)
    environment = models.CharField(max_length=20)
    invitation_generation = models.PositiveIntegerField(default=0)
    provisioned_at = models.DateTimeField()
    invited_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Initialkonto"
        verbose_name_plural = "Initialkonten"


class LocationNotice(models.Model):
    """Mitteilung an die Betreuung ueber redaktionelle Anpassungen und Entscheide.

    Sichtbar im Betreuerportal und in der Verwaltung; Entscheide zusaetzlich per Mail.
    Enthaelt nur Bereichsnamen, keine Inhalte und keine Redaktionsnotizen.
    """

    class Kind(models.TextChoices):
        PUBLISHED = "published", "Eingereichte Fassung freigegeben"
        DIRECT_PUBLISHED = "direct_published", "Von der Redaktion angepasst und freigegeben"
        REJECTED = "rejected", "Eingereichte Fassung zurueckgewiesen"
        WITHDRAWN = "withdrawn", "Veroeffentlichung durch die Redaktion zurueckgezogen"
        PHOTO_REMOVED = "photo_removed", "Veroeffentlichtes Foto durch die Redaktion entfernt"
        EDITOR_EDIT = "editor_edit", "Arbeitsfassung durch die Redaktion bearbeitet"

    class MailStatus(models.TextChoices):
        NONE = "none", "keine Mail vorgesehen"
        SENT = "sent", "Mail gesendet"
        FAILED = "failed", "Mail nicht zugestellt"
        BUDGET = "budget", "Mail nicht gesendet (Budget)"
        NO_RECIPIENT = "no_recipient", "keine bestaetigte Betreuungsadresse"

    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="notices")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    sections = models.CharField(max_length=300, blank=True)
    feedback = models.TextField(blank=True)
    work_version = models.PositiveIntegerField(default=0)
    mail_status = models.CharField(max_length=16, choices=MailStatus.choices, default=MailStatus.NONE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mitteilung an Betreuung"
        verbose_name_plural = "Mitteilungen an Betreuung"
        ordering = ["-created_at", "-id"]
