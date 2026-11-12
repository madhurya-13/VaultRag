#!/bin/sh
set -eu

for database in vaultrag vaultrag_test; do
  psql --username "$POSTGRES_USER" --dbname "$database" \
    --set=app_password="$APP_DB_PASSWORD" \
    --set=migrator_password="$MIGRATOR_DB_PASSWORD" \
    --file=/bootstrap/bootstrap_roles.sql
done
