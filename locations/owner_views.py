"""Betreuerportal "Meine Objekte" (Abschnitt 7).

Zugang: vollstaendig verifizierte Sitzung + aktuelle Rolle Objektbetreuung.
Jede Route laedt das Objekt nur ueber die AKTUELLE Zuordnung (managed_by =
request.user); bekannte IDs oder Dateinamen sind kein Zugriffsnachweis.
Schreibaktionen sind begrenzt; Betreuer koennen zurueckziehen, aber nicht
selbst veroeffentlichen. Interne Arbeitszugriffe verbrauchen kein Besucherkontingent.
"""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from . import rate_limits, request_limits
from .forms import LocationWorkForm, NewLocationForm, PhotoEditForm, PhotoUploadForm
from .geo import CoordinateError
from .models import AuditEvent, Location, Photo
from .permissions import can_manage_location, can_use_owner_portal, fully_verified_required
from .services import photos, publishing


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    return response


def owner_portal_required(view):
    @wraps(view)
    @fully_verified_required
    def wrapper(request, *args, **kwargs):
        if not can_use_owner_portal(request):
            raise Http404()
        if request.method == "POST":
            try:
                rate_limits.reserve([
                    *rate_limits.configured("portal_write_account", str(request.user.pk)),
                    *rate_limits.configured("portal_write_ip", rate_limits.client_ip(request)),
                ])
            except rate_limits.RateLimited as exc:
                return request_limits.too_many(request, exc.retry_after)
            except rate_limits.RateLimiterUnavailable:
                return request_limits.unavailable(request)
        return _no_store(view(request, *args, **kwargs))

    return wrapper


def _own_location(request, public_id: str) -> Location:
    loc = Location.objects.filter(public_id=public_id, managed_by=request.user).first()
    if loc is None or not can_manage_location(request, loc):
        raise Http404()
    return loc


@owner_portal_required
@require_http_methods(["GET"])
def portal_list(request):
    locations = (
        Location.objects.filter(managed_by=request.user)
        .select_related("published").order_by("title", "id")
    )
    return render(request, "portal/list.html", {"locations": locations})


@owner_portal_required
@require_http_methods(["GET", "POST"])
def portal_create(request):
    form = NewLocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            rate_limits.reserve(rate_limits.configured("draft_create_account", str(request.user.pk)))
        except rate_limits.RateLimited as exc:
            return request_limits.too_many(request, exc.retry_after)
        loc = form.save(commit=False)
        loc.managed_by = request.user  # serverseitig, nie aus dem Formular
        loc.created_by = loc.updated_by = request.user
        loc.save()
        AuditEvent.objects.create(actor=request.user, action="create", target_type="location",
                                  target_id=loc.public_id)
        return redirect("portal:edit", public_id=loc.public_id)
    return render(request, "portal/create.html", {"form": form})


@owner_portal_required
@require_http_methods(["GET", "POST"])
def portal_edit(request, public_id: str):
    loc = _own_location(request, public_id)
    if request.method == "POST":
        form = LocationWorkForm(request.POST, instance=Location.objects.get(pk=loc.pk), user=request.user)
        if form.is_valid():
            try:
                publishing.save_work_version(loc.pk, request.user, form.cleaned_data["expected_version"],
                                             form.changes())
            except publishing.VersionConflict:
                messages.error(request, "Das Objekt wurde inzwischen geaendert. Bitte Eingaben pruefen und erneut speichern.")
                return redirect("portal:edit", public_id=public_id)
            messages.success(request, "Gespeichert. Aenderungen werden erst nach Einreichung und Pruefung sichtbar.")
            return redirect("portal:edit", public_id=public_id)
    else:
        form = LocationWorkForm(instance=loc, user=request.user, initial={"expected_version": loc.work_version})
    return render(request, "portal/edit.html", {
        "loc": loc, "form": form,
        "missing": publishing.missing_for_publication(loc),
        "photos": loc.photos.order_by("sort_order", "id"),
        "upload_form": PhotoUploadForm(),
        "published": getattr(loc, "published", None),
        # Nur an diese Betreuung gerichtete Mitteilungen; ohne Namen der Redaktion.
        "notices": list(loc.notices.filter(recipient=request.user)[:20]),
    })


@owner_portal_required
@require_http_methods(["GET"])
def portal_preview(request, public_id: str):
    """Vorschau der Arbeitsfassung je Stufe, ueber Verwaltungsrechte (keine Buchung)."""
    loc = _own_location(request, public_id)
    error = None
    values, photos = {}, []
    try:
        values, photos = publishing.snapshot_values(loc)
    except CoordinateError as exc:
        error = str(exc)
    return render(request, "portal/preview.html", {
        "loc": loc, "v": values, "error": error,
        "guest_photos": [p for p in photos if p.visibility == Photo.Visibility.GUEST_PREVIEW],
        "member_photos": photos,
    })


