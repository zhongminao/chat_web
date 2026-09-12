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

export async function createWorkspace(root) {
  const response = await fetch("/api/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root }),
  });
  if (!response.ok) {
    throw new Error("路径不是一个存在的目录");
  }
  const data = await response.json();
  return data.workspace;
}

// 还有对话引用它时服务端会拒绝（409）—— 那些对话会变成孤儿，找不到也回不来。
export async function deleteWorkspace(workspaceId) {
  const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
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
