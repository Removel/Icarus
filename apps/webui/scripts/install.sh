#!/usr/bin/env bash
set -euo pipefail

app_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

case "${1:-}" in
  "") ;;
  --dev) ;;
  *) echo "Usage: $0 [--dev]" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--dev]" >&2
  exit 2
fi

if ! command -v node >/dev/null 2>&1; then
  echo "WebUI requires Node.js 22.12 or newer" >&2
  exit 1
fi
if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)'; then
  echo "WebUI requires Node.js 22.12 or newer" >&2
  exit 1
fi

# Honor the pnpm version pinned in package.json. A system pnpm matching the pin
# is used as-is (Linux and Windows may install pnpm normally); otherwise Corepack
# provides the pinned version without a global install.
pinned=$(node -p "require('$app_dir/package.json').packageManager" 2>/dev/null || true)
pinned_version=${pinned#pnpm@}
run_pnpm=()
shim_dir=

if command -v pnpm >/dev/null 2>&1 && [ "$(pnpm --version 2>/dev/null || true)" = "$pinned_version" ]; then
  run_pnpm=(pnpm)
elif command -v corepack >/dev/null 2>&1 && [ -n "$pinned_version" ]; then
  run_pnpm=(corepack pnpm)
  # `pnpm build` runs package scripts that call bare `pnpm` (for example
  # `pnpm --filter @icarus/shell build`), so keep those nested calls on the
  # pinned version too instead of whatever pnpm is installed first.
  shim_dir=$(mktemp -d)
  trap 'rm -rf "$shim_dir"' EXIT
  printf '%s\n' '#!/usr/bin/env bash' 'exec corepack pnpm "$@"' > "$shim_dir/pnpm"
  chmod +x "$shim_dir/pnpm"
  export PATH="$shim_dir:$PATH"
elif command -v pnpm >/dev/null 2>&1 && [ -z "$pinned_version" ]; then
  # No pinned version in package.json: any installed pnpm is acceptable.
  run_pnpm=(pnpm)
else
  echo "WebUI requires pnpm $pinned_version (install it, or enable Corepack)" >&2
  exit 1
fi

cd -- "$app_dir"
"${run_pnpm[@]}" install --frozen-lockfile
"${run_pnpm[@]}" build
