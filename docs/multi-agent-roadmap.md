# 多 agent 改造设计（沙箱 / 锁 / 计划 / 框架改写）

本文是对 `tools/chat` 现有代码的一次评审结论 + 改造方案。**只写方案，不改代码** ——
读完能直接开工，也能逐条反驳。

规模基线（2026-09-16，当前工作区）：

| 口径 | 行数 |
|---|---|
| Python / JS / JSX / TS / Shell（含 `evals` 与 `workplace`） | 6317 |
| 同上，排除 `evals/` 与 `workplace/` | 5747 |
| 再加 CSS / YAML / lock | 8844 |

体量前几名：`tools.py` 556、`App.jsx` 580、`app.py` 509、`session_store.py` 434、
`runtime.py` 406、`check_api.py` 461、`styles.css` 1610。

---

## 0. 一句话结论

**先沙箱，再锁，然后才是多 agent；plan/progress 要有，但排在这三件之后。**

但"沙箱"和"锁"不是同一层的东西，也不是二选一：

| 问题 | 现在的答案 | 多 agent 需要的答案 |
|---|---|---|
| agent 会不会碰到不该碰的文件 | 会（`shell=True` + 绝对路径 + 无路径校验） | 结构化拒绝，不是"提示词里求它别" |
| 两个 agent 会不会同时改同一个文件 | 不存在两个 agent | 文件级互斥 + 版本检测 |
| 两个 agent 会不会同时写同一个会话日志 | 单进程内被 409 挡住 | 任务级权限 + 事件分片 |
| 一个跑飞的 agent 谁踩刹车 | 没人（`loop.py` 无轮数上限） | 预算对象 + 硬超时 |

**排期理由**：沙箱是"新能力的准入条件"，锁是"新能力的正确性条件"，plan 是"给人看的
可解释性"，多 agent 是"新能力本身"。倒过来做会得到"两个 agent 一起越过边界、一起
改坏文件、还都显示 100% 完成"。

---

## 1. 现状定位：它现在到底是什么

调用链（已核对代码）：

```
HTTP POST /api/chat ─→ app.chat()
    ├─ REGISTRY.begin(session_id)          # 每会话互斥，单进程内有效
    ├─ TurnWriter.begin()                  # 写 session header + turn + user
    ├─ runtime.request_real_reply()
    │     ├─ normalize_messages()          # 拼 system、丢工具协议残留
    │     └─ loop.run_agent_turn()
    │           ├─ client.request_assistant_message()   # 同步阻塞
    │           └─ execute_tool() → tools.py：直接 open()/subprocess
    └─ TurnWriter.finish()                 # turn-end
```

已经做对、且多 agent 要复用的东西：

- **日志即权威**：`session_store` 的 append-only JSONL 是唯一状态，历史靠重放，
  前端是订阅者。多 agent 的事件流可以长在同一套写法上。
- **循环与宿主解耦**：`loop.run_agent_turn(client, messages, writer, execute_tool,
  should_stop)` 的四个注入点，正好是"多 agent 要换掉的东西"（换 client、换 executor、
  换取消源）。
- **取消是协作式且形状正确**：`cancel.CancelToken` 是"另一个线程置位、循环在步边界
  看见"，`/interrupt` 已经是这条通道的入口。
- **`root` 由宿主注入**：`_INJECTED_ARGS` 排掉模型能给的 `root`/`should_stop` ——
  这条纪律要原样继承，沙箱的"允许根"也必须走同一个位置。
- **验证四件套**：`check_api.py`（真调路由 + 契约比对）、`demo_agent_loop.py`（假
  client + 真工具）、前端 `smoke.mjs`、`evals/agent/run.py`（判世界变成什么样）。

还没做对、且多 agent 之前必须动的：

1. `tools.py` 只决定"基准目录在哪"，**不是边界**（注释自己写明了：绝对路径照用、
   `../` 也可能走出 root）。
2. `observed.py` 的两个模块级字典是**全进程**的键空间（`_observed` / `_warned`），
   键只有路径 —— 没有会话、没有 agent。这是**现在就存在的串扰**，不是多 agent 才出现。
3. `cancel.REGISTRY` 是单进程内存表：多 worker、多实例、CLI 同时跑时形同不存在。
4. 一轮 = 一个 HTTP 请求，**同步跑完**。多 agent 扇出放不进这个形状。
5. 事件种类只有 `user/assistant/tool/running`（`ITEM_KINDS`），**没有任务、没有计划、
   没有 agent 身份**。
