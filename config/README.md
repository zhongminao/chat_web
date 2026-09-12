# 配置与密钥

**真实密钥不进仓库。** 真实文件全部在仓库之外，由根 `.gitignore` 的 `*.env` 规则挡在库外。
这里只做一件事：一张表说清每个变量在哪、谁在读 —— 省得再去翻 `~/.config`、`~/.bashrc`
和 `/usr/local/etc`。

## 映射表：变量 → 真实文件 → 谁在读

| 变量 / 键 | 真实文件（都在仓库外） | 读取方 |
|---|---|---|
| `GPT_API_KEY` | `~/.bashrc` | `chat/app.py` → `load_env_value_from_bashrc()` |
| `DEEPSEEK_API_KEY` | `~/.bashrc` | 同上 |
| `LOCAL_QWEN_API_KEY` | `~/.bashrc` | 同上（本地 vLLM 无鉴权，占位值） |
| `LOCAL_QWEN_BASE_URL` / `_MODEL_NAME` / `_MODEL_DIR` | shell 环境变量 | `llm_client/providers.yaml` 的 `base_url_env` / `model_name_env`；`start_local_qwen.sh` |
| *（当前无变量）* | `~/.config/chat/chat.env` | `chat.service` 的 `EnvironmentFile=` |
| `FORTRIX_*` | `~/.config/fortrix/fortrix.env` | `fortrix.service` 的 `EnvironmentFile=` |
| `authtoken` / 每条隧道的 `auth` | `/usr/local/etc/cpolar/cpolar.yml` | `cpolar.service` 的 `-config=` |

> **chat 的公网密码在最后一行那个 cpolar 文件里**，不在 `chat.env`，也不在 chat 进程里。
> 最后两行是同机的另外两个服务，与本仓库无关 —— 列在这里只是因为它们和 chat 共用
> cpolar 配置，排查时最容易混。

## `~/.config/chat/chat.env` 是空的，但不能删

它**必须存在**。`chat.service` 的 `EnvironmentFile=` 没有 `-` 前缀，实测文件缺失时
systemd 直接拒绝启动：

```text
chat.service: Failed to load environment files: No such file or directory
```

它当前不含任何变量（公网密码由 cpolar 边缘负责），所以是空的，不是漏了。

## 为什么公网密码不在应用里

公网请求必经 cpolar 边缘 → 被挡；局域网直连 8200 不经过它 → 免密。
应用层分不清请求来源（cpolar 客户端连的是 localhost，和局域网设备在应用眼里一样），
所以这件事只能放在边缘做。详见 `chat/app.py` 里那段注释。

## 新增配置时

1. 真实值写进上表的真实文件（**永远不要写进仓库**）
2. 若新增了配置文件本身，在映射表补一行
3. 仓库里不放 `*.example` 模板 —— 空模板只是把这张表抄第二遍，照表重建即可
