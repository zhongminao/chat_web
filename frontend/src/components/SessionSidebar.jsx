import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  IconFolderClose16,
  IconFolderOpen16,
  IconNewChatOutline16,
  IconPanelLeftOutline16,
  IconProjectAddOutline16,
  IconSearchOutline16,
  IconTrashOutline16,
} from "../icons";
import DirectoryPicker from "./DirectoryPicker";

// 月日 + 时刻。原先当天只显示时刻、跨天只有一个 MM-DD —— 分不清哪天几点。
function formatTime(ms) {
  if (!ms) {
    return "";
  }
  const at = new Date(ms);
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

// 左侧栏：工作区分组 + 每个工作区下的会话历史。
//
// 分组对齐上游 sidebar 的 groupBy=workspace：**所有**工作区的会话一起列出来、
// 按工作区分组，而不是过滤到当前那一个。切换工作区 = 点它的组头（或点它下面的
// 任何一场对话），「新对话」落在当前工作区。
//
// 收起时缩成 56px 窄条（上游 SIDEBAR_COLLAPSED），只留展开按钮 —— 收起不等于
// 消失，入口留在原地。
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
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState({});
  const searchRef = useRef(null);

  const keyword = query.trim();

  // 分组：按登记顺序列出每个工作区，会话挂到各自的组下。搜索时只留命中的组，
  // 不然一搜就只剩一堆空组头。
  const groups = useMemo(() => {
    const byWorkspace = workspaces.map((entry) => ({
      workspace: entry,
      sessions: sessions.filter((session) => {
        if (session.workspaceId !== entry.id) {
          return false;
        }
        return !keyword || session.title.includes(keyword);
      }),
    }));
    // 老数据可能没有 workspaceId（迁移补不上时）—— 别让它们从列表里消失。
    const orphans = sessions.filter(
      (session) => !workspaces.some((entry) => entry.id === session.workspaceId)
    );
    const visible = keyword
      ? byWorkspace.filter((group) => group.sessions.length > 0)
      : byWorkspace;
    if (orphans.length) {
      visible.push({
        workspace: { id: "", name: "未分组", root: "" },
        sessions: orphans.filter((session) => !keyword || session.title.includes(keyword)),
      });
    }
    return visible;
  }, [workspaces, sessions, keyword]);

  function closeSearch() {
    setSearchOpen(false);
    setQuery("");
  }

  useEffect(() => {
    if (searchOpen) {
      searchRef.current?.focus();
    }
  }, [searchOpen]);

  async function removeWorkspace(event, entry) {
    event.stopPropagation();   // 别冒泡成"选中这个工作区"
    try {
      await onRemoveWorkspace(entry.id);
      setError("");
    } catch (removeError) {
      setError(removeError.message);
    }
  }

  async function confirmAdd(root) {
    const entry = await onAddWorkspace(root);
    setPickerOpen(false);
    return entry;
  }

  if (collapsed) {
    return (
      <aside className="sidebar is-collapsed">
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label="展开侧栏"
          title="展开侧栏"
        >
          <IconPanelLeftOutline16 size={18} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      {/* 顶行：当前工作区 + 收起按钮。这里只是"新对话落在哪"的提示，切换靠下面的组头。 */}
      <div className="sidebar-head">
        <span className="sidebar-workspace-name" title={workspace?.root || ""}>
          {workspace?.name || "工作区"}
        </span>
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label="收起侧栏"
          title="收起侧栏"
        >
          <IconPanelLeftOutline16 size={16} />
        </button>
      </div>

      <button type="button" className="new-session-button" onClick={onNew}>
        <IconNewChatOutline16 size={16} />
        新对话
      </button>

      {/* 区块头（上游 WorkspaceBrowser 的 sectionHeader）：标签 + 内联搜索 + 动作。
          搜索展开时标签与动作簇一起收 max-width 让位 —— 过渡靠 CSS，不是硬切。 */}
      <div className="section-header">
        <span className={searchOpen ? "section-label is-hidden" : "section-label"}>工作区</span>
        <div className={searchOpen ? "section-search-slot is-expanded" : "section-search-slot"}>
          <button
            type="button"
            className="section-icon-button"
            onClick={() => setSearchOpen(true)}
            disabled={searchOpen}
            aria-label="搜索对话"
            title="搜索对话"
          >
            <IconSearchOutline16 size={searchOpen ? 12 : 14} />
          </button>
          <input
            ref={searchRef}
            className="section-search-input"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                closeSearch();
              }
            }}
            tabIndex={searchOpen ? 0 : -1}
            placeholder="搜索对话"
            aria-label="搜索对话"
          />
          {/* 清空按钮在搜索槽**里面**（上游 clearButton 也在 search 里）——
              放进右边动作簇会被"搜索展开时隐藏"的规则一起藏掉。 */}
          <button
            type="button"
            className="section-clear-button"
            onClick={closeSearch}
            aria-label="清空搜索"
            title="清空搜索"
          >
            ✕
          </button>
        </div>
        <div className={searchOpen ? "section-actions is-hidden" : "section-actions"}>
          <button
            type="button"
            className="section-icon-button"
            onClick={() => {
              setError("");
              setPickerOpen(true);
            }}
            aria-label="添加工作区"
            title="添加工作区"
          >
            <IconProjectAddOutline16 size={16} />
          </button>
        </div>
      </div>

      <nav className="session-list">
        {groups.length === 0 ? (
          <div className="session-empty">{keyword ? "没有匹配的对话" : "还没有工作区"}</div>
        ) : (
          groups.map((group) => {
            const isCurrent = group.workspace.id === workspace?.id;
            const isOpen = expanded[group.workspace.id] !== false;
            return (
              <section key={group.workspace.id || "__orphan"} className="group">
                {/* 整行点击 = 折叠/展开（上游 projectRow 就是这么做的：行本身是开关）。
                    以前只有那个 20px 的小文件夹能点，等于没有折叠功能。
                    「在这个工作区新建对话」和「删除」做成 hover 才出现的行内操作。 */}
                <div className={isCurrent ? "group-header is-current" : "group-header"}>
                  <button
                    type="button"
                    className="group-main"
                    onClick={() =>
                      setExpanded((state) => ({
                        ...state,
                        [group.workspace.id]: !isOpen,
                      }))
                    }
                    aria-expanded={isOpen}
                    aria-label={`${isOpen ? "收起" : "展开"} ${group.workspace.name}`}
                    title={group.workspace.root || group.workspace.name}
                  >
                    <span className="group-icon">
                      {isOpen ? <IconFolderOpen16 size={16} /> : <IconFolderClose16 size={16} />}
                    </span>
                    <span className="group-name">{group.workspace.name}</span>
                  </button>

                  <span className="group-count">{group.sessions.length}</span>

                  <div className="group-actions">
                    {group.workspace.id ? (
                      <button
                        type="button"
                        className="row-action"
                        onClick={() => {
                          onSelectWorkspace(group.workspace.id);
                          onNew();
                        }}
                        aria-label={`在 ${group.workspace.name} 新建对话`}
                        title="在这个工作区新建对话"
                      >
                        <IconNewChatOutline16 size={14} />
                      </button>
                    ) : null}
                    {group.workspace.id ? (
                      <button
                        type="button"
                        className="row-action"
                        onClick={(event) => removeWorkspace(event, group.workspace)}
                        aria-label={`删除工作区 ${group.workspace.name}`}
                        title="删除这个工作区（里面还有对话时会失败）"
                      >
                        <IconTrashOutline16 size={14} />
                      </button>
                    ) : null}
                  </div>
                </div>

                {isOpen
                  ? group.sessions.map((session) => (
                      <button
                        key={session.id}
                        type="button"
                        className={
                          session.id === activeId ? "session-item is-active" : "session-item"
                        }
                        onClick={() => onSelect(session)}
                        title={session.title}
                      >
                        <span className="session-item-title">{session.title}</span>
                        <span className="session-item-meta">
                          {session.turns} 轮 · {formatTime(session.lastActivity)}
                        </span>
                      </button>
                    ))
                  : null}
              </section>
            );
          })
        )}
      </nav>

      {error ? <div className="sidebar-error">{error}</div> : null}
      <div className="session-list-fade" aria-hidden="true" />

      <DirectoryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onConfirm={confirmAdd}
      />
    </aside>
  );
}
