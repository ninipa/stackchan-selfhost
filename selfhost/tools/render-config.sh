#!/usr/bin/env bash
# 渲染 StackChan 服务端配置（.config.yaml / behavior .env / prompt 模板）。
#
# 用法：
#   ./selfhost/tools/render-config.sh --check
#   ./selfhost/tools/render-config.sh --write
#   ./selfhost/tools/render-config.sh --print
#   ./selfhost/tools/render-config.sh --firmware-snippet
#
# 依赖：python3 >= 3.9（zoneinfo）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: 找不到 $PYTHON" >&2
    exit 1
fi

exec "$PYTHON" "$ROOT/selfhost/tools/render.py" "$@"
