#!/usr/bin/env bash
# Drop and recreate dev + test databases. Destructive!
set -euo pipefail

PG_DB_DEV="lsd_dev"
PG_DB_TEST="lsd_test"

echo "→ Dropping databases..."
sudo -u postgres dropdb --if-exists "${PG_DB_DEV}"
sudo -u postgres dropdb --if-exists "${PG_DB_TEST}"
echo "→ Recreating..."
bash "$(dirname "$0")/db-create.sh"
