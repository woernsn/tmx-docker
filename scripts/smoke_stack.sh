#!/usr/bin/env bash
# Build the pinned upstream sources and exercise the actual Compose stack.
set -euo pipefail

sources_file="${1:-sources.env}"
source "$sources_file"
: "${TMX_REF:?Missing TMX_REF}"
: "${SERVER_REF:?Missing SERVER_REF}"
: "${PUB_REF:?Missing PUB_REF}"
: "${HIVEID_REF:?Missing HIVEID_REF}"

docker buildx build --platform linux/amd64 --load \
  --build-context "source=https://github.com/CourtHive/TMX.git#${TMX_REF}" \
  --file Dockerfile.web --tag tmx-smoke-web:local .
docker buildx build --platform linux/amd64 --load \
  --build-context "source=https://github.com/CourtHive/competition-factory-server.git#${SERVER_REF}" \
  --file Dockerfile.server --tag tmx-smoke-server:local .
docker buildx build --platform linux/amd64 --load \
  --build-context "source=https://github.com/CourtHive/courthive-public.git#${PUB_REF}" \
  --file Dockerfile.pub --tag tmx-smoke-pub:local .
docker buildx build --platform linux/amd64 --load \
  --build-context "source=https://github.com/CourtHive/courthive-hiveid.git#${HIVEID_REF}" \
  --file Dockerfile.hiveid --tag tmx-smoke-hiveid:local .

export PG_PASSWORD="$(openssl rand -hex 32)"
export JWT_SECRET="$(openssl rand -hex 32)"
export HIVEID_SIGNER_TOKEN="$(openssl rand -hex 32)"
export JWT_KID=smoke-current
private_key_file="$(mktemp)"
public_key_file="$(mktemp)"
openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out "$private_key_file"
openssl ec -in "$private_key_file" -pubout -out "$public_key_file"
export JWT_PRIVATE_KEY="$(awk 'BEGIN { ORS="" } { print $0 "\\n" }' "$private_key_file")"
export JWT_PUBLIC_KEY="$(awk 'BEGIN { ORS="" } { print $0 "\\n" }' "$public_key_file")"
export HTTP_PORT=18080
export BIND_ADDRESS=127.0.0.1
export MANAGER_COOKIE_SECURE=false
export PUBLIC_ORIGIN="http://${BIND_ADDRESS}:${HTTP_PORT}"

compose=(docker compose -p tmx-smoke -f compose.yaml -f tests/compose.smoke.yaml)
cleanup() {
  result=$?
  rm -f "$private_key_file" "$public_key_file"
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
