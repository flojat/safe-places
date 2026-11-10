"""Redaktion und Administration im Django Admin (Abschnitt 7).

- Zugang nur mit vollstaendig verifizierter Sitzung (SecureAdminSite).
- Redaktion: fachliche Modelle; Freigabe/Zurueckweisung/Rueckzug einzeln mit
  Versionsvergleich; keine Massenaktionen, kein Loeschen.
- Administration (Superuser): zusaetzlich Konten, Rollen, Sperren. Keine Anzeige
  von Passwort-Hashes oder OTP-Geheimnissen; Rollenaenderungen verlangen eine
  frische Bestaetigung (Passwort + Faktor, 5 min) und werden protokolliert.
"""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import F
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from . import reauth
from .forms import PhotoUploadForm, categories_field, validate_guest_releases
from .models import AuditEvent, Category, Location, LocationNotice, Photo, Provider, PublishedLocation, PublishedPhoto
from .permissions import can_administer, can_publish
from . import rate_limits, request_limits
from .roles import EDITOR, OBJECT_MANAGER, default_object_manager, ensure_role_groups
from .services import owner_notices, photos, publishing

User = get_user_model()

COMPARE_FIELDS = [
    ("title", "Titel"), ("categories", "Kategorien"), ("preview_text", "Vorschautext"),
    ("place_text", "Ort/Region"), ("preview_lat", "Vorschau Breite"), ("preview_lon", "Vorschau Laenge"),
    ("long_description", "Langbeschreibung"), ("details", "Details"),
    ("member_address", "Mitglieder: Adresse"), ("member_lat", "Mitglieder: Breite"),
    ("member_lon", "Mitglieder: Laenge"), ("contact_name", "Kontakt Name"),
    ("contact_organisation", "Organisation"), ("contact_email", "Kontakt E-Mail"),
    ("contact_phone", "Telefon"), ("contact_website", "Website"),
    ("guest_long_description_released", "Besucher: Langbeschreibung"),
    ("guest_details_released", "Besucher: Details"), ("guest_contacts_released", "Besucher: Kontakt"),
]


# --- Sicherheitsrelevante Fremdregistrierungen entfernen ------------------------

def _unregister_security_admins():
    from django_otp.plugins.otp_email.models import EmailDevice
    from django_otp.plugins.otp_static.models import StaticDevice
    from django_otp.plugins.otp_totp.models import TOTPDevice

    for model in (TOTPDevice, EmailDevice, StaticDevice, Group):
        if admin.site.is_registered(model):
            admin.site.unregister(model)
    try:
        from axes.models import AccessAttempt, AccessFailureLog, AccessLog

        for model in (AccessAttempt, AccessLog, AccessFailureLog):
            if admin.site.is_registered(model):
                admin.site.unregister(model)
    except ImportError:  # pragma: no cover
        pass


_unregister_security_admins()


class NoBulkDeleteMixin:
    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def has_delete_permission(self, request, obj=None):
        return False


# --- Objekte ----------------------------------------------------------------------

class CategoryListFilter(admin.SimpleListFilter):
    title = "Kategorie"
    parameter_name = "kategorie"

    def lookups(self, request, model_admin):
        return [(c.slug, c.label) for c in Category.objects.all()]

    def queryset(self, request, queryset):
        value = self.value()
        if value and Category.objects.filter(slug=value).exists():
            return queryset.filter(categories__contains=[value])
        return queryset


class ManagerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.email


class LocationAdminForm(forms.ModelForm):
    expected_version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    categories = categories_field()

    class Meta:
        model = Location
        exclude = ["review_state", "work_version", "submitted_at", "reviewed_at",
                   "created_by", "updated_by", "public_id"]

    def clean(self):
        data = super().clean()
        if self.instance.pk:
            current = Location.objects.filter(pk=self.instance.pk).values_list("work_version", flat=True).first()
            if data.get("expected_version") != current:
                raise forms.ValidationError(
                    "Die Arbeitsfassung wurde inzwischen geaendert. Bitte neu laden und erneut bearbeiten.")
        validate_guest_releases(self, data)
        return data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["expected_version"].initial = self.instance.work_version
        # Zuweisung nur an bereits berechtigte, aktive Betreuer; erzeugt keine Rolle.
        self.fields["managed_by"] = ManagerChoiceField(
            queryset=User.objects.filter(groups__name=OBJECT_MANAGER, is_active=True,
                                         state=User.State.ACTIVE).order_by("email"),
            required=True, label="Zustaendige Betreuung",
            help_text="Pflicht. Fuer redaktionell erfasste Objekte den internen Standard-Betreuer waehlen.",
        )
        if not self.instance.pk:
            default = default_object_manager()
            if default is not None:
                self.fields["managed_by"].initial = default.pk


