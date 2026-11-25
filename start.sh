#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ "${DJANGO_ENV:-${APP_ENV:-}}" != "production" ]]; then
    echo "DJANGO_ENV must be production when starting the Render service." >&2
    exit 1
fi

STORAGE_MODE="${STORAGE_MODE:-persistent}"
case "$STORAGE_MODE" in
    persistent)
        if [[ "${DATA_DIR:-}" != "/var/data" ]]; then
            echo "DATA_DIR must be /var/data for persistent storage." >&2
            exit 1
        fi
        REQUIRE_MOUNT="1"
        ;;
    ephemeral)
        if [[ "${DATA_DIR:-}" != "/tmp/wildlens" ]]; then
            echo "DATA_DIR must be /tmp/wildlens for ephemeral storage." >&2
            exit 1
        fi
        mkdir -p "$DATA_DIR"
        REQUIRE_MOUNT="0"
        echo "WARNING: Ephemeral storage is enabled; database and media may be lost on restart or deploy." >&2
        ;;
    *)
        echo "STORAGE_MODE must be persistent or ephemeral." >&2
        exit 1
        ;;
esac

if [[ -z "${PORT:-}" ]]; then
    echo "Render PORT must be provided." >&2
    exit 1
fi

REQUIRE_MOUNT="$REQUIRE_MOUNT" python - <<'PY'
import os
import tempfile
from pathlib import Path

data_dir = Path(os.environ["DATA_DIR"])
if not data_dir.is_dir():
    raise SystemExit("Configured DATA_DIR does not exist.")
if os.environ["REQUIRE_MOUNT"] == "1" and not data_dir.is_mount():
    raise SystemExit("Render Persistent Disk is not mounted at /var/data.")
if not os.access(data_dir, os.W_OK):
    raise SystemExit("Configured DATA_DIR is not writable.")

try:
    with tempfile.NamedTemporaryFile(prefix=".wildlens-write-check-", dir=data_dir):
        pass
except OSError as exc:
    raise SystemExit(f"Render Persistent Disk is not writable: {exc}") from exc
PY

mkdir -p "$DATA_DIR/media" "$DATA_DIR/backups"
python manage.py migrate --noinput

WORKERS=2
if [[ "$STORAGE_MODE" == "ephemeral" ]]; then
    WORKERS=1
fi

exec gunicorn WildLensAI.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "$WORKERS" \
    --timeout 180 \
    --access-logfile - \
    --error-logfile -
