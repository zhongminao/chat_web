import React from "react";

// 系统提示词面板。两份提示词的分工见 App：systemPrompt 是当前值，随时可改；
// defaultSystemPrompt 是默认值的副本，只在接口回来后写一次，之后不动 —— 否则
// 「恢复默认」没有东西可恢复。
export default function SystemPanel({
  systemPrompt,
  onSystemPromptChange,
  defaultSystemPrompt,
  toolsEnabled,
  toolSystemPrompt,
  onRestoreDefault,
}) {
  const restoredValue =
    toolsEnabled && toolSystemPrompt
      ? toolSystemPrompt + "\n\n" + defaultSystemPrompt
      : defaultSystemPrompt;

  return (
    <section className="system-panel">
      <div className="system-panel-title">
        <span>系统提示词（System Prompt）</span>
        <button
          type="button"
          className="link-button"
          onClick={onRestoreDefault}
          disabled={!defaultSystemPrompt || systemPrompt === restoredValue}
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
        修改后对下一次发送生效，不影响已有对话记录。留空则这一轮不发送系统提示词。
      </div>
    </section>
  );
}
