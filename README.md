# chat

React + FastAPI 的聊天服务，后端带一个能真的动手的 agent（读写文件、跑 bash）。
模型走 OpenAI 兼容接口（provider：`gpt` / `deepseek` / `local_qwen`）。

## 代码结构

```
tools/chat/                        # 仓库根（git 在这一层）
├── packages/
│   ├── chat/                      # 唯一的 Python 包（入口是 python -m chat，没有 scripts）
│   │   ├── backend-contract.json  # 后端响应的形状契约（见「验证」）
│   │   ├── check_api.py           # 校验：路由真调一遍 + 契约比对
│   │   ├── check_observed.py      # 校验：先读后改守卫
│   │   ├── demo_agent_loop.py     # 演示：假 client 驱动真工具，不花钱
│   │   └── src/chat/
│   │       ├── __main__.py        # 入口①：python -m chat → uvicorn
│   │       ├── app.py             # 只留 HTTP：路由 + 请求响应模型 + lifespan
│   │       ├── runtime.py         # ★ 核心：路径/环境/提示词/消息归一/一轮对话/审批后恢复
│   │       ├── run.py             # 入口②：python -m chat.run（CLI）
│   │       ├── openai_client.py   # provider 目录 + 无状态 Client
│   │       ├── providers.yaml     # 供应商/模型目录
│   │       ├── prompts.yaml       # 系统提示词模板（base / tools）
│   │       ├── static/            # 前端**产物**（提交进仓库）+ 样式
│   │       └── agent/             # 纯逻辑：不知道 HTTP，也不知道 CLI
│   │           ├── loop.py            # 工具循环（无轮数上限，边跑边落盘，遇审批暂停）
│   │           ├── tools.py           # 四个工具 + 分发 + root/沙箱/审计注入
│   │           ├── sandbox.py         # 路径围栏：canonical containment（软链接逃逸也拦）
│   │           ├── bash_audit.py      # bash 审计（append-only JSONL）
│   │           ├── session_store.py   # 会话日志 + TurnWriter + 设置回溯 + bash 请求
│   │           ├── workspace_store.py # 工作区登记表（沙箱的边界）
│   │           ├── cancel.py          # 轮次表 + 取消令牌
│   │           ├── observed.py        # 先读后改守卫（进程内存，按 context 隔离）
│   │           └── spill.py           # 超长输出落 /tmp/chat-spill/
│   └── frontend/                  # Node 包：前端源码，esbuild 打包
├── evals/agent/                   # 评估：量 agent 做对没有
├── storage/                       # 运行时数据（不进 git）：sessions/*.jsonl + workspaces.json + bash-audit.jsonl
├── workplace/                     # 一个工作区目录（agent 干活的地方）
└── start_local_qwen.sh            # 本地 Qwen3.5-2B vLLM
```

```
__main__.py ──→ app.py ──────┐
                             ├──→ runtime.py ──┬──→ agent/（loop + tools + sandbox + stores）
run.py (CLI) ────────────────┘                └──→ openai_client.py ──→ providers.yaml
```

**两个入口，一个核心**：web 与 CLI 都汇到 `runtime.py`，同一份消息归一、同一份一轮逻辑、同一份日志格式。`runtime.py` 刻意不 import fastapi，CLI 和评估才能用它而不拉起 web 栈。

## 安装与启动

```bash
pip install -e ~/mydisk/tools/chat/packages/chat   # 唯一的 Python 包
python -m chat                                     # 默认 8200
systemctl --user restart chat                      # 常驻；journalctl --user -u chat -f
```

局域网 `http://10.23.14.209:8200`。**移动或改名仓库后必须重装上面的包**（editable 记的是绝对路径）。
**改后端代码要重启服务**；改前端只要重新构建。

## 沙箱与审批

两种模式，**每轮可改**（不像工具开关那样锁死）：

| 模式 | 文件工具 | `run_bash` |
|---|---|---|
| `workspace-write`（默认） | 只能读写当前工作区内 | 不执行，生成**一次性审批请求**并暂停 |
| `full-access` | 不限制 | 直接执行 |