6. 没有预算、没有硬超时。`loop.py` 删掉 `MAX_ROUNDS_DEFAULT` 时把兜底一起删了，
   责任明确交给调用方 —— 而调用方目前**没有接**。
7. `list_sessions()` / `load_history()` 每次全量读整个 JSONL。事件变多之后这是
   显性成本（顺带是第 6 项上下文压缩的前置）。

---

## 2. 沙箱：具体攻击面与分阶段方案

现在的 `resolve_path()`（`tools.py`）：

```python
target = Path(path).expanduser()
if not target.is_absolute():
    target = root_dir(root) / target
return target.resolve()
```

`resolve()` 只做规范化，**没有任何"是否在允许根之下"的判断**。所以下面每一条现在
都是通的：

| # | 入口 | 现在会怎样 | 该怎样 |
|---|---|---|---|
| 1 | `read_file("/etc/passwd")` | 读到 | workspace 外读取按策略拒绝或需审批 |
| 2 | `write_file("../other-project/x.py")` | 写到别的项目 | 拒绝 |
| 3 | 符号链接：workspace 内 `link → /home/zhong` | 跟着走出去 | 规范化后再判断 + 保留真实路径检查 |
| 4 | `run_bash("cd / && rm -rf ...")` | 照做 | 进程边界隔离，不是字符串检查 |
| 5 | `run_bash("cat ~/.bashrc")` | 读到 API key | 拒绝 / 只给受限环境变量 |
| 6 | `run_bash("curl ...")` | 出网 | 默认禁网（多 agent 后这条更重要，扇出=多份出网能力）|
| 7 | spill 文件落 `/tmp/chat-spill/` | 任何会话都能读，且 `/tmp` 在 workspace 外 | 会话/任务维度的目录 + 生命周期绑定 |
| 8 | `POST /api/workspaces` 能登记 `/` 当工作区 | 直接绕过一切根判定 | 登记时也过策略（比如只许 HOME 之下）|
| 9 | `GET /api/browse` 能列任意目录 | 泄露目录结构 | 沙箱一起收紧 |
| 10 | `run_bash` 里 `timeout`/后台进程 | 已经收得干净（`_kill_group`）| 保留，扩展到资源限额 |

**根因**：文件工具（结构化 API）和执行工具（shell）是两种完全不同的信任级别，
现在共用一个"root"概念。分阶段：

### 阶段 A：结构化路径沙箱（1~2 天，收益最大）

新增 `agent/sandbox.py`，只做纯函数决策，**不碰 IO**：

```python
@dataclass(frozen=True)
class SandboxPolicy:
    roots: tuple[Path, ...]          # 允许的根（工作区，可能多个）
    writable: bool = True
    network: bool = False
    allow_absolute: bool = False     # 绝对路径是否只在 roots 内允许
    protected: tuple[str, ...] = (".git", ".env", "id_rsa", ".ssh")

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""                 # 拒绝时给模型看的一句话
    needs_approval: bool = False

def check_path(policy, target: Path, *, write: bool) -> Decision
def check_bash(policy, command: str, cwd: Path) -> Decision   # 粗筛 + 审批触发
```

接入点只有一处：`tools.resolve_path()` 之后、真正 `open()` 之前。**所有四个工具
都必须走它**，包括 `run_bash` 的 `cwd`。`Decision.reason` 用现有
`TOOL_ERROR_PREFIX` 通道回喂模型（"被拒绝"也是一条工具错误，不是异常）。

要点：

- **先 `resolve()` 再判断，且判断用 `Path.is_relative_to()`**，字符串前缀比较会被
  `/home/zhong-evil` 骗过。
- 符号链接要**在判断前解掉**（`resolve()` 已经解了），并且对"根本身是符号链接"
  这个情况显式处理。
- `needs_approval` 这一段先不实现执行，只**先让它可表达**：返回值有第三个状态，
  这样阶段 D 的审批接上去不用改调用方。
- 写一份**必过的负向测试清单**（每条都对应上表一行）：`..`、绝对路径、软链、
  `/etc`、`~/.bashrc`、`/tmp` 内的 spill。测试放
  `packages/chat/tests/test_sandbox.py`（现在没有 Python 测试目录，这是要新建的第一批）。

### 阶段 B：执行隔离（3~5 天，做完才算"沙箱"）

结构化沙箱挡不住 `run_bash`，因为 shell 的命令空间不可枚举。真正的边界只能是进程：

最小可行版本（不引入容器依赖）：

