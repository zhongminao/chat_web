// 后端接口。三个调用点都在这里，组件里不再直接写 fetch。

export async function fetchProviders() {
  const response = await fetch("/api/providers");
  return response.json();
}

// 侧栏用：{workspace, sessions: [{id, title, workspace, turns, lastActivity, createdAt}]}
export async function fetchSessions() {
  const response = await fetch("/api/sessions");
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
