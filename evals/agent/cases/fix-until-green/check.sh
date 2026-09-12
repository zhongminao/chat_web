#!/usr/bin/env bash
# 判定：自检脚本必须真的跑通。受保护文件由 runner 单独校验（见 protected.txt）。
set -u
python3 check_config.py
