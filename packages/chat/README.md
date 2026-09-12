# chat

LLM 聊天服务：Web 层是 FastAPI，后端带一个能真的动手的 agent（读写文件、跑 bash）。
**一个包** —— 源码在 `src/chat/`，分层如下（依赖单向，别反过来）：

    src/chat/
    ├── app.py             FastAPI：会话、工作区、工具开关；把 agent 的产出落进会话日志
    ├── __main__.py        python -m chat
    ├── agent/             工具调用循环、四个工具、观测守卫、spill、会话日志、工作区登记
    ├── openai_client.py   provider 目录与无状态 Client（单次对话）
    ├── providers.yaml     供应商/模型目录（随包分发，按 __file__ 相对加载）
    └── static/            前端**产物**与样式（app.js / index.html / styles.css / theme/）

前端源码在 `packages/frontend/`（Node 包，esbuild 打包），产物写进上面的 `static/`。
产物**提交进仓库**，所以不碰前端的人 clone 下来直接能跑，机器上不需要 Node。

用 `src/` 布局不只是通行做法：包目录不挨着仓库里的其他目录，"同名的普通目录被当成
命名空间包、把真包遮蔽掉"这类事故就物理上不可能。这个坑踩过两次（`llm_client` 那次
服务直接起不来；`chat-agent` 那次靠目录名带连字符躲过去）。

```bash
pip install -e ~/mydisk/tools/chat/packages/chat
python -m chat                 # http://127.0.0.1:8200
systemctl --user restart chat  # 以服务常驻时；日志见 journalctl --user -u chat -f
```

- 运行时数据位置由 `CHAT_STORAGE` 决定（unit 里显式给成 `<仓库>/storage`）：会话日志与
  工作区登记表都在那儿。默认值是"跟着代码走"的，代码一挪数据目录就跟着挪，表现成
  历史对话凭空消失 —— 所以显式钉死
- 运行时配置：`~/.config/chat/chat.env`（`chat.service` 的 `EnvironmentFile`，当前无变量）
- 模型目录：`src/chat/providers.yaml`，改完**不用重启**（每次请求重读）
- `static/theme/` 是 DSH 上游 token 的逐字拷贝（MIT，许可见其中的 `LICENSE`，别删）。
  **别手改** —— 配色改 `styles.css`，或在自己的表里覆盖同名变量
- 鉴权不在本进程：chat 当前只在局域网可达、**没有密码**（公网入口已撤）。应用层区分
  不出来源，所以这件事不在这里做。要对外时把 chat8200 加回 cpolar 的启动列表
- **`run_bash` 没有沙箱**：工具只是在工作区根里干活（相对路径按它解析、bash 的 cwd
  是它），绝对路径照样能到任何地方。这是路线图的第 5 步

## 智能体路线图（重心在 `src/chat/agent/`，按序勿跳步）

1. ~~轨迹落盘~~ —— 已完成
2. **eval 基线** —— 真实任务 + 通过率，作为后续所有改动的裁判
3. **read_file 输出总量预算** —— 最坏输出＝行上限×单行上限，量级万级 token 且无总量上界
4. **步边界进度反馈** —— 只推进度不推 token；一次任务 30~50 秒全程无信息，且是第 5 步审批的通道前提
5. **沙箱 + 危险命令审批** —— 公网可达的 `run_bash` 不能无门
6. **工具注册表单一来源** —— schema 在 tools.py、散文在 app.py，加一个工具要改两处
7. **上下文压缩** —— 降级为待证明：瓶颈可能是单次读太胖，不是历史太长
8. **planning + 可见 UI** —— plan-then-execute + 每步自检
9. **search** —— 走 API；不用本地 2B

第 2 步之后，每步都须用第 2 步的数字证明没变坏。

## 什么情况需要重新安装？

| 情况 | editable（`pip install -e .`） | 普通安装（`pip install .`） |
|---|---|---|
| 改 `src/chat/**/*.py` 代码 | **不需要**，重启进程即生效 | 需要重装 |
| 改 `src/chat/providers.yaml` | **不需要**，重启进程即生效 | 需要重装 |
| 改 `pyproject.toml`（包名/版本/依赖） | **需要**重新 `pip install -e .` | 需要重装 |
| 目录搬家 / 改名（如 `packages/chat` 移到别处） | **需要**重装（editable 记的是绝对路径） | 需要重装（拷贝已在旧路径） |
| 换 conda 环境 / 新机器 | **需要**在该环境重新安装 | 需要重装 |

一句话：**editable 模式下改代码不用重装，只有改包元数据或目录搬家才需要。**

## 卸载

```bash
pip uninstall chat
```

## 卸载

```bash
pip uninstall chat
```
