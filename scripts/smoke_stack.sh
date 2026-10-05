#!/usr/bin/env bash
# Build the proposed upstream pair and exercise the actual Compose stack.
set -euo pipefail

sources_file="${1:-sources.env}"
source "$sources_file"
: "${TMX_REF:?Missing TMX_REF}"
: "${SERVER_REF:?Missing SERVER_REF}"

docker buildx build --platform linux/amd64 --load \
  --build-context "source=https://github.com/CourtHive/TMX.git#${TMX_REF}" \
  --file Dockerfile.web --tag tmx-smoke-web:local .
docker buildx build --platform linux/amd64 --load \
  --build-context "source=https://github.com/CourtHive/competition-factory-server.git#${SERVER_REF}" \
  --file Dockerfile.server --tag tmx-smoke-server:local .

export PG_PASSWORD="$(openssl rand -hex 32)"
export JWT_SECRET="$(openssl rand -hex 32)"
export HTTP_PORT=18080
export MANAGER_HTTP_PORT=18081
export BIND_ADDRESS=127.0.0.1
export MANAGER_BIND_ADDRESS=127.0.0.1
export MANAGER_COOKIE_SECURE=false
export PUBLIC_ORIGIN="http://${BIND_ADDRESS}:${HTTP_PORT}"

compose=(docker compose -p tmx-smoke -f compose.yaml -f tests/compose.smoke.yaml)
cleanup() {
  result=$?
  if (( result != 0 )); then
    "${compose[@]}" ps || true
    "${compose[@]}" logs --no-color --tail=100 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans || true
  exit "$result"
}
trap cleanup EXIT

"${compose[@]}" up --detach --wait --wait-timeout 240
admin_password="$(openssl rand -hex 24)"
"${compose[@]}" exec -T server node src/scripts/admin-user.mjs create \
  --email smoke@example.invalid --password "$admin_password"
SMOKE_PASSWORD="$admin_password" python3 tests/smoke_stack.py
