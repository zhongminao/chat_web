# chat

React + FastAPI 的聊天服务，后端带一个能真的动手的 agent（读写文件、跑 bash）。
模型走 OpenAI 兼容接口（provider：`gpt` / `deepseek` / `local_qwen`）。

```
tools/chat/                      # 仓库根（git 在这一层）
├── packages/
│   ├── chat/                    # 唯一的 Python 包（src 布局）
│   │   ├── pyproject.toml
│   │   ├── demo_agent_loop.py   # agent 循环的可跑示例（假 client，不花钱）
│   │   └── src/chat/
│   │       ├── app.py           # FastAPI：只留 HTTP（路由 + 请求响应模型）
│   │       ├── runtime.py       # web 与 CLI 共用：路径 / 环境 / 消息归一 / 一轮对话
│   │       ├── run.py           # CLI：python -m chat.run（不经过网页用 agent）
│   │       ├── __main__.py      # 入口：python -m chat
│   │       ├── openai_client.py # Client（无状态单次对话）
│   │       ├── providers.yaml   # 供应商目录
│   │       ├── agent/           # 工具循环 + 四个工具 + 会话日志 + 工作区登记
│   │       └── static/          # 前端**产物**与样式（app.js / index.html / styles.css / theme/）
│   └── frontend/                # Node 包：前端源码，esbuild 打包（package.json / smoke.mjs / src/）
├── evals/agent/                 # 评估：量 agent 循环的通过率（第 2 步，见下）
├── storage/                     # 运行时数据（**不进 git**）：sessions/*.jsonl + workspaces.json
├── workplace/                   # 一个工作区目录（agent 干活的地方，可增删）
└── start_local_qwen.sh          # 本地 Qwen3.5-2B vLLM
```

## 安装与启动

```bash
pip install -e ~/mydisk/tools/chat/packages/chat   # 唯一的 Python 包
python -m chat                                     # 默认 8200，任意目录下都能起
systemctl --user restart chat                      # 常驻服务；日志 journalctl --user -u chat -f
```

局域网访问 `http://10.23.14.209:8200`。**移动或改名仓库之后必须重装上面那个包** ——
editable 记的是绝对路径，路径一换就 `No module named chat`（踩过）；`~/mydisk/web/chat`
那个软链接同理，它不跟着 git 走。

## 命令行用 agent（不经过网页）

```bash
python -m chat.run "把 workplace 里 README 的标题改掉"      # 一轮就退
python -m chat.run --tools "跑一下测试，挂了就修"            # 这一轮允许用工具
python -m chat.run -i                                      # 连着聊，exit 退出
python -m chat.run --list                                  # 列出会话（网页里那些也在）
python -m chat.run -s <会话id> "接着上次那个问题说"           # 续聊某一场
```

- **多轮**：不带 `-i` 也能多轮 —— 每次追加到同一场会话，下次调用时把历史读回来。
  不带 `-s` 时用"上次 CLI 用的那场"（记在 `storage/cli-session`，第一次用 CLI 时创建），`--new` 另开一场。
- **跟网页是同一套东西**：同一份消息归一、同一份一轮逻辑、同一份会话日志格式。
  所以 CLI 里聊的在网页侧栏能看到，网页里聊的也能 `-s <id>` 接着聊。
- **工作区**：默认是登记表里那个默认工作区（跟网页"新对话落在哪"一致），
  `-w <id>` 换。工具就在它的根里干活 —— 相对路径的基准、`run_bash` 的 cwd 都是它。
- **工具开关一场会话内固定**：第一轮 `--tools` 定下来，之后沿用日志里记的值。
  不是限制，是因为关掉工具时 `normalize_messages` 会丢掉历史里的工具协议消息
  （等于静默截断历史），所以服务端在 `/api/chat` 上直接 409 拒绝中途改，CLI 同一条规矩。
- 其它：`-p/-m` 换供应商/模型，`--system` 换系统提示词。

