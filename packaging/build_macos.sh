#!/usr/bin/env bash
# 로컬에서 메뉴바 .app 빌드 (격리된 venv 사용 — 시스템 파이썬 안 건드림)
#   사용:  bash packaging/build_macos.sh
#   결과:  dist/KO Focus Switch.app  +  dist/KO-Focus-Switch-macos.zip
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV=".build-venv"
echo "[1/4] 빌드용 venv 준비: $VENV"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip >/dev/null
pip install "pyinstaller>=6.0" "rumps>=0.4.0"

echo "[2/4] 이전 산출물 정리"
rm -rf build dist

echo "[3/4] PyInstaller 빌드"
pyinstaller --noconfirm packaging/KoFocusSwitch.spec

echo "[4/4] 배포용 zip 생성 (심볼릭/권한 보존)"
( cd dist && ditto -c -k --sequesterRsrc --keepParent "KO Focus Switch.app" "KO-Focus-Switch-macos.zip" )

echo
echo "완료:"
echo "  앱   : $ROOT/dist/KO Focus Switch.app"
echo "  배포 : $ROOT/dist/KO-Focus-Switch-macos.zip"
echo
echo "처음 실행은 Gatekeeper 때문에 우클릭 → 열기 (서명/공증 안 했을 경우)."
