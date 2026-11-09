"""Etappe 0/1: Grundlagen - Konfiguration, Datenbankumgebung, Benutzerbasis."""

import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase

from config.envfile import Env, EnvFileError, parse_env_file

BASE_DIR = Path(settings.BASE_DIR)


class EnvFileTests(SimpleTestCase):
    def _write(self, text: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".env", encoding="utf-8")
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_values_are_literal_not_shell(self):
        path = self._write("# c\nA=$(echo x)\nB=a=b#c\n\nC='q'\n")
        self.assertEqual(
            parse_env_file(path), {"A": "$(echo x)", "B": "a=b#c", "C": "'q'"}
        )

    def test_crlf_is_accepted(self):
        self.assertEqual(parse_env_file(self._write("A=1\r\nB=2\r\n")), {"A": "1", "B": "2"})

    def test_rejects_invalid_lines_without_echoing_values(self):
        for text in ("export A=1\n", "a=1\n", "NOEQUALS\n", "A=1\nA=2\n"):
            with self.subTest(text=text), self.assertRaises(EnvFileError) as ctx:
                parse_env_file(self._write(text))
            self.assertNotIn("=1", str(ctx.exception))

    def test_require_names_only_the_key(self):
        env = Env(self._write("A=\n"))
        with self.assertRaisesMessage(EnvFileError, "Pflichtwert fehlt: A"):
            env.require("A")


class DatabaseEnvironmentTests(TestCase):
    def test_runs_on_mariadb_not_sqlite(self):
        self.assertEqual(connection.vendor, "mysql")
        self.assertTrue(connection.mysql_is_mariadb)

    def test_session_is_strict_innodb_utf8mb4(self):
        with connection.cursor() as cur:
            cur.execute("SELECT @@SESSION.sql_mode, @@SESSION.default_storage_engine, @@character_set_connection")
            sql_mode, engine, charset = cur.fetchone()
        self.assertIn("STRICT_TRANS_TABLES", sql_mode)
        self.assertEqual(engine, "InnoDB")
        self.assertEqual(charset, "utf8mb4")

    def test_user_table_is_innodb(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                [get_user_model()._meta.db_table],
            )
            engine, collation = cur.fetchone()
        self.assertEqual(engine, "InnoDB")
        self.assertTrue(collation.startswith("utf8mb4"))

    def test_test_database_is_separate_from_dev(self):
        self.assertNotEqual(settings.DATABASES["default"]["NAME"], settings.ENV.get("DB_NAME"))
        self.assertEqual(settings.EMAIL_BACKEND, "django.core.mail.backends.locmem.EmailBackend")


class UserModelTests(TestCase):
    def test_email_is_login_and_normalized(self):
        user = get_user_model().objects.create_user("  Person@Example.ORG ")
        self.assertEqual(user.email, "person@example.org")
        self.assertEqual(get_user_model().USERNAME_FIELD, "email")

    def test_aliases_are_not_canonicalized(self):
        User = get_user_model()
        User.objects.create_user("a.b+x@example.org")
        User.objects.create_user("ab@example.org")
        self.assertEqual(User.objects.count(), 2)

    def test_case_variants_collide(self):
        User = get_user_model()
        User.objects.create_user("x@example.org")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(email="X@EXAMPLE.org")

    def test_new_user_is_pending_without_roles_or_usable_password(self):
        user = get_user_model().objects.create_user("new@example.org")
        self.assertEqual(user.state, user.State.PENDING_EMAIL)
        self.assertFalse(user.email_verified)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff or user.is_superuser)

    def test_create_superuser_is_disabled(self):
        with self.assertRaises(PermissionError):
            get_user_model().objects.create_superuser("root@example.org", "x")


class CommandGuardTests(SimpleTestCase):
    def _manage(self, *args, env_settings=None):
        import os

        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        if env_settings:
            env["DJANGO_SETTINGS_MODULE"] = env_settings
        return subprocess.run(
            [sys.executable, str(BASE_DIR / "manage.py"), *args],
            capture_output=True, text=True, env=env, cwd=BASE_DIR, timeout=60,
        )

    def test_test_command_requires_test_settings(self):
        result = self._manage("test", "--settings=config.settings.local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nur mit --settings=config.settings.test", result.stderr)

    def test_flush_refused_with_production_settings(self):
        result = self._manage("flush", "--noinput", "--settings=config.settings.production")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("in Produktion verboten", result.stderr)

    def test_production_settings_refuse_local_environment(self):
        result = self._manage("check", "--settings=config.settings.production")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verlangt ENVIRONMENT=production", result.stderr)
