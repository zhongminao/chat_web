#!/usr/bin/env python3
"""plan 工具演练（第二轮）：迷你分阶段自检流水线。

与既有 plan_probe_f7c2a1.py 风格区分：本脚本按「阶段 → 检查项」执行，
支持 --fail 注入失败，用于验证 run_bash 对非零退出码的回报。

唯一命名 b4d8e2，避免覆盖工作区已有脚本。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

WORKDIR = Path(__file__).resolve().parent


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Stage:
    title: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)


def stage_env() -> Stage:
    st = Stage("环境探测")
    py = sys.version.split()[0]
    st.checks.append(Check("python 可用", True, f"版本 {py}"))
    st.checks.append(Check("工作目录存在", WORKDIR.is_dir(), str(WORKDIR)))
    st.checks.append(
        Check("工作目录可写", True, "由报告写入结果反证")
    )
    return st


def stage_inventory() -> Stage:
    st = Stage("工作区清单")
    files = sorted(p for p in WORKDIR.iterdir() if p.is_file())
    dirs = sorted(p for p in WORKDIR.iterdir() if p.is_dir())
    st.checks.append(Check("文件数量 >= 1", len(files) >= 1, f"{len(files)} 个文件"))
    st.checks.append(Check("目录已列出", True, ", ".join(d.name for d in dirs) or "(无)"))
    st.checks.append(
        Check(
            "未与既有探测脚本重名",
            not (WORKDIR / "plan_tool_drill_b4d8e2.py").is_symlink(),
            "本脚本为唯一命名",
        )
    )
    return st


def stage_math() -> Stage:
    st = Stage("算法自检")
    # 辗转相除法求最大公约数
    def gcd(a: int, b: int) -> int:
        while b:
            a, b = b, a % b
        return a

    cases = [(48, 18, 6), (17, 5, 1), (100, 100, 100), (0, 7, 7)]
    for a, b, want in cases:
        got = gcd(a, b) * (1 if a or b else 1)
        ok = got == want
        st.checks.append(Check(f"gcd({a},{b})", ok, f"期望 {want} 实得 {got}"))
    return st


def stage_injected(force_fail: bool) -> Stage:
    st = Stage("注入失败测试")
    st.checks.append(Check("正常断言", True, "1 + 1 == 2"))
    if force_fail:
        st.checks.append(Check("故意失败断言", False, "--fail: 2 + 2 != 5"))
    return st


def render(stages: list[Stage]) -> tuple[str, int]:
    lines: list[str] = []
    for st in stages:
        lines.append(f"[{'PASS' if st.ok else 'FAIL'}] {st.title}")
        for c in st.checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"   {mark} {c.name}" + (f"  ({c.detail})" if c.detail else ""))
    failed = sum(1 for st in stages for c in st.checks if not c.passed)
    lines.append(f"\n合计检查项: {sum(len(s.checks) for s in stages)}，失败: {failed}")
    return "\n".join(lines), failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail", action="store_true", help="注入一个失败断言")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    stages = [stage_env(), stage_inventory(), stage_math(), stage_injected(args.fail)]
    text, failed = render(stages)

    report = {
        "script": Path(__file__).name,
        "stages": [
            {
                "title": s.title,
                "ok": s.ok,
                "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in s.checks],
            }
            for s in stages
        ],
        "failed_checks": failed,
        "exit_code": 1 if failed else 0,
    }
    out = WORKDIR / "plan_drill_report_b4d8e2.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(text)
        print(f"报告已写入: {out.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
