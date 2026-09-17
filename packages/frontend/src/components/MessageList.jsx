// 样式：packages/chat/src/chat/static/styles/03-conversation.css（.message-row / .bubble / .message-action）与 04-tools.css（.tool-step / .tool-running / .item-unknown）
// 窄屏（手机）覆盖统一在 06-responsive-overlay.css，改小屏表现去那里找。
import React, { useEffect, useRef, useState } from "react";

import { copyText } from "../clipboard";
import { assistantTurnRanges, assistantTurnToText, messageToText } from "../messageText";
import { IconCheckOutline16, IconCopyOutline16 } from "../icons";
import MarkdownContent from "./MarkdownContent";

// 复制控件。三种长相，一套状态机（className 由调用方给，见下面三处用法）：
//
//   气泡下面（消息级）  28×28 图标按钮，复制成功后把图标换成对勾（DSH 的
//                       MessageIconActions 就是这么做的：28×28、圆角 28px、
//                       透明底 label-tertiary，悬停换 interactive-bg-hover 底 +
//                       label-secondary 色）
//   段尾               文字胶囊「复制整段」—— 与消息级差一整轮的内容，
//                       做成文字才不会点错
//   工具块头部          文字按钮「复制」，**和代码块那个一模一样**（上游也是这个
//                       位置逻辑：TerminalBlock 的 header 左边是命令行、右边是
//                       复制控件）。工具输出是"一块内容"，不该套消息那套操作条。
//
// 与上游唯一的出入：上游 writeClipboard 失败时静默（不声称成功），这里额外把控件
// 染成错误色并给出 title。原因是本服务跑在纯 HTTP 上，手机那个 origin 没有
// clipboard API，只能走 execCommand 回退 —— 两条路都可能不通，"点了没反应"
// 是这里最需要避免的状态。
function CopyControl({ text, label, className, renderIcon = false }) {
  const [state, setState] = useState("idle");   // idle | copied | failed
  const timer = useRef(null);

  // 卸载时清掉定时器：轮询会让列表频繁重建，留着定时器会对已卸载组件 setState。
  useEffect(() => () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
    }
  }, []);

  if (!text) {
    return null;
  }

  async function handleClick(event) {
    // 工具块的那个按钮长在 <summary> 里 —— 不拦住冒泡的话，点复制会顺带把
    // 输出折叠/展开掉。消息级那几个不在可点区域里，拦一下也无害。
    event.stopPropagation();
    const ok = await copyText(text);
    setState(ok ? "copied" : "failed");
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
    }
    timer.current = window.setTimeout(() => setState("idle"), 1000);
  }

  const title = state === "copied" ? "已复制" : state === "failed" ? "复制失败" : label;
  const shown = state === "copied" ? "已复制" : state === "failed" ? "复制失败" : label;

  return (
    <button
      type="button"
      className={`${className}${state === "failed" ? " is-failed" : ""}`}
      onClick={handleClick}
      title={title}
      aria-label={title}
    >
      {renderIcon ? (state === "copied" ? <IconCheckOutline16 /> : <IconCopyOutline16 />) : shown}
    </button>
  );
}

// 这一行要不要画。抽出来是因为"整段按钮挂在哪一行"也要用同一个判断：
// 段尾那一条可能是个空正文的 assistant（它不画），按钮就得往上找最近画出来的那条。
function isRendered(message) {
  if (message.role === "plan") {
    return false;
  }
  if (message.role === "assistant" && !String(message.content || "").trim()) {
    return false;
  }
  if (
    message.role === "bash-request" &&
    (message.status === "pending" || message.status === "submitting")
  ) {
    return false;
  }
  return true;
}

// 只有**消息**（你的提问、助手的回复）才有气泡下面那套操作条。
// 工具块没有 —— 它的复制控件长在自己的头部行里（见 tool-step 的 summary），
// 因为工具输出是"一块内容"，跟代码块同类，不该套消息那套。
function hasMessageActions(message) {
  return message.role === "user" || message.role === "assistant";
}

