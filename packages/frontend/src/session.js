// 会话 id 的存放。历史归服务端，前端只记这一个 id。

import { readStored, writeStored } from "./storage";

const SESSION_KEY = "chat.sessionId";

export function readStoredSessionId() {
  return readStored(SESSION_KEY);
}

export function writeStoredSessionId(value) {
  writeStored(SESSION_KEY, value);
}

// 前缀 web- 是为了在 storage/sessions/ 里一眼看出是浏览器建的。
// 格式受限（字母数字与连字符/下划线）是因为服务端拿它当文件名。
export function newSessionId() {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