class PhotoInline(admin.TabularInline):
    model = Photo
    fields = ["preview", "alt_text", "visibility", "sort_order", "is_withdrawn", "rights_confirmed_at"]
    # Rueckzug nur ueber "Fotos verwalten" bzw. Portal; kein stilles Wiedereinblenden.
    readonly_fields = ["preview", "is_withdrawn", "rights_confirmed_at"]
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False  # Uploads nur ueber geprueften Upload-Weg (Portal)

    @admin.display(description="Bild")
    def preview(self, obj):
        url = reverse("locations:photo", args=[obj.public_id, "vorschau"])
        return format_html('<img src="{}" width="120" alt="">', url)


@admin.register(Location)
class LocationAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    form = LocationAdminForm
    list_display = ["title", "review_state", "work_version", "published_state", "managed_by", "is_demo"]
    list_filter = ["review_state", CategoryListFilter, "is_demo"]
    search_fields = ["title", "place_text", "public_id"]
    readonly_fields = ["public_id", "review_state", "work_version", "submitted_at", "reviewed_at"]
    inlines = [PhotoInline]
    actions = None
    change_form_template = "admin/locations/location/change_form.html"

    @admin.display(description="Veroeffentlicht")
    def published_state(self, obj):
        pub = getattr(obj, "published", None)
        if pub is None:
            return "nein"
        return f"v{pub.source_work_version}" if pub.is_active else "zurueckgezogen"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("published", "managed_by")

    def save_model(self, request, obj, form, change):
        """Redaktionelle Bearbeitung = neue Arbeitsversion; veroeffentlichter Stand bleibt."""
        if change:
            old = Location.objects.select_for_update().get(pk=obj.pk)
            if old.work_version != form.cleaned_data.get("expected_version"):
                raise publishing.VersionConflict("Arbeitsfassung zwischenzeitlich geaendert")
            obj.work_version = old.work_version + 1
            if old.review_state == Location.ReviewState.APPROVED:
                obj.review_state = Location.ReviewState.DRAFT
            if old.managed_by_id != obj.managed_by_id:
                AuditEvent.objects.create(actor=request.user, action="assign", target_type="location",
                                          target_id=obj.public_id,
                                          detail=f"betreuung {old.managed_by_id}->{obj.managed_by_id}")
        else:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(actor=request.user, action="admin_edit", target_type="location",
                                  target_id=obj.public_id, detail=f"v{obj.work_version}")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change:
            return
        photos_changed = any(fs.has_changed() for fs in formsets)
        sections = owner_notices.editor_edit_sections(form.changed_data, photos_changed)
        if sections:
            owner_notices.record(form.instance, LocationNotice.Kind.EDITOR_EDIT, request.user, sections=sections)

    def get_urls(self):
        custom = [
            path("<int:pk>/vergleich/", self.admin_site.admin_view(self.compare_view), name="locations_location_compare"),
            path("<int:pk>/entscheid/", self.admin_site.admin_view(self.decide_view), name="locations_location_decide"),
            path("<int:pk>/fotos/", self.admin_site.admin_view(self.photos_view), name="locations_location_photos"),
        ]
        return custom + super().get_urls()

    def compare_view(self, request, pk: int):
        if not can_publish(request):
            raise Http404()
        loc = get_object_or_404(Location, pk=pk)
        pub = PublishedLocation.objects.filter(location=loc).first()
        try:
            work_values, work_photos = publishing.snapshot_values(loc)
            work_error = ""
        except Exception as exc:  # ungueltige Lage etc.
            work_values, work_photos, work_error = {}, [], str(exc)
        labels = {c.slug: c.label for c in Category.objects.all()}

        def show(field, value):
            if field == "categories":
                return ", ".join(labels.get(v, v) for v in (value or []))
            if isinstance(value, bool):
                return "ja" if value else "nein"
            return value

        rows = []
        for field, label in COMPARE_FIELDS:
            old = getattr(pub, field, "") if pub else ""
            new = work_values.get(field, "")
            rows.append((label, show(field, old), show(field, new), old != new))
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "loc": loc, "pub": pub,
            "rows": rows, "work_error": work_error, "missing": publishing.missing_for_publication(loc),
            "work_photos": work_photos,
            "pub_photos": list(pub.photos.select_related("photo")) if pub else [],
            "publish_blocker": publishing.editorial_publish_blocker(loc),
            "is_direct": loc.review_state != Location.ReviewState.SUBMITTED,
            "notices": list(loc.notices.select_related("actor")[:30]),
            "title": f"Fassungsvergleich: {loc}",
        }
        return TemplateResponse(request, "admin/locations/location/compare.html", context)

    def decide_view(self, request, pk: int):
        if request.method != "POST" or not can_publish(request):
            raise Http404()
        loc = get_object_or_404(Location, pk=pk)
        try:
            expected = int(request.POST.get("expected_version", "0"))
            action = request.POST.get("action")
            if action == "publish":
                _pub, kind, sections = publishing.editorial_publish(loc.pk, request.user, expected)
                loc.refresh_from_db()
                if kind == LocationNotice.Kind.PUBLISHED and loc.submitted_at and loc.notices.filter(
                        kind=LocationNotice.Kind.EDITOR_EDIT, created_at__gte=loc.submitted_at).exists():
                    kind = LocationNotice.Kind.DIRECT_PUBLISHED  # Einreichung redaktionell angepasst
                if loc.managed_by_id == request.user.pk:
                    AuditEvent.objects.create(actor=request.user, action="self_publish", target_type="location",
                                              target_id=loc.public_id)
                notice = owner_notices.record(loc, kind, request.user, sections=sections)
                messages.success(request, "Direkt freigegeben." if kind == LocationNotice.Kind.DIRECT_PUBLISHED
                                 else "Freigegeben.")
                self._report_mail(request, notice)
            elif action == "reject":
                feedback = request.POST.get("feedback", "")
                publishing.reject(loc.pk, request.user, expected, feedback)
                loc.refresh_from_db()
                notice = owner_notices.record(loc, LocationNotice.Kind.REJECTED, request.user, feedback=feedback)
                messages.success(request, "Zurueckgewiesen.")
                self._report_mail(request, notice)
            elif action == "withdraw":
                was_active = PublishedLocation.objects.filter(location=loc, is_active=True).exists()
                publishing.withdraw(loc.pk, request.user)
                messages.success(request, "Zurueckgezogen.")
                if was_active:
                    self._report_mail(request, owner_notices.record(loc, LocationNotice.Kind.WITHDRAWN, request.user))
            else:
                raise Http404()
        except ValueError:
            messages.error(request, "Ungueltige Anfrage.")
        except publishing.VersionConflict:
            messages.error(request, "Die Arbeitsfassung wurde seit dem Vergleich geaendert. Bitte erneut pruefen.")
        except publishing.PublishError as exc:
            messages.error(request, str(exc))
        return HttpResponseRedirect(reverse("admin:locations_location_compare", args=[loc.pk]))

    @staticmethod
    def _report_mail(request, notice):
        if notice.mail_status != LocationNotice.MailStatus.SENT:
            messages.warning(request, f"Betreuung im Portal informiert; Mail: {notice.get_mail_status_display()}.")

    def photos_view(self, request, pk: int):
        """Fotos der Arbeitsfassung durch Redaktion/Administration: hochladen und entfernen.

        Bildbeschreibung, Sichtbarkeit und Reihenfolge werden im Objektformular (Inline) bearbeitet.
        Wirksam fuer Besucher erst mit der naechsten Freigabe; das Entfernen eines bereits
        veroeffentlichten Bildes blendet es sofort aus (wie der Rueckzug durch die Betreuung).
        """
        if not can_publish(request):
            raise Http404()
        loc = get_object_or_404(Location, pk=pk)
        url = reverse("admin:locations_location_photos", args=[loc.pk])
        form = PhotoUploadForm()
        if request.method == "POST":
            action = request.POST.get("action")
            if action == "upload":
                form = PhotoUploadForm(request.POST, request.FILES)
                if form.is_valid():
                    try:
                        rate_limits.reserve([
                            *rate_limits.configured("upload_account", str(request.user.pk)),
                            *rate_limits.configured("upload_ip", rate_limits.client_ip(request)),
                        ])
                    except rate_limits.RateLimited as exc:
                        return request_limits.too_many(request, exc.retry_after)
                    except rate_limits.RateLimiterUnavailable:
                        return request_limits.unavailable(request)
                    try:
                        photos.add_photo(loc.pk, form.cleaned_data["image"], alt_text=form.cleaned_data["alt_text"],
                                         visibility=form.cleaned_data["visibility"], actor=request.user,
                                         rights_confirmed=form.cleaned_data["rights_confirmed"])
                    except photos.PhotoRejected as exc:
                        messages.error(request, str(exc))
                    else:
                        loc.refresh_from_db()
                        owner_notices.record(loc, LocationNotice.Kind.EDITOR_EDIT, request.user,
                                             sections=["Fotos (neues Bild)"])
                        messages.success(request, "Bild hochgeladen. Sichtbar erst nach der naechsten Freigabe.")
                        return HttpResponseRedirect(url)
                else:
                    messages.error(request, "Bitte Bild, Sichtbarkeit und Bestaetigung der Bildrechte angeben.")
            elif action == "remove":
                photo = Photo.objects.filter(public_id=request.POST.get("photo", ""), location=loc).first()
                if photo is None:
                    raise Http404()
                was_public = photos.remove_photo(photo.pk, request.user)
                loc.refresh_from_db()
                if was_public:
                    notice = owner_notices.record(loc, LocationNotice.Kind.PHOTO_REMOVED, request.user,
                                                  sections=["Fotos"])
                    messages.success(request, "Veroeffentlichtes Bild sofort ausgeblendet; Betreuung informiert.")
                    self._report_mail(request, notice)
                else:
                    owner_notices.record(loc, LocationNotice.Kind.EDITOR_EDIT, request.user,
                                         sections=["Fotos (Bild entfernt)"])
                    messages.success(request, "Bild entfernt.")
                return HttpResponseRedirect(url)
            else:
                raise Http404()
        published_ids = set(PublishedPhoto.objects.filter(published__location=loc, published__is_active=True)
                            .values_list("photo_id", flat=True))
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "loc": loc,
            "photos": [(p, p.id in published_ids) for p in loc.photos.order_by("sort_order", "id")],
            "form": form, "title": f"Fotos: {loc}",
            "max_photos": settings.MAX_ACTIVE_PHOTOS_PER_LOCATION,
        }
        return TemplateResponse(request, "admin/locations/location/photos.html", context)


