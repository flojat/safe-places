"""Sichere Bildverarbeitung (Auftrag Abschnitt 9).

- Erlaubt: JPEG, PNG, WebP nach tatsaechlichem Inhalt (nicht Endung/MIME).
- Abgelehnt: SVG/HTML/GIF/Animationen/unbekannte Formate, defekte Dateien,
  > MAX_UPLOAD_BYTES, > MAX_PIXELS (inkl. Dekompressionsbomben).
- Ausrichtung (EXIF) wird angewendet, danach statische JPEG-Varianten neu
  kodiert; EXIF/GPS und sonstige Metadaten werden dabei nicht uebernommen.
- Entscheidung: Originale werden nach erfolgreicher Verarbeitung verworfen.
- Dateinamen/-pfade nur serverseitig aus einem zufaelligen storage_key.
- Varianten sind unveraenderlich (kein In-place-Ueberschreiben).
"""

from __future__ import annotations

import io
import os
import re
import secrets
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
VARIANTS = {"thumb": 400, "detail": 1600}
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


class ImageRejected(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedImage:
    storage_key: str
    width: int
    height: int


def _limits() -> tuple[int, int]:
    return settings.IMAGE_MAX_UPLOAD_BYTES, settings.IMAGE_MAX_PIXELS


def storage_dir(storage_key: str) -> Path:
    if not _KEY_RE.match(storage_key or ""):
        raise ValueError("ungueltiger storage_key")
    root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
    path = (root / storage_key[:2] / storage_key).resolve()
    if root not in path.parents:
        raise ValueError("Pfad ausserhalb des Medienverzeichnisses")
    return path


def variant_path(storage_key: str, variant: str) -> Path:
    if variant not in VARIANTS:
        raise ValueError("unerlaubte Variante")
    return storage_dir(storage_key) / f"{variant}.jpg"


def _decode(data: bytes) -> Image.Image:
    max_bytes, max_pixels = _limits()
    if not data:
        raise ImageRejected("Leere Datei")
    if len(data) > max_bytes:
        raise ImageRejected("Datei zu gross")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            probe = Image.open(io.BytesIO(data))
            fmt = probe.format
            width, height = probe.size
            if fmt not in ALLOWED_FORMATS:
                raise ImageRejected("Format nicht erlaubt")
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ImageRejected("Bild zu gross (Pixel)")
            if getattr(probe, "is_animated", False) or getattr(probe, "n_frames", 1) > 1:
                raise ImageRejected("Animierte Bilder sind nicht erlaubt")
            probe.verify()  # Strukturpruefung
            img = Image.open(io.BytesIO(data))  # nach verify neu oeffnen
            img.load()
        except ImageRejected:
            raise
        except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageRejected("Kein gueltiges Bild") from exc
        except (OSError, SyntaxError, ValueError) as exc:
            raise ImageRejected("Defekte Bilddatei") from exc
    if img.format not in ALLOWED_FORMATS:
        raise ImageRejected("Format nicht erlaubt")
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    return img.convert("RGB")


def process_upload(data: bytes) -> ProcessedImage:
    """Prueft, erzeugt Varianten atomar in einem neuen Verzeichnis, verwirft das Original."""
    img = _decode(data)
    storage_key = secrets.token_hex(16)
    final_dir = storage_dir(storage_key)
    tmp_dir = final_dir.with_name(final_dir.name + ".tmp")
    tmp_dir.mkdir(parents=True, mode=0o700)
    try:
        for name, edge in VARIANTS.items():
            variant = img.copy()
            variant.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            # Neu kodiert ohne exif/icc/sonstige Metadaten.
            variant.save(tmp_dir / f"{name}.jpg", format="JPEG", quality=85, optimize=True)
            os.chmod(tmp_dir / f"{name}.jpg", 0o600)
        os.rename(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return ProcessedImage(storage_key=storage_key, width=img.width, height=img.height)


def delete_files(storage_key: str) -> None:
    shutil.rmtree(storage_dir(storage_key), ignore_errors=True)