```
run_bash
  → 独立进程（已有 start_new_session）
  → 可用 setrlimit：CPU 秒数 / 地址空间 / 文件大小 / 进程数
  → 环境变量白名单（现成 API key 不继承）
  → cwd 固定在任务的工作目录（阶段 A 已保证）
  → 可选：独立临时 HOME / TMPDIR
```

再往上（多 agent 之后值得做）：容器 / `bwrap` / 只读挂载 + 只对任务目录可写，
外加 CPU、内存、磁盘、进程数、网络限额。**这一步是"要不要让本地小模型在真实目录里
放开跑"的分水岭**（README 里那句话是对的）：本地模型乱试的成本比 API 模型高得多。

### 阶段 C：危险命令审批（2~3 天）

审批**不是新的中断机制**，是现有中断形状的复用：

```
工具执行前 → Decision.needs_approval
  → 写一条 approval-required 事件（含命令、cwd、理由）
  → 循环在步边界看见"待批准"，抛 ApprovalRequired（与 TurnCancelled 同族）
  → POST /api/tasks/{id}/approvals/{approvalId} { approve | deny }
  → 循环被重新唤起，继续这一批工具
```

难点不在机制，在**进程怎么活过审批等待**：现在的轮次活在 HTTP 请求里，请求不能在
那里挂 10 分钟等人点。所以阶段 C 其实**依赖阶段 4 的任务化**（见第 4 节）—— 这也是
"先做沙箱"不等于"沙箱要先于任务化"的原因，A/B 可以先做，C 要等任务化。

---

## 3. 锁：三层，只有第一层是现成的

现有唯一互斥：`app.chat()` 里 `REGISTRY.begin(session_id)` → 同一会话第二轮 409。
它挡不住下面任何一件事：

- 多 uvicorn worker（现在 `uvicorn.run(app, ...)` 单进程，但这是部署决定不是设计约束）
- `python -m chat.run` 和 web 服务同时跑
- 两个 agent（任务内扇出）同时改一个文件
- 进程崩掉之后残留的锁

### 第 1 层：会话轮次锁（已有，改成跨进程）

`cancel.TurnRegistry` 的内存表 → 加一层落盘：`storage/locks/<session_id>.lock`，
`fcntl.flock` 独占 + 写入 pid/host/started_at。语义不变（第二个来者 409），
但服务重启后不会留下假死锁（flock 随进程消失）。**不要手写 pid 文件判活** —— 那是
经典的"pid 复用"坑。

### 第 2 层：文件锁（多 agent 的核心）

放在工具执行层，**不能放提示词层**，也不能靠 agent 自报"我要改这个文件"：

```python
# agent/locks.py
class FileLocks:
    def acquire(self, paths: list[Path], *, owner: str, wait: float = 0) -> Lease | None
    def release(self, lease)
```

规则（照 README 第 7 项那个方向走）：

| 操作 | 并发 |
|---|---|
| `read_file` | 无限并发 |
| `write_file` / `edit_file` | 同一路径互斥（`owner = agent_run_id`）|
| `run_bash` | 默认**独占整个工作目录**（它可能改任何东西）|
| 不同工作目录的任务 | 并行 |

**锁的持有者必须是任务/agent，不是"这一轮"** —— 否则 agent 之间没有互斥可言。

`wait=0`（拿不到就报错回喂模型"文件正被另一个 agent 修改，等它完成或换目标"）比
阻塞更符合现有风格：现有系统所有失败都是"文本回喂 + 模型自己决定"。

### 第 3 层：写入版本校验（已有雏形，要修）

`observed.py` 的 `guard()` 就是"写前版本校验"，方向对，但**键空间错了**：
`_observed` 是模块级、键只有路径。后果现在是：会话 A 读过 `x.py`，会话 B 直接改
`x.py` 不会被拦（"先读后改"形同虚设）。多 agent 后更严重。

修法：

```python
class ObservationContext:      # 由 executor 注入，替代模块全局
    def remember(self, path, lines=None)
    def guard(self, path) -> str | None
```

键变成 `(task_id, agent_run_id, workspace_id, realpath)`，状态**仍然放内存**
（进程重启后重读是可接受的代价，这条现有结论不用推翻），但生命周期归任务。

顺带：文件锁和版本校验是**互补的**，不能互相替代 —— 锁保证"没人同时写"，版本校验
保证"我读过的版本还在"（跨任务、跨进程、跨人操作都算）。两层都要。

---

## 4. 框架里要改写的地方（按优先级）

