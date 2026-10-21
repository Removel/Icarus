#!/usr/bin/env bash
# Build and push the four runtime images to Docker Hub.
#
# Usage:
#   scripts/publish-images.sh
#   DOCKER_NAMESPACE=removel IMAGE_TAG=2026.10.19 scripts/publish-images.sh
#
# The images are multi-arch and are pushed directly; no local image is loaded.
set -euo pipefail

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
namespace=${DOCKER_NAMESPACE:-removel}
image_tag=${IMAGE_TAG:-2026.10.19}
platforms=${PLATFORMS:-linux/amd64,linux/arm64}
# The Docker Desktop default builder can reach the configured registry network;
# override with BUILDX_BUILDER=name to use a dedicated builder instead.
builder=${BUILDX_BUILDER:-default}
progress=${BUILD_PROGRESS:-auto}
revision=$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || echo unknown)
source_url=${SOURCE_URL:-https://github.com/Removel/Icarus}

if ! docker buildx version >/dev/null 2>&1; then
  echo "docker buildx is required" >&2
  exit 1
fi

if docker buildx inspect "$builder" >/dev/null 2>&1; then
  docker buildx use "$builder"
else
  docker buildx create --name "$builder" --driver docker-container --use
fi
docker buildx inspect --bootstrap >/dev/null

build_push() {
  local name=$1
  local context=$2
  local dockerfile=$3
  echo "==> ${namespace}/icarus-${name}:latest and :${image_tag} (${platforms})"
  docker buildx build \
    --builder "$builder" \
    --platform "$platforms" \
    --progress "$progress" \
    -f "${repo_root}/${context}/${dockerfile}" \
    -t "${namespace}/icarus-${name}:latest" \
    -t "${namespace}/icarus-${name}:${image_tag}" \
    --label "org.opencontainers.image.source=${source_url}" \
    --label "org.opencontainers.image.revision=${revision}" \
    --label "org.opencontainers.image.version=${image_tag}" \
    --push \
    "${repo_root}/${context}"
}

build_push webui apps/webui Dockerfile
build_push mem0 apps/mem0 server/Dockerfile
build_push openkb apps/openkb Dockerfile.icarus
build_push rela-mem apps/rela-mem Dockerfile

echo "Published:"
for name in webui mem0 openkb rela-mem; do
  echo "  ${namespace}/icarus-${name}:latest"
  echo "  ${namespace}/icarus-${name}:${image_tag}"
done
