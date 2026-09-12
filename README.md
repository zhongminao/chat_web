# AI 聊天助手（chat）

React + FastAPI 聊天应用：前端负责展示和提交对话，后端通过 **chat-agent** 调用 LLM（gpt / qwen / deepseek / local_qwen）。

## 目录

```
tools/chat/
├── pyproject.toml          # chat 包元数据（deps: fastapi/uvicorn/pydantic/chat-agent）
├── chat/
│   ├── __main__.py         # 启动入口（python -m chat）
│   ├── app.py              # FastAPI 后端
│   └── static/             # 前端**产物**与样式（app.js / index.html / styles.css / theme/）
├── frontend/               # 前端源码（esbuild 构建，产物写到 chat/static/app.js）
│   ├── package.json
│   ├── smoke.mjs           # UI 冒烟测试（jsdom 里真跑一遍产物）
│   └── src/main.jsx
├── chat-agent/             # 独立可安装的 LLM 客户端库（零项目依赖，仅 openai+pyyaml）
│   ├── pyproject.toml
│   └── chat_agent/
│       ├── openai_client.py    # Client（无状态单次对话）
│       ├── providers.yaml      # 供应商目录
│       └── agent/              # agent 循环 + 四个工具（路线图见该包 README）
├── traces/                 # 轨迹（不进 git）
├── sessions/               # 会话日志（不进 git）
└── start_local_qwen.sh     # 本地 Qwen3.5-2B vLLM 服务
```

## 前端构建

```bash
cd frontend && pnpm install      # 首次
pnpm run build                   # 产出 chat/static/app.js（约 150KB）
pnpm run watch                   # 开发时挂着，改完自动重建
pnpm run smoke                   # 只跑 UI 冒烟测试
```

**产物是提交进仓库的**，所以不改前端的人 clone 下来直接能跑，机器上不需要 Node。
Node 只在构建时需要；服务运行时只跑 Python。

> ⚠️ **项目目录叫 `chat-agent`（连字符），不要改成 `chat_agent`。** 目录名若与包名
> 相同，仓库根就成了 `sys.path[0]` 上的同名**命名空间包**，会把真包遮蔽掉 ——
> `chat.service` 的 `WorkingDirectory` 正是仓库根，后果是服务启动即 `ImportError`。
> 连字符使该目录无法被 `import`，从根本上排除这种遮蔽。

**一个仓库、两个包**：`chat` 是 Web 应用，`chat_agent` 是库。二者通过 `trace` 契约耦合
（循环产出 → 接口转出 → 前端回放），改动经常跨包，所以 git 合在一起、包各自独立。

## 启动

```bash
pip install -e ~/mydisk/tools/chat            # chat 应用
pip install -e ~/mydisk/tools/chat/chat-agent # chat-agent 库（装一次即可）
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

- 当前对话记录保存在前端内存里，刷新页面会清空
- 默认模型为 `gpt-5.4`（供应商目录来自 chat-agent 包内的 `providers.yaml`）
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

- `~/mydisk/web/chat` 现在是**软链接**，指向本目录（旧路径兼容，可放心使用）
- git 历史已随迁移保留（tools/chat/.git）
- `chat_agent` 前身叫 `llm_client`，原先在 `tools/llm_client`（**无 git**）。2026-09-12 并入
  本仓库，随后改名 `chat_agent` —— 此后一个仓库维护两个包。
  agent 能力演进路线图见 `chat-agent/README.md`
- 密钥与运行时配置**不进仓库**：真实文件在 `~/.config/{chat,fortrix}/` 与
  `/usr/local/etc/cpolar/cpolar.yml`，仓库里连模板都不放。变量 → 文件的映射表见
  `config/README.md`
- **chat 当前不对外**：cpolar 启动列表里已没有 chat8200，cpolar 本身也是 disabled。
  只在局域网可达，因此**没有密码** —— 安全性建立在"只有局域网连得上"。要对外演示
  时把 chat8200 加回 cpolar.service 的 ExecStart
