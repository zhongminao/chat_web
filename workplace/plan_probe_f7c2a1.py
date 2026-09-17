#!/usr/bin/env python3
"""plan 工具演练用小脚本：斐波那契 + 文本统计 + 简单报告。

唯一命名，避免与工作区已有脚本冲突。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORKDIR = Path(__file__).resolve().parent


def fib(n: int) -> list[int]:
    """返回前 n 个斐波那契数（迭代实现）。"""
    seq: list[int] = []
    a, b = 0, 1
    for _ in range(n):
        seq.append(a)
        a, b = b, a + b
    return seq


def scan_texts(folder: Path) -> list[dict]:
    """统计目录下每个 .txt 的行数、字符数与词数。"""
    rows = []
    for p in sorted(folder.glob("*.txt")):
        text = p.read_text(encoding="utf-8", errors="replace")
        rows.append(
            {
                "file": p.name,
                "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
                "chars": len(text),
                "words": len(text.split()),
            }
        )
    return rows


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    seq = fib(n)
    print(f"[1] 斐波那契前 {n} 项: {seq}")
    print(f"    校验 fib(0)+fib(1) == fib(2): {seq[0] + seq[1] == seq[2]}")
    print(f"    第 {n} 项 = {seq[-1]}")

    folder = WORKDIR / "narr-probe"
    rows = scan_texts(folder) if folder.is_dir() else []
    print(f"\n[2] 文本统计 ({folder.name}/):")
    if not rows:
        print("    (无 .txt 文件)")
    for r in rows:
        print(f"    {r['file']:<12} 行={r['lines']:<3} 字符={r['chars']:<4} 词={r['words']}")

    total_chars = sum(r["chars"] for r in rows)
    total_words = sum(r["words"] for r in rows)
    report = {
        "fib_n": n,
        "fib_last": seq[-1],
        "texts": rows,
        "totals": {"files": len(rows), "chars": total_chars, "words": total_words},
    }
    out = WORKDIR / "plan_probe_report_f7c2a1.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[3] 汇总: 文件 {len(rows)} 个, 字符 {total_chars}, 词 {total_words}")
    print(f"    报告已写入: {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
