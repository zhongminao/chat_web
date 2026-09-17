import inspect
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

from chat.agent import bash_audit
from chat.agent.observed import ObservationContext
from chat.agent.sandbox import DEFAULT_SANDBOX_MODE, SandboxPolicy, check_path, normalize_mode
from chat.agent.spill import save as save_spill

# 工具输出的内联上限。bash 按整体掐（超长另存 spill 文件）；read_file 按行分页，
# 单行过长则原地截断。
#
# 数值随时可调，所以**任何地方都不要写具体数字** —— 包括 schema 描述和注释。
# 描述只讲行为（"超长会截断"），不讲阈值，改了值不会有东西过期。
BASH_OUTPUT_LIMIT = 500
BASH_OUTPUT_HEAD = 200
BASH_OUTPUT_TAIL = 300

READ_LINE_LIMIT = 200
READ_LINE_HEAD = 130
READ_LINE_TAIL = 70

# run_bash 的时限：默认 60 秒，最长 3600 秒。
#
# 这一对与上面那些输出上限**性质不同**：它要写进 schema。模型得知道"不传会怎样、
# 传太大会怎样"，才谈得上要不要显式给值 —— 而输出上限只要说"超长会截断"就够，
# 具体数字对模型没用。上面那条"不要写具体数字"防的是**抄死的字面量**过期；
# 这里 schema 里那句是用 f-string 从下面这两个变量**生成**的，不是另抄一份，
# 所以改了值描述跟着变，过期依然不成立。
BASH_TIMEOUT_DEFAULT = 60
BASH_TIMEOUT_MAX = 3600