**文件围栏**（`agent/sandbox.py`）：判断的是目标**真实路径**，不是字符串前缀 —— 先 canonicalize（软链接、`..`、`.` 全部解析），再要求落在工作区根下；目标不存在时解析"最近存在的父目录"再拼后缀，所以"软链接目录下建新文件"同样拦得住。写入前会**重新 resolve 一次**并用那个 fresh target 落盘，缩小 TOCTOU 窗口。

**bash 审批**是一条完整回路：

```
workspace-write 下模型调 run_bash
  → 生成 bashreq-xxx，写进会话日志与审计，loop **暂停**（不写最终回复）
  → 输入端（Composer 位置）出现审批面板：命令 + cwd + 「拒绝 / 允许一次」
  → 批准：只执行日志里那条**原始命令**（requestId 绑定，模型无法事后替换）
      → 结果落盘 → resume_after_approval() 重新拉起循环
      → 模型看到真实输出，继续跑到最终回复
  → 拒绝：记录拒绝，同样恢复循环（模型知道这条没跑）
```

- 审批面板**只在输入端**，对话流里不出现"Bash 请求"卡片；跑完才以普通工具步骤（`⚙ run_bash`）出现。
- 同一批工具不会因为审批而丢：批次里其余 `read_file`/`write_file`/`edit_file` 照常跑完，只是**不再进入下一轮模型**。
- **审计**在 `storage/bash-audit.jsonl`（append-only）：`sessionId` / `requestId` / 模式 / `cwd` / 命令 / `timeout` / `exitCode` / `stopped` / 输出大小 / `denied` + 原因。删会话**不删审计**。

> **审批保证什么、不保证什么**（重要，别误解边界）：批准的是**那一串精确字符**，不展开它内部调用了什么。`bash a` 里的 `b`/`c`/`d`、`$(...)`、`eval`、下载后执行，都不会给你看；批准后命令以 **full-access** 跑，只受 `cwd` / `timeout` / 进程组回收约束。**审批 = 人的判断锚点 + 命令防篡改，不是行为范围保证**；真正的隔离要来自 OS 层（bwrap / Landlock / 容器）。

## 命令行用 agent

```bash
python -m chat.run "把 workplace 里 README 的标题改掉"   # 一轮就退
python -m chat.run --tools "跑一下测试，挂了就修"         # 这一轮允许用工具
python -m chat.run -i                                   # 连着聊
python -m chat.run --list                               # 列出会话（网页那些也在）
python -m chat.run -s <会话id> "接着上次说"               # 续聊某一场
```

- 不带 `-i` 也能多轮：每次追加到同一场会话，下次把历史读回来（不带 `-s` 就用"上次 CLI 用的那场"）。
- 工具开关**一场会话内固定**：第一轮 `--tools` 定下来，服务端对中途改直接 409。
- `Ctrl-C` 中断；其它：`-p/-m`、`-w <id>`、`--sandbox`、`--system`。
- **CLI 没有交互式审批入口**：`workspace-write` 下 bash 会停在审批请求上，要执行得走 `POST /api/sessions/{id}/bash-requests/{requestId}/approve`。

## 前端构建

```bash
cd packages/frontend && pnpm install   # 首次
pnpm run build      # 产出 packages/chat/src/chat/static/app.js
pnpm run watch      # 自动重建
pnpm run check      # build + smoke（jsdom 里真跑产物）
```

产物**提交进仓库**，所以不碰前端的人 clone 下来直接能跑，机器上不需要 Node。

## 配置与密钥

真实密钥不进仓库，也不放 `*.example` 模板。

| 变量 | 真实文件 | 谁在读 |
|---|---|---|
| `GPT_API_KEY` / `DEEPSEEK_API_KEY` / `LOCAL_QWEN_API_KEY` | `~/.bashrc` | `runtime.ensure_runtime_env()`（web 与 CLI 共用）|
| `CHAT_STORAGE` / `CHAT_WORKSPACE` | `chat.service` 的 `Environment=` | 运行时数据位置 / 默认工作区根 |
| `LOCAL_QWEN_MODEL_DIR` / `LOCAL_QWEN_MODEL_NAME` | 可选覆盖 | 只管启动 vLLM 服务，chat 不读 |

