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

// {workspaces: [{id, name, root}], default: "<id>", resolved: "<id>"}
// resolved = 按传进来的 workspaceId 解析出来的"现在该用哪个"（无效就回落默认）。
// 客户端**采纳它**，不要自己再算一遍回落 —— 那是服务端的判断。
export async function fetchWorkspaces(workspaceId) {
  const query = workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : "";
  const response = await fetch(`/api/workspaces${query}`);
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

// 历史里还没说过话时服务端返回 404 —— 那是正常情况，不是错误。
export async function fetchSessionItems(sessionId) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) {
    return null;
  }
  return response.json();
}

// 一轮对话。返回的是**状态回执**（{state, toolsLocked}），不是结果的副本 ——
// 发生过什么去读 GET /api/sessions/{id}（日志的投影，唯一权威）。
//
// 失败时把服务端的 detail 带出来：那些话是给人看的（"这场对话已经有一轮在跑 ——
// 等它结束，或者先点停止"），吞掉它只留一个状态码，用户就没法知道该怎么办。
// status 也带出来，调用方可能按状态分流（比如 400 = sessionId 非法）。
export async function sendChat(payload) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || `请求失败，状态码：${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

// 请求中止这场会话正在跑的那一轮。
//
// **协作式**：服务端只在步边界检查（每次模型调用之前、每条工具调用之前），所以
// 前面还有一步在跑时，这个请求会立刻返回、但那一轮还没停 —— 界面必须显示
// "正在停止…"，真正的结束是 /api/chat 那个请求自己返回（带 interrupted: true）。
//
// interrupted: false 不是错误：点停止时那一轮可能刚好自己结束了。
export async function approveBashRequest(sessionId, requestId) {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/bash-requests/${encodeURIComponent(requestId)}/approve`,
    { method: "POST" },
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "批准 bash 失败");
  }
  return data;
}

export async function rejectBashRequest(sessionId, requestId) {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/bash-requests/${encodeURIComponent(requestId)}/reject`,
    { method: "POST" },
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "拒绝 bash 失败");
  }
  return data;
}

export async function interruptSession(sessionId) {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/interrupt`,
    { method: "POST" },
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "停止失败");
  }
  return data;
}

// 拨沙箱开关。它写的是**会话日志里的一条事件**，不是一次界面状态同步 —— 服务端执行侧
// 每次操作边界都折一遍日志，所以拨完**下一条工具调用**就按新模式走。
//
// 为什么必须单独发这一次：模式以前只能靠下一轮 /api/chat 的 payload 带上去，而"一轮
// 中途改"恰恰是最想改的时候（审批面板横在输入端、模型停着等你回答）。错过那次机会，
// 就得等这一轮结束才生效 —— 看起来就是"改了没用"。
//
// recorded: false 不是错误：还没说过话的会话不落盘（那种选择是前端草稿），下一轮
// /api/chat 的 payload 会把它带上去。
export async function updateSessionSandbox(sessionId, sandboxMode) {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/sandbox`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sandboxMode }),
    },
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "切换沙箱模式失败");
  }
  return data;
}
