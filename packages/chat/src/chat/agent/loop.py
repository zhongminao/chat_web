"""agent 循环：把"模型想用工具"变成多轮对话直到模型直接说话。

职责边界（刻意保持窄）：
- 只做循环控制：调模型 → 有 tool_calls 就逐个执行并回喂 → 直到纯文本回复；
- 不关心工具是什么：工具由 execute_tool 回调提供；
- 不负责提示词：system 消息由调用方（chat）拼好放进 messages；
- 无状态：history 是本次调用内部的局部列表，请求结束即丢；
  但会把本次新产生的协议消息放进 protocol_messages 返回，调用方可存下来跨请求回放，
  让模型下一轮仍能看到完整工具过程（assistant tool_calls + tool 结果对）。

**没有轮数上限**（2026-09-16 删掉了 MAX_ROUNDS_DEFAULT = 30 和那个 max_rounds 参数）。
循环只在"模型不再要求调工具"时结束，否则一直继续。理由与代价：

- 循环本身跟"策略"无关，它只负责"调模型、跑工具、重复"。**什么时候该停是调用方的
  判断** —— 那个 30 同时当天花板和默认值用，两者被绑在一起之后，没人能按任务调它。
- 代价是**进程内不再有任何东西能让一个跑飞的循环停下来**。删上限的同时也删掉了原来
  那条"达到上限后不带工具再问一次、逼它给最终答复"的兜底：它只在轮数耗尽时才可达，
  没有上限之后那段代码永远跑不到，留着就是死代码。
- 所以"谁来兜底"这件事现在是**调用方的责任**。DSH 那边是同一个立场：核心循环刻意
  没有内建轮次预算（Known Limitations 明写 "No built-in turn budget"），限制交给
  生命周期扩展点（取消、guard 插件、人）。将来要加预算，加在调用方或每步回调上，
  别再把它塞回循环内部。

**落盘**：writer 必填。每产生一条协议消息就落盘一次；工具结果是**每个执行完立刻交**，
不是攒一批、更不是跑完统一交。这样一轮中途崩溃/被杀/被取消，已经发生的事都在盘上。

execute_tool 契约（由 chat/agent/tools.py 提供）：
    execute_tool(name: str, arguments_raw: str) -> str
    arguments_raw 是模型返回的 JSON 字符串；结果/错误都以文本返回，不抛异常。
    因为是"以文本返回"，成败只能靠 tools.TOOL_ERROR_PREFIX 前缀区分 ——
    steps 里的 ok 就是据此算的，不是"没有异常"。
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from chat.agent.cancel import TurnCancelled


def _interrupted_reason() -> str:
    """把正在往外抛的那个异常压成一句话，写进合成结果给人和模型看。

    只在 try/finally 的 finally 里调用 —— 那里 sys.exc_info() 拿到的正是正在传播的
    异常。拿不到（正常跑完）时返回"没有执行"。
    """
    exc = sys.exc_info()[1]
    if exc is None:
        return "没有执行"
    if isinstance(exc, KeyboardInterrupt):
        return "被 Ctrl-C 中断"
    if isinstance(exc, TurnCancelled):
        return f"被调用方取消：{exc}"
    return f"{type(exc).__name__}: {exc}"


def run_agent_turn(
    client: Any,
    messages: list[dict[str, Any]],
    *,
    writer: Any,
    tool_schemas: list[dict[str, Any]] | None = None,
    execute_tool: Callable[[str, str], str] | None = None,
    should_stop: Callable[[], str | None] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """跑完一轮 agent 任务。

    client: chat 的 Client 实例（提供 request_assistant_message）。
    messages: 完整的对话消息（第一条通常是 system，由调用方拼好）。
    writer: 落盘器（chat.agent.session_store.TurnWriter）。**必填** —— 循环边跑边
        落盘，没有"不落盘"这条路径。begin()/finish() 归调用方，循环只调 record()。
    tool_schemas: 工具说明书列表；None → 用 tools.TOOL_SCHEMAS。
    execute_tool: 执行回调；None → tools.make_executor(None, <一次性 observed
        context>)，即在**进程当前目录**里操作（只在随手调时用；服务端必须传绑好
        工作区根和 observed context 的 executor）。
    should_stop: 步边界轮询的回调；返回非空字符串就是"该停了"，循环抛 TurnCancelled。
        **只在这些边界上检查**：每次模型调用之前、每条工具调用之前 —— 正在等模型
        回复、或正在跑一个命令的那一下拦不住（同步阻塞，没有可从外部中断的句柄）。
        None = 不主动停（默认）。

    不设轮数上限：只有模型不再要求调工具时才会返回（见模块 docstring）。
    调用方要限速/限预算，自己在这个循环外面或每步回调上做。

    返回 (最终文本, steps, protocol_messages)：
    - steps: 工具调用流水账，每项 {"tool", "arguments", "result", "ok"}。它是协议
      消息的**投影** —— 每条 tool 消息（含被中断时补出来的）在这里都有对应项；
    - protocol_messages: 本次运行**新产生**的协议消息，可直接追加进调用方的历史缓存。
    """
    # 延迟 import：避免循环依赖，也允许 tools.py 还在迭代时 loop.py 先存在
    from chat.agent import tools as _tools

    if tool_schemas is None:
        tool_schemas = _tools.TOOL_SCHEMAS
    if execute_tool is None:
        # 兜底：在进程当前目录里干活。服务端和评估都该显式传一个绑好 root 的
        # executor（tools.make_executor）—— 走这条就等于"没有工作区"。
        #
        # observed 也得给：这里没有会话身份，就开一个**一次性**的 context。
        # 不用某个固定的默认 id —— 那会让所有兜底调用共用一张表，正好是本次要
        # 修掉的那种串味（只是换到了兜底这条路上）。
        from chat.agent import observed as _observed
        execute_tool = _tools.make_executor(
            None, _observed.REGISTRY.new_context("loop-fallback"))

    history = list(messages)      # 局部历史：无状态，请求结束即丢
    steps: list[dict[str, Any]] = []
    protocol_messages: list[dict[str, Any]] = []   # 本次新增的协议消息，供跨请求回放

    # round_no 只有一个用处：个别网关不返回 tool_call id 时，用它拼一个 fallback id。
    round_no = 0

    while True:
        # 步边界①：调模型之前。取消请求只会在这里被看见。
        if should_stop is not None and (reason := should_stop()):
            raise TurnCancelled(reason)

        assistant_message, _ = client.request_assistant_message(
            messages=history,
            tools=tool_schemas,   # 每轮都带工具清单，模型才能继续调用
        )

        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            # 模型直接说话了 → 任务结束。这是**唯一**的出口。
            content = str(assistant_message.get("content") or "")
            final = {"role": "assistant", "content": content}
            protocol_messages.append(final)
            writer.record(final)
            return content, steps, protocol_messages

        # 不变式①：声明要调工具的消息必须原样进历史，
        # 否则服务端无法把后续 tool 结果与它配对。
        history.append(assistant_message)
        protocol_messages.append(assistant_message)
        writer.record(assistant_message)

        # 这一批工具。try/finally 保的是：**不管怎么退出**（正常跑完 / 被取消 /
        # Ctrl-C / 意外异常），每个声明过的 tool_call 都必须有一条配对的 tool 结果
        # 落进日志。
        #
        # 为什么在这里补，而不是留给重放时的 repair_orphans：
        #   repair_orphans 跑在下一次重放，那时它只能看见"少了一条"，说不出原因。
        #   而这里**当场就知道**原因（取消 / Ctrl-C / 具体异常），能把真实的那句话
        #   写进日志。repair_orphans 于是退化成兜底 —— 只管"连补都没来得及"的情况
        #   （kill -9、断电），那才是它该管的范围。
        completed = 0        # 结果已经落盘的调用数
        attempted = 0        # 已经**发起**的调用数（含正在跑、结果还没落盘的那个）
        awaiting_bash_approval = False
        try:
            for tc in tool_calls:
                # 步边界②：每条工具调用之前。一批里剩下的不该全跑完才停。
                # 放在 attempted += 1 **之前** —— 被这一句拦住的那个调用确实没执行，
                # 于是 finally 里给它的合成结果会写成"没有执行"而不是"可能已部分执行"。
                if should_stop is not None and (reason := should_stop()):
                    raise TurnCancelled(reason)
                attempted += 1
                # 用 .get 取值而不是 []：个别网关返回的字段可能缺失
                fn = tc.get("function") or {}
                name = str(fn.get("name") or "")
                arguments_raw = str(fn.get("arguments") or "")

                try:
                    result = execute_tool(name, arguments_raw)
                    # execute_tool 从不抛异常，失败是以 TOOL_ERROR_PREFIX 开头的文本
                    # 返回的，所以这里必须看文本。只写 ok = True 会让 ok 恒真 ——
                    # 曾经如此，结果是按 ok 统计失败率永远得 0。
                    ok = not result.startswith(_tools.TOOL_ERROR_PREFIX)
                except Exception as exc:
                    # 防弹：单个工具失败不能弄死整个循环。
                    # 错误文本回喂给模型，让它能根据原因自救/重试。
                    result = f"{_tools.TOOL_ERROR_PREFIX}{exc}"
                    ok = False

                plan_todos = None
                if ok and result.startswith(_tools.PLAN_UPDATE_PREFIX):
                    try:
                        payload = json.loads(result[len(_tools.PLAN_UPDATE_PREFIX):])
                    except json.JSONDecodeError:
                        payload = {}
                    todos = payload.get("todos") if isinstance(payload, dict) else None
                    plan_todos = todos if isinstance(todos, list) else []
                    result = "[plan] updated"

                steps.append({
                    "tool": name,
                    "arguments": arguments_raw,
                    "result": result,
                    "ok": ok,
                })

                # 不变式②：每个工具结果一条 tool 消息，tool_call_id 与上面配对
                tool_message = {
                    "role": "tool",
                    "tool_call_id": str(tc.get("id") or f"call_r{round_no}"),
                    "content": result,
                }
                history.append(tool_message)
                protocol_messages.append(tool_message)
                writer.record(tool_message)   # 每个工具跑完立刻落盘，不等这一批
                if plan_todos is not None:
                    writer.record_plan(plan_todos)
                completed += 1
                if result.startswith(_tools.BASH_REQUEST_PREFIX):
                    awaiting_bash_approval = True
        finally:
            if completed < len(tool_calls):
                # 补上没留下结果的调用。分两种，措辞不能一样：
                #   - offset < attempted：**已经发起**、正在执行时被打断 —— 副作用
                #     可能已经部分发生（一个跑了一半的 run_bash 可能已经改了文件），
                #     所以必须让模型"先确认现场再决定要不要重试"；
                #   - 其余：还没轮到，真的没执行。
                # 落盘顺序与正常结果一致；内容是 TOOL_ERROR_PREFIX 开头 → ok 算失败，
                # 模型会看到并自己决定下一步。
                reason = _interrupted_reason()
                for offset in range(completed, len(tool_calls)):
                    tc = tool_calls[offset]
                    fn = tc.get("function") or {}
                    if offset < attempted:
                        result = (
                            f"{_tools.TOOL_ERROR_PREFIX}这个调用被中断，可能已经部分执行"
                            f"（重试前先确认现场）：{reason}"
                        )
                    else:
                        result = f"{_tools.TOOL_ERROR_PREFIX}这个调用没有执行：{reason}"
                    steps.append({
                        "tool": str(fn.get("name") or ""),
                        "arguments": str(fn.get("arguments") or ""),
                        "result": result,
                        "ok": False,
                    })
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": str(tc.get("id") or f"call_r{round_no}"),
                        "content": result,
                    }
                    history.append(tool_message)
                    protocol_messages.append(tool_message)
                    writer.record(tool_message)

        if awaiting_bash_approval:
            # 暂停点：不写最终回复。审批接口执行 bash 后用 resume_after_approval
            # 重新拉起循环，模型看到真实执行结果后继续输出最终文本。
            return "", steps, protocol_messages

        round_no += 1