实现上分了两层：`runtime.py`（路径 / 环境 / 消息归一 / 一轮对话，**不 import fastapi**）
与 `app.py`（只留 HTTP：路由 + 请求响应模型）。两个入口共用前者，所以两边不会各飘一套。

## 前端构建

```bash
cd packages/frontend && pnpm install   # 首次
pnpm run build      # 产出 packages/chat/src/chat/static/app.js
pnpm run watch      # 改完自动重建
pnpm run check      # build + smoke（jsdom 里真跑一遍产物）
```

界面那套 smoke 把 fetch 全 stub 掉了 —— 它验的是"给定数据下渲染对不对"，**后端接口
500 它照样全绿**（真被咬过：`/api/providers` 500 的表现是网页上模型选择器整个用不了，
而 smoke 一直是绿的）。所以后端另有一条，两个都要跑：

```bash
python packages/chat/check_api.py   # 把所有路由真调一遍，任何 5xx 就失败
```

产物**提交进仓库**，所以不碰前端的人 clone 下来直接能跑，机器上不需要 Node。

## 配置与密钥

真实密钥不进仓库，也不放 `*.example` 模板（空模板只是把下表抄第二遍）。

| 变量 | 真实文件 | 谁在读 |
|---|---|---|
| `GPT_API_KEY` / `DEEPSEEK_API_KEY` / `LOCAL_QWEN_API_KEY` | `~/.bashrc` | `src/chat/runtime.py` 的 `ensure_runtime_env()`（web 与 CLI 共用） |
| `CHAT_STORAGE` / `CHAT_WORKSPACE` | `chat.service` 的 `Environment=` | `src/chat/runtime.py`：运行时数据位置 / 默认工作区根 |
| `LOCAL_QWEN_MODEL_DIR` / `LOCAL_QWEN_MODEL_NAME` | **可选覆盖**，默认值在脚本里 | `start_local_qwen.sh` —— 只管**启动 vLLM 服务**，chat 不读它们 |

- **`local_qwen` 默认一个环境变量都不用设**：base_url、模型名、api_key 都在 `providers.yaml`
  里。要指向别的机器就改 yaml 的 `base_url` —— 不再有"环境变量覆盖"这条路（曾经有
  `base_url_env` / `model_name_env`，但那条路只靠**继承 shell 环境**，systemd 起的服务
  继承不到，留着只是多一层无效的间接）
- local_qwen 的 `api_key_default: local_qwen` **不是密钥，是占位**：vLLM 不校验 key，
  但 `openai` 客户端库不给非空 key 就直接 `OpenAIError: Missing credentials` 构造不出来。
  真给 vLLM 加了 `--api-key` 时才需要在 `~/.bashrc` 里 `export LOCAL_QWEN_API_KEY="..."`
  —— 注意这条**走的是"读文件"而不是"继承环境"**（`ensure_runtime_env()` 去解析 `~/.bashrc`），
  所以 systemd 服务能拿到。这也是 `api_key_env` 这个键留着、而 `base_url_env` 该删的区别
- `LOCAL_QWEN_MODEL_DIR` / `LOCAL_QWEN_MODEL_NAME` 不属于客户端配置：它们决定 **vLLM 从
  哪个目录加载模型、对外报什么模型名**，归启动脚本管。改 `_MODEL_NAME` 时记得把 yaml 里
  的 `models[0].id` 一起改，否则请求会报模型不存在
- `GPT_API_KEY` 是**文本解析 `~/.bashrc`** 拿的，不是读环境变量：服务由 systemd 启动，
  **继承不到你终端的 shell 环境**（环境变量只在自己那棵进程树里往下传）
- `CHAT_STORAGE` / `CHAT_WORKSPACE` 是**路径**不是密钥，所以直接写在 unit 里。它们的默认值
  "跟着代码走"，代码一挪数据目录就跟着挪、表现成历史对话凭空消失 —— 所以显式钉死
- `~/.config/chat/chat.env` 是 unit 的可选 `EnvironmentFile`，**当前不存在**（unit 用 `-` 前缀，
  缺失不影响启动）。要加变量再创建它，不必改 unit

