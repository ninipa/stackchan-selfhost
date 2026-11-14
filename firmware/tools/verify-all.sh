#!/usr/bin/env bash
# 本地门禁：环境 + 构建 + host tests + lvsim + check-dist + gitleaks。
#
# 这不是 CI；是发版前/提交前自己跑的一套检查。
#
# 用法：
#   ./tools/verify-all.sh
#   ./tools/verify-all.sh --skip-build   # 只跑环境/测试/门禁
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKIP_BUILD=0
FAIL=0

for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        -h | --help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

step() { printf '\n== %s ==\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

step "1. 环境"
if "$DIR/tools/check-env.sh"; then ok "check-env"; else bad "check-env"; fi

if [ "$SKIP_BUILD" = 0 ]; then
    step "2. 构建"
    if "$DIR/tools/build.sh"; then ok "build"; else bad "build"; fi
else
    warn "跳过构建"
fi

step "3. host tests"
if [ -d "$DIR/tests" ]; then
    if cmake -S "$DIR/tests" -B "$DIR/build-host-tests" >/dev/null \
        && cmake --build "$DIR/build-host-tests" >/dev/null \
        && ctest --test-dir "$DIR/build-host-tests" --output-on-failure; then
        ok "host tests"
    else
        bad "host tests"
    fi
else
    warn "没有 tests/，跳过"
fi

step "4. lvsim（可选，macOS）"
LV="$DIR/../../tools/lvsim/run.sh"
[ -f "$LV" ] || LV="$DIR/../tools/lvsim/run.sh"
if [ -f "$LV" ]; then
    if bash "$LV" >/tmp/chan-lvsim.log 2>&1; then
        ok "lvsim"
    else
        bad "lvsim（日志 /tmp/chan-lvsim.log）"
    fi
else
    warn "没找到 tools/lvsim/run.sh，跳过"
fi

step "5. check-dist（可选）"
CHK="$DIR/../../tools/check-dist.sh"
[ -x "$CHK" ] || CHK="$DIR/../tools/check-dist.sh"
if [ -x "$CHK" ] && [ -n "${CHAN_DIST_TARGET:-}" ]; then
    if "$CHK" "$CHAN_DIST_TARGET"; then ok "check-dist"; else bad "check-dist"; fi
elif [ -x "$CHK" ]; then
    warn "check-dist 需要导出树：CHAN_DIST_TARGET=/path/to/public $CHK /path/to/public（跳过）"
else
    warn "没找到 tools/check-dist.sh，跳过"
fi

step "6. gitleaks（可选）"
if command -v gitleaks >/dev/null 2>&1; then
    GITLEAKS_ARGS=(detect --source "$DIR" --no-git --no-banner --redact)
    [ -f "$DIR/.gitleaks.toml" ] && GITLEAKS_ARGS+=(--config "$DIR/.gitleaks.toml")
    if gitleaks "${GITLEAKS_ARGS[@]}" >/tmp/chan-gitleaks.log 2>&1; then
        ok "gitleaks（当前工作树；历史扫描用完整 clone）"
    else
        bad "gitleaks（日志 /tmp/chan-gitleaks.log）"
    fi
else
    warn "gitleaks 未安装，跳过（brew install gitleaks）"
fi

step "结果"
if [ "$FAIL" -eq 0 ]; then
    printf '  \033[32m全部通过\033[0m\n'
    exit 0
fi
printf '  \033[31m%d 项失败\033[0m\n' "$FAIL"
exit 1
