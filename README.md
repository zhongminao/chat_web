# AI 聊天助手（chat）

React + FastAPI 聊天应用：前端负责展示和提交对话，后端通过 **chat-agent** 调用 LLM（gpt / qwen / deepseek / local_qwen）。

## 目录

```
tools/chat/                 # 仓库根（git 就在这一层）
├── README.md                  # 本文件
├── .gitignore
├── packages/                  # 一个个可独立安装/构建的项目，各自带自己的清单
│   ├── chat/                  # Python 包 chat：FastAPI 应用
│   │   ├── pyproject.toml
│   │   └── chat/
│   │       ├── __main__.py    # 启动入口（python -m chat）
│   │       ├── app.py         # 后端：会话/工作区/工具开关
│   │       └── static/        # 前端**产物**与样式（app.js / index.html / styles.css / theme/）
│   ├── chat-agent/            # Python 包 chat_agent：LLM 客户端库（仅 openai+pyyaml）
│   │   ├── pyproject.toml
│   │   └── chat_agent/
│   │       ├── openai_client.py   # Client（无状态单次对话）
│   │       ├── providers.yaml     # 供应商目录
│   │       └── agent/             # agent 循环 + 四个工具（路线图见该包 README）
│   └── frontend/              # Node 包：前端源码，esbuild 打包
│       ├── package.json
│       ├── smoke.mjs          # UI 冒烟测试（jsdom 里真跑一遍产物）
│       └── src/main.jsx
├── evals/                     # 评估：量 agent 循环的通过率（见 evals/agent/README.md）
│   └── agent/
├── config/                    # 密钥/运行时配置的说明（真文件不进仓库）
├── storage/                   # 运行时数据（**不进 git**）：
│   ├── sessions/<id>.jsonl    #   会话日志：一场对话一个 append-only 文件
│   └── workspaces.json        #   工作区登记表
├── workplace/                 # 一个工作区目录（agent 干活的地方，可自行增删）
└── start_local_qwen.sh        # 本地 Qwen3.5-2B vLLM 服务
```

## 前端构建

```bash
cd packages/frontend && pnpm install   # 首次
pnpm run build                   # 产出 packages/chat/chat/static/app.js（约 170KB）
pnpm run watch                   # 开发时挂着，改完自动重建
pnpm run smoke                   # 只跑 UI 冒烟测试
```

**产物是提交进仓库的**，所以不改前端的人 clone 下来直接能跑，机器上不需要 Node。
Node 只在构建时需要；服务运行时只跑 Python。

> ⚠️ **`packages/chat-agent/`（连字符），不要改成 `chat_agent`。** 目录名若与包名相同，
> 该目录就可能被当成同名**命名空间包**把真包遮蔽掉（这条踩过一次：服务启动即
> `ImportError`）。连字符使该目录无法被 `import`，从根本上排除这种遮蔽。

**一个仓库，三个项目**：`chat`（Web 应用）、`chat_agent`（库）、`frontend`（前端源码，
Node 工具链）。三者用不同的工具链，但改动经常跨项目（agent 循环产出的协议消息 →
后端落进会话日志 → 前端回放），所以 git 合在一起、包各自独立、各自带清单。
`frontend` 的产物**写进 `chat` 包里**并提交进仓库：不碰前端的人 clone 下来直接能跑，
机器上不需要 Node。

## 启动

```bash
pip install -e ~/mydisk/tools/chat/packages/chat        # chat 应用
pip install -e ~/mydisk/tools/chat/packages/chat-agent  # chat-agent 库（装一次即可）
python -m chat [port]                         # 任意目录下启动，默认端口 8200
```

打开：

```text
http://10.23.14.209:8200
```

tmux 里后台常驻：

```bash
tmux new-session -d -s chat 'python -m chat 8200'
```

## 运行说明

- 对话记录在**服务端**：一场对话一个 append-only JSONL（`storage/sessions/<id>.jsonl`），
  历史用重放得到。刷新页面、换浏览器都不会丢；清掉文件就是永久删除那场对话
- 工作区决定 agent 在哪个目录里干活（相对路径的基准、`run_bash` 的 cwd）
- 默认模型 `gpt-5.5`（供应商目录来自 chat-agent 包内的 `providers.yaml`）
- `app.py` 优先从当前 shell 读取 `GPT_API_KEY`，没有则尝试从 `~/.bashrc` 读取
- 其他供应商 key：`QWEN_API_KEY` / `DEEPSEEK_API_KEY` / `LOCAL_QWEN_API_KEY`

## 本地 Qwen（local_qwen provider）

```bash
bash start_local_qwen.sh [port]       # 默认 8001
export LOCAL_QWEN_BASE_URL=http://127.0.0.1:8001/v1
```

模型目录默认 `/home/zhong/mydisk/IM_Opt/LLM/qwen3p5_2b`，可用环境变量覆盖：
`export LOCAL_QWEN_MODEL_DIR=/path/to/model`

## 端口占用

```bash
ss -ltnp '( sport = :8200 )'
```

## 说明

- `~/mydisk/web/chat` 是软链接，指向本目录（旧路径的兼容入口）。**移动/改名仓库时要
  记得一起改** —— 它不跟着 git 走，改名那次就断过一次
- git 历史已随迁移保留（`.git` 在仓库根，跟着目录一起走）
- `chat_agent` 前身叫 `llm_client`，原先在 `tools/llm_client`（**无 git**）。2026-09-12 并入
  本仓库，随后改名 `chat_agent` —— 此后一个仓库维护两个包。
  agent 能力演进路线图见 `packages/chat-agent/README.md`
- 密钥与运行时配置**不进仓库**：真实文件在 `~/.config/{chat,fortrix}/` 与
  `/usr/local/etc/cpolar/cpolar.yml`，仓库里连模板都不放。变量 → 文件的映射表见
  `config/README.md`
- **chat 当前不对外**：cpolar 启动列表里已没有 chat8200，cpolar 本身也是 disabled。
  只在局域网可达，因此**没有密码** —— 安全性建立在"只有局域网连得上"。要对外演示
  时把 chat8200 加回 cpolar.service 的 ExecStart
- **`run_bash` 没有沙箱**：工具只是在工作区根里干活（相对路径/`cwd`），绝对路径照样
  能到任何地方。这是路线图第 5 步（见 `packages/chat-agent/README.md`）
- 运行时数据位置可以用 `CHAT_STORAGE` 钉死（systemd unit 里已显式给成 `<仓库>/storage`）：
  默认值是"跟着代码走"的，代码一挪数据目录就跟着挪，表现成历史对话凭空消失
