#!/usr/bin/env bash
# run_capture_7a2e4f.sh —— 可靠捕获被调用命令退出码的包装器。
#
# 解决的问题：把命令接在 `; echo "$?"` 或管道后面时，shell 返回的是最后一条
# 命令（echo / 管道右端）的退出码，被测命令的真实失败会被吃掉。本包装器：
#   1) 先把 stdout+stderr 落盘到日志文件；
#   2) 立刻取走被测命令的退出码；
#   3) 打印日志、追加一行机器可读的 exit_code 记录；
#   4) 用被测命令的原始退出码退出（不掩盖失败）。
#
# 用法:
#   ./run_capture_7a2e4f.sh [--tag 名称] -- 命令 [参数...]
#
# 可用环境变量:
#   CAPTURE_DIR  日志目录（默认 /tmp/run-capture-7a2e4f）
#
# 唯一命名，避免覆盖工作区已有 helper 脚本。
set -uo pipefail

usage() {
    echo "用法: $0 [--tag 名称] -- 命令 [参数...]" >&2
}

tag="run"
if [ "${1:-}" = "--tag" ]; then
    [ "$#" -ge 3 ] || { usage; exit 64; }
    tag="$2"
    shift 2
fi
if [ "${1:-}" = "--" ]; then
    shift 1
fi
if [ "$#" -eq 0 ]; then
    usage
    exit 64
fi

capture_dir="${CAPTURE_DIR:-/tmp/run-capture-7a2e4f}"
mkdir -p "$capture_dir"
stamp="$(date +%Y%m%d-%H%M%S)"
safe_tag="$(printf '%s' "$tag" | tr -c 'A-Za-z0-9._-' '_')"
log="$capture_dir/${stamp}_${safe_tag}_$$.log"

# 关键：先重定向落盘，再捕获退出码；不要用管道（管道返回右端退出码）。
set +e
"$@" >"$log" 2>&1
code=$?
set -e

cat "$log"

# 机器可读记录，便于后续 grep / 汇总
printf '\n[run_capture_7a2e4f] tag=%s exit_code=%d log=%s\n' "$tag" "$code" "$log"
printf '{"tag":"%s","exit_code":%d,"log":"%s"}\n' "$tag" "$code" "$log" >>"$log"

# 原样透出被测命令的退出码
exit "$code"
