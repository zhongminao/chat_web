import React, { useEffect, useRef, useState } from "react";

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

// 左侧栏：工作区 + 会话历史。
//
// 收起时不留整条空白，缩成一条窄条（宽 56px，对齐上游 SIDEBAR_COLLAPSED），
// 里面只放展开按钮 —— 收起之后仍然点得到，不用去顶栏找入口。
//
// 工作区是个实体（id / 名字 / 根路径），所以这里是可点的：点开列出已登记的工作区，
// 也能按路径新登记一个。切换工作区等于开始一场新对话（一个对话只属于一个工作区）。
export default function SessionSidebar({
  workspace,
  workspaces,
  sessions,
  activeId,
  collapsed,
  onToggle,
  onSelectWorkspace,
  onAddWorkspace,
  onSelect,
  onNew,
}) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [pathDraft, setPathDraft] = useState("");
  const [error, setError] = useState("");
  const menuRef = useRef(null);

  // 点菜单外面关掉。挂在 document 上才能知道点到的是外面；ref 挂最外层，
  // 让按钮和菜单算作同一块内部区域，否则点按钮会被判成外部点击。
  useEffect(() => {
    if (!isMenuOpen) {
      return undefined;
    }
    function handlePointerDown(event) {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setIsMenuOpen(false);
        setIsAdding(false);
        setError("");
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [isMenuOpen]);

  function pickWorkspace(id) {
    onSelectWorkspace(id);
    setIsMenuOpen(false);
    setIsAdding(false);
    setError("");
  }

  async function submitWorkspace(event) {
    event.preventDefault();
    const root = pathDraft.trim();
    if (!root) {
      return;
    }
    try {
      const entry = await onAddWorkspace(root);
      setPathDraft("");
      pickWorkspace(entry.id);
    } catch (addError) {
      setError(addError.message);
    }
  }

  if (collapsed) {
    return (
      <aside className="sidebar is-collapsed">
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label="展开对话列表"
          title="展开对话列表"
        >
          »
        </button>
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-head" ref={menuRef}>
        <button
          type="button"
          className="workspace-button"
          onClick={() => {
            setIsMenuOpen((open) => !open);
            setIsAdding(false);
            setError("");
          }}
          aria-haspopup="menu"
          aria-expanded={isMenuOpen}
          title={workspace?.root || ""}
        >
          <span className="sidebar-workspace-name">{workspace?.name || "工作区"}</span>
          <span className="model-chip-chevron" aria-hidden="true" />
        </button>
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label="收起对话列表"
          title="收起对话列表"
        >
          «
        </button>

        {isMenuOpen ? (
          <div className="workspace-popup" role="menu">
            {workspaces.map((item) => (
              <button
                key={item.id}
                type="button"
                role="menuitemradio"
                aria-checked={item.id === workspace?.id}
                className={
                  item.id === workspace?.id ? "workspace-option is-selected" : "workspace-option"
                }
                onClick={() => pickWorkspace(item.id)}
              >
                <span className="workspace-option-name">{item.name}</span>
                <span className="workspace-option-path">{item.root}</span>
              </button>
            ))}

            {isAdding ? (
              <form className="workspace-add" onSubmit={submitWorkspace}>
                <input
                  autoFocus
                  value={pathDraft}
                  onChange={(event) => setPathDraft(event.target.value)}
                  placeholder="目录绝对路径"
                  aria-label="新工作区路径"
                />
                {error ? <div className="workspace-add-error">{error}</div> : null}
              </form>
            ) : (
              <button
                type="button"
                className="workspace-option"
                onClick={() => setIsAdding(true)}
              >
                <span className="workspace-option-name">添加工作区…</span>
              </button>
            )}
          </div>
        ) : null}
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
    </aside>
  );
}
