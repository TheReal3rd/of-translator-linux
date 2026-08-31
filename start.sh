#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

BIN="${ROOT}/dist/overfield-translator"
PY="${ROOT}/venv/bin/python"
MAIN="${ROOT}/main.py"
CONFIG="${ROOT}/configData.json"
RUNTIME_DIR="/tmp/overfield-translator-runtime"

XAUTH="${XAUTHORITY:-${HOME}/.Xauthority}"
INVOKING_USER="${SUDO_USER:-${USER:-$(id -un)}}"

# Qt running as root cannot use /run/user/1000 from the caller's session.
sudo install -d -m 700 -o root -g root "${RUNTIME_DIR}"

RUN=(
  sudo
  env
  "HOME=${HOME}"
  "USER=${INVOKING_USER}"
  "LOGNAME=${INVOKING_USER}"
  "SUDO_USER=${INVOKING_USER}"
  "DISPLAY=${DISPLAY:-}"
  "XAUTHORITY=${XAUTH}"
  "PATH=${PATH}"
  "LANG=${LANG:-en_US.UTF-8}"
  "OVERFIELD_CONFIG=${CONFIG}"
  "OVERFIELD_ROOT=${ROOT}"
  "XDG_RUNTIME_DIR=${RUNTIME_DIR}"
)

# Packet capture requires root. Do not use sudo -E.
# Default to Python so config changes are picked up immediately.
# Set OVERFIELD_USE_BINARY=1 after rebuilding with ./build_nuitka.sh
if [[ "${OVERFIELD_USE_BINARY:-0}" == "1" && -x "${BIN}" ]]; then
  cp -f "${CONFIG}" "${ROOT}/dist/configData.json" 2>/dev/null || true
  exec "${RUN[@]}" "${BIN}" "$@"
fi

if [[ ! -x "${PY}" ]]; then
  echo "Missing ${PY}. Create the venv or set OVERFIELD_USE_BINARY=1." >&2
  exit 1
fi

exec "${RUN[@]}" "${PY}" "${MAIN}" "$@"