@owner_portal_required
@require_POST
def portal_submit(request, public_id: str):
    loc = _own_location(request, public_id)
    try:
        expected = int(request.POST.get("expected_version", "0"))
        missing = publishing.missing_for_publication(loc)
        if missing:
            messages.error(request, "Noch unvollstaendig: " + ", ".join(missing))
        else:
            publishing.submit(loc.pk, request.user, expected)
            messages.success(request, "Eingereicht. Die Redaktion prueft die Fassung.")
    except (ValueError, publishing.VersionConflict):
        messages.error(request, "Das Objekt wurde inzwischen geaendert. Bitte erneut pruefen.")
    return redirect("portal:edit", public_id=public_id)


@owner_portal_required
@require_POST
def portal_withdraw(request, public_id: str):
    loc = _own_location(request, public_id)
    publishing.withdraw(loc.pk, request.user)
    messages.success(request, "Das Objekt ist fuer neue Abrufe zurueckgezogen. Eine erneute Veroeffentlichung erfolgt durch die Redaktion.")
    return redirect("portal:edit", public_id=public_id)


@owner_portal_required
@require_POST
def portal_withdraw_contacts(request, public_id: str):
    loc = _own_location(request, public_id)
    publishing.withdraw_contacts(loc.pk, request.user)
    messages.success(request, "Kontaktangaben sind fuer neue Abrufe zurueckgezogen.")
    return redirect("portal:edit", public_id=public_id)


@owner_portal_required
@require_POST
def portal_photo_upload(request, public_id: str):
    loc = _own_location(request, public_id)
    form = PhotoUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Bitte Bild, Sichtbarkeit und Bestaetigung der Bildrechte angeben.")
        return redirect("portal:edit", public_id=public_id)
    if loc.photos.filter(is_withdrawn=False).count() >= settings.MAX_ACTIVE_PHOTOS_PER_LOCATION:
        messages.error(request, "Maximale Anzahl aktiver Bilder erreicht.")
        return redirect("portal:edit", public_id=public_id)
    upload = form.cleaned_data["image"]
    if upload.size > settings.IMAGE_MAX_UPLOAD_BYTES:
        messages.error(request, "Datei zu gross.")
        return redirect("portal:edit", public_id=public_id)
    try:
        rate_limits.reserve([
            *rate_limits.configured("upload_account", str(request.user.pk)),
            *rate_limits.configured("upload_ip", rate_limits.client_ip(request)),
        ])
    except rate_limits.RateLimited as exc:
        return request_limits.too_many(request, exc.retry_after)
    try:
        photos.add_photo(
            loc.pk, upload, alt_text=form.cleaned_data["alt_text"], visibility=form.cleaned_data["visibility"],
            actor=request.user, rights_confirmed=form.cleaned_data["rights_confirmed"],
            still_allowed=lambda locked: locked.managed_by_id == request.user.pk,  # Zuordnung entzogen?
        )
    except photos.PhotoRejected as exc:
        messages.error(request, str(exc))
        return redirect("portal:edit", public_id=public_id)
    except photos.AccessLost:
        raise Http404()
    messages.success(request, "Bild hochgeladen. Sichtbar erst nach Einreichung und Freigabe.")
    return redirect("portal:edit", public_id=public_id)


@owner_portal_required
@require_POST
def portal_photo_update(request, public_id: str, photo_id: str):
    loc = _own_location(request, public_id)
    photo = Photo.objects.filter(public_id=photo_id, location=loc).first()
    if photo is None:
        raise Http404()
    if request.POST.get("action") == "withdraw":
        publishing.withdraw_photo(photo.pk, request.user)  # sofort wirksam
        messages.success(request, "Bild zurueckgezogen.")
        return redirect("portal:edit", public_id=public_id)
    form = PhotoEditForm(request.POST)
    if form.is_valid():
        Photo.objects.filter(pk=photo.pk).update(**form.cleaned_data)
        publishing.mark_work_changed(loc.pk, request.user)
        AuditEvent.objects.create(actor=request.user, action="photo_edit", target_type="photo",
                                  target_id=photo.public_id)
        messages.success(request, "Bildangaben gespeichert (wirksam nach naechster Freigabe).")
    return redirect("portal:edit", public_id=public_id)

