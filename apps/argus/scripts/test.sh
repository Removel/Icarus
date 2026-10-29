#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin="$repo_root/apps/argus/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    printf '%s\n' 'Argus environment is missing. Run: icarus install argus --dev' >&2
    exit 1
fi

container=''
cleanup() {
    if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -z "${ARGUS_TEST_DATABASE_URL:-}" ]; then
    container=$(docker run -d --rm --name "argus-test-pg-$$" \
        -e POSTGRES_PASSWORD=argus_test -e POSTGRES_USER=argus_test -e POSTGRES_DB=argus_test \
        -p 127.0.0.1::5432 "${ICARUS_POSTGRES_IMAGE:-pgvector/pgvector:pg17}")
    ready=false
    for ((i=0; i<30; i++)); do
        if docker exec "$container" pg_isready -U argus_test -d argus_test >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 1
    done
    if [ "$ready" != true ]; then
        printf '%s\n' 'Isolated Argus test PostgreSQL did not become ready' >&2
        exit 1
    fi
    binding=$(docker port "$container" 5432/tcp)
    export ARGUS_TEST_DATABASE_URL="postgresql+asyncpg://argus_test:argus_test@127.0.0.1:${binding##*:}/argus_test"
fi

cd "$repo_root"
if [ "$#" -gt 0 ]; then
    "$python_bin" -m pytest "$@"
else
    "$python_bin" -m pytest apps/argus/test -q
fi
"$python_bin" -m compileall -q apps/argus/src apps/argus/test packages