@admin.register(Category)
class CategoryAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    """Kategorien pflegen: nur Administration. Kein Loeschen (deaktivieren genuegt)."""

    list_display = ["label", "slug", "icon", "sort_order", "is_active", "usage"]
    list_editable = ["sort_order", "is_active"]
    prepopulated_fields = {"slug": ("label",)}
    actions = None

    @admin.display(description="Objekte")
    def usage(self, obj):
        return Location.objects.filter(categories__contains=[obj.slug]).count()

    def get_readonly_fields(self, request, obj=None):
        return ["slug"] if obj is not None else []

    def get_prepopulated_fields(self, request, obj=None):
        return {} if obj is not None else self.prepopulated_fields

    def has_module_permission(self, request):
        return can_administer(request)

    def has_view_permission(self, request, obj=None):
        return can_administer(request)

    def has_change_permission(self, request, obj=None):
        return can_administer(request)

    def has_add_permission(self, request):
        return can_administer(request)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(actor=request.user, action="category_change" if change else "category_add",
                                  target_type="category", target_id=obj.slug[:32],
                                  detail=f"aktiv={obj.is_active}")


@admin.register(PublishedLocation)
class PublishedLocationAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    list_display = ["title", "is_active", "source_work_version", "disclosure_version", "published_at"]
    list_filter = ["is_active", CategoryListFilter]
    search_fields = ["title", "public_id"]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # nur ueber den Freigabeweg


