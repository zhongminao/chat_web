# AI 聊天助手（chat）

React + FastAPI 聊天应用：前端负责展示和提交对话，后端通过 **llm-client** 调用 LLM（gpt / qwen / deepseek / local_qwen）。

## 目录

```
tools/chat/
├── pyproject.toml          # 包元数据（deps: fastapi/uvicorn/pydantic/llm-client）
├── chat/
│   ├── __main__.py         # 启动入口（python -m chat）
│   ├── app.py              # FastAPI 后端
│   └── static/             # React 前端（index.html / main.jsx / styles.css）
└── start_local_qwen.sh     # 本地 Qwen3.5-2B vLLM 服务
```

## 启动

```bash
pip install -e ~/mydisk/tools/chat    # 首次安装（改代码后无需重装，editable）
python -m chat [port]                 # 任意目录下启动，默认端口 8200
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
