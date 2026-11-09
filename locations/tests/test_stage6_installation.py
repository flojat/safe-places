"""Etappe 6: Installations-/Pruef-/Deploymentroutine (Auftrag 12 G). Ohne Netzwerk, ohne echtes Hosting."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "manage_installation.py"
_spec = importlib.util.spec_from_file_location("manage_installation", SCRIPT)
mi = importlib.util.module_from_spec(_spec)
sys.modules["manage_installation"] = mi  # Dataclasses benoetigen das registrierte Modul
_spec.loader.exec_module(mi)

SECRET = "Sehr-geheimes-DB-Passwort-123"
HOST = "db.intern.example"


def write_config(directory: Path, environment="production", extra="", hosting=None):
    env = (f"ENVIRONMENT={environment}\nDJANGO_SECRET_KEY={'k' * 60}\nDB_NAME=prod_db\nDB_USER=prod_user\n"
           f"DB_PASSWORD={SECRET}\nDB_HOST={HOST}\nALLOWED_HOSTS=karte.example.org\nEMAIL_HOST=smtp.example.org\n"
           f"DEFAULT_FROM_EMAIL=noreply@example.org\nVAR_DIR=/srv/app/shared/var\n{extra}")
    path = directory / ".env"
    path.write_text(env)
    os.chmod(path, 0o600)
    if hosting is not None:
        (directory / "hosting.local.json").write_text(json.dumps(hosting))


def git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(root: Path) -> Path:
    repo = root / "repository"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.org")
    git(repo, "config", "user.name", "Test")
    (repo / "app" / "migrations").mkdir(parents=True)
    (repo / "app" / "migrations" / "0001_initial.py").write_text("# m1\n")
    (repo / "README").write_text("v1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "v1")
    git(repo, "tag", "v1")
    return repo


class ReportAndConfigTests(SimpleTestCase):
    def test_env_parser_is_literal(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("A=$(rm -rf /)\nB='x'\n")
            self.assertEqual(mi.parse_env_file(p), {"A": "$(rm -rf /)", "B": "'x'"})

    def test_reports_redact_secrets_and_hosts(self):
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d))
            report_path = Path(d) / "bericht.json"
            with mock.patch.object(mi, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 1, stdout=f"Access denied for {SECRET}@{HOST}", stderr="")
                code = mi.main(["preflight", "--environment", "production", "--config-dir", d, "--report", str(report_path)])
            text = report_path.read_text()
            self.assertEqual(oct(report_path.stat().st_mode & 0o777), "0o600")
            for value in (SECRET, HOST, "prod_user", "prod_db", "karte.example.org", "smtp.example.org"):
                self.assertNotIn(value, text)
            self.assertEqual(code, 2)  # BLOCKED ohne WSGI-Profil

    def test_missing_keys_named_without_values(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".env").write_text(f"ENVIRONMENT=production\nDB_PASSWORD={SECRET}\n")
            os.chmod(Path(d) / ".env", 0o644)
            report = mi.Report("preflight", "production")
            mi.check_config(report, Path(d), "production")
            codes = {c.code: c for c in report.checks}
            self.assertEqual(codes["CONFIG_KEYS"].status, "FAIL")
            self.assertIn("DJANGO_SECRET_KEY", codes["CONFIG_KEYS"].message)
            self.assertEqual(codes["CONFIG_PERMS"].status, "WARN")
            self.assertNotIn(SECRET, json.dumps(report.shareable()))


class PreflightTests(SimpleTestCase):
    def _run(self, d, environment="production"):
        report = mi.Report("preflight", environment)
        args = mi.build_parser().parse_args(["preflight", "--environment", environment, "--config-dir", d])
        mi.mode_preflight(args, report)
        return report, {c.code: c for c in report.checks}

    def test_preflight_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d))
            before = sorted(p.name for p in Path(d).iterdir())
            project_before = sorted(p.name for p in Path(settings.BASE_DIR).iterdir())
            self._run(d)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), before)
            self.assertEqual(sorted(p.name for p in Path(settings.BASE_DIR).iterdir()), project_before)

    def test_missing_wsgi_is_blocked_and_confirmed_is_not_claimed_as_running(self):
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d))
            report, codes = self._run(d)
            self.assertEqual(codes["BLOCKED_HOSTING_WSGI"].status, "BLOCKED")
            self.assertEqual(codes["SMTP_NOT_VERIFIED"].status, "UNKNOWN")
            self.assertEqual(codes["NET_CHECK"].status, "UNKNOWN")
            self.assertEqual(report.exit_code(), 2)
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d), hosting={"wsgi_profile": "mod_wsgi", "operator_confirmed": True})
            with mock.patch.object(mi.shutil, "which", side_effect=lambda t: None if "apache" in t else "/usr/bin/" + t):
                _, codes = self._run(d)
            self.assertEqual(codes["WSGI_PROFILE"].status, "PASS")
            self.assertEqual(codes["WSGI_RUNTIME"].status, "UNKNOWN")

    def test_relative_var_dir_fails_in_production(self):
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d), extra="")
            env = (Path(d) / ".env").read_text().replace("VAR_DIR=/srv/app/shared/var", "VAR_DIR=var")
            (Path(d) / ".env").write_text(env)
            _, codes = self._run(d)
            self.assertEqual(codes["CONFIG_VAR_DIR"].status, "FAIL")

    def test_environment_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d), environment="local")
            _, codes = self._run(d, "production")
            self.assertEqual(codes["CONFIG_ENVIRONMENT"].status, "FAIL")

    def test_vendor_tampering_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "static/vendor/leaflet").mkdir(parents=True)
            src = Path(settings.BASE_DIR)
            (root / "vendor-manifest.json").write_text((src / "vendor-manifest.json").read_text())
            for f in json.loads((src / "vendor-manifest.json").read_text())["assets"][0]["files_sha256"]:
                (root / "static/vendor/leaflet" / f).parent.mkdir(parents=True, exist_ok=True)
                (root / "static/vendor/leaflet" / f).write_bytes((src / "static/vendor/leaflet" / f).read_bytes())
            report = mi.Report("doctor", "local")
            mi.verify_vendor(report, root)
            self.assertEqual(report.checks[-1].status, "PASS")
            with open(root / "static/vendor/leaflet/leaflet.js", "a") as fh:
                fh.write("/* manipuliert */")
            mi.verify_vendor(report, root)
            self.assertEqual(report.checks[-1].status, "FAIL")

    def test_install_production_migration_requires_backup_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d))
            report = mi.Report("install", "production")
            args = mi.build_parser().parse_args(["install", "--environment", "production", "--config-dir", d, "--apply", "--migrate"])
            with mock.patch.object(mi, "run") as run:
                mi.mode_install(args, report)
            run.assert_not_called()
            self.assertIn("BACKUP_UNCONFIRMED", {c.code for c in report.checks})


class DeployTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = make_repo(self.home)
        (self.home / "shared" / "config").mkdir(parents=True)
        write_config(self.home / "shared" / "config", environment="production")
        (self.home / "app.wsgi").write_text("# reload marker\n")
        self.prepare = mock.patch.object(mi, "prepare_release_environment", side_effect=self._fake_prepare)
        self.prepare.start()

    def tearDown(self):
        self.prepare.stop()
        self.tmp.cleanup()

    def _fake_prepare(self, release, args, report, env_file):
        (release / ".migrations-pending").write_text("0")

    def _hosting(self, reload=True):
        cfg = {"wsgi_profile": "mod_wsgi", "operator_confirmed": True}
        if reload:
            cfg["reload"] = {"type": "touch", "path": "app.wsgi", "operator_confirmed": True}
        (self.home / "shared" / "config" / "hosting.local.json").write_text(json.dumps(cfg))

    def _deploy(self, *extra, ref="v1"):
        report = mi.Report("deploy", "production")
        args = mi.build_parser().parse_args(["deploy", "--environment", "production", "--app-home",
                                             str(self.home), "--ref", ref, *extra])
        mi.mode_deploy(args, report)
        return report, {c.code: c for c in report.checks}

    def test_plan_then_apply_activates_with_confirmed_reload(self):
        self._hosting()
        report, codes = self._deploy()
        self.assertIn("PLAN", codes)
        self.assertFalse((self.home / "current").exists())
        report, codes = self._deploy("--apply")
        self.assertEqual(codes["ACTIVATED"].status, "PASS")
        self.assertTrue((self.home / "current").is_symlink())
        self.assertTrue((self.home / "current" / "README").is_file())
        self.assertNotIn(str(self.home), json.dumps(report.shareable()))

    def test_without_reload_prepared_not_activated(self):
        self._hosting(reload=False)
        self._deploy()
        report, codes = self._deploy("--apply")
        self.assertEqual(report.exit_code(), 3)
        self.assertIn("PREPARED_NOT_ACTIVATED", codes)
        self.assertFalse((self.home / "current").exists())

    def test_ref_moved_between_plan_and_apply_is_detected(self):
        self._hosting()
        self._deploy()
        (self.repo / "README").write_text("v2\n")
        git(self.repo, "commit", "-qam", "v2")
        git(self.repo, "tag", "-f", "v1")
        report, codes = self._deploy("--apply")
        self.assertEqual(codes["REF_CHANGED"].status, "FAIL")
        self.assertFalse(any((self.home / "releases").glob("*")) if (self.home / "releases").exists() else False)

    def test_invalid_refs_and_no_command_injection(self):
        for ref in ("--upload-pack=touch /tmp/x", "a..b", "$(id)", "v1;rm -rf /", ""):
            with self.subTest(ref=ref):
                with self.assertRaises(mi.DeployError):
                    mi.resolve_ref(self.repo, ref)

    def test_parallel_deploy_blocked_by_lock(self):
        self._hosting()
        self._deploy()
        with mi.DeployLock(self.home / "shared" / "locks" / "deploy.lock"):
            report, codes = self._deploy("--apply")
        self.assertEqual(codes["LOCK_HELD"].status, "FAIL")

    def test_unsafe_symlink_in_repository_rejected(self):
        self._hosting()
        os.symlink("/etc/passwd", self.repo / "evil")
        git(self.repo, "add", "evil")
        git(self.repo, "commit", "-qm", "evil")
        self._deploy(ref="main")
        report, codes = self._deploy("--apply", ref="main")
        self.assertEqual(codes["UNSAFE_ARCHIVE"].status, "FAIL")
        self.assertFalse((self.home / "current").exists())

    def test_failed_reload_relinks_previous_release(self):
        self._hosting()
        self._deploy()
        self._deploy("--apply")
        first = os.readlink(self.home / "current")
        (self.repo / "README").write_text("v2\n")
        git(self.repo, "commit", "-qam", "v2")
        git(self.repo, "tag", "v2")
        (self.home / "app.wsgi").unlink()  # Reload schlaegt fehl
        self._deploy(ref="v2")
        report, codes = self._deploy("--apply", ref="v2")
        self.assertEqual(codes["RELOAD_FAILED"].status, "FAIL")
        self.assertEqual(os.readlink(self.home / "current"), first)

    def test_rollback_refuses_newer_schema(self):
        self._hosting()
        self._deploy()
        self._deploy("--apply")
        old_release = Path(os.readlink(self.home / "current")).name
        (self.repo / "app" / "migrations" / "0002_new.py").write_text("# m2\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "m2")
        git(self.repo, "tag", "v2")
        import time
        time.sleep(1)  # eindeutige Release-ID
        self._deploy(ref="v2")
        self._deploy("--apply", ref="v2")
        report = mi.Report("rollback", "production")
        args = mi.build_parser().parse_args(["rollback", "--environment", "production", "--app-home",
                                             str(self.home), "--to", old_release, "--apply"])
        mi.mode_rollback(args, report)
        self.assertEqual({c.code: c for c in report.checks}["SCHEMA_NEWER"].status, "FAIL")
        self.assertNotEqual(Path(os.readlink(self.home / "current")).name, old_release)

    def test_cli_usage_errors(self):
        self.assertEqual(mi.main(["deploy", "--environment", "production"]), 64)
        self.assertEqual(mi.main(["unbekannt", "--environment", "local"]), 64)
