# AI 聊天助手（chat）

React + FastAPI 聊天应用：前端负责展示和提交对话，后端通过 **llm-client** 调用 LLM（gpt / qwen / deepseek / local_qwen）。

## 目录

```
tools/chat/
├── pyproject.toml          # chat 包元数据（deps: fastapi/uvicorn/pydantic/llm-client）
├── chat/
│   ├── __main__.py         # 启动入口（python -m chat）
│   ├── app.py              # FastAPI 后端
│   └── static/             # React 前端（index.html / main.jsx / styles.css）
├── llm_client/             # 独立可安装的 LLM 客户端库（零项目依赖，仅 openai+pyyaml）
│   ├── pyproject.toml
│   └── llm_client/
│       ├── openai_client.py    # Client + ConversationSession
│       ├── providers.yaml      # 供应商目录
│       └── agent/              # agent 循环 + 四个工具（路线图见该包 README）
└── start_local_qwen.sh     # 本地 Qwen3.5-2B vLLM 服务
```

**一个仓库、两个包**：`chat` 是 Web 应用，`llm_client` 是库。二者通过 `trace` 契约耦合
（循环产出 → 接口转出 → 前端回放），改动经常跨包，所以 git 合在一起、包各自独立。

## 启动

```bash
pip install -e ~/mydisk/tools/chat             # chat 应用
pip install -e ~/mydisk/tools/chat/llm_client  # llm-client 库（装一次即可）
python -m chat [port]                          # 任意目录下启动，默认端口 8200
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
- 默认模型为 `gpt-5.4`（供应商目录来自 llm-client 包内的 `providers.yaml`）
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
- `llm_client` 原先在 `tools/llm_client`（**无 git**），2026-09-12 并入本仓库 —— 此后
  一个仓库维护两个包。agent 能力演进路线图见 `llm_client/README.md`
- 密钥与运行时配置**不进仓库**：真实文件在 `~/.config/{chat,fortrix}/` 与
  `/usr/local/etc/cpolar/cpolar.yml`，仓库只应有 `*.env.example` 模板
