# chat

React + FastAPI 的聊天服务，后端带一个能真的动手的 agent（读写文件、跑 bash）。
模型走 OpenAI 兼容接口（provider：`gpt` / `deepseek` / `local_qwen`）。

## 代码结构

`packages/chat/src/chat/`：`app.py`（HTTP）· `runtime.py`（★提示词 / 消息归一 / 一轮对话 / 审批后恢复）· `run.py`+`__main__.py`（CLI 与 web 两个入口）· `openai_client.py` + `providers.yaml` + `prompts.yaml` · `static/`（前端产物）· `agent/`（纯逻辑，不知道 HTTP 也不知道 CLI：`loop` 循环 · `tools` 工具+分发+注入 · `sandbox` 路径围栏 · `session_store` 会话日志 · `cancel` 取消令牌 · `observed` 先读后改守卫 · `bash_audit`/`spill`/`workspace_store`）。
其余：`packages/frontend/`（前端源码，产物提交进仓库）· `evals/agent/`（评估）· `storage/`（运行时数据，不进 git）· `workplace/`（一个工作区）· `start_local_qwen.sh`。
**两个入口，一个核心**：web 与 CLI 都汇到 `runtime.py`（同一份归一、一轮、日志格式）；它刻意不 import fastapi，CLI 与评估才能用它而不拉起 web 栈。

## 安装与启动

```bash
pip install -e ~/mydisk/tools/chat/packages/chat   # 唯一的 Python 包；移动/改名仓库后必须重装
python -m chat                                     # 默认 8200；局域网 http://10.23.14.209:8200
systemctl --user restart chat   # 常驻；journalctl --user -u chat -f；改后端要重启，改前端只要重新构建
```

## 沙箱与审批

两种模式，**随时可改**：`workspace-write`（默认）文件工具只能动工作区内、`run_bash` 不执行而是生成一次性审批请求并停住；`full-access` 都不限制。

- **模式是日志里的一条事件，不是快照**：拨开关写一条 `{"type":"sandbox","mode":…}`（log-only：不产 item、不进模型历史），**当前值 = 最后一条**；`turn.sandboxMode` 只表示"这一轮开始时是什么"，决策不读它。没说过话的会话不落盘（前端草稿）。
- **执行侧每次工具调用现折一遍** → 拨完开关，下一条命令就按新模式走（不必等下一轮、也不必等某次审批）。
- **模型只在权限变了时被告知一句**，平时靠工具返回学：`[bash request]` 自带"没执行、别重试"，围栏拒绝自带原因与路径。**文件围栏**比的是真实路径（canonicalize 后必须落在工作区根下，写入前重新 resolve 一次）。

```
模型调 run_bash → 生成 bashreq-xxx（写日志与审计），这一轮**停住**（不写最终回复，也不写 turn-end）
→ 面板出现（命令 + cwd + 「停止这一轮 / 拒绝 / 允许一次」）：批准 → 只执行日志里那条**原始命令**
  （requestId 绑定，模型无法事后替换）→ 结果落盘 → 模型继续；拒绝 → 记下"没跑"，同样让它继续
```

- **停住等审批是一种真状态**：`running` 仍为 `true`；`/interrupt` 的含义是**放弃这一轮**（没答复的请求记成 `cancelled` 后收尾）；`turn-end` 只在真正结束时写一次。
- **一批里有多条命令**：全部变成待批准；批哪条哪条**当场跑**，但**全批完才唤醒模型** —— "它说话"永远在你答复之后；批次里其余工具照常跑完。
- **审计**在 `storage/bash-audit.jsonl`（每条命令的模式 / 命令 / 用时 / 退出码）；删会话**不删审计**。

> **安全边界**：只在局域网可达、因此没有密码，应用层不做鉴权 —— 对外暴露前必须先加鉴权，并把 OS 级隔离做出来（bwrap / Landlock / 容器），而不是只靠审批。
> **审批保证什么、不保证什么**：批准的是**那一串精确字符**，不展开它内部调用了什么。`bash a` 里的 `b`/`c`/`d`、`$(...)`、`eval`、下载后执行，都不会给你看；批准后命令以 **full-access** 跑，只受 `cwd` / `timeout` / 进程组回收约束，审计只记命令与输出（无进程树 / 文件改动 / 网络记录）。**审批 = 人的判断锚点 + 命令防篡改，不是行为范围保证**。

## 命令行用 agent

```bash
python -m chat.run --tools "跑一下测试，挂了就修"            # 一轮就退（不加 --tools 不许用工具）
python -m chat.run -i · --list · -s <会话id> "接着上次说"    # 连着聊 · 列会话 · 续聊某一场
```

