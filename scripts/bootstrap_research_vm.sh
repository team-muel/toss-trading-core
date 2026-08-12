#!/usr/bin/env bash
# Run once through an OS Login administrator on a clean Ubuntu research VM.
set -Eeuo pipefail
umask 077

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
else
  SUDO=(sudo)
fi

if ! id seoje >/dev/null 2>&1; then
  "${SUDO[@]}" useradd --create-home --shell /bin/bash seoje
fi
"${SUDO[@]}" apt-get update
"${SUDO[@]}" DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl git python3 python3-pip python3-venv util-linux

if ! command -v gcloud >/dev/null 2>&1; then
  "${SUDO[@]}" snap install google-cloud-cli --classic
fi
if ! dpkg-query -W google-cloud-ops-agent >/dev/null 2>&1; then
  temporary="$(mktemp -d)"
  trap 'rm -rf -- "${temporary}"' EXIT
  curl -fsSLo "${temporary}/add-google-cloud-ops-agent-repo.sh" \
    https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
  "${SUDO[@]}" bash "${temporary}/add-google-cloud-ops-agent-repo.sh" \
    --also-install --version=2.*.*
fi

"${SUDO[@]}" install -d -m 0755 -o seoje -g seoje \
  /home/seoje/toss-trading \
  /home/seoje/toss-trading/releases
"${SUDO[@]}" systemctl is-active google-cloud-ops-agent
echo "research_vm_bootstrap=ok runtime_user=seoje"
