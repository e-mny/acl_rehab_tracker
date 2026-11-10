#!/usr/bin/env bash
# Pull/reconcile one complete image release without changing journal volumes.
set -euo pipefail
umask 077
for command in docker python3 flock; do
  if ! command -v "$command" >/dev/null; then
    echo "Install $command before running the updater on the Linux Docker host." >&2
    exit 1
  fi
done

deploy_dir=${STRIDE_DEPLOY_DIR:-/opt/stride}
cd -- "$deploy_dir"
if [[ ! -f .env || ! -f compose.production.yaml ]]; then
  echo 'Missing .env or compose.production.yaml in STRIDE_DEPLOY_DIR.' >&2
  exit 1
fi
mkdir -p .deploy-state
exec 9>.deploy-state/update.lock
flock -n 9 || exit 0

compose() {
  docker compose --env-file .env --file compose.production.yaml "$@"
}

# Only emit deployment metadata; the resolved configuration contains credentials.
metadata=$(compose config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
for name, service in config["services"].items():
    if name not in ("stride", "telegram", "pose") or "build" in service:
        sys.exit("Expected the image-only Stride production configuration.")
    print("service", name, service["image"], sep="\t")
for key, volume in config["volumes"].items():
    if key not in ("stride_data", "telegram_data") or not volume.get("external"):
        sys.exit("Production volumes must be external.")
    print("volume", key, volume["name"], sep="\t")
')

# Fail before pulling/recreating anything when a volume is missing or differs
# from an existing container mount in this Compose project.
while IFS=$'\t' read -r kind key value; do
  if [[ $kind != volume ]]; then continue; fi
  docker volume inspect "$value" >/dev/null
  if [[ $key == stride_data ]]; then
    service=stride
    destination=/data
  else
    service=telegram
    destination=/bot-data
  fi
  container=$(compose ps -a -q "$service")
  if [[ -n $container ]]; then
    mounted=$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"$destination\"}}{{.Name}}{{end}}{{end}}" "$container")
    if [[ $mounted != "$value" ]]; then
      echo "Refusing to change the existing $service data volume. Check the production volume settings." >&2
      exit 1
    fi
  fi
done <<< "$metadata"

# Pull all enabled services first. Failures here leave running containers alone.
compose pull --policy always
release=''
while IFS=$'\t' read -r kind service image; do
  if [[ $kind != service ]]; then continue; fi
  revision=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")
  if [[ ! $revision =~ ^[0-9a-f]{40}$ ]]; then
    echo "Missing release revision on $service image; refusing the update." >&2
    exit 1
  fi
  if [[ -n $release && $revision != "$release" ]]; then
    echo 'Registry release is still being promoted; images have different revisions. Retrying on the next timer run.' >&2
    exit 1
  fi
  release=$revision
done <<< "$metadata"
if [[ -z $release ]]; then
  echo 'No enabled images were found.' >&2
  exit 1
fi

container=$(compose ps -a -q stride)
if [[ -n $container ]]; then
  previous=$(docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$container")
  if [[ $previous =~ ^[0-9a-f]{40}$ && $previous != "$release" ]]; then
    printf '%s\n' "$previous" > .deploy-state/previous-release
  fi
fi

# Compose recreates services only when their image or configuration changed.
# Always reconcile so edits to local settings also take effect.
if ! compose up -d --no-build --pull never --wait --wait-timeout 180; then
  echo 'Deployment did not become healthy. Inspect Compose status/logs; no data or images were removed.' >&2
  exit 1
fi
printf '%s\n' "$release" > .deploy-state/current-release
printf 'Deployment is running revision %s.\n' "$release"
