# 配置与密钥

**真实密钥不进仓库。** 真实文件都在仓库外，由根 `.gitignore` 的 `*.env` 规则挡在库外。
这里只做一件事：一张表说清每个变量在哪、谁在读。

## 映射表：变量 → 真实文件 → 谁在读

| 变量 / 键 | 真实文件（都在仓库外） | 读取方 |
|---|---|---|
| `GPT_API_KEY` | `~/.bashrc` | `chat/app.py` → `load_env_value_from_bashrc()` |
| `DEEPSEEK_API_KEY` | `~/.bashrc` | 同上 |
| `LOCAL_QWEN_API_KEY` | `~/.bashrc` | 同上（本地 vLLM 无鉴权，占位值） |
| `LOCAL_QWEN_BASE_URL` / `_MODEL_NAME` / `_MODEL_DIR` | shell 环境变量 | `chat_agent/providers.yaml` 的 `base_url_env` / `model_name_env`；`start_local_qwen.sh` |
| `FORTRIX_*` | `~/.config/fortrix/fortrix.env` | `fortrix.service` 的 `EnvironmentFile=` |
| `authtoken` / 各隧道 `auth` | `/usr/local/etc/cpolar/cpolar.yml` | `cpolar.service` 的 `-config=` |

> **chat 的公网密码在最后一行那个文件里**，不在 chat 进程里。最后两行是同机的另外两个
> 服务，与本仓库无关 —— 列出来只是因为它们与 chat 共用 cpolar，排查时最容易混。

`~/.config/chat/chat.env` 是 chat.service 的可选 EnvironmentFile，**当前不存在** ——
unit 用 `-` 前缀，文件缺失不影响启动。要加变量再创建它，不必改 unit。

## 为什么公网密码不在应用里

公网请求必经 cpolar 边缘 → 被挡；局域网直连 8200 不经过它 → 免密。应用层分不清请求
来源（cpolar 客户端连的是 localhost，和局域网设备在应用眼里一样），只能放在边缘做。

## 新增配置时

真实值写进上表的真实文件；新增了配置文件本身就在表里补一行。仓库里不放 `*.example`
模板 —— 空模板只是把这张表抄第二遍。
