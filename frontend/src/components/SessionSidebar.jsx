import React, { useEffect, useRef, useState } from "react";

// 月日 + 时刻。原先当天只显示时刻，跨天后只有一个 MM-DD —— 分不清是哪天几点
// 发生的事，所以统一带上日期。
function formatTime(ms) {
  if (!ms) {
    return "";
  }
  const at = new Date(ms);
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

// 左侧栏：工作区 + 会话历史。
//
// 收起时不留整条空白，缩成一条窄条（对齐上游 SIDEBAR_COLLAPSED 的 56px），
// 里面只放展开按钮 —— 收起之后仍然点得到，不用去顶栏找入口。
//
// 工作区是个实体（id / 名字 / 根路径），所以这里是可点的：点开列出已登记的工作区，
// 也能按路径新登记一个、或删掉一个。切换工作区等于开始一场新对话
// （一个对话只属于一个工作区）。
export default function SessionSidebar({
  workspace,
  workspaces,
  sessions,
  activeId,
  collapsed,
  onToggle,
  onSelectWorkspace,
  onAddWorkspace,
  onRemoveWorkspace,
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

  function closeMenu() {
    setIsMenuOpen(false);
    setIsAdding(false);
    setError("");
  }

  function pickWorkspace(id) {
    onSelectWorkspace(id);
    closeMenu();
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

  async function removeWorkspace(event, id) {
    event.stopPropagation();   // 别让它冒泡成"选中这个工作区"
    try {
      await onRemoveWorkspace(id);
      setError("");
    } catch (removeError) {
      setError(removeError.message);
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
              <div
                key={item.id}
                className={
                  item.id === workspace?.id
                    ? "workspace-option is-selected"
                    : "workspace-option"
                }
              >
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={item.id === workspace?.id}
                  className="workspace-option-main"
                  onClick={() => pickWorkspace(item.id)}
                >
                  <span className="workspace-option-name">{item.name}</span>
                  <span className="workspace-option-path">{item.root}</span>
                </button>
                <button
                  type="button"
                  className="row-action"
                  onClick={(event) => removeWorkspace(event, item.id)}
                  aria-label={`删除工作区 ${item.name}`}
                  title="删除这个工作区（里面还有对话时会失败）"
                >
                  ✕
                </button>
              </div>
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
              </form>
            ) : (
              <button
                type="button"
                className="workspace-option-main is-add"
                onClick={() => setIsAdding(true)}
              >
                <span className="workspace-option-name">添加工作区…</span>
              </button>
            )}

            {error ? <div className="workspace-error">{error}</div> : null}
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
