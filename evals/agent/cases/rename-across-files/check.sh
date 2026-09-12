#!/usr/bin/env bash
# 判定：① 旧名字在整个目录里一次都不能剩 ② 验收脚本跑得通。
# 受保护文件（verify.py）由 runner 单独校验 —— 否则"改测试让它过"就是作弊。
set -u
if grep -rn "total_price" . --include=*.py > /dev/null; then
  echo "还有地方在叫 total_price:"
  grep -rn "total_price" . --include=*.py
  exit 1
fi
python3 verify.py
