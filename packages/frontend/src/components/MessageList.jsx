import React, { useState } from "react";

import { copyText } from "../clipboard";
import { messageToText, turnRanges, turnToText } from "../messageText";
import MarkdownContent from "./MarkdownContent";

// 复制按钮：自己管「已复制 / 失败」这点局部状态，不往上交给 App ——
// 它是纯界面状态，跟会话数据无关。
//
// 失败要**显眼**：这个服务是纯 HTTP，手机那个 origin 拿不到 clipboard API，
// 只能走 execCommand 回退；万一两条路都不通，用户必须看得见，而不是按了没反应。
function CopyButton({ text, label, title }) {
  const [state, setState] = useState("idle");

  if (!text) {
    return null;
  }

  async function handleClick() {
    const ok = await copyText(text);
    setState(ok ? "done" : "failed");
    window.setTimeout(() => setState("idle"), 1400);
  }

  return (
    <button
      type="button"
      className={`message-copy${state === "failed" ? " is-failed" : ""}`}
      onClick={handleClick}
      title={title}
    >
      {state === "done" ? "已复制" : state === "failed" ? "复制失败" : label}
    </button>
  );
}

// 对话区。四类条目共用同一套气泡：user / assistant / tool-step / tool-running。
//
// **不写「我：/ 助手：」标签**：说话人靠对齐（user 靠右、assistant 靠左）和气泡
// 样式区分就够了。那两个标签以前是用 color:transparent 藏起来的（界面看不见、
// 复制时带上），现在连元素一起删掉了。
//
// tool-running = **此刻正在跑**的那个工具（声明了调用、结果还没落盘）。它只会在
// 轮询过程中出现，用来回答"现在到底卡在哪一步"—— 没有它的话，进度只能显示到
// 上一个**跑完**的步骤，中间那段等待看起来就还是"正在思考"。
export default function MessageList({ messages, isLoading }) {
  // 每一段 loop 的起止下标。按钮挂在**段首**那一行上 —— 一段 loop 总是从一条
  // user 条目开始，所以"复制整段"放在你的提问旁边最符合直觉。
  const ranges = turnRanges(messages);
  const rangeByStart = new Map(ranges.map((range) => [range.start, range]));

  return (
    <section className="chat-box">
      {messages.map((message, index) => {
        // 待审批 / 正在提交的 bash 请求**不进对话流**：审批交互只在输入端
        // （Composer 位置的审批面板），免得它把对话本身挤开。
        // 跑完之后才以普通工具步骤的形式出现，和 read_file/run_bash 一样可折叠。
        //
        // 正文为空的 assistant 条目也**整行不画**：那种记录确实会落盘（带 tool_calls
        // 的轮次里，模型只调用工具、没说任何话），画出来就是一个空气泡 —— 界面看着
        // 像出错了。判断放在这里而不是只放在 MarkdownContent 里：后者只能去掉里层
        // 的 .markdown-content，外层 .bubble 照样占一行。
        if (
          message.role === "plan" ||
          (message.role === "assistant" && !String(message.content || "").trim()) ||
          (message.role === "bash-request" &&
            (message.status === "pending" || message.status === "submitting"))
        ) {
          return null;
        }
        const turn = rangeByStart.get(index);
        return (
        <div
          key={message.id}
          className={message.role === "user" ? "message-row user-row" : "message-row"}
        >
          {/* 操作条：悬停才出现（触屏上常显，见 CSS 的 @media (hover: none)）。
              每行一个「复制」，段首那一行多一个「复制整段」。 */}
          <div className="message-actions">
            <CopyButton
              text={messageToText(message)}
              label="复制"
              title="复制这一条"
            />
            {turn ? (
              <CopyButton
                text={turnToText(messages.slice(turn.start, turn.end + 1))}
                label="复制整段"
                title="复制这一段：你的提问 + 这一轮 agent 的全部产出（含工具输出）"
              />
            ) : null}
          </div>
          <div className={message.role === "user" ? "bubble user-bubble" : "bubble"}>
            {message.role === "tool-step" ? (
              <details className="tool-step">
                <summary>
                  ⚙ {message.tool} {message.ok ? "" : "（执行失败）"}
                </summary>
                <pre>{message.result}</pre>
              </details>
            ) : message.role === "tool-running" ? (
              <div className="tool-running">
                <span className="tool-running-dot" />⚙ {message.tool} 正在执行…
              </div>
            ) : message.role === "bash-request" ? (
              // 只有跑完/被拒之后才走到这里（pending 与 submitting 在上面被过滤掉了）。
              // 长相与普通 run_bash 步骤一致，不再是一个抢眼的"Bash 请求"卡片。
              <details className="tool-step" open={message.status === "rejected"}>
                <summary>
                  ⚙ run_bash {message.status === "rejected" ? "（已拒绝）" : ""}
                </summary>
                <pre>{message.result}</pre>
              </details>
            ) : message.role === "unknown" ? (
              // 服务端给了一个前端不认识的 kind。**明说**，不要当成普通消息画出来 ——
              // 静默兜底会让"后端加了新条目类型"这件事在界面上完全看不出来。
              // 契约里的 item_kind 取值域 + smoke 逐一渲染，保证这条平时走不到。
              <div className="item-unknown">
                ⚠ 未知条目（kind={message.kind}）—— 前端还不认识它，升级一下界面
              </div>
            ) : message.role === "assistant" ? (
              <MarkdownContent content={message.content} />
            ) : (
              message.content
            )}
          </div>
        </div>
        );
      })}

      {/* 只有在**没有任何正在跑的工具**时才显示"正在思考" —— 否则两条提示会打架
          （上面刚说"正在执行 bash"，下面又说"正在思考"）。 */}
      {isLoading && !messages.some((message) => message.role === "tool-running") ? (
        <div className="message-row">
          <div className="bubble loading-bubble">正在思考...</div>
        </div>
      ) : null}
    </section>
  );
}
