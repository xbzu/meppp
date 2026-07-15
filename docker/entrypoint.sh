#!/bin/sh
set -eu
umask 077

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
