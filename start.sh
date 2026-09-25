#!/usr/bin/env bash
set -e
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py bootstrap_admin
exec gunicorn deriv_insight.wsgi:application --bind 0.0.0.0:${PORT:-10000} --workers 2 --timeout 120
