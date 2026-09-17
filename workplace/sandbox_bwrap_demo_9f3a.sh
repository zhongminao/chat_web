#!/usr/bin/env bash
# 最简隔离沙箱 demo —— 基于 bubblewrap (bwrap) + Linux namespaces
# 用法: ./sandbox_bwrap_demo_9f3a.sh [--net] <命令> [参数...]
#   默认: 断网、只读根、独立 PID/UTS/IPC，无宿主 /home 可见
#   --net: 保留网络（外网可用，但仍与宿主进程/PID 隔离）
set -euo pipefail

WITH_NET=0
if [[ "${1:-}" == "--net" ]]; then WITH_NET=1; shift; fi
CMD=("$@"); [[ ${#CMD[@]} -eq 0 ]] && CMD=(bash)

# 沙箱里唯一可写的地方：一个 tmpfs，退出即销毁
RO=(--ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 \
    --ro-bind /bin /bin --ro-bind /sbin /sbin --ro-bind /etc /etc)

# 只读引用宿主的代码/数据（“调用外部环境”），但不给写权限
# 例: --ro-bind "$HOME/project" /work

NET=()
[[ $WITH_NET -eq 0 ]] && NET=(--unshare-net)

exec bwrap \
  "${RO[@]}" \
  --proc /proc --dev /dev \
  --tmpfs /tmp --tmpfs /run \
  --bind "$(mktemp -d)" /work \
  --unshare-user --unshare-pid --unshare-uts --unshare-ipc "${NET[@]}" \
  --die-with-parent \
  --chdir /work \
  -- "${CMD[@]}"
