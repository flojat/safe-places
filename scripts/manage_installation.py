#!/usr/bin/env python3
"""Installations-, Pruef- und Deploymentroutine der Adresskarte (Auftrag 10.4).

Nur Python-Standardbibliothek, damit sie vor der Installation der Abhaengigkeiten
laeuft. Danach wird an die projekteigene virtuelle Umgebung delegiert.

Modi:
  preflight  Hosting-/Systemvoraussetzungen ermitteln (lesend; Netz nur mit --network-check)
  install    Projektumgebung planen (--plan, Standard) oder bereitstellen (--apply)
  doctor     Bestehende Installation pruefen (lesend, keine Reparatur)
  deploy     Freigegebenen Git-Stand als Release planen/vorbereiten/aktivieren
  rollback   Code-Rueckwechsel planen/anwenden (nur bei Schema-Kompatibilitaet)

Ergebnis je Pruefung: PASS/WARN/FAIL/UNKNOWN/BLOCKED mit stabilem Code und naechstem
Schritt. Exitcodes: 0 ok (inkl. WARN), 1 FAIL, 2 BLOCKED/UNKNOWN, 3 PREPARED_NOT_ACTIVATED,
64 Bedienfehler. Ausgaben und Berichte enthalten keine Secrets, Hostnamen, DB-/Kontonamen
oder Konfigurationswerte. Keine Shell-Auswertung (`shell=True`), keine Downloads ausser
pip aus den gesperrten Requirements, kein sudo, keine Systemaenderungen.
"""

from __future__ import annotations

import argparse
import ctypes.util
import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True

PROJECT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)
ENVIRONMENTS = ("local", "staging", "production")
REQUIRED_KEYS = {
    "local": ["DJANGO_SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD"],
    "staging": ["DJANGO_SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD", "ALLOWED_HOSTS", "EMAIL_HOST", "DEFAULT_FROM_EMAIL"],
    "production": ["DJANGO_SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD", "ALLOWED_HOSTS", "EMAIL_HOST", "DEFAULT_FROM_EMAIL"],
}
SECRET_KEYS = {"DJANGO_SECRET_KEY", "DB_PASSWORD", "TEST_DB_PASSWORD", "EMAIL_HOST_PASSWORD",
               "DB_USER", "DB_NAME", "DB_HOST", "TEST_DB_USER", "TEST_DB_NAME", "EMAIL_HOST",
               "EMAIL_HOST_USER", "ALLOWED_HOSTS", "DEFAULT_FROM_EMAIL"}
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,99}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{12}-[0-9]{14}$")
EXIT = {"PASS": 0, "WARN": 0, "FAIL": 1, "UNKNOWN": 2, "BLOCKED": 2, "PREPARED_NOT_ACTIVATED": 3}
_ORDER = ["PASS", "WARN", "PREPARED_NOT_ACTIVATED", "UNKNOWN", "BLOCKED", "FAIL"]


# --- Bericht ------------------------------------------------------------------------

@dataclass
class Check:
    code: str
    status: str
    message: str
    next_step: str = ""


@dataclass
class Report:
    mode: str
    environment: str
    checks: list[Check] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list, repr=False)

    def add(self, code, status, message, next_step=""):
        self.checks.append(Check(code, status, self.redact(message), self.redact(next_step)))
        return status

    def redact(self, text: str) -> str:
        for value in sorted({s for s in self.secrets if s and len(s) >= 3}, key=len, reverse=True):
            text = text.replace(value, "<redigiert>")
        return text

    def worst(self) -> str:
        return max((c.status for c in self.checks), key=_ORDER.index, default="PASS")

    def exit_code(self) -> int:
        return EXIT[self.worst()]

    def text(self) -> str:
        lines = [f"{self.mode} ({self.environment}): {self.worst()}"]
        for c in self.checks:
            lines.append(f"  [{c.status:7}] {c.code}: {c.message}")
            if c.next_step and c.status != "PASS":
                lines.append(f"            -> {c.next_step}")
        return "\n".join(lines)

    def shareable(self) -> dict:
        return {
            "mode": self.mode, "environment": self.environment, "result": self.worst(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": f"{sys.platform}-{os.uname().machine}" if hasattr(os, "uname") else sys.platform,
            "checks": [asdict(c) for c in self.checks],
        }


# --- Konfiguration ------------------------------------------------------------------

class ConfigError(ValueError):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    """Gleiches Format wie config/envfile.py: KEY=VALUE, keine Shell-Auswertung."""
    values: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Zeile {lineno}: keine KEY=VALUE-Zeile")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Z][A-Z0-9_]*$", key) or key in values:
            raise ConfigError(f"Zeile {lineno}: ungueltiger oder doppelter Schluessel")
        values[key] = value
    return values