def _clamp_bash_timeout(timeout)->int:
    """把模型给的 timeout 收进 [1, BASH_TIMEOUT_MAX]；缺失或非法一律回默认值。

    模型这一侧不可信：小模型会传字符串、0、负数、几百万秒。这里**刻意不报错**，
    静默夹进合法区间 —— 超时只是个旋钮，不值得为它废掉整条命令。
    字符串形态（"120"）在 execute_tool 的 _NUMERIC_FIELDS 那一步已转成 int，
    这里只兜剩下的。**上限因此是硬性的**：模型给多大都越不过 BASH_TIMEOUT_MAX。
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return BASH_TIMEOUT_DEFAULT
    # NaN 与任何值比较都是 False，所以 <= 0 拦不住它；JSON 里确实能写出 NaN。
    if timeout != timeout or timeout <= 0:
        return BASH_TIMEOUT_DEFAULT
    return min(int(timeout), BASH_TIMEOUT_MAX)


# ---------------------------------------------------------------------------
# 基准目录（root）
# --------------------------------------------------------------------------
# 四个工具都在某个目录里干活，这个目录由**调用方显式给**，不从环境里猜：
#   - 服务端给的是这场会话所属工作区的根 —— 界面上选哪个工作区，agent 就在哪干活；
#   - 评估时给的是一个临时目录 —— 一个 case 一个目录，互不污染、跑完就删；
#   - None 才回落到进程当前目录（只在随手调工具时用）。
#
# 以前这里全是隐式的：run_bash 不给 cwd、文件路径按进程 cwd 解析。后果是
# 工作区在界面上能选、能分组，但 agent **始终在服务进程的目录里操作** ——
# 选了等于没选，而且没有任何地方会报错。
#
# 注意这**不是沙箱**：绝对路径照用，`../` 也可能走出 root。这里只决定"基准在哪"，
# 拦住越界是沙箱那一步（要判断目标在不在某个工作区根下面）。
# ---------------------------------------------------------------------------
def root_dir(root: "str | Path | None") -> Path:
    """把 root 规范成绝对目录；None = 进程当前目录。"""
    if root is None:
        return Path.cwd()
    return Path(root).expanduser().resolve()


def resolve_path(path: "str | Path", root: "str | Path | None",
                 *, sandbox_mode: str | None = DEFAULT_SANDBOX_MODE) -> Path:
    """相对路径按 root 解析，并按 sandbox 模式检查最终真实路径。"""
    return check_path(path, SandboxPolicy(normalize_mode(sandbox_mode), root_dir(root)))

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file and return its content. "
            "Use this to inspect any file before editing it. "
            "The output starts with the file path, total line count, and the "
            "line range returned. Large files are read page by page: when more "
            "lines remain, the output ends with a hint telling you the next "
            "offset to continue from. An extremely long line is shortened in "
            "place, keeping its beginning and end with a marker for what was "
            "cut — if you need such a line verbatim (for edit_file), fetch it "
            "with run_bash instead, e.g. sed a line range."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file, relative to the working directory or absolute.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based line number to start reading from.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many lines to read.",
                },
            },
            "required": ["path"],
        },
    },
}

EDIT_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Replace exactly one occurrence of old_text with new_text in a file. "
            "old_text must be copied character-for-character from the file "
            "(read_file first, never invent content), and must match exactly once; "
            "errors tell you if it is missing or ambiguous. You must have read the "
            "file in this session. If it changed on disk since that read you will "
            "be stopped once and told how much it changed — re-read the part your "
            "change depends on before retrying."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file, relative to the working directory or absolute.",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text currently in the file to replace. Copy it verbatim from read_file output.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text.",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
}

PLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "plan",
        "description": (
            "Create or replace the current task checklist for longer multi-step work. "
            "Use it when the task has several dependent steps, may need investigation and verification, "
            "or will take enough tool work that visible progress helps. Do not use it for simple questions "
            "or single-step edits. Send the complete checklist every time; the latest call is the current plan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "Complete task checklist in display order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Short user-visible task description.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Current task status.",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
}

RUN_BASH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": (
            "Run a bash command and return combined stdout/stderr. "
            "In full-access mode the command runs immediately. In workspace-write mode it "
            "creates a one-time approval request for this exact command and pauses; approval "
            "runs only this command and does not switch the session to full-access. "
            "Use for listing files, searching (grep), git, or running programs. "
            "Output that is too long is elided in the middle — you get the "
            "beginning and the end — but it is never lost: the full text is saved "
            "to a file whose path is reported at the end of the result. When you "
            "need what was elided, grep or sed that file with run_bash. A command "
            "killed by timeout or a non-zero exit code is reported in the output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        f"Timeout in seconds. Defaults to {BASH_TIMEOUT_DEFAULT}; "
                        f"anything larger is capped at {BASH_TIMEOUT_MAX}. "
                        "The whole process group is killed on expiry."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}

WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write content to a file. Creates the file if it does not exist, "
            "overwrites it if it does. Parent directories are created "
            "automatically. Use ONLY for new files or complete rewrites; for a "
            "small targeted change in an existing file, use edit_file instead. "
            "Overwriting an existing file requires having read it in this session; "
            "if it changed on disk since then you will be stopped once and told how "
            "much it changed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file, relative to the working directory or absolute.",
                },
                "content": {
                    "type": "string",
                    "description": "Full content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
}

def _elide_line(line:str)->str:
    """单行过长就地截断，留头也留尾。

    必须head+tail，不能只留头：`import {a, b} from "x"` 的模块名、SQL 的 FROM
    表名、日志行的报错都落在行尾，只留头会把最该看的部分切掉。

    刻意不含换行 —— 一旦插了换行，行号就会错位，offset/limit 分页随之失效。
    """
    if len(line) <= READ_LINE_LIMIT:
        return line
    omitted = len(line) - READ_LINE_HEAD - READ_LINE_TAIL
    return (
        line[:READ_LINE_HEAD]
        + f"...[{omitted} chars cut]..."
        + line[-READ_LINE_TAIL:]
    )


def read_file(path:str,offset=1,limit=500,*,root=None,observed:ObservationContext,
              sandbox_mode: str | None = DEFAULT_SANDBOX_MODE)->str:
    target = resolve_path(path, root, sandbox_mode=sandbox_mode)
    if offset < 1:
        offset = 1
    if limit <= 0:
        limit = 500
    try:
        with open(target,"r",encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        raise ValueError(f"read {target} failed, error:{exc}")

    lines = content.splitlines()
    total = len(lines)

    if total == 0:
        # 空文件也要记：以前这里直接 return，于是"读过空文件"不算观测到，
        # 后面一次 write_file 会被守卫拦成 not read yet —— 读过了却说不算，没道理。
        observed.remember(target, lines=0)
        return f"[read_file] {target}: empty file (0 lines)"

    if offset > total:
        raise ValueError(f"offset={offset} is beyond end of file ({total} lines total): {target}")

    start = offset - 1
    chunk = lines[start:start+limit]
    end = start + len(chunk)
    body = "\n".join(_elide_line(line) for line in chunk)
    observed.remember(target, lines=total)
    result = f"[read_file] {target} ({total} lines total, showing lines {start + 1}-{end})\n{body}"
    if end < total:
        result += f"\n...[{total - end} more lines in file. Use offset={end + 1} to continue.]"
    return result

def edit_file(path:str,old_text:str,new_text:str,*,root=None,observed:ObservationContext,
              sandbox_mode: str | None = DEFAULT_SANDBOX_MODE)->str:
    target = resolve_path(path, root, sandbox_mode=sandbox_mode)
    reason = observed.guard(target)
    if reason:
        raise ValueError(f"cannot edit {target}: {reason}")
    try:
        with open(target,"r",encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise ValueError(f"File {target} Not Found")
    except UnicodeDecodeError:
        raise ValueError(f"File {target} decoding with utf-8 failed")
    count = content.count(old_text)
    if count == 0:
        raise ValueError(f"old_text not found in {target}. Read the file first and copy the exact text.")
    if count > 1:
        raise ValueError(f"old_text appears {count} times in {target}. Add surrounding context to make it unique.")
    updated = content.replace(old_text,new_text,1)
    with open(target,"w",encoding="utf-8") as f:
        f.write(updated)
    observed.remember(target, lines=len(updated.splitlines()))
    return f"[edit_file] '{old_text}' replaced with '{new_text}' in {target}"

def _elide_middle(text:str,limit:int=BASH_OUTPUT_LIMIT)->str:
    """超长输出掐中间、留头尾。

    留尾是关键：退出码、报错、堆栈都在末尾，只留头会丢掉最该看的信息。
    """
    if len(text) <= limit:
        return text
    omitted = len(text) - BASH_OUTPUT_HEAD - BASH_OUTPUT_TAIL
    return (
        text[:BASH_OUTPUT_HEAD]
        + f"\n...[{omitted} chars omitted]...\n"
        + text[-BASH_OUTPUT_TAIL:]
    )


def _group_alive(pgid: int) -> bool:
    """这个进程组里还有活着的进程吗？（信号 0 = 只探测，不真发信号。）"""
    try:
        os.killpg(pgid, 0)
        return True
    except OSError:
        return False


def _kill_group(proc: "subprocess.Popen[str]", grace: float = 3.0) -> None:
    """先 SIGTERM **整个进程组**，宽限之后还活着就 SIGKILL。

    为什么必须是进程组而不是 proc.kill()：proc 只是 /bin/bash，命令里启动的东西
    （`cmd &`、管道里的下一段、sleep …）都是它的孩子，**杀 bash 不会连带动它们** ——
    那些进程变成孤儿继续活着。实测过：`timeout` 一到，`sleep 30 &` 留下的进程还在。
    start_new_session=True 让子进程自成一组，killpg 才能一次收干净。

    阶梯（TERM → 宽限 → KILL）是给它一个自己收尾的机会：一个正在写文件的命令收到
    SIGTERM 会去清理，直接 KILL 就可能留下半个文件。

    判断"还在不在"看的是**组**而不是 proc.wait()：bash 死了但某个忽略 SIGTERM 的
    后代还活着时，后者才是要继续升级的理由。

    ⚠️ 等的时候必须 `proc.poll()` 收尸。**僵尸进程在进程组里仍然算"存在"** ——
    不收的话 killpg(pgid, 0) 一直说"还活着"，整个阶梯会傻等满两轮宽限（实测 6 秒，
    而真正该花的是 0.1 秒）。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return                      # 组里已经没进程了
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            proc.poll()                 # ← 收僵尸，否则下面永远是 True
            if not _group_alive(pgid):
                return
            time.sleep(0.05)


