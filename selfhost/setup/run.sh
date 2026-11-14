#!/usr/bin/env bash
# StackChan 服务端配置页（宿主机，默认 127.0.0.1:8200）。
#
#   ./selfhost/setup/run.sh
#   SETUP_DRY_RUN=1 ./selfhost/setup/run.sh     # 只写文件，不重启
#   SETUP_REQUIRE_TOKEN=1 ./selfhost/setup/run.sh  # 多人共用宿主机时可选
#   SETUP_PORT=8201 ./selfhost/setup/run.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
# launchd / SSH 非交互 shell 的 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin，
# Docker CLI 常装在 /usr/local/bin 或 /opt/homebrew/bin；补上，否则页面保存报“docker 命令不存在”。
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/Applications/OrbStack.app/Contents/MacOS/xbin:${PATH}"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3

if ! "$PY" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
    echo "缺少 fastapi/uvicorn。安装：$PY -m pip install -r $DIR/requirements.txt" >&2
    exit 1
fi

exec "$PY" -m uvicorn app:app --app-dir "$DIR" \
    --host 127.0.0.1 --port "${SETUP_PORT:-8200}"