不带 `-i` 也能多轮（追加到同一场会话，历史读回来）；工具开关**一场会话内固定**，中途改服务端 409；`Ctrl-C` 中断，其它 `-p/-m`、`-w <id>`、`--sandbox`、`--system`。**CLI 没有交互式审批入口**：`workspace-write` 下 bash 停在审批请求上，要执行得走 `POST /api/sessions/{id}/bash-requests/{requestId}/approve`。

## 前端构建

```bash
cd packages/frontend && pnpm install   # 首次
pnpm run build | watch | check         # 产出 static/app.js / 自动重建 / build + jsdom 冒烟
```

产物**提交进仓库**（不碰前端的人 clone 下来直接能跑，机器上不需要 Node）；样式拆在 `static/styles/` 6 张表，先后由各文件里的 `@layer` 决定、**与 link 顺序无关**；产物带 `Cache-Control: no-cache`，改完普通刷新即可。助手侧**一轮一个复制按钮**（整轮含工具调用与输出）。

## Markdown 渲染

**只渲染助手正文**：`user` 消息、工具结果、审批命令、`plan` 条目一律**逐字原样**（审批命令尤其"显示的和执行的必须是同一串字符"）。支持标题/段落/列表/引用/粗斜体/行内码/链接/GFM 表格/围栏代码块/行间公式；`$...$` 是不透明区间（不排版数学，只保证原样可复制）。**硬约束是"宁可少渲染，绝不吞字符"**：`*` 可能既是强调符也是乘号或通配符，所以**两侧都是 ASCII 字母数字时永不当强调符**（`3*4`、`src/*.py` 安全）。不支持 `_x_`、`~~删除~~`、嵌套列表、`\(...\)`、h5/h6，以及"汉字开头、ASCII 结尾"的混排强调（把收尾标点写进粗体里就生效）。边界由 smoke 场景 9 逐条钉住。

## 配置与密钥

真实密钥不进仓库。**key 是文本解析 `~/.bashrc` 拿的**（服务由 systemd 启动，继承不到终端环境），而且**每轮重读** —— 换了 key 下一轮就生效，不用重启；`CHAT_STORAGE` / `CHAT_WORKSPACE` 由 `chat.service` 的 `Environment=` 给（运行时数据位置 / 默认工作区根）。

**`providers.yaml` 是 `base_url` 的唯一来源**；**`prompts.yaml` 是系统提示词的唯一来源**，每次请求重读（改完不用重启；但已说过话的会话用落盘的那份，所以要新会话才吃到）。

## 运行说明

- 对话记录在**服务端**：一场一个 append-only JSONL，历史靠重放，删文件就是永久删除；**没说过话就没有会话文件**（id 由客户端生成）。**工作区**决定 agent 在哪干活（相对路径、`run_bash` 的 `cwd`），第一轮绑定、之后不许改。
- **进度实时可见**：每条记录一产生就落盘，前端在轮次进行中轮询 `GET /api/sessions/{id}`。
- **可以中途停止**：`POST /api/sessions/{id}/interrupt`。跑着时是**协作式**取消（步边界检查）；**停在审批上时立即生效**，含义是"放弃这一轮"。
- **先读后改守卫**：没读过的文件不许改（硬拦）；读过之后被外部改过先拦一次。按 context 隔离（id 由宿主注入），进程内存、TTL 6 小时，丢掉只会"多拦一次"。
- 默认 `deepseek` / `deepseek-flash`（改 `runtime.py` 那两个常量）。

```
{"type":"session",…,"workspaceId":…}      首行，只写一次
{"type":"turn",…,"model":…,"toolsEnabled":…,"sandboxMode":…,"systemPrompt":…}   一轮开始（跑之前）
{"type":"sandbox",…,"mode":…}             人拨了沙箱开关（log-only）
{"type":"user"|"assistant"|"tool",…}      对话与工具，边跑边落盘（assistant 带 tool_calls / reasoning_content）
{"type":"plan","todos":[…]} · {"type":"turn-end",…,"durationMs":…,"error":…}   计划快照 · 收尾（**停住等审批时不写**）
```

审批的结果走 `{"type":"bash-result","requestId":…,"status":…,"content":…}`（批准 / 拒绝 / 放弃）。`systemPrompt` 只在**变化**的那轮出现；**当前模式 = 最后一条 `sandbox` 事件**。**`reasoning_content`**（thinking 模式的思维链）**必须原样回传**，否则下一次请求整个 400 —— 所以它跟着 assistant 一起落盘、一起重放。历史重放 = `user`/`assistant`/`tool` 按序取出，**已批准的 bash 请求会被换成真实执行结果**；`repair_orphans` 补"声明了 `tool_calls` 但没结果"的洞。

## 验证

