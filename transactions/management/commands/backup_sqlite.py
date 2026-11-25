import sqlite3
from contextlib import closing
from datetime import timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone as django_timezone

from django.conf import settings


class Command(BaseCommand):
    help = "Create a consistent, timestamped backup of the default SQLite database."

    def handle(self, *args, **options):
        connection = connections["default"]
        if connection.vendor != "sqlite":
            raise CommandError("backup_sqlite only supports the configured SQLite database.")

        backup_dir = Path(settings.BACKUP_ROOT)
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = django_timezone.now().astimezone(timezone.utc).strftime(
                "%Y%m%dT%H%M%S.%fZ"
            )
            backup_path = backup_dir / f"db-{timestamp}.sqlite3"
            with backup_path.open("xb"):
                pass
        except OSError as exc:
            raise CommandError(f"Unable to create a backup file in {backup_dir}: {exc}") from exc

        backup_created = True
        try:
            connection.ensure_connection()
            source = connection.connection
            with closing(sqlite3.connect(str(backup_path), timeout=20)) as destination:
                source.backup(destination, pages=128, sleep=0.1)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise CommandError(
                        f"SQLite backup integrity check failed: {integrity[0] if integrity else 'no result'}"
                    )
        except (OSError, sqlite3.Error) as exc:
            if backup_created:
                backup_path.unlink(missing_ok=True)
            raise CommandError(f"SQLite backup failed: {exc}") from exc
        except CommandError:
            if backup_created:
                backup_path.unlink(missing_ok=True)
            raise

        self.stdout.write(self.style.SUCCESS(f"SQLite backup created: {backup_path}"))
