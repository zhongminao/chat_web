# llm-client

自包含 LLM 客户端库（零项目依赖），提供：

- **`Client`**：无状态单次对话执行者 —— provider 解析、api_key/base_url、调用 OpenAI 兼容接口、响应解析
- **`ConversationSession`**：有状态会话历史容器 —— 多轮对话的 system/user/assistant/tool 消息管理
- **供应商目录**：`providers.yaml`（gpt / qwen / deepseek / local_qwen），随包分发

仅依赖 `openai` 和 `pyyaml`。

本包与 chat 应用同处一个仓库（`tools/chat/llm-client/`），但**仍是可独立安装的包**：
把仓库和包两个边界分开 —— 合的是 git，不是包。理由见仓库根 `README.md`。

项目目录名带**连字符**（`llm-client`）是刻意的：若与包名 `llm_client` 同名，仓库根就会
把真包遮蔽掉（详见仓库根 `README.md` 的警告）。连字符同时与 pyproject 里的发行名一致。

## 智能体路线图（重心在 `llm_client/agent/`，按序勿跳步）

1. **轨迹落盘** —— steps/trace + 元信息落 JSONL；顺手修 `run_bash` 缺失的 20000 字符截断
2. **eval 基线** —— 20~30 条真实任务 + 通过率，作为后续所有改动的裁判
3. **沙箱 + 危险命令审批** —— 公网可达的 `run_bash` 不能无门
4. **上下文压缩** —— planning 的前置；history 现为全量回放、零压缩
5. **planning + 可见 UI** —— plan-then-execute + 每步自检
6. **search** —— 走 API；不用本地 2B（它在 tool calling 上已需三处补丁）

第 3 步之后，每步都须用第 2 步的数字证明没变坏。

## 安装

### 方式一：editable 安装（开发/日常推荐）

```bash
cd ~/mydisk/tools/chat/llm-client
pip install -e .
```

editable 安装 = 环境"链接"到源码目录。效果：

- 任何目录下都能 `import llm_client`
- **改代码 / 改 providers.yaml 都立即生效**（重启使用方进程即可），**不需要重新安装**

### 方式二：普通安装（分发/部署用）

```bash
pip install .          # 把当前源码打包装进环境
# 或
pip install /path/to/llm_client
```

普通安装是把代码**拷贝**进 site-packages —— 之后改源码不会生效，需要重新安装。

## 使用

```python
from llm_client import Client, ConversationSession, create_client, list_providers

# 列出供应商/模型（供前端选择器）
list_providers()

# 单次对话（无状态）
client = create_client(provider="gpt", model_name="gpt-5.4")
reply, metadata = client.request_assistant_message(
    messages=[{"role": "user", "content": "你好"}],
)

# 多轮对话（有状态）
session = client.new_session(system_message="You are helpful.")
session.append_user_message("你好")
reply, metadata = client.complete_session(session)
```

API key 通过环境变量提供（`GPT_API_KEY` / `QWEN_API_KEY` / `DEEPSEEK_API_KEY` / `LOCAL_QWEN_API_KEY`），详见 `llm_client/providers.yaml`。

## 什么情况需要重新安装？

| 情况 | editable（`pip install -e .`） | 普通安装（`pip install .`） |
|---|---|---|
| 改 `llm_client/*.py` 代码 | **不需要**，重启进程即生效 | 需要重装 |
| 改 `providers.yaml` | **不需要**，重启进程即生效 | 需要重装 |
| 改 `pyproject.toml`（包名/版本/依赖） | **需要**重新 `pip install -e .` | 需要重装 |
| 目录搬家 / 改名（如 `llm_client` 移到别处） | **需要**重装（旧链接失效） | 需要重装（拷贝已在旧路径） |
| 换 conda 环境 / 新机器 | **需要**在该环境重新安装 | 需要重装 |

一句话：**editable 模式下改代码不用重装，只有改包元数据或目录搬家才需要。**

## 卸载

```bash
pip uninstall llm-client
```

## 目录结构

```
llm-client/                 # 项目目录：连字符，刻意不与包名同名（见仓库根 README 警告）
├── pyproject.toml          # 包元数据与依赖声明
├── README.md
├── demo_agent_loop.py      # agent 循环的可跑示例（不在包内，不随包分发）
└── llm_client/             # 包目录：下划线，可被 import
    ├── __init__.py         # re-export 公开 API
    ├── openai_client.py    # Client + ConversationSession 实现
    ├── providers.yaml      # 供应商目录（随包分发，__file__ 相对加载）
    ├── test.py             # 空文件
    └── agent/
        ├── __init__.py     # 对外暴露 run_agent_turn / TOOL_SCHEMAS / execute_tool
        ├── loop.py         # run_agent_turn：多轮工具调用循环
        └── tools.py        # 四个工具实现 + schema + execute_tool 分发
```
