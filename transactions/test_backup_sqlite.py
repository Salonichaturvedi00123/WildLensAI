import re
import sqlite3
from contextlib import closing
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings


class BackupSqliteCommandTests(TransactionTestCase):
    def test_creates_timestamped_integrity_checked_backup(self):
        user = User.objects.create_user(
            username="backup_test_user",
            password="StrongPassword123!",
        )

        with TemporaryDirectory() as backup_dir:
            output = StringIO()
            with override_settings(BACKUP_ROOT=backup_dir):
                call_command("backup_sqlite", stdout=output)

            backup_paths = list(Path(backup_dir).glob("db-*.sqlite3"))
            self.assertEqual(len(backup_paths), 1)
            self.assertRegex(
                backup_paths[0].name,
                re.compile(r"^db-\d{8}T\d{6}\.\d{6}Z\.sqlite3$"),
            )
            self.assertIn(str(backup_paths[0]), output.getvalue())

            with closing(sqlite3.connect(str(backup_paths[0]))) as backup:
                self.assertEqual(
                    backup.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    backup.execute(
                        "SELECT COUNT(*) FROM auth_user WHERE username = ?",
                        [user.username],
                    ).fetchone()[0],
                    1,
                )