def run_bash(command:str,timeout:int=BASH_TIMEOUT_DEFAULT,*,root=None,should_stop=None,
             sandbox_mode: str | None = DEFAULT_SANDBOX_MODE, audit_root=None,
             audit_session_id: str | None = None, approved_request_id: str | None = None)->str:
    """跑一条命令。返回文本，**从不抛异常**（取消和超时都走正常返回）。

    timeout: 秒。缺省用 BASH_TIMEOUT_DEFAULT，超过 BASH_TIMEOUT_MAX 会被夹住
        （两个变量都在文件顶部），由 _clamp_bash_timeout 收口 —— 模型传 0、负数、
        垃圾都回默认值，不报错。**上限是硬性的**：模型给多大都越不过它。

    should_stop: 宿主注入的取消轮询函数（同 root，模型给不了）。给了就每 0.2 秒
        看一眼 —— 用户点了停止时**正在跑的命令**也能被收掉，不必等它自己结束。
        没有它的话，"停止"最多要等一条 `sleep 200` 跑完，界面上看起来就是没反应。

    超时和取消都杀**整个进程组**（见 _kill_group），所以命令启动的后台进程不会
    留下来变成孤儿。
    """
    timeout = _clamp_bash_timeout(timeout)
    mode = normalize_mode(sandbox_mode)
    cwd = root_dir(root)
    audit_event = {
        "sessionId": audit_session_id,
        "requestId": approved_request_id,
        "sandboxMode": mode,
        "cwd": str(cwd),
        "command": command,
        "timeout": timeout,
    }
    if mode != "full-access":
        request_id = f"bashreq-{uuid.uuid4().hex[:12]}"
        payload = {
            "id": request_id,
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "note": (
                "Not executed yet — this command is waiting for the user to approve it. "
                "The turn is paused here. Do not retry it and do not claim it ran."
            ),
        }
        message = "run_bash requires one-time approval in workspace-write mode"
        bash_audit.append(audit_root, {
            **audit_event,
            "requestId": request_id,
            "status": "requested",
            "reason": message,
        })
        return f"{BASH_REQUEST_PREFIX}{json.dumps(payload, ensure_ascii=False)}"
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            executable="/bin/bash",
            # stderr 并到 stdout：模型只看一份输出，和以前 capture_output 的合并一致
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            start_new_session=True,      # 自成进程组，取消/超时才能连带后代一起收
        )
    except OSError as exc:
        # 工作目录没了（比如工作区目录被人在磁盘上删了）——说清是目录的问题，
        # 别说成命令的问题，否则模型会去改命令然后一直失败。
        bash_audit.append(audit_root, {**audit_event, "spawnError": str(exc)})
        return f"$ {command}\n[cannot run in {cwd}: {exc}]"

    deadline = time.monotonic() + max(timeout, 1)
    stopped: str | None = None
    text = ""
    while True:
        # 取消优先于超时：用户点了停止，就不该再等这条命令跑完。
        # 0.2 秒一轮 —— 比一次模型调用短得多，用户感知不到这个延迟。
        if should_stop is not None and (reason := should_stop()):
            stopped = f"cancelled: {reason}"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stopped = f"timed out after {timeout}s"
            break
        try:
            # TimeoutExpired 之后可以继续 communicate，已读到的输出不会丢
            text = proc.communicate(timeout=min(0.2, remaining))[0] or ""
            break
        except subprocess.TimeoutExpired:
            continue

    if stopped is not None:
        _kill_group(proc)
        try:
            text = (text or "") + (proc.communicate(timeout=5)[0] or "")
        except subprocess.TimeoutExpired:
            text = text or ""

    text = text or ""
    hint = ""
    if len(text) > BASH_OUTPUT_LIMIT:
        preview = _elide_middle(text)
        # 只截断会让被掐掉的那段永远拿不回来（重跑还是被截，用 sed 又不知道该看哪几行），
        # 所以全文另存一份，内联换成"预览 + 定位符"。取回靠 run_bash 自己 grep/sed
        # 那个路径 —— 不必新工具，read_file 也不再分页。
        path = save_spill(text, source="run_bash")
        if path is not None:
            hint = (
                f"\n[full output: {len(text)} chars saved to {path} — the middle above was "
                f"elided. Read it with read_file (page it with offset/limit), or grep/sed "
                f"it with run_bash.]"
            )
        text = preview

    # 状态行：正常退出报退出码；被取消/超时则说明原因（那时退出码是信号值，
    # 报 -15 之类的数字对模型没有意义）。
    status = f"[{stopped}]" if stopped is not None else f"[exit code: {proc.returncode}]"
    bash_audit.append(audit_root, {
        **audit_event,
        "stopped": stopped,
        "exitCode": proc.returncode,
        "outputChars": len(text),
    })
    return f"$ {command}\n{text}{status}{hint}"

