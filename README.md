# AI 聊天助手

这是一个 React + FastAPI 的聊天应用，前端负责展示和提交对话，后端调用 GPT 返回回复。

## 目录

- `app.py`: FastAPI 后端
- `run.py`: 无缓冲前台启动入口
- `static/index.html`: 页面入口
- `static/main.jsx`: React 前端代码
- `static/styles.css`: 样式

## 启动

```bash
cd ~/mydisk/web/chat
python -u run.py 8200
```

打开：

```text
http://10.23.14.209:8200
```

## 运行说明

- 当前对话记录保存在前端内存里
- 刷新页面后，当前对话记录会清空
- 默认模型为 `gpt-5.4`
- `app.py` 会优先从当前 shell 读取 `GPT_API_KEY`，如果没有，再尝试从 `~/.bashrc` 里读取

## 端口占用

如果端口被占用，直接看 PID：

```bash
ss -ltnp '( sport = :8200 )'
```

改端口：

```bash
python -u run.py 8300
```

退出：

```bash
Ctrl+C
```

这会同时关掉 uvicorn 子进程。


