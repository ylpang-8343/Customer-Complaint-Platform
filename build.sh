#!/usr/bin/env bash
# The build step on Render (see render.yaml): install, gather the static files, update the database, load demo data.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate --no-input

# An administrator (for the audit log and for creating users), if these are set in the host's environment:
# DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD. Safe to repeat on every deploy.
if [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  python manage.py createsuperuser --noinput || echo "The administrator already exists."
fi

if [ "${DEMO_DATA:-0}" = "1" ]; then
  # The reference data, the demo users and twelve demo complaints (skipped if the database already has complaints).
  python manage.py seed --demo --complaints
else
  # Only the reference data: the five factories, the complaint types and the lab tests.
  python manage.py seed
fi
