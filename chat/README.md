# chat

React + FastAPI 的 AI 聊天应用。后端 `app.py` 经 llm-client 调 LLM；前端是浏览器里直接跑的
React（无构建、无 Node —— JSX 由网页里的 Babel 现场编译，代价是首屏约 3MB）。

```bash
pip install -e ~/mydisk/tools/chat
python -m chat                 # http://127.0.0.1:8200
systemctl --user restart chat  # 以服务常驻时；日志见 journalctl --user -u chat -f
```

- 运行时配置：`~/.config/chat/chat.env`（`chat.service` 的 `EnvironmentFile`，当前无变量）
- 模型目录：`llm_client/providers.yaml`，改完**不用重启**（每次请求重读）
- `static/theme/` 是 DSH 上游 token 的逐字拷贝（MIT，许可见其中的 `LICENSE`，别删）。
  **别手改** —— 配色改 `styles.css`，或在自己的表里覆盖同名变量
- 鉴权不在本进程：公网密码由 cpolar 边缘负责，局域网直连免密码。应用层区分不出来源，
  所以这件事不在这里做
