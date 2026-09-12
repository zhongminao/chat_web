# agent 循环评估

量的是**agent 循环本身**，不是聊天回答好不好。判据是"世界变成什么样了"，不是"回复像不像"。

这是路线图第 2 步（见 `packages/chat/README.md`）。它排在这里的原因：之后的每一步
（read_file 总量预算、进度反馈、沙箱、工具注册表、上下文压缩、planning）都要用它的
数字证明"没变坏"。

## 一个 case 是什么

```
（fixture + 任务 + 判定）→ 跑 agent 循环 → 查世界 → pass / fail
```

- **fixture**：初始目录长什么样（可以是几个坏文件，也可以是一个跑不过的脚本）
- **任务**：给模型的一句话
- **判定（oracle）**：**机器可判**的检查，例如
  - 坏掉的 JSON 修好了 → `json.load()` 能过
  - 脚本输出期望值 → 跑它比对 stdout
  - 测试通过 → 跑测试
  - "只告诉我别动它" → 文件 hash **没变**
  - 副作用边界 → case 目录之外没有新文件

判定绝不能是"回复里出现了某个词"。模型可以把工具都调对了但文件还是坏的。

## 两层，别混

**第 1 层：确定性、不花钱。** 脚本化 client（`packages/chat/demo_agent_loop.py` 里
的 `FakeClient` 就是种子）跑同一套 fixture。断言的是 loop 机制与工具语义：该停就停、
不撞 `max_rounds`、工具报错后能继续、`tool_call_id` 配对、协议消息形状、elision 头尾、
spill 落盘与取回、exit code、超时、非 UTF-8、路径带空格、守卫的三条路径、日志回放往返。

**第 2 层：真模型，才有数字。** 每 case 重复 N 次（模型不确定），报 pass@1 与 pass^N。
10 个 case × 3 次 ≈ 30 次调用，改一次跑得起。

## 指标

通过率（pass@1 / pass^N）、**轮数**、工具调用次数、耗时、**token 与成本**。

> 缺口：token 现在**没有记录** —— provider 返回的 `usage` 从来没被读，turn 的 meta 里只有
> provider / model / temperature / durationMs。要报成本得先补这一环。

## 结果落在哪

一次跑一份 append-only JSONL（和会话日志同一个设计思路），带模型名、温度、日期、
`git rev-parse`。这样两次跑能 diff：**这次改动让哪个 case 变坏了**。不带这些版本的
通过率数字，过两天自己都没法比。

## 不需要的东西

**服务端、前端、storage 都不需要。** 只需要 `chat` 这个包（的 agent 部分）：

```python
from chat.agent import TOOL_SCHEMAS, make_executor, run_agent_turn

reply, steps, protocol = run_agent_turn(
    client, messages, tool_schemas=TOOL_SCHEMAS,
    execute_tool=make_executor(case_tmpdir),   # 一个 case 一个临时目录
)
# 然后查 case_tmpdir 里的世界，不是查 reply
```

`run_agent_turn` 是无状态的：给它 messages，还你协议消息。"一个 case 一个目录"正是
工具层收 `root` 换来的（见 `packages/chat/src/chat/agent/tools.py` 顶部注释）。

## 会话日志在评估里的角色

是**诊断**，不是判定。日志里有完整的工具调用与结果（`tool_calls` / `tool` 记录），
case 挂了可以翻轨迹看它在第几轮走偏。但日志记的是**过程**，判定要的是**结果** ——
模型完全可能把工具都调对了，文件还是坏的。

单轮 case 连落盘都不需要。要留存就在跑完 loop 后补一句 `session_store.append_turn(...)`，
失败时能用现成的工具翻。

## 防自欺

- 留一部分 case 平时**不看** —— 否则调着调着就过拟合到评估集上了
- 报告里必须带模型名 / 温度 / 日期 / `git rev-parse`
- 只有 5~10 个 case 时，数字是**回归信号的**，不是"我的 agent 有 87 分"。别当排行榜用