### 4.1 `tools.py`：从"函数集合"改成"执行上下文"

现在 `execute_tool()` 的注入只有 `root` 和 `should_stop`，靠**函数签名**过滤模型给的
参数（`_INJECTED_ARGS`）。加沙箱/锁/预算之后，再往下塞参数会开始难看。

改成显式上下文（签名不变的部分尽量保持）：

```python
@dataclass
class ExecutionContext:
    task_id: str
    agent_run_id: str
    workspace_root: Path
    sandbox: SandboxPolicy
    locks: FileLocks
    observation: ObservationContext
    budget: Budget
    emit: Callable[[dict], None]     # 事件出口（写日志）

def execute_tool(name, arguments_raw, ctx: ExecutionContext) -> str
```

`make_executor(root, should_stop)` → `make_executor(ctx)`，`loop.py` 的调用形状不变
（它本来就只要求 `(name, arguments_raw) -> str`）。**这是这一步值钱的地方**：
以后新增一个工具，沙箱/锁/预算不可能忘接，因为它们不在工具函数签名里。

### 4.2 `loop.py`：接入预算，别改回轮数上限

`loop.py` 的立场（"什么时候停是调用方的判断"）保留，加**注入式预算**：

```python
def run_agent_turn(..., step_hook: StepHook | None = None)
# step_hook.before_model() / before_tool() / after_model(usage) / after_tool(result)
```

`Budget` 实现这个协议，超限抛 `BudgetExceeded`（与 `TurnCancelled` 同族，由
`app.py` 捕获后写 `turn-end` 并回 200/409）。要记账的至少：墙钟、模型调用次数、
工具调用次数、bash 累计秒数、输出字节、token/花费（`openai_client` 现在把
`response.usage` 直接丢了，metadata 里补上即可 —— 这是 README 第 4 项）。

### 4.3 `app.py`：一轮 = 一个请求 → 任务化

多 agent 扇出不可能活在一个 HTTP 请求里。目标形状：

```
POST /api/tasks            → {taskId}      立刻返回，后台跑
GET  /api/tasks/{id}       → 状态 + 事件（现成的轮询模式，前端不用换范式）
POST /api/tasks/{id}/cancel
GET  /api/tasks/{id}/events?after=<seq>    → 增量拉事件（替掉全量重读）
POST /api/tasks/{id}/approvals/{aid}       → 审批
```

`/api/chat` 保留为"单 agent 一步"的兼容入口（它现在是 CLI、评估、前端三处共用的
路径，不要为了多 agent 把它拆了）。

后台执行放哪：先**进程内线程池 + 落盘任务表**（够用、改动小），把
"任务表"设计成可以换成独立 worker 进程（接口不吃 `asyncio` 原语）。真要多机再上
队列 —— 现在上队列是过早优化，除了增加调试难度没有收益。

### 4.4 `session_store.py`：日志要承载"任务"这种一等公民

现在的行类型：`session / turn / user / assistant / tool / turn-end`。多 agent 需要：

```
task            任务开始（一句话目标 + policy）
plan            plan-1 的步骤清单（结构化）
plan-update     某一项状态变化
agent-start     agentRunId / role / 目标 / 继承的预算
agent-event     某个 agent 内部的事件（它的 tool/assistant 也带 agentRunId）
artifact        产物（路径 / 版本 / 生产者）
approval        待批准 / 已批准 / 已拒绝
task-end        任务结束（状态 + 原因）
```

两条纪律：

1. **不改已有字段语义**。给 `assistant`/`tool` 记录加可选的 `agentRunId`，
   旧日志没有这个字段 = 主 agent，读法不变（老会话照样能打开）。
2. **`ITEM_KINDS` 是前后端共用契约**：新增 `plan` / `agent` / `artifact` / `approval`
   之后必须同步 `backend-contract.json` 的 `enums.item_kind`，并在前端
   `toDisplayItems()`（`App.jsx`）那张穷举表里加分支 —— 现在落到 `default` 会渲染成
   "未知条目"，这是**设计好的报警**，别绕过去。

还有两个顺带要修的：

- `TurnWriter.begin()` 里 `if not path.exists()` 是 check-then-act。多 agent 之后
  写入者变多，这个洞要堵（第一层锁 + header 写入改成"先写 header 到临时文件再 rename"）。
- `load_items()` / `list_sessions()` 全量重读。事件变多后加**事件序号水位线**
  （前端拉增量）+ 会话列表加索引/缓存。这是性能项，先记着，别提前做。