```bash
python packages/chat/check_api.py          # 后端：路由不 5xx + 契约比对 + 语义断言
python packages/chat/check_observed.py · demo_agent_loop.py   # 守卫隔离/TTL · agent 闭环（都不花钱）
cd packages/frontend && pnpm run check      # 前端：构建 + jsdom 冒烟（14 个场景）
python evals/agent/run.py --runs 2          # 评估：agent 做对没有（会花钱）
```

四条覆盖不同的层、**谁也不能替谁**（smoke 把 `fetch` 全 stub 掉，后端 500 它照样全绿）。

**后端契约**：`backend-contract.json` 进 git、两侧都对着它断言（由 `check_api.py --update` 从真实后端采出）—— 后端改字段 → check_api 红 → `--update` → smoke 发现 stub 过时；里面的 `enums.item_kind` 是前端那张 `kind → 渲染` 表必须覆盖的取值域（认不出的在界面上明说「未知条目」）。

**评估**：判据是**世界变成什么样**，不是回复像不像。一个 case = `task.txt` + `fixture/` + `check.sh`（临时目录里跑，退出码 0 算过），走服务端同一条路径、只换根目录；护栏是 `protected.txt` 哈希不许变、跑前跑后 `git status --short` 必须一样。基线（`deepseek-flash`）：两个 case 都 ✅✅ —— 回归信号，不是分数。

## 要做什么

**顺序：0 → 3**，其余按需。

**0. 评估的 agent 侧超时**（前置于一切"换模型/加能力"的实验）：`loop.py` 没有轮数上限，评估里的 agent 循环也没有超时（只有 `check.sh` 有 300s）—— 小模型陷入工具循环会把评估挂死。

1 planning + 可见 UI（已完成）、2 沙箱 + 审批（已完成；缺 OS 级隔离与 CLI 审批入口）、**3 离线生成 VRMA 动作**（下一节）。之后按需：4 token/成本账（`response.usage` 从没读过）· 5 工具注册表单一来源（schema 与散文在两处）· 6 上下文压缩（循环无轮数上限，压缩成了必要配套）· 7 并行工具调用（先定谁可并行，`run_bash` 必须独占）· 8 search（走 API，不用本地 2B）。

## 应用：离线生成 VRMA 动作

**目标**：动作**离线生成**（慢慢试错无所谓），直播只**回放**生成好的 `.vrma` —— 延迟因此从致命变成无关。一个 `.vrma` 是 52 根骨、91 条通道、11.8 秒、**6.4 万个浮点数**，不是语言模型的输出空间，所以分三层：

```
① 意图（LLM）"挥手打招呼，再鞠躬" → ② 火柴人：22 个关节的**位置**序列（程序算）→ ③ 插值 + aim/IK + retarget
   ↑ 校验（规则）→ 文字问题清单 ↺                                          → 相对 rest pose 的旋转 → 写 .vrma
```

中间必须是火柴人：`humanBones` 本身就是"名字 → 节点号"的标准表，22 根非手指骨正好是标准火柴人；**存位置而不是旋转**（可直接插值/镜像/重定向，"脚踩地"就是 `foot.y ≈ 0`）。校验先用规则（脚打滑 / 穿地失衡 / 关节限位 / 自穿模 / 瞬移），"看着别扭"那类再留给对抗网络。

**落到本仓库**：新增工具（`list_motions` / `sample_motion` / `blend` / `verify_motion`）+ 一个动作 case，于是"哪个模型够用"（2B / 8B / 13B）有数字而不是猜；已有 7 个真动作在 `/home/zhong/mydisk/shared/vrma/`，可先拿它们自证。

## 已知的小问题

| 问题 | 说明 |
|---|---|
| 审批不展开调用链 · 批准后以 full-access 跑 | 批准 `bash a` 不展开内部的 `b`/`c`/`d`（审计无进程树）；所以 `workspace-write` 的文件限制**不覆盖 bash**。|
| CLI 无审批入口 · provider 容忍度不同 | `workspace-write` 下 bash 在 CLI 里只能靠 HTTP 端点批准；严格模板（vLLM 的 Qwen）拒收中途出现的第二条 system 消息、云端 API 照收 —— 这类错**只在本地模型上暴露**。|
| thinking 模式必须回传思维链 · 暂停时的回执 | `reasoning_content` 不回传 → 下一次请求 400（已修）；暂停时 `/api/chat` 回执仍是 `state:"ok"` —— 判断"这一轮还没结束"要看 `running`。|
| 其它零碎 | 审计无轮转；中断轮次 `temperature` 是 null；key 轮换没有回归测试；bashrc 删 key 不会让进程忘掉；observed 不跨进程共享；陈旧保护是 check-then-write；契约只钉形状不钉值域；权限变更通知罕见说两遍；Composer 里的停止按钮没有测试。|