本地 Qwen：`bash start_local_qwen.sh [port]`（默认 8001）就行，**不用 export 任何东西**；
要指向别的服务就去改 `providers.yaml` 里 `local_qwen` 的 `base_url`。

## 运行说明

- 对话记录在**服务端**：一场一个 append-only JSONL，历史靠重放。刷新、换浏览器都不丢；
  删掉文件就是永久删除那场对话（没有数据库，也没有回收站）
- **没说过话就没有会话文件**：会话 id 由客户端生成，文件只在第一轮写日志时创建。
  所以"点一下工具开关"不会在磁盘上造出一个空会话（以前会，还会在侧栏里留一个
  永远停在「未分组」的 `(空对话)`）
- 工具开关属于**这一场对话**：第一轮定下来，之后不许改（服务端 409 拒绝）——因为
  关掉工具时 `normalize_messages` 会丢掉历史里的工具协议消息，等于静默截断历史。
  还没开始那场对话时拨的开关只是本地草稿，随第一条消息写进那一轮的记录
- **工作区决定 agent 在哪干活**：相对路径按它解析、`run_bash` 的 `cwd` 是它
- 默认是 `deepseek` / `deepseek-flash`：改 `src/chat/runtime.py` 那两个常量即可 ——
  `/api/providers` 会把它们发给界面（**界面的默认值听服务端，不看供应商列表顺序**），
  CLI 也拿它们当默认值
- **`run_bash` 没有沙箱**：绝对路径照样能到任何地方。这是路线图第 5 步

## 安全边界

**当前不对外**：`cpolar.yml` 里的 `chat8200` 隧道已删，cpolar 本身也是 disabled ——
只在局域网可达，因此**没有密码**。要对外就往下加回一个隧道块（服务用
`cpolar start-all -config=...`，加块即生效，不必改 unit）：

```yaml
  chat8200:
    proto: http
    addr: "8200"
    region: cn_vip
    redirect_https: true
    auth: "用户名:密码"     # cpolar 边缘的 Basic Auth（键名就是 auth，不是 http_auth）
```

密码放边缘而不是放在应用里：**只有边缘那层能区分"公网 / 局域网"** —— cpolar 客户端连的是
localhost，在应用眼里和局域网设备完全一样。

## agent 路线图（按序勿跳步）

1. ~~轨迹落盘~~ 已完成
2. ~~eval harness + 基线~~ 已完成（`evals/agent/`，用法见下）—— 之后每步都拿它的数字当裁判
3. **planning + 可见 UI** ← **下一步从这里开始**
4. `read_file` 输出总量预算 + 步边界进度反馈
   （挪到规划**之后**：多步任务才真正会撞输出上限；"进度"也只有有了计划才有意义）
5. 沙箱 + 危险命令审批 —— `run_bash` 不能无门
6. 工具注册表单一来源 —— schema 在 `tools.py`、散文在 `app.py`，加一个工具要改两处
7. 上下文压缩 —— 降级为待证明：瓶颈可能是单次读太胖，不是历史太长
8. search —— 走 API，不用本地 2B

**"按序勿跳步"的意思是尺子要存在、要能重跑，不是"量到完美才能动"。** 数字是快照。

### 下一步：planning（明天从这里开始）

**顺序上先加 case，再做规划。** 现在这两个 case 6~14 步就做完了、根本不需要计划，
拿它们当基线**测不出规划的好处** —— 所以：

1. **加第 3 个 case**：一次任务要 5 步以上（例如"给这个小项目加一个命令行参数、
   补上测试、跑通"）。先跑一遍，把它的基线记下来。
2. **做 planning**：
   - 形状：加一个工具（`update_plan`），模型先给步骤清单、中途更新状态；计划写进会话
     日志（新记录类型 `plan`），前端渲染成清单
   - 为什么用工具而不是"提示词里要求它先输出计划"：循环里本来就有工具这条通道、
     日志里本来就有协议消息、前端本来就在渲染步骤 —— 不必为计划新造一条链
