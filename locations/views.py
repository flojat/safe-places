from django.shortcuts import render


def home(request):
    # Platzhalter bis Etappe 1 (Gastkarte/-liste).
    return render(request, "locations/home.html")
