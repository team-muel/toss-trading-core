#!/usr/bin/env bash
# Run on personal-agent-vm after the candidate release has passed tests.
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

for command in sudo systemctl; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "required command is missing: ${command}" >&2
    exit 69
  fi
done

sudo install -d -m 0750 -o seoje -g seoje \
  /home/seoje/toss-trading/research-runtime
sudo install -d -m 0755 /etc/toss-trading
if [[ ! -f /etc/toss-trading/research.env ]]; then
  sudo install -m 0600 -o root -g root \
    deploy/systemd/research.env.example \
    /etc/toss-trading/research.env
fi

sudo systemd-analyze verify \
  deploy/systemd/toss-foundation.service \
  deploy/systemd/toss-research-automation@.service \
  deploy/systemd/toss-research-daily.timer \
  deploy/systemd/toss-research-weekly.timer
sudo install -m 0644 deploy/systemd/toss-foundation.service \
  /etc/systemd/system/toss-foundation.service
sudo install -m 0644 deploy/systemd/toss-research-automation@.service \
  /etc/systemd/system/toss-research-automation@.service
sudo install -m 0644 deploy/systemd/toss-research-daily.timer \
  /etc/systemd/system/toss-research-daily.timer
sudo install -m 0644 deploy/systemd/toss-research-weekly.timer \
  /etc/systemd/system/toss-research-weekly.timer
sudo install -m 0644 deploy/ops-agent/toss-foundation.yaml \
  /etc/google-cloud-ops-agent/config.yaml
sudo systemctl daemon-reload
sudo systemctl restart google-cloud-ops-agent
sudo systemctl enable --now \
  toss-research-daily.timer \
  toss-research-weekly.timer

sudo systemctl is-active google-cloud-ops-agent
sudo systemctl is-enabled toss-foundation.timer
sudo systemctl is-enabled toss-research-daily.timer
sudo systemctl is-enabled toss-research-weekly.timer