// 一轮 loop 只给**一个**复制按钮，挂在轮末那条助手条目上；同一轮里其它助手条目不画。
//
// 为什么必须这样：一段 loop 是"模型说一句 → 调工具 → 再收尾"，而日志里它是**多条**
// assistant 记录（中间夹着工具结果），界面上就是两个气泡。每个气泡都放一个按钮，
// 看起来就成了"两段、要复制两次" —— 尽管它们复制的内容完全一样。一轮一个按钮，
// 语义才和"复制这一整轮"对上。
//
// 注意这不改变内容的顺序：工具结果仍然在两句叙述**中间**（那是真实发生顺序），
// 所以两个气泡不会合并 —— 合并就得挪动工具块的位置，那是拿真实性换整齐。
function turnTextByRow(messages) {
  const texts = new Map();
  for (const range of assistantTurnRanges(messages)) {
    const text = assistantTurnToText(messages.slice(range.start, range.end + 1));
    if (!text) {
      continue;   // 整段没内容（比如只有一条 plan）就没有可复制的
    }
    // 从后往前找**第一条**（也就是最后一条）真的画出来的助手条目
    for (let index = range.end; index >= range.start; index -= 1) {
      if (messages[index].role === "assistant" && isRendered(messages[index])) {
        texts.set(index, text);
        break;
      }
    }
  }
  return texts;
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
  // 助手条目 → 它所在那一轮的完整文本（含工具调用与输出）。
  const turnTexts = turnTextByRow(messages);

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
        if (!isRendered(message)) {
          return null;
        }
        // 助手侧：只有**轮末**那一条有复制按钮，复制的是整轮（含工具调用与输出）；
        // user 侧：复制自己的提问。
        const turnText = message.role === "assistant" ? turnTexts.get(index) : null;
        const copyText = message.role === "assistant" ? turnText : messageToText(message);
        const copyLabel = message.role === "assistant"
          ? "复制这一整轮（含工具调用与输出）"
          : "复制这条提问";
        return (
        <div
          key={message.id}
          className={message.role === "user" ? "message-row user-row" : "message-row"}
        >
          <div className={message.role === "user" ? "bubble user-bubble" : "bubble"}>
            {message.role === "tool-step" ? (
              <details className="tool-step">
                {/* 工具块的头部行：左边工具名、右边复制控件（上游 TerminalBlock 的
                    header 就是这个排布：命令行在左、复制控件在右）。按钮在 <summary>
                    里，所以 CopyControl 里拦了事件冒泡 —— 否则点复制会顺带折叠。 */}
                <summary>
                  <span className="tool-step-name">
                    ⚙ {message.tool} {message.ok ? "" : "（执行失败）"}
                  </span>
                  <CopyControl text={copyText} label="复制" className="tool-step-copy" />
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
                  <span className="tool-step-name">
                    ⚙ run_bash {message.status === "rejected" ? "（已拒绝）" : ""}
                  </span>
                  <CopyControl text={copyText} label="复制" className="tool-step-copy" />
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
          {/* 操作条在**气泡下面**，不在右上角浮着 —— 位置和间距照上游：
              user 侧是「气泡 + IconActions」右对齐的竖列（gap 6）；
              assistant 侧是正文下方的 footer（margin-top 16、左移 6 做光学对齐，
              因为 28px 的点击区比字形宽出 6px）。
              没有可复制文本的行（正在执行、未知条目）整条不画 —— 否则会留一个
              28px 高的空行。

              「一轮一个复制按钮」见上面 turnTextByRow 的注释：助手侧只有轮末那一条
              有按钮，复制的是整轮（含工具调用与输出）。工具块另外还有自己的复制
              按钮（在它自己的头部行里），那是"只要这一块输出"用的。 */}
          {hasMessageActions(message) && copyText ? (
            <div className="message-actions">
              <CopyControl text={copyText} label={copyLabel} className="message-action" renderIcon />
            </div>
          ) : null}
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