- **key 是文本解析 `~/.bashrc` 拿的**，不是读环境变量 —— 服务由 systemd 启动，继承不到终端环境。
- **每轮都重读**：在 bashrc 里换了 key，**下一轮就生效，不用重启**。优先级：启动时已存在的环境变量 > `~/.bashrc`；文件读不到时**不动**已有的值。
- **`providers.yaml` 是 `base_url` 的唯一来源**；local_qwen 的 `api_key_default` 是占位不是密钥。
- **`prompts.yaml` 是系统提示词的唯一来源**，每次请求重读 —— 改完**不用重启**。已说过话的会话用**落盘在 `turn` 里的那份**，所以要新会话才吃到改动。

## 运行说明

- 对话记录在**服务端**：一场一个 append-only JSONL，历史靠重放。删掉文件就是永久删除。
- **没说过话就没有会话文件**：会话 id 由客户端生成，文件只在第一轮写日志时创建。
- **工作区决定 agent 在哪干活**：相对路径按它解析、`run_bash` 的 `cwd` 是它。第一轮绑定，之后不许改。
- **系统提示词跟着会话走**：服务端只在它**变化**的那一轮记进日志，读回来靠回溯。
- **进度实时可见**：每条记录一产生就落盘，前端在轮次进行中轮询 `GET /api/sessions/{id}`。
- **可以中途停止**：`POST /api/sessions/{id}/interrupt`，取消是**协作式**的（只在两个步边界检查），界面显示"正在停止…"直到真正收尾。
- 默认 `deepseek` / `deepseek-flash`，改 `runtime.py` 那两个常量即可。

### 先读后改守卫（observed）

**没读过的文件不许改**（硬拦）；**读过之后被外部改过，先拦一次并说明变了多少**（只提醒一次 —— 守卫说不出"哪里变了"，所以把判断交还模型）。

- 按 context 隔离，**没有默认 context**，id 由宿主注入（模型塞不进来）：web/CLI 是 `session:<id>`，评估是 `eval:<case>-<时间戳>-<第几次>`。
- **进程内存**，TTL 6 小时，只管"这场会话多久没人用"，**不做陈旧判断**（那是 version 比较的事）。丢掉只会"多拦一次"，不会放行错误写入。
- 从没被读过的文件不存在，所以建新文件不受影响。

### 会话日志格式

```
{"type":"session","version":1,"id":…,"createdAt":…,"workspaceId":…}      首行，只写一次
{"type":"turn","time":…,"provider":…,"model":…,"toolsEnabled":…,
                 "sandboxMode":…}                                       一轮开始（跑之前）
{"type":"user","content":…}
{"type":"assistant","content":…,"tool_calls":[…]}                       收到模型回复即落盘
{"type":"tool","tool_call_id":…,"content":…}                            每个工具跑完即落盘
{"type":"plan","time":…,"todos":[…]}                                    `plan` 工具写入的当前任务清单
{"type":"bash-result","requestId":…,"status":…,"content":…}             审批后追加
{"type":"turn-end","time":…,"durationMs":…,"temperature":…,"error":…}   收尾
```

- **`systemPrompt` / `sandboxMode`** 在 `turn` 里；`systemPrompt` 只在**变化**的那轮出现。
- 历史重放 = `user`/`assistant`/`tool` 按序取出。**已批准的 bash 请求在重放时会被换成真实执行结果**，模型恢复时看到的是输出而不是请求卡片。
- 没有产出的那一轮，它的 `user` 不进历史 —— 失败/中断后重试不会把同一句话追加两遍。
- `repair_orphans` 给"声明了 `tool_calls` 但没结果"的洞补合成结果，会话不会废掉。

## 安全边界

**只在局域网可达，因此没有密码。** 应用层不做鉴权。

- `workspace-write` 限制的是**文件工具**；bash 是"停住让人批一次"的放行闸门，不是牢笼（见「沙箱与审批」那段引用块）。
- 批准后命令以 **full-access** 执行，审计只记命令字符串与输出，**没有进程树、文件改动、网络记录**。
- 所以对外暴露前必须先加鉴权，并且把 OS 级隔离做出来（bwrap/Landlock/容器），而不是只靠审批。

