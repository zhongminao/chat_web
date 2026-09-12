// 会话 id 的存放。历史归服务端，前端只记这一个 id。

const SESSION_KEY = "chat.sessionId";

// 存储不可用时（隐私模式 / file:// / 浏览器禁用）访问 localStorage 会抛
// SecurityError，而这发生在 useState 初始化里 —— 抛出去就是整页空白。
export function readStoredSessionId() {
  try {
    return window.localStorage.getItem(SESSION_KEY);
  } catch (error) {
    return null;
  }
}

export function writeStoredSessionId(value) {
  try {
    window.localStorage.setItem(SESSION_KEY, value);
  } catch (error) {
    /* 存不下就只活在内存里 */
  }
}

// 前缀 web- 是为了在 storage/sessions/ 里一眼看出是浏览器建的。
// 格式受限（字母数字与连字符/下划线）是因为服务端拿它当文件名。
export function newSessionId() {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
