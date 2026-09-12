import React from "react";

// 对话区。三类条目共用同一套气泡：user / assistant / tool-step，
// 说话人标签由 CSS 做成透明（复制时带上、界面不显示）。
export default function MessageList({ messages, isLoading }) {
  return (
    <section className="chat-box">
      {messages.map((message) => (
        <div
          key={message.id}
          className={message.role === "user" ? "message-row user-row" : "message-row"}
        >
          <div className="message-role">{message.role === "user" ? "我：" : "助手："}</div>
          <div className={message.role === "user" ? "bubble user-bubble" : "bubble"}>
            {message.role === "tool-step" ? (
              <details className="tool-step">
                <summary>
                  ⚙ {message.tool} {message.ok ? "" : "（执行失败）"}
                </summary>
                <pre>{message.result}</pre>
              </details>
            ) : (
              message.content
            )}
          </div>
        </div>
      ))}

      {isLoading ? (
        <div className="message-row">
          <div className="message-role">助手：</div>
          <div className="bubble loading-bubble">正在思考...</div>
        </div>
      ) : null}
    </section>
  );
}
