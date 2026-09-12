# 配置与密钥

**真实密钥不进仓库。** 这里只有模板（`*.env.example` / `*.example`）和下面这张映射表。
真实文件全部在仓库之外，由根 `.gitignore` 的 `*.env` 规则挡在库外。

## 映射表：变量 → 真实文件 → 谁在读

| 变量 / 键 | 真实文件（都在仓库外） | 读取方 |
|---|---|---|
| `GPT_API_KEY` | `~/.bashrc` | `chat/app.py` → `load_env_value_from_bashrc()` |
| `DEEPSEEK_API_KEY` | `~/.bashrc` | 同上 |
| `LOCAL_QWEN_API_KEY` | `~/.bashrc` | 同上（本地 vLLM 无鉴权，占位值） |
| `LOCAL_QWEN_BASE_URL` / `_MODEL_NAME` / `_MODEL_DIR` | shell 环境变量 | `llm_client/providers.yaml` 的 `base_url_env` / `model_name_env`；`start_local_qwen.sh` |
| *（当前无变量）* | `~/.config/chat/chat.env` | `chat.service` 的 `EnvironmentFile=` |
| `FORTRIX_BASIC_AUTH_*` / `FORTRIX_DEBUG` / `FORTRIX_SECRET_KEY` | `~/.config/fortrix/fortrix.env` | `fortrix.service` 的 `EnvironmentFile=` |
| `authtoken` / 每条隧道的 `auth` | `/usr/local/etc/cpolar/cpolar.yml` | `cpolar.service` 的 `-config=` |

> **chat 的公网密码在最后一行那个 cpolar 文件里**，不在 `chat.env`，也不在 chat 进程里。

## 为什么公网密码不在应用里

公网请求必经 cpolar 边缘 → 被挡；局域网直连 8200 不经过它 → 免密。
应用层分不清请求来源（cpolar 客户端连的是 localhost，和局域网设备在应用眼里一样），
所以这件事只能放在边缘做。详见 `chat/app.py` 里那段注释。

## 新增配置时

1. 在对应的 `config/*.example` 里加上变量名和注释说明
2. 真实值写进上面那张表里的真实文件（**永远不要写进仓库**）
3. 若新增了配置文件本身，在映射表补一行
