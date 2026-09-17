"""校验：spill 落在**工作区里**，所以 read_file 能直接读回来。

守的是一个"看起来一切正常"的错配：命令行被截断时，工具会回一句

    [full output: N chars saved to <路径> — the middle above was elided.
     Read it with read_file (page it with offset/limit), or grep/sed it with run_bash.]

提示语把 read_file 放在第一位。但 read_file 受沙箱围栏管，**workspace-write 下只能
读工作区里的东西** —— 而 spill 曾经落在 /tmp，于是模型照着提示做必然吃到
"file access denied"，只能退回 run_bash 去 cat，而**每条 run_bash 都要用户批一次**。
症状是"每次想看长输出都得再批一条命令"，但没有任何一处会报错。

三条断言：
  1. 定位符指向的路径在**工作区里**，而且 read_file（workspace-write）真的读得到
     全文 —— 这是提示语成立的前提；
  2. 没有工作区时退回系统临时目录（CLI / 评估那条路），别把文件写进奇怪的地方；
  3. fail-soft 还在：工作区不可写时返回 None，命令**照样算成功**（退回纯截断）。

不需要模型、不需要网络：真的跑一条 `seq`，真的读回来。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from chat.agent import observed, spill
from chat.agent.tools import BASH_OUTPUT_LIMIT, read_file, run_bash

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


work = Path(tempfile.mkdtemp(prefix="check-spill-"))
# 造一条输出明显超过内联上限的命令（行数与字节数都够）
lines = BASH_OUTPUT_LIMIT * 3
command = f"seq 1 {lines} | sed 's/^/line-/'"

print("\n[1. 定位符指向工作区，且 read_file 能读回来（提示语成立的前提）]")
result = run_bash(command, root=str(work), sandbox_mode="full-access")
check("输出被 spill 了（回的是预览 + 定位符）", "[full output:" in result)
path_str = ""
if "[full output:" in result:
    # 形如：[full output: 8123 chars saved to /路径 — the middle ...
    path_str = result.split(" chars saved to ", 1)[1].split(" —", 1)[0].strip()
check("落盘路径存在且在工作区里",
      bool(path_str) and Path(path_str).is_relative_to(work),
      f"实得 {path_str}")

if path_str:
    # 关键：按提示语用 read_file 读，**不加任何特权**（workspace-write 是默认档）。
    # 包一层 try：围栏拒绝时 read_file 是抛 PermissionError（工具分发那层才把它变成
    # "[tool error] ..."）。这里接住并记成一条失败，好让后面的断言照样跑完 ——
    # 脚本直接崩在第 57 行会掩盖另外两条。
    guard = observed.REGISTRY.new_context("check-spill")
    try:
        head = read_file(path_str, root=str(work), sandbox_mode="workspace-write", observed=guard)
        check("read_file 在 workspace-write 下读得到（不再被围栏拒）",
              not head.startswith("[tool error]"), f"实得 {head[:80]!r}")
        tail = read_file(path_str, offset=lines, limit=5, root=str(work),
                         sandbox_mode="workspace-write", observed=guard)
        check("按 offset 翻页能读到最后一页（被掐掉的尾部取回来了）",
              f"line-{lines}" in tail, f"实得 {tail[-60:]!r}")
    except PermissionError as exc:
        check("read_file 在 workspace-write 下读得到（不再被围栏拒）", False, str(exc))
        check("按 offset 翻页能读到最后一页（被掐掉的尾部取回来了）", False, "上一条已经失败")

print("\n[2. 没有工作区时退回系统临时目录]")
check("root=None → 落在临时目录下",
      spill.spill_dir(None).is_relative_to(Path(tempfile.gettempdir())),
      f"实得 {spill.spill_dir(None)}")
check("给了工作区 → 落在工作区里",
      spill.spill_dir(work) == work / spill.DIR_NAME, f"实得 {spill.spill_dir(work)}")

print("\n[3. 防软链接逃逸 + fail-soft]")
# 预先埋一个指向工作区外的 `.chat-spill` 软链接：不能顺着它写到外面去
outside = Path(tempfile.mkdtemp(prefix="check-spill-outside-"))
escape = Path(tempfile.mkdtemp(prefix="check-spill-escape-"))
(escape / spill.DIR_NAME).symlink_to(outside)
check("根是软链接指向工作区外时，退回临时目录而不是顺着写出去",
      spill.spill_dir(escape).is_relative_to(Path(tempfile.gettempdir())),
      f"实得 {spill.spill_dir(escape)}")

# 工作区不可写：save 返回 None，而命令本身仍然算成功（调用方退回纯截断）
readonly = Path(tempfile.mkdtemp(prefix="check-spill-ro-"))
spill_base = readonly / spill.DIR_NAME
spill_base.mkdir()
spill_base.chmod(0o500)
check("工作区不可写时 save 返回 None（fail-soft）",
      spill.save("x", source="run_bash", root=str(readonly)) is None)
spill_base.chmod(0o700)

print()
if failures:
    print(f"失败 {len(failures)} 项：" + "；".join(failures))
    sys.exit(1)
print("spill 校验通过：长输出落在工作区里，read_file 能直接读回来。")
