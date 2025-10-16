#!/bin/sh
set -e

# Directories and optional overrides.
MEDIA_ROOT=${DJANGO_MEDIA_ROOT:-/app/media}
DB_HOST=${DJANGO_DB_HOST:-}
DB_PORT=${DJANGO_DB_PORT:-}
PROJECT_ROOT=${DJANGO_PROJECT_ROOT:-/app/taskmanager}
AUTO_IMPORT=${DJANGO_AUTO_IMPORT_DUMP:-1}
FIXTURE_PATH=${DJANGO_FIXTURE_PATH:-/app/backup.json}
PREPARED_FIXTURE=${DJANGO_PREPARED_FIXTURE_PATH:-/app/backup_prepared.json}

# Wait for the database if host/port provided.
if [ -n "$DB_HOST" ] && [ -n "$DB_PORT" ]; then
  echo "Waiting for database at ${DB_HOST}:${DB_PORT}..."
  until nc -z "$DB_HOST" "$DB_PORT"; do
    sleep 1
  done
fi

# Ensure media directory exists and seed blank.docx if present.
mkdir -p "$MEDIA_ROOT"
if [ -f "/app/blank.docx" ] && [ ! -f "${MEDIA_ROOT}/blank.docx" ]; then
  cp "/app/blank.docx" "${MEDIA_ROOT}/blank.docx"
fi

# Move to project root so manage.py commands work.
if [ -d "$PROJECT_ROOT" ]; then
  cd "$PROJECT_ROOT"
else
  echo "Project root ${PROJECT_ROOT} not found; using /app"
  cd /app
fi

python manage.py makemigrations --noinput
# Apply migrations.
python manage.py migrate --noinput

# Optionally load fixture data if database is empty.
if [ "$AUTO_IMPORT" = "1" ]; then
  if [ -f "$FIXTURE_PATH" ]; then
    echo "Preparing data fixture from ${FIXTURE_PATH}..."
    python <<'PY'
import json
import os
import sys
from pathlib import Path

fixture_path = Path(os.environ.get("DJANGO_FIXTURE_PATH", "/app/backup.json"))
prepared_path = Path(os.environ.get("DJANGO_PREPARED_FIXTURE_PATH", "/app/backup_prepared.json"))

raw = fixture_path.read_bytes()
if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
    text = raw.decode("utf-16")
elif raw.startswith(b"\xef\xbb\xbf"):
    text = raw[3:].decode("utf-8")
else:
    text = raw.decode("utf-8")

try:
    data = json.loads(text)
except json.JSONDecodeError as exc:
    print(f"Failed to parse fixture {fixture_path}: {exc}", file=sys.stderr)
    sys.exit(1)

exclude = {"contenttypes.contenttype", "auth.permission"}
filtered = [obj for obj in data if obj.get("model") not in exclude]
prepared_path.write_text(json.dumps(filtered, ensure_ascii=False), encoding="utf-8")
print(f"Fixture prepared with {len(filtered)} objects (removed {len(data) - len(filtered)}).")
PY

    # Import only if there are no users yet (assumes empty DB).
    if python <<'PY'
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "taskmanager.settings")
import django
django.setup()
from django.contrib.auth import get_user_model

sys.exit(0 if not get_user_model().objects.exists() else 1)
PY
    then
      echo "Database appears empty; importing fixture ${PREPARED_FIXTURE}..."
      if python manage.py loaddata "$PREPARED_FIXTURE"; then
        echo "Fixture import completed."
      else
        echo "Fixture import failed. Continuing without restored data." >&2
      fi
    else
      echo "Existing data detected; skipping automatic fixture import."
    fi
  else
    echo "Expected fixture ${FIXTURE_PATH} not found; skipping automatic import."
  fi
else
  echo "Automatic fixture import disabled (DJANGO_AUTO_IMPORT_DUMP=${AUTO_IMPORT})."
fi

# Ensure superuser exists.
python <<'PY'
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "taskmanager.settings")
import django
django.setup()
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin1")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin")

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f"Created superuser '{username}'.")
else:
    print(f"Superuser '{username}' already exists.")
PY

# Collect static files (idempotent).
python manage.py collectstatic --noinput

cd /app

exec "$@"
