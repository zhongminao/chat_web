"""校验：observed（先读后改守卫）按 context 隔离，且长期不用的 context 会被扫掉。

为什么单独一个脚本：这条守卫**曾经静默失效过**，而失效的表现是"什么都照常工作"
—— 没有任何报错、没有红灯，只是"先读后改"跨会话不再成立（A 场读过的文件，B 场
能直接改）。守这种东西只能靠断言，不能靠看。

三组断言，各守一件事：
  1. 隔离    —— 一个 context 的观测不影响另一个；同一个 context 内部照常放行。
  2. 硬拦    —— 没读过就改，工具真的**不动文件**（只回一句可自救的错误文本）。
  3. TTL     —— 闲置的 context 被回收；回收后回到"必须重读"，而不是放宽。

另外守两条**接口约束**（它们各自对应一次踩过的坑）：
  - 没有隐式默认 context：拿不到 id 就不许改，而不是退回一张共享表；
  - 模型不能自己塞 observed（它是宿主注入参数，和 root / should_stop 一样）。

不需要模型、不需要网络：直接调工具层的真实实现。
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from chat.agent import observed
from chat.agent.tools import execute_tool, make_executor, read_file

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


# 工作区根。**必须显式注入给工具**：root 和 observed 一样是"宿主注入参数"，
# 模型在 arguments 里塞不进来（见 tools._INJECTED_ARGS）。
#
# 这个检查原来调 execute_tool 时只传 observed、不传 root，于是沙箱回落成
# sandbox_mode 的默认值 workspace-write + 一个不是这里临时目录的根 —— 临时目录里
# 的每个路径都被判成"在工作区外"，**全部被拒**。后果特别隐蔽：那几条本来就期望
# 被拒的断言照样绿（因为错误的原因通过），只有期望放行的 4 条红。
WORK: Path | None = None


def call(name: str, context, **kwargs) -> str:
    return execute_tool(name, json.dumps(kwargs), root=str(WORK), observed=context)


def main() -> int:
    global WORK
    work = Path(tempfile.mkdtemp(prefix="check-observed-"))
    WORK = work
    alpha = work / "alpha.txt"
    beta = work / "beta.txt"
    alpha.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    beta.write_text("one\ntwo\nthree\n", encoding="utf-8")

    # 每个断言用自己命名的 context，免得互相干扰（也用不着清全局表）
    ctx_a = observed.REGISTRY.context("check:A")
    ctx_b = observed.REGISTRY.context("check:B")

    print("=== 隔离：一个 context 的观测不泄漏到另一个 ===")
    check("A 读 alpha.txt", call("read_file", ctx_a, path=str(alpha)).startswith("[read_file]"))
    check("A 改 alpha.txt 放行",
          call("edit_file", ctx_a, path=str(alpha), old_text="beta", new_text="BETA")
          .startswith("[edit_file]"))
    blocked = call("edit_file", ctx_b, path=str(alpha), old_text="gamma", new_text="GAMMA")
    check("B 改 alpha.txt 被拦（A 读过不算 B 读过）", blocked.startswith("[tool error]"),
          blocked[:80])
    check("B 改自己没读过的 beta.txt 也被拦",
          call("edit_file", ctx_b, path=str(beta), old_text="two", new_text="TWO")
          .startswith("[tool error]"))
    check("beta.txt 真的没被动", beta.read_text(encoding="utf-8") == "one\ntwo\nthree\n")

    print()
    print("=== 硬拦：没读过就改，文件必须原封不动 ===")
    blocked_write = call("write_file", ctx_b, path=str(alpha), content="clobbered\n")
    check("write_file 覆盖已存在且没读过的文件被拦",
          blocked_write.startswith("[tool error]"), blocked_write[:80])
    check("alpha.txt 没被覆盖", alpha.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n")
    check("新文件不受影响（createIfAbsent 语义）",
          call("write_file", ctx_b, path=str(work / "brand-new.txt"), content="x\n")
          .startswith("[write_file]"))

    print()
    print("=== 提醒额度是每个 context 一份（以前会被别的会话花掉）===")
    warn_a = observed.REGISTRY.context("check:warn-A")
    warn_b = observed.REGISTRY.context("check:warn-B")
    drifting = work / "drifting.txt"
    drifting.write_text("v1\n", encoding="utf-8")
    warn_a.remember(drifting)
    drifting.write_text("v2 changed\n", encoding="utf-8")
    check("读它的 A 收到变更提醒", (warn_a.guard(drifting) or "").startswith("changed since"))
    check("没读过的 B 收到的是「没读过」而不是「变了」",
          (warn_b.guard(drifting) or "").startswith("not read yet"))
    check("A 的额度还在 A 自己手里（第二次不再拦）", warn_a.guard(drifting) is None)

    print()
    print("=== TTL：闲置的 context 被扫掉，回收后回到「必须重读」而**不是**放宽 ===")
    short = observed.ObservationRegistry(ttl_seconds=1, sweep_interval_seconds=0)
    ctx_x = short.context("session:X")
    ctx_x.remember(alpha)
    check("刚用过 → 放行", ctx_x.guard(alpha) is None)
    time.sleep(1.2)
    check("闲置超过 TTL → sweep 回收 1 个", short.sweep() == 1)
    check("回收后表空了", short.live_context_ids() == [])
    check("重新取到的是全新 context → 要求重读",
          (short.context("session:X").guard(alpha) or "").startswith("not read yet"))

    print()
    print("=== 接口约束：没有隐式默认 context，模型也不能自己塞 ===")
    for bad in ("", "   "):
        try:
            observed.REGISTRY.context(bad)
            check(f"空 context_id {bad!r} 应该报错", False)
        except ValueError:
            check(f"空 context_id {bad!r} 被拒", True)
    try:
        make_executor(None)
        check("make_executor 缺少 observed 应该报错", False)
    except TypeError:
        check("make_executor 的 observed 是必填", True)
    try:
        read_file(str(alpha))
        check("read_file 缺少 observed 应该报错", False)
    except TypeError:
        check("read_file 的 observed 是必填", True)

    observed.REGISTRY.context("check:already-read").remember(beta)
    sneaky = call("edit_file", observed.REGISTRY.context("check:never-read"),
                  path=str(beta), old_text="two", new_text="TWO",
                  observed="check:already-read")
    check("模型塞 observed 无效（宿主注入覆盖它）", sneaky.startswith("[tool error]"),
          sneaky[:80])

    print()
    if failures:
        print(f"❌ {len(failures)} 条断言没过：{failures}")
        return 1
    print("✅ observed 的隔离、硬拦、TTL 与接口约束都成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
