#!/bin/sh
set -eu
umask 077

data_dir="${MEPPP_DATA_DIR:-/data}"
database_path="${data_dir}/meppp.sqlite3"

if [ "${MEPPP_BACKUP_BEFORE_MIGRATE:-1}" = "1" ] && [ -f "$database_path" ]; then
    python manage.py backup_sqlite \
        --database "$database_path" \
        --backup-dir "${MEPPP_BACKUP_DIR:-${data_dir}/backups/sqlite}" \
        --daily "${MEPPP_BACKUP_DAILY:-7}" \
        --weekly "${MEPPP_BACKUP_WEEKLY:-4}"
fi

python manage.py migrate --noinput
python manage.py bootstrap_roles
python manage.py collectstatic --noinput

exec "$@"
