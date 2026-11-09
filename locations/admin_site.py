from two_factor.admin import AdminSiteOTPRequired

from .permissions import is_fully_verified


class SecureAdminSite(AdminSiteOTPRequired):
    """Admin nur fuer aktive, E-Mail-bestaetigte Staff-Konten mit OTP-Sitzung.

    Fachliche Rollenrechte (Redaktion vs. Administration) folgen in Etappe 4.
    """

    site_header = "Adresskarte – Verwaltung"
    site_title = "Adresskarte Verwaltung"

    def has_permission(self, request):
        return super().has_permission(request) and is_fully_verified(request)