def write_file(path:str,content:str,*,root=None,observed:ObservationContext,
               sandbox_mode: str | None = DEFAULT_SANDBOX_MODE)->str:
    p = resolve_path(path, root, sandbox_mode=sandbox_mode)
    reason = observed.guard(p)
    if reason:
        raise ValueError(f"cannot write {p}: {reason}")
    p.parent.mkdir(parents = True,exist_ok=True)
    try:
        with open(p,"w",encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        raise ValueError(f"write {p} failed: {exc}")
    observed.remember(p, lines=len(content.splitlines()))
    return f"[write_file] {p} written"


def plan(todos:list)->str:
    """记录当前任务清单。第一次调用就是创建，之后调用就是替换当前计划。"""
    if not isinstance(todos, list):
        raise ValueError("todos must be a list")
    allowed = {"pending", "in_progress", "completed"}
    normalized = []
    for index, item in enumerate(todos, 1):
        if not isinstance(item, dict):
            raise ValueError(f"todos[{index}] must be an object")
        content = str(item.get("content") or "").strip()
        status = str(item.get("status") or "").strip()
        if not content:
            raise ValueError(f"todos[{index}].content is required")
        if status not in allowed:
            raise ValueError(f"todos[{index}].status must be one of {sorted(allowed)}")
        normalized.append({"content": content, "status": status})
    payload = {"todos": normalized}
    return f"{PLAN_UPDATE_PREFIX}{json.dumps(payload, ensure_ascii=False)}"

# ---------------------------------------------------------------------------
# 分发入口：模型说"调 read_file" → 找到函数 → 解析参数 → 执行
# ---------------------------------------------------------------------------

# 工具名 → 执行函数 的映射表。以后加新工具：写函数 + schema，再往这里加一行。
_TOOL_FUNCS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "run_bash": run_bash,
    "plan": plan,
}

