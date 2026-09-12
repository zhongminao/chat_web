import inspect
import json
import subprocess
from pathlib import Path

from chat_agent.agent.observed import guard as guard_mutation
from chat_agent.agent.observed import remember as remember_observed
from chat_agent.agent.spill import save as save_spill

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


def resolve_path(path: "str | Path", root: "str | Path | None") -> Path:
    """相对路径按 root 解析，绝对路径原样。返回规范化的绝对路径。

    规范化是必须的，不只是好看：observed 守卫按路径记"读过没读过"，`a.txt` 和
    `/root/a.txt` 必须是同一个 key —— 否则模型换个写法就能绕过"先读后改"。
    """
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = root_dir(root) / target
    return target.resolve()

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

RUN_BASH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": (
            "Run a bash command and return combined stdout/stderr. "
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
                    "description": "Timeout in seconds.",
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


def read_file(path:str,offset=1,limit=500,*,root=None)->str:
    target = resolve_path(path, root)
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
        return f"[read_file] {target}: empty file (0 lines)"

    if offset > total:
        raise ValueError(f"offset={offset} is beyond end of file ({total} lines total): {target}")

    start = offset - 1
    chunk = lines[start:start+limit]
    end = start + len(chunk)
    body = "\n".join(_elide_line(line) for line in chunk)
    remember_observed(target, lines=total)
    result = f"[read_file] {target} ({total} lines total, showing lines {start + 1}-{end})\n{body}"
    if end < total:
        result += f"\n...[{total - end} more lines in file. Use offset={end + 1} to continue.]"
    return result

def edit_file(path:str,old_text:str,new_text:str,*,root=None)->str:
    target = resolve_path(path, root)
    reason = guard_mutation(target)
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
    remember_observed(target, lines=len(updated.splitlines()))
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


def run_bash(command:str,timeout:int=60,*,root=None)->str:
    cwd = root_dir(root)
    try:
        result = subprocess.run(
            command,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        return f"$ {command}\n[timed out after {timeout}s]"
    except OSError as exc:
        # 工作目录没了（比如工作区目录被人在磁盘上删了）——说清是目录的问题，
        # 别说成命令的问题，否则模型会去改命令然后一直失败。
        return f"$ {command}\n[cannot run in {cwd}: {exc}]"

    text = (result.stdout or "") + (result.stderr or "")
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
    return f"$ {command}\n{text}[exit code: {result.returncode}]{hint}"

def write_file(path:str,content:str,*,root=None)->str:
    p = resolve_path(path, root)
    reason = guard_mutation(p)
    if reason:
        raise ValueError(f"cannot write {p}: {reason}")
    p.parent.mkdir(parents = True,exist_ok=True)
    try:
        with open(p,"w",encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        raise ValueError(f"write {p} failed: {exc}")
    remember_observed(p, lines=len(content.splitlines()))
    return f"[write_file] {p} written"

# ---------------------------------------------------------------------------
# 分发入口：模型说"调 read_file" → 找到函数 → 解析参数 → 执行
# ---------------------------------------------------------------------------

# 工具名 → 执行函数 的映射表。以后加新工具：写函数 + schema，再往这里加一行。
_TOOL_FUNCS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "run_bash": run_bash,
}

# 这些数值参数，小模型经常传成字符串（"10" 而不是 10），分发时统一转 int
_NUMERIC_FIELDS = {"offset", "limit", "timeout"}

# 由**调用方**注入、不许模型自己给的参数。
#
# 分发时是按函数签名过滤模型给的 JSON 的（挡掉 schema 外的多余字段）。root 也在签名里，
# 不排除掉的话模型塞一个 "root": "/" 就能把它自己的基准目录改掉 —— 基准目录必须
# 由宿主决定，这条不能交给模型。以后再有这类参数，加进这个集合。
_INJECTED_ARGS = {"root"}

# 工具失败的统一前缀。这是 tools.py 与 loop.py 之间的**契约**：
# execute_tool 从不抛异常，所以 loop.py 判断不了成功与否，只能认这个前缀。
# 抽成常量是为了别让两处各写一遍字符串 —— 格式一改，ok 字段会静默失效。
TOOL_ERROR_PREFIX = "[tool error] "


def execute_tool(name: str, arguments_raw: str, *, root: "str | Path | None" = None) -> str:
    """执行一次工具调用，任何情况都返回文本，绝不抛异常。

    name: 工具名（模型给的 function.name）。
    arguments_raw: 模型给的参数，JSON 字符串（可能不合法，小模型常犯）。
    root: 基准目录 —— 相对路径按它解析、run_bash 在它里面执行。None 才是进程当前
          目录；服务端必须传工作区的根（见 root_dir 那段注释）。

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
    #    签名里但属于"宿主注入"的（root）也要排掉，见 _INJECTED_ARGS。
    valid_names = set(inspect.signature(func).parameters) - _INJECTED_ARGS
    args = {key: value for key, value in args.items() if key in valid_names}

    # 3. 数值字段若被传成字符串，转成 int（读文件/超时这类参数）
    for key in _NUMERIC_FIELDS:
        value = args.get(key)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            args[key] = int(value.strip())

    # 4. 执行。异常按类型给不同错误文本，模型能看到具体原因
    try:
        return func(**args, root=root)
    except TypeError as exc:
        return f"{TOOL_ERROR_PREFIX}bad arguments for {name}: {exc}"
    except ValueError as exc:
        return f"{TOOL_ERROR_PREFIX}{exc}"
    except Exception as exc:
        return f"{TOOL_ERROR_PREFIX}{name} crashed: {exc}"


def make_executor(root: "str | Path | None"):
    """把 root 绑进一个 (name, arguments_raw) -> str 的回调，交给 run_agent_turn。

    loop 只管"调模型、跑工具、回喂"，不该知道文件系统的基准在哪；基准由调用方在这里绑好。
    服务端绑工作区的根，评估绑临时目录。
    """
    def _executor(name: str, arguments_raw: str) -> str:
        return execute_tool(name, arguments_raw, root=root)
    return _executor

TOOL_SCHEMAS = [
    READ_FILE_SCHEMA,
    WRITE_FILE_SCHEMA,
    EDIT_FILE_SCHEMA,
    RUN_BASH_SCHEMA,
]
