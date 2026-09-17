// 把展示列表折成「可复制的文本」。两个粒度：
//
//   messageToText(message)  一条 —— 一个问题 / 一条助手回复 / 一个工具步骤的输出
//   turnRanges(messages)    一段 loop —— 从一条 user 起，到下一条 user 之前
//
// 为什么带 turnRanges 这种分组：界面上的"一次 loop"（模型说一句、跑几个工具、
// 再收尾）在日志里是一串平铺的条目，边界只能靠 user 条目认出来 —— 一条 user
// 到吓一条 user 之间，就是那一轮的全部产出。

// spill 的定位符：工具输出太长时，中间被掐掉、全文另存到 /tmp/chat-spill/，
// 原地留一行 `[full output: N chars saved to <path> — ...]`。
//
// 复制时去掉它 —— 那是给**模型**取回全文用的地址，粘给人看只是噪音。
// 注意**只去这一行**：`...[{n} chars omitted]...` 那种省略标记留着，因为它标出
// "这里少了内容"，去掉会让复制出来的文本看起来是连续的（那才是真的骗人）。
const SPILL_LOCATOR = /\n?\[full output: \d+ chars saved to [^\]]*\]/g;

export function messageToText(message) {
  if (!message) {
    return "";
  }
  // 工具步骤与跑完的 bash 请求：正文就是输出本身。命令通常已经在输出里了
  // （run_bash 的输出以 `$ <命令>` 开头，read_file 以 `[read_file] <路径>` 开头），
  // 所以这里不再另加"⚙ 工具名"前缀 —— 加了就是同一件事说两遍。
  if (message.role === "tool-step" || message.role === "bash-request") {
    return String(message.result ?? "").replace(SPILL_LOCATOR, "").trim();
  }
  // 正在跑的那个工具：还没有结果，没什么可复制。
  if (message.role === "tool-running") {
    return "";
  }
  // 未知条目：把原文带上总比复制出一段空白强（它本来就是因为版本不匹配才出现）。
  if (message.role === "unknown") {
    return "";
  }
  return String(message.content ?? "").trim();
}

/** 每一段 loop 的起止下标。第一段之前如果先出现 assistant（刷新后落在轮次中间），那段也算一段。 */
export function turnRanges(messages) {
  const ranges = [];
  (messages || []).forEach((message, index) => {
    const last = ranges[ranges.length - 1];
    if (message.role === "user" || !last) {
      ranges.push({ start: index, end: index });
    } else {
      last.end = index;
    }
  });
  return ranges;
}

/** 一段 loop 的文本：各条目之间空一行，空条目（plan、待审批的 bash、正在跑的工具）自然丢掉。 */
export function turnToText(messages) {
  return (messages || [])
    .map(messageToText)
    .filter(Boolean)
    .join("\n\n");
}
