#!/usr/bin/env bash
# StackChan 服务端体检：配置、渲染、模型、补丁、镜像、Docker、服务健康。
#
# 用法：
#   ./selfhost/tools/doctor.sh
#
# 退出码：0 = 全部通过；1 = 有失败项（warning 不算失败）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SELFHOST="$ROOT/selfhost"
FAIL=0
WARN=0

# 优先用工作区 venv（有 PyYAML），否则用系统 python3
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if [ -x "$ROOT/.venv/bin/python" ]; then
        PYTHON="$ROOT/.venv/bin/python"
    else
        PYTHON=python3
    fi
fi

ENV_FILE="$SELFHOST/deploy.env"
TARGET="$SELFHOST"
while [ $# -gt 0 ]; do
    case "$1" in
        --env-file)
            ENV_FILE="${2:?--env-file 需要路径}"
            shift 2
            ;;
        --target)
            TARGET="${2:?--target 需要目录}"
            shift 2
            ;;
        -h | --help)
            echo "用法: $0 [--env-file PATH] [--target DIR]"
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 2
            ;;
    esac
done

pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
section() { printf '\n%s\n' "$*"; }

section "1. 部署配置"
if [ -f "$ENV_FILE" ]; then
    pass "deploy.env 存在（${ENV_FILE}）"
else
    fail "缺少 ${ENV_FILE}（cp deploy.env.example deploy.env）"
fi

section "2. 渲染检查"
if "$SELFHOST/tools/render-config.sh" --env-file "$ENV_FILE" --check --target "$TARGET" >/tmp/chan-doctor-render.log 2>&1; then
    pass "render --check 通过"
else
    fail "render --check 失败（配置还没渲染，或 deploy.env 变了）："
    sed 's/^/    /' /tmp/chan-doctor-render.log | head -20
fi

section "3. YAML 解析"
if command -v "$PYTHON" >/dev/null 2>&1 && "$PYTHON" -c 'import yaml' >/dev/null 2>&1; then
    if "$PYTHON" -c "import yaml; yaml.safe_load(open('$TARGET/data/.config.yaml'))" >/dev/null 2>&1; then
        pass ".config.yaml 可解析"
    else
        fail ".config.yaml 解析失败"
    fi
else
    warn "PyYAML 未安装，跳过 YAML 解析"
fi

section "4. ASR 模型"
MODEL="$SELFHOST/models/SenseVoiceSmall/model.pt"
if [ -f "$MODEL" ]; then
    SIZE=$(stat -f%z "$MODEL" 2>/dev/null || stat -c%s "$MODEL" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt 100000000 ]; then
        pass "model.pt 存在（$((SIZE / 1024 / 1024)) MB）"
    else
        fail "model.pt 太小（$((SIZE / 1024 / 1024)) MB）"
    fi
else
    fail "缺少 models/SenseVoiceSmall/model.pt（见 models/README.md）"
fi

section "5. 补丁"
if [ -x "$SELFHOST/patches/verify-patches.sh" ]; then
    if "$SELFHOST/patches/verify-patches.sh" >/tmp/chan-doctor-patches.log 2>&1; then
        pass "补丁校验通过"
    else
        fail "补丁校验失败："
        sed 's/^/    /' /tmp/chan-doctor-patches.log | head -20
    fi
else
    warn "patches/verify-patches.sh 不存在"
fi

section "6. 镜像 tag"
if grep -qE 'image:.*:server_latest' "$SELFHOST/docker-compose.yml" 2>/dev/null; then
    warn "镜像 tag 是 server_latest（会漂移）；建议 pin digest"
else
    pass "镜像不是 server_latest"
fi

section "7. Docker"
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        pass "Docker 可用"
        if (cd "$SELFHOST" && docker compose config -q) >/dev/null 2>&1; then
            pass "compose 配置有效"
        else
            fail "compose 配置无效"
        fi
    else
        warn "Docker 守护进程不可用"
    fi
else
    warn "docker 命令不存在"
fi

section "8. 服务健康（可选）"
HOST=$(grep -E '^DEVICE_HOST=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
if [ -n "${HOST:-}" ]; then
    code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' -X POST \
        "http://$HOST:8003/xiaozhi/ota/" \
        -H 'Device-Id: doctor' -H 'Client-Id: doctor' \
        -H 'Content-Type: application/json' \
        -d '{"application":{"version":"1.6.2"},"board":{"type":"m5stack-stack-chan"}}' 2>/dev/null || true)
    if [ "$code" = "200" ]; then
        pass "OTA 200"
    else
        warn "OTA 返回 ${code:-无响应}（服务未起？）"
    fi

    code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8100/health" 2>/dev/null || true)
    if [ "$code" = "200" ]; then
        pass "behavior /health 200"
    else
        warn "behavior /health 返回 ${code:-无响应}（服务未起？）"
    fi
else
    warn "deploy.env 里没有 DEVICE_HOST，跳过健康检查"
fi

section "结果"
if [ "$FAIL" -eq 0 ]; then
    printf '  \033[32m全部通过\033[0m（warnings: %d）\n' "$WARN"
    exit 0
fi
printf '  \033[31m%d 项失败\033[0m（warnings: %d）\n' "$FAIL" "$WARN"
exit 1
