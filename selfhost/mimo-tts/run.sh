#!/usr/bin/env bash
# 启动 MiMo TTS 适配层（前台运行，Ctrl-C 退出）
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

exec "${PYTHON:-python3}" server.py
