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
│   │       ├── app.py           # FastAPI：会话 / 工作区 / 工具开关
│   │       ├── __main__.py      # 入口：python -m chat
│   │       ├── openai_client.py # Client（无状态单次对话）
│   │       ├── providers.yaml   # 供应商目录
│   │       ├── agent/           # 工具循环 + 四个工具 + 会话日志 + 工作区登记
│   │       └── static/          # 前端**产物**与样式（app.js / index.html / styles.css / theme/）
│   └── frontend/                # Node 包：前端源码，esbuild 打包（package.json / smoke.mjs / src/）
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

## 前端构建

```bash
cd packages/frontend && pnpm install   # 首次
pnpm run build      # 产出 packages/chat/src/chat/static/app.js
pnpm run watch      # 改完自动重建
pnpm run check      # build + smoke（jsdom 里真跑一遍产物）
```

产物**提交进仓库**，所以不碰前端的人 clone 下来直接能跑，机器上不需要 Node。

## 配置与密钥

真实密钥不进仓库，也不放 `*.example` 模板（空模板只是把下表抄第二遍）。

| 变量 | 真实文件 | 谁在读 |
|---|---|---|
| `GPT_API_KEY` / `DEEPSEEK_API_KEY` / `LOCAL_QWEN_API_KEY` | `~/.bashrc` | `src/chat/app.py` 的 `ensure_runtime_env()` |
| `CHAT_STORAGE` / `CHAT_WORKSPACE` | `chat.service` 的 `Environment=` | `src/chat/app.py`：运行时数据位置 / 默认工作区根 |
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
- **工作区决定 agent 在哪干活**：相对路径按它解析、`run_bash` 的 `cwd` 是它
- 默认模型 `gpt-5.5`
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
2. **eval 基线** —— 真实任务 + 通过率，作为后续所有改动的裁判
3. `read_file` 输出总量预算 —— 最坏输出＝行上限 × 单行上限，量级万级 token 且无总量上界
4. 步边界进度反馈 —— 只推进度不推 token；也是第 5 步审批的通道前提
5. 沙箱 + 危险命令审批 —— `run_bash` 不能无门
6. 工具注册表单一来源 —— schema 在 `tools.py`、散文在 `app.py`，加一个工具要改两处
7. 上下文压缩 —— 降级为待证明：瓶颈可能是单次读太胖，不是历史太长
8. planning + 可见 UI
9. search —— 走 API，不用本地 2B

### 第 2 步怎么做（代码将放在 `evals/agent/`）

判据是**世界变成什么样**，不是回复像不像。一个 case = fixture + 任务 + **机器可判的判定**
（JSON 能 `load`、脚本输出对、文件 hash 没变、case 目录之外没有新文件）。

- 第 1 层（不花钱）：脚本化 client（`demo_agent_loop.py` 里那个 `FakeClient` 就是种子），
  断言 loop 机制与工具语义 —— 该停就停、报错后能继续、`tool_call_id` 配对、elision 头尾、
  spill 落盘与取回、守卫三条路径
- 第 2 层（真模型）：每 case 重复 N 次，报 pass@1 / pass^N + 轮数 + 耗时 + token
- 结果落 append-only JSONL，带模型名 / 温度 / 日期 / `git rev-parse` —— 这样两次跑能 diff 出
  "这次改动让哪个 case 变坏了"
- **不需要服务端、前端、storage**：只 import 包，`make_executor(临时目录)` 一 case 一目录

> 缺口：**token 从来没被记录**（provider 返回的 `usage` 一次都没读），要报成本得先补。
> 另外只有 5~10 个 case 时，数字是回归信号，不是"我的 agent 有 87 分"，别当排行榜用。

## 沿革

- 代码原是两个发行包（`chat` + `chat-agent`），2026-09-12 合并成一个 `chat`：那个边界
  从没被用过，而且 `chat` 声明依赖 `chat-agent` 时根本解析不了（那名字不在任何索引上）
- 更早：`chat_agent` 前身叫 `llm_client`，在 `tools/llm_client`（无 git）
- 用 `src/` 布局是为了**根除遮蔽事故**：包目录不挨着仓库里其他目录，"同名的普通目录被
  当成命名空间包、把真包盖掉"就不可能发生（踩过两次，其中一次服务启动即 `ImportError`）
