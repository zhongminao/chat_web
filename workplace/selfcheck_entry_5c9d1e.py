#!/usr/bin/env python3
"""统一自检入口：把前两轮的两份探测脚本合并成一个可复用入口。

复用而非重写：
  - plan_probe_f7c2a1.py  提供 fib() 与 scan_texts()（斐波那契 + 文本统计）
  - plan_tool_drill_b4d8e2.py 提供分阶段检查（环境/清单/gcd/注入失败）

额外做一件前两轮没做的事：把本轮统计结果与上一轮产出的
plan_probe_report_f7c2a1.json 做交叉比对，确认历史报告仍与现状一致。

用法:
  python3 selfcheck_entry_5c9d1e.py            # 正常路径
  python3 selfcheck_entry_5c9d1e.py --fail     # 注入失败，用于验证退出码
  python3 selfcheck_entry_5c9d1e.py --json     # 仅输出 JSON

退出码：0 = 全部通过；1 = 有检查项失败。

唯一命名 5c9d1e，避免覆盖工作区已有脚本。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = HERE / "selfcheck_report_5c9d1e.json"
PREV_REPORT = HERE / "plan_probe_report_f7c2a1.json"
TEXT_DIR = HERE / "narr-probe"

MODULE_SOURCES = {
    "probe": HERE / "plan_probe_f7c2a1.py",
    "drill": HERE / "plan_tool_drill_b4d8e2.py",
}


def load_module(name: str, path: Path):
    """按路径加载已有脚本为模块（不改动、不重写它们）。

    注意：必须先注册进 sys.modules 再 exec_module。否则 @dataclass 在注册
    数据类时会执行 sys.modules.get(cls.__module__).__dict__，拿到 None 而
    抛 AttributeError: 'NoneType' object has no attribute '__dict__'。
    """
    modname = f"ws_{name}_{path.stem}"
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(modname, None)
        raise
    return mod


def sha256_12(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def collect(mode_fail: bool) -> tuple[list[dict], list[dict]]:
    """返回 (模块来源指纹, 检查组列表)。"""
    sources = []
    for name, path in MODULE_SOURCES.items():
        if not path.is_file():
            raise SystemExit(f"缺少依赖脚本: {path.name}")
        sources.append(
            {"role": name, "file": path.name, "bytes": path.stat().st_size, "sha256_12": sha256_12(path)}
        )

    probe = load_module("probe", MODULE_SOURCES["probe"])
    drill = load_module("drill", MODULE_SOURCES["drill"])

    groups: list[dict] = []

    # --- 组 1: 复用 probe.fib 的算法自检 ---
    seq = probe.fib(10)
    checks = [
        {
            "name": "fib(10) 第 10 项 == 34",
            "passed": seq[-1] == 34,
            "detail": f"实得 {seq[-1]}",
        },
        {
            "name": "递推关系成立",
            "passed": all(seq[i] + seq[i + 1] == seq[i + 2] for i in range(len(seq) - 2)),
            "detail": "相邻两项之和等于下一项",
        },
        {
            "name": "序列单调不减",
            "passed": all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1)),
            "detail": f"{len(seq)} 项",
        },
    ]
    groups.append({"title": "复用 probe 模块：斐波那契", "checks": checks})

    # --- 组 2: 复用 probe.scan_texts + 与上一轮报告交叉比对 ---
    rows = probe.scan_texts(TEXT_DIR) if TEXT_DIR.is_dir() else []
    total_chars = sum(r["chars"] for r in rows)
    total_words = sum(r["words"] for r in rows)
    checks = [
        {
            "name": "narr-probe 下发现 .txt",
            "passed": len(rows) > 0,
            "detail": f"{len(rows)} 个: " + ", ".join(r["file"] for r in rows),
        },
    ]
    if PREV_REPORT.is_file():
        prev = json.loads(PREV_REPORT.read_text(encoding="utf-8"))
        prev_totals = prev.get("totals", {})
        same = (
            prev_totals.get("files") == len(rows)
            and prev_totals.get("chars") == total_chars
            and prev_totals.get("words") == total_words
        )
        checks.append(
            {
                "name": "与上一轮报告一致",
                "passed": same,
                "detail": f"本轮 files/chars/words = {len(rows)}/{total_chars}/{total_words}，"
                f"上轮 = {prev_totals.get('files')}/{prev_totals.get('chars')}/{prev_totals.get('words')}",
            }
        )
    else:
        checks.append({"name": "上一轮报告存在", "passed": False, "detail": f"缺少 {PREV_REPORT.name}"})
    groups.append({"title": "复用 probe 模块：文本统计与回归比对", "checks": checks})

    # --- 组 3: 复用 drill 模块的分阶段检查 ---
    for stage in (drill.stage_env(), drill.stage_inventory(), drill.stage_math()):
        groups.append(
            {
                "title": f"复用 drill 模块：{stage.title}",
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail} for c in stage.checks
                ],
            }
        )

    # --- 组 4: 注入失败 ---
    injected = drill.stage_injected(mode_fail)
    groups.append(
        {
            "title": f"复用 drill 模块：{injected.title}",
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in injected.checks
            ],
        }
    )

    return sources, groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail", action="store_true", help="注入一个失败断言")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    sources, groups = collect(args.fail)

    failed = sum(1 for g in groups for c in g["checks"] if not c["passed"])
    passed = sum(len(g["checks"]) for g in groups) - failed
    report = {
        "entry": Path(__file__).name,
        "sha256_12": sha256_12(Path(__file__).resolve()),
        "sources": sources,
        "groups": [
            {
                "title": g["title"],
                "ok": all(c["passed"] for c in g["checks"]),
                "checks": g["checks"],
            }
            for g in groups
        ],
        "totals": {"groups": len(groups), "checks": passed + failed, "passed": passed, "failed": failed},
        "exit_code": 1 if failed else 0,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for g in report["groups"]:
            print(f"[{'PASS' if g['ok'] else 'FAIL'}] {g['title']}")
            for c in g["checks"]:
                print(f"   {'✓' if c['passed'] else '✗'} {c['name']}  ({c['detail']})")
        print(f"\n依赖脚本: " + "; ".join(f"{s['file']}@{s['sha256_12']}" for s in sources))
        print(f"合计 {report['totals']['checks']} 项检查，失败 {failed} 项")
        print(f"报告已写入: {REPORT.name}")

    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
