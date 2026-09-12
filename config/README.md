# 配置与密钥

**真实密钥不进仓库。** 真实文件都在仓库外，由根 `.gitignore` 的 `*.env` 规则挡在库外。
这里只做一件事：一张表说清每个变量在哪、谁在读。

## 映射表：变量 → 真实文件 → 谁在读

| 变量 / 键 | 真实文件（都在仓库外） | 读取方 |
|---|---|---|
| `GPT_API_KEY` | `~/.bashrc` | `packages/chat/chat/app.py` → `load_env_value_from_bashrc()` |
| `DEEPSEEK_API_KEY` | `~/.bashrc` | 同上 |
| `LOCAL_QWEN_API_KEY` | `~/.bashrc` | 同上（本地 vLLM 无鉴权，占位值） |
| `LOCAL_QWEN_BASE_URL` / `_MODEL_NAME` / `_MODEL_DIR` | shell 环境变量 | `packages/chat/src/chat/providers.yaml` 的 `base_url_env` / `model_name_env`；`start_local_qwen.sh` |
| `CHAT_STORAGE` | `chat.service` 的 `Environment=`（**不是**密钥，写死在 unit 里） | `packages/chat/chat/app.py` —— 会话日志与工作区登记表放哪 |
| `CHAT_WORKSPACE` | 同上 | 同上 —— 登记表空着时兜底登记的那个工作区根 |
| `FORTRIX_*` | `~/.config/fortrix/fortrix.env` | `fortrix.service` 的 `EnvironmentFile=` |
| `authtoken` / 各隧道 `auth` | `/usr/local/etc/cpolar/cpolar.yml` | `cpolar.service` 的 `-config=` |

> 最后两行是同机的另外两个服务，与本仓库无关 —— 列出来只是因为它们历史上与 chat
> 共用 cpolar，排查时最容易混。
>
> **chat 当前不对外**：cpolar 启动列表里已没有 chat8200，且 cpolar 是 disabled。
> `cpolar.yml` 里 chat8200 那个块的 `auth` 只在重新对外演示时才起作用。

`CHAT_STORAGE` / `CHAT_WORKSPACE` 不是密钥，所以直接写在 systemd unit 里，不塞进
EnvironmentFile：它们是**路径**，改了能立刻看出问题，没必要藏起来。
两者的默认值都是"跟着代码走"的（chat 包的同级目录 / 进程 cwd），代码一挪就会跟着
挪 —— 所以 unit 里显式钉死，免得历史对话凭空"消失"。

`~/.config/chat/chat.env` 是 chat.service 的可选 EnvironmentFile，**当前不存在** ——
unit 用 `-` 前缀，文件缺失不影响启动。要加变量再创建它，不必改 unit。

## chat 现在没有密码

局域网直连 8200 不需要密码；公网入口撤掉之后，也就没有"公网要密码"这回事了。
安全性完全建立在"只有局域网连得上"之上。

将来要对外演示，把 chat8200 加回 `cpolar.service` 的 ExecStart，密码就由那一层的
`auth` 负责 —— 为什么不放在应用层：应用层分不清请求来源（cpolar 客户端连的是
localhost，和局域网设备在应用眼里一样），只有边缘那一层才是结构性的。

## 新增配置时

真实值写进上表的真实文件；新增了配置文件本身就在表里补一行。仓库里不放 `*.example`
模板 —— 空模板只是把这张表抄第二遍。
