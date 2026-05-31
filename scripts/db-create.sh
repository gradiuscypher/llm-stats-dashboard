#!/usr/bin/env bash
# Create the lsd_user role and dev/test databases. Idempotent.
set -euo pipefail

PG_USER="lsd_user"
PG_PASS="lsd_pass"
PG_DB_DEV="lsd_dev"
PG_DB_TEST="lsd_test"

echo "→ Creating role ${PG_USER}..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${PG_USER}') THEN
    CREATE ROLE ${PG_USER} LOGIN PASSWORD '${PG_PASS}';
    RAISE NOTICE 'Role created.';
  ELSE
    RAISE NOTICE 'Role already exists, skipping.';
  END IF;
END
\$\$;
SQL

echo "→ Creating databases..."
sudo -u postgres createdb -O "${PG_USER}" "${PG_DB_DEV}"  2>/dev/null && echo "  Created ${PG_DB_DEV}" || echo "  ${PG_DB_DEV} already exists"
sudo -u postgres createdb -O "${PG_USER}" "${PG_DB_TEST}" 2>/dev/null && echo "  Created ${PG_DB_TEST}" || echo "  ${PG_DB_TEST} already exists"
echo "→ Done."
