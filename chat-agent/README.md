# chat-agent

agent 运行时与 LLM 客户端（零项目依赖），提供：

- **`Client`**：无状态单次对话执行者 —— provider 解析、api_key/base_url、调用 OpenAI 兼容接口、响应解析
- **供应商目录**：`providers.yaml`（gpt / qwen / deepseek / local_qwen），随包分发
- **`agent/`**：工具调用循环（read/write/edit/bash）+ 观测守卫 + spill + **会话日志**

仅依赖 `openai` 和 `pyyaml`。

本包与 chat 应用同处一个仓库（`tools/chat/chat-agent/`），但**仍是可独立安装的包**：
把仓库和包两个边界分开 —— 合的是 git，不是包。理由见仓库根 `README.md`。

项目目录名带**连字符**（`chat-agent`）是刻意的：若与包名 `chat_agent` 同名，仓库根就会
把真包遮蔽掉（详见仓库根 `README.md` 的警告）。连字符同时与 pyproject 里的发行名一致。

## 智能体路线图（重心在 `chat_agent/agent/`，按序勿跳步）

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

## 安装

### 方式一：editable 安装（开发/日常推荐）

```bash
cd ~/mydisk/tools/chat/chat-agent
pip install -e .
```

editable 安装 = 环境"链接"到源码目录。效果：

- 任何目录下都能 `import chat_agent`
- **改代码 / 改 providers.yaml 都立即生效**（重启使用方进程即可），**不需要重新安装**

### 方式二：普通安装（分发/部署用）

```bash
pip install .          # 把当前源码打包装进环境
# 或
pip install /path/to/chat_agent
```

普通安装是把代码**拷贝**进 site-packages —— 之后改源码不会生效，需要重新安装。

## 使用

```python
from chat_agent import Client, create_client, list_providers

# 列出供应商/模型（供前端选择器）
list_providers()

# 单次对话（无状态）
client = create_client(provider="gpt", model_name="gpt-5.5")
reply, metadata = client.request_assistant_message(
    messages=[{"role": "user", "content": "你好"}],
)
```

多轮对话不在这里 —— 历史由 `agent.session_store` 的会话日志承载（一个会话一个
append-only JSONL，历史用重放得到）。原先的 `ConversationSession` 与
`Client.new_session()` / `complete_session()` 已于 2026-09-12 删除：内存里再存
一份历史，就会和落盘的记录对不上。

API key 通过环境变量提供（`GPT_API_KEY` / `QWEN_API_KEY` / `DEEPSEEK_API_KEY` / `LOCAL_QWEN_API_KEY`），详见 `chat_agent/providers.yaml`。

## 什么情况需要重新安装？

| 情况 | editable（`pip install -e .`） | 普通安装（`pip install .`） |
|---|---|---|
| 改 `chat_agent/*.py` 代码 | **不需要**，重启进程即生效 | 需要重装 |
| 改 `providers.yaml` | **不需要**，重启进程即生效 | 需要重装 |
| 改 `pyproject.toml`（包名/版本/依赖） | **需要**重新 `pip install -e .` | 需要重装 |
| 目录搬家 / 改名（如 `chat_agent` 移到别处） | **需要**重装（旧链接失效） | 需要重装（拷贝已在旧路径） |
| 换 conda 环境 / 新机器 | **需要**在该环境重新安装 | 需要重装 |

一句话：**editable 模式下改代码不用重装，只有改包元数据或目录搬家才需要。**

## 卸载

```bash
pip uninstall chat-agent
```

## 目录结构

```
chat-agent/                 # 项目目录：连字符，刻意不与包名同名（见仓库根 README 警告）
├── pyproject.toml          # 包元数据与依赖声明
├── README.md
├── demo_agent_loop.py      # agent 循环的可跑示例（不在包内，不随包分发）
└── chat_agent/             # 包目录：下划线，可被 import
    ├── __init__.py         # re-export 公开 API
    ├── openai_client.py    # Client 实现（无状态）
    ├── providers.yaml      # 供应商目录（随包分发，__file__ 相对加载）
    └── agent/
        ├── __init__.py     # 对外暴露 run_agent_turn / TOOL_SCHEMAS / execute_tool / session_store
        ├── loop.py         # run_agent_turn：多轮工具调用循环
        ├── tools.py        # 四个工具实现 + schema + execute_tool 分发
        ├── observed.py     # 观测状态：先读后改 + 变更提醒
        ├── spill.py        # 超长输出全文落盘，内联只留预览 + 定位符
        └── session_store.py # 会话日志：append-only JSONL，历史用重放得到
```
