// 后端接口。三个调用点都在这里，组件里不再直接写 fetch。

export async function fetchProviders() {
  const response = await fetch("/api/providers");
  return response.json();
}

// 侧栏用：{workspace, sessions: [{id, title, workspaceId, turns, lastActivity}]}
// 按工作区过滤放在服务端做 —— 切到别的工作区时不该还看见另一个工作区的对话。
export async function fetchSessions(workspaceId) {
  const query = workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : "";
  const response = await fetch(`/api/sessions${query}`);
  return response.json();
}

// {workspaces: [{id, name, root}], default: "<id>"}
export async function fetchWorkspaces() {
  const response = await fetch("/api/workspaces");
  return response.json();
}

// 两种用法：{root} 登记已存在的目录；{parent, name} 新建目录再登记。
export async function createWorkspace(payload) {
  const response = await fetch("/api/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "登记工作区失败");
  }
  return data.workspace;
}

// 目录浏览（添加工作区用）。只返回子目录名，不读文件内容。
export async function browseDirectories(path) {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  const response = await fetch(`/api/browse${query}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "读不了这个目录");
  }
  return data;
}

// 默认只删登记；里面还有对话时服务端会拒绝（409）—— 那些对话的 header 指向它，
// 删了登记它们就成孤儿，找不到也回不来。
//
// withSessions 才连同里面的对话一起删。那是**不可撤销**的，所以服务端要显式参数
// 才肯做：调用方必须先让用户确认过。返回的 deletedSessions 是实际删掉几场。
export async function deleteWorkspace(workspaceId, { withSessions = false } = {}) {
  const query = withSessions ? "?withSessions=true" : "";
  const response = await fetch(
    `/api/workspaces/${encodeURIComponent(workspaceId)}${query}`,
    { method: "DELETE" },
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "删除失败");
  }
  return data;
}

// 删掉一场对话。日志文件就是它的全部（历史是折叠回放这份日志得来的），
// 删了没有回收站 —— 调用前必须确认过。
export async function deleteSession(sessionId) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "删除失败");
  }
  return data;
}

// 会话级设置（是否使用工具）。它属于对话，不属于界面。
export async function updateSessionSettings(sessionId, settings) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  return response.json();
}

// 历史里还没说过话时服务端返回 404 —— 那是正常情况，不是错误。
export async function fetchSessionItems(sessionId) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    return null;
  }
  return response.json();
}

export async function sendChat(payload) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`请求失败，状态码：${response.status}`);
  }
  return response.json();
}
