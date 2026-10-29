#!/usr/bin/env bash
# PostgreSQL entrypoint runs this only when initializing an empty cluster.
set -euo pipefail

create_database() {
  local role=$1 database=$2 password=$3
  if [ -z "$password" ]; then
    printf 'Missing password for database %s\n' "$database" >&2
    return 1
  fi
  psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    --set=role="$role" --set=database="$database" --set=password="$password" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'role', :'password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'database', :'role')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database') \gexec
SQL
}

create_database mem0_local mem0_app "${ICARUS_MEM0_POSTGRES_PASSWORD:?Set ICARUS_MEM0_POSTGRES_PASSWORD}"
create_database rela_mem rela_mem "${RELA_MEM_PG_PASSWORD:?Set RELA_MEM_PG_PASSWORD}"

# Extensions are per database and require admin installation before app access.
for database in mem0_app rela_mem; do
  psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" \
    -c 'CREATE EXTENSION IF NOT EXISTS vector'
done
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname rela_mem \
  -c 'CREATE SCHEMA IF NOT EXISTS rela_mem AUTHORIZATION rela_mem'
