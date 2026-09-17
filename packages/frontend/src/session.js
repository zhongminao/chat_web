// 会话 id 的存放。历史归服务端，前端只记这一个 id。

import { readStored, writeStored } from "./storage";

const SESSION_KEY = "chat.sessionId";

export function readStoredSessionId() {
  return readStored(SESSION_KEY);
}

export function writeStoredSessionId(value) {
  writeStored(SESSION_KEY, value);
}

// 新会话 id。**合法性由服务端判，这里不重复它的规则。**
//
// 服务端有一条严格的正则（它拿 id 当文件名，是防路径穿越的），但那是**它**的规则 ——
// 客户端"保证生成合法 id"等于把格式规则抄一份在这边，两边靠注释维系。
// 这里只负责一个**约定**：前缀 web- 让 storage/sessions/ 里一眼看出是浏览器建的。
// 约定和校验是两回事，前者不承担"必须通过校验"的义务。
//
// 这个耦合由 check_api.py 盯着（它断言服务端接受这里生成的形状）—— 尺子放在
// **能坏的那一侧**（服务端的正则），而不是让客户端去猜。
export function newSessionId() {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
