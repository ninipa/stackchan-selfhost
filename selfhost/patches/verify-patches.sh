#!/usr/bin/env bash
# 校验 selfhost/patches/ 下的挂载补丁没有被误改。
#
# 用法：
#   ./selfhost/patches/verify-patches.sh
#
# 说明：这是「文件有没有变」的校验，不是「上游镜像是否兼容」的校验。
# 升级镜像后仍要跑一次真实对话/OTA 验证；兼容性靠 pin digest + VERSIONS.md。
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

if [ ! -f patches/MANIFEST.sha256 ]; then
    echo "缺少 patches/MANIFEST.sha256" >&2
    exit 1
fi

shasum -a 256 -c patches/MANIFEST.sha256
