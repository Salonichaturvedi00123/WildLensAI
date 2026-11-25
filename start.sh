#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ "${DJANGO_ENV:-${APP_ENV:-}}" != "production" ]]; then
    echo "DJANGO_ENV must be production when starting the Render service." >&2
    exit 1
fi

if [[ "${DATA_DIR:-}" != "/var/data" ]]; then
    echo "DATA_DIR must be set to the mounted Render disk at /var/data." >&2
    exit 1
fi

python - <<'PY'
import os
import tempfile
from pathlib import Path

data_dir = Path(os.environ["DATA_DIR"])
if not data_dir.is_dir() or not data_dir.is_mount():
    raise SystemExit("Render Persistent Disk is not mounted at /var/data.")

try:
    with tempfile.NamedTemporaryFile(prefix=".wildlens-write-check-", dir=data_dir):
        pass
except OSError as exc:
    raise SystemExit(f"Render Persistent Disk is not writable: {exc}") from exc
PY

mkdir -p "$DATA_DIR/media" "$DATA_DIR/backups"
python manage.py migrate --noinput

exec gunicorn WildLensAI.wsgi:application \
    --bind "0.0.0.0:${PORT:-10000}" \
    --workers 2 \
    --timeout 180 \
    --access-logfile - \
    --error-logfile -
