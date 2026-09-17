import React from "react";

// 审批面板：占据 composer 位置（DSH 风格的 composer-takeover）。
// 顶部琥珀色 band + 呼吸点，中间是命令，右下角「拒绝 / 允许一次」。
// 点击一次后按钮进入禁用态，面板离开、输入框回来 —— 审批是一次性的。
export default function BashApprovalPanel({ command, cwd, submitting, onApprove, onReject }) {
  return (
    <div className="bash-approval-root">
      <div className="bash-approval-card">
        <div className="bash-approval-strip">
          <span className="bash-approval-dot" />
          等待审批
        </div>
        <div className="bash-approval-body">
          <div className="bash-approval-headline">模型请求执行 bash 命令</div>
          {cwd ? <div className="bash-approval-cwd">cwd: {cwd}</div> : null}
          <div className="bash-approval-command">{command}</div>
        </div>
        <div className="bash-approval-actions">
          <button
            type="button"
            className="bash-approval-reject"
            onClick={onReject}
            disabled={submitting}
          >
            拒绝
          </button>
          <button
            type="button"
            className="bash-approval-allow"
            onClick={onApprove}
            disabled={submitting}
          >
            {submitting ? "正在执行…" : "允许一次"}
          </button>
        </div>
      </div>
    </div>
  );
}
