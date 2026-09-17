// 样式：packages/chat/src/chat/static/styles/04-tools.css（.bash-approval-*）
// 窄屏（手机）覆盖统一在 06-responsive-overlay.css，改小屏表现去那里找。
import React from "react";

// 审批面板：占据 composer 位置（DSH 风格的 composer-takeover）。
// 顶部琥珀色 band + 呼吸点，中间是命令，右下角「拒绝 / 允许一次」。
// 点击一次后按钮进入禁用态，面板离开、输入框回来 —— 审批是一次性的。
export default function BashApprovalPanel({
  command,
  cwd,
  submitting,
  stopping,
  onApprove,
  onReject,
  onStop,
}) {
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
          {/* 停止 = **放弃这一轮**（不是"拒绝这条命令"）：拒绝是"这条别跑，接着干活"，
              停止是"整轮不干了"。这个按钮以前不存在，因为暂停期间服务端没有可停的东西；
              现在暂停是一种真状态（令牌留着、/interrupt 收尾），它才有意义。 */}
          <button
            type="button"
            className="bash-approval-stop"
            onClick={onStop}
            disabled={submitting || stopping}
          >
            {stopping ? "正在停止…" : "停止这一轮"}
          </button>
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
