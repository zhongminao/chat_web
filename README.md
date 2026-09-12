# AI 聊天助手（chat）

React + FastAPI 的聊天服务，后端带一个能真的动手的 agent（读写文件、跑 bash）。
模型走 OpenAI 兼容接口（gpt / qwen / deepseek / local_qwen）。

## 目录

```
tools/chat/                 # 仓库根（git 就在这一层）
├── README.md                  # 本文件
├── .gitignore
├── packages/                  # 一个个可独立安装/构建的项目，各自带自己的清单
│   ├── chat/                  # 唯一的 Python 包（src 布局）
│   │   ├── pyproject.toml
│   │   ├── demo_agent_loop.py # agent 循环的可跑示例（假 client，不花钱）
│   │   └── src/chat/
│   │       ├── __main__.py    # 启动入口（python -m chat）
│   │       ├── app.py         # 后端：会话/工作区/工具开关
│   │       ├── openai_client.py   # Client（无状态单次对话）
│   │       ├── providers.yaml     # 供应商目录
│   │       ├── agent/             # agent 循环 + 四个工具 + 会话日志（路线图见该包 README）
│   │       └── static/        # 前端**产物**与样式（app.js / index.html / styles.css / theme/）
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

**一个 Python 包 + 一个 Node 包。** Python 那边只有 `chat` —— LLM 客户端、agent 运行时、
FastAPI 应用都在里面（原先拆成 `chat` 和 `chat-agent` 两个发行包，但那个边界从没被用过：
消费者只有本应用一个，而它作为依赖根本解析不了）。`frontend` 是另一套工具链（Node），
只负责产出 `chat` 包里的 `static/app.js`。产物**提交进仓库**，所以不碰前端的人 clone
下来直接能跑，机器上不需要 Node。

Python 包用 **`src/` 布局**：包目录不挨着仓库里其他目录，"同名的普通目录被当成命名空间
包、把真包遮蔽掉"这类事故就物理上不可能（这个坑踩过两次：`llm_client` 那次服务启动即
`ImportError`；`chat-agent` 那次靠目录名带连字符躲过去 —— 现在不需要这种技巧了）。

## 启动

```bash
pip install -e ~/mydisk/tools/chat/packages/chat   # 唯一的 Python 包，装一次
python -m chat [port]                              # 任意目录下启动，默认端口 8200
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
- 默认模型 `gpt-5.5`（供应商目录是包内的 `src/chat/providers.yaml`）
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
- 代码原先是两个发行包（`chat` + `chat-agent`），2026-09-12 合并成一个 `chat`：那个边界
  从没被用过，而且 `chat` 声明依赖 `chat-agent` 时它根本解析不了（那个名字不在任何索引
  上），只有本机两个 editable 包都装了才凑得出来
- 更早的名字：`chat_agent` 前身叫 `llm_client`，原先在 `tools/llm_client`（**无 git**）
- agent 能力演进路线图见 `packages/chat/README.md`
- 密钥与运行时配置**不进仓库**：真实文件在 `~/.config/{chat,fortrix}/` 与
  `/usr/local/etc/cpolar/cpolar.yml`，仓库里连模板都不放。变量 → 文件的映射表见
  `config/README.md`
- **chat 当前不对外**：cpolar 启动列表里已没有 chat8200，cpolar 本身也是 disabled。
  只在局域网可达，因此**没有密码** —— 安全性建立在"只有局域网连得上"。要对外演示
  时把 chat8200 加回 cpolar.service 的 ExecStart
- **`run_bash` 没有沙箱**：工具只是在工作区根里干活（相对路径/`cwd`），绝对路径照样
  能到任何地方。这是路线图第 5 步（见 `packages/chat/README.md`）
- 运行时数据位置可以用 `CHAT_STORAGE` 钉死（systemd unit 里已显式给成 `<仓库>/storage`）：
  默认值是"跟着代码走"的，代码一挪数据目录就跟着挪，表现成历史对话凭空消失