def load_hosting(config_dir: Path) -> dict:
    path = config_dir / "hosting.local.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError("hosting.local.json ist kein gueltiges JSON") from exc
    return data if isinstance(data, dict) else {}


def _mode_ok(path: Path, max_mode: int) -> bool:
    return (path.stat().st_mode & 0o777) & ~max_mode == 0


# --- Prozesse -----------------------------------------------------------------------

def run(argv: list[str], *, cwd: Path, env_extra: dict | None = None, timeout: int = 900,
        report: Report | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in SECRET_KEYS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(env_extra or {})
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, shell=False)
    if report is not None:
        result.stdout = report.redact(result.stdout or "")
        result.stderr = report.redact(result.stderr or "")
    return result


def _tail(text: str, lines: int = 6) -> str:
    return " | ".join([l for l in (text or "").strip().splitlines() if l.strip()][-lines:])[:600]


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


# --- Gemeinsame Pruefungen -----------------------------------------------------------

def check_python(report: Report) -> None:
    v = sys.version_info
    if (v.major, v.minor) >= MIN_PYTHON:
        report.add("PY_VERSION", "PASS", f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        report.add("PY_VERSION", "FAIL", f"Python {v.major}.{v.minor} zu alt",
                   f"Gepflegtes Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} vom Hoster anfordern")
    try:
        import ensurepip  # noqa: F401
        import venv  # noqa: F401
        report.add("PY_VENV", "PASS", "venv/ensurepip verfuegbar")
    except ImportError:
        report.add("PY_VENV", "FAIL", "venv/ensurepip fehlt", "Paket fuer virtuelle Umgebungen vom Hoster/Betreiber installieren lassen")


def check_config(report: Report, config_dir: Path, environment: str) -> dict:
    env_file = config_dir / ".env"
    if not env_file.is_file():
        report.add("CONFIG_ENV", "FAIL", "Private .env fehlt", "Aus .env.example erstellen (Rechte 0600)")
        return {}
    try:
        values = parse_env_file(env_file)
    except ConfigError as exc:
        report.add("CONFIG_ENV", "FAIL", f"Private .env ungueltig ({exc})", "Format KEY=VALUE pruefen")
        return {}
    report.secrets.extend(v for k, v in values.items() if k in SECRET_KEYS)
    if not _mode_ok(env_file, 0o600):
        report.add("CONFIG_PERMS", "WARN", "Private .env ist fuer Gruppe/Andere lesbar", "chmod 600 auf die Datei")
    else:
        report.add("CONFIG_PERMS", "PASS", "Private .env nur fuer Besitzer lesbar")
    missing = [k for k in REQUIRED_KEYS[environment] if not values.get(k)]
    if missing:
        report.add("CONFIG_KEYS", "FAIL", "Pflichtwerte fehlen: " + ", ".join(missing), "In privater .env ergaenzen")
    else:
        report.add("CONFIG_KEYS", "PASS", "Pflichtwerte vorhanden (Werte nicht geprueft/angezeigt)")
    if values.get("ENVIRONMENT") != environment:
        report.add("CONFIG_ENVIRONMENT", "FAIL", "ENVIRONMENT in .env passt nicht zu --environment",
                   "Zielumgebung pruefen; nie Produktiv- und Testkonfiguration mischen")
    else:
        report.add("CONFIG_ENVIRONMENT", "PASS", "Umgebung stimmt ueberein")
    if len(values.get("DJANGO_SECRET_KEY", "")) < 50:
        report.add("CONFIG_SECRET", "FAIL" if environment == "production" else "WARN",
                   "DJANGO_SECRET_KEY zu kurz", "Mindestens 50 zufaellige Zeichen; lokale Schluessel nie produktiv")
    return values


def verify_vendor(report: Report, root: Path) -> None:
    manifest = root / "vendor-manifest.json"
    try:
        assets = json.loads(manifest.read_text(encoding="utf-8"))["assets"]
    except (OSError, ValueError, KeyError):
        report.add("VENDOR_MANIFEST", "FAIL", "vendor-manifest.json fehlt oder ungueltig")
        return
    bad = []
    for asset in assets:
        base = root / asset["install_path"]
        for rel, expected in asset["files_sha256"].items():
            path = (base / rel).resolve()
            if base.resolve() not in path.parents:
                bad.append(rel)
                continue
            try:
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    bad.append(rel)
            except OSError:
                bad.append(rel)
    if bad:
        report.add("VENDOR_HASH", "FAIL", f"{len(bad)} Browserasset(s) weichen vom Manifest ab",
                   "Assets aus gepruefter Quelle neu bereitstellen; nicht blind neu hashen")
    else:
        report.add("VENDOR_HASH", "PASS", "Browserassets entsprechen dem Manifest")


def check_wsgi(report: Report, environment: str, hosting: dict) -> None:
    if environment == "local":
        report.add("WSGI_PROFILE", "PASS", "Lokal: Entwicklungsserver nur auf 127.0.0.1; WSGI nicht erforderlich")
        return
    profile = hosting.get("wsgi_profile")
    if profile not in ("mod_wsgi", "passenger", "gunicorn_proxy") or not hosting.get("operator_confirmed"):
        report.add("BLOCKED_HOSTING_WSGI", "BLOCKED",
                   "Keine vom Betreiber bestaetigte Python-Webanbindung (mod_wsgi, Passenger oder betreuter Gunicorn)",
                   "Beim Hoster eine WSGI-Anbindung mit passendem Python anfordern und in hosting.local.json bestaetigen")
        return
    report.add("WSGI_PROFILE", "PASS", f"Profil {profile} vom Betreiber bestaetigt (Ausfuehrung damit nicht bewiesen)")
    apachectl = shutil.which("apachectl") or shutil.which("apache2ctl")
    if profile == "mod_wsgi" and apachectl:
        result = run([apachectl, "-M"], cwd=PROJECT, timeout=20)
        if "wsgi_module" in (result.stdout + result.stderr):
            report.add("WSGI_MODULE", "PASS", "mod_wsgi im Apache geladen")
        else:
            report.add("WSGI_MODULE", "UNKNOWN", "mod_wsgi im lokalen Apache nicht erkennbar",
                       "Vom Hoster bestaetigen lassen, dass mod_wsgi zum Python-Interpreter passt")
    report.add("WSGI_RUNTIME", "UNKNOWN", "Echte WSGI-Ausfuehrung nicht verifiziert",
               "Nach Deployment einen autorisierten Webrequest pruefen (docs/HOSTING_CHECK.md)")


# --- Modi -----------------------------------------------------------------------------

def mode_preflight(args, report: Report) -> None:
    config_dir = Path(args.config_dir)
    check_python(report)
    for tool, code, needed in (("git", "TOOL_GIT", args.environment != "local"),
                               ("pkg-config", "TOOL_PKGCONFIG", False), ("cc", "TOOL_CC", False)):
        if shutil.which(tool):
            report.add(code, "PASS", f"{tool} vorhanden")
        else:
            report.add(code, "FAIL" if needed else "WARN", f"{tool} nicht gefunden",
                       "Nur noetig fuer Git-Deployment bzw. Bau von mysqlclient ohne passendes Wheel")
    if ctypes.util.find_library("mariadb") or ctypes.util.find_library("mysqlclient"):
        report.add("NATIVE_MYSQL", "PASS", "MariaDB/MySQL-Clientbibliothek gefunden")
    else:
        report.add("NATIVE_MYSQL", "WARN", "MariaDB/MySQL-Clientbibliothek nicht gefunden",
                   "Laufzeitbibliothek (z. B. libmariadb) beim Hoster pruefen lassen")
    include = sysconfig.get_paths().get("include", "")
    report.add("PY_HEADERS", "PASS" if Path(include, "Python.h").is_file() else "WARN",
               "Python-Header " + ("vorhanden" if Path(include, "Python.h").is_file() else "fehlen (nur fuer Builds noetig)"))
    try:
        hosting = load_hosting(config_dir)
    except ConfigError as exc:
        report.add("HOSTING_CONFIG", "FAIL", str(exc))
        hosting = {}
    check_config(report, config_dir, args.environment)
    check_wsgi(report, args.environment, hosting)
    free = shutil.disk_usage(PROJECT).free
    report.add("DISK_FREE", "PASS" if free > 1 << 30 else "WARN", f"{free // (1 << 20)} MiB frei")
    if args.environment != "local":
        smtp_ok = hosting.get("smtp_verified") is True
        report.add("SMTP_NOT_VERIFIED" if not smtp_ok else "SMTP", "UNKNOWN" if not smtp_ok else "PASS",
                   "SMTP-Zustellung " + ("bestaetigt" if smtp_ok else "nicht verifiziert"),
                   "Separat freigegebenen Testversand an vorgesehenen Testempfaenger durchfuehren")
        report.add("CRON", "PASS" if hosting.get("cron_available") else "UNKNOWN",
                   "Zeitgesteuerte Wartung " + ("verfuegbar" if hosting.get("cron_available") else "nicht bestaetigt"),
                   "cleanup_security_state, check_tile_cache_update, cleanup_tile_cache regelmaessig ausfuehren")
    if args.network_check:
        for code, url in (("NET_PYPI", "https://pypi.org/simple/pip/"),):
            try:
                req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Adresskarte-Installer/0.5"})
                with urllib.request.urlopen(req, timeout=8, context=ssl.create_default_context()) as resp:
                    report.add(code, "PASS" if resp.status < 400 else "WARN", f"HTTPS erreichbar (Status {resp.status})")
            except Exception as exc:
                report.add(code, "WARN", f"HTTPS nicht erreichbar ({type(exc).__name__})",
                           "Wheelhouse/Offline-Installation vorbereiten oder Egress klaeren")
    else:
        report.add("NET_CHECK", "WARN" if args.environment == "local" else "UNKNOWN",
                   "Netzwerkpruefung nicht ausgefuehrt", "Bei Bedarf mit --network-check")


def _install_steps(root: Path, args) -> list[tuple[str, list[str], dict]]:
    py = str(venv_python(root))
    settings_module = "config.settings.local" if args.environment == "local" else f"config.settings.{args.environment}"
    env = {"DJANGO_SETTINGS_MODULE": settings_module}
    if getattr(args, "env_file", None):
        env["ADRESSKARTE_ENV_FILE"] = str(args.env_file)
    pip = [py, "-m", "pip", "install", "--require-hashes", "--no-deps", "-r", "requirements.txt"]
    if args.wheelhouse:
        pip += ["--no-index", "--find-links", str(Path(args.wheelhouse).resolve())]
    steps = [
        ("VENV_CREATE", [sys.executable, "-m", "venv", str(root / ".venv")], {}),
        ("PIP_INSTALL", pip, {}),
        ("PIP_CHECK", [py, "-m", "pip", "check"], {}),
        ("DJANGO_CHECK", [py, "manage.py", "check"] + (["--deploy"] if args.environment == "production" else []), env),
        ("STATIC_COLLECT", [py, "manage.py", "collectstatic", "--noinput"], env),
        ("MIGRATION_PLAN", [py, "manage.py", "migrate", "--plan"], env),
    ]
    if getattr(args, "migrate", False):
        steps.append(("MIGRATE", [py, "manage.py", "migrate", "--noinput"], env))
    return steps


def mode_install(args, report: Report, root: Path = PROJECT) -> None:
    check_python(report)
    values = check_config(report, Path(args.config_dir), args.environment)
    if report.worst() == "FAIL":
        return
    verify_vendor(report, root)
    steps = _install_steps(root, args)
    if not args.apply:
        for code, argv, _env in steps:
            if code == "VENV_CREATE" and venv_python(root).exists():
                continue
            report.add(f"PLAN_{code}", "PASS", "geplant: " + " ".join(Path(a).name if "/" in a else a for a in argv[:6]))
        report.add("PLAN_ONLY", "PASS", "Nichts veraendert",
                   "Mit --apply ausfuehren; Migrationen nur zusaetzlich mit --migrate auf bestaetigter DB")
        return
    if args.environment == "production" and getattr(args, "migrate", False) and not args.backup_confirmed:
        report.add("BACKUP_UNCONFIRMED", "BLOCKED", "Migration ohne bestaetigtes Backup",
                   "Verifiziertes Backup erstellen und mit --backup-confirmed bestaetigen")
        return
    for code, argv, env in steps:
        if code == "VENV_CREATE" and venv_python(root).exists():
            report.add(code, "PASS", "Virtuelle Umgebung vorhanden (wird nicht ersetzt)")
            continue
        result = run(argv, cwd=root, env_extra=env, report=report)
        if result.returncode != 0:
            report.add(code, "FAIL", "Schritt fehlgeschlagen: " + _tail(result.stderr or result.stdout),
                       "Ursache beheben; nichts wurde automatisch zurueckgesetzt")
            return
        message = "ok"
        if code == "MIGRATION_PLAN":
            pending = [l for l in result.stdout.splitlines() if l.strip().startswith(("Apply", "Unapply", "Raw", "Run", "+", "-")) or " -> " in l]
            message = "keine offenen Migrationen" if "No planned migration" in result.stdout else f"offene Migrationsschritte: {len(pending)}"
            if "No planned migration" not in result.stdout and not getattr(args, "migrate", False):
                report.add(code, "WARN", message, "Nach Pruefung mit --migrate anwenden")
                continue
        report.add(code, "PASS", message)
    report.add("NEXT_STEPS", "PASS", "Keine Demodaten oder Konten automatisch angelegt",
               "Initialkonten separat: manage.py provision_initial_accounts --plan/--apply")


def mode_doctor(args, report: Report, root: Path = PROJECT) -> None:
    check_python(report)
    values = check_config(report, Path(args.config_dir), args.environment)
    py = venv_python(root)
    if not py.exists():
        report.add("VENV_MISSING", "FAIL", "Virtuelle Umgebung fehlt", "install --plan / --apply ausfuehren")
        return
    verify_vendor(report, root)
    result = run([str(py), "-m", "pip", "check"], cwd=root, report=report)
    report.add("PIP_CHECK", "PASS" if result.returncode == 0 else "FAIL",
               "Abhaengigkeiten konsistent" if result.returncode == 0 else "pip check meldet Konflikte: " + _tail(result.stdout))
    freeze = run([str(py), "-m", "pip", "freeze", "--all"], cwd=root, report=report).stdout
    installed = {l.split("==")[0].lower().replace("_", "-"): l.split("==")[1] for l in freeze.splitlines() if "==" in l}
    pinned = {}
    for line in (root / "requirements.txt").read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", line)
        if m:
            pinned[m.group(1).lower().replace("_", "-")] = m.group(2)
    drift = [n for n, v in pinned.items() if installed.get(n) != v]
    report.add("LOCK_MATCH", "PASS" if not drift else "FAIL",
               "Installierte Pakete entsprechen requirements.txt" if not drift else f"{len(drift)} Paket(e) weichen ab: " + ", ".join(sorted(drift)[:8]),
               "install --apply aus den gesperrten Requirements")
    settings_module = "config.settings.local" if args.environment == "local" else f"config.settings.{args.environment}"
    env = {"DJANGO_SETTINGS_MODULE": settings_module}
    if getattr(args, "env_file", None):
        env["ADRESSKARTE_ENV_FILE"] = str(args.env_file)
    check = run([str(py), "manage.py", "check"] + (["--deploy"] if args.environment == "production" else []),
                cwd=root, env_extra=env, report=report)
    warnings = len(re.findall(r"\(security\.W\d+\)", check.stdout + check.stderr))
    if check.returncode != 0:
        report.add("DJANGO_CHECK", "FAIL", "manage.py check fehlgeschlagen: " + _tail(check.stderr or check.stdout))
    else:
        report.add("DJANGO_CHECK", "WARN" if warnings else "PASS",
                   f"manage.py check ok ({warnings} Sicherheitshinweise)" if warnings else "manage.py check ok",
                   "Hinweise einzeln bewerten (docs/SECURITY.md)")
    mig = run([str(py), "manage.py", "migrate", "--check"], cwd=root, env_extra=env, report=report)
    report.add("MIGRATIONS", "PASS" if mig.returncode == 0 else "WARN",
               "Alle Migrationen angewendet" if mig.returncode == 0 else "Offene Migrationen vorhanden",
               "Migrationsplan pruefen und kontrolliert anwenden")
    probe = run([str(py), "manage.py", "installation_status"], cwd=root, env_extra=env, report=report)
    try:
        status = json.loads(probe.stdout.strip().splitlines()[-1])
        report.add("SETTINGS_DEBUG", "FAIL" if status["debug"] and args.environment == "production" else "PASS",
                   f"DEBUG={'an' if status['debug'] else 'aus'}")
        report.add("SETTINGS_HOSTS", "FAIL" if status["wildcard_hosts"] else "PASS",
                   "ALLOWED_HOSTS ohne Wildcard" if not status["wildcard_hosts"] else "ALLOWED_HOSTS enthaelt *")
        report.add("BOOTSTRAP", "PASS" if status["admin_ready"] else "WARN",
                   "Initialkonten: " + (", ".join(f"{k}={v}" for k, v in sorted(status["initial_accounts"].items())) or "keine"),
                   "provision_initial_accounts und Aktivierung des ersten Administrators")
        report.add("TILE_SOURCE", "PASS", f"Kartenquelle {status['tile_source']}")
    except (ValueError, IndexError, KeyError):
        report.add("SETTINGS_PROBE", "UNKNOWN", "Statusabfrage nicht auswertbar: " + _tail(probe.stderr))
    var_dir = root / values.get("VAR_DIR", "var") if values else root / "var"
    private = [var_dir] + [var_dir / n for n in ("private_media", "private_mail", "tile_cache")]
    loose = [p.name for p in private if p.exists() and p.stat().st_mode & 0o077]
    if any(p.exists() and p.stat().st_mode & 0o002 for p in private):
        report.add("VAR_PERMS", "FAIL", "Privates Datenverzeichnis ist world-writable", "chmod 700")
    elif loose:
        report.add("VAR_PERMS", "WARN", "Private Verzeichnisse fuer Gruppe/Andere zugaenglich: " + ", ".join(loose),
                   "chmod 700 (bei getrennter Webserver-UID Rechte mit Hoster abstimmen)")
    else:
        report.add("VAR_PERMS", "PASS", "Private Datenverzeichnisse nur fuer Besitzer zugaenglich")


# --- Deployment -------------------------------------------------------------------------

class DeployError(Exception):
    def __init__(self, code: str, message: str, next_step: str = ""):
        super().__init__(message)
        self.code, self.next_step = code, next_step


def _layout(app_home: Path) -> dict[str, Path]:
    return {
        "repository": app_home / "repository", "releases": app_home / "releases",
        "current": app_home / "current", "shared": app_home / "shared",
        "config": app_home / "shared" / "config", "state": app_home / "shared" / "installation-state",
        "locks": app_home / "shared" / "locks",
    }


def resolve_ref(repository: Path, ref: str) -> str:
    if not REF_RE.match(ref) or ".." in ref:
        raise DeployError("REF_INVALID", "Ungueltiger Git-Ref", "Nur Tag/Branch/Commit-ID ohne Sonderzeichen")
    result = run(["git", "-C", str(repository), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                 cwd=repository)
    commit = result.stdout.strip()
    if result.returncode != 0 or not re.match(r"^[0-9a-f]{40}$", commit):
        raise DeployError("REF_UNKNOWN", "Git-Ref nicht im Deployment-Repository gefunden",
                          "Freigegebenen Stand vorher kontrolliert holen (git fetch durch Betreiber)")
    return commit


class DeployLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = open(self.path, "a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise DeployError("LOCK_HELD", "Ein anderes Deployment/Rollback laeuft bereits",
                              "Abschluss abwarten; Lock nicht manuell entfernen")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def export_release(repository: Path, commit: str, target: Path) -> None:
    """git archive (keine Hooks, keine Submodule) und sicheres Entpacken (filter=data)."""
    if target.exists():
        raise DeployError("RELEASE_EXISTS", "Releaseverzeichnis existiert bereits", "Neuen Plan erstellen")
    tmp = Path(tempfile.mkdtemp(prefix=".release-", dir=target.parent))
    try:
        archive = tmp / "release.tar"
        with open(archive, "wb") as out:
            result = subprocess.run(["git", "-C", str(repository), "archive", "--format=tar", commit],
                                    stdout=out, stderr=subprocess.PIPE, timeout=300, shell=False)
        if result.returncode != 0:
            raise DeployError("EXPORT_FAILED", "git archive fehlgeschlagen")
        extract = tmp / "tree"
        extract.mkdir()
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(extract, filter="data")  # verhindert Pfad-/Symlinkausbruch
        except (tarfile.TarError, OSError) as exc:
            raise DeployError("UNSAFE_ARCHIVE", f"Archiv abgelehnt ({type(exc).__name__})",
                              "Repository-Inhalt pruefen (Links/Pfade ausserhalb des Releases)")
        os.rename(extract, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def migration_names(release: Path) -> set[str]:
    return {f"{p.parent.parent.name}.{p.stem}" for p in release.glob("*/migrations/[0-9]*.py")}


def switch_current(layout: dict, release: Path) -> None:
    tmp = layout["current"].with_name(f".current-{os.getpid()}")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(release, tmp)
    os.replace(tmp, layout["current"])


def reload_app(hosting: dict, app_home: Path) -> tuple[bool, str]:
    reload_cfg = hosting.get("reload") or {}
    if not reload_cfg.get("operator_confirmed"):
        return False, "Keine vom Betreiber bestaetigte Reload-Funktion"
    if reload_cfg.get("type") == "touch":
        path = (app_home / reload_cfg.get("path", "")).resolve()
        if app_home.resolve() not in path.parents or not path.is_file():
            return False, "Reload-Datei ausserhalb des App-Verzeichnisses oder fehlend"
        os.utime(path, None)
        return True, "Reload ausgeloest (touch)"
    if reload_cfg.get("type") == "command" and isinstance(reload_cfg.get("argv"), list):
        result = run([str(a) for a in reload_cfg["argv"]], cwd=app_home, timeout=120)
        return result.returncode == 0, "Reload-Befehl " + ("ok" if result.returncode == 0 else "fehlgeschlagen")
    return False, "Reload-Konfiguration unbekannt"


def prepare_release_environment(release: Path, args, report: Report, env_file: Path) -> None:
    """Venv, Pakete, Assets, Checks im neuen Release (in Tests ersetzbar)."""
    sub = argparse.Namespace(**{**vars(args), "apply": True, "env_file": env_file})
    steps = [s for s in _install_steps(release, sub) if s[0] != "MIGRATE"]
    verify_vendor(report, release)
    for code, argv, env in steps:
        result = run(argv, cwd=release, env_extra=env, report=report)
        if result.returncode != 0:
            raise DeployError(code, "Schritt im neuen Release fehlgeschlagen: " + _tail(result.stderr or result.stdout),
                              "Laufendes Release ist unveraendert; Ursache beheben und neu planen")
        if code == "MIGRATION_PLAN":
            pending = "No planned migration" not in result.stdout
            (release / ".migrations-pending").write_text("1" if pending else "0")


def mode_deploy(args, report: Report) -> None:
    app_home = Path(args.app_home).resolve()
    layout = _layout(app_home)
    report.secrets.append(str(app_home))
    if not layout["repository"].is_dir():
        report.add("REPOSITORY_MISSING", "BLOCKED", "Deployment-Repository fehlt",
                   "Betreiber legt app-home/repository per lesendem Git-Zugang an")
        return
    plan_path = layout["state"] / "deploy-plan.json"
    try:
        commit = resolve_ref(layout["repository"], args.ref)
        if not args.apply:
            release_id = f"{commit[:12]}-{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
            layout["state"].mkdir(parents=True, exist_ok=True, mode=0o700)
            plan_path.write_text(json.dumps({"ref": args.ref, "commit": commit, "release_id": release_id}))
            report.add("PLAN", "PASS", f"Release {release_id} geplant (Commit {commit[:12]})",
                       "Mit --apply und demselben --ref ausfuehren")
            return
        try:
            planned = json.loads(plan_path.read_text())
        except (OSError, ValueError):
            raise DeployError("PLAN_MISSING", "Kein gueltiger Plan", "Zuerst --plan ausfuehren")
        if planned.get("ref") != args.ref or planned.get("commit") != commit or not RELEASE_RE.match(planned.get("release_id", "")):
            raise DeployError("REF_CHANGED", "Ref zeigt nicht mehr auf den geplanten Commit",
                              "Aenderung pruefen und neu planen")
        hosting = load_hosting(layout["config"])
        with DeployLock(layout["locks"] / "deploy.lock"):
            release = layout["releases"] / planned["release_id"]
            layout["releases"].mkdir(parents=True, exist_ok=True, mode=0o750)
            export_release(layout["repository"], commit, release)
            report.add("EXPORT", "PASS", "Release aus Git exportiert (ohne Hooks/Submodule)")
            env_file = layout["config"] / ".env"
            prepare_release_environment(release, args, report, env_file)
            report.add("PREPARE", "PASS", "Pakete, Assets, Checks und statische Dateien im neuen Release bereit")
            if (release / ".migrations-pending").read_text() == "1":
                if not args.migrate:
                    report.add("PREPARED_NOT_ACTIVATED", "PREPARED_NOT_ACTIVATED",
                               "Offene Migrationen; Release vorbereitet, nicht aktiviert",
                               "Backup verifizieren, Wartungsmodus abstimmen, dann mit --migrate --backup-confirmed")
                    return
                if not args.backup_confirmed:
                    raise DeployError("BACKUP_UNCONFIRMED", "Migration ohne bestaetigtes Backup",
                                      "Verifiziertes Backup erstellen und mit --backup-confirmed bestaetigen")
                py = venv_python(release)
                result = run([str(py), "manage.py", "migrate", "--noinput"], cwd=release, report=report,
                             env_extra={"DJANGO_SETTINGS_MODULE": f"config.settings.{args.environment}",
                                        "ADRESSKARTE_ENV_FILE": str(env_file)})
                if result.returncode != 0:
                    raise DeployError("MIGRATE_FAILED", "Migration fehlgeschlagen; gestoppt (MySQL-DDL nicht transaktional)",
                                      "Diagnose; Wiederherstellung nur nach Betreiberentscheid, kein Auto-Restore")
                report.add("MIGRATE", "PASS", "Migrationen angewendet")
            if not (hosting.get("reload") or {}).get("operator_confirmed"):
                report.add("PREPARED_NOT_ACTIVATED", "PREPARED_NOT_ACTIVATED",
                           "Release vorbereitet; keine bestaetigte Reload-Funktion",
                           "Manuell: current-Symlink umstellen und App-Neustart durch Hoster ausloesen")
                return
            previous = os.readlink(layout["current"]) if layout["current"].is_symlink() else ""
            switch_current(layout, release)
            ok, message = reload_app(hosting, app_home)
            if not ok:
                if previous:
                    switch_current(layout, Path(previous))
                report.add("RELOAD_FAILED", "FAIL", message + "; vorheriges Release wieder verlinkt",
                           "Reload mit dem Hoster klaeren")
                return
            history = layout["state"] / "releases.json"
            entries = json.loads(history.read_text()) if history.exists() else []
            entries.append({"release_id": planned["release_id"], "commit": commit,
                            "activated_at": _dt.datetime.now().isoformat(timespec="seconds")})
            history.write_text(json.dumps(entries, indent=1))
            plan_path.unlink(missing_ok=True)
            report.add("ACTIVATED", "PASS", message)
            report.add("WSGI_RUNTIME", "UNKNOWN", "Webrequest gegen neues Release nicht automatisch verifiziert",
                       "Autorisierten Request pruefen (docs/HOSTING_CHECK.md)")
    except DeployError as exc:
        report.add(exc.code, "FAIL", str(exc), exc.next_step)


def mode_rollback(args, report: Report) -> None:
    app_home = Path(args.app_home).resolve()
    layout = _layout(app_home)
    report.secrets.append(str(app_home))
    if not RELEASE_RE.match(args.to or ""):
        report.add("RELEASE_INVALID", "FAIL", "Ungueltige Release-ID")
        return
    target = layout["releases"] / args.to
    if not target.is_dir() or not layout["current"].is_symlink():
        report.add("RELEASE_MISSING", "FAIL", "Ziel- oder aktuelles Release fehlt")
        return
    current = Path(os.readlink(layout["current"]))
    newer = migration_names(current) - migration_names(target)
    if newer:
        report.add("SCHEMA_NEWER", "FAIL",
                   f"Aktuelles Release hat {len(newer)} Migration(en), die das Ziel nicht kennt",
                   "Kein automatischer Rueckwechsel; Wartungsmodus und Betreiberentscheid (kein Auto-Restore)")
        return
    report.add("COMPATIBLE", "PASS", "Migrationsstand kompatibel (Code-Rueckwechsel moeglich)")
    if not args.apply:
        report.add("PLAN_ONLY", "PASS", "Nichts veraendert", "Mit --apply ausfuehren")
        return
    hosting = load_hosting(layout["config"])
    try:
        with DeployLock(layout["locks"] / "deploy.lock"):
            if not (hosting.get("reload") or {}).get("operator_confirmed"):
                report.add("PREPARED_NOT_ACTIVATED", "PREPARED_NOT_ACTIVATED", "Keine bestaetigte Reload-Funktion",
                           "Manuell: current-Symlink auf Ziel umstellen und App-Neustart ausloesen")
                return
            switch_current(layout, target)
            ok, message = reload_app(hosting, app_home)
            if not ok:
                switch_current(layout, current)
                report.add("RELOAD_FAILED", "FAIL", message)
                return
            report.add("ROLLED_BACK", "PASS", message)
    except DeployError as exc:
        report.add(exc.code, "FAIL", str(exc), exc.next_step)


# --- CLI --------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adresskarte: preflight/install/doctor/deploy/rollback")
    parser.add_argument("mode", choices=["preflight", "install", "doctor", "deploy", "rollback"])
    parser.add_argument("--environment", required=True, choices=ENVIRONMENTS)
    parser.add_argument("--config-dir", default=str(PROJECT), help="Verzeichnis mit privater .env und hosting.local.json")
    parser.add_argument("--app-home", help="Privates Hosting-Basisverzeichnis (deploy/rollback)")
    parser.add_argument("--ref", help="Freigegebener Tag/Commit (deploy)")
    parser.add_argument("--to", help="Release-ID (rollback)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true", help="Nur planen (Standard)")
    group.add_argument("--apply", action="store_true", help="Ausfuehren")
    parser.add_argument("--migrate", action="store_true", help="Migrationen anwenden (zusaetzlich zu --apply)")
    parser.add_argument("--backup-confirmed", action="store_true", help="Verifiziertes Backup bestaetigt")
    parser.add_argument("--wheelhouse", help="Lokales Wheelhouse (Offline-Installation)")
    parser.add_argument("--network-check", action="store_true", help="Explizite HTTPS-Erreichbarkeitspruefung")
    parser.add_argument("--report", help="Teilbaren JSON-Bericht an diesen Pfad schreiben")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 64
    if args.mode == "deploy" and (not args.app_home or not args.ref):
        parser.print_usage(sys.stderr)
        return 64
    if args.mode == "rollback" and (not args.app_home or not args.to):
        parser.print_usage(sys.stderr)
        return 64
    report = Report(args.mode, args.environment)
    {"preflight": mode_preflight, "install": mode_install, "doctor": mode_doctor,
     "deploy": mode_deploy, "rollback": mode_rollback}[args.mode](args, report)
    print(report.text())
    if args.report:
        target = Path(args.report)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(report.redact(json.dumps(report.shareable(), indent=2, ensure_ascii=False)))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