### 4.5 `runtime.py`：拆分，别继续长

`request_real_reply()` 现在同时干：环境变量、温度解析、建 client、消息归一、跑循环、
决定是否落盘。多 agent 之后它是第一个会失控的函数。目标：

```
runtime.py       消息/提示词/路径的纯逻辑（保留现在的角色，不动）
task_runner.py   任务生命周期：建 AgentRun → 跑 → 落事件 → 释放锁 → 产出结果
```

`request_real_reply()` 保留为**单步实现**（`TaskRunner` 内部调它），CLI 与评估继续
直接用它 —— 这样"两个入口一个核心"这条纪律不受影响。

### 4.6 工作区失效语义：必须分开

`resolve_workspace()` 找不到就**静默回落默认工作区**。聊天场景合理，任务场景危险：
任务绑的是工作区 A，A 被删了却跑到默认根 B 去改文件，等于改错项目还不报错。

```
新会话（没有绑定）  → 可以回落
任务/会话已绑定     → 绑定的工作区失效 = 任务失败，不换根
```

---

## 5. plan / progress：要加，但先明确它解决什么

**现状已经有的**：`GET /api/sessions/{id}` 返回 `items`（`user/step/assistant/running`），
前端 1 秒轮询。它能回答"刚才跑了什么、现在在跑什么"，**刷新、换设备都在**。
这套设计不要推翻。

**它回答不了**：整件事有几步、现在第几步、为什么有这些步骤、后面还剩什么、谁在负责。
多 agent 之后这三点从"体验问题"变成"可用性问题"：执行时间变长、并发变多，
用户没法从一串流水里判断"是不是卡住了、是不是走错路了"。

### 5.1 实现方式（沿用 README 第 1 项的判断，这里给形状）

- 新工具 `update_plan(items)`，写 `plan` / `plan-update` 事件，`planId` 聚合。
- 结构（**人看的那一列和机器用的一列分开**）：

```json
{"type":"plan","planId":"plan-1","agentRunId":"main",
 "items":[
   {"id":"s1","title":"扫描相关文件","status":"completed","owner":"explorer"},
   {"id":"s2","title":"修改入口","status":"running","owner":"implementer",
    "dependsOn":["s1"],"artifacts":["cli.py"]}
 ]}
```

状态域：`pending / running / completed / failed / blocked / cancelled`。多 agent 后加
`owner` / `dependsOn` / `artifacts` —— **v1 只做前四项 + owner**，别一次做满。

- 前端：计划面板（渲染序在消息流上方），步骤和 `step` 事件按时间对齐，能点开看某个
  步骤跑了哪些工具。`running` 条目的语义沿用现在那条（"声明了但还没有结果"）。
- **不要**为了计划引入 SSE/WebSocket：现有轮询 + 事件水位线足够；token 级流式是另一个
  需求，等它真的成为瓶颈再说（README 里那句结论成立）。

### 5.2 验收（照现有 evals 的办法量）

README 里已经给了靶子：本地 2B 在 `rename-across-files` 上 0/3，失败原因是"漏了
`cli.py`" —— 恰好是"枚举不全"，也就是计划该解决的问题。所以：

- 通过率**不降**、步数**不显著上涨**；
- 日志里真的出现 `plan` 事件；
- 新增 1~2 个"多步骤、容易漏"的 case（比 `rename-across-files` 更长）。

---

## 6. 多 agent 目标形状（第一版别做自由对话）

```
Task（一句话目标 + policy + workspace）
  └─ Planner            → plan 事件
       └─ Scheduler     → 按 dependsOn / 文件冲突 排序
            ├─ AgentRun(explorer,   read-only)     可并行
            ├─ AgentRun(implementer, write)        串行（持文件锁）
            ├─ AgentRun(tester,     exec)          与 implementer 互斥
            └─ Developer/Reviewer → 汇总 + 校验
```

角色先**固定**（planner / explorer / implementer / tester / reviewer），不要
agent-to-agent 自由对话 —— 那是把"调度问题"变成"社会问题"，出问题无从复现。

数据模型：

```python
Task      id, session_id, workspace_id, goal, status, policy, created_at, deadline
AgentRun  id, task_id, role, status, budget, owned_files, model, provider
Artifact  id, task_id, path, producer, version, checksum
Event     id, task_id, agent_run_id, seq, type, payload, time
```

落盘：**一个任务一个 JSONL**（或一个 agent_run 一个 JSONL + 任务级索引），
沿用"append-only + 折叠"的读法。不要多个 agent 共用一个 `TurnWriter` ——
`TurnWriter` 的语义是"一场会话一轮"，塞进并发就是错的。

