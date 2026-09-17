"""agent 循环的评估 runner（路线图第 2 步）。

一个 case = `task.txt`（给模型的一句话）+ `fixture/`（初始目录）+ `check.sh`（判定）。
runner 只做四件事：拷 fixture 到临时目录 → 跑 agent → 跑 check.sh → 记一行结果。

    python evals/agent/run.py                 # 全部 case 各跑一次
    python evals/agent/run.py -k rename       # 只跑名字含 rename 的
    python evals/agent/run.py --runs 3        # 每 case 三次，报 pass@1 与 pass^3
    python evals/agent/run.py --keep          # 留住临时目录，好进去看

判据是**世界变成什么样**，不是回复像不像：check.sh 在临时目录里跑，退出码 0 才算过。
模型回复放在环境变量 `$EVAL_REPLY` 里，需要时可用。

两条护栏（每个 case 自动带，不用各自实现）：
- `protected.txt` 里列的文件**哈希不许变** —— 挡住"改测试让它过"这种作弊
- 跑前跑后各取一次仓库的 `git status --short`，必须一模一样 —— 没有沙箱，
  这是唯一能抓住"agent 跑到 case 目录之外乱改"的办法

结果落 `results/<时间戳>.jsonl`（append-only），带模型名/温度/日期/git 版本 ——
两次跑能 diff 出"这次改动让哪个 case 变坏了"。会话轨迹另存 `sessions/`，格式与
服务端会话日志相同，卡住时能翻。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
CASES_DIR = EVAL_DIR / "cases"
RESULTS_DIR = EVAL_DIR / "results"
SESSIONS_DIR = EVAL_DIR / "sessions"

sys.path.insert(0, str(REPO_ROOT / "packages" / "chat" / "src"))

from chat import create_client, get_model_temperature  # noqa: E402
from chat.agent import session_store  # noqa: E402
from chat.runtime import (  # noqa: E402
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    ChatMessage,
    TurnRequest,
    resolve_system_prompt,
    elapsed_ms,
    ensure_runtime_env,
    request_real_reply,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_dirty_state() -> str:
    """仓库当前有没有被改动过（护栏用）。取不到 git 就返回空串，不因此失败。"""
    try:
        out = subprocess.run(
            ["git", "status", "--short"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def install_fixture(case_dir: Path, work_dir: Path) -> None:
    shutil.copytree(case_dir / "fixture", work_dir, dirs_exist_ok=True)


def protected_paths(case_dir: Path) -> list[str]:
    listing = case_dir / "protected.txt"
    if not listing.exists():
        return []
    return [line.strip() for line in listing.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_case(
    case_dir: Path,
    *,
    provider: str,
    model_name: str,
    keep: bool,
    run_index: int,
    session_id: str,
) -> dict:
    task = (case_dir / "task.txt").read_text(encoding="utf-8").strip()
    protected = protected_paths(case_dir)

    work_dir = Path(tempfile.mkdtemp(prefix=f"eval-{case_dir.name}-"))
    install_fixture(case_dir, work_dir)
    # 受保护文件在跑之前的哈希 —— 跑完比一遍
    before_hashes = {name: sha256(work_dir / name) for name in protected if (work_dir / name).exists()}

    dirty_before = repo_dirty_state()
    started = time.monotonic()
    error = ""
    steps: list[dict] = []
    reply = ""
    # 落盘器在这里建、不在 try 里：TurnWriter 只是拼一个路径，不会失败；放外面才能
    # 保证 except 分支里它一定已绑定，否则异常会变成 NameError 盖掉真正的原因。
    writer = session_store.TurnWriter(SESSIONS_DIR, session_id)
    try:
        # 走的是**服务端同一条路径**（同一个 TurnRequest → normalize → 循环），
        # 所以系统提示词怎么拼、工具怎么给，跟真实使用完全一致。只在根目录上不同：
        # 这里是这个 case 的临时目录。
        request = TurnRequest(
            messages=[ChatMessage(role="user", content=task)],
            provider=provider,
            model_name=model_name,
            tools_enabled=True,
        )
        # 轨迹另存一份（跟服务端会话日志同格式），卡住时能翻。
        # 父目录由 TurnWriter 落盘时建，不用在这里 mkdir。
        writer.begin(
            {
                "provider": provider,
                "model": model_name,
                "case": case_dir.name,
                "run": run_index,
                # 提示词在**跑之前**解析并落盘：对比两次结果时，"这次用的哪份提示词"
                # 是必要上下文，不是装饰。
                "systemPrompt": resolve_system_prompt(None, tools_enabled=True),
            },
            [{"role": "user", "content": task}],
        )
        result = request_real_reply(
            request, [], str(work_dir), writer=writer,
            observed_context_id=f"eval:{session_id}")
        writer.finish(
            durationMs=elapsed_ms(started),
            temperature=result.temperature,
        )
        reply, steps = result.reply, result.steps
    except Exception as exc:  # 模型/网络挂了也要留一条记录，别让整轮评估中断
        error = f"{type(exc).__name__}: {exc}"
        writer.finish(durationMs=elapsed_ms(started), error=error)
    duration_ms = elapsed_ms(started)

    # 判定：check.sh 在临时目录里跑，退出码 0 = 通过
    check_output = ""
    check_ok = False
    if not error:
        env = {**os.environ, "EVAL_REPLY": reply}
        try:
            # 注意：脚本在 case 目录里，但**工作目录必须是临时目录** ——
            # 判定要检查的正是 agent 改过的那份东西（python3 check_config.py、grep -rn . …）
            completed = subprocess.run(
                ["bash", str(case_dir / "check.sh")], cwd=work_dir,
                capture_output=True, text=True, timeout=300, env=env,
            )
            check_output = (completed.stdout + completed.stderr).strip()
            check_ok = completed.returncode == 0
        except subprocess.SubprocessError as exc:
            check_output = f"check.sh 没能跑起来: {exc}"

    # 作弊检测：受保护文件动过就一律不算过
    tampered = [
        name for name, digest in before_hashes.items()
        if not (work_dir / name).exists() or sha256(work_dir / name) != digest
    ]
    if tampered:
        check_ok = False

    # 护栏：case 目录之外不许有改动
    dirty_after = repo_dirty_state()
    leaked = dirty_after != dirty_before

    record = {
        "case": case_dir.name,
        "run": run_index,
        "ok": bool(check_ok and not error and not leaked and not tampered),
        # error = 模型/网关层面没跑起来（不计入通过率）；ok=False 且无 error = agent 真没做对。
        # 分开的理由：供应商抽风会把通过率打下去，那不是 agent 的能力问题，混在一起
        # 数字就没法看了（gpt 那个网关就是会偶发 400）。
        "error": error,
        "tampered": tampered,
        "repo_leaked": leaked,
        "rounds": len(steps),
        "tools": [step.get("tool") for step in steps],
        "durationMs": duration_ms,
        "provider": provider,
        "model": model_name,
        # 取不到就记 None：供应商名写错时 create_client 已经报过错，这里再抛一次
        # 会把"这一条记录"整个弄丢（跑完的东西白跑）
        "temperature": _safe_temperature(provider, model_name),
        "checkOutput": check_output[:600],
        "reply": reply[:600],
        "session": f"{SESSIONS_DIR / (session_id + '.jsonl')}",
        "workDir": str(work_dir) if keep else "",
    }

    if not keep:
        shutil.rmtree(work_dir, ignore_errors=True)
    return record


def _safe_temperature(provider: str, model_name: str):
    try:
        return get_model_temperature(provider=provider, model_name=model_name)
    except Exception:
        return None


def print_record(record: dict) -> None:
    marks = []
    if record["rounds"]:
        marks.append(f"{record['rounds']} 步")
    marks.append(f"{record['durationMs'] / 1000:.1f}s")
    if record["tampered"]:
        marks.append(f"改动了受保护文件 {record['tampered']}")
    if record["repo_leaked"]:
        marks.append("⚠️ case 目录之外有改动")
    if record["error"]:
        marks.append(record["error"][:80])
    print(f"  {'✅' if record['ok'] else '❌'} [{record['case']}] {'，'.join(marks)}")
    print(f"      工具: {record['tools'] or '(一个都没调)'}")
    if not record["ok"] and record["checkOutput"]:
        for line in record["checkOutput"].splitlines()[:4]:
            print(f"      判定: {line[:110]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python evals/agent/run.py", description="跑 agent 循环评估")
    parser.add_argument("-k", "--filter", help="只跑名字里含这个串的 case")
    parser.add_argument("--runs", type=int, default=1, help="每个 case 跑几次（模型不确定，>1 才有意义）")
    parser.add_argument("-p", "--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--keep", action="store_true", help="留住临时目录")
    args = parser.parse_args(argv)

    cases = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    if args.filter:
        cases = [c for c in cases if args.filter in c.name]
    if not cases:
        print("没有匹配的 case")
        return 2

    ensure_runtime_env(args.provider)
    ensure_runtime_env("gpt")  # 备用供应商的 key 也读进来，换模型时不必重跑

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H-%M-%S")
    try:
        git_rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                                 capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git_rev = ""

    print(f"模型 {args.provider}/{args.model}｜{len(cases)} 个 case × {args.runs} 次｜git {git_rev}\n")

    records: list[dict] = []
    for case_dir in cases:
        for index in range(1, args.runs + 1):
            session_id = f"eval-{case_dir.name}-{stamp}-{index}"
            record = run_case(
                case_dir, provider=args.provider, model_name=args.model,
                keep=args.keep, run_index=index, session_id=session_id,
            )
            records.append(record)
            print_record(record)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / f"{stamp}.jsonl"
    header = {
        "type": "run",
        "time": stamp,
        "git": git_rev,
        "provider": args.provider,
        "model": args.model,
        "runs": args.runs,
        "cases": [c.name for c in cases],
    }
    with results_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, ensure_ascii=False) + "\n")
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print("\n=== 汇总 ===")
    errored = [r for r in records if r["error"]]
    measured = [r for r in records if not r["error"]]
    passed = sum(1 for r in measured if r["ok"])
    print(f"  通过 {passed}/{len(measured)}" + (
        f"（另有 {len(errored)} 次报错，不计入通过率 —— 那是模型/网关层面的问题）"
        if errored else ""))
    for case_dir in cases:
        mine = [r for r in records if r["case"] == case_dir.name]
        wins = sum(1 for r in mine if r["ok"])
        errs = sum(1 for r in mine if r["error"])
        rounds = [r["rounds"] for r in mine]
        suffix = f"  报错 {errs}" if errs else ""
        print(f"   {case_dir.name:22s} {wins}/{len(mine) - errs}  步数 {rounds}{suffix}")
    if errored:
        print("  报错明细:")
        for r in errored:
            print(f"   {r['case']}: {r['error'][:110]}")
    print(f"  结果: {results_path}")
    print(f"  轨迹: {SESSIONS_DIR}/（与服务端会话日志同格式）")
    return 0 if passed == len(measured) == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