## 验证

```bash
python packages/chat/check_api.py          # 后端：路由不 5xx + 契约比对 + 语义断言
python packages/chat/check_observed.py     # 守卫：按 context 隔离 + 硬拦 + TTL，不花钱
python packages/chat/demo_agent_loop.py    # agent 闭环保真：假 client + 真工具，不花钱
cd packages/frontend && pnpm run check      # 前端：构建 + jsdom 冒烟（8 个场景）
python evals/agent/run.py --runs 2          # 评估：agent 做对没有
```

四条覆盖不同的层，**谁也不能替谁**：`smoke` 把 `fetch` 全 stub 掉，后端接口 500 它照样全绿；`check_api` 只验后端；`check_observed` 只验守卫；`evals` 只验 agent 做完没有。

### 后端契约

前后端之间那条边界**必须有测试**，所以 `backend-contract.json` 进 git，两侧都对着它断言：

```
backend-contract.json     ← 由 check_api.py --update 从真实后端采出
      ↑ 断言                    ↑ 断言
check_api.py（调接口比形状）   frontend/smoke.mjs（比自己的 stub）
```

后端改字段 → `check_api` 红 → 跑 `--update` → 前端 smoke 立刻发现 stub 过时。契约里还有 `enums.item_kind`（`user`/`step`/`running`/`assistant`/`bash-request`）：前端那张 `kind → 渲染` 表必须覆盖它，认不出的 kind 在界面上明说「未知条目」。

### 评估

判据是**世界变成什么样**，不是回复像不像。一个 case = `task.txt` + `fixture/` + `check.sh`（临时目录里跑，退出码 0 才算过）。走**服务端同一条路径**，只把根目录换成临时目录。两条护栏：`protected.txt` 哈希不许变；跑前跑后仓库 `git status --short` 必须一样。

```bash
python evals/agent/run.py -k rename    # 名字含 rename 的
python evals/agent/run.py --runs 3     # 每 case 三次
python evals/agent/run.py -m gpt-5.5   # 换模型
```

当前基线（2026-09-16，`deepseek-flash`，每 case 2 次）：`fix-until-green` ✅✅、`rename-across-files` ✅✅。只有 2 个 case、都简单 —— 这些数字是**回归信号**，不是分数。

## 要做什么

**推荐顺序：0 → 3**（评估超时 → VRMA 应用），其余按需。

**0. 评估的 agent 侧超时（10 分钟，前置于一切"换模型/加能力"的实验）**
`loop.py` 没有轮数上限，而评估里的 agent 循环**没有超时**（只有 `check.sh` 有 300s）。本地小模型陷入工具循环时会把评估挂死 —— 现在只能靠外层 `timeout` 兜。这条不补，后面任何"换个模型试试"都不可靠。

**1. planning + 可见 UI —— 已完成**
新增 `plan` 工具：模型用 `{todos:[{content,status}]}` 创建或更新当前任务清单；计划写进日志（`type:"plan"`），前端在输入区上方以可折叠清单显示，只展示当前 turn 的计划。工具描述里说明"长任务才用"，系统提示词只轻量列出这个工具。

**2. 沙箱 + 危险命令审批 —— 已完成**
两档模式、文件围栏、bash 一次性审批 + 恢复回路、审计日志都已落地（见「沙箱与审批」）。
**剩余**：bash **没有 OS 级隔离** —— 批准后以 full-access 跑，内部调用链不展开。要做真隔离得接 `bwrap`/Landlock，这是另一个工程量级；另外 CLI 还缺交互式审批入口。

**3. 应用：离线生成 VRMA 动作**（最终目标，见下一节）

之后按需，互不前置：

