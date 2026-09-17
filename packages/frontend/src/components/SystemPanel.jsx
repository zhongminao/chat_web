// 样式：packages/chat/src/chat/static/styles/02-controls.css（.system-panel）
// 窄屏（手机）覆盖统一在 06-responsive-overlay.css，改小屏表现去那里找。
import React from "react";

// 系统提示词面板。
//
// systemPrompt 是**当前值**——面板里显示的就是实际会发送的那条 system 消息，用户
// 随时可以改。这里**不做任何拼接**：服务端已经拼好了两份成品（promptPlain /
// promptWithTools），切换工具开关时 App 负责在两者之间选，这个组件只管显示和编辑。
//
// 以前这里有一份 `toolSystemPrompt + "\n\n" + defaultSystemPrompt`——同一条拼接
// 规则在前后端一共四份实现，改一次分隔符谁都不会跟着动。现在只有一处：
// runtime.resolve_system_prompt。
export default function SystemPanel({
  systemPrompt,
  onSystemPromptChange,
  restoreValue,
  onRestoreDefault,
}) {
  return (
    <section className="system-panel">
      <div className="system-panel-title">
        <span>系统提示词（System Prompt）</span>
        <button
          type="button"
          className="link-button"
          onClick={onRestoreDefault}
          disabled={!restoreValue || systemPrompt === restoreValue}
        >
          恢复默认
        </button>
      </div>
      <textarea
        value={systemPrompt}
        onChange={(event) => onSystemPromptChange(event.target.value)}
        rows={6}
      />
      <div className="system-panel-hint">
        修改后对下一次发送生效，并记进这一轮的会话日志（刷新或换设备都能恢复）。
        留空则这一轮不发送系统提示词。
      </div>
    </section>
  );
}
