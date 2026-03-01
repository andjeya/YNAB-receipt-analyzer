#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
APP_ROOT="${REPO_ROOT}/edge/pi-outbox-shipper"
INSTALL_ROOT="/opt/receipt-shipper"
CONFIG_DIR="/etc/receipt-shipper"
SERVICE_NAME="receipt-shipper.service"

mkdir -p "${INSTALL_ROOT}" "${CONFIG_DIR}"
rsync -a --exclude config/ "${APP_ROOT}/" "${INSTALL_ROOT}/"

python3 -m venv "${INSTALL_ROOT}/.venv"
"${INSTALL_ROOT}/.venv/bin/pip" install --upgrade pip
"${INSTALL_ROOT}/.venv/bin/pip" install "${INSTALL_ROOT}"

if [[ ! -f "${CONFIG_DIR}/shipper.yaml" ]]; then
  cp "${INSTALL_ROOT}/config/shipper.example.yaml" "${CONFIG_DIR}/shipper.yaml"
  echo "Created ${CONFIG_DIR}/shipper.yaml from example. Edit it before starting the service."
fi

cp "${INSTALL_ROOT}/systemd/${SERVICE_NAME}" "/etc/systemd/system/${SERVICE_NAME}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
echo "Installed ${SERVICE_NAME}. Start it with:"
echo "  systemctl start ${SERVICE_NAME}"