3. **判定标准**（这就是拿尺子量）：
   - 第 3 个 case 的通过率**不降**、步数不显著上涨（涨了说明规划是负收益）
   - 前两个 case 保持 4/4
   - 会话日志里真的出现 `plan` 记录，UI 能看到

跑法：改完 `python evals/agent/run.py --runs 3`，和 `results/` 里上一份对比。

开工前先确认现在这三条是绿的（改坏了也能立刻知道是哪一层）：

```bash
python packages/chat/check_api.py                      # 后端路由（不再有缺 import 那种 500）
cd packages/frontend && pnpm run check                  # 前端产物 + smoke
python evals/agent/run.py --runs 2                      # 基线：应该 4/4
```

### 附：评估 harness 怎么用（第 2 步的产物）

判据是**世界变成什么样**，不是回复像不像。一个 case = `task.txt`（给模型的一句话）
+ `fixture/`（初始目录）+ `check.sh`（判定，在 case 的临时目录里跑，退出码 0 才算过）。

```bash
python evals/agent/run.py                 # 全部 case 各跑一次
python evals/agent/run.py -k rename       # 只跑名字含 rename 的
python evals/agent/run.py --runs 3        # 每 case 三次（模型不确定，单次结果没意义）
python evals/agent/run.py -m gpt-5.5      # 换模型跑，比一比
python evals/agent/run.py --keep          # 留住临时目录，好进去看
```

- **走的是服务端同一条路径**：同一个 `TurnRequest` → `normalize_messages` → agent 循环，
  只是把根目录换成 case 的临时目录。所以系统提示词怎么拼、工具怎么给，跟真实使用一致。
- **两条护栏**（每个 case 自动带，不用各自实现）：`protected.txt` 里列的文件哈希不许变
  （挡住"改测试让它过"）；跑前跑后各取一次仓库 `git status --short`，必须一模一样
  （没有沙箱，这是唯一能抓住"agent 跑到 case 目录外乱改"的办法）。
- 结果落 `results/<时间戳>.jsonl`（append-only，**进 git**），带模型名/温度/日期/git 版本
  —— 两次跑能 diff 出"这次改动让哪个 case 变坏了"。轨迹另存 `sessions/`（不进 git），
  格式与服务端会话日志相同，卡住时能翻。
- **报错与"没做对"分开统计**：模型/网关层面没跑起来（记录里的 `error`）不计入通过率。
  供应商抽风会把通过率打下去，那不是 agent 的能力问题，混在一起数字就没法看了 ——
  那个 gpt 网关就偶发 400（同样的请求复现四次全成功，是它不稳定，不是代码）。
  **agent 相关的事就用 `deepseek-flash` 量。**
- **不需要服务端、前端、storage**：只 import 包。

第一份基线（2026-09-12，deepseek-flash，每 case 1 次）：

| case | 结果 | 步数 |
|---|---|---|
| `fix-until-green` | ✅ | 6 |
| `rename-across-files` | ✅ | 13 |

> 两个 case 都是**多步**任务（定位 → 读 → 改 → 跑验证），不是一次调用就能完事的小题。
> 但只有 2 个 case 时，这些数字是**回归信号**，不是"我的 agent 有 100 分"，别当排行榜用。

待补：**token/成本报不出来**（provider 返回的 `usage` 从没被读，要报成本得让 client
把它带出来、由循环跨轮累加）；第 1 层（脚本化 client 的不花钱 case）也还没做 ——
那层复用同一批 fixture，只换 driver。

## 沿革

- 代码原是两个发行包（`chat` + `chat-agent`），2026-09-12 合并成一个 `chat`：那个边界
  从没被用过，而且 `chat` 声明依赖 `chat-agent` 时根本解析不了（那名字不在任何索引上）
- 更早：`chat_agent` 前身叫 `llm_client`，在 `tools/llm_client`（无 git）
- 用 `src/` 布局是为了**根除遮蔽事故**：包目录不挨着仓库里其他目录，"同名的普通目录被
  当成命名空间包、把真包盖掉"就不可能发生（踩过两次，其中一次服务启动即 `ImportError`）
