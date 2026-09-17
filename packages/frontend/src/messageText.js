// 把展示列表折成「可复制的文本」。两个粒度：
//
//   messageToText(message)  一条 —— 一个问题 / 一条助手回复 / 一个工具步骤的输出
//   turnRanges(messages)    一段 loop —— 从一条 user 起，到下一条 user 之前
//
// 为什么带 turnRanges 这种分组：界面上的"一次 loop"（模型说一句、跑几个工具、
// 再收尾）在日志里是一串平铺的条目，边界只能靠 user 条目认出来 —— 一条 user
// 到下一条 user 之间，就是那一轮的全部产出。
//
// **唯一的语义：复制出来的就是日志里的原文，也就是模型当时看到的那些字符。**
// 这里不做任何清理、截断或美化，连"看起来像噪音"的东西也不动 —— 一旦开始"帮"
// 用户删东西，复制的内容就不再是"模型看到的那一份"，也就没法用它复盘"模型当时
// 到底看到了什么"。spill 的定位符（`[full output: N chars saved to /tmp/chat-spill/...]`）
// 正是这一类：它是模型**确实看到过**的一行，而且完整预览之外的全文在它指向的文件里，
// 复制时既不该删掉它、也不该去把全文捞回来（那样复制出来的就不再是模型看到的东西）。

export function messageToText(message) {
  if (!message) {
    return "";
  }
  // 工具步骤与跑完的 bash 请求：正文就是输出本身。命令通常已经在输出里了
  // （run_bash 的输出以 `$ <命令>` 开头，read_file 以 `[read_file] <路径>` 开头），
  // 所以这里不再另加"⚙ 工具名"前缀 —— 加了就是同一件事说两遍。
  if (message.role === "tool-step" || message.role === "bash-request") {
    return String(message.result ?? "");
  }
  // 正在跑的那个工具：还没有结果，没什么可复制。
  if (message.role === "tool-running") {
    return "";
  }
  // 未知条目：前端不认识它，复制不出东西来（宁可为空，也不要编一段出来）。
  if (message.role === "unknown") {
    return "";
  }
  return String(message.content ?? "");
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