| # | 事 | 要点 |
|---|---|---|
| 4 | token / 成本账 | `openai_client` 从没读过 `response.usage`，扇出和"最少 token"都没法量。落进 turn 记录即可。 |
| 5 | 工具注册表单一来源 | schema 在 `tools.py`、散文在 `prompts.yaml` —— 加工具要改两处，且不会报错。 |
| 6 | 上下文压缩 | `loop.py` 没有轮数上限，压缩从"可选优化"变成"放开上限的必要配套"。 |
| 7 | 并行工具调用 | 现在一批是串行的。加池前先定：哪些可并行（`run_bash` 必须独占）、结果按模型给的顺序落盘、并发时的 observed 干扰。 |
| 8 | search | 走 API，不用本地 2B。 |

## 应用：离线生成 VRMA 动作

**目标**：动作**离线生成**（慢慢试错无所谓），直播只负责**回放**生成好的 `.vrma` —— 这个形状把延迟从致命变成无关。

**任务有多大**：一个 `.vrma` 是 52 根 humanoid 骨、91 条通道、60fps、11.8 秒、**6.4 万个浮点数**。这不是语言模型的输出空间（旋转还是四元数），所以分三层：

```
① 意图（LLM）      "挥手打招呼，再鞠躬"        ← 文本
      ↓ 工具调用
② 火柴人（确定性） 22 个关节的**位置**序列      ← 数字，程序算
      ↓ 插值 + aim/IK + retarget
③ VRMA（确定性）   相对标准 rest pose 的局部旋转 → 写文件
      ↓
 校验（规则）→ **文字**问题清单 → 回喂 ①  ↺
```

**为什么中间必须有「火柴人」**：`humanBones` 本身就是**名字 → 节点号**的标准表，让火柴人用同一套名字，映射就是查表；52 根里 30 根是手指，非手指的 **22 根**正好是标准火柴人（v1 砍掉手指和表情）；**存位置而不是旋转** —— 位置可直接插值/镜像/重定向，且"脚踩地"在位置空间就是 `foot.y ≈ 0`，旋转空间得先跑 FK；采样降到 10~15fps 后序列从 6.4 万降到**千级**，60fps 由插值还原。`.vrma` 与模型无关（存的是相对标准 rest 坐标系的旋转）。

**校验先用规则**（离线可以慢慢查，输出还是文字）：脚打滑 / 穿地失衡 / 关节限位 / 自穿模 / 瞬移。对抗网络留给"看着别扭"那类说不清的问题。

**已有数据可先自证**：`/home/zhong/mydisk/shared/vrma/` 有 7 个真动作，全转成火柴人再转回去比对四元数误差 —— 两个方向一次都验了。

**落到本仓库**：新增工具（`list_motions` / `sample_motion` / `blend` / `verify_motion`）+ 一个动作 case（`task.txt` 是一句动作描述，`check.sh` 跑 `verify_motion.py`），于是"哪个模型够用"（2B / 8B / 13B）有了数字而不是猜。

## 已知的小问题

| 问题 | 说明 |
|---|---|
| 审批不展开调用链 | 批准 `bash a` 不展开 `a` 内部的 `b`/`c`/`d`；审计也没有进程树。见「安全边界」。|
| 审批后命令以 full-access 跑 | 所以 `workspace-write` 的文件限制**不覆盖 bash**。|
| CLI 无审批入口 | `workspace-write` 下 bash 在 CLI 里只能靠 HTTP 端点批准。|
| 中断的轮次 `temperature` 是 `null` | 取消时调用方拿不到 `TurnResult`（提示词不受影响）。|
| `bash-audit.jsonl` 无轮转 | append-only，删除会话不清理它，会一直增长。|
| 契约只钉形状、不钉值域 | 除 `item_kind` 外的枚举靠 pydantic 的 `Literal` 保证。|
| 停止按钮这条交互没有测试 | `smoke.mjs` 覆盖渲染，不覆盖点击。|
| key 轮换没有回归测试 | "重读 bashrc 免重启"只靠人验过。|
| bashrc 里删掉 key 不会让进程忘掉它 | 只做"读到就更新"，彻底移除仍需重启。|
| 变更提醒"只一次"意味着重试可以绕过重读 | 刻意的"告知而非禁止"，代价是第二次试就能过。|
| observed 不跨进程共享 | 网页与 CLI 各有一份。|
| 陈旧保护是 check-then-write，不是 CAS | 中间有窗口，而 `run_bash` 能挤进去。|