# 这些数值参数，小模型经常传成字符串（"10" 而不是 10），分发时统一转 int
_NUMERIC_FIELDS = {"offset", "limit", "timeout"}

# 由**调用方**注入、不许模型自己给的参数。
#
# 分发时是按函数签名过滤模型给的 JSON 的（挡掉 schema 外的多余字段）。这三个都在
# 签名里，不排除掉的话模型塞一个 "root": "/" 就能把它自己的基准目录改掉，塞一个
# "should_stop" 就能把取消检查换掉 —— 基准目录和取消权必须由宿主决定，不能交给模型。
# observed 同理：模型要是能自己挑 context，就能挑一个"已经读过"的上下文把守卫绕过去。
_INJECTED_ARGS = {"root", "should_stop", "observed", "sandbox_mode", "audit_root", "audit_session_id", "approved_request_id"}

# 工具失败的统一前缀。这是 tools.py 与 loop.py 之间的**契约**：
# execute_tool 从不抛异常，所以 loop.py 判断不了成功与否，只能认这个前缀。
# 抽成常量是为了别让两处各写一遍字符串 —— 格式一改，ok 字段会静默失效。
TOOL_ERROR_PREFIX = "[tool error] "
BASH_REQUEST_PREFIX = "[bash request] "
PLAN_UPDATE_PREFIX = "[plan update] "


