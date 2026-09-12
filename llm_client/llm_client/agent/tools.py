import inspect
import json
import subprocess
from pathlib import Path

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
            "offset to continue from."
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
                    "description": "1-based line number to start reading from (default 1).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to read (default 500).",
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
            "errors tell you if it is missing or ambiguous."
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
            "Output is truncated to the last 20000 chars; a command killed by "
            "timeout or a non-zero exit code is reported in the output."
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
                    "description": "Timeout in seconds (default 60).",
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
            "small targeted change in an existing file, use edit_file instead."
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

def read_file(path:str,offset=1,limit=500)->str:
    if offset < 1:
        offset = 1
    if limit <= 0:
        limit = 500
    try:
        with open(path,"r",encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        raise ValueError(f"read {path} failed, error:{exc}")
    lines = content.splitlines()
    total = len(lines)

    if total == 0:
        # 空文件：没有"第几行到第几行"可言，单独表述
        return f"[read_file] {path}: empty file (0 lines)"

    if offset > total:
        # offset 超出文件末尾：切片会得到空段，行号区间会变成"第 N-(N-1) 行"的胡话
        raise ValueError(f"offset={offset} is beyond end of file ({total} lines total): {path}")

    start = offset - 1
    chunk = lines[start:start+limit]
    end = start + len(chunk)
    body = "\n".join(chunk)
    result = f"[read_file] {path} ({total} lines total, showing lines {start + 1}-{end})\n{body}"
    if end < total:
        result += f"\n...[{total - end} more lines in file. Use offset={end + 1} to continue.]"
    return result

def edit_file(path:str,old_text:str,new_text:str)->str:
    try:
        with open(path,"r",encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise ValueError(f"File {path} Not Found")
    except UnicodeDecodeError:
        raise ValueError(f"File {path} decoding with utf-8 failed")
    count = content.count(old_text)
    if count == 0:
        raise ValueError(f"old_text not found in {path}. Read the file first and copy the exact text.")
    if count > 1:
        raise ValueError(f"old_text appears {count} times in {path}. Add surrounding context to make it unique.")
    updated = content.replace(old_text,new_text,1)
    with open(path,"w",encoding="utf-8") as f:
        f.write(updated)
    return f"[edit_file] '{old_text}' replaced with '{new_text}' in {path}"

def run_bash(command:str,timeout:int=60)->str:
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
        )
    except subprocess.TimeoutExpired:
        return f"$ {command}\n[timed out after {timeout}s]"
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        output += result.stderr
    output = f"$ {command}\n{output}"
    output += f"[exit code: {result.returncode}]"
    return output

def write_file(path:str,content:str)->str:
    p = Path(path)
    p.parent.mkdir(parents = True,exist_ok=True)
    try:
        with open(p,"w",encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        raise ValueError(f"write {p} failed: {exc}")
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


def execute_tool(name: str, arguments_raw: str) -> str:
    """执行一次工具调用，任何情况都返回文本，绝不抛异常。

    name: 工具名（模型给的 function.name）。
    arguments_raw: 模型给的参数，JSON 字符串（可能不合法，小模型常犯）。

    成功 → 执行函数自己的输出文本；
    失败 → "[tool error] ..." 错误文本。错误会回喂给模型，让它能自救/重试。
    """
    func = _TOOL_FUNCS.get(name)
    if func is None:
        return (
            f"[tool error] unknown tool: {name}. "
            f"Available tools: {', '.join(_TOOL_FUNCS)}"
        )

    # 1. 解析 JSON 参数
    try:
        args = json.loads(arguments_raw)
    except json.JSONDecodeError:
        return f"[tool error] arguments not valid JSON: {arguments_raw[:200]}"
    if not isinstance(args, dict):
        return f"[tool error] arguments must be a JSON object, got {type(args).__name__}"

    # 2. 只保留函数签名里有的参数。
    #    模型常塞 schema 外的多余字段，直接 func(**args) 会 TypeError。
    valid_names = set(inspect.signature(func).parameters)
    args = {key: value for key, value in args.items() if key in valid_names}

    # 3. 数值字段若被传成字符串，转成 int（读文件/超时这类参数）
    for key in _NUMERIC_FIELDS:
        value = args.get(key)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            args[key] = int(value.strip())

    # 4. 执行。异常按类型给不同错误文本，模型能看到具体原因
    try:
        return func(**args)
    except TypeError as exc:
        return f"[tool error] bad arguments for {name}: {exc}"
    except ValueError as exc:
        return f"[tool error] {exc}"
    except Exception as exc:
        return f"[tool error] {name} crashed: {exc}"

TOOL_SCHEMAS = [
    READ_FILE_SCHEMA,
    WRITE_FILE_SCHEMA,
    EDIT_FILE_SCHEMA,
    RUN_BASH_SCHEMA,
]
