#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

python -m pip install -r requirements.txt

# Render's persistent disk is unavailable during builds; collect static files
# with the build-only settings mode and keep output in STATIC_ROOT.
DJANGO_ENV=build DEBUG=False python manage.py collectstatic --noinput
