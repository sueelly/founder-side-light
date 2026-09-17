#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
unset CLAUDECODE
export FOUNDER_SETUP_OPENED=1
if [[ -f setup.md ]]; then open -t setup.md; fi
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Windows에서는 실행.bat를 여세요.'
  exit 1
fi
mkdir -p .bootstrap/bin
export UV_INSTALL_DIR="$PWD/.bootstrap/bin"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_INSTALL_DIR="$PWD/.bootstrap/python"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
if [[ ! -x .bootstrap/bin/uv ]]; then
  curl -fLsS https://astral.sh/uv/install.sh -o .bootstrap/install-uv.sh
  sh .bootstrap/install-uv.sh
fi
if ! .bootstrap/bin/uv run --no-project --python 3.12 python bootstrap.py --uv "$PWD/.bootstrap/bin/uv"; then
  read -r -p '설정 오류를 확인한 뒤 Enter를 누르세요. 다시 실행하면 이어서 설치합니다.' founder_retry
  exit 1
fi