def execute_tool(name: str, arguments_raw: str, *, root: "str | Path | None" = None,
                 should_stop=None, observed: ObservationContext,
                 sandbox_mode: str | None = DEFAULT_SANDBOX_MODE,
                 audit_root: "str | Path | None" = None,
                 audit_session_id: str | None = None,
                 approved_request_id: str | None = None) -> str:
    """执行一次工具调用，任何情况都返回文本，绝不抛异常。

    name: 工具名（模型给的 function.name）。
    arguments_raw: 模型给的参数，JSON 字符串（可能不合法，小模型常犯）。
    root: 基准目录 —— 相对路径按它解析、run_bash 在它里面执行。None 才是进程当前
          目录；服务端必须传工作区的根（见 root_dir 那段注释）。
    should_stop: 取消轮询函数，宿主注入（同 root）。只有 run_bash 用它 —— 让一条
          正在跑的命令也能被取消，而不是等它自己结束。
    observed: **必填**。哪个执行上下文的观测状态（见 observed.ObservationContext）。
          没有"默认context"可退回：拿不到就不该放行改文件。

    成功 → 执行函数自己的输出文本；
    失败 → TOOL_ERROR_PREFIX 开头的错误文本。错误会回喂给模型，让它能自救/重试。

    契约：**从不抛异常**，所以调用方（loop.py）只能靠这个前缀判断成败。
    改前缀就等于改契约，TOOL_ERROR_PREFIX 是唯一的定义处。
    """
    func = _TOOL_FUNCS.get(name)
    if func is None:
        return (
            f"{TOOL_ERROR_PREFIX}unknown tool: {name}. "
            f"Available tools: {', '.join(_TOOL_FUNCS)}"
        )

    # 1. 解析 JSON 参数
    try:
        args = json.loads(arguments_raw)
    except json.JSONDecodeError:
        return f"{TOOL_ERROR_PREFIX}arguments not valid JSON: {arguments_raw[:200]}"
    if not isinstance(args, dict):
        return f"{TOOL_ERROR_PREFIX}arguments must be a JSON object, got {type(args).__name__}"

    # 2. 只保留函数签名里有的参数。
    #    模型常塞 schema 外的多余字段，直接 func(**args) 会 TypeError。
    #    签名里但属于"宿主注入"的（root / should_stop）也要排掉，见 _INJECTED_ARGS。
    signature = inspect.signature(func)
    valid_names = set(signature.parameters) - _INJECTED_ARGS
    args = {key: value for key, value in args.items() if key in valid_names}

    # 3. 数值字段若被传成字符串，转成 int（读文件/超时这类参数）
    for key in _NUMERIC_FIELDS:
        value = args.get(key)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            args[key] = int(value.strip())

    # 4. 执行。异常按类型给不同错误文本，模型能看到具体原因
    #
    # 宿主注入的参数按**签名**给：只有 run_bash 声明了 should_stop（长命令值得被取消），
    # 另外三个是快操作，给它们塞这个参数会直接 TypeError。以后哪个工具需要取消能力，
    # 在它自己的签名里加上就行，这里不用改。
    injected: dict = {}
    if "root" in signature.parameters:
        injected["root"] = root
    if "should_stop" in signature.parameters:
        injected["should_stop"] = should_stop
    if "observed" in signature.parameters:
        injected["observed"] = observed
    if "sandbox_mode" in signature.parameters:
        injected["sandbox_mode"] = sandbox_mode
    if "audit_root" in signature.parameters:
        injected["audit_root"] = audit_root
    if "audit_session_id" in signature.parameters:
        injected["audit_session_id"] = audit_session_id
    if "approved_request_id" in signature.parameters:
        injected["approved_request_id"] = approved_request_id
    try:
        return func(**args, **injected)
    except TypeError as exc:
        return f"{TOOL_ERROR_PREFIX}bad arguments for {name}: {exc}"
    except ValueError as exc:
        return f"{TOOL_ERROR_PREFIX}{exc}"
    except Exception as exc:
        return f"{TOOL_ERROR_PREFIX}{name} crashed: {exc}"


def make_executor(root: "str | Path | None", observed: ObservationContext, *,
                  should_stop=None, sandbox_mode: str | None = DEFAULT_SANDBOX_MODE,
                  audit_root: "str | Path | None" = None,
                  audit_session_id: str | None = None):
    """把宿主的东西绑进一个 (name, arguments_raw) -> str 的回调，交给 run_agent_turn。

    loop 只管"调模型、跑工具、回喂"，不该知道文件系统的基准在哪、观测状态属于哪个
    上下文、也不该知道怎么取消；这些都由调用方在这里绑好：服务端绑工作区的根 +
    这一场的 observed context + 这一轮的取消令牌，评估绑临时目录 + 一次 case 的 context。

    should_stop 一路传到 run_bash —— 没有它，用户点停止之后一条正在跑的 `sleep 200`
    只能等它自己结束（界面上看起来就是"没反应"）。

    observed **必填**：不传就等于"没有上下文"，那正好是以前跨会话串味的老路。
    没有会话身份的调用方用 observed.REGISTRY.new_context() 开一个一次性的，
    不要退回某张共享表。
    """
    def _executor(name: str, arguments_raw: str) -> str:
        return execute_tool(
            name,
            arguments_raw,
            root=root,
            should_stop=should_stop,
            observed=observed,
            sandbox_mode=sandbox_mode,
            audit_root=audit_root,
            audit_session_id=audit_session_id,
        )
    return _executor

TOOL_SCHEMAS = [
    READ_FILE_SCHEMA,
    WRITE_FILE_SCHEMA,
    EDIT_FILE_SCHEMA,
    RUN_BASH_SCHEMA,
    PLAN_SCHEMA,
]
