import React from "react";

// 危险操作的确认框。存在的理由：删对话 / 删工作区都会**真的删掉日志文件**，
// 而日志是那些对话的唯一载体 —— 没有数据库，也没有回收站。
//
// 所以这个框不是走过场：标题说清对象，正文说清代价（几场对话、找不回来），
// 确认按钮也**不复用**「确认」这种没信息量的词，直接把后果写在按钮上。
//
// 样式复用目录选择器的 .modal / .modal-actions（同一套动作栏：左边留空、
// 右边取消 + 危险动作），不另起一套弹窗材质。
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  busy = false,
}) {
  if (!open) {
    return null;
  }
  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-title">{title}</div>
        <div className="modal-body">{message}</div>
        <div className="modal-actions">
          {/* 左边留空：动作栏是 space-between，取消与危险动作固定靠右。 */}
          <div className="modal-actions-left" />
          <div className="modal-actions-right">
            <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
              取消
            </button>
            <button type="button" className="danger-button" onClick={onConfirm} disabled={busy}>
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
