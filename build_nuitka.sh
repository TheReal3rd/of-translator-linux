#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ROOT}/venv"
OUT_DIR="${ROOT}/dist"
APP_NAME="overfield-translator"

cd "${ROOT}"

if [[ ! -d "${VENV}" ]]; then
  python3 -m venv "${VENV}"
fi

"${VENV}/bin/python" -m pip install -q --upgrade pip
"${VENV}/bin/python" -m pip install -q -r requirements.txt
"${VENV}/bin/python" -m pip install -q -r requirements-build.txt

mkdir -p "${OUT_DIR}"

echo "Building single-file binary with Nuitka..."

"${VENV}/bin/python" -m nuitka \
  --standalone \
  --onefile \
  --assume-yes-for-downloads \
  --output-dir="${OUT_DIR}" \
  --output-filename="${APP_NAME}" \
  --enable-plugin=pyqt5 \
  --include-module=net_pb2 \
  --include-module=msg_id \
  --include-module=prompt_templates \
  --include-module=lang_utils \
  --include-module=HailoAPI \
  --include-module=TranslatorManager \
  --include-module=gui \
  --include-package=googletrans \
  --include-package=httpx \
  --include-package=langdetect \
  --include-package-data=langdetect \
  --include-package=libretranslatepy \
  --include-package=scapy \
  --nofollow-import-to=libretranslate \
  --nofollow-import-to=flask \
  --nofollow-import-to=argostranslate \
  --nofollow-import-to=tests \
  --nofollow-import-to=pytest \
  --nofollow-import-to=unittest \
  --remove-output \
  main.py

echo ""
echo "Build complete: ${OUT_DIR}/${APP_NAME}"
echo "Run with: sudo ${OUT_DIR}/${APP_NAME}"