@admin.register(Provider)
class ProviderAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    list_display = ["name", "managed_by", "is_demo"]
    search_fields = ["name"]
    actions = None

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "managed_by":
            kwargs["queryset"] = User.objects.filter(groups__name=OBJECT_MANAGER, is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(AuditEvent)
class AuditEventAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    list_display = ["created_at", "actor", "action", "target_type", "target_id", "detail"]
    list_filter = ["action", "target_type"]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# --- Konten (nur Administration) ----------------------------------------------------

ROLE_CHOICES = [(OBJECT_MANAGER, "Objektbetreuung"), (EDITOR, "Redaktion"), ("admin", "Administration")]


class UserAdminForm(forms.ModelForm):
    roles = forms.MultipleChoiceField(choices=ROLE_CHOICES, required=False,
                                      widget=forms.CheckboxSelectMultiple, label="Rollen")

    class Meta:
        model = User
        fields = ["state"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state"].choices = [
            (User.State.ACTIVE, "Aktiv"), (User.State.SUSPENDED, "Gesperrt"),
        ] if self.instance.state in (User.State.ACTIVE, User.State.SUSPENDED) else [
            (self.instance.state, self.instance.get_state_display()),
        ]
        current = set(self.instance.groups.values_list("name", flat=True)) if self.instance.pk else set()
        if self.instance.is_superuser:
            current.add("admin")
        self.initial["roles"] = sorted(current)


@admin.register(User)
class UserAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    form = UserAdminForm
    list_display = ["email", "state", "email_verified", "role_names", "last_login"]
    list_filter = ["state", "groups", "is_superuser"]
    search_fields = ["email"]
    readonly_fields = ["email", "email_verified_at", "origin", "date_joined", "last_login", "factor_summary"]
    fields = ["email", "email_verified_at", "origin", "date_joined", "last_login", "factor_summary", "state", "roles"]
    actions = None
    ordering = ["email"]

    def has_module_permission(self, request):
        return can_administer(request)

    def has_view_permission(self, request, obj=None):
        return can_administer(request)

    def has_change_permission(self, request, obj=None):
        return can_administer(request)

    def has_add_permission(self, request):
        return False  # Konten entstehen durch Registrierung oder Initialprovisionierung

    @admin.display(boolean=True, description="E-Mail bestaetigt")
    def email_verified(self, obj):
        return obj.email_verified_at is not None

    @admin.display(description="Rollen")
    def role_names(self, obj):
        names = list(obj.groups.values_list("name", flat=True))
        if obj.is_superuser:
            names.append("Administration")
        return ", ".join(names) or "Besucher"

    @admin.display(description="Zweiter Schritt")
    def factor_summary(self, obj):
        from django_otp import devices_for_user

        kinds = sorted({d.__class__.__name__.replace("Device", "") for d in devices_for_user(obj)})
        return ", ".join(kinds) or "nicht eingerichtet"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST" and not reauth.is_recent(request):
            messages.warning(request, "Bitte bestaetigen Sie diese sensible Aenderung erneut mit Passwort und zweitem Schritt.")
            return reauth.redirect_to_reauth(request, request.get_full_path())
        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        ensure_role_groups()
        old = User.objects.get(pk=obj.pk)
        old_roles = set(old.groups.values_list("name", flat=True)) | ({"admin"} if old.is_superuser else set())
        new_roles = set(form.cleaned_data["roles"])
        if obj.pk == request.user.pk and "admin" in old_roles and "admin" not in new_roles:
            messages.error(request, "Eigene Administrationsrolle kann nicht selbst entzogen werden.")
            new_roles.add("admin")
        is_superuser = "admin" in new_roles
        state_changed = old.state != obj.state
        updates = {
            "is_superuser": is_superuser,
            "is_staff": is_superuser or EDITOR in new_roles,
            "state": obj.state,
            "is_active": obj.state != User.State.SUSPENDED,
        }
        if state_changed or old_roles != new_roles:
            updates["security_generation"] = F("security_generation") + 1  # Sitzungen neu anmelden
        # Feldweise speichern: nie veraltete Sicherheitsfelder (Generation, Passwort, E-Mail) zurueckschreiben.
        User.objects.filter(pk=obj.pk).update(**updates)
        obj.refresh_from_db()
        obj.groups.set(Group.objects.filter(name__in=new_roles & {EDITOR, OBJECT_MANAGER}))
        for role in sorted(new_roles - old_roles):
            AuditEvent.objects.create(actor=request.user, action="role_grant", target_type="user",
                                      target_id=str(obj.pk), detail=role)
        for role in sorted(old_roles - new_roles):
            AuditEvent.objects.create(actor=request.user, action="role_revoke", target_type="user",
                                      target_id=str(obj.pk), detail=role)
        if state_changed:
            AuditEvent.objects.create(actor=request.user, action=f"account_{obj.state}", target_type="user",
                                      target_id=str(obj.pk))

    def save_related(self, request, form, formsets, change):
        pass  # Gruppen werden in save_model gesetzt

    def get_urls(self):
        custom = [path("<int:pk>/wiederherstellung/", self.admin_site.admin_view(self.recovery_view),
                       name="locations_user_recovery")]
        return custom + super().get_urls()

    def recovery_view(self, request, pk: int):
        from .bootstrap import admin_recovery

        if not can_administer(request):
            raise Http404()
        target = get_object_or_404(User, pk=pk)
        if request.method == "POST":
            if not reauth.is_recent(request):
                return reauth.redirect_to_reauth(request, request.path)
            if request.POST.get("identity_checked") != "on":
                messages.error(request, "Bitte die Identitaetspruefung bestaetigen.")
            else:
                try:
                    admin_recovery(target, request.user, request)
                    messages.success(request, "Konto in Neueinrichtung versetzt; Bestaetigungsmail an die gespeicherte Adresse.")
                    return HttpResponseRedirect(reverse("admin:locations_user_change", args=[pk]))
                except ValueError as exc:
                    messages.error(request, str(exc))
        context = {**self.admin_site.each_context(request), "opts": self.model._meta, "target": target,
                   "title": "Kontowiederherstellung"}
        return TemplateResponse(request, "admin/locations/user/recovery.html", context)