`loop.py` 不用改：多 agent 就是"多份 client + 多份 executor + 多份 writer"，
这正是它现在解耦出来的形状。

---

## 7. 排期与验收（每阶段都可独立回滚）

| 阶段 | 内容 | 依赖 | 验收 |
|---|---|---|---|
| **P0** | `observed` 键加会话/agent；`resolve_workspace` 失效语义分开；补 Python 测试目录 | 无 | 新增负向测试全绿；现有四条验证不红 |
| **P1** | 沙箱 A：`sandbox.py` + `resolve_path` 接入 + 路径负向测试 | P0 | 上表 1/2/3/5/7/8/9 每条都有测试；`check_api` 契约更新 |
| **P2** | 锁：跨进程会话锁 + 文件锁 + 版本校验归任务 | P1 | 两个进程同时写同一文件 → 后者被拒且可读原因 |
| **P3** | 预算与硬超时（含 `response.usage` 入日志） | P0 | 跑飞的任务在 `max_wall_time` 内结束；评估有 agent 侧超时（README 第 0 项）|
| **P4** | plan 工具 + 事件 + 前端面板（**单 agent 上先做完**）| P0 | 2B 在 `rename-across-files` 通过率不降、日志有 `plan` |
| **P5** | 任务化：`POST /api/tasks` + 后台 runner + 增量事件接口 | P2 P3 | 关闭页面任务继续跑；刷新能恢复全过程 |
| **P6** | 沙箱 C：审批 | P5 | 危险命令挂起 → 批准 → 继续；拒绝 → 模型看到拒绝并改路 |
| **P7** | 沙箱 B：进程隔离 + 资源/网络限额 | P6 | 越界写盘失败；出网被拒；CPU/内存超额被杀 |
| **P8** | 多 agent：固定角色 + 调度器 + 任务级日志 | P4 P5 P7 | 多 agent case 的通过率 vs 单 agent；产物可追溯 |

**顺序上的两个反直觉点**，写下来免得后面自己纠结：

1. **P1 做完不等于"有沙箱"** —— `run_bash` 仍可到任何地方。要对外暴露服务，
   P7 才是那条线。P1 的价值是"堵住文件工具的越界 + 让边界可表达"。
2. **审批（P6）排在任务化（P5）之后** —— 因为等待审批的循环不能挂在 HTTP 请求里。
   不是"沙箱 C 拖后腿"，是它本来就需要一个能挂起的执行体。

---

## 8. 今天就能做的 5 个小改动（不依赖任何阶段）

1. `observed.py`：`_observed` / `_warned` 的键从 `path` 改成 `(scope, path)`，
   `scope` 由 executor 传入（先传 `session_id` 就够，多 agent 再细化成 `agent_run_id`）。
   这是**现在就有**的跨会话串扰。
2. `resolve_workspace()`：加一个 `strict=False` 参数；任务路径传 `strict=True`
   （绑定的工作区不存在 → 抛错，不换根）。
3. `openai_client.py`：`metadata` 里补 `usage`（prompt/completion tokens）——
   一行的事，但扇出和"最少 token"全靠它（README 第 4 项）。
4. `loop.py`：给 `run_agent_turn` 加 `step_hook=None` 参数（**先只加空实现**），
   为 P3 的预算留位置，不改任何行为。
5. `README.md`：把"要做什么"第 4 项（token 账）和第 7 项（并行工具调用）里
   已经写明的两条风险 —— `observed` 全局字典、`run_bash` 必须独占 —— 标成
   "多 agent 前置条件"，避免以后按"优化"处理。

---

## 9. 一句话回给那三个问题

- **先沙箱还是文件锁？** 先沙箱的**结构化部分**（路径判定，1~2 天，收益最大且无依赖），
  紧接着做锁；沙箱的进程隔离和审批反而不急 —— 服务现在只在局域网。
- **plan/progress 要不要加？** 要，但它是"单 agent 上就能做完"的事（P4），
  不要拿它当多 agent 的第一步；加了之后用现有 evals 量"通过率不降 + 步数不涨"。
- **框架要改什么？** 六处：`tools.py` 的执行上下文、`observed.py` 的键空间、
  `cancel.py` 的单进程锁、`loop.py` 的预算、`app.py` 的"一轮=一个请求"、
  `session_store.py` 的事件种类。其中前两处**不修就是 bug**，跟多 agent 无关。
