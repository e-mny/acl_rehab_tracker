#!/usr/bin/env bash
# Install on the Linux VM/LXC running Docker, not the Proxmox hypervisor.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Run this installer with sudo on the Docker host.' >&2
  exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
deploy_dir=$(realpath -- "${1:-$source_dir/..}")
if [[ $deploy_dir == *$'\n'* || $deploy_dir == *$'\r'* ]]; then
  echo 'The deployment directory must not contain newline characters.' >&2
  exit 1
fi
for command in docker python3 flock systemctl; do
  if ! command -v "$command" >/dev/null; then
    echo "Install $command before enabling updates." >&2
    exit 1
  fi
done
if [[ ! -f $deploy_dir/.env || ! -f $deploy_dir/compose.production.yaml ]]; then
  echo 'Prepare .env and compose.production.yaml in the deployment directory first.' >&2
  exit 1
fi
docker compose --env-file "$deploy_dir/.env" --file "$deploy_dir/compose.production.yaml" config --quiet
install -d -m 0755 /usr/local/lib/stride
install -m 0755 "$source_dir/update.sh" /usr/local/lib/stride/update.sh
install -m 0644 "$source_dir/stride-update.service" /etc/systemd/system/stride-update.service
install -m 0644 "$source_dir/stride-update.timer" /etc/systemd/system/stride-update.timer
escaped_dir=${deploy_dir//\\/\\\\}
escaped_dir=${escaped_dir//\"/\\\"}
umask 077
printf 'STRIDE_DEPLOY_DIR="%s"\n' "$escaped_dir" > /etc/stride-updater.conf
systemctl daemon-reload
systemctl enable --now stride-update.timer
echo 'Automatic updates are enabled. Inspect runs with: journalctl -u stride-update.service'
