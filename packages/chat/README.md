# chat

React + FastAPI 的 AI 聊天应用。后端 `app.py` 经 chat-agent 调 LLM；前端源码在
`frontend/`，由 esbuild 打包成 `static/app.js`（约 150KB），本目录的 FastAPI 直接发。

```bash
pip install -e ~/mydisk/tools/workbench/packages/chat
python -m chat                 # http://127.0.0.1:8200
systemctl --user restart chat  # 以服务常驻时；日志见 journalctl --user -u chat -f
```

- 运行时配置：`~/.config/chat/chat.env`（`chat.service` 的 `EnvironmentFile`，当前无变量）
- 模型目录：`chat_agent/providers.yaml`，改完**不用重启**（每次请求重读）
- `static/theme/` 是 DSH 上游 token 的逐字拷贝（MIT，许可见其中的 `LICENSE`，别删）。
  **别手改** —— 配色改 `styles.css`，或在自己的表里覆盖同名变量
- 鉴权不在本进程：chat 当前只在局域网可达、**没有密码**（公网入口已撤）。应用层区分
  不出来源，所以这件事不在这里做。要对外时把 chat8200 加回 cpolar 的启动列表
- 智能体能力演进（轨迹落盘 → eval → 进度反馈 → 沙箱 → 注册表 → 压缩 → planning → search）：
  见 `chat-agent/README.md` 的「智能体路线图」
