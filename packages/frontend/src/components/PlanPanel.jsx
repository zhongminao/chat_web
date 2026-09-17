import React from "react";

const TODO_STATUS_LABELS = {
  pending: "待处理",
  in_progress: "进行中",
  completed: "已完成",
};

export default function PlanPanel({ todos }) {
  if (!Array.isArray(todos) || todos.length === 0) {
    return null;
  }

  const completed = todos.filter((todo) => todo.status === "completed").length;

  return (
    <details className="plan-panel" open>
      <summary className="plan-panel-summary">
        <span>任务清单</span>
        <span className="plan-panel-count">{completed}/{todos.length}</span>
      </summary>
      <div className="plan-list">
        {todos.map((todo, index) => {
          const status = String(todo.status || "pending");
          return (
            <div className={`plan-item ${status}`} key={`${status}:${index}:${todo.content}`}>
              <span className="plan-dot" aria-hidden="true">
                {status === "completed" ? "✓" : ""}
              </span>
              <span className="plan-content">{todo.content}</span>
              <span className="plan-status">{TODO_STATUS_LABELS[status] || status}</span>
            </div>
          );
        })}
      </div>
    </details>
  );
}
