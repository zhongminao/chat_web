import React from "react";

function formatTime(ms) {
  if (!ms) {
    return "";
  }
  const at = new Date(ms);
  const now = new Date();
  if (at.toDateString() === now.toDateString()) {
    return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
  }
  return `${at.getMonth() + 1}-${at.getDate()}`;
}

function baseName(path) {
  if (!path) {
    return "";
  }
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

// 左侧栏：工作区 + 会话历史。
//
// 收起时不留整条空白，缩成一条窄条（宽 56px，对齐上游 SIDEBAR_COLLAPSED），
// 里面只放展开按钮 —— 收起之后仍然点得到，不用去顶栏找入口。
//
// 工作区只显示一次（末端目录名，完整路径在 title 里）：现在服务只有一个工作区，
// 每行都重复一遍路径没有意义。会话按最后活动倒序，由服务端排好。
export default function SessionSidebar({
  workspace,
  sessions,
  activeId,
  collapsed,
  onToggle,
  onSelect,
  onNew,
}) {
  return (
    <aside className={collapsed ? "sidebar is-collapsed" : "sidebar"}>
      {collapsed ? (
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label="展开对话列表"
          title="展开对话列表"
        >
          »
        </button>
      ) : (
        <>
          <div className="sidebar-head">
            <div className="sidebar-workspace" title={workspace || ""}>
              <span className="sidebar-workspace-name">
                {baseName(workspace) || "工作区"}
              </span>
            </div>
            <button
              type="button"
              className="sidebar-toggle"
              onClick={onToggle}
              aria-label="收起对话列表"
              title="收起对话列表"
            >
              «
            </button>
          </div>

          <button type="button" className="new-session-button" onClick={onNew}>
            新对话
          </button>

          <nav className="session-list">
            {sessions.length === 0 ? (
              <div className="session-empty">还没有对话</div>
            ) : (
              sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  className={session.id === activeId ? "session-item is-active" : "session-item"}
                  onClick={() => onSelect(session.id)}
                  title={session.title}
                >
                  <span className="session-item-title">{session.title}</span>
                  <span className="session-item-meta">
                    {session.turns} 轮 · {formatTime(session.lastActivity)}
                  </span>
                </button>
              ))
            )}
          </nav>
        </>
      )}
    </aside>
  );
}
