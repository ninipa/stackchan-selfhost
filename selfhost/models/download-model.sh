#!/usr/bin/env bash
# 下载 SenseVoiceSmall 到 selfhost/models/SenseVoiceSmall/model.pt
#
# 用法：
#   ./selfhost/models/download-model.sh
#
# 依赖：pip install modelscope
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/selfhost/models/SenseVoiceSmall"
MODEL="$DEST/model.pt"

mkdir -p "$DEST"

if [ -f "$MODEL" ]; then
    SZ=$(stat -f%z "$MODEL" 2>/dev/null || stat -c%s "$MODEL" 2>/dev/null || echo 0)
    echo "已存在：$MODEL（$((SZ / 1024 / 1024)) MB）"
    exit 0
fi

if command -v modelscope >/dev/null 2>&1; then
    modelscope download --model iic/SenseVoiceSmall --local_dir "$DEST"
elif python3 -c 'import modelscope' >/dev/null 2>&1; then
    python3 - "$DEST" <<'PY'
import sys
from modelscope import snapshot_download

path = snapshot_download("iic/SenseVoiceSmall", local_dir=sys.argv[1])
print(path)
PY
else
    echo "缺少 modelscope。先运行：pip install modelscope" >&2
    exit 1
fi

if [ ! -f "$MODEL" ]; then
    echo "下载完成，但没有找到 $MODEL" >&2
    echo "检查 $DEST 下是否有 model.pt，必要时移动/改挂载路径。" >&2
    find "$DEST" -maxdepth 3 -name 'model.pt' -print 2>/dev/null || true
    exit 1
fi

SZ=$(stat -f%z "$MODEL" 2>/dev/null || stat -c%s "$MODEL" 2>/dev/null || echo 0)
echo "完成：$MODEL（$((SZ / 1024 / 1024)) MB）"